# tsk-998 (П9): контрольная точка «Проверь себя» внутри материала — LMS

Постановка: `docs/briefs/2026-10-16-p6-p9-p10-review-sla-checkpoint-reflection.md`, фаза C, шаги 13–14.
Ревью: `reviews/2026-10-18-tsk998-material-checkpoint-review.md` — ПРИНЯТО. Диф — `2026-10-18-tsk998-material-checkpoint.diff`.

## Что сделано

- `app/schemas/material_content.py`: `MaterialCheckpoint`, `TextContent.checkpoints` (без точек ключа нет — content прежний).
- `app/services/material_checkpoints_service.py`: проекция для ученика (без `answer`/`explanation`; рубильник
  выкл. — без `checkpoints`), проверка ответа, событие `learning_events.material_checkpoint` с `first_attempt`.
- `app/api/v1/materials_extra.py`: проекция в `GET /materials/{id}` и `GET /courses/{id}/materials` для непривилегированных.
- `app/api/v1/learning.py`: `POST /learning/materials/{id}/checkpoint/{cp_id}`.
- `MATERIAL_CHECKPOINTS_ENABLED` (по умолчанию false), `.env.example`, `docs/api-reference.md`, `docs/openapi.json`.
- `unseen_constructs_service`: скрытые ответы точек не считаются теорией.
- Миграций нет; голова Alembic `tsk999_help_reflection`.

## Проверки

- `tests/test_tsk998_material_checkpoints.py` — 30 passed.
- Полный `pytest` в облаке (пустая база + справочники ролей/сложности): ветка — 4345 passed, 55 failed, 17 errors;
  main — 4315 passed, те же 55 failed и 17 errors (сверка списков — расхождений нет).
