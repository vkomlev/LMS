# Subsystem A Phase 1: materials bulk-upsert API

**Дата:** 2026-03-23  
**Статус:** `DB uniqueness confirmed` | `endpoint live in swagger` | `idempotent create/update confirmed`

## DB contract (подтверждено до реализации)

- Уникальность `(course_id, external_uid)`: constraint `uq_materials_course_external_uid` в `app/models/materials.py` (миграция materials structure).

## Contract summary (handoff для ContentBackbone)

### Idempotency key

- Пара **`(course_id, external_uid)`** (`external_uid` обязателен, непустая строка после trim).

### Request

- `POST /api/v1/materials/bulk-upsert?api_key=...`
- Body: `MaterialsBulkUpsertRequest`
  - `items`: список `MaterialsBulkUpsertItem` (1…2000)
  - Поля элемента: `course_id`, `external_uid`, `title`, `type`, `content` (dict), опционально `description`, `caption`, `is_active` (bool, default `true`), `order_position`
  - Валидация `content` — `validate_material_content` из `app/schemas/material_content.py` (как в CRUD)
- Дубликаты одного ключа в одном batch: **последний по порядку в `items` выигрывает** (детерминированно).

### Response

- `MaterialsBulkUpsertResponse`: `processed`, `created`, `updated`, `unchanged`, `items[]`, `errors[]` (по умолчанию пусто)
- Элемент `items[]`: `course_id`, `external_uid`, `status` ∈ `created` | `updated` | `unchanged` | `error`, опционально `material_id`, `error`, `error_type` ∈ `validation` | `runtime` | `external`

### Status codes

- **200** — batch обработан (ошибки по строкам в `items[].status=error`, без обязательного rollback всего batch)
- **403** — нет/неверный `api_key` (через `Depends(get_db)`)
- **422** — ошибка валидации тела (Pydantic)

### Реализация

- Не использует `POST /materials/import/google-sheets`.
- Роут объявлен **до** `/materials/{material_id}/...`, чтобы путь `bulk-upsert` не конфликтовал с int-параметром.

## Validation (локально)

- `python -m pytest tests/test_materials_bulk_upsert.py -v` — OK
- `python -m pytest tests/ -k material -v` — OK

## OpenAPI

- Путь `/api/v1/materials/bulk-upsert` присутствует в `app.openapi()` (см. тест).

## Diff

Полный diff: [2026-03-23-subsystem-a-phase1-materials-bulk-upsert.diff](2026-03-23-subsystem-a-phase1-materials-bulk-upsert.diff)
