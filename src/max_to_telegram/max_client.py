"""Async MAX WebSocket listener with login, acknowledgements, and reconnects."""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from typing import Any, Protocol

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from max_to_telegram.auth import AuthError, LocalMaxSession, MaxCredentials
from max_to_telegram.config import Settings
from max_to_telegram.protocol import Frame, FrameCodec, ProtocolError

logger = logging.getLogger(__name__)

MAX_WEB_USER_AGENT = "Mozilla/5.0 MAX-to-Telegram/0.1"

OPCODE_KEEPALIVE = 1
OPCODE_INIT = 6
OPCODE_LOGIN = 19
OPCODE_LOGOUT = 20
OPCODE_NEW_MESSAGE = 128


class MaxClientError(RuntimeError):
    """Base class for MAX connection errors."""


class MaxAuthenticationError(MaxClientError):
    """Raised when MAX rejects the imported web-session token."""


class MaxCommandError(MaxClientError):
    """Raised for a server command error without leaking the request payload."""

    def __init__(self, *, opcode: int, code: str = "unknown", message: str = "") -> None:
        self.opcode = opcode
        self.code = code
        suffix = f": {message}" if message else ""
        super().__init__(f"MAX command {opcode} failed ({code}){suffix}")


class WebSocketLike(Protocol):
    async def send(self, message: bytes) -> None: ...

    async def recv(self) -> bytes | str: ...

    def __aiter__(self) -> AsyncIterator[bytes | str]: ...


Connector = Callable[..., AbstractAsyncContextManager[WebSocketLike]]
Sleep = Callable[[float], Awaitable[None]]
MessageHook = Callable[[Frame], Awaitable[None]]
CredentialsUpdated = Callable[[LocalMaxSession], None]


class MaxClient:
    """Minimal client for the private MAX web protocol.

    The listener acknowledges server pushes before yielding them to downstream
    code, so Telegram latency cannot block the MAX protocol acknowledgement.
    """

    def __init__(
        self,
        settings: Settings,
        credentials: MaxCredentials,
        *,
        connector: Connector = connect,
        codec: FrameCodec | None = None,
        sleep: Sleep = asyncio.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
        before_message_ack: MessageHook | None = None,
        credentials_updated: CredentialsUpdated | None = None,
        device_id: str | None = None,
        command_timeout: float = 35.0,
        keepalive_interval: float = 30.0,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings
        self.credentials = credentials
        self.connector = connector
        self.codec = codec or FrameCodec()
        self.sleep = sleep
        self.random_uniform = random_uniform
        self.before_message_ack = before_message_ack
        self.credentials_updated = credentials_updated
        self.command_timeout = command_timeout
        self.keepalive_interval = keepalive_interval
        self.monotonic = monotonic
        self.device_id = device_id or settings.max_device_id or str(uuid.uuid4())
        self._stop_event = asyncio.Event()
        self._next_seq = 0

    def stop(self) -> None:
        """Ask the reconnect loop to stop after the current operation."""

        self._stop_event.set()

    def set_before_message_ack(self, hook: MessageHook) -> None:
        """Install durable-ingest work that must succeed before message ACK."""

        self.before_message_ack = hook

    async def events(self) -> AsyncIterator[Frame]:
        """Yield acknowledged MAX push frames and reconnect on transport errors."""

        attempt = 0
        while not self._stop_event.is_set():
            try:
                async for frame in self._connected_events():
                    attempt = 0
                    yield frame
                if not self._stop_event.is_set():
                    raise MaxClientError("MAX WebSocket closed without an error")
            except (MaxAuthenticationError, MaxCommandError, AuthError, ProtocolError):
                raise
            except asyncio.CancelledError:
                raise
            except (
                MaxClientError,
                OSError,
                asyncio.TimeoutError,
                TimeoutError,
                WebSocketException,
            ) as exc:
                if self._stop_event.is_set():
                    return
                delay = self._reconnect_delay(attempt)
                logger.warning(
                    "MAX connection lost; reconnecting in %.2fs (%s)",
                    delay,
                    type(exc).__name__,
                )
                attempt += 1
                await self.sleep(delay)

    async def _connected_events(self) -> AsyncIterator[Frame]:
        self._next_seq = 0
        user_agent = MAX_WEB_USER_AGENT
        async with self.connector(
            self.settings.max_ws_url,
            origin="https://web.max.ru",
            user_agent_header=user_agent,
            open_timeout=self.command_timeout,
            close_timeout=5,
            ping_interval=None,
            max_size=16 * 1024 * 1024,
        ) as websocket:
            send_lock = asyncio.Lock()
            buffered = await self._handshake(websocket, send_lock, user_agent)
            logger.info("MAX session authenticated")
            keepalive = asyncio.create_task(self._keepalive(websocket, send_lock))
            receive: asyncio.Future[bytes | str] | None = None
            try:
                for frame in buffered:
                    yield frame
                iterator = websocket.__aiter__()
                while True:
                    receive = asyncio.ensure_future(anext(iterator))
                    done, _ = await asyncio.wait(
                        (receive, keepalive),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if keepalive in done:
                        if not receive.done():
                            receive.cancel()
                        await asyncio.gather(receive, return_exceptions=True)
                        await keepalive
                        return
                    try:
                        raw = receive.result()
                    except StopAsyncIteration:
                        break
                    frame = self._decode_raw(raw)
                    if frame.cmd == 0:
                        should_yield = await self._ack_push(websocket, send_lock, frame)
                        if should_yield:
                            yield frame
                    elif frame.cmd == 3:
                        raise self._command_error(frame)
            finally:
                if receive is not None and not receive.done():
                    receive.cancel()
                if receive is not None:
                    await asyncio.gather(receive, return_exceptions=True)
                if not keepalive.done():
                    keepalive.cancel()
                await asyncio.gather(keepalive, return_exceptions=True)

    async def _handshake(
        self,
        websocket: WebSocketLike,
        send_lock: asyncio.Lock,
        user_agent: str,
    ) -> list[Frame]:
        init_seq = self._take_sequence()
        await self._send(
            websocket,
            send_lock,
            Frame(cmd=0, seq=init_seq, opcode=OPCODE_INIT, payload=self._init_payload(user_agent)),
        )
        _, buffered = await self._wait_for_response(
            websocket, send_lock, seq=init_seq, opcode=OPCODE_INIT
        )

        login_seq = self._take_sequence()
        await self._send(
            websocket,
            send_lock,
            Frame(cmd=0, seq=login_seq, opcode=OPCODE_LOGIN, payload=self._login_payload()),
        )
        response, during_login = await self._wait_for_response(
            websocket, send_lock, seq=login_seq, opcode=OPCODE_LOGIN
        )
        self._validate_login_response(response)
        return [*buffered, *during_login]

    async def _wait_for_response(
        self,
        websocket: WebSocketLike,
        send_lock: asyncio.Lock,
        *,
        seq: int,
        opcode: int,
    ) -> tuple[Frame, list[Frame]]:
        buffered: list[Frame] = []
        clock = self.monotonic or asyncio.get_running_loop().time
        deadline = clock() + self.command_timeout
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                raise asyncio.TimeoutError
            raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
            frame = self._decode_raw(raw)
            if frame.cmd == 0:
                should_yield = await self._ack_push(websocket, send_lock, frame)
                if should_yield:
                    buffered.append(frame)
                continue
            if frame.seq != seq or frame.opcode != opcode:
                continue
            if frame.cmd == 3:
                error = self._command_error(frame)
                if opcode == OPCODE_LOGIN:
                    raise MaxAuthenticationError(str(error)) from error
                raise error
            if frame.cmd == 1:
                return frame, buffered

    async def _ack_push(
        self,
        websocket: WebSocketLike,
        send_lock: asyncio.Lock,
        frame: Frame,
    ) -> bool:
        if frame.opcode == OPCODE_KEEPALIVE:
            await self._send(
                websocket,
                send_lock,
                Frame(cmd=1, seq=frame.seq, opcode=OPCODE_KEEPALIVE),
            )
            return False
        if frame.opcode == OPCODE_NEW_MESSAGE:
            if not isinstance(frame.payload, dict):
                raise ProtocolError("MAX message push payload must be an object")
            message = frame.payload.get("message")
            if (
                not isinstance(message, dict)
                or "id" not in message
                or "chatId" not in frame.payload
            ):
                raise ProtocolError("MAX message push is missing chatId or message.id")
            if self.before_message_ack is not None:
                await self.before_message_ack(frame)
            await self._send(
                websocket,
                send_lock,
                Frame(
                    cmd=1,
                    seq=frame.seq,
                    opcode=OPCODE_NEW_MESSAGE,
                    payload={
                        "chatId": frame.payload["chatId"],
                        "messageId": message["id"],
                    },
                ),
            )
        if frame.opcode == OPCODE_LOGOUT:
            raise MaxAuthenticationError("MAX session was logged out or revoked")
        return True

    async def _keepalive(
        self,
        websocket: WebSocketLike,
        send_lock: asyncio.Lock,
    ) -> None:
        while not self._stop_event.is_set():
            with suppress(asyncio.TimeoutError, TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.keepalive_interval)
            if self._stop_event.is_set():
                return
            seq = self._take_sequence()
            await self._send(
                websocket,
                send_lock,
                Frame(
                    cmd=0,
                    seq=seq,
                    opcode=OPCODE_KEEPALIVE,
                    payload={"interactive": False},
                ),
            )

    async def _send(
        self,
        websocket: WebSocketLike,
        send_lock: asyncio.Lock,
        frame: Frame,
    ) -> None:
        encoded = self.codec.encode(frame)
        async with send_lock:
            await websocket.send(encoded)

    def _decode_raw(self, raw: bytes | str) -> Frame:
        if isinstance(raw, str):
            raise ProtocolError("MAX sent an unexpected text WebSocket frame")
        return self.codec.decode(raw)

    def _take_sequence(self) -> int:
        if self._next_seq >= 32767:
            raise MaxClientError("MAX sequence space exhausted; reconnect required")
        value = self._next_seq
        self._next_seq += 1
        return value

    def _init_payload(self, user_agent: str) -> dict[str, Any]:
        return build_init_payload(self.settings, self.device_id, user_agent)

    def _login_payload(self) -> dict[str, Any]:
        return {
            "token": self.credentials.token,
            "chatsCount": 15,
            "lastLogin": 0,
            "interactive": False,
            "chatsSync": 0,
            "contactsSync": 0,
            "presenceSync": -1,
            "draftsSync": 0,
            "configHash": "",
        }

    def _validate_login_response(self, frame: Frame) -> None:
        if not isinstance(frame.payload, dict):
            raise ProtocolError("MAX login response payload must be an object")
        profile = frame.payload.get("profile")
        if isinstance(profile, dict):
            contact = profile.get("contact")
            if isinstance(contact, dict) and contact.get("id") is not None:
                try:
                    response_viewer_id = int(contact["id"])
                except (TypeError, ValueError) as exc:
                    raise ProtocolError("MAX login returned an invalid viewer ID") from exc
                if response_viewer_id != self.credentials.viewer_id:
                    raise MaxAuthenticationError("MAX login returned a different viewer account")

        refreshed = frame.payload.get("token")
        if isinstance(refreshed, str) and refreshed != self.credentials.token:
            updated = MaxCredentials(self.credentials.viewer_id, refreshed)
            if self.credentials_updated is not None:
                self.credentials_updated(LocalMaxSession(updated, self.device_id))
            self.credentials = updated

    def _command_error(self, frame: Frame) -> MaxCommandError:
        payload = frame.payload if isinstance(frame.payload, dict) else {}
        code = _safe_server_text(
            payload.get("error"),
            fallback="unknown",
            maximum=64,
            secret=self.credentials.token,
        )
        message = _safe_server_text(
            payload.get("localizedMessage") or payload.get("message"),
            fallback="",
            maximum=500,
            secret=self.credentials.token,
        )
        return MaxCommandError(opcode=frame.opcode, code=code, message=message)

    def _reconnect_delay(self, attempt: int) -> float:
        maximum = self.settings.reconnect_max_seconds
        if attempt >= maximum.bit_length() + 1:
            ceiling = float(maximum)
        else:
            ceiling = min(0.5 * (2**attempt), float(maximum))
        return self.random_uniform(ceiling / 2, ceiling)


def _safe_server_text(value: Any, *, fallback: str, maximum: int, secret: str) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return fallback
    rendered = " ".join(str(value).split()).replace(secret, "<redacted>")
    return rendered[:maximum] or fallback


def build_init_payload(settings: Settings, device_id: str, user_agent: str) -> dict[str, Any]:
    """Build the credential-free opcode 6 payload shared with the public probe."""

    return {
        "userAgent": {
            "deviceType": "WEB",
            "pushDeviceType": "WEBPUSH",
            "locale": settings.max_locale,
            "deviceLocale": settings.max_locale,
            "osVersion": "Python",
            "deviceName": "MAX to Telegram bridge",
            "headerUserAgent": user_agent,
            "isPwa": False,
            "appVersion": settings.max_app_version,
            "screen": "0x0 1.0x",
            "timezone": "Europe/Moscow",
        },
        "deviceId": device_id,
    }
