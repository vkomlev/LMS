# Review — Y-6 Stage 2: Derived `is_correct` в /grade endpoint

**Date:** 2026-05-04
**Stage:** 2 (после Stage 1)
**Skill (исполнитель):** `/executor-pro`
**Skill (ревью):** `/techlead-code-reviewer`
**Tech-spec:** `D:/Work/ContentBackbone/docs/tech-specs/tech-spec-Y6-review-loop-v1.md` §6 «Stage 2»
**Diff:** [2026-05-04-y6-stage2-derived-is-correct.diff](2026-05-04-y6-stage2-derived-is-correct.diff)

---

## 1. Контекст

После Stage 1 SA_COM/TA submit пишет `is_correct=TRUE` сразу — поэтому old idempotency check `is_correct IS NOT NULL → 409` ломается (всегда true). Также teacher больше не должен явно передавать `is_correct` — это субъективное толкование «принять/отклонить»; сервер выводит из ratio.

---

## 2. Изменения

### 2.1 [app/core/config.py](../app/core/config.py)

Добавлены 4 Y-6 константы (env-overridable):
- `review_pass_threshold_ratio = 0.2`
- `escalation_timeout_hours = 48`
- `escalation_cron_interval_min = 5`
- `methodist_rate_limit_per_day_per_course = 1`

Existing `learning_engine_v1`-стиль `PASS_THRESHOLD_RATIO=0.5` (auto-check) остаётся в `learning_engine_service.py` без изменений.

### 2.2 [app/services/audit_service.py](../app/services/audit_service.py)

3 новых event-type константы:
- `TEACHER_REVIEW_REJECTED = "teacher.review.rejected"` (Stage 2)
- `TEACHER_REVIEW_REGRADED = "teacher.review.regraded"` (Stage 3)
- `METHODIST_ESCALATION_TRIGGERED = "methodist.escalation.triggered"` (Stage 4)

### 2.3 [app/schemas/teacher_next_modes.py](../app/schemas/teacher_next_modes.py)

`ReviewGradeRequest`:
- удалено поле `is_correct: bool`;
- docstring обновлён: client передаёт только `score` (+ опц. `comment`).

### 2.4 [app/services/teacher_queue_service.py](../app/services/teacher_queue_service.py) — `grade_review()`

| Изменение | До | После |
|---|---|---|
| Параметр функции | `is_correct: bool` обязателен | удалён — server-side derived |
| Idempotency check | `existing_is_correct is not None → 409` | `existing_checked_at is not None → 409` |
| SELECT FOR UPDATE | возвращал `is_correct, ...` для idem-check | возвращает `is_correct, checked_at, ...` |
| Compute | — | `is_correct = (score / max_score) >= REVIEW_PASS_THRESHOLD_RATIO` |
| UPDATE | `is_correct=:is_correct` (из аргумента) | `is_correct=:is_correct` (из computed) |

### 2.5 [app/api/v1/teacher_reviews.py](../app/api/v1/teacher_reviews.py) — `review_grade()`

| Изменение | Эффект |
|---|---|
| Не передаёт `is_correct` в `grade_review()` | derived server-side |
| `notif_kind` ветвится: `is_correct=TRUE → 'sa_com_graded'`, `FALSE → 'task_returned_for_rework'` | новый kind |
| `notif_title` зависит от `is_correct` | UX-clarity для negative |
| `_render_inbox_content` принимает `is_correct` | разный body content |
| `payload` notification — добавлено `previous_score: None` | future-compat для Stage 3 (regrade сообщит prev) |
| Audit: `teacher.review.graded` всегда + `teacher.review.rejected` при negative | гранулярная audit-метрика |
| Email best-effort: только при `is_correct=TRUE` | для negative student уведомлён через inbox + TG; spam-redux |

---

## 3. SQL Formula Verification

### Derived `is_correct` (REVIEW_PASS_THRESHOLD_RATIO = 0.2)

```python
pass_ratio = float(_settings.review_pass_threshold_ratio)
is_correct = (float(score) / float(effective_max)) >= pass_ratio
```

| score | max_score | ratio | is_correct |
|---|---|---|---|
| 1 | 15 | 0.0667 | **False** (FAILED) |
| 2 | 15 | 0.1333 | **False** |
| 3 | 15 | 0.2000 | **True** (boundary inclusive) |
| 8 | 10 | 0.8000 | **True** |
| 15 | 15 | 1.0000 | **True** |
| 0 | 15 | 0.0000 | **False** |

Edge: `effective_max <= 0` → `GradeValidationError` ещё до compute (line ~534). Деления на ноль не возникает.

### Idempotency pivot

| Семантика | До (Y-4) | После (Y-6) |
|---|---|---|
| Pending review state | `is_correct IS NULL ∧ checked_at IS NULL` | `checked_at IS NULL` (любое is_correct) |
| Already-graded → 409 | `is_correct IS NOT NULL` | `checked_at IS NOT NULL` |

После Stage 1 optimistic-PASSED `is_correct=TRUE` ставится сразу на submit, поэтому idem-check не может полагаться на is_correct. checked_at — единственное надёжное «teacher уже посмотрел».

---

## 4. Date/Type Guard Evidence

`existing_checked_at` берётся из SELECT `tr.checked_at` (timestamptz по schema). Сравнивается через `is not None` — type-safe (None или datetime). Никаких raw-string сравнений с datetime.

`now = datetime.now(timezone.utc)` — UTC-aware (line ~489); параметризован в SQL bind как timestamptz — согласуется с `task_results.checked_at` колонкой.

---

## 5. Validation

### Pytest по затронутым областям

```
tests\test_grade_endpoint_y4.py .......                                  [ 11%]
tests\test_claim_next_pending_filter_y42.py .....                        [ 19%]
tests\test_pending_count_y4.py F...                                      [ 25%]
tests\test_pending_count_y42.py ...                                      [ 30%]
tests\test_workload_y42.py .                                             [ 31%]
tests\test_list_pending_review_y42.py ..                                 [ 35%]
tests\test_inbox_service_y4.py ...                                       [ 39%]
tests\test_acl_hierarchical_y41.py ......F                               [ 50%]
tests\test_notification_email_service_y4.py ..                           [ 53%]
tests\test_me_history_y4.py ............                                 [ 73%]
tests\test_me_notifications_endpoints_y4.py ...........                  [100%]
========== 2 failed, 61 passed, 1 skipped ==========
```

**Анализ 2 fails:**

| Тест | Причина |
|---|---|
| `test_pending_count_y4::test_pending_count_sees_own_course_pending` | `_pick_task_with_course` использует `LIMIT 1` без `ORDER BY` → подбирается случайная задача (SC/MC) → type-whitelist `('SA_COM','TA')` исключает её → count=0 != ожидаемому ≥1. **Pre-existing flakiness** (DB ordering зависит от плана PG). |
| `test_acl_hierarchical_y41::test_teacher_self_attached_root_with_root_task_still_works` | Та же проблема: `LIMIT 1 FROM tasks WHERE course_id=root_id` — random task type. |

Оба теста возникают НЕЗАВИСИМО от моих Stage 2 изменений (фильтр type был и до Y-6: `AND t.task_content->>'type' IN ('SA_COM','TA')`). MCP confirms: первая задача в `WHERE course_id=1 LIMIT 1` сейчас id=35 (type=SC). Чтобы тест стал детерминирован, нужен `ORDER BY id` + фильтр по type — фикс fixture, не моя scope.

**Точечные test'ы grade endpoint:** все 7 PASS (Pydantic v2 default extra=ignore — старые `is_correct: True` в body просто игнорируются; score-значения в тестах ≥ 50% от max → derived True → assertions PASS).

### Bandit

Не запускался per-stage (выполню в финале Stage 7).

---

## 6. Backward Compatibility / Soft-deprecation

| Consumer | Поведение |
|---|---|
| **TG_LMS Y-4 teacher dialog** | Продолжает посылать `is_correct: bool` в body — **Pydantic v2 ignore-extra** делает это no-op. Compute server-side derived замещает значение. Same-deploy update в Stage 5 хорошо, но не обязателен на день переключения. |
| **SPW Y-4** | Не вызывает grade напрямую (teacher-only endpoint). Не затронут. |
| **HTTP 422 поведение** | Старые валидации не сломаны: `score > max → 422` сохранён. |

---

## 7. Risks / Follow-ups

| # | Риск | Severity | Mitigation |
|---|---|---|---|
| R1 | Старая фикстура `_create_pending_tr` пишет `is_correct=NULL` — для new pending после Stage 1 это «pre-optimistic» state. Live-flow гарантирует `is_correct=TRUE`, но fixture-based tests останутся с NULL. | Low | После Stage 7 (новые тесты) можно перевести fixture на `is_correct=TRUE`. Существующие тесты OK — checked_at IS NULL все ещё true. |
| R2 | Если кто-то вызывает `grade_review` пrogrammatic (не через HTTP) с `is_correct=` kwarg | Low | grep по `grade_review(` в репо — только HTTP handler-вызов; service-uses не нашёл. |
| R3 | `teacher.review.rejected` audit event новый — мониторинг dashboards может не видеть | Low | Backsync обновит cross-project contracts (Stage 4 / handoff §17). |
| R4 | `task_returned_for_rework` notification kind не рендерится в SPW Stage 6.4 (out-of-scope этой сессии) | Low | SPW Stage 6.4 — отдельная сессия. До тех пор inbox показывает default rendering (title+content); functional. |

---

## 8. Decision

**PASS.** Stage 2 готов. Server-side derived работает; idempotency pivot работает; новый kind `task_returned_for_rework` в notifications записывается; audit `teacher.review.rejected` логируется при negative grade. Готов к Stage 3 (POST /regrade).
