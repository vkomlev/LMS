# tsk-348 — LMS: pending-count для help_requests + inbox-уведомления ученику

## Контекст

P0, реальный инцидент 2026-07-21: запрос помощи ученика на живом уроке остался
незамеченным преподавателем. Диагностика (см. TG_LMS
`reviews/2026-07-21-tsk348-help-requests-poller.md`): TG-бот поллер отслеживал
только очередь ручной проверки заданий (`task_results`), таблица
`help_requests` вообще не имела push-механизма — ни для учителя, ни для
ученика (ответ/закрытие/разблокировка лимита раньше не создавали `notifications`,
только пассивный `messages`-тред).

## Изменения

1. **`GET /teacher/help-requests/pending-count`** (новый) — count открытых
   заявок (`manual_help` + `blocked_limit`), назначенных на преподавателя.
   Auth: тот же паттерн `is_service or current_user.id == teacher_id`, что у
   остальных `teacher_help_requests.py` эндпоинтов и у аналогичного
   `/teacher/reviews/pending-count`. Источник — прямой SELECT по
   `assigned_teacher_id` (не ACL-объединение методистов — это личная очередь
   учителя, как в разделе «Вопросы студентов»).
   Используется TG_LMS bot-поллером (30 сек) и веб-бейджем SPW (20 сек).
2. **inbox-уведомления ученику** (переиспользуют существующий
   `inbox_service.create_for_user` + `/me/notifications/unread-count` +
   `UnreadBadge` в SPW — ноль нового кода на стороне ученика):
   - `reply_help_request` → `kind="help_request_replied"` (учитель ответил).
   - `close_help_request`, только при `closed_by is not None` (явное
     закрытие учителем; системный auto-close tsk-339, когда ученик решил
     сам, — БЕЗ пуша) → `kind="help_request_closed"`.
   - `task_limit_override` (`app/api/v1/teacher_learning.py`) →
     `kind="task_limit_override"` (учитель дал ещё попыток / разблокировал).

## Changed Files

- `app/schemas/teacher_help_requests.py` — `HelpRequestPendingCountResponse`
- `app/services/help_requests_service.py` — `get_help_requests_pending_count`,
  notify в `reply_help_request`/`close_help_request`
- `app/api/v1/teacher_help_requests.py` — `GET /pending-count` (объявлен ДО
  `GET /{request_id}`, чтобы не быть перехваченным как request_id)
- `app/api/v1/teacher_learning.py` — notify в `task_limit_override`
- `tests/test_help_requests_pending_count_tsk348.py` — 5 интеграционных
  HTTP-тестов
- `docs/openapi.json` — перегенерирован (`scripts/export_openapi.py`) для
  SPW-кодогенерации типов

## Fact-Check Evidence

- `inbox_service.create_for_user` — существующий helper (Y-4, M8), уже
  используется `methodist_notify_service.py` для `kind="review_escalated"`
  с идентичной сигнатурой; переиспользован без изменений контракта.
- `/me/notifications/unread-count` — `WHERE user_id = current_user.id`, без
  фильтра по `kind` (проверено чтением `app/services/inbox_service.unread_count`)
  → новые kind'ы подхватываются существующим SPW `UnreadBadge` без правок эндпоинта.

## Validation

```
D:\Work\LMS\.venv\Scripts\python.exe -m pytest tests/test_help_requests_pending_count_tsk348.py -v
# 5 passed

D:\Work\LMS\.venv\Scripts\python.exe -m pytest tests/test_teacher_help_requests_stage381.py \
  tests/test_teacher_help_requests_overdue_tsk312.py tests/test_help_request_autoclose_tsk339.py \
  tests/test_pending_count_y4.py tests/test_pending_count_y42.py -v
# 18 passed — регрессий нет
```

## Review Gate

Независимый `/review-gate` (paranoid, отдельный subagent без доступа к ходу
реализации): первый прогон — **FAIL**, диф `git diff -- app tests` случайно
захватил чужую незакоммиченную работу (`tasks_service.py` — tsk-345,
переупорядочивание заданий по сложности, отдельная сессия в этом же дереве).
Diff пересобран строго по pathspec из 5 файлов tsk-348 (без tasks_service.py,
без scripts/reorder_courses_by_difficulty_tsk345.py,
tests/test_tsk345_reorder_by_difficulty.py — они не тронуты и не будут
закоммичены этой задачей). Повторная проверка diff (`grep -c tasks_service`) —
0 совпадений. Route ordering, ACL-паттерн, транзакционная безопасность
`inbox_service.create_for_user` (до `db.commit()`, без sирот при rollback),
отсутствие двойных уведомлений на idempotent retry (`reply`/`close`) —
подтверждены независимо. Единственная non-blocking находка: explicit-mode
`task_limit_override` может задвоить уведомление при retry (нет
idempotency_key на этом эндпоинте) — тот же пробел уже есть у
`audit_service.log_event`, не регрессия, не блокирует.

## Residual Risk / Follow-ups

- `pending-count` считает по `assigned_teacher_id` напрямую — если заявку
  переназначат другому учителю (сейчас такого флоу нет), счётчик уедет вместе
  с назначением; ACL списка (`list_help_requests`) шире (методисты видят по
  иерархии) — счётчик сознательно уже (личная очередь, не общая).
- `oldest_created_at` в ответе пока не используется ни ботом, ни вебом (задел
  на будущий SLA-индикатор, зеркалит `oldest_received_at` у review pending-count).
