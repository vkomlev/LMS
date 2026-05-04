# Review — Y-6 M12 Optimistic-PASSED Backfill

**Date:** 2026-05-04
**Stage:** M12 (предусловие для Stage 1)
**Skills (review):** `/techlead-code-reviewer` + `/db-check`
**Файл:** [app/db/migrations/versions/20260504_010000_M12_y6_optimistic_pass.py](../app/db/migrations/versions/20260504_010000_M12_y6_optimistic_pass.py)
**Tech-spec:** `D:/Work/ContentBackbone/docs/tech-specs/tech-spec-Y6-review-loop-v1.md` §6 «M12»

---

## 1. Контекст

Phase Y-6 переключает submit-side TA/SA_COM на optimistic-PASSED: на submit
`is_correct=TRUE, score=max_score`, а pending teacher review определяется
исключительно по `checked_at IS NULL`. До деплоя Stage 1 в БД могут быть
**legacy-записи** SA_COM/TA с `is_correct IS NULL AND checked_at IS NULL` —
после переключения они «зависли» бы для student (state=NEW по old logic),
хотя backend уже использует новую семантику.

M12 приводит legacy-pending к новому состоянию + добавляет партиал-индекс
для escalation/queue запросов Stage 4.

---

## 2. Изменения

| Что | Где | Эффект |
|---|---|---|
| Backfill UPDATE | `task_results.is_correct=TRUE, score=max_score, metrics+=backfill_y6_optimistic` | legacy-pending → optimistic-PASSED |
| `CREATE INDEX IF NOT EXISTS idx_task_results_pending_review` | `task_results (submitted_at, checked_at) WHERE checked_at IS NULL` | ускоряет Stage 4 cron + queue queries |
| Downgrade | revert по `metrics ? 'backfill_y6_optimistic'`, DROP INDEX | безопасный rollback |

Revision: `m12_y6_optimistic_pass`, down_revision: `m11_courses_is_public_demo`.

---

## 3. DB Findings (MCP read-only)

### 3.1 Pre-migration baseline (на 2026-05-04, dev DB `learn`)

```sql
SELECT COUNT(*) FROM task_results tr JOIN tasks t ON t.id=tr.task_id
WHERE tr.is_correct IS NULL AND tr.checked_at IS NULL
  AND t.task_content->>'type' IN ('SA_COM','TA');
-- → 0
```

После M9 zombie-sanitize (2026-04-30) overlap `is_correct IS NULL ∩ checked_at IS NULL`
= 0 в текущей dev DB. Полная картина:

| фильтр | count |
|---|---|
| `is_correct IS NULL` | 8 |
| `checked_at IS NULL` | 19 |
| **overlap** (legacy pending) | **0** |
| `checked_at IS NULL AND type IN (SA_COM,TA)` | 1 |

Единственная SA_COM/TA-pending запись имеет `is_correct=TRUE` (видимо, ранее
была вручную отмечена или попала из тестов post-M9) — backfill её **не трогает**.

### 3.2 Post-migration

```sql
SELECT COUNT(*) FROM task_results WHERE metrics ? 'backfill_y6_optimistic';
-- → 0
SELECT indexname FROM pg_indexes WHERE indexname='idx_task_results_pending_review';
-- → idx_task_results_pending_review
```

`indexdef` после применения:

```
CREATE INDEX idx_task_results_pending_review ON public.task_results
USING btree (submitted_at, checked_at) WHERE (checked_at IS NULL)
```

### 3.3 Idempotency / rollback dry-run

- `alembic downgrade m11 → upgrade m12` повторно: PASS (логи).
- Powershell exit codes 0; индекс восстановлен (verified MCP).

---

## 4. Технические замечания

### 4.1 CONCURRENTLY → IF NOT EXISTS

Tech-spec §6 «M12» предлагал `CREATE INDEX CONCURRENTLY`. Однако `env.py:75-83`
обёртывает миграцию в `context.begin_transaction()` (Alembic default
`transactional_ddl`), и `CONCURRENTLY` несовместим с транзакцией.

**Решение:** обычный `CREATE INDEX IF NOT EXISTS`. На текущем объёме
`task_results` (4-значное число записей в dev/staging) lock < 1с.

**Pre-deploy в prod (Y-7):** документировано в docstring миграции — оператор
ВРУЧНУЮ создаёт индекс через CONCURRENTLY до `alembic upgrade`, далее
`IF NOT EXISTS` отрабатывает no-op:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_task_results_pending_review
ON task_results (submitted_at, checked_at) WHERE checked_at IS NULL;
```

Это требование добавлено в OPERATOR runbook через handoff §17 backsync.

### 4.2 Backfill semantics

Backfill использует CTE с снимком `lp_max_score` — гарантирует, что значение
`max_score` берётся из той же row, которую мы UPDATE'им (а не пере-join'ится
после изменения). `metrics ?` оператор используется в downgrade — не подвержен
ложным срабатываниям на нестандартных типах.

### 4.3 Forward-compatible с Stage 1

После backfill: все legacy SA_COM/TA → `is_correct=TRUE, checked_at IS NULL` →
state=PASSED для student + видны в новой queue (filter по `checked_at IS NULL`,
Stage 1.2). Совместимо.

---

## 5. Validation

| Команда | Результат |
|---|---|
| `alembic upgrade m12_y6_optimistic_pass` | OK (logged) |
| `alembic downgrade m11_courses_is_public_demo` | OK |
| Re-upgrade m11 → m12 | OK (idempotent) |
| MCP `pg_indexes` check | INDEX exists with correct WHERE clause |
| MCP `metrics ? 'backfill_y6_optimistic'` count | 0 (как и pre-count = 0) |

---

## 6. Risks / Follow-ups

| Риск | Mitigation |
|---|---|
| Prod backfill коснётся больше записей чем dev (legacy users) | Pre-deploy: повторить pre-count на prod-snapshot; если N >> dev — рассмотреть batched UPDATE с `LIMIT … RETURNING` (пока не нужно — N=0 в dev) |
| `CREATE INDEX` lock на больших prod-таблицах | Pre-deploy steps в OPERATOR_RUNBOOK §Y-7 — CONCURRENTLY вручную до alembic |
| Метрика `backfill_y6_optimistic` в `task_results.metrics` останется навсегда | Acceptable — служит audit-маркером; downgrade умеет её удалить |

---

## 7. Decision

**PASS.** M12 безопасна, idempotent, downgrade-able. Готова к Stage 1.

**Branched-blocker для Stage 1 нет** — переходим к submit_attempt_answers.
