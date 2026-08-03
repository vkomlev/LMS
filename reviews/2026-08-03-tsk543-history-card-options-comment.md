# tsk-543 — карточка истории задания: текст вариантов MC/SC (backend)

## Контекст
Задача [[tsk-543]] (`D:\Work\Root\tasks\tsk-543-...md`): карточка истории
задания (`SPW/components/task-history/TaskHistoryCard.tsx`) показывала только
ID варианта MC/SC (обычно A/B/C), не его текст. Backend-пробел: схема
`TaskHistoryResponse` не содержала текстов вариантов вообще, только
`correct_option_ids` в `solution` (а у ученика `solution` всегда `null`, поэтому
без backend-фикса расшифровать даже СВОЙ выбор невозможно).

## Изменения
- `app/schemas/task_history.py`: новая модель `TaskHistoryOption {id, text}`,
  поле `TaskHistoryTaskInfo.options: Optional[List[TaskHistoryOption]]`. Живёт
  в `task`, не в `solution` — solution ученику не собирается вовсе, а
  расшифровка своего выбора нужна и без него.
- `app/services/task_history_service.py`: `_task_options()` — только для
  `type in ("SC", "MC")`, только активные варианты (`is_active`, паттерн
  `embed_api.py:214-220`), без `explanation` (пояснение методиста — почти
  answer leak на ученической ветке). Заполняется в обеих ветках
  `build_task_history` (ученик/учитель) — единый источник для обеих сторон
  карточки.
- `tests/test_task_history_tsk349.py`: новый MC-таск с одним неактивным
  вариантом в fixture `hgraph`; тесты — учитель и ученик получают
  `task.options` с текстом активных вариантов, скрытый вариант исключён;
  явная проверка, что SA_COM-таск (без options) отдаёт `options: null`.

## DB Findings
Не требовалось — `task_content.options` уже существующее поле, только чтение.

## Validation
- `pytest -q tests/test_task_history_tsk349.py` — 10 passed.
- `pytest -q` (весь backend) — 1541 passed, 11 skipped (было ~1539 до правки,
  +2 новых теста).
- `docs/openapi.json` перегенерирован (`scripts/export_openapi.py`) —
  `TaskHistoryOption` + `TaskHistoryTaskInfo.options` в контракте.

## Risks / Follow-ups
- Фронтенд SPW — отдельный коммит той же сессии (`SPW/reviews/2026-08-03-tsk543-*`).
- Живая проверка на проде — после деплоя обеих сторон, в этой же сессии.
