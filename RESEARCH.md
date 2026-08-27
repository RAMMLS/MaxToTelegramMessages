# Исследование веб-версии MAX

Дата исследования: 2026-08-22
Последняя перепроверка публичного протокола: 2026-08-27
Целевой клиент: `https://web.max.ru/`
Версия клиентского bundle на момент исследования: `26.8.8`

## Краткий вывод

Веб-клиент MAX получает личные сообщения через закрытый бинарный WebSocket-протокол по адресу:

```text
wss://api.oneme.ru/websocket
```

Основная авторизация не основана на cookie. Клиент сохраняет в browser storage объект `__oneme_auth` с полями `viewerId` и `token`, а при восстановлении сессии передаёт этот токен командой opcode `19` внутри WebSocket.

Новые сообщения сервер отправляет push-командой opcode `128`. Полезная нагрузка содержит `chatId`, объект `message` и служебные отметки непрочитанного состояния. Получатель подтверждает push отдельным кадром с тем же opcode и идентификаторами чата/сообщения.

Первичный протокол был воспроизведён без аккаунта до этапа создания
QR-сессии. Позднее импортированная web-сессия подтвердила реальный login opcode
`19` и входящий push opcode `128`; первая доставка нового поста выбранного
канала в Telegram остаётся отдельной ручной release-проверкой. Поля ниже
разделены на:

- подтверждённые живым сетевым обменом;
- подтверждённые статическим анализом актуального клиентского bundle;
- подтверждённые импортированной авторизованной сессией;
- ещё не подтверждённые полным end-to-end сценарием.

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

### Воспроизводимый fingerprint публичных assets

Повторная выборка 2026-08-22 из HTML `https://web.max.ru/` ссылалась, среди
прочего, на следующие assets:

| Asset | SHA-256 |
|---|---|
| `/_app/immutable/entry/app.DznDOx5E.js` | `803c7b0375413489384e0da3f53ee880bf5920382d0f8dbde0d4469af32641d9` |
| `/_app/immutable/nodes/0.DCgo0-Ul.js` | `635bcaed8a469f54c09ceb1fb6adba6d676ebbc5befadd103816426ff9fc243c` |

Assets загружались во временную директорию только для анализа и в репозиторий
не включены. Имена и хэши динамические: они фиксируют исследованный снимок, а не
обещают неизменность MAX.

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

Форма init, production probe и создание QR-сессии закреплены тремя opt-in
live-тестами в `tests/test_live_handshake.py`. На 2026-08-22 они повторно прошли
против публичного endpoint; тесты не печатают и не сохраняют
`trackId`/`qrLink`.
Для безопасной операторской перепроверки только init доступна команда
`max-to-telegram --check-max-public`, не требующая профиля или token.

### Перепроверка QR-протокола 2026-08-27

Три публичных live-теста снова прошли против production endpoint. Актуальный
HTML `web.max.ru` ссылался на следующие исследованные assets:

| Asset | SHA-256 |
|---|---|
| `/_app/immutable/entry/app.B0HyG2-5.js` | `51a1bb512971576a25bf3fbe715786c2a87a2e3efc6f8c9cb1bce4649869b5bb` |
| `/_app/immutable/nodes/0.C9CD7-ZT.js` | `aac86fda35a8560404d8ec6ba825b85305cbb7d8a9a2d36e277fb98870865439` |
| `/_app/immutable/chunks/Bu0sqcK1.js` | `f02d608757684512258cccb0b44f4a3a5b0cf12e887795e7e09c7b5cb21667e4` |

Статический анализ актуального bundle подтвердил прежнюю цепочку `288` →
`289` → `291`, опциональный 2FA opcode `115` и расположение результата в
`tokenAttrs.LOGIN.token` и `profile.contact.id`.

Отдельный live-пробник установил важное ограничение: `trackId` привязан к тому
же WebSocket, на котором был создан. Попытка создать QR на первом соединении и
опросить его на втором возвращает `track.not.found`/invalid `trackId`.
Следовательно, QR-портал обязан держать один исходящий MAX WebSocket от создания
QR до завершения login; сохранять только `trackId` между serverless-запросами
недостаточно.

Первый production-вариант проксировал QR-сессию через WebSocket браузера, но
публичный Sites-dispatch вернул системный HTTP 500 до вызова Worker. Вариант с
потоковым HTTPS/NDJSON дошёл до Worker и вернул HTTP 200, однако dispatch не
отдал первый chunk браузеру за 25 секунд. Поэтому долгоживущая часть вынесена в
отдельный аутентифицированный relay на уже используемом alwaysdata: браузер
опрашивает Sites обычными same-origin HTTPS-запросами, Sites обращается к relay
по server-only bearer token, а relay держит один исходящий MAX WebSocket и
`trackId` в памяти до завершения входа. MAX credentials возвращаются только
между relay и Sites по HTTPS, шифруются в D1 и никогда не передаются браузеру.
Внешний адрес не требует входа через ChatGPT: собственный код доступа создаёт
подписанную `HttpOnly` cookie, а status/QR endpoints проверяют её на сервере.
Полный production-путь и реальный скан QR должны быть проверены после запуска
relay и настройки reverse proxy alwaysdata.

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
2. после account-gated capture при необходимости добавить локальный кэш
   `contact_id -> display name` из подтверждённого login/resync payload;
3. безопасно падать обратно на `MAX user <id>`.

До авторизованного capture точные поля имени считаются неподтверждёнными.

## HTTP-запросы

Core messaging/login в исследованном клиенте идёт через WebSocket. Из HTTP/XHR на login-экране наблюдалась в основном телеметрия `sdk-api.apptracer.ru` и статические ресурсы `web.max.ru`; cookie не понадобились для WebSocket init/QR.

Официальный Bot API — отдельный HTTPS JSON API на `platform-api2.max.ru` и не является транспортом личной веб-сессии.

## Риски и ограничения

- Протокол неофициальный, без гарантий совместимости; opcode, схема payload, домен и auth-flow могут измениться без предупреждения.
- Использование пользовательского session token вне официального клиента может нарушать правила сервиса. Владелец должен самостоятельно проверить актуальные условия MAX.
- Session token равнозначен доступу к аккаунту. Его нельзя логировать, коммитить, отправлять в issue/PR или включать в диагностические архивы.
- Публичный адрес QR-портала не означает публичный доступ к QR: интерфейс и
  endpoints требуют отдельную подписанную сессию, login rate-limited, а код
  хранится как secret окружения Sites.
- Новый QR-login через production Sites ещё не подтверждён реальным сканом;
  импортированная сессия и сам bridge проверялись отдельно.
- Не подтверждены token refresh/revocation и полнота display name для всех типов
  чатов.
- Вложения пока исследованы только на уровне общей схемы `attaches`; первая версия должна честно пересылать текст и краткое описание неизвестного вложения, не теряя событие.
- Повторы возможны после reconnect. Дедупликация по `(chatId, message.id, status/updateTime)` обязательна.

## Что должно быть проверено после появления профиля MAX

1. Открыть production Sites-портал, пройти реальный QR-login и подтвердить, что
   bridge получает сохранённую сессию без показа token в браузере.
2. Получить новый пост выбранного канала и подтвердить полный маршрут до
   Telegram.
3. Проверить личный диалог, группу, канал, системное событие, edit и delete.
   Отдельно сверить значения `message.type`: до capture runtime policy
   fail-closed пересылает только `USER`, остальные типы считает служебными.
4. Проверить отображаемое имя отправителя и fallback по ID.
5. Прервать сеть, дождаться reconnect и убедиться, что сообщение не дублируется в Telegram.
6. Проверить logout/revoke: клиент должен остановиться без бесконечного
   login-loop; инвалидный локальный session-файл оператор удаляет вручную.

Полный release checklist с матрицей разрешённого/запрещённого чата вынесен в
[`MANUAL_TEST.md`](MANUAL_TEST.md).

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

Сетевой listener не должен зависеть от Telegram latency. Для opcode `128` нужно
декодировать и нормализовать push, проверить точный `chatId`, а выбранное
сообщение записать в durable outbox **до** protocol ACK. После этого ACK можно
отправить немедленно и передать событие в ограниченную очередь; Telegram latency
не блокирует MAX. Невыбранное валидное сообщение подтверждается без локального
сохранения. Telegram sender выполняет retry только для временных ошибок и
учитывает `retry_after` при HTTP 429.

Фильтр должен быть fail-closed: пустой `MAX_CHAT_IDS` означает «не пересылать ничего», а не «пересылать всё». Для первоначальной настройки нужен discovery/dry-run режим, который показывает встреченные `chatId` и доступные названия, но не вызывает Telegram API. Имена допустимы только как подсказка оператору; рабочий allowlist хранит стабильные числовые ID, чтобы переименование чата не меняло политику доставки.

## Итог по уровню подтверждения

| Находка | Уровень |
|---|---|
| `wss://api.oneme.ru/websocket` | live + bundle |
| Protocol version 10, 10-byte header | live + bundle |
| MessagePack + LZ4 | live + bundle |
| Init opcode 6 | live + bundle |
| QR opcodes 288/289 | live + bundle |
| QR finalize opcode 291 и 2FA opcode 115 | актуальный bundle |
| storage `__oneme_auth` с `viewerId/token` | bundle |
| Login opcode 19 | bundle + импортированная сессия |
| Message push opcode 128 и ack | bundle + импортированная сессия |
| TrackId привязан к исходному WebSocket | live |
| Production Sites QR-login | ожидает ручной скан |
| Реальный payload личного сообщения | частично проверен; матрица типов не завершена |
| Token renewal/revocation | не проверен полностью |
