"""Authenticated alwaysdata relay for short-lived MAX QR login sessions."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
import os
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from aiohttp import web
from dotenv import load_dotenv
from websockets.asyncio.client import connect

from max_to_telegram.max_client import MAX_WEB_USER_AGENT, MaxCommandError
from max_to_telegram.protocol import Frame, FrameCodec, ProtocolError

logger = logging.getLogger(__name__)

MAX_WS_URL = "wss://api.oneme.ru/websocket"
MAX_WEB_ORIGIN = "https://web.max.ru"
OPCODE_KEEPALIVE = 1
OPCODE_INIT = 6
OPCODE_QR_CREATE = 288
OPCODE_QR_STATUS = 289
OPCODE_QR_COMPLETE = 291
OPCODE_QR_PASSWORD = 115
REQUEST_TIMEOUT_SECONDS = 25.0
DEFAULT_QR_TTL_MS = 120_000
MAX_REQUEST_BYTES = 2_048
MAX_SESSIONS = 4


class RelaySocket(Protocol):
    async def send(self, message: bytes) -> None: ...

    async def recv(self) -> bytes | str: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


Connector = Callable[..., Awaitable[RelaySocket]]
DEFAULT_CONNECTOR = cast(Connector, connect)


class QrRelayError(RuntimeError):
    """Sanitized QR relay error safe to map to a public error code."""

    def __init__(self, code: str, *, status: int = 502) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(slots=True)
class QrSession:
    websocket: RelaySocket
    device_id: str
    track_id: str
    qr_link: str
    expires_at: int
    polling_interval: int
    codec: FrameCodec = field(default_factory=FrameCodec)
    next_seq: int = 2
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False

    async def command(self, opcode: int, payload: Any = None) -> Frame:
        async with self.lock:
            if self.closed:
                raise QrRelayError("track.not.found", status=409)
            seq = self.next_seq
            self.next_seq += 1
            await self.websocket.send(
                self.codec.encode(Frame(cmd=0, seq=seq, opcode=opcode, payload=payload))
            )
            deadline = asyncio.get_running_loop().time() + REQUEST_TIMEOUT_SECONDS
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise QrRelayError("max_timeout")
                raw = await asyncio.wait_for(self.websocket.recv(), timeout=remaining)
                if isinstance(raw, str):
                    raise ProtocolError("MAX sent an unexpected text WebSocket frame")
                frame = self.codec.decode(raw)
                if frame.cmd == 0 and frame.opcode == OPCODE_KEEPALIVE:
                    await self.websocket.send(
                        self.codec.encode(Frame(cmd=1, seq=frame.seq, opcode=OPCODE_KEEPALIVE))
                    )
                    continue
                if frame.seq != seq or frame.opcode != opcode:
                    continue
                if frame.cmd == 3:
                    payload_object = frame.payload if isinstance(frame.payload, dict) else {}
                    code = safe_server_code(payload_object.get("error"))
                    raise MaxCommandError(opcode=opcode, code=code)
                if frame.cmd == 1:
                    return frame

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        with suppress(Exception):
            await self.websocket.close(code=1000, reason="QR session complete")


class QrSessionManager:
    """Own active MAX sockets across independent HTTPS requests."""

    def __init__(
        self,
        *,
        connector: Connector = DEFAULT_CONNECTOR,
        app_version: str = "26.8.8",
    ) -> None:
        self.connector = connector
        self.app_version = app_version
        self.sessions: dict[str, QrSession] = {}
        self.lock = asyncio.Lock()

    async def start(self) -> dict[str, Any]:
        await self.cleanup_expired()
        async with self.lock:
            if len(self.sessions) >= MAX_SESSIONS:
                raise QrRelayError("relay_busy", status=429)
        session = await self._open_session()
        session_id = secrets.token_urlsafe(32)
        async with self.lock:
            self.sessions[session_id] = session
        return {
            "type": "qr",
            "sessionId": session_id,
            "qrLink": session.qr_link,
            "expiresAt": session.expires_at,
            "pollingInterval": session.polling_interval,
        }

    async def poll(self, session_id: str) -> dict[str, Any]:
        session = await self._require(session_id)
        if int(time.time() * 1000) >= session.expires_at:
            await self.cancel(session_id)
            raise QrRelayError("track.not.found", status=409)
        frame = await session.command(OPCODE_QR_STATUS, {"trackId": session.track_id})
        payload = require_object(frame.payload)
        status = require_object(payload.get("status"))
        expires_at = future_timestamp(status.get("expiresAt"), session.expires_at)
        session.expires_at = expires_at
        if not status.get("loginAvailable"):
            return {"type": "waiting", "expiresAt": expires_at}

        completed = await session.command(OPCODE_QR_COMPLETE, {"trackId": session.track_id})
        completed_payload = require_object(completed.payload)
        challenge = completed_payload.get("passwordChallenge")
        if challenge:
            challenge_object = require_object(challenge)
            hint = challenge_object.get("hint")
            return {"type": "password_required", "hint": hint if isinstance(hint, str) else None}
        return await self._complete(session_id, session, completed.payload)

    async def password(self, session_id: str, password: str) -> dict[str, Any]:
        if not 1 <= len(password) <= 256:
            raise QrRelayError("password_invalid", status=400)
        session = await self._require(session_id)
        completed = await session.command(
            OPCODE_QR_PASSWORD,
            {"trackId": session.track_id, "password": password},
        )
        return await self._complete(session_id, session, completed.payload)

    async def cancel(self, session_id: str) -> None:
        async with self.lock:
            session = self.sessions.pop(session_id, None)
        if session is not None:
            await session.close()

    async def cleanup_expired(self) -> None:
        now = int(time.time() * 1000)
        async with self.lock:
            expired = [key for key, value in self.sessions.items() if value.expires_at <= now]
        for session_id in expired:
            await self.cancel(session_id)

    async def close(self) -> None:
        async with self.lock:
            session_ids = list(self.sessions)
        await asyncio.gather(*(self.cancel(session_id) for session_id in session_ids))

    async def _require(self, session_id: str) -> QrSession:
        if not session_id or len(session_id) > 128:
            raise QrRelayError("track.not.found", status=409)
        async with self.lock:
            session = self.sessions.get(session_id)
        if session is None or session.closed:
            raise QrRelayError("track.not.found", status=409)
        return session

    async def _open_session(self) -> QrSession:
        device_id = str(uuid.uuid4())
        websocket = await self.connector(
            MAX_WS_URL,
            origin=MAX_WEB_ORIGIN,
            user_agent_header=MAX_WEB_USER_AGENT,
            open_timeout=REQUEST_TIMEOUT_SECONDS,
            close_timeout=5,
            ping_interval=None,
            max_size=16 * 1024 * 1024,
        )
        session = QrSession(
            websocket=websocket,
            device_id=device_id,
            track_id="pending",
            qr_link="pending",
            expires_at=int(time.time() * 1000) + DEFAULT_QR_TTL_MS,
            polling_interval=5_000,
            next_seq=0,
        )
        try:
            await session.command(OPCODE_INIT, self._init_payload(device_id))
            created = require_object((await session.command(OPCODE_QR_CREATE)).payload)
            track_id = created.get("trackId")
            qr_link = created.get("qrLink")
            if not isinstance(track_id, str) or not track_id:
                raise QrRelayError("invalid_qr_response")
            if not isinstance(qr_link, str) or not qr_link.startswith("https://"):
                raise QrRelayError("invalid_qr_response")
            session.track_id = track_id
            session.qr_link = qr_link
            session.expires_at = future_timestamp(
                created.get("expiresAt"), int(time.time() * 1000) + DEFAULT_QR_TTL_MS
            )
            session.polling_interval = clamp_integer(created.get("pollingInterval"), 2_000, 10_000)
            return session
        except Exception:
            await session.close()
            raise

    async def _complete(
        self,
        session_id: str,
        session: QrSession,
        payload: Any,
    ) -> dict[str, Any]:
        result = extract_session(payload, session.device_id)
        await self.cancel(session_id)
        return {"type": "completed", "session": result}

    def _init_payload(self, device_id: str) -> dict[str, Any]:
        return {
            "userAgent": {
                "deviceType": "WEB",
                "pushDeviceType": "WEBPUSH",
                "locale": "ru",
                "deviceLocale": "ru",
                "osVersion": "alwaysdata",
                "deviceName": "MAX Telegram bridge QR relay",
                "headerUserAgent": MAX_WEB_USER_AGENT,
                "isPwa": False,
                "appVersion": self.app_version,
                "screen": "0x0 1.0x",
                "timezone": "Europe/Moscow",
            },
            "deviceId": device_id,
        }


MANAGER_KEY = web.AppKey("qr_manager", QrSessionManager)
TOKEN_KEY = web.AppKey("relay_token", str)


def create_app(*, token: str, manager: QrSessionManager | None = None) -> web.Application:
    if len(token) < 32:
        raise ValueError("MAX_QR_RELAY_TOKEN must contain at least 32 characters")
    app = web.Application(client_max_size=MAX_REQUEST_BYTES, middlewares=[auth_middleware])
    app[TOKEN_KEY] = token
    app[MANAGER_KEY] = manager or QrSessionManager(
        app_version=os.getenv("MAX_APP_VERSION", "26.8.8")
    )
    app.router.add_get("/healthz", health)
    app.router.add_post("/v1/qr/start", start)
    app.router.add_post("/v1/qr/poll", poll)
    app.router.add_post("/v1/qr/password", password)
    app.router.add_post("/v1/qr/cancel", cancel)
    app.on_cleanup.append(cleanup_app)
    return app


@web.middleware
async def auth_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    if request.path == "/healthz":
        return await handler(request)
    supplied = request.headers.get("Authorization", "")
    expected = f"Bearer {request.app[TOKEN_KEY]}"
    if not hmac.compare_digest(supplied, expected):
        return json_response({"error": "unauthorized"}, status=401)
    try:
        return await handler(request)
    except QrRelayError as exc:
        return json_response({"type": "error", "code": exc.code}, status=exc.status)
    except MaxCommandError as exc:
        return json_response({"type": "error", "code": exc.code}, status=409)
    except (OSError, TimeoutError, asyncio.TimeoutError, ProtocolError) as exc:
        logger.warning("QR relay transport failed (%s)", type(exc).__name__)
        return json_response({"type": "error", "code": "relay_transport_failed"}, status=502)
    except Exception as exc:
        logger.exception("QR relay request failed (%s)", type(exc).__name__)
        return json_response({"type": "error", "code": "relay_failed"}, status=502)


async def health(request: web.Request) -> web.Response:
    manager = request.app[MANAGER_KEY]
    await manager.cleanup_expired()
    return json_response({"ok": True, "activeSessions": len(manager.sessions)})


async def start(request: web.Request) -> web.Response:
    await read_object(request)
    return json_response(await request.app[MANAGER_KEY].start())


async def poll(request: web.Request) -> web.Response:
    body = await read_object(request)
    return json_response(await request.app[MANAGER_KEY].poll(require_session_id(body)))


async def password(request: web.Request) -> web.Response:
    body = await read_object(request)
    supplied = body.get("password")
    if not isinstance(supplied, str):
        raise QrRelayError("password_invalid", status=400)
    return json_response(
        await request.app[MANAGER_KEY].password(require_session_id(body), supplied)
    )


async def cancel(request: web.Request) -> web.Response:
    body = await read_object(request)
    await request.app[MANAGER_KEY].cancel(require_session_id(body))
    return json_response({"ok": True})


async def cleanup_app(app: web.Application) -> None:
    await app[MANAGER_KEY].close()


async def read_object(request: web.Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except (ValueError, web.HTTPException) as exc:
        raise QrRelayError("invalid_request", status=400) from exc
    if not isinstance(value, dict):
        raise QrRelayError("invalid_request", status=400)
    return value


def require_session_id(body: dict[str, Any]) -> str:
    session_id = body.get("sessionId")
    if not isinstance(session_id, str) or not 1 <= len(session_id) <= 128:
        raise QrRelayError("track.not.found", status=409)
    return session_id


def extract_session(payload: Any, device_id: str) -> dict[str, str]:
    try:
        root = require_object(payload)
        token_attrs = require_object(root.get("tokenAttrs"))
        login = require_object(token_attrs.get("LOGIN"))
        token = login.get("token")
        profile = require_object(root.get("profile"))
        contact = require_object(profile.get("contact"))
        viewer_id = contact.get("id")
    except QrRelayError as exc:
        raise QrRelayError("invalid_login_result") from exc
    if not isinstance(token, str) or not 1 <= len(token) <= 16_384:
        raise QrRelayError("invalid_login_result")
    if isinstance(viewer_id, bool) or not isinstance(viewer_id, (str, int)):
        raise QrRelayError("invalid_login_result")
    rendered_id = str(viewer_id)
    if not rendered_id.isdecimal() or int(rendered_id) <= 0:
        raise QrRelayError("invalid_login_result")
    return {"viewerId": rendered_id, "token": token, "deviceId": device_id}


def require_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QrRelayError("invalid_max_response")
    return value


def future_timestamp(value: Any, fallback: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > int(time.time() * 1000):
        return value
    return fallback


def clamp_integer(value: Any, minimum: int, maximum: int) -> int:
    integer = value if isinstance(value, int) and not isinstance(value, bool) else 5_000
    return max(minimum, min(maximum, integer))


def safe_server_code(value: Any) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return "max_command_failed"
    rendered = "".join(
        character for character in str(value) if character.isalnum() or character in "._-"
    )
    return rendered[:64] or "max_command_failed"


def json_response(payload: Any, *, status: int = 200) -> web.Response:
    return web.json_response(
        payload,
        status=status,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the private MAX QR relay")
    parser.add_argument("--host", default=os.getenv("MAX_QR_RELAY_HOST", "::"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MAX_QR_RELAY_PORT", "8301")))
    args = parser.parse_args(argv)
    token = os.getenv("MAX_QR_RELAY_TOKEN", "")
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    web.run_app(create_app(token=token), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
