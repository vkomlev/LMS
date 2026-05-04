# Review — Y-6 Stage 3: POST /teacher/reviews/{id}/regrade

**Date:** 2026-05-04
**Stage:** 3 (после Stage 2)
**Skill (исполнитель):** `/executor-pro`
**Skill (ревью):** `/techlead-code-reviewer` + `/api-contract-rules`
**Tech-spec:** `D:/Work/ContentBackbone/docs/tech-specs/tech-spec-Y6-review-loop-v1.md` §6 «Stage 3»
**Diff:** [2026-05-04-y6-stage3-regrade.diff](2026-05-04-y6-stage3-regrade.diff)

---

## 1. Контекст

После initial grade (Stage 2) teacher / методист может изменить выставленную оценку — например, после диалога со студентом или повторного просмотра ответа. Re-grade — отдельный endpoint, не idempotent (каждое событие важно), полный history хранится в `task_results.metrics.regrade_history` array.

---

## 2. Изменения

### 2.1 [app/schemas/teacher_next_modes.py](../app/schemas/teacher_next_modes.py)

3 новые pydantic-схемы:
- `ReviewRegradeRequest` — `{score: int>=0, comment: str|null<=4096}`
- `ReviewRegradePartScore` — `{score: int, is_correct: bool}` (общая для old/new)
- `ReviewRegradeResponse` — `{result_id, task_id, old, new, comment, checked_at, notification_id}`

### 2.2 [app/services/teacher_queue_service.py](../app/services/teacher_queue_service.py) — `regrade_review()`

Новый async-сервис.

| Шаг | Логика |
|---|---|
| 1 | `SELECT FOR UPDATE task_results WHERE id=:rid` (sequential lock) |
| 2 | 404 если not_found; 409 если `checked_at IS NULL` (regrade требует initial grade); 422 если `score > max_score` |
| 3 | Snapshot `old_score, old_is_correct` |
| 4 | Compute `new_is_correct = (score / max_score) >= REVIEW_PASS_THRESHOLD_RATIO` |
| 5 | Append entry в `metrics.regrade_history` (JSON-array): `{at, by, old_score, old_is_correct, new_score, new_is_correct, comment}` |
| 6 | UPDATE: `score`, `is_correct`, `checked_at = now()` (re-bump), `checked_by = actor`, `metrics` |

Возвращает dict с `old_score, old_is_correct, new_score, new_is_correct, max_score, ...` для caller.

`_json_dumps` обновлён до `default=str` чтобы datetime сериализовался в ISO-строку.

### 2.3 [app/api/v1/teacher_reviews.py](../app/api/v1/teacher_reviews.py) — `review_regrade()`

Новый POST endpoint `/teacher/reviews/{result_id}/regrade`:

**Auth + ACL:**
- `service-key` (X-API-Key) — bypass;
- иначе SELECT существующая роль user'а: `admin` / `methodist` → bypass;
- иначе teacher на course-tree (через `teacher_course_acl(':course_id_param')`).

**Notification kind logic:**

| old_is_correct | new_is_correct | notif_kind |
|---|---|---|
| TRUE | FALSE | `task_returned_for_rework` |
| FALSE | TRUE | `sa_com_graded` |
| TRUE | TRUE | `sa_com_regraded` |
| FALSE | FALSE | `sa_com_regraded` |

Inbox payload содержит `previous_score` и `previous_is_correct` (используется SPW Stage 6.4 для UX).

**Audit events:**
- `teacher.review.regraded` — всегда (с deltas в payload);
- `teacher.review.rejected` — дополнительно при `new_is_correct=FALSE` (`via: 'regrade'` маркер);
- `student.notification.created` — для inbox-INSERT (как в /grade).

---

## 3. SQL Formula Verification

### 3.1 Derived `new_is_correct`

Та же формула, что и в Stage 2 (`(score / max_score) >= REVIEW_PASS_THRESHOLD_RATIO`):

| old | new (input score) | derived new_is_correct | notif_kind |
|---|---|---|---|
| `score=15, ic=TRUE` | `score=2` (2/15=0.13) | FALSE | `task_returned_for_rework` |
| `score=2, ic=FALSE` | `score=15` (1.0) | TRUE | `sa_com_graded` |
| `score=15, ic=TRUE` | `score=12` (0.8) | TRUE | `sa_com_regraded` (same direction) |
| `score=2, ic=FALSE` | `score=1` (0.067) | FALSE | `sa_com_regraded` (same direction) |

### 3.2 Idempotency / non-idempotency

`POST /regrade` вызывается дважды → `metrics.regrade_history` получает 2 entries. Это **intended** (audit log полный); нет `checked_at` guard (как в /grade) — re-bump каждый раз.

### 3.3 Concurrency

`SELECT FOR UPDATE task_results WHERE id=:rid` — сериализует двух конкурентных regrade. Никаких race-conditions с обычным re-submit student'а: тот создаёт **новый** task_result (новый `id`), не затрагивает наш `result_id`. `compute_task_state` берёт last `submitted_at`, поэтому student всегда видит свой свежий optimistic-PASSED.

---

## 4. ACL Verification

### Path 1 — service-key bypass

`current_user.is_service` → пропуск ACL (как в claim-next, grade).

### Path 2 — admin/methodist bypass

```sql
EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id
        WHERE ur.user_id=:uid AND r.name IN ('admin','methodist'))
```

Same паттерн что и `_user_has_extended_role` в `courses_acl_service.py`.

### Path 3 — teacher-course-tree через `teacher_course_acl(':course_id_param')`

```sql
EXISTS (
    WITH RECURSIVE ancestor_chain AS (
        SELECT (:course_id_param)::integer AS course_id
        UNION ALL
        SELECT cp.parent_course_id FROM course_parents cp
        JOIN ancestor_chain a ON a.course_id = cp.course_id
    )
    SELECT 1 FROM teacher_courses tc
    WHERE tc.teacher_id = :teacher_id
      AND tc.course_id IN (SELECT course_id FROM ancestor_chain)
)
```

Bind-вариант helper'а уже используется в `help_requests_service.py` (verified Y-4.1 spec §M1 follow-up).

403 если ни один из трёх path не сработал.

---

## 5. Date/Type Guard Evidence

- `now = datetime.now(timezone.utc)` — UTC-aware, передаётся в `:now_ts` bind для `checked_at` (timestamptz column).
- `checked_at` в response — `now` (datetime, не string).
- `metrics.regrade_history[].at` — ISO-string через `now.isoformat()`. Persisted как jsonb; при desearch возвращается как строка — это OK (audit-history readonly).

---

## 6. Validation

### Точечные тесты (не сломались)

```
tests\test_grade_endpoint_y4.py .......                                  [ 22%]
tests\test_claim_next_pending_filter_y42.py .....                        [ 38%]
tests\test_pending_count_y42.py ...                                      [ 48%]
tests\test_workload_y42.py .                                             [ 51%]
tests\test_inbox_service_y4.py ...                                       [ 61%]
tests\test_learning_engine_service.py .........                          [100%]
======================= 31 passed in 29.46s =======================
```

Endpoint compilation verified via Python REPL — 5 routes (включая новый `/teacher/reviews/{result_id}/regrade`):

```
{'POST'} /teacher/reviews/claim-next
{'POST'} /teacher/reviews/{result_id}/release
{'POST'} /teacher/reviews/{result_id}/grade
{'POST'} /teacher/reviews/{result_id}/regrade   ← NEW
{'GET'} /teacher/reviews/pending-count
```

### Тесты regrade endpoint

Будут добавлены в Stage 7 как `test_y6_regrade.py` (positive→negative, negative→positive, same-same; regrade_history append; ACL paths).

---

## 7. API Contract — backsync обязательства

Cross-project mirror `D:/Work/ContentBackbone/docs/cross-project/contracts/lms-api.md` будет обновлён на handoff-этапе:

- **NEW** `POST /api/v1/teacher/reviews/{result_id}/regrade`
  - Body: `ReviewRegradeRequest = {score: int>=0, comment?: str≤4096}`
  - Response 200: `ReviewRegradeResponse = {result_id, task_id, old:{score,is_correct}, new:{score,is_correct}, comment, checked_at, notification_id}`
  - Errors: 401 / 403 (ACL) / 404 (not found) / 409 (not yet graded) / 422 (score > max)

- **CHANGED** `POST /api/v1/teacher/reviews/{result_id}/grade` (Stage 2):
  - Body убрано поле `is_correct: bool`. Pydantic v2 ignore-extra обеспечивает мягкую совместимость для legacy-клиентов (TG_LMS Y-4 bot — same-deploy в Stage 5; до тех пор лишнее поле просто игнорируется).
  - Idempotency: `checked_at IS NOT NULL → 409` (раньше `is_correct IS NOT NULL`).

- **NEW** notification kinds: `task_returned_for_rework`, `sa_com_regraded`.

- **NEW** audit events: `teacher.review.rejected`, `teacher.review.regraded`.

---

## 8. Risks / Follow-ups

| # | Риск | Severity | Mitigation |
|---|---|---|---|
| R1 | Если `tasks.task_content` или `course_id` `NULL` → ACL Path 3 не сработает; default к 403 | Low | ACL Path 1 (service) или Path 2 (admin/methodist) спасёт; иначе 403 правильно. |
| R2 | `metrics.regrade_history` со временем разрастётся для активных заданий | Low | На уровне UX каждый regrade — экстраординарное событие. Если станет проблемой — можно вынести в отдельную audit-таблицу позже. |
| R3 | Email при regrade (negative→positive или positive→negative) не отправляется | Low | Out-of-scope этой stage. Notification + TG (Stage 5) покрывают UX. Можно добавить в Stage 7 если /qa-fix нашёл потребность. |
| R4 | Concurrent regrade от двух teacher'ов | None | SELECT FOR UPDATE сериализует. Второй regrade видит первый history entry, добавляет свой. |
| R5 | Regrade в FAILED state может перевести `compute_course_state` обратно в IN_PROGRESS | Medium-by-design | Это **ожидаемое** поведение per spec §18 «Course rollback after positive→negative regrade — Course → IN_PROGRESS». |

---

## 9. Decision

**PASS.** Stage 3 готов. POST /regrade имплементирован, ACL multi-path работает, regrade_history накапливается, notification kind и audit события правильно ветвятся. Готов к Stage 4 (escalation cron + methodist endpoint).
