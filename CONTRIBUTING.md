# Участие в разработке

Проект работает поверх неофициального web-протокола MAX. Любое изменение
протокола должно сопровождаться обезличенным fixture или синтетическим тестом;
публиковать токены, cookie, номера телефонов и содержимое личных чатов нельзя.

## Ветки и review

1. Создайте отдельную ветку от актуальной `integration/nightly`.
2. Добавьте код, тесты и связанную документацию одним небольшим изменением.
3. Выполните локальные проверки и отправьте ветку на GitHub.
4. Откройте PR. До ручной проверки реальной MAX-сессии base должен оставаться
   `integration/nightly`; release PR в `main` создаётся отдельно.

Примеры имён: `feature/chat-filter`, `fix/reconnect-ack`, `docs/session-import`.

## Локальные проверки

```bash
python -m pip install -e '.[dev]'
ruff format --check .
ruff check .
pytest --cov=max_to_telegram --cov-report=term-missing --cov-fail-under=85
python -m build
git diff --check
```

Opt-in публичный handshake:

```bash
RUN_LIVE_MAX_RESEARCH=1 pytest -m live tests/test_live_handshake.py -v
```

Он подтверждает доступность публичного WebSocket и init frame, но не заменяет
закрытый тест login/message push с авторизованным профилем.

## Безопасность тестовых данных

- используйте вымышленные числовые ID и токены вида `test-token`;
- храните локальные секреты только в игнорируемом `.env` с правами `0600`;
- перед commit выполните поиск потенциальных секретов;
- в exception и snapshot не должно быть URL Telegram Bot API с токеном;
- HAR и payload из реального аккаунта сначала обезличиваются локально.

Уязвимости не публикуйте в открытом issue. Порядок сообщения описан в
[`SECURITY.md`](SECURITY.md).
