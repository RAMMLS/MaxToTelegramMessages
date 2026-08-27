'use client';

import { useState } from 'react';

export function PortalGate() {
  const [accessKey, setAccessKey] = useState('');
  const [message, setMessage] = useState('Введите код доступа, полученный от владельца моста');
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!accessKey || busy) return;
    setBusy(true);
    setMessage('Проверяем код…');
    try {
      const response = await fetch('/api/portal/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ accessKey }),
      });
      if (response.ok) {
        location.replace('/');
        return;
      }
      setMessage(response.status === 429
        ? 'Слишком много попыток. Подождите 10 минут.'
        : 'Неверный код доступа.');
    } catch {
      setMessage('Сеть недоступна. Попробуйте ещё раз.');
    } finally {
      setAccessKey('');
      setBusy(false);
    }
  };

  return (
    <main className="portal-shell">
      <section className="access-card" aria-labelledby="access-title">
        <div className="brand-mark" aria-hidden="true">M</div>
        <p className="eyebrow">MAX → Telegram</p>
        <h1 id="access-title">Закрытый доступ</h1>
        <p className="access-copy">{message}</p>
        <form className="access-form" onSubmit={submit}>
          <label htmlFor="portal-access-key">Код доступа</label>
          <input
            id="portal-access-key"
            type="password"
            autoComplete="current-password"
            value={accessKey}
            onChange={(event) => setAccessKey(event.target.value)}
            maxLength={256}
            autoFocus
          />
          <button className="primary-button" type="submit" disabled={!accessKey || busy}>
            {busy ? 'Проверяем…' : 'Открыть QR-портал'}
          </button>
        </form>
        <p className="access-note">Код не сохраняется в браузере. Сессия доступа действует 12 часов.</p>
      </section>
    </main>
  );
}
