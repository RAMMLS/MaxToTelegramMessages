# Разработка и Git-процесс

## Выпущенный snapshot 2026-08-22

- `main`: release commit `951bb15`;
- локальные release gates: 312 passed, 3 opt-in live skipped, coverage 89.68%;
- GitHub Actions run `32560721027`: Ubuntu Python 3.10/3.12 и Windows Python
  3.12 прошли;
- реальный MAX login и входящий opcode `128` подтверждены;
- первый новый пост выбранного MAX-канала → Telegram остаётся ручным
  post-release smoke;
- следующие изменения снова выполняются только в отдельных feature-ветках.

## Исторический snapshot перед release

- рабочая интеграция перед этой handoff-веткой: `35e28db` в
  `integration/nightly`;
- `main`: исходный commit `6fea704`, рабочий bridge туда не сливался;
- локально: 308 passed, 3 opt-in live tests skipped, coverage 89.52%;
- Linux Python 3.10/3.12 и Windows Python 3.12:
  [успешный matrix run](https://github.com/RAMMLS/MaxToTelegramMessages/actions/runs/32546948537);
- публичный MAX init/production/QR handshake: 3 passed без профиля и без
  сохранения QR/track ID;
- закрытый MAX login, реальный opcode `128` и матрица разрешённого/запрещённого
  чата остаются release blockers.

Главная пользовательская политика уже реализована и покрыта unit/integration
тестами: delivery mode требует непустой exact allowlist `MAX_CHAT_IDS`, а
сообщения остальных чатов отбрасываются до Telegram. Пустой allowlist никогда
не расширяется до «все чаты».

## Историческое состояние веток до release

Исследование и каждая функция были разработаны в отдельных ветках, проверены
локально и объединены в `integration/nightly` через merge commits. После
release gates интеграция выпущена в `main` commit `951bb15`.

В текущем окружении доступен Git push по SSH, но отсутствует GitHub CLI/API token,
а GitHub в браузере не авторизован. Поэтому серверные PR автоматически не
создавались. Ветки отправлены на GitHub; для них доступны ссылки создания PR.
GitHub Actions запускается на push, но зелёный push workflow не является PR или
review.

Новые ветки не должны сливаться в `main` без локальных проверок, зелёных GitHub
Checks и соответствующего ручного smoke.

## Ветки функций

| Ветка | Содержание | Создание PR |
|---|---|---|
| `research/max-web-version` | Исследование web MAX | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/research/max-web-version) |
| `feature/config` | `.env`, fail-closed config, CI | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/config) |
| `feature/auth` | Импорт и защита MAX-сессии | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/auth) |
| `feature/websocket-protocol` | Binary codec | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/websocket-protocol) |
| `feature/websocket-listener` | Login, ack, reconnect | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/websocket-listener) |
| `feature/message-parser` | Parser и chat allowlist | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/message-parser) |
| `feature/telegram-sender` | Telegram Bot API sender | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/telegram-sender) |
| `feature/deduplication` | SQLite delivery state | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/deduplication) |
| `feature/durable-outbox` | Восстановление pending queue | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/durable-outbox) |
| `feature/bridge-runtime` | Оркестрация pipeline | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/bridge-runtime) |
| `feature/cli-logging` | CLI, сигналы и безопасные логи | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/cli-logging) |
| `docs/readme-operations` | Руководство запуска и systemd unit | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/docs/readme-operations) |
| `feature/privacy-state` | Очистка доставленного содержимого | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/privacy-state) |
| `feature/protocol-safety` | Лимиты бинарных frame и decompression | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/protocol-safety) |
| `feature/python310-compat` | Совместимость с Python 3.10 | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/python310-compat) |
| `chore/package-metadata` | Метаданные wheel и sdist | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/chore/package-metadata) |
| `feature/telegram-validation` | Безопасные проверки getMe/getChat | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/telegram-validation) |
| `feature/session-maintenance` | Атомарное обновление MAX token | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/session-maintenance) |
| `feature/observability` | Счётчики состояния без содержимого | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/observability) |
| `feature/single-instance-lock` | Межпроцессная блокировка outbox | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/single-instance-lock) |
| `test/protocol-fuzz` | Воспроизводимые fuzz/property проверки | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/test/protocol-fuzz) |
| `chore/ci-node24` | SHA-pinned GitHub Actions на Node 24 | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/chore/ci-node24) |
| `feature/chunk-delivery-checkpoints` | Resume длинных Telegram-сообщений по частям | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/chunk-delivery-checkpoints) |
| `feature/service-message-policy` | Fail-closed policy неизвестных типов | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/service-message-policy) |
| `feature/graceful-shutdown-drain` | Bounded drain при SIGINT/SIGTERM | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/graceful-shutdown-drain) |
| `feature/config-path-safety` | Безопасные session/state paths | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/config-path-safety) |
| `feature/windows-process-lock-ci` | Windows lock, timezone dependency и CI matrix | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/feature/windows-process-lock-ci) |
| `docs/nightly-handoff` | Итоговый snapshot и release gate | [Открыть форму PR](https://github.com/RAMMLS/MaxToTelegramMessages/pull/new/docs/nightly-handoff) |

Таблица перечисляет основные review units, но не заменяет Git как источник
истины. Полный список всех небольших hardening/test/docs веток для текущего
checkout выводится командой:

```bash
git for-each-ref --format='%(refname:short)' refs/remotes/origin | sort
```

Ветки основаны на последовательных снимках `integration/nightly`, поэтому PR в
`main` являются логически stacked. Перед открытием/слиянием нужно выбрать один из
двух процессов:

1. Проверить и последовательно слить stacked PR, обновляя base следующего PR.
2. Провести account-gated проверку интеграционной ветки и открыть один release PR
   `integration/nightly -> main`, сохранив feature-ветки как историю ревью.

Второй вариант практичнее для текущего состояния. Серверные PR ещё не созданы:
наличие feature-ветки и зелёного push workflow не следует считать PR review.

## Проверенный CI

- `feature/windows-process-lock-ci` —
  [успешный запуск GitHub Actions](https://github.com/RAMMLS/MaxToTelegramMessages/actions/runs/32546948537):
  Ubuntu/Python 3.10, Ubuntu/Python 3.12 и Windows/Python 3.12;
- Windows job действительно выполняет тест отказа второму процессу с тем же
  state DB, а не только импортирует пакет;
- при падении pytest CI создаёт bounded redacted annotations без полного
  traceback и без token-shaped строк;
- actions закреплены полными commit SHA и используют Node 24;
- push интеграционной ветки после каждого merge запускает тот же workflow;
- live-тест публичного MAX WebSocket помечен `live` и намеренно не запускается в
  GitHub Actions, чтобы CI не зависел от внешнего неофициального endpoint.

## Обязательная проверка перед release PR

```bash
git switch integration/nightly
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
ruff format --check .
ruff check .
mypy
mypy --platform win32 src
pip-audit --strict --progress-spinner=off .
pytest --cov=max_to_telegram --cov-report=term-missing --cov-fail-under=85
RUN_LIVE_MAX_RESEARCH=1 pytest -m live tests/test_live_handshake.py -v
git diff --check
```

Затем нужен закрытый end-to-end прогон с реальным профилем по checklist из
`RESEARCH.md`. Секреты не должны появляться в PR или CI.

## Правила продолжения

- новая функция — новая ветка от актуальной интеграционной ветки;
- тесты и документация входят в ту же feature-ветку;
- feature-ветка пушится до интеграции;
- `main` меняется только PR после локальных проверок, GitHub Checks и ручного smoke;
- формат commit message — короткое действие, без секретов и персональных данных.

## Post-release обязательные действия

1. Владелец перевыпускает любой Telegram token, который когда-либо был отправлен
   в чат/скриншот, и кладёт новый token только в локальный `.env`.
2. После появления MAX-профиля выполняется весь [`MANUAL_TEST.md`](MANUAL_TEST.md)
   на одном commit, включая один разрешённый и один запрещённый чат.
3. Подтвердить первый новый пост выбранного MAX-канала → Telegram и отрицательную
   проверку неразрешённого чата.
4. Для следующих изменений открывать отдельный PR в `main` и сливать только
   после зелёных Checks и review.
