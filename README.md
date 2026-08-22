# MAX → Telegram bridge

[![CI](https://github.com/RAMMLS/MaxToTelegramMessages/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/RAMMLS/MaxToTelegramMessages/actions/workflows/ci.yml?query=branch%3Amain)

Асинхронный Python-мост для личного использования: читает входящие сообщения
авторизованной веб-сессии MAX и отправляет уведомления в заданный Telegram-чат.

Ключевое правило проекта — **пересылаются только явно выбранные MAX-чаты**.
Фильтр fail-closed: пустой `MAX_CHAT_IDS` не означает «все чаты», а останавливает
обычный режим с ошибкой конфигурации.

> [!WARNING]
> MAX WebSocket API неофициальный. Реальный login и входящий opcode `128`
> подтверждены с импортированной web-сессией, но полный маршрут первого нового
> поста выбранного канала в Telegram всё ещё требует ручной проверки. См.
> [RESEARCH.md](RESEARCH.md) и [MANUAL_TEST.md](MANUAL_TEST.md).

## Что реализовано

- импорт существующей MAX web-сессии из `.env` или защищённого JSON-файла;
- бинарный protocol v10: MessagePack, LZ4, 10-байтовый заголовок;
- WebSocket init/login, ping/ack, reconnect с exponential backoff и jitter;
- немедленный reconnect при отказе фонового application keepalive;
- парсер входящих сообщений, edits и вложений;
- fail-closed отсечение removed/service и всех неподтверждённых не-`USER` типов;
- точный allowlist числовых `chatId`;
- discovery-режим, который показывает встреченные чаты и не обращается к Telegram;
- bounded async queue и локальный durable outbox SQLite;
- SQLite checkpoint после каждой принятой Telegram-части длинного сообщения;
- дедупликация по ревизии MAX-сообщения;
- opaque SHA-256 dedupe keys без chat/message ID в delivered history;
- single-instance lock через `flock` на POSIX и `msvcrt` на Windows;
- Telegram `sendMessage`, безопасный HTML, разбиение длинного текста;
- retry для network errors, HTTP `429`, `408`, `425` и `5xx`;
- редактирование секретов в логах и корректное завершение по SIGINT/SIGTERM;
- до 20 секунд graceful drain после сигнала с forced cancel и durable recovery;
- строгие типы ID/time и границы размера attachment metadata из private protocol;
- bounded queue/retry/reconnect settings и безопасный backoff при долгом outage;
- permanent MAX command errors останавливают процесс без retry storm;
- абсолютный deadline init/login не продлевается посторонними frames;
- MAX session token отправляется на закреплённый `api.oneme.ru`; custom endpoint
  требует явного `MAX_ALLOW_CUSTOM_WS_URL=true`;
- тесты, Ruff, строгая типизация, dependency audit и GitHub Actions для Python
  3.10/3.12 на Linux и Python 3.12 на Windows.

Проверенный bridge выпущен в `main`. Перед постоянным запуском всё равно нужен
ручной account-gated тест выбранного MAX-канала и ротация раскрытых credentials.

Исследование также подтвердило существование официального MAX Bot API. Если
сообщения могут приходить непосредственно MAX-боту, лучше использовать его.
Этот проект нужен для личного inbox обычного пользователя, который Bot API не
отдаёт.

## Требования

- Python 3.10+;
- существующий профиль и авторизованная web-сессия MAX;
- Telegram-бот и целевой Telegram `chat_id`;
- Linux, macOS или Windows с поддержкой Python-зависимостей проекта. На Windows
  пакет автоматически устанавливает IANA timezone-базу `tzdata`.

## Быстрый старт

```bash
git clone git@github.com:RAMMLS/MaxToTelegramMessages.git
cd MaxToTelegramMessages

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'

cp .env.example .env
chmod 600 .env
```

Автозагрузка рассматривает только `.env` в текущем каталоге и не ищет файл в
родительских директориях. Поэтому запускайте CLI из каталога проекта либо
задавайте process environment/systemd `EnvironmentFile` явно.

Заполните `.env`, затем проверьте его без сетевых соединений:

```bash
max-to-telegram --check-config
```

Публичный MAX init можно проверить вообще без credentials; команда не создаёт QR
и не выполняет login. Для этой команды `.env` намеренно не читается:

```bash
max-to-telegram --check-max-public
```

Она выводит только hostname, protocol/opcode, факт compression и количество
ключей публичной конфигурации — без значений ответа.

После настройки Telegram можно проверить bot token и доступ к целевому чату без
отправки сообщения:

```bash
max-to-telegram --check-telegram
```

Состояние durable outbox можно посмотреть без вывода содержимого сообщений:

```bash
max-to-telegram --check-state
```

Команда также выполняет SQLite `quick_check` и отдельно считает recoverable и
legacy pending-записи без тела сообщения. При ненулевом
`unrecoverable_pending` обычный запуск останавливается до подключения к MAX:
такое состояние нужно разобрать вручную, чтобы не скрыть возможную потерю
уведомления.
Аналогично `invalid_progress` блокирует запуск, если JSON checkpoint частей
повреждён или содержит недопустимые Telegram message IDs.
SQLite schema имеет `user_version=1`; bridge мигрирует поддерживаемую legacy
таблицу, но не открывает DB от более новой/несовместимой версии.

Запуск:

```bash
max-to-telegram
```

Процесс возвращает код `2` для постоянной ошибки конфигурации, credentials,
protocol/state или запроса Telegram. Исчерпание retry из-за временной сети/API
возвращает `75` (`EX_TEMPFAIL`), чтобы supervisor мог безопасно перезапустить
мост и восстановить pending outbox.

Альтернативный вариант без console script:

```bash
python -m max_to_telegram
```

## MAX-аутентификация

Мост не автоматизирует телефон, CAPTCHA, OTP или 2FA. Он импортирует сессию,
которую пользователь уже создал в `https://web.max.ru/`.

После появления профиля:

1. Войдите в web MAX.
2. В DevTools откройте Application/Storage и найдите `__oneme_auth`.
3. Скопируйте `viewerId` и `token` только в локальный `.env`:

   ```dotenv
   MAX_VIEWER_ID=123456789
   MAX_AUTH_TOKEN=replace-with-local-session-token
   ```

Вместо двух переменных можно создать игнорируемый файл `.max-session.json`:

```json
{
  "viewerId": 123456789,
  "token": "replace-with-local-session-token"
}
```

```bash
chmod 600 .max-session.json
```

Файл `BRIDGE_STATE_DB` также должен быть обычным файлом с правами `0600` и не
может быть симлинком. Новый файл создаётся с приватными правами автоматически.
Session JSON и SQLite outbox должны указывать на разные непустые пути;
`--check-config` отклоняет управляющие символы, чрезмерную длину и неразрешимый
`~user` до открытия файлов.

И указать:

```dotenv
MAX_SESSION_FILE=.max-session.json
```

Не используйте оба способа одновременно. Session token равнозначен доступу к
аккаунту MAX.

В файловом режиме мост при первом рабочем запуске атомарно нормализует JSON,
добавляет стабильный `deviceId` и сохраняет обновлённый token, если login response
его вернул. Файл остаётся с правами `0600`. Исходный browser-export при этом
заменяется нормализованной формой, поэтому при необходимости заранее сохраните
его отдельную закрытую копию вне репозитория.

## Выбор конкретных MAX-чатов

### 1. Безопасное обнаружение ID

Сначала включите режим обнаружения:

```dotenv
MAX_DISCOVERY_MODE=true
MAX_CHAT_IDS=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Запустите мост и получите новое сообщение в каждом нужном MAX-чате. Для каждого
чата один раз появится строка без текста сообщения:

```text
Discovered MAX chat: chat_id=123456789 title='Семья'
```

Discovery не вызывает Telegram API, не пересылает сообщения и не пишет в лог
имя последнего отправителя. В обычном режиме MAX chat/message IDs и имена также
не попадают в журнал.

### 2. Точный allowlist

Выключите discovery и перечислите только выбранные числовые ID:

```dotenv
MAX_DISCOVERY_MODE=false
MAX_CHAT_IDS=123456789,-987654321
```

Проверка выполняется по полному signed-int64 `chatId`, не по имени чата; одна
конфигурация принимает не более 1000 ID. Поэтому
переименование чата не расширяет доступ. После удаления ID из allowlist даже
старое pending-сообщение этого чата не будет доставлено при рестарте.

## Telegram

1. Создайте бота через BotFather или используйте существующего.
2. Запишите новый token в `TELEGRAM_BOT_TOKEN` локального `.env`, временно
   оставив `TELEGRAM_CHAT_ID` пустым.
3. Откройте диалог с ботом и нажмите Start либо добавьте его в целевую группу и
   отправьте туда тестовое сообщение.
4. Получите только ID/тип/название недавних чатов, без вывода текста сообщений:

   ```bash
   max-to-telegram --discover-telegram-chats
   ```

   Эта команда не требует MAX credentials и не подтверждает/удаляет updates
   с помощью `offset`. Если бот использует webhook, Telegram может запретить
   одновременный `getUpdates`; тогда временно отключите webhook либо получите ID
   через уже настроенный обработчик.

5. Выберите нужный числовой `chat_id` и запишите значения только в `.env`:

   ```dotenv
   TELEGRAM_BOT_TOKEN=replace-with-bot-token
   TELEGRAM_CHAT_ID=123456789
   ```

Для группы `chat_id` обычно отрицательный. Telegram API не сможет отправлять
ботом в чат, где бот отсутствует или не имеет нужных прав.
Username вида `@channel` намеренно не принимается: числовой ID делает целевой
чат однозначным и не меняется при переименовании.

После выбора выполните `max-to-telegram --check-telegram`: команда вызывает
только `getMe` и `getChat`, не отправляя тестовое сообщение.
Обычный запуск повторяет тот же безопасный preflight до подключения к MAX, чтобы
ошибка token/destination не привела к чтению входящих сообщений без рабочего
канала доставки.

## Полный пример `.env`

```dotenv
MAX_VIEWER_ID=123456789
MAX_AUTH_TOKEN=replace-with-max-token
MAX_DEVICE_ID=123e4567-e89b-12d3-a456-426614174000

MAX_WS_URL=wss://api.oneme.ru/websocket
MAX_ALLOW_CUSTOM_WS_URL=false
MAX_APP_VERSION=26.8.8
MAX_LOCALE=ru
MAX_CHAT_IDS=111111111,-222222222
MAX_DISCOVERY_MODE=false

TELEGRAM_BOT_TOKEN=replace-with-telegram-token
TELEGRAM_CHAT_ID=333333333

BRIDGE_QUEUE_SIZE=100
BRIDGE_STATE_DB=.max-to-telegram.sqlite3
MAX_RECONNECT_MAX_SECONDS=10
TELEGRAM_MAX_RETRIES=5
LOG_LEVEL=INFO
```

`MAX_DEVICE_ID` необязателен. В файловом режиме UUID создаётся и сохраняется в
session JSON. При прямом `MAX_AUTH_TOKEN` из окружения UUID создаётся при каждом
старте, поэтому для стабильности лучше один раз задать `MAX_DEVICE_ID` явно.

## Надёжность и локальное состояние

Перед MAX protocol ACK нормализованное выбранное сообщение записывается в
`BRIDGE_STATE_DB`. Если локальная запись не удалась, ACK не отправляется. Если
payload выбранного `chatId` нельзя безопасно нормализовать, ACK также не
отправляется и мост останавливается с санитизированной ошибкой, чтобы не скрыть
потерю уведомления.

После успешного Telegram response запись помечается доставленной. При рестарте pending
outbox восстанавливается до обработки новых событий. Перед восстановлением мост
проверяет целостность SQLite и наличие тела у каждой pending-записи;
повреждённое или невосстановимое состояние блокирует запуск вместо молчаливого
пропуска.

Для длинного сообщения ID каждой успешно принятой Telegram-части записывается в
outbox сразу. После ошибки/restart sender продолжает со следующей части, не
повторяя уже зафиксированные chunks. Узкое окно между ответом Telegram и локальным
checkpoint всё равно остаётся, поэтому это уменьшает дубли, но не превращает
Bot API в exactly-once транспорт.
Checkpoint обновляется атомарно и может только расширять уже записанный префикс;
откат или замена ранее сохранённых Telegram IDs отклоняется.

Гарантия доставки практическая, а не математически exactly-once:

- падение после принятия Telegram части, но до её локального checkpoint, может
  повторить эту последнюю часть;
- неофициальный сервер может изменить семантику ack/replay.

SQLite-файл содержит текст только у незавершённых доставок и должен оставаться
локальным. Он, `.env`, session JSON, WAL/SHM и виртуальные окружения исключены из
Git.
После доставки SQLite сохраняет только opaque digest ревизии и Telegram message
IDs; MAX chat/message IDs из dedupe key восстановить нельзя.

Рядом со state DB создаётся process-lock без пользовательских данных (на Windows
он содержит один служебный байт). Он не удаляется после остановки, но OS-lock
освобождается автоматически. Одновременно
может работать только один экземпляр моста с данным `BRIDGE_STATE_DB`, иначе
второй процесс завершится до доставки и не создаст дубли.

## Проверки разработчика

```bash
ruff format --check .
ruff check .
mypy
mypy --platform win32 src
pip-audit --strict --progress-spinner=off .
pytest
pytest --cov=max_to_telegram --cov-report=term-missing --cov-fail-under=85
git diff --check
```

Опциональная проверка только публичного MAX handshake:

```bash
RUN_LIVE_MAX_RESEARCH=1 pytest -m live tests/test_live_handshake.py -v
```

Она не использует профиль и не проверяет login/messages.

## Запуск как systemd service

Пример unit находится в [`deploy/max-to-telegram.service`](deploy/max-to-telegram.service).
Скопируйте проект в `/opt/max-to-telegram`, а закрытый env-файл — в
`/etc/max-to-telegram/bridge.env`:

```bash
sudo useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin maxbridge
sudo install -d -m 700 /etc/max-to-telegram
sudo install -m 600 .env /etc/max-to-telegram/bridge.env
sudo install -d -o maxbridge -g maxbridge -m 700 /var/lib/max-to-telegram
# Только при файловом импорте MAX-сессии:
sudo install -o maxbridge -g maxbridge -m 600 .max-session.json \
  /var/lib/max-to-telegram/session.json
sudo install -m 644 deploy/max-to-telegram.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now max-to-telegram
sudo journalctl -u max-to-telegram -f
```

В `/etc/max-to-telegram/bridge.env` используйте абсолютные writable-пути:

```dotenv
MAX_SESSION_FILE=/var/lib/max-to-telegram/session.json
BRIDGE_STATE_DB=/var/lib/max-to-telegram/state.sqlite3
```

Если session token задан напрямую через `MAX_AUTH_TOKEN`, строка
`MAX_SESSION_FILE` не нужна. Код в `/opt/max-to-telegram` остаётся read-only;
обновляемая сессия и outbox находятся в закрытом `StateDirectory`. Пути и
`User=` в unit нужно адаптировать к серверу. Не запускайте мост от root.

Unit не перезапускает exit code `2` (исправьте конфигурацию/credentials/state и
запустите сервис снова), но автоматически перезапускает временный exit `75`,
после чего durable outbox восстанавливает недоставленные уведомления. Unit
проверен `systemd-analyze verify`; offline security exposure на systemd 252 —
`2.8 OK`.

SIGINT/SIGTERM сначала останавливает MAX listener и даёт worker до 20 секунд на
завершение уже поставленных в очередь отправок. Затем задача отменяется; все не
зафиксированные как delivered записи остаются pending и поднимаются при следующем
старте. Второй сигнал отменяет grace period немедленно.

## Бесплатный 24/7-хостинг на alwaysdata

Для личного low-traffic bridge можно использовать alwaysdata Free Public Cloud:
custom service работает в foreground, автоматически перезапускается, а файлы
сессии и SQLite остаются в persistent home directory. Готовые bootstrap,
безопасный env-шаблон и пошаговая настройка находятся в
[`deploy/alwaysdata/README.md`](deploy/alwaysdata/README.md).

Перед передачей конфигурации на хостинг обязательно перевыпустите MAX session и
Telegram bot token, если они появлялись в чате или логах. Секреты загружаются
только в ignored-файлы `.env` и `data/max-session.json`, не в Git и не в команду
alwaysdata service.

## Ограничения текущей версии

- нет полного end-to-end нового поста выбранного MAX-канала в Telegram;
- реальные поля разных типов MAX-чатов и вложений требуют capture;
- изменения приватного web protocol могут потребовать обновления codec/client;
- пересылается текст и ярлык вложения, но не бинарный файл;
- одна конфигурация отправляет все выбранные MAX-чаты в один Telegram-чат.

План обязательной ручной проверки оформлен в
[`MANUAL_TEST.md`](MANUAL_TEST.md). История веток и статус PR находятся в
[`DEVELOPMENT.md`](DEVELOPMENT.md).

## Безопасность

- никогда не коммитьте `.env`, session JSON и SQLite state;
- используйте `chmod 600` для локальных секретов;
- не прикладывайте токены к issue, PR, логам или скриншотам;
- после случайной публикации немедленно отзовите/перевыпустите токен;
- начните с `MAX_DISCOVERY_MODE=true`, затем задайте минимальный allowlist;
- перед эксплуатацией проверьте актуальные условия использования MAX.

## Лицензия

[MIT](LICENSE)
