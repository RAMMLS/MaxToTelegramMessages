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

export function LoginPortal() {
  const streamAbortRef = useRef<AbortController | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [qrLink, setQrLink] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<number | null>(null);
  const [passwordHint, setPasswordHint] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('Готово создать одноразовый QR');
  const [clock, setClock] = useState(0);

  const stopSession = useCallback(() => {
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    sessionIdRef.current = null;
  }, []);

  useEffect(() => stopSession, [stopSession]);
  useEffect(() => {
    if (phase !== 'waiting') return;
    const timer = setInterval(() => setClock(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, [phase]);

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
        break;
      case 'waiting':
        setExpiresAt(event.expiresAt);
        setClock(Date.now());
        break;
      case 'password_required':
        setPhase('password');
        setPasswordHint(event.hint);
        setMessage('MAX запросил пароль двухэтапной защиты');
        break;
      case 'saving':
        setPhase('saving');
        setMessage('Шифруем и сохраняем новую сессию…');
        break;
      case 'saved':
        sessionIdRef.current = null;
        setPhase('saved');
        setQrLink(null);
        setMessage(`Готово · ${new Date(event.updatedAt).toLocaleString('ru-RU')}`);
        break;
      case 'error':
        sessionIdRef.current = null;
        setPhase('error');
        setQrLink(null);
        setMessage(event.message);
        break;
    }
  }, []);

  const start = () => {
    stopSession();
    setQrLink(null);
    setExpiresAt(null);
    setPassword('');
    setPasswordHint(null);
    setPhase('connecting');
    setMessage('Подключаемся к MAX…');
    const controller = new AbortController();
    streamAbortRef.current = controller;
    void consumeQrStream(controller, handleEvent).finally(() => {
      if (streamAbortRef.current === controller) streamAbortRef.current = null;
    });
  };

  const submitPassword = async (event: React.FormEvent) => {
    event.preventDefault();
    const sessionId = sessionIdRef.current;
    if (!password || !sessionId) return;
    const suppliedPassword = password;
    setPassword('');
    setPhase('saving');
    setMessage('Проверяем пароль в MAX…');
    try {
      const response = await fetch('/api/max/qr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'password', sessionId, password: suppliedPassword }),
      });
      if (!response.ok) {
        const failure = await response.json() as Partial<PortalEvent>;
        throw new Error('message' in failure && typeof failure.message === 'string'
          ? failure.message
          : 'MAX отклонил пароль.');
      }
    } catch (error) {
      setPhase('error');
      setMessage(error instanceof Error ? error.message : 'Не удалось проверить пароль.');
    }
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

async function consumeQrStream(
  controller: AbortController,
  onEvent: (event: PortalEvent) => void,
): Promise<void> {
  let terminal = false;
  try {
    const response = await fetch('/api/max/qr', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'start' }),
      signal: controller.signal,
    });
    if (!response.ok) {
      const failure = await response.json() as Partial<PortalEvent>;
      if (failure.type === 'error' && typeof failure.message === 'string') {
        onEvent(failure as PortalEvent);
        return;
      }
      throw new Error('QR request failed');
    }
    if (!response.body) throw new Error('QR stream is unavailable');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        const event = parsePortalEvent(line);
        if (!event) continue;
        onEvent(event);
        if (event.type === 'saved' || event.type === 'error') terminal = true;
      }
      if (done) break;
    }
    const finalEvent = parsePortalEvent(buffer);
    if (finalEvent) {
      onEvent(finalEvent);
      if (finalEvent.type === 'saved' || finalEvent.type === 'error') terminal = true;
    }
    if (!terminal && !controller.signal.aborted) throw new Error('QR stream ended early');
  } catch {
    if (controller.signal.aborted) return;
    onEvent({
      type: 'error',
      code: 'portal_failed',
      message: 'Защищённое соединение прервалось. Создайте новый QR.',
    });
  }
}

function parsePortalEvent(raw: string): PortalEvent | null {
  if (!raw || raw.length > 4096) return null;
  try {
    const value = JSON.parse(raw) as PortalEvent;
    return value && typeof value === 'object' && typeof value.type === 'string' ? value : null;
  } catch {
    return null;
  }
}
