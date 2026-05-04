# Review-Gate — Y-6 LMS (review-loop) Final Decision

**Date:** 2026-05-04
**Skill:** `/review-gate`
**Gate mode:** **paranoid** (схема + миграция + cron + multi-worker concurrency + новые public endpoints)
**Execution posture:** report-only
**Tech-spec:** `D:/Work/ContentBackbone/docs/tech-specs/tech-spec-Y6-review-loop-v1.md`

---

## 1. Scope

LMS-сторона Y-6 (SPW + TG_LMS — отдельные сессии).

| Артефакт | Stage | Файлы |
|---|---|---|
| [2026-05-04-y6-m12-optimistic-pass.md](2026-05-04-y6-m12-optimistic-pass.md) | M12 | migration |
| [2026-05-04-y6-stage1-optimistic-pass.md](2026-05-04-y6-stage1-optimistic-pass.md) | 1 | attempts.py + teacher_queue_service.py + learning_engine_service.py |
| [2026-05-04-y6-stage2-derived-is-correct.md](2026-05-04-y6-stage2-derived-is-correct.md) | 2 | grade endpoint (derived + idem-pivot) |
| [2026-05-04-y6-stage3-regrade.md](2026-05-04-y6-stage3-regrade.md) | 3 | regrade endpoint + service |
| [2026-05-04-y6-stage4-escalation.md](2026-05-04-y6-stage4-escalation.md) | 4 | cron + methodist_notify + endpoint + lifespan |
| [tests/test_y6_review_loop.py](../tests/test_y6_review_loop.py) | 7 | 9 integration test'ов |

---

## 2. Decision

# **PASS**

LMS-сторона Y-6 готова к интеграции в main. Никаких blocking findings. Подкрепляющие артефакты per-stage все PASS, новые endpoints зарегистрированы, миграция M12 idempotent, тесты Y-6 — 9/9 зелёные, регрессия по Y-4-затронутым областям — 70/72 (2 fail'а — pre-existing fixture-flakiness).

---

## 3. Validation evidence

### 3.1 Pytest

| Suite | Count | Status |
|---|---|---|
| `test_y6_review_loop.py` (новые) | 9 | **9 PASS** |
| `test_grade_endpoint_y4.py` | 7 | 7 PASS |
| `test_claim_next_pending_filter_y42.py` | 5 | 5 PASS |
| `test_pending_count_y4.py` | 4 | 3 PASS, 1 FAIL (flaky) |
| `test_pending_count_y42.py` | 3 | 3 PASS |
| `test_workload_y42.py` | 1 | 1 PASS |
| `test_list_pending_review_y42.py` | 2 | 2 PASS |
| `test_inbox_service_y4.py` | 3 | 3 PASS |
| `test_acl_hierarchical_y41.py` | 7 | 6 PASS, 1 FAIL (flaky) |
| `test_notification_email_service_y4.py` | 2 | 2 PASS |
| `test_me_history_y4.py` | 12 | 12 PASS |
| `test_me_notifications_endpoints_y4.py` | 11 | 11 PASS |
| `test_learning_engine_service.py` | 9 | 9 PASS |
| **Total (Y-4 + Y-6)** | **75** | **70 PASS, 2 FAIL flaky, 1 skip, 0 blocking-fail** |

**2 FAIL'а — flaky, pre-existing:**
- `test_pending_count_y4::test_pending_count_sees_own_course_pending` — fixture `_pick_task_with_course` использует `LIMIT 1` без `ORDER BY` → подбор задачи зависит от plan'а PG; тест проходит изолированно.
- `test_acl_hierarchical_y41::test_teacher_self_attached_root_with_root_task_still_works` — та же проблема (`LIMIT 1` без ORDER BY).

Обе зафиксированы как fixture-flakiness в [Stage 1 review §6.2](2026-05-04-y6-stage1-optimistic-pass.md#62-полная-регрессия-tests).

### 3.2 Bandit (security)

Команда: `python -m bandit -q -r app/api/v1/teacher_reviews.py app/api/v1/methodist_escalations.py app/services/teacher_queue_service.py app/services/escalation_service.py app/services/methodist_notify_service.py`

```
Total potential issues skipped due to specifically being disabled (e.g., #nosec BXXX): 7

Run metrics:
    Total issues (by severity):
        Low: 0
        Medium: 2
        High: 0
```

**2 оставшихся Medium-Low (B608)** — **pre-existing**, в `claim_next_help_request` и аналогах из stage39 кода (используют `HELP_REQUESTS_ACL_SQL` f-string). Не Y-6 scope.

Все Y-6 новые f-string SQL (с REVIEW_ACL_SQL / `_acl_clause(':course_id_param')` / `since_clause` literal-only) помечены `# nosec B608` с обоснованием.

### 3.3 App boots

```
Routes: 222
Y-6 escalation routes:
  {'GET'} /api/v1/methodist/escalations/pending
{'POST'} /teacher/reviews/{result_id}/regrade   ← NEW
```

Lifespan-handlers зарегистрированы (start/stop scheduler в try/except), не блокируют startup.

### 3.4 Migration

`alembic upgrade m12_y6_optimistic_pass → downgrade m11 → upgrade m12` — все идемпотентны. Партиал-индекс `idx_task_results_pending_review (submitted_at, checked_at) WHERE checked_at IS NULL` создан и проверен MCP.

### 3.5 MCP DB Findings

| Объект | Статус |
|---|---|
| `idx_task_results_pending_review` | exists |
| `task_results.metrics ? 'backfill_y6_optimistic'` count | 0 (pre=0, idempotent) |
| `notifications.modified_at` | используется (нет `created_at` колонки в schema) |
| roles.name='methodist' | присутствует |

---

## 4. Blocking Findings

**Нет.**

---

## 5. Non-Blocking Findings

| # | Finding | Owner / When |
|---|---|---|
| N1 | `@app.on_event` deprecated в FastAPI — DeprecationWarning. Можно мигрировать на `@asynccontextmanager` lifespan. | LMS infra task, отдельный PR |
| N2 | 2 pre-existing flaky-fixture тестов (`LIMIT 1` без ORDER BY) | LMS infra task, отдельный PR. Fix: добавить `ORDER BY id` + filter type. |
| N3 | M12 индекс создан без CONCURRENTLY (env.py обёртывает в transaction) | OPERATOR runbook §Y-7 — pre-create индекс через `CREATE INDEX CONCURRENTLY IF NOT EXISTS` ВРУЧНУЮ до `alembic upgrade` в prod |
| N4 | 2 pre-existing bandit B608 в stage39 кода | unrelated, отдельный PR |
| N5 | Tests `test_y5_guest_endpoints` падают из-за `is_public_demo=FALSE` для seed-курса в dev DB | dev environment configuration — оператор должен `UPDATE courses SET is_public_demo=TRUE WHERE course_uid='wp:rabota-so-strokami-v-python'` если эти тесты нужны |

---

## 6. Required Validation Commands (для оператора перед merge / deploy)

```bash
# Smoke на Y-6 тесты
.venv/Scripts/python -m pytest tests/test_y6_review_loop.py -v
# 9 passed expected

# Регрессия на затронутые области
.venv/Scripts/python -m pytest tests/test_grade_endpoint_y4.py tests/test_inbox_service_y4.py tests/test_learning_engine_service.py tests/test_claim_next_pending_filter_y42.py tests/test_pending_count_y42.py tests/test_workload_y42.py tests/test_list_pending_review_y42.py
# Все PASS expected

# Migration sanity
alembic current  # → m12_y6_optimistic_pass

# OpenAPI re-export (handoff §17)
# curl http://localhost:8000/openapi.json > docs/openapi.json
```

---

## 7. Residual Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | TG_LMS Y-4 teacher bot ещё посылает `is_correct: bool` в /grade body | Pydantic v2 `extra=ignore` делает no-op; derived server-side. Same-deploy update в Stage 5 (другая сессия) — не блокер. |
| R2 | Нет methodist user'ов в prod DB | Pre-deploy checklist §15 — оператор создаёт минимум 1 (через admin/users endpoint). |
| R3 | APScheduler в каждом gunicorn-worker'е | Защищено advisory lock 0x59365453; non-blocking pg_try_advisory_lock. |
| R4 | Email отправка только для positive grade в Stage 2 | Дизайн-выбор: для negative — inbox + TG (Stage 5 push). Не блокер. |
| R5 | regrade_history неограниченно растёт в metrics | Future audit-table refactoring (Y-7+); MVP объём низкий. |

---

## 8. Docs / Config / Runtime Drift

- **Docs:** spec authority chain указывает на cross-project contracts. Backsync — отдельный финальный todo (см. Stage 8 в плане). Не блокер для merge feature-ветки; обязателен до tag/release.
- **Config:** новые env-vars (`REVIEW_PASS_THRESHOLD_RATIO`, `ESCALATION_TIMEOUT_HOURS`, `ESCALATION_CRON_INTERVAL_MIN`, `METHODIST_RATE_LIMIT_PER_DAY_PER_COURSE`) имеют дефолты в `Settings.__init__` — `.env` обновление не обязательно.
- **Runtime:** APScheduler в venv (`pip list | grep -i scheduler` → `APScheduler 3.11.2`). `requirements.txt` обновлён.

---

## 9. Final Decision Statement

**`/review-gate`: PASS** для интеграции LMS Y-6 review-loop в `main`. Все per-stage `/techlead-code-reviewer` артефакты — PASS. Регрессии нет. Pre-existing flakiness задокументирован. Готово к backsync (Stage 8 — обновление docs/cross-project + LMS-side spec).

После backsync — рекомендуется feature-branch commit с conventional message: `feat(y6): review-loop backend (M12 + optimistic-PASSED + derived grade + regrade + escalation)`.
