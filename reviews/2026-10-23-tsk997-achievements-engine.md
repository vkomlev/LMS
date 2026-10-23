# tsk-997, этап 2 — движок условий достижений и 6 учебных достижений

Diff: `reviews/2026-10-23-tsk997-achievements-engine.diff` (staged, только куски tsk-997;
в `app/core/config.py` и `app/api/main.py` рядом лежат незакоммиченные куски tsk-1254/1255 — не включены).

## Что сделано
- `app/services/achievements_engine.py` — реестр «тип условия → число», `is_earned`/`progress_of`,
  `load_learning_facts` (6 запросов на набор учеников). Незнакомый/битый condition → `False` + лог.
- `retention_service` — `_is_earned`/`_progress_of` стали обёртками над движком; экран удержания и
  родительская сводка фильтруют каталог по типам удержания; `award_pending` (тот же тик tsk-032)
  фиксирует и учебные достижения.
- Миграция `tsk997_learning_ach` (down `tsk1403_materials_placement`): 12 строк каталога,
  `reward_points = 0`, `ON CONFLICT (name) DO NOTHING`. «Закрыл модуль» — ступенями 1/3/10
  (PK `user_achievements` не даёт повторной выдачи — отклонение от плана этапа 1).
- `GET /me/achievements`, `GET /teacher/students/{id}/achievements` за рубильниками
  `ACHIEVEMENTS_STUDENT_UI_ENABLED` / `ACHIEVEMENTS_TEACHER_UI_ENABLED` (false по умолчанию →
  `{enabled:false, earned:[], next:[]}`). Преподаватель — ACL `ensure_can_edit_progress`.
- SPW: `lib/achievements.ts`, `AchievementsCard` на `/me/progress`, `StudentAchievements` в карточке
  ученика; при `enabled:false` не рисуются.

## Валидация
- `pytest tests/test_tsk997_achievements.py tests/test_tsk032_retention.py` → `27 passed`.
- dev: `alembic upgrade` → в `achievements` 21 строка (9 + 12).
- прод, read-only: имена каталога не пересекаются (9 вех удержания); EXPLAIN самого тяжёлого
  запроса (`fixed_after_error` по всей школе) — 60 мс, 126 учеников получат «Довёл до верного»
  задним числом (без уведомлений).
- SPW: `tsc --noEmit`, `eslint` — чисто.

## Review-gate: ПРИНЯТО
Без блокировки: тип ответа в SPW описан руками (общий `api-types.ts` правят соседи) — заменить
при следующей регенерации. Персональные послабления обязательности в «Закрыл модуль» не учтены —
правило строже, не мягче.

## Откат
`alembic downgrade` на `tsk1403_materials_placement` (удалит каталог и выданные учебные записи) +
revert коммита. Показ выключается рубильниками без выката.
