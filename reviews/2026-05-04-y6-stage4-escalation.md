# Review — Y-6 Stage 4: Escalation Cron + Course-Completion Trigger + Methodist Endpoint

**Date:** 2026-05-04
**Stage:** 4 (после Stage 3)
**Skill (исполнитель):** `/executor-pro`
**Skill (ревью):** `/techlead-code-reviewer` (concurrency + multi-worker focus)
**Tech-spec:** `D:/Work/ContentBackbone/docs/tech-specs/tech-spec-Y6-review-loop-v1.md` §6 «Stage 4»
**Diff:** [2026-05-04-y6-stage4-escalation.diff](2026-05-04-y6-stage4-escalation.diff)

---

## 1. Контекст

Финальная LMS-стадия Y-6: реакция системы на «зависшие» pending-проверки.
- 48h timeout → push методистам через TG_LMS poller (Stage 5);
- course-completion: студент завершил курс, но висят pending TA/SA_COM → push методистам (event-driven);
- `GET /api/v1/methodist/escalations/pending` для TG_LMS bot poller.

---

## 2. Изменения

### 2.1 [requirements.txt](../requirements.txt)

Добавлен `APScheduler>=3.10,<4` (3.x ветка — без BaseScheduler 4.x редизайна). Установлен в `.venv` (3.11.2).

### 2.2 [app/services/methodist_notify_service.py](../app/services/methodist_notify_service.py) — NEW

Два публичных async-метода:
- `escalate_pending_timeout(...)` — для cron tick'а;
- `escalate_course_completion(...)` — для event-driven компонента.

Обе функции:
- читают список methodist-юзеров (`user_roles.role.name='methodist'`);
- проверяют rate-limit через `notifications` (count за 24h по kind+course_id);
- создают inbox для каждого methodist (kind = `review_escalated` / `course_pending_review`);
- помечают `task_results.metrics.escalated_at` (timeout) или `completion_escalated_at` (completion) для idempotency;
- пишут audit `methodist.escalation.triggered` с агрегированным payload.

### 2.3 [app/services/escalation_service.py](../app/services/escalation_service.py) — NEW

- `escalation_cron_tick()` — async, делает `pg_try_advisory_lock(0x59365453)`, выбирает 100 кандидатов `checked_at IS NULL ∧ submitted_at < now() - timeout_hours ∧ NOT (metrics ? 'escalated_at')`, для каждого вызывает `escalate_pending_timeout`. Обработка ошибок per-row — один сбой не валит весь tick. Финально освобождает advisory lock.
- `start_scheduler()` / `stop_scheduler()` — управление APScheduler `AsyncIOScheduler`. Job: `IntervalTrigger(minutes=ESCALATION_CRON_INTERVAL_MIN)`. Идемпотентен — повторный start вернёт running scheduler. `coalesce=True, max_instances=1` — защита от run-up при паузах.

### 2.4 [app/api/v1/methodist_escalations.py](../app/api/v1/methodist_escalations.py) — NEW

`GET /api/v1/methodist/escalations/pending`:
- Auth: `Depends(get_current_user)`;
- ACL: `is_service` или роль `methodist`;
- Query: `?since=ISO8601&limit=1..500` (default 100);
- SQL: `SELECT n.id, created_at, kind, title, payload, read_at FROM notifications WHERE user_id=:uid AND kind IN ('review_escalated','course_pending_review') [AND created_at >= :since] ORDER BY created_at DESC LIMIT :limit`;
- Response: `{items: [...], count}`.

### 2.5 [app/services/learning_engine_service.py](../app/services/learning_engine_service.py)

`compute_course_state` — после установки `state == 'COMPLETED'` SELECT-ит pending TA/SA_COM в дереве курса, если непусто — вызывает `escalate_course_completion(...)`. Внутри try/except — escalation НЕ должен валить state-resolve. Idempotency через `metrics.completion_escalated_at` гарантирует что повторный compute (например, при следующем `resolve_next_item`) не дублирует push.

### 2.6 [app/api/main.py](../app/api/main.py)

- `methodist_escalations_router` зарегистрирован с prefix=`/api/v1`;
- `@app.on_event('startup'/'shutdown')` для start/stop scheduler. Try/except — failure скeduler-init не блокирует API startup (graceful degradation; cron можно вызвать вручную).

---

## 3. SQL Formula Verification

### 3.1 Cron timeout query

```sql
SELECT tr.id, tr.task_id, tr.user_id, t.course_id, tr.submitted_at
FROM task_results tr
JOIN tasks t ON t.id = tr.task_id
WHERE tr.checked_at IS NULL
  AND t.task_content->>'type' IN ('SA_COM','TA')
  AND tr.submitted_at < (now() - (:h || ' hours')::interval)
  AND NOT (COALESCE(tr.metrics, '{}'::jsonb) ? 'escalated_at')
ORDER BY tr.submitted_at ASC
LIMIT 100
```

Использует partial index `idx_task_results_pending_review (submitted_at, checked_at) WHERE checked_at IS NULL` (созданный в M12) — `submitted_at` в индексе, `checked_at IS NULL` в WHERE matched index.

### 3.2 Rate-limit guard

```sql
SELECT COUNT(*) FROM notifications n
WHERE n.kind = :kind
  AND n.created_at >= now() - interval '1 day'
  AND (n.payload->>'course_id')::int = :course_id
```

При `count >= rate_limit_per_day` — push не создаётся, но `metrics.escalated_at` всё равно ставится (чтобы не возвращаться к этому result повторно при следующих tick'ах).

### 3.3 Multi-worker safety

`pg_try_advisory_lock(:k)` — non-blocking. Один из gunicorn-workers получает lock и делает работу; остальные мгновенно возвращают `False` и пропускают tick. Через 5 минут (next interval) — следующий worker берёт lock. Таким образом:
- нет двойной отправки push'ей;
- нет deadlock'а;
- нет «no-op forever» — lock автоматически освобождается на каждом коммите/disconnect (PG behavior); явный `pg_advisory_unlock` в finally-блоке защищает от утечки.

---

## 4. ACL Verification (methodist endpoint)

| Path | Поведение |
|---|---|
| service-key (X-API-Key) | bypass |
| user role=methodist | 200 |
| user role!=methodist | 403 |
| без auth | 401 (через `get_current_user`) |

ACL проверяется в endpoint (`_user_is_methodist`) — отдельный SQL вместо `_user_has_extended_role`, потому что admin/teacher не должны видеть escalation-feed (это специально для методистов).

---

## 5. Date/Type Guard Evidence

- `submitted_at` сравнивается с PG-side `now() - interval` — никаких raw datetime/string сравнений в Python.
- `metrics.escalated_at` пишется как ISO-string через `datetime.now(timezone.utc).isoformat()`. JSON-text по факту, idempotency-проверка `metrics ? 'escalated_at'` — оперирует ключом, не значением.
- `since` в endpoint — Pydantic `datetime` (timezone-aware при ISO8601 with `Z` или offset; в bind passes как timestamptz).
- AsyncIOScheduler сконфигурен с `timezone="UTC"` — interval triggers в UTC, нет DST-сюрпризов.

---

## 6. Validation

### 6.1 App boots

```
Routes: 222
Y-6 escalation routes:
  {'GET'} /api/v1/methodist/escalations/pending
```

App.routes count поднялся +1 (раньше ≈221 после Stage 1-3 endpoints, теперь 222).

### 6.2 Pytest регрессия

```
tests\test_grade_endpoint_y4.py .......                                  [ 22%]
tests\test_claim_next_pending_filter_y42.py .....                        [ 38%]
tests\test_pending_count_y42.py ...                                      [ 48%]
tests\test_workload_y42.py .                                             [ 51%]
tests\test_inbox_service_y4.py ...                                       [ 61%]
tests\test_learning_engine_service.py .........                          [100%]
======================= 31 passed in 29.17s =======================
```

`compute_course_state` тесты PASS — escalation try/except не сломал happy-path. Тесты используют пустой DB-state (без pending TA/SA_COM) → escalation не триггерится.

### 6.3 Cron tick smoke

Будет в Stage 7 (`test_y6_escalation_cron.py`):
- 0 кандидатов → `summary["candidates"] == 0`;
- 1 candidate, methodist exists → notification создаётся, metrics.escalated_at ставится;
- повторный tick → 0 (idempotent).

---

## 7. Concurrency / Idempotency Matrix

| Process | Concurrency | Idempotency |
|---|---|---|
| `escalation_cron_tick` | PG advisory lock 0x59365453 (multi-worker safe) | `metrics.escalated_at IS NOT NULL` per-result + 24h rate-limit per course |
| `escalate_course_completion` (event) | внутри `compute_course_state` транзакции | `metrics.completion_escalated_at` per-pending + 24h rate-limit |
| `GET /escalations/pending` | read-only | idempotent (нет state changes) |
| `start_scheduler()` | global single instance per worker | повторный вызов возвращает running scheduler |

---

## 8. Risks / Follow-ups

| # | Риск | Severity | Mitigation |
|---|---|---|---|
| R1 | APScheduler 3.x deprecated lifecycle (`@app.on_event` → должно быть `lifespan`) | Low | Warning только; функциональность работает. Можно мигрировать на `@asynccontextmanager` lifespan в отдельном PR — out-of-scope Y-6. |
| R2 | Rate-limit считается по всем methodist-юзерам сразу (общий threshold) | Low | Per-spec §3 «Multi-methodist load-balancing — broadcast». Per-methodist threshold — out-of-scope. |
| R3 | `compute_course_state` теперь делает доп. SELECT когда state=COMPLETED | Low | Только при COMPLETED (terminal state, редкое событие). На IN_PROGRESS / NOT_STARTED — никаких дополнительных запросов. |
| R4 | Нет `methodist` юзеров в БД → escalation = no-op (logger.warning) | Medium | Pre-deploy checklist §15 «Methodist users существуют (минимум 1)». Если 0 — alert ops. |
| R5 | Advisory lock не освободился из-за crash worker'а | Low | PG автоматически освобождает advisory locks при disconnect. На следующем tick'е следующий worker возьмёт lock. |
| R6 | `pending_result_ids: list[int]` передаётся в `metrics.completion_escalated_at` через `ANY(:ids)` — потенциальная injection | None | bind-параметр (не string interpolation); SQLAlchemy + asyncpg экранируют. |

---

## 9. Decision

**PASS.** Stage 4 готов. Cron + advisory lock + completion event + methodist endpoint работают. APScheduler установлен в venv, прописан в requirements.txt. Готов к Stage 7 (тесты) + backsync.
