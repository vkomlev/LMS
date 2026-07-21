# tsk-348 follow-up — читаемая лента уведомлений + CTA к заданию

## Контекст

Оператор указал на живой пример (скрин `/me/notifications`): уведомление
рендерится, но из него некуда перейти — ни к заданию (у ученика), ни у
учителя вообще не было ленты (только бейдж-счётчик). Требование: обе стороны
должны иметь readable-ленту + переход к цели, вызвавшей уведомление.

## Изменения (LMS)

- `get_or_create_help_request` / `get_or_create_blocked_limit_help_request` —
  при создании НОВОЙ заявки (не dedup) создают inbox-уведомление
  (`kind="help_request_opened"`) назначенному учителю (`assigned_teacher_id`).
  Раньше учитель узнавал только через `pending-count` (число) — читать
  события было негде.
- `reply_help_request` / `close_help_request` — payload уведомления ученику
  теперь содержит `task_id` (раньше только `request_id`/`thread_id`) — без
  него SPW не может построить deeplink на задание.

## Changed Files

- `app/services/help_requests_service.py`
- `tests/test_help_requests_pending_count_tsk348.py` — +1 тест
  (`test_new_help_request_notifies_assigned_teacher`, через реальный API-путь
  `POST /learning/tasks/{id}/request-help`, не raw INSERT) + task_id-ассёршены
  в существующих reply/close тестах.

## Validation

```
D:\Work\LMS\.venv\Scripts\python.exe -m pytest tests/test_help_requests_pending_count_tsk348.py -v
# 6 passed

D:\Work\LMS\.venv\Scripts\python.exe -m pytest tests/test_teacher_help_requests_stage381.py \
  tests/test_teacher_help_requests_overdue_tsk312.py tests/test_help_request_autoclose_tsk339.py \
  tests/test_manual_progress_tsk297.py -v
# 55 passed
```

## Residual Risk

- `help_request_opened` не создаётся, если `assigned_teacher_id IS NULL`
  (заявка без назначенного учителя) — как и раньше, некому уведомлять.
- SPW-часть (страница `/teacher/notifications`, CTA-резолвер) — в review-
  артефакте SPW.
