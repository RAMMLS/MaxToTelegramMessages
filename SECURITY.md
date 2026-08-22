# Security policy

## Supported version

До первого проверенного релиза security fixes применяются к ветке
`integration/nightly`. `main` пока не содержит рабочую интеграцию.

## Сообщение об уязвимости

Не публикуйте в issue:

- MAX session token, `viewerId` или device ID;
- Telegram bot token;
- `.env`, session JSON или SQLite outbox;
- сырые WebSocket capture с личными сообщениями.

Используйте private vulnerability reporting репозитория, если он включён, либо
свяжитесь с владельцем репозитория приватным каналом. В отчёте достаточно
санитизированного описания, версии/commit, шагов воспроизведения и минимального
теста без реальных секретов.

## Если секрет уже раскрыт

1. Немедленно перевыпустите Telegram bot token через BotFather.
2. Завершите активную web-сессию MAX и создайте новую.
3. Удалите секрет из локальных файлов/логов и проверьте Git history.
4. Не считайте простое удаление сообщения или commit достаточным отзывом секрета.

## Локальные меры

- `.env` и session JSON должны иметь права `0600`;
- `.env` и session JSON читаются через проверенный descriptor с `O_NOFOLLOW`,
  inode/fstat-сверкой и жёстким пределом байтов;
- allowlist `MAX_CHAT_IDS` обязателен и по умолчанию пуст;
- allowlist ограничен 1000 signed-int64 ID, а MAX URL/version/locale проходят
  ранние пределы длины и проверку управляющих символов;
- DEBUG-логи транспортов отключены, известные секреты редактируются formatter-ом;
- pending outbox содержит текст локально, после доставки текст очищается;
- process lock имеет POSIX `flock` и Windows `msvcrt` backend;
- state/session/env файлы исключены из Git.
- session и state paths ограничены, безопасно expand-ятся и не могут совпадать;
- CI без вывода совпадения блокирует Telegram-token-shaped значения и tracked `.env`;
  перед release вся история дополнительно проверяется Gitleaks.
- ошибки конфигурации/outbox не повторяют supplied chat values и record keys.
- Telegram error metadata проверяется по типу, ограничивается и переводится в
  однострочный текст до попадания в исключение или журнал.
- обычные runtime-логи содержат только счётчики и причины policy; raw MAX
  chat/message IDs и имена печатаются только в явно включённом discovery mode.
- credential-free `--check-max-public` не открывает `.env`.
- dotenv-loader не ищет `.env` выше текущего рабочего каталога.
- `MAX_WS_URL` закреплён на исследованном endpoint; нестандартный host требует
  явного opt-in, потому что login-команда содержит MAX session token.
