'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';

type Phase = 'idle' | 'connecting' | 'waiting' | 'password' | 'saving' | 'saved' | 'error';
type PortalEvent =
  | { type: 'connecting' }
  | { type: 'qr'; qrLink: string; expiresAt: number; pollingInterval: number; sessionId: string }
  | { type: 'waiting'; expiresAt: number }
  | { type: 'password_required'; hint: string | null }
  | { type: 'saving' }
  | { type: 'saved'; updatedAt: number }
  | { type: 'error'; code: string; message: string };
type PortalCommand =
  | { type: 'start' }
  | { type: 'poll' | 'cancel'; sessionId: string }
  | { type: 'password'; sessionId: string; password: string };

export function LoginPortal() {
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const requestInFlightRef = useRef(false);
  const runCommandRef = useRef<(command: PortalCommand) => Promise<void>>(async () => {});
  const sessionIdRef = useRef<string | null>(null);
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

  const stopSession = useCallback(() => {
    stopPolling();
    const sessionId = sessionIdRef.current;
    sessionIdRef.current = null;
    if (sessionId) {
      void fetch('/api/max/qr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'cancel', sessionId }),
        keepalive: true,
      });
    }
  }, [stopPolling]);

  useEffect(() => stopSession, [stopSession]);
  useEffect(() => {
    if (phase !== 'waiting') return;
    const timer = setInterval(() => setClock(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, [phase]);

  const schedulePolling = useCallback((sessionId: string, interval: number) => {
    stopPolling();
    pollTimerRef.current = setInterval(() => {
      void runCommandRef.current({ type: 'poll', sessionId });
    }, Math.max(2_000, Math.min(10_000, interval)));
  }, [stopPolling]);

  const handleEvent = useCallback((event: PortalEvent) => {
    switch (event.type) {
      case 'connecting':
        setPhase('connecting');
        setMessage('Устанавливаем защищённое соединение с MAX…');
        break;
      case 'qr':
        sessionIdRef.current = event.sessionId;
        setPhase('waiting');
        setQrLink(event.qrLink);
        setExpiresAt(event.expiresAt);
        setClock(Date.now());
        setMessage('Отсканируйте QR в приложении MAX');
        schedulePolling(event.sessionId, event.pollingInterval);
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
        sessionIdRef.current = null;
        setPhase('saved');
        setQrLink(null);
        setMessage(`Готово · ${new Date(event.updatedAt).toLocaleString('ru-RU')}`);
        break;
      case 'error':
        stopPolling();
        sessionIdRef.current = null;
        setPhase('error');
        setQrLink(null);
        setMessage(event.message);
        break;
    }
  }, [schedulePolling, stopPolling]);

  const runCommand = useCallback(async (command: PortalCommand) => {
    if (requestInFlightRef.current) return;
    requestInFlightRef.current = true;
    try {
      const response = await fetch('/api/max/qr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(command),
      });
      const event = await response.json() as PortalEvent;
      if (!event || typeof event !== 'object' || typeof event.type !== 'string') {
        throw new Error('Invalid portal response');
      }
      handleEvent(event);
    } catch {
      stopPolling();
      sessionIdRef.current = null;
      setPhase('error');
      setQrLink(null);
      setMessage('Защищённый запрос не выполнился. Повторите попытку.');
    } finally {
      requestInFlightRef.current = false;
    }
  }, [handleEvent, stopPolling]);

  useEffect(() => {
    runCommandRef.current = runCommand;
  }, [runCommand]);

  const start = () => {
    stopSession();
    setQrLink(null);
    setExpiresAt(null);
    setPassword('');
    setPasswordHint(null);
    setPhase('connecting');
    setMessage('Подключаемся к MAX…');
    void runCommand({ type: 'start' });
  };

  const submitPassword = async (event: React.FormEvent) => {
    event.preventDefault();
    const sessionId = sessionIdRef.current;
    if (!password || !sessionId) return;
    const suppliedPassword = password;
    setPassword('');
    setPhase('saving');
    setMessage('Проверяем пароль в MAX…');
    void runCommand({ type: 'password', sessionId, password: suppliedPassword });
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
            <small>доступ подтверждён</small>
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
          <p><span aria-hidden="true">✓</span>Доступ по отдельному коду портала</p>
          <p><span aria-hidden="true">✓</span>Токен MAX не показывается и не записывается в браузер</p>
          <p><span aria-hidden="true">✓</span>QR действует только в рамках одной короткой сессии</p>
        </footer>
      </section>
      <p className="privacy-note">Личный служебный портал · данные сообщений здесь не хранятся</p>
    </main>
  );
}
