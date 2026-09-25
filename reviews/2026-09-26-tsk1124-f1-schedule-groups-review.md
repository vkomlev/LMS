# review-gate: tsk-1124 Ф1 — схема групп расписания

**Решение: ПРИНЯТО** (при зелёном полном прогоне pytest).

Файлы: `app/db/migrations/versions/2026_09_26_tsk1124_schedule_groups.py`,
`app/models/schedule_group.py`, `app/models/lesson_slot.py`, `app/db/base.py`,
`tests/test_tsk1124_schedule_groups_schema.py`.

## Проверено
- Соответствие спеку: таблицы, группа у слота NOT NULL, засев двух групп, разметка
  всех слотов в «Дети · Информатика» (решение оператора 25.09). Фильтрации нет — это Ф3.
- Миграция: upgrade / downgrade / upgrade на dev чисто; одна голова. Засев в миграции,
  строки ищутся по коду плана, не по id (ERRORS 2026-09-04).
- Прод до записи (MCP, read-only): head `tsk1088_adults_static_price`, 41 слот
  (24 активных), `teacher_id` NULL нет, триггеров на `lesson_slot` нет, функции с
  таким именем нет, план `adults` → pricing_group 9.
- Совместимость: ~20 тестов и сервисов вставляют слот сырым SQL без группы —
  покрыто триггером; ORM перечитывает значение (`FetchedValue`). Тесты на оба пути.
- Модель ↔ миграция: имена индексов выровнены (находка ревью, исправлена).
- API не менялся — openapi не затронут. Межпроектная память обновлена
  (`lms-db-schema.md`, `CHANGELOG.md`).

## Не блокирует
- `schedule_group.updated_at` не обновляется триггером — появится с CRUD в Ф2.
