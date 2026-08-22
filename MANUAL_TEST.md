# Закрытая ручная проверка перед release PR

Этот checklist выполняется только локально владельцем MAX-профиля. Реальные
credentials, payload, HAR, chat title и текст сообщений не прикладываются к PR.
До прохождения обязательных пунктов `integration/nightly` нельзя сливать в
`main`.

## 1. Подготовка

- [ ] Перевыпустить любой Telegram token, который когда-либо попадал в чат,
      скриншот, лог или issue.
- [ ] Использовать отдельного Telegram-бота и тестовый целевой чат.
- [ ] Взять свежую авторизованную MAX web-сессию из `https://web.max.ru/`.
- [ ] Сохранить MAX-сессию только в `.max-session.json` с правами `0600` либо в
      закрытом `.env`; убедиться, что оба файла игнорируются Git.
- [ ] Выбрать два контролируемых MAX-чата: один разрешённый и один запрещённый.
- [ ] Зафиксировать проверяемый commit SHA без записи credentials.

## 2. Автоматические проверки

```bash
python -m pip install -e '.[dev]'
ruff format --check .
ruff check .
mypy
pytest --cov=max_to_telegram --cov-report=term-missing --cov-fail-under=85
pip-audit --strict --progress-spinner=off .
python -m build
RUN_LIVE_MAX_RESEARCH=1 pytest -m live tests/test_live_handshake.py -v
git diff --check
```

- [ ] Все команды завершились успешно.
- [ ] Проверены GitHub Actions для того же commit на Python 3.10 и 3.12.
- [ ] `max-to-telegram --check-max-public` вернул protocol `10` и init opcode
      `6` без чтения `.env`.

## 3. Telegram без отправки

1. Оставить `TELEGRAM_CHAT_ID` пустым, написать тестовому боту и выполнить:

   ```bash
   max-to-telegram --discover-telegram-chats
   ```

2. Выбрать ID, записать его в `.env` и выполнить:

   ```bash
   max-to-telegram --check-telegram
   ```

- [ ] Discovery вывел только ID, тип и название, без текста update.
- [ ] `getMe` вернул ожидаемое имя бота.
- [ ] `getChat` вернул ожидаемый закрытый тестовый чат.
- [ ] Команды не отправили Telegram-сообщение.

## 4. MAX discovery и allowlist

1. Включить `MAX_DISCOVERY_MODE=true`, очистить Telegram-настройки и получить по
   одному новому сообщению в обоих MAX-чатах.
2. Записать только разрешённый ID в `MAX_CHAT_IDS`, выключить discovery и вернуть
   Telegram-настройки.
3. Выполнить `max-to-telegram --check-config`, затем запустить мост.

- [ ] MAX init opcode `6` и login opcode `19` успешны.
- [ ] Discovery показал ID обоих чатов, но не вызвал Telegram API.
- [ ] В allowlist записан полный числовой ID, а не название чата.
- [ ] Пустой `MAX_CHAT_IDS` не запускает delivery mode.

## 5. Матрица сообщений

| Сценарий | Ожидаемый результат |
|---|---|
| Входящий текст в разрешённом чате | Одно Telegram-уведомление |
| Входящий текст в запрещённом чате | Нет Telegram-запроса |
| Исходящее сообщение владельца | Нет уведомления |
| Edit разрешённого сообщения | Новое уведомление с отметкой об изменении |
| Delete/removed/service event | Нет уведомления |
| Неизвестный `message.type`, отличный от `USER` | Нет уведомления до проверки схемы |
| Текст с `<`, `>`, `&` | Видимый текст без HTML-инъекции |
| Текст длиннее 4096 символов | Нумерованные части, каждая в лимите |
| Фото/файл/неизвестное вложение | Безопасный текстовый ярлык, без binary upload |
| Личный диалог и группа | Верные sender/chat fallback либо имя из payload |

- [ ] Для каждого сценария фактический результат совпал с таблицей.
- [ ] В логах нет текста сообщения и credentials.

## 6. ACK, reconnect и durable outbox

- [ ] Для выбранного opcode `128` запись появляется в SQLite до protocol ACK.
- [ ] При недоступном Telegram запись остаётся `pending`, процесс завершается с
      санитизированной ошибкой, token отсутствует даже в traceback.
- [ ] После восстановления Telegram и рестарта pending запись доставляется.
- [ ] Ошибка на второй части длинного сообщения сохраняет checkpoint первой;
      рестарт продолжает со второй, не отправляя первую повторно.
- [ ] Повтор того же `(chatId, message.id, revision)` не создаёт второе
      уведомление после зафиксированной доставки.
- [ ] Разрыв MAX-сети приводит к reconnect с backoff, а не к tight loop.
- [ ] Одновременный второй процесс с тем же state DB получает отказ.
- [ ] SIGTERM даёт завершить короткую активную отправку; зависшая отправка после
      grace period остаётся pending и восстанавливается на следующем старте.
- [ ] Удаление chat ID из allowlist до рестарта удаляет его pending delivery без
      отправки.
- [ ] `--check-state` показывает `integrity_ok=true`, а также нулевой
      `unrecoverable_pending`; legacy pending без тела блокирует startup.

## 7. Session lifecycle

- [ ] Обновлённый token из login response атомарно сохранён только в файловом
      режиме, с правами `0600` и стабильным `deviceId`.
- [ ] Logout/revoke останавливает мост с понятной ошибкой без бесконечного login
      loop; старый session-файл удаляется оператором вручную.
- [ ] После новой web-сессии мост снова проходит login.

## 8. Release gate

Release PR `integration/nightly -> main` допустим, только если:

- [ ] обязательные пункты выше прошли на одном commit;
- [ ] реальные opcode/payload сверены с `RESEARCH.md`, а расхождения исправлены;
- [ ] PR не содержит credentials или персональные capture;
- [ ] GitHub Actions зелёный;
- [ ] reviewer проверил exact allowlist и поведение запрещённого чата;
- [ ] известные ограничения перечислены в README/PR без завышенных обещаний.

В PR фиксируются только commit SHA, версия Python/ОС, отметки pass/fail и
обезличенные выводы. Непройденный пункт остаётся blocker, а не «известной
особенностью» рабочего релиза.

## Связанные документы

- [`RESEARCH.md`](RESEARCH.md) — происхождение и уровень подтверждения протокола;
- [`DEVELOPMENT.md`](DEVELOPMENT.md) — ветки, CI и release-процесс;
- [`SECURITY.md`](SECURITY.md) — правила обращения с секретами;
- [`README.md`](README.md) — настройка и эксплуатация.
