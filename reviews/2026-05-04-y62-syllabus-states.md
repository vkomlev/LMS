# Y-6.2 LMS-side: GET /me/courses/{course_id}/syllabus-states

**Дата:** 2026-05-04
**Phase:** Y-6.2 (LMS-side)
**Тип:** feature (NEW endpoint)
**Spec:** user task spec (architect-system-analyst handoff 2026-05-04)
**Cross-project mirror:** `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md` §«GET /api/v1/me/courses/{course_id}/syllabus-states (Y-6.2 NEW)»

## Цель правки

Дать SPW (Y-6.2 frontend, отдельная сессия) батч-endpoint для рендера syllabus-дерева курса:
sectioned-list с 3+ sticky-headers, smart-collapse, бейджами 6 task-статусов
(passed / pending_review / failed / blocked_limit / in_progress / not_started),
material completed/not_started, 🔒 на subcourse-узлах с непройденными `course_dependencies`.

Без этого endpoint SPW делал бы N+1 fetch'ей (per task: `compute_task_state`-like
запрос; per material: `student_material_progress` lookup) — несовместимо с aggressive
invalidate `["learning","syllabus-states", course_id]` после каждого submit.

## Затронутые файлы и контракты

| Файл | Изменение |
|---|---|
| `app/schemas/me.py` | NEW: `SyllabusTaskItem` / `SyllabusMaterialItem` (discriminated по `kind`) / `SyllabusStatesResponse` |
| `app/services/me_service.py` | NEW: `get_syllabus_states()` + 3 SQL CTE (`_SYLLABUS_TASKS_SQL` / `_SYLLABUS_MATERIALS_SQL` / `_BLOCKED_COURSES_SQL`) + helper `_compute_syllabus_task_status` |
| `app/api/v1/me.py` | NEW endpoint `GET /courses/{course_id}/syllabus-states` (auth + ACL + `Cache-Control: private, max-age=15`) |
| `docs/openapi.json` | regenerated (159 paths) |
| `tests/test_y62_syllabus_states.py` | NEW: 16 тестов |
| (cross-project) `contracts/lms-api.md` | NEW endpoint section + Last verified bump |
| (cross-project) `CHANGELOG.md` | NEW entry в начало |
| (cross-project) `STATE.md` | LMS phase + Y-6.2 endpoint в списке |

### Endpoint contract

- **Auth:** `Depends(require_authenticated)` (cookie / Bearer / X-API-Key) — 401 без auth
- **ACL:** `assert_course_access` (тот же helper что `/me/courses`, `/tasks/by-course/{id}`):
  service-key + admin/methodist/teacher — bypass; student — только если `course_id ∈ recursive(user_courses + course_parents)`. Иначе 403.
- **Path:** `course_id` — корневой узел поддерева (любой узел иерархии).
- **Cache:** `Cache-Control: private, max-age=15`.
- **Response 200 body:** см. spec / contracts mirror.

### Status mapping (per task)

```
passed         ← last is_correct=TRUE + checked_at NOT NULL
pending_review ← last is_correct=TRUE + checked_at NULL (Y-6 optimistic) | legacy is_correct IS NULL
failed         ← last is_correct=FALSE + attempts_used < attempts_limit_effective
blocked_limit  ← last is_correct=FALSE + attempts_used >= attempts_limit_effective
in_progress    ← no task_result + open course-level attempt в task.course_id
not_started    ← no task_result + no open attempt
```

`attempts_limit_effective = COALESCE(student_task_limit_override, tasks.max_attempts, DEFAULT_MAX_ATTEMPTS=3)`.

`last_per_task` использует `INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL` —
парность с `learning_engine_service.compute_task_state` (Y-5.3 fix).

### Edge max_score=0

`is_correct=TRUE + checked_at NOT NULL → passed`, независимо от `max_score`. Auto-check
может пройти задачу без баллов; правило по флагу, не по ratio (отличается от engine
`compute_task_state` где ratio-based BLOCKED_LIMIT detection).

## Результаты валидации

### Unit/integration tests (новые 16/16 PASS)

```
$ python -m pytest tests/test_y62_syllabus_states.py -x --tb=short

tests/test_y62_syllabus_states.py ................                       [100%]
====================== 16 passed, 11 warnings in 20.53s =======================
```

Покрытие:
- `test_syllabus_states_requires_auth` — 401 без auth
- `test_syllabus_states_403_for_unenrolled_student` — student без enrollment
- `test_syllabus_states_hierarchical_acl_enrolled_in_parent` — student → parent → grandchild
- `test_syllabus_states_teacher_bypass` — extended-role bypass
- `test_syllabus_states_cache_header` — Cache-Control: private, max-age=15
- `test_status_passed_checked` — status=passed
- `test_status_pending_review_optimistic` — status=pending_review (Y-6 optimistic)
- `test_status_failed_with_attempts_left` — status=failed (override limit=5)
- `test_status_blocked_limit` — status=blocked_limit (override limit=2, 2 неудачные попытки)
- `test_status_in_progress_open_attempt` — status=in_progress
- `test_status_not_started_default` — status=not_started
- `test_status_passed_edge_max_score_zero` — edge max_score=0 + is_correct=TRUE → passed
- `test_material_completed_and_not_started` — material 2 кейса
- `test_blocked_courses_via_dependencies` — blocked_courses содержит child с unmet dep
- `test_blocked_courses_unblocks_after_prerequisite_completed` — пустой если dep COMPLETED
- `test_perf_smoke_50_tasks` — root с >=50 задач за <2s

### Регрессия (Y-3 + Y-4 + Y-6 + Y-4.1 ACL)

```
$ python -m pytest tests/test_me_endpoints_y3.py tests/test_me_history_y4.py \
    tests/test_y6_review_loop.py tests/test_acl_hierarchical_y41.py --tb=short
============ 1 failed, 34 passed, 1 skipped, 11 warnings in 37.78s ============
```

1 fail — `test_acl_hierarchical_y41.py::test_teacher_self_attached_root_with_root_task_still_works`,
**pre-existing flake**: воспроизводится на чистом `main` без правок (verified через `git stash`+rerun).
Не вызвано данным изменением.

### URL-guard

```
$ grep -rE "https?://(learn|api|tg)\.victor-komlev\.ru|https?://localhost:[0-9]+" \
    app/api/v1/me.py app/services/me_service.py app/schemas/me.py
exit=1 (no matches)
```

### Smoke imports + route registered

```
me ok / service ok / schemas ok
/api/v1/me/courses/{course_id}/syllabus-states {'GET'}
```

### openapi.json regen

```
openapi.json regenerated: 159 paths
syllabus path present: True
```

## Date/Type Guard Evidence

В сервисе все datetime поля (`last_submitted_at`, `completed_at`) приходят из БД
напрямую (Postgres `timestamptz`); никаких `text(...)` сравнений с `str` нет.
Pydantic схема (`SyllabusTaskItem.last_submitted_at: datetime | None`) обеспечивает
type-safe сериализацию в ISO8601.

`attempts_limit_effective` приведён к `int` через `int(t["attempts_limit_effective"] or 0)`
— защита от `None` если `tasks.max_attempts IS NULL` AND нет override (резолвится через
`COALESCE(... :default_max)` в SQL).

## Risks / Follow-ups

- **(low)** TG_LMS / другие consumer'ы не затронуты — endpoint новый, никого не ломает.
- **(low)** Тесты используют реальные курсы из dev DB (`_pick_root_course`,
  `_pick_root_with_grandchild`) — паттерн `test_acl_hierarchical_y41`. На чистой DB
  без data — тесты `pytest.skip()`.
- **(low)** SPW Y-6.2 frontend ещё не реализован (отдельная сессия). LMS endpoint
  готов и cross-project mirror обновлён — frontend агент сможет работать против
  contracts/lms-api.md без читания LMS spec.
- **No-op для existing clients:** SPW Stage 6 / TG_LMS Y-6 / mobile / WP — не вызывают
  этот endpoint.

## Rollback

```powershell
git revert HEAD                                           # LMS commit
cd D:\Work\ContentBackbone; git revert HEAD               # cross-project commit
```

Endpoint отдельный, удаление regenerates openapi.json, других regression нет.
