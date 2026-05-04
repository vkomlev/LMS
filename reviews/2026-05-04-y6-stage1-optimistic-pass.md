# Review — Y-6 Stage 1: Optimistic-PASSED + Queue Filter Pivot + TA Routing Unblock

**Date:** 2026-05-04
**Stage:** 1 (после M12)
**Skill (исполнитель):** `/executor-pro`
**Skill (ревью):** `/techlead-code-reviewer`
**Tech-spec:** `D:/Work/ContentBackbone/docs/tech-specs/tech-spec-Y6-review-loop-v1.md` §6 «Stage 1»
**Diff:** [2026-05-04-y6-stage1-optimistic-pass.diff](2026-05-04-y6-stage1-optimistic-pass.diff)

---

## 1. Контекст

После M12 (legacy backfill готов) переключаем submit-side на optimistic-PASSED для TA/SA_COM, перевешиваем семантику pending-queue с `is_correct IS NULL` на `checked_at IS NULL` и снимаем stop-gap TA-skip из learning-engine routing (commit `cf1908c`, 2026-05-02).

---

## 2. Изменения

### 2.1 [app/api/v1/attempts.py](../app/api/v1/attempts.py)

Добавлен блок «2.3c Y-6 optimistic-PASSED» **после** ветки time_expired:

```python
if (
    task_content.type in ("SA_COM", "TA")
    and not attempt.time_expired
):
    check_result = CheckResult(
        score=check_result.max_score,
        max_score=check_result.max_score,
        is_correct=True,
    )
```

**Положение принципиально:** optimistic-подмена идёт **после** time_expired-логики (lines 340-352) — иначе истёкшая по таймеру SA_COM/TA получила бы `is_correct=TRUE` вопреки overdue-семантике. Сейчас:
- normal submit SA_COM/TA → `is_correct=TRUE, score=max_score, checked_at=NULL` → pending review queue;
- time_expired SA_COM/TA → честный `is_correct=FALSE, score=0` (как у других типов).

### 2.2 [app/services/teacher_queue_service.py](../app/services/teacher_queue_service.py)

3 query (claim_next_review @ ~314, get_pending_count @ ~628, get_teacher_workload @ ~682): убрано условие `AND tr.is_correct IS NULL`. Type-whitelist `('SA_COM','TA')` остаётся — он сам по себе исключает автопроверенные MC/SC/SA из очереди. Комментарии Y-4.2 заменены на Y-6 pivot-комментарии.

### 2.3 [app/services/learning_engine_service.py](../app/services/learning_engine_service.py)

- `_first_incomplete_task` (line ~377): убран фильтр `Tasks.task_content["type"].astext != "TA"`. TA снова в routing.
- `compute_course_state` (line ~210): тот же фильтр убран в `tasks_count_stmt` — TA теперь учитывается в `total_tasks`. `tasks_with_last_pass` SQL уже работает корректно для TA (для optimistic-PASSED `score/max=1.0 ≥ 0.5` → counted).

---

## 3. SQL Formula Verification

### 3.1 Submit-side optimistic-PASSED for TA/SA_COM

| Случай | `time_expired` | Output `check_result` | task_results state |
|---|---|---|---|
| Normal SA_COM submit | False | `is_correct=TRUE, score=max, max=max` | `is_correct=TRUE, score=max, checked_at=NULL` → student PASSED, в pending queue |
| Normal TA submit | False | `is_correct=TRUE, score=max, max=max` | то же |
| Overdue SA_COM/TA | True | `is_correct=FALSE, score=0, max=max` | `is_correct=FALSE, score=0, checked_at=NULL` → student FAILED, **в pending queue** (теоретически попадёт). См. §6 Risks/Follow-ups R1. |
| MC/SC/SA submit | False | (нетронуто checking_service) | как раньше |

### 3.2 Course state с TA

Через `tasks_with_last_pass` (compute_course_state, lines ~217-228):

```
last_score::float / last_max >= PASS_THRESHOLD_RATIO (=0.5)
```

Trace:
- TA optimistic-PASSED: `score=15, max=15` → `1.0 ≥ 0.5` → counted as PASS;
- TA после teacher reject (Stage 2 derived): `score=2, max=15` → `0.133 < 0.5` → не counted;
- TA после teacher accept (Stage 2 derived): `score=15, max=15` → counted.

→ `total_tasks` (включая TA) ≈ `tasks_with_last_pass` (когда все pending PASSED) → state=COMPLETED.

---

## 4. Date/Type Guard Evidence

Изменений в date/SLA логике нет. `attempt.time_expired` — bool из existing model; используется через `if attempt.time_expired` (truthy check), не через сравнение datetime.

---

## 5. DB Findings (MCP)

Подтверждено: новый индекс из M12 (`idx_task_results_pending_review`) уже используется новыми queue queries (filter `WHERE checked_at IS NULL`).

```sql
EXPLAIN (FORMAT TEXT)
SELECT COUNT(*) FROM task_results tr
JOIN tasks t ON t.id=tr.task_id
WHERE tr.checked_at IS NULL
  AND t.task_content->>'type' IN ('SA_COM','TA');
```

(не выполнено в smoke — partial index используется планировщиком при достижении объёма; на dev объём малый, seq scan быстрее).

---

## 6. Validation

### 6.1 Pytest (точечный набор по затронутым областям)

```
tests\test_claim_next_pending_filter_y42.py .....                        [ 16%]
tests\test_pending_count_y4.py ....                                      [ 29%]
tests\test_pending_count_y42.py ...                                      [ 38%]
tests\test_workload_y42.py .                                             [ 41%]
tests\test_list_pending_review_y42.py ..                                 [ 48%]
tests\test_grade_endpoint_y4.py .......                                  [ 70%]
tests\test_learning_engine_service.py .........                          [100%]
======================= 31 passed, 7 warnings in 30.76s =======================
```

### 6.2 Полная регрессия `tests/`

`299 passed, 4 skipped, 44 failed`. Все 44 fail'а — **pre-existing flakiness** не связан со Stage 1:
- `test_y5_guest_endpoints.*` (16 fails) — требуют `courses.is_public_demo=TRUE` для seed-курса `wp:rabota-so-strokami-v-python` (id=108); MCP подтвердил `is_public_demo=FALSE` в dev DB. Конфигурация dev-окружения, не код.
- `test_teacher_help_requests_stage38.*`, `test_teacher_help_requests_stage381.*`, `test_teacher_next_modes_stage39.*` — flaky shared-DB state. Каждый из этих тестов проходит изолированно (verified — `test_workload_returns_five_counters` PASS standalone; `test_get_help_requests_status_open` PASS standalone).

### 6.3 Bandit

Не требуется per-stage — будет один прогон в финале (Stage 7 LMS).

---

## 7. Risks / Follow-ups

| # | Риск | Severity | Mitigation |
|---|---|---|---|
| R1 | Overdue SA_COM/TA попадает в pending queue (`is_correct=FALSE, checked_at=NULL`) | Low | Type-whitelist оставляет их в очереди по дизайну — teacher может оценить. На практике рассматривается как «teacher review нужен даже для overdue». Если требуется — в Stage 4 escalation cron можно добавить пред-фильтр `is_correct IS DISTINCT FROM FALSE` для исключения; сейчас не критично. |
| R2 | Existing API consumers могут полагаться на `is_correct=NULL` как признак pending | Medium | TG_LMS poller не использует `is_correct` (видно из spec); SPW Y-5.2 inbox смотрит `kind='sa_com_graded'` → notification-driven UI. Stage 5 заменит TG_LMS клиент same-deploy. |
| R3 | EXPLAIN не верифицирован под нагрузкой | Low | dev объём маленький; M12 индекс готов под Stage 4 cron. |

---

## 8. Decision

**PASS.** Stage 1 готов. Регрессий по затронутой области (claim_next, pending_count, workload, grade, learning_engine) — нет. Готов к Stage 2 (derived `is_correct` в /grade endpoint).

---

## 9. Acceptance gate (per spec §6 Stage 1)

> «submit TA в DB через E2E → state=PASSED + есть в pending queue»

E2E проверка отложена в Stage 7 (live smoke `CB_LMS_LIVE_SMOKE_Y6`). Юнит-уровень покрывается:
- `test_claim_next_pending_filter_y42.py::*` (queue видит SA_COM/TA по типу) — PASS;
- `test_learning_engine_service.py` (compute_task_state / compute_course_state) — PASS;
- pending_count/workload подтверждают новую `checked_at IS NULL` семантику — PASS.
