# История изменений

Проект пока не выпущен; рабочая сборка находится в `integration/nightly`.

## [Unreleased]

### Добавлено

- исследование публичной web-версии MAX и protocol v10;
- импорт локальной MAX-сессии без автоматизации OTP/CAPTCHA;
- WebSocket listener с reconnect, ping, ack и обновлением session token;
- парсер входящих сообщений и вложений;
- fail-closed allowlist выбранных MAX-чатов и безопасный discovery-режим;
- Telegram sender с HTML escaping, разбиением и retry/backoff;
- durable SQLite outbox, дедупликация и восстановление pending delivery;
- CLI-проверки конфигурации, Telegram и локального состояния;
- SQLite integrity/recoverability check с fail-closed startup для legacy pending;
- редактирование секретов в логах, лимиты frame/decompression и process lock;
- тесты Python 3.10/3.12, property/fuzz cases и SHA-pinned GitHub Actions.

### Ограничения

- login и реальный входящий push не проверены без MAX-профиля;
- endpoint и payload неофициальные и могут измениться;
- серверные PR в `main` ожидают GitHub-authenticated review и account-gated smoke.
