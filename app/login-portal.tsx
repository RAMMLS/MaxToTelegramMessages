'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';

type Phase = 'idle' | 'connecting' | 'waiting' | 'password' | 'saving' | 'saved' | 'error';
type PortalEvent =
  | { type: 'connecting' }
  | { type: 'qr'; qrLink: string; expiresAt: number; pollingInterval: number }
  | { type: 'waiting'; expiresAt: number }
  | { type: 'password_required'; hint: string | null }
  | { type: 'saving' }
  | { type: 'saved'; updatedAt: number }
  | { type: 'error'; code: string; message: string };

export function LoginPortal({ displayName }: { displayName: string }) {
  const socketRef = useRef<WebSocket | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [qrLink, setQrLink] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<number | null>(null);
  const [passwordHint, setPasswordHint] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('Готово создать одноразовый QR');
  const [clock, setClock] = useState(0);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    pollTimerRef.current = null;
  }, []);

  const closeSocket = useCallback(() => {
    stopPolling();
    const socket = socketRef.current;
    socketRef.current = null;
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000, 'page closed');
  }, [stopPolling]);

  useEffect(() => closeSocket, [closeSocket]);
  useEffect(() => {
    if (phase !== 'waiting') return;
    const timer = setInterval(() => setClock(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, [phase]);

  const schedulePolling = useCallback((socket: WebSocket, interval: number) => {
    stopPolling();
    pollTimerRef.current = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'poll' }));
    }, Math.max(2_000, Math.min(10_000, interval)));
  }, [stopPolling]);

  const handleEvent = useCallback((event: PortalEvent, socket: WebSocket) => {
    switch (event.type) {
      case 'connecting':
        setPhase('connecting');
        setMessage('Устанавливаем защищённое соединение с MAX…');
        break;
      case 'qr':
        setPhase('waiting');
        setQrLink(event.qrLink);
        setExpiresAt(event.expiresAt);
        setClock(Date.now());
        setMessage('Отсканируйте QR в приложении MAX');
        schedulePolling(socket, event.pollingInterval);
        break;
      case 'waiting':
        setExpiresAt(event.expiresAt);
        setClock(Date.now());
        break;
      case 'password_required':
        stopPolling();
        setPhase('password');
        setPasswordHint(event.hint);
        setMessage('MAX запросил пароль двухэтапной защиты');
        break;
      case 'saving':
        stopPolling();
        setPhase('saving');
        setMessage('Шифруем и сохраняем новую сессию…');
        break;
      case 'saved':
        stopPolling();
        setPhase('saved');
        setQrLink(null);
        setMessage(`Готово · ${new Date(event.updatedAt).toLocaleString('ru-RU')}`);
        break;
      case 'error':
        stopPolling();
        setPhase('error');
        setQrLink(null);
        setMessage(event.message);
        break;
    }
  }, [schedulePolling, stopPolling]);

  const start = () => {
    closeSocket();
    setQrLink(null);
    setExpiresAt(null);
    setPassword('');
    setPasswordHint(null);
    setPhase('connecting');
    setMessage('Подключаемся к MAX…');
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${location.host}/api/max/qr`);
    socketRef.current = socket;
    socket.onmessage = (event) => {
      if (typeof event.data !== 'string' || event.data.length > 4096) return;
      try { handleEvent(JSON.parse(event.data) as PortalEvent, socket); } catch { /* Ignore invalid server frames. */ }
    };
    socket.onerror = () => {
      stopPolling();
      setPhase('error');
      setMessage('Защищённое соединение не открылось. Повторите попытку.');
    };
    socket.onclose = (event) => {
      stopPolling();
      if (!event.wasClean && phase !== 'saved') {
        setPhase('error');
        setMessage('Соединение прервалось. Создайте новый QR.');
      }
    };
  };

  const submitPassword = (event: React.FormEvent) => {
    event.preventDefault();
    const socket = socketRef.current;
    if (!password || !socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: 'password', password }));
    setPassword('');
    setPhase('saving');
    setMessage('Проверяем пароль в MAX…');
  };

  const countdown = expiresAt && clock ? Math.max(0, Math.ceil((expiresAt - clock) / 1000)) : null;
  const busy = phase === 'connecting' || phase === 'waiting' || phase === 'saving';

  return (
    <main className="portal-shell">
      <section className="portal-card" aria-labelledby="portal-title">
        <header className="portal-header">
          <div className="brand-mark" aria-hidden="true">M</div>
          <div>
            <p className="eyebrow">MAX → Telegram</p>
            <h1 id="portal-title">Подключение аккаунта</h1>
          </div>
          <div className="account-block">
            <span className="secure-badge"><span aria-hidden="true">●</span> защищено</span>
            <small title={displayName}>{displayName}</small>
          </div>
        </header>

        <div className="portal-grid">
          <section className="qr-panel">
            <div className={`qr-placeholder ${phase === 'saved' ? 'qr-success' : ''}`}>
              {qrLink ? (
                <QRCodeSVG value={qrLink} size={190} level="M" marginSize={1} />
              ) : phase === 'saved' ? (
                <b className="success-mark" aria-label="Подключено">✓</b>
              ) : (
                <><span /><span /><span /><b>MAX</b></>
              )}
            </div>
            <div className="status-line" role="status" aria-live="polite">
              <span className={`status-dot status-${phase}`} /> {message}
            </div>
            {phase === 'waiting' && countdown !== null ? (
              <p className="expiry-note">Код активен ещё примерно {countdown} сек.</p>
            ) : null}
            {phase === 'password' ? (
              <form className="password-form" onSubmit={submitPassword}>
                <label htmlFor="max-password">Пароль MAX {passwordHint ? `· подсказка: ${passwordHint}` : ''}</label>
                <input id="max-password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} maxLength={256} autoFocus />
                <button className="primary-button" type="submit" disabled={!password}>Подтвердить</button>
              </form>
            ) : (
              <button className="primary-button" type="button" onClick={start} disabled={busy}>
                {phase === 'idle' ? 'Создать QR для входа' : phase === 'saved' ? 'Обновить сессию' : phase === 'error' ? 'Попробовать снова' : 'Подождите…'}
              </button>
            )}
          </section>

          <section className="instructions">
            <p className="step-label">Как это работает</p>
            <h2>Один скан — и мост получит новую сессию</h2>
            <ol>
              <li><span>1</span><div><strong>Создайте QR</strong><p>Код появится только после защищённого запроса к серверу.</p></div></li>
              <li><span>2</span><div><strong>Откройте MAX на телефоне</strong><p>Отсканируйте код стандартным сканером MAX и подтвердите вход.</p></div></li>
              <li><span>3</span><div><strong>Дождитесь подтверждения</strong><p>Сессия шифруется на сервере и становится доступна мосту без ручного копирования токена.</p></div></li>
            </ol>
          </section>
        </div>

        <footer className="security-strip">
          <p><span aria-hidden="true">✓</span>Вход через защищённую учётную запись ChatGPT</p>
          <p><span aria-hidden="true">✓</span>Токен MAX не показывается и не записывается в браузер</p>
          <p><span aria-hidden="true">✓</span>QR действует только в рамках одной короткой сессии</p>
        </footer>
      </section>
      <p className="privacy-note">Личный служебный портал · данные сообщений здесь не хранятся</p>
    </main>
  );
}
