# Review: HTTP-тесты 409 для hint-events (finished/cancelled attempt)

**Дата:** 2026-02-26

**Контекст:** Необязательный усилитель этапа 3.6 — отдельные HTTP-тесты на 409, когда попытка уже завершена или отменена: `finished/cancelled attempt → POST hint-events` должен возвращать 409.

**Изменения (только тесты):**
- `tests/test_hint_events_stage36.py`:
  - **test_http_hint_events_409_finished_attempt**: создаём попытку, вызываем `POST /api/v1/attempts/{id}/finish?api_key=...`, затем `POST .../hint-events` → ожидаем 409.
  - **test_http_hint_events_409_cancelled_attempt**: создаём попытку, вызываем `POST /api/v1/attempts/{id}/cancel?api_key=...`, затем `POST .../hint-events` → ожидаем 409.
  - В `main()` добавлены оба теста в список запуска.
  - Для вызовов finish/cancel добавлен query-параметр `api_key` (эндпоинты используют `get_db` → `get_api_key`).

Полный diff (включая ранее внесённые правки): [2026-02-26-hint-events-409-http-tests.diff](2026-02-26-hint-events-409-http-tests.diff)
