# Y-6 review-loop — LMS-side IMPLEMENTED

**Date:** 2026-05-04
**Status:** IMPLEMENTED (LMS-only). SPW Stage 6 + TG_LMS Stage 5 — отдельные сессии.
**Authority chain:** [tech-spec-Y6-review-loop-v1.md](../../../ContentBackbone/docs/tech-specs/tech-spec-Y6-review-loop-v1.md) → этот документ → review-артефакты.

> Этот файл — LMS-side контракт-snapshot. Авторитет — cross-project tech-spec. Здесь фиксируются конкретные file:line изменения и команды смоук-проверки.

---

## 1. Что сделано (LMS)

| Stage | Описание | Артефакт |
|---|---|---|
| **M12** | Backfill in-flight pending TA/SA_COM + `idx_task_results_pending_review` | [reviews/2026-05-04-y6-m12-optimistic-pass.md](../../reviews/2026-05-04-y6-m12-optimistic-pass.md) |
| **Stage 1** | Submit-side optimistic-PASSED + queue filter pivot + TA routing unblock | [reviews/2026-05-04-y6-stage1-optimistic-pass.md](../../reviews/2026-05-04-y6-stage1-optimistic-pass.md) |
| **Stage 2** | Derived `is_correct` в `/grade` + idempotency pivot + `task_returned_for_rework` + `teacher.review.rejected` audit | [reviews/2026-05-04-y6-stage2-derived-is-correct.md](../../reviews/2026-05-04-y6-stage2-derived-is-correct.md) |
| **Stage 3** | `POST /teacher/reviews/{id}/regrade` + `metrics.regrade_history` | [reviews/2026-05-04-y6-stage3-regrade.md](../../reviews/2026-05-04-y6-stage3-regrade.md) |
| **Stage 4** | APScheduler escalation cron + course-completion event trigger + `GET /methodist/escalations/pending` | [reviews/2026-05-04-y6-stage4-escalation.md](../../reviews/2026-05-04-y6-stage4-escalation.md) |
| **Stage 7 (LMS)** | `tests/test_y6_review_loop.py` — 9 integration test'ов | [reviews/2026-05-04-y6-review-gate-final.md](../../reviews/2026-05-04-y6-review-gate-final.md) |

---

## 2. API endpoints — публичные изменения

### Изменённые

- **`POST /api/v1/teacher/reviews/{result_id}/grade`**
  - Body: `{teacher_id, lock_token, score, comment?}` (поле `is_correct` удалено).
  - Pydantic v2 `extra=ignore` сохраняет совместимость с TG_LMS Y-4 bot до его обновления (Stage 5).
  - Idempotency: `checked_at IS NOT NULL → 409` (раньше — `is_correct IS NOT NULL`).
  - Notification kind ветвится: TRUE → `sa_com_graded`, FALSE → **`task_returned_for_rework`** (NEW).
  - Audit: всегда `teacher.review.graded`, при negative — дополнительно `teacher.review.rejected`.
  - Email: только при `is_correct=TRUE`.

### Новые

- **`POST /api/v1/teacher/reviews/{result_id}/regrade`** — Stage 3.
  - Body: `{score, comment?}`.
  - 409 если `checked_at IS NULL` (regrade требует initial grade).
  - Notification kinds: `task_returned_for_rework` (T→F), `sa_com_graded` (F→T), **`sa_com_regraded`** (same direction).
  - Audit: `teacher.review.regraded` + опц. `teacher.review.rejected`.
  - Не idempotent — `metrics.regrade_history` накапливает все события.
  - ACL: service / admin / methodist / teacher на course-tree.

- **`GET /api/v1/methodist/escalations/pending`** — Stage 4.4.
  - Query: `since?: ISO8601`, `limit: 1..500 (default 100)`.
  - Response: `{items: [...], count}`.
  - ACL: service / role=methodist.

---

## 3. Внутренние изменения

- `app/api/v1/attempts.py:submit_attempt_answers` — для `type ∈ {SA_COM, TA} ∧ not attempt.time_expired` подменяет `check_result` на `{score=max, max=max, is_correct=True}` ДО записи в task_results.
- `app/services/teacher_queue_service.py` — 3 query (claim_next_review / get_pending_count / get_teacher_workload): фильтр `is_correct IS NULL` → `checked_at IS NULL`. `grade_review` принимает `score` (без `is_correct`), сам compute'ит derived. `regrade_review` (NEW) — атомарный SELECT FOR UPDATE + history append.
- `app/services/learning_engine_service.py` — снят фильтр `type != 'TA'` из `_first_incomplete_task` и `compute_course_state.total_tasks`. `compute_course_state` при `state=COMPLETED` запускает `escalate_course_completion(...)` через try/except (escalation не валит state-resolve).
- `app/services/escalation_service.py` (NEW) — `escalation_cron_tick()` с PG advisory lock 0x59365453; `start_scheduler()` / `stop_scheduler()` управляют APScheduler `AsyncIOScheduler`.
- `app/services/methodist_notify_service.py` (NEW) — `escalate_pending_timeout(...)` + `escalate_course_completion(...)`. Read methodist users, rate-limit per course/day, INSERT inbox + audit, mark `metrics.escalated_at` / `completion_escalated_at`.
- `app/api/v1/methodist_escalations.py` (NEW) — handler + Pydantic schemas.
- `app/services/audit_service.py` — 3 новые event-type константы.
- `app/core/config.py` — 4 новые env-var с дефолтами.
- `app/api/main.py` — регистрация router + `@app.on_event('startup'/'shutdown')` для scheduler.

---

## 4. Date/Type Guards

- `attempt.time_expired` — bool (truthy check).
- `now = datetime.now(timezone.utc)` — UTC-aware; bind как timestamptz.
- `metrics.escalated_at` / `completion_escalated_at` — ISO-string через `.isoformat()`; idempotency-проверка через jsonb-ключ (`metrics ? 'key'`).
- `pg_try_advisory_lock(0x59365453)` — int64 fixed key.

---

## 5. Команды проверки

```powershell
# Migration
cd d:\Work\LMS
.venv\Scripts\alembic.exe current   # → m12_y6_optimistic_pass

# Y-6 tests
.venv\Scripts\python.exe -m pytest tests\test_y6_review_loop.py -v
# 9 passed expected

# Регрессия по затронутым областям
.venv\Scripts\python.exe -m pytest `
  tests\test_grade_endpoint_y4.py `
  tests\test_claim_next_pending_filter_y42.py `
  tests\test_pending_count_y4.py `
  tests\test_pending_count_y42.py `
  tests\test_workload_y42.py `
  tests\test_list_pending_review_y42.py `
  tests\test_inbox_service_y4.py `
  tests\test_acl_hierarchical_y41.py `
  tests\test_learning_engine_service.py
# 70/72 passed (2 pre-existing flakies — fixture LIMIT 1 без ORDER BY)

# Bandit
.venv\Scripts\python.exe -m bandit -q -r `
  app\api\v1\teacher_reviews.py `
  app\api\v1\methodist_escalations.py `
  app\services\teacher_queue_service.py `
  app\services\escalation_service.py `
  app\services\methodist_notify_service.py
# Y-6 новые файлы clean (#nosec на literal-only ACL fragments)
```

---

## 6. Pre-deploy в prod (Y-7)

- [ ] Pre-create индекс CONCURRENTLY вручную:
  ```sql
  CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_task_results_pending_review
  ON task_results (submitted_at, checked_at) WHERE checked_at IS NULL;
  ```
- [ ] `alembic upgrade m12_y6_optimistic_pass` (no-op для индекса, только backfill UPDATE).
- [ ] Минимум 1 user с `role=methodist` присутствует в `learn`.
- [ ] APScheduler в venv (`pip list | grep -i scheduler` → ≥3.10).
- [ ] Multi-worker gunicorn config протестирован на dev (PG advisory lock работает).
- [ ] OpenAPI re-export: `curl http://localhost:8000/openapi.json > docs/openapi.json` после dev-deploy.

---

## 7. Sign-off

- **/executor-pro:** реализация per-stage — DONE 2026-05-04
- **/techlead-code-reviewer:** PASS все 5 review артефактов (M12 + Stage 1-4)
- **/review-gate (paranoid mode):** PASS — `reviews/2026-05-04-y6-review-gate-final.md`

**Status:** LMS-side READY for integration в `main`. SPW Stage 6 + TG_LMS Stage 5 — отдельные сессии.
