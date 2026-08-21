# Исследование веб-версии MAX

Дата исследования: 2026-08-22  
Целевой клиент: `https://web.max.ru/`  
Версия клиентского bundle на момент исследования: `26.8.8`

## Краткий вывод

Веб-клиент MAX получает личные сообщения через закрытый бинарный WebSocket-протокол по адресу:

```text
wss://api.oneme.ru/websocket
```

Основная авторизация не основана на cookie. Клиент сохраняет в browser storage объект `__oneme_auth` с полями `viewerId` и `token`, а при восстановлении сессии передаёт этот токен командой opcode `19` внутри WebSocket.

Новые сообщения сервер отправляет push-командой opcode `128`. Полезная нагрузка содержит `chatId`, объект `message` и служебные отметки непрочитанного состояния. Получатель подтверждает push отдельным кадром с тем же opcode и идентификаторами чата/сообщения.

Протокол воспроизведён без аккаунта до этапа создания QR-сессии. Авторизованный login и реальное входящее сообщение не были сняты: у владельца проекта пока нет профиля MAX. Поэтому поля login/message ниже разделены на:

- подтверждённые живым сетевым обменом;
- подтверждённые статическим анализом актуального клиентского bundle;
- ещё не подтверждённые авторизованным аккаунтом.

## Важная альтернатива: официальный MAX Bot API

У MAX существует официальный Bot API на `https://platform-api2.max.ru`. Он поддерживает Webhook и `GET /updates`, но отдаёт события только из диалогов, групповых чатов и каналов, доступных самому MAX-боту. Он не предоставляет MAX-боту произвольный личный inbox обычного пользователя.

Если исходный продуктовый сценарий можно изменить так, чтобы люди писали MAX-боту, следует использовать официальный API: это стабильнее и безопаснее reverse engineering веб-клиента. Для зеркалирования личного inbox обычного пользователя официальный Bot API задачу не решает.

Официальные источники:

- [MAX Bot API](https://dev.max.ru/docs-api)
- [Long Polling `GET /updates`](https://dev.max.ru/docs-api/methods/GET/updates)
- [Webhook `POST /subscriptions`](https://dev.max.ru/docs-api/methods/POST/subscriptions)

## Методика

Исследование состояло из трёх частей:

1. Открытие `max.ru` и `web.max.ru` во встроенном браузере, просмотр экрана входа, консольных событий и загруженных ресурсов.
2. Статический анализ JavaScript-модулей, которые реально загрузил `web.max.ru`.
3. Независимое воспроизведение анонимного WebSocket handshake и QR-команд небольшим Python-клиентом на `websockets`, `msgpack` и `lz4`.

Никакие токены, QR-ссылки, `trackId`, номера телефонов или иные учётные данные в репозиторий не записывались.

## Подтверждено живым трафиком

### Открытие соединения

Браузерный клиент вывел последовательность:

```text
[0] connection attempt #0
[0] awaiting network...
[0] network online, connecting...
[0] connection opened
[0] session inited
```

Независимый Python-клиент успешно открыл `wss://api.oneme.ru/websocket` с Origin `https://web.max.ru`, отправил init-команду и получил ответ без cookie и пользовательского токена.

### Начальный кадр, opcode 6

Клиент отправляет command-кадр:

```json
{
  "cmd": 0,
  "seq": 0,
  "opcode": 6,
  "payload": {
    "userAgent": {
      "deviceType": "WEB",
      "pushDeviceType": "WEBPUSH",
      "locale": "ru",
      "deviceLocale": "ru",
      "osVersion": "...",
      "deviceName": "...",
      "headerUserAgent": "...",
      "isPwa": false,
      "appVersion": "26.8.8",
      "screen": "...",
      "timezone": "Europe/Moscow"
    },
    "deviceId": "<UUID>"
  }
}
```

Сервер ответил кадром `cmd=1`, `seq=0`, `opcode=6`, protocol version `10`. Ответ был LZ4-сжат и после декодирования содержал публичную конфигурацию с ключами:

```json
{
  "lang": "...",
  "location": "...",
  "phone-auth-enabled": true,
  "reg-country-code": "...",
  "web-pwa-promo": "..."
}
```

### QR-авторизация до подтверждения профилем

Создание QR-сессии доступно до login:

```text
opcode 288, payload отсутствует
```

Ответ `cmd=1`, `opcode=288` содержит:

```json
{
  "trackId": "<REDACTED>",
  "qrLink": "https://<REDACTED>",
  "expiresAt": 0,
  "pollingInterval": 5000,
  "ttl": 0
}
```

Числовые значения показаны только как типы/пример формы; реальные срок и TTL меняются от сессии к сессии.

Статус проверяется командой:

```json
{
  "cmd": 0,
  "opcode": 289,
  "payload": {"trackId": "<REDACTED>"}
}
```

До сканирования сервер вернул `status.expiresAt`. После подтверждения клиент ожидает `status.loginAvailable`, затем завершает QR-login командой opcode `291` с `trackId`.

## Формат бинарного WebSocket-протокола

Каждый кадр начинается с 10-байтового заголовка. Числа в заголовке записаны в big-endian, как в браузерном `DataView` без флага little-endian.

| Смещение | Размер | Поле | Наблюдение |
|---:|---:|---|---|
| 0 | 1 | version | Текущее значение `10` |
| 1 | 1 | cmd | `0` command/push, `1` success/ack, `3` error; `2` игнорируется клиентом |
| 2 | 2 | seq | signed int16, порядковый номер |
| 4 | 2 | opcode | signed int16, код операции |
| 6 | 1 | compression | `0` без сжатия; положительное значение используется как оценка размера LZ4 output |
| 7 | 3 | payload length | длина payload в байтах, unsigned 24-bit big-endian |
| 10 | N | payload | MessagePack, при необходимости LZ4 block |

Сжатие включается клиентом для MessagePack payload длиннее 32 байт. В байте compression записывается `ceil(uncompressed_size / compressed_size)`, максимум 255. При распаковке клиент выделяет до `compressed_size * compression` байт.

MessagePack extension type `1` применяется для 64-битных целых. Значения, помещающиеся в безопасный диапазон JavaScript, клиент приводит к Number; остальные остаются BigInt.

## Аутентификация и хранение сессии

### Подтверждено статическим анализом клиента

Веб-клиент использует следующие storage-ключи:

```text
__oneme_auth
__oneme_device_id
__oneme_calls_auth_token
```

Для моста нужен только основной `__oneme_auth`. Его логическая форма:

```json
{
  "viewerId": "<USER_ID>",
  "token": "<SESSION_TOKEN>"
}
```

Точная реализация storage-обёртки может добавлять сериализацию/namespace. Получать значение следует из DevTools уже авторизованного `web.max.ru` и никогда не коммитить.

После init клиент ставит восстановленную пару в очередь как логическое событие opcode `23`, затем отправляет login opcode `19`:

```json
{
  "token": "<SESSION_TOKEN>",
  "chatsCount": 15,
  "lastLogin": 0,
  "interactive": false,
  "chatsSync": 0,
  "contactsSync": 0,
  "presenceSync": -1,
  "draftsSync": 0,
  "configHash": ""
}
```

Поля sync/cache динамические и могут быть опущены или изменены. После успешного opcode `19` клиент запоминает новое время login и считает последующие команды авторизованными.

### Телефонный login

Клиент содержит login по телефону, OTP, CAPTCHA и 2FA. Выявлены основные opcodes:

- `224` — подготовка CAPTCHA/phone auth;
- `17` — отправка/повтор кода;
- `18` — проверка OTP;
- `115` — 2FA password;
- `101`, `109`, `110`, `111`, `112`, `116` — дополнительные 2FA/recovery операции.

Автоматизация этого потока в первой версии не рекомендуется: CAPTCHA и 2FA требуют интерактивного участия, а поведение и ограничения могут меняться. Практичный MVP импортирует уже созданную веб-сессию через `.env` или локальный session-файл вне Git.

## Получение новых сообщений

### Push opcode 128

Подтверждено статическим анализом актуального клиента: сервер отправляет новое или изменённое сообщение кадром `cmd=0`, `opcode=128`.

Ожидаемая форма payload:

```json
{
  "chatId": 123,
  "postId": 0,
  "chat": {"id": 123, "type": "DIALOG"},
  "message": {
    "id": "<MESSAGE_ID>",
    "time": 0,
    "updateTime": 0,
    "sender": 456,
    "text": "Текст сообщения",
    "elements": [],
    "attaches": [],
    "status": null,
    "type": "USER"
  },
  "unread": 1,
  "mark": 0
}
```

Не все поля обязательны. `message.id`, числовые идентификаторы и временные отметки могут декодироваться как `int` или `BigInt`/MessagePack extension. Для обычного текстового сообщения важны:

- `chatId` — чат;
- `message.id` — идентификатор и ключ дедупликации;
- `message.sender` — ID отправителя;
- `message.text` — текст;
- `message.time` — время;
- `message.status` — `null`, `EDITED`, `REMOVED` и другие состояния;
- `message.attaches` — вложения и служебные события.

Для каждого push opcode `128` клиент сразу отправляет подтверждение:

```json
{
  "cmd": 1,
  "seq": "<SERVER_SEQ>",
  "opcode": 128,
  "payload": {
    "chatId": "<PAYLOAD_CHAT_ID>",
    "messageId": "<PAYLOAD_MESSAGE_ID>"
  }
}
```

Это подтверждение нужно отправлять до медленной доставки в Telegram, иначе reconnect может привести к повторам.

### Ping и поддержание соединения

- Server push opcode `1` подтверждается кадром `cmd=1`, с тем же `seq/opcode=1`, без payload.
- После login веб-клиент раз в 30 секунд отправляет command opcode `1` с `{ "interactive": false }`.
- При разрыве используется exponential backoff с jitter, ограниченный примерно 10 секундами.
- Невыполненные команды могут быть повторно поставлены в очередь после reconnect.

### Контакты и имена отправителей

В push гарантированно виден `message.sender` как ID, но человекочитаемое имя может находиться в `chat`, кэше контактов или login/resync payload. Реализация должна:

1. использовать имя из вложенного `chat`/`contact`, если оно присутствует;
2. вести локальный кэш `contact_id -> display name` из login/resync;
3. безопасно падать обратно на `MAX user <id>`.

До авторизованного capture точные поля имени считаются неподтверждёнными.

## HTTP-запросы

Core messaging/login в исследованном клиенте идёт через WebSocket. Из HTTP/XHR на login-экране наблюдалась в основном телеметрия `sdk-api.apptracer.ru` и статические ресурсы `web.max.ru`; cookie не понадобились для WebSocket init/QR.

Официальный Bot API — отдельный HTTPS JSON API на `platform-api2.max.ru` и не является транспортом личной веб-сессии.

## Риски и ограничения

- Протокол неофициальный, без гарантий совместимости; opcode, схема payload, домен и auth-flow могут измениться без предупреждения.
- Использование пользовательского session token вне официального клиента может нарушать правила сервиса. Владелец должен самостоятельно проверить актуальные условия MAX.
- Session token равнозначен доступу к аккаунту. Его нельзя логировать, коммитить, отправлять в issue/PR или включать в диагностические архивы.
- Без профиля MAX не выполнены end-to-end login, capture реального opcode `128`, получение display name и проверка token refresh/revocation.
- Вложения пока исследованы только на уровне общей схемы `attaches`; первая версия должна честно пересылать текст и краткое описание неизвестного вложения, не теряя событие.
- Повторы возможны после reconnect. Дедупликация по `(chatId, message.id, status/updateTime)` обязательна.

## Что должно быть проверено после появления профиля MAX

1. Войти в `web.max.ru` по QR и подтвердить точный сериализованный вид `__oneme_auth` без публикации значения.
2. Снять один личный текстовый входящий opcode `128` и сравнить со схемой выше.
3. Проверить личный диалог, группу, канал, системное событие, edit и delete.
4. Проверить отображаемое имя отправителя и fallback по ID.
5. Прервать сеть, дождаться reconnect и убедиться, что сообщение не дублируется в Telegram.
6. Проверить logout/revoke: клиент должен удалить локальную сессию и остановиться без бесконечного login-loop.

## Рекомендуемая архитектура MVP

```text
.env / локальный session JSON
        ↓
MAX session loader
        ↓
binary WebSocket codec ── ack/ping/reconnect
        ↓
message parser + deduplication
        ↓
chat allowlist (`MAX_CHAT_IDS`)
        ↓
bounded async queue
        ↓
Telegram Bot API sendMessage
```

Сетевой listener не должен зависеть от Telegram latency. Сначала декодировать и подтвердить MAX push, затем нормализовать событие и проверить точный `chatId` по allowlist. Только разрешённое событие попадает в ограниченную очередь. Telegram sender выполняет retry только для временных ошибок и учитывает `retry_after` при HTTP 429.

Фильтр должен быть fail-closed: пустой `MAX_CHAT_IDS` означает «не пересылать ничего», а не «пересылать всё». Для первоначальной настройки нужен discovery/dry-run режим, который показывает встреченные `chatId` и доступные названия, но не вызывает Telegram API. Имена допустимы только как подсказка оператору; рабочий allowlist хранит стабильные числовые ID, чтобы переименование чата не меняло политику доставки.

## Итог по уровню подтверждения

| Находка | Уровень |
|---|---|
| `wss://api.oneme.ru/websocket` | live + bundle |
| Protocol version 10, 10-byte header | live + bundle |
| MessagePack + LZ4 | live + bundle |
| Init opcode 6 | live + bundle |
| QR opcodes 288/289 | live + bundle |
| QR finalize opcode 291 | bundle |
| storage `__oneme_auth` с `viewerId/token` | bundle |
| Login opcode 19 | bundle |
| Message push opcode 128 и ack | bundle |
| Реальный payload личного сообщения | не проверен без аккаунта |
| Token renewal/revocation | не проверен без аккаунта |
