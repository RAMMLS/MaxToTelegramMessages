## Изменение

Кратко опишите цель и границы изменения.

## Проверки

- [ ] `ruff format --check .`
- [ ] `ruff check .`
- [ ] `mypy`
- [ ] `pytest --cov=max_to_telegram --cov-fail-under=85`
- [ ] `pip-audit --strict --progress-spinner=off .`
- [ ] `python -m build`
- [ ] GitHub Actions завершился успешно

## MAX protocol

- [ ] Изменение не зависит от непроверенного предположения о private protocol,
      либо предположение явно отмечено в `RESEARCH.md`.
- [ ] Для protocol/payload изменения добавлен обезличенный fixture или
      синтетический тест.
- [ ] Account-gated ручной тест выполнен, либо PR не претендует на готовность к
      слиянию в `main`.

## Безопасность

- [ ] В diff, тестах, логах и capture нет реальных MAX/Telegram token, cookie,
      телефонных номеров и содержимого личных чатов.
- [ ] `MAX_CHAT_IDS` остаётся точным fail-closed allowlist.
- [ ] Изменение не расширяет права systemd unit и GitHub Actions без объяснения.

Укажите ручные проверки и известные ограничения ниже.
