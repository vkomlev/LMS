# Audit events контракт

**Источники:** `app/services/audit_service.py` (helper `log_event`), таблица `public.audit_event` (append-only через триггер `audit_event_no_modify`).
**Связано:** `app/api/middleware/request_id.py` (трассировка), `app/core/logger.py` (JSON-формат `logs/app.log`).
**Этап:** tsk-004 этап 4 (единый формат logs/app.log + audit_event).

## Структура события

Каждое событие имеет два зеркальных представления:

### В БД (`audit_event` row)

| Поле | Тип | Описание |
|---|---|---|
| `id` | bigserial | Primary key, монотонно растёт. |
| `event_type` | text NOT NULL | Dot-namespace identifier (см. таблицу ниже). |
| `user_id` | int FK→users NULL | NULL для безсессионных/системных событий. FK SET NULL при удалении users. |
| `ts` | timestamptz NOT NULL DEFAULT now() | Серверное время PG. |
| `ip` | inet NULL | IP клиента (если известен из request). |
| `user_agent` | text NULL | UA клиента. |
| `details` | jsonb NULL | Свободный payload — обогащается `request_id` автоматически в `log_event`. |

### В `logs/app.log` (JSON-строка через `JsonFormatter`)

```json
{
  "ts": "2026-05-22T12:34:56.789Z",
  "level": "INFO",
  "logger": "audit",
  "message": "audit_event recorded",
  "event_type": "teacher.review.graded",
  "user_id": 142,
  "audit_id": 1234,
  "request_id": "abc-def-...",
  "details": {"task_id": 99, "score": 10, "max_score": 10},
  "ip": "127.0.0.1",
  "user_agent": "Mozilla/..."
}
```

Симметрия ключей: `event_type / user_id / ip / user_agent / details / request_id` совпадают между БД и log. Дополнительно log содержит `audit_id` (FK на БД-row) и `level / logger / message` (стандартные logging-поля).

## Trace correlation

`request_id` (uuid4 или клиентский `X-Request-ID`) генерируется в `RequestIDMiddleware` per-request, попадает:
- В `response.headers["X-Request-ID"]` — клиент видит.
- В каждый `LogRecord` через `RequestIDFilter` — пишется в JSON-log.
- В `audit_event.details.request_id` через `audit_service.log_event` (автоматически из ContextVar).

**Поиск инцидента:**
```sh
# По логам приложения
grep '"request_id":"abc-..."' logs/app.log

# По БД audit
SELECT * FROM audit_event WHERE details->>'request_id' = 'abc-...';
```

## Реестр event_type

Все идентификаторы определены как константы в `app/services/audit_service.py`. Запрещено использовать сырые строки на местах вызова — только импорт констант.

| Константа | event_type | Кто emit'ит | Когда | Обязательные поля `details` |
|---|---|---|---|---|
| `TEACHER_REVIEW_GRADED` | `teacher.review.graded` | `teacher_reviews.py` | Преподаватель проверил attempt (Y-4) | `task_id`, `score`, `max_score`, `is_correct` |
| `STUDENT_NOTIFICATION_CREATED` | `student.notification.created` | `me_notifications.py`, `notification_email_service.py` | Создана notification для студента (Y-4) | `kind`, `recipient_user_id` |
| `STUDENT_NOTIFICATION_READ` | `student.notification.read` | `me_notifications.py` | Студент пометил прочитанным (Y-4) | `notification_id` |
| `EMAIL_FAILED` | `email.failed` | `notification_email_service.py` | Resend API вернул ошибку | `to`, `error` |
| `TEACHER_REVIEW_REJECTED` | `teacher.review.rejected` | `teacher_reviews.py` | Y-6: преподаватель отклонил attempt | `task_id`, `reason` |
| `TEACHER_REVIEW_REGRADED` | `teacher.review.regraded` | `teacher_reviews.py` | Y-6: переоценка previously-graded | `task_id`, `prev_score`, `new_score` |
| `METHODIST_ESCALATION_TRIGGERED` | `methodist.escalation.triggered` | `methodist_notify_service.py` | Y-6: эскалация на методиста по SLA | `course_id`, `pending_count` |
| `STUDENT_ROLE_AUTO_ASSIGNED` | `student.role.auto_assigned` | `auth/*_service.py`, `role_assign_service.py` | Y-4 pre-S5: auto-присвоение роли student при регистрации | `role`, `origin`, `channel` |
| `AUTH_ROLE_MISSING_SELF_HEALED` | `auth.role.missing_self_healed` | `deps.py` (`get_current_user_defensive`) | Defensive self-heal у user без ролей | `role`, `origin`, `channel` |
| `AUTH_TEST_SESSION_ISSUED` | `auth.test.session_issued` | `auth/test_session.py` | Тестовая сессия выпущена (dev/CI) | `purpose` |

## Инварианты

1. **Append-only** — `UPDATE` и `DELETE` на `audit_event` запрещены триггером `audit_event_no_modify` (RAISE EXCEPTION). Обход допустим только в test-fixtures cleanup (см. `tests/conftest.py:_cleanup_test_artifacts` — DISABLE TRIGGER + DELETE + ENABLE TRIGGER).
2. **Симметрия БД ↔ log** — `log_event` сначала flush'ит INSERT, потом эмитит structured log. Если INSERT падает — log не пишется (через `raise`). Если INSERT OK, но log упал — событие в БД остаётся (rollback не делаем, БД — приоритетный источник правды).
3. **request_id обогащение** — `details.request_id` подставляется автоматически из ContextVar только если вызвано внутри HTTP-request. CLI/background-задачи (cron escalation tick, alembic) `request_id` НЕ имеют — это OK, distinguishing feature.
4. **Запрещено сырьё** — никогда `log_event(db, "teacher.review.graded", ...)`. Только `log_event(db, TEACHER_REVIEW_GRADED, ...)`. Облегчает grep и rename.

## Эволюция

При добавлении нового `event_type`:
1. Создать константу в `audit_service.py` с docstring (когда emit'ится).
2. Добавить строку в таблицу реестра выше.
3. Если детали имеют схему — описать `details.{...}` в колонке «Обязательные поля».
4. Если событие критично для метрик/алертов — добавить заметку в `docs/ai/architecture.md` (или эквивалент).
