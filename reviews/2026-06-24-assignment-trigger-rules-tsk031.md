# Review — Триггеры назначения курсов (tsk-031)

- **Дата:** 2026-06-24
- **Задача:** tsk-031 «Доп курсы для закрепления»
- **ADR:** [docs/ai/adr/0002-course-assignment-trigger-rules.md](../docs/ai/adr/0002-course-assignment-trigger-rules.md)
- **Diff:** [2026-06-24-assignment-trigger-rules-tsk031.diff](2026-06-24-assignment-trigger-rules-tsk031.diff)

## Контекст

LMS не умела автоматически назначать ученику курсы по условиям прохождения.
Заложен фундамент runtime-слоя назначения поверх графовой модели курсов
(`course_uid = wp:<slug>`, совпадает с публикатором ContentBackbone):
- автоназначение по правилам (ответ на вопрос / провал темы);
- ручное назначение учителем в один клик.

## Changed Files

| Файл | Что |
|---|---|
| `app/db/migrations/versions/20260624_010000_tsk031_assignment_rules.py` | Миграция: таблицы `assignment_rule` + `assignment_event` |
| `app/models/assignment_rule.py`, `app/models/assignment_event.py` | ORM-модели |
| `app/db/base.py` | Регистрация моделей в метаданных |
| `app/schemas/assignment_rules.py` | Pydantic: ManualAssign{Request,Response}, AssignmentEventRead |
| `app/services/assignment_rules_service.py` | Идемпотентное ядро + движок оценки правил (3 типа триггера) |
| `app/api/v1/teacher_assignments.py` | Эндпоинт `POST /teacher/students/{id}/assignments` (teacher-only) |
| `app/api/main.py` | Регистрация роутера |
| `app/api/v1/attempts.py` | 2 хука движка (после ответа / после завершения попытки), soft-fail |
| `docs/ai/adr/0002-*.md` | ADR проектного решения |
| `tests/test_assignment_rules_tsk031.py` | 7 тестов: ядро, движок, эндпоинт |

## Validation Commands

```
.venv/Scripts/python.exe -m alembic upgrade head        # миграция применена
.venv/Scripts/python.exe -m alembic downgrade -1 && ... upgrade head   # обратима
.venv/Scripts/python.exe -m pytest tests/test_assignment_rules_tsk031.py -q   # 7 passed
.venv/Scripts/python.exe -m pytest tests/test_attempts_integration_stage4.py tests/test_attempts_answers_comment.py -q   # 3 passed (регрессия)
```

## DB Findings

- Две новые таблицы созданы, миграция down/up проходит чисто (transactional DDL).
- Идемпотентность зачисления опирается на существующий PK `user_courses(user_id, course_id)`.
- `order_number` ставит существующий триггер БД (не дублируем в коде).

## Risks / Follow-ups

- **По умолчанию no-op:** таблица `assignment_rule` пуста → хуки не меняют поведение.
  Подтверждено регрессией существующих тестов попыток.
- **Движок синхронный** в request-цикле, soft-fail. При росте нагрузки — вынести в воркер
  поверх `learning_events` (зафиксировано в ADR).
- **Следующий шаг:** UI-кнопка в SPW / teacher API; админка правил; `course_failed`
  по накопленной истории, а не только в рамках одной попытки.
- **Cross-project:** обновлены `contracts/lms-api.md`, `lms-db-schema.md`, `CHANGELOG.md` в ContentBackbone.
