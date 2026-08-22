# Разработка и Git-процесс

## Состояние веток

`main` не изменялась и остаётся на исходном рабочем commit. Исследование и каждая
функция разработаны в отдельных ветках, проверены локально и затем объединены
только в `integration/nightly` через merge commits.

В текущем окружении доступен Git push по SSH, но отсутствует GitHub CLI/API token.
Поэтому серверные PR автоматически не создавались. Ветки отправлены на GitHub;
для них доступны ссылки создания PR. GitHub Actions запускается на push: последний
проверенный workflow feature-ветки завершился успешно для Python 3.10 и 3.12.

Ни одна ветка не должна сливаться в `main` до account-gated ручной проверки MAX.

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

Ветки основаны на последовательных снимках `integration/nightly`, поэтому PR в
`main` являются логически stacked. Перед открытием/слиянием нужно выбрать один из
двух процессов:

1. Проверить и последовательно слить stacked PR, обновляя base следующего PR.
2. Провести account-gated проверку интеграционной ветки и открыть один release PR
   `integration/nightly -> main`, сохранив feature-ветки как историю ревью.

Второй вариант практичнее для текущего состояния. Серверные PR ещё не созданы:
наличие feature-ветки и зелёного push workflow не следует считать PR review.

## Проверенный CI

- `chore/ci-node24` — [успешный запуск GitHub Actions](https://github.com/RAMMLS/MaxToTelegramMessages/actions/runs/32539777019), Python 3.10/3.12;
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
