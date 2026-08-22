# MAX → Telegram bridge

Асинхронный Python-мост для личного использования: читает входящие сообщения
авторизованной веб-сессии MAX и отправляет уведомления в заданный Telegram-чат.

Ключевое правило проекта — **пересылаются только явно выбранные MAX-чаты**.
Фильтр fail-closed: пустой `MAX_CHAT_IDS` не означает «все чаты», а останавливает
обычный режим с ошибкой конфигурации.

> [!WARNING]
> MAX WebSocket API неофициальный. Реальный login и входящий message push пока
> не проверены end-to-end, потому что для исследования не было профиля MAX.
> Подтверждены публичный handshake, бинарный протокол и QR-сессия; login и
> opcode сообщения восстановлены из актуального web bundle. См. [RESEARCH.md](RESEARCH.md).

## Что реализовано

- импорт существующей MAX web-сессии из `.env` или защищённого JSON-файла;
- бинарный protocol v10: MessagePack, LZ4, 10-байтовый заголовок;
- WebSocket init/login, ping/ack, reconnect с exponential backoff и jitter;
- парсер входящих сообщений, edits и вложений;
- точный allowlist числовых `chatId`;
- discovery-режим, который показывает встреченные чаты и не обращается к Telegram;
- bounded async queue и локальный durable outbox SQLite;
- дедупликация по ревизии MAX-сообщения;
- Telegram `sendMessage`, безопасный HTML, разбиение длинного текста;
- retry для network errors, HTTP `429`, `408`, `425` и `5xx`;
- редактирование секретов в логах и корректное завершение по SIGINT/SIGTERM;
- тесты, Ruff и GitHub Actions для Python 3.10 и 3.12.

Исследование также подтвердило существование официального MAX Bot API. Если
сообщения могут приходить непосредственно MAX-боту, лучше использовать его.
Этот проект нужен для личного inbox обычного пользователя, который Bot API не
отдаёт.

## Требования

- Python 3.10+;
- существующий профиль и авторизованная web-сессия MAX;
- Telegram-бот и целевой Telegram `chat_id`;
- Linux, macOS или другая ОС с поддержкой Python-зависимостей проекта.

## Быстрый старт

```bash
git clone git@github.com:RAMMLS/MaxToTelegramMessages.git
cd MaxToTelegramMessages
git switch integration/nightly

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'

cp .env.example .env
chmod 600 .env
```

`main` намеренно содержит только проверенный начальный commit. До account-gated
проверки рабочая сборка находится в `integration/nightly`.

Заполните `.env`, затем проверьте его без сетевых соединений:

```bash
max-to-telegram --check-config
```

После настройки Telegram можно проверить bot token и доступ к целевому чату без
отправки сообщения:

```bash
max-to-telegram --check-telegram
```

Состояние durable outbox можно посмотреть без вывода содержимого сообщений:

```bash
max-to-telegram --check-state
```

Запуск:

```bash
max-to-telegram
```

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
Discovered MAX chat: chat_id=123456789 title='Семья' last_sender='Имя'
```

Discovery не вызывает Telegram API и не пересылает сообщения.

### 2. Точный allowlist

Выключите discovery и перечислите только выбранные числовые ID:

```dotenv
MAX_DISCOVERY_MODE=false
MAX_CHAT_IDS=123456789,-987654321
```

Проверка выполняется по полному числовому `chatId`, не по имени чата. Поэтому
переименование чата не расширяет доступ. После удаления ID из allowlist даже
старое pending-сообщение этого чата не будет доставлено при рестарте.

## Telegram

1. Создайте бота через BotFather или используйте существующего.
2. Откройте диалог с ботом и нажмите Start либо добавьте его в целевую группу.
3. Узнайте числовой `chat_id` через `getUpdates` или другой доверенный инструмент.
4. Запишите значения только в `.env`:

   ```dotenv
   TELEGRAM_BOT_TOKEN=replace-with-bot-token
   TELEGRAM_CHAT_ID=123456789
   ```

Для группы `chat_id` обычно отрицательный. Telegram API не сможет отправлять
ботом в чат, где бот отсутствует или не имеет нужных прав.

## Полный пример `.env`

```dotenv
MAX_VIEWER_ID=123456789
MAX_AUTH_TOKEN=replace-with-max-token
MAX_DEVICE_ID=123e4567-e89b-12d3-a456-426614174000

MAX_WS_URL=wss://api.oneme.ru/websocket
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
`BRIDGE_STATE_DB`. Если локальная запись не удалась, ACK не отправляется. После
успешного Telegram response запись помечается
доставленной. При рестарте pending outbox восстанавливается до обработки новых
событий.

Гарантия доставки практическая, а не математически exactly-once:

- падение после принятия Telegram части длинного сообщения, но до фиксации всей
  доставки может повторить уже принятую часть;
- неофициальный сервер может изменить семантику ack/replay.

SQLite-файл содержит текст только у незавершённых доставок и должен оставаться
локальным. Он, `.env`, session JSON, WAL/SHM и виртуальные окружения исключены из
Git.

Рядом со state DB создаётся пустой process-lock. Он не содержит данных и не
удаляется после остановки, но OS-lock освобождается автоматически. Одновременно
может работать только один экземпляр моста с данным `BRIDGE_STATE_DB`, иначе
второй процесс завершится до доставки и не создаст дубли.

## Проверки разработчика

```bash
ruff format --check .
ruff check .
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
sudo install -d -m 700 /etc/max-to-telegram
sudo install -m 600 .env /etc/max-to-telegram/bridge.env
sudo install -m 644 deploy/max-to-telegram.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now max-to-telegram
sudo journalctl -u max-to-telegram -f
```

Пути и `User=` в unit нужно адаптировать к серверу. Не запускайте мост от root.

## Ограничения текущей версии

- нет account-gated end-to-end теста без профиля MAX;
- реальные поля разных типов MAX-чатов и вложений требуют capture;
- автоматическое обновление импортированного session token пока не сохраняется;
- изменения приватного web protocol могут потребовать обновления codec/client;
- пересылается текст и ярлык вложения, но не бинарный файл;
- одна конфигурация отправляет все выбранные MAX-чаты в один Telegram-чат.

План обязательной ручной проверки перечислен в [RESEARCH.md](RESEARCH.md). История
веток и статус PR находятся в [DEVELOPMENT.md](DEVELOPMENT.md).

## Безопасность

- никогда не коммитьте `.env`, session JSON и SQLite state;
- используйте `chmod 600` для локальных секретов;
- не прикладывайте токены к issue, PR, логам или скриншотам;
- после случайной публикации немедленно отзовите/перевыпустите токен;
- начните с `MAX_DISCOVERY_MODE=true`, затем задайте минимальный allowlist;
- перед эксплуатацией проверьте актуальные условия использования MAX.

## Лицензия

[MIT](LICENSE)
