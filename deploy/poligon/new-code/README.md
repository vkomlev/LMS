# Новый код ветки `poligon` — LMS backend

Эти файлы НЕ являются частью `main` и не должны туда попадать. Применяются
один раз при создании ветки `poligon` (`git checkout -b poligon` от актуального
`main`, затем скопировать файлы отсюда на их целевые пути и закоммитить в
`poligon`).

Полное описание каждого дефекта — `docs/qa-poligon/defect-registry.md`
(закрытый реестр, остаётся в `main`, НЕ копируется в `poligon`).

## Куда копировать

| Файл здесь | Целевой путь в ветке `poligon` |
|---|---|
| `migration_poligon_tables.py` | `app/db/migrations/versions/<timestamp>_poligon_tables.py` (переименовать по конвенции Alembic, проставить `down_revision` на актуальный head ветки) |
| `poligon_schemas.py` | `app/schemas/poligon.py` |
| `poligon_router.py` | `app/api/v1/poligon.py` |
| — | В `app/api/main.py` ветки `poligon` добавить `app.include_router(poligon_router, prefix="/api/v1")` (единственная правка существующего файла в LMS-части) |

## Перед реальным деплоем — обязательно

- Свериться с `docs/qa-poligon/defect-registry.md` построчно (там — источник
  истины по формулировкам, здесь — рабочий скелет кода).
- Прогнать `alembic upgrade head` на dev-tier локально/на VDS перед test/stage.
- `/review-gate` — как и для любого кода перед интеграцией (здесь —
  интеграцией в `poligon`, не в `main`, но тот же принцип: не разворачивать
  непроверенный код на публично доступный, пусть и учебный, сервер).
