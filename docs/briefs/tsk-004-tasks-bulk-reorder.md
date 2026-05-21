---
slug: tsk-004-tasks-bulk-reorder
title: Bulk reorder endpoint для tasks (этап 1.7, зеркало materials)
parent_task: tsk-004 (этап 1.7)
status: ready-for-implementation
created: 2026-05-21
updated: 2026-05-21
owner: eng-review → tech-spec-composer
review_skill: eng-review
context_levels: standard
related_files:
  - app/api/v1/tasks_extra.py
  - app/services/tasks_service.py
  - app/repos/tasks_repo.py
  - app/schemas/tasks.py
  - tests/test_tasks_reorder_api.py
  - docs/database-triggers-contract.md
authority_docs:
  - docs/briefs/tsk-004-tasks-order-position.md (фундаментальный бриф этапа 1.6)
  - app/api/v1/materials_extra.py:157-174 (шаблон router)
  - app/services/materials_service.py:80-110 (шаблон service)
  - app/repos/materials_repo.py:108-138 (шаблон repo)
  - app/schemas/materials.py:91-150 (шаблон Pydantic)
authority_review:
  - d:/Work/TG_LMS/docs/briefs/tsk-NNN-methodist-tasks-ordering.md (cross-project eng-review)
---

# Бриф: tsk-004 этап 1.7 — Bulk reorder endpoint для tasks

## Контекст и происхождение

Этап 1.6 фазы 1+2 (commits `1182d30`, `63bcddd` от 2026-05-21) добавили в LMS поле
`tasks.order_position`, триггеры PL/pgSQL, индекс `idx_tasks_course_order`, и проброс
`order_position` через `TaskCreate / TaskUpdate / TaskRead / TaskUpsertItem` + `PATCH
/api/v1/tasks/{task_id}` + `POST /api/v1/tasks/bulk-upsert`.

Этого достаточно для использования из Telegram-бота методиста через single-PATCH:
бот выбирает order_position соседа и шлёт PATCH — триггер атомарно сдвигает соседей.

Однако веб-версия рабочего места методиста (планируется) использует drag-and-drop
переупорядочивание заданий — это требует **одного атомарного запроса** на новый
порядок, а не N последовательных PATCH (где промежуточные состояния могут конфликтовать
и при обрыве оставить неконсистентный порядок).

Этот этап добавляет в LMS массовый reorder endpoint, **зеркальный** existing
`POST /api/v1/courses/{course_id}/materials/reorder`. Single-move endpoint
`POST /api/v1/tasks/{task_id}/move` **не добавляется** — PATCH+триггер семантически
эквивалентны, добавление symmetry-only endpoint = tech debt.

## Цель

Дать LMS endpoint `POST /api/v1/courses/{course_id}/tasks/reorder`, принимающий
полный (или частичный) новый порядок заданий курса в одной транзакции, для
последующего использования веб-фронтом методиста (drag-list) и любым другим
будущим API-consumer'ом.

## Не цели (out of scope этапа 1.7)

- UI-изменения в TG_LMS методиста (отдельная задача `tsk-NNN` в TG_LMS, depends_on этого этапа)
- Single-move endpoint `POST /tasks/{id}/move` — отказались (см. eng-review)
- Дрейф-учёт LE snapshot — уже покрыт LMS T25
- Изменение триггеров `trg_set_task_order_position` — этот endpoint их **выключает**
  через `set_config('app.skip_task_order_trigger', 'true', true)` так же, как materials.

## Ключевые решения (зафиксированы в eng-review + tech-spec-composer)

| ID | Решение | Источник |
|----|---------|----------|
| D1 | Только bulk reorder, без single-move | eng-review «доп. вопрос про web-future» |
| D2 | Зеркало materials архитектурно (router → service → repo → schemas), но **с улучшением валидации** | tech-spec-composer Q1=A |
| D3 | Валидация дубликатов task_id и order_position в сервисе → HTTP 422 | tech-spec-composer Q1=A |
| D4 | Partial reorder разрешён (зеркало materials) — можно прислать порядок только для подмножества заданий курса | по аналогии materials |
| D5 | Auth: без endpoint-level Depends — middleware-level X-API-Key (зеркало materials) | по аналогии materials |
| D6 | Атомарность через `set_config skip_trigger=true` + bulk UPDATE + `db.commit()` | зеркало `app/repos/materials_repo.py:108-138` |
| D7 | OpenAPI regen автоматический в CI/CD | tech-spec-composer Q2 |
| D8 | Cross-project mirror — обновить после merge: `contracts/lms-api.md` + CHANGELOG.md | стандарт cross-project-memory |

## Затронутые файлы

| Файл | Изменение |
|------|-----------|
| `app/schemas/tasks.py` | + 4 новых класса (TaskOrderItem, TaskReorderRequest, TaskOrderRead, TaskReorderResponse) |
| `app/api/v1/tasks_extra.py` | + 1 endpoint `reorder_course_tasks` |
| `app/services/tasks_service.py` | + 1 метод `reorder_tasks(db, course_id, task_orders)` |
| `app/repos/tasks_repo.py` | + 1 метод `reorder_tasks(db, course_id, task_orders)` |
| `tests/test_tasks_reorder_api.py` | новый файл — кейсы BR1-BR8 |
| `docs/database-triggers-contract.md` | + раздел 15 «tasks bulk reorder» |

ContentBackbone mirror (post-merge):
- `docs/cross-project/contracts/lms-api.md` — новый раздел
- `docs/cross-project/CHANGELOG.md` — запись `tsk-004 этап 1.7`

## Skill-pipeline

| Шаг | Skill |
|-----|-------|
| Эта спецификация | `/tech-spec-composer` (текущий) |
| Реализация | `/fastapi-api-developer` |
| Pre-merge DB-проверка | `/db-check` (read-only, проверка триггер-сессии и `set_config('app.skip_task_order_trigger')`) |
| Pre-merge code review | `/techlead-code-reviewer` (валидация, contracts, race-conditions) |
| Финальный gate | `/review-gate` (PASS/FAIL) |
| Cross-project mirror | post-merge: Edit ContentBackbone |

## Связанные документы

- ТЗ этапа: `docs/specs/2026-05-21-tz-tasks-bulk-reorder-stage1-7.md` (создаётся сейчас)
- Фундаментальный бриф 1.6: `docs/briefs/tsk-004-tasks-order-position.md`
- Cross-project eng-review: `D:\Work\TG_LMS\docs\briefs\tsk-NNN-methodist-tasks-ordering.md`
- Шаблон materials reorder: `app/api/v1/materials_extra.py:157-174`
