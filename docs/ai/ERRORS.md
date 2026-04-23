# Журнал ошибок AI-контуров

## Как использовать
1. Добавляйте запись при каждом значимом промахе/сбое.
2. Заполняйте `Класс ошибки` и `Серьезность`.
3. На еженедельном разборе переносите профилактику в правила/skills/workflows.

## Классы ошибок
- `SPEC`, `CONTEXT`, `LOGIC`, `INTEGRATION`, `DATA`, `TEST`, `SAFETY`, `COST`, `PROCESS`

## Серьезность
- `S1` критично
- `S2` высоко
- `S3` средне
- `S4` низко

## Шаблон записи
| Дата | Проект | Контекст | Симптом | Корневая причина | Класс | Severity | Как обнаружено | Исправление | Профилактика | Статус |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-02-27 | LMS | <task-context> | <symptom> | <root-cause> | LOGIC | S2 | smoke test | <fix> | <prevention> | done |
| 2026-03-03 | LMS | teacher help request detail | 500: `TypeError '<' not supported between 'str' and 'datetime.datetime'` | raw SQL/text date value compared with `now` without normalization/type-guard | DATA | S1 | runtime manual test + logs | normalize date via helper before compare; add service type-guards | update Cursor fastapi agents (normalization + negative tests + type-guards), add FastAPI-specific techlead review checks (raw SQL->types->now, runtime smoke detail/list, reproducer test) | done |

## Чеклист weekly review
- Выгрузить все `open` + `in_progress`.
- Отдельно разобрать `S1/S2`.
- Обновить минимум 1 артефакт процесса (rule/skill/workflow) на повторяющиеся причины.
