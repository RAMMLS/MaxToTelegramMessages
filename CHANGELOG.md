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
- отдельный exit code `75` для временного исчерпания Telegram retry;
- systemd restart policy и усиленная sandbox-изоляция, проверенные `systemd-analyze`;
- fail-closed отказ от ACK для ненормализуемого сообщения выбранного MAX-чата;
- parser bounds для числовых полей и массива/типов вложений;
- верхние границы resource settings и overflow-safe reconnect backoff;
- data-minimized MAX discovery без имени последнего отправителя;
- однозначный MAX auth source и строгий числовой Telegram destination ID;
- CI timeout и невыводящий secret-pattern guard для tracked файлов;
- race-resistant descriptor reads для `.env` и MAX session JSON;
- обязательный Telegram getMe/getChat preflight до подключения к MAX;
- credential-free CLI probe публичного MAX opcode 6;
- минимизация пользовательских значений в config/outbox errors;
- канонические opaque SHA-256 dedupe keys без MAX identifiers в delivered rows;
- versioned SQLite outbox schema с проверкой совместимости и trusted_schema=OFF;
- публичный MAX probe не читает `.env` и локальные credentials;
- `.env` загружается только из текущего каталога без parent-directory search;
- keepalive task failure немедленно прерывает blocked WebSocket receive для reconnect;
- MAX command errors отделены от transient transport reconnect;
- opt-in live gate для production public-probe path;
- абсолютный MAX command deadline поверх потока unrelated frames;
- pin MAX WebSocket endpoint с отдельным opt-in для protocol research;
- нормализация Telegram error code/description против утечек и log injection;
- signed-int64 границы для MAX viewer/chat/sender/time identifiers;
- удаление MAX identifiers и имён из обычных delivery/policy логов;
- ранние лимиты нормализации MAX/Telegram remote metadata;
- fail-closed policy для служебных и неизвестных не-`USER` message types;
- 20-секундный graceful shutdown drain с bounded forced cancel;
- редактирование секретов в логах, лимиты frame/decompression и process lock;
- тесты Python 3.10/3.12, property/fuzz cases и SHA-pinned GitHub Actions.

### Ограничения

- login и реальный входящий push не проверены без MAX-профиля;
- endpoint и payload неофициальные и могут измениться;
- серверные PR в `main` ожидают GitHub-authenticated review и account-gated smoke.
