# review-gate: tsk-1124 Ф2 + Ф3 — API групп и видимость слотов для ученика

**Решение: ПРИНЯТО** (независимый агент-рецензент; блокеров нет).

## Охват
Ф2: `/schedule-groups` (справочник, группы ученика), `group_id` у слота, фильтр
`GET /lesson-slots?group_id=`. Ф3: предикат `schedule_group_service.effective_group_ids`
+ `slot_visible` / `occurrence_visible` во всех путях ученика.

## Проверено рецензентом
- Пропущенных путей ученика нет: `schedule-slots` + join, `bookable` + join,
  `available-slots`, `reschedule`, ad-hoc ученика, `/me/teachers?at=`.
  `post_attendance` и `list_my_occurrences` работают по своим участиям — фильтр не нужен.
  Путь преподавателя (`require_scheduled_slot=False`) не ограничен — по спеку.
- `FOR UPDATE OF ls` с JOIN, отсутствие циклического импорта, откаты IntegrityError.
- Роли и отсутствие затенения маршрутов.

## Неблокирующие находки — исправлены до коммита
1. Выключенная группа держала ученика без слотов → `effective_group_ids` берёт только
   активные группы, иначе группа по умолчанию.
2. Гонка двух PUT групп ученика → `INSERT … ON CONFLICT DO NOTHING`.
3. GET групп несуществующего/не-ученика отдавал группу по умолчанию → проверка роли.
5. `free-slots` с неизвестной группой молча пуст → 404/422 через `ensure_active_group`.
6. Спек называл модуль `schedule_group_policy` → поправлено.

## Оставлено
4. N+1 в `list_bookable_occurrences_for_student` (список ≤30 кандидатов) — приемлемо.
7. Для взрослых сетка не действует — решение оператора 26.09 (один слот пт 12:00).

## Тесты
`tests/test_tsk1124_schedule_groups_api.py`, `tests/test_tsk1124_schedule_group_visibility.py`
(в т.ч. выключенная группа, разовое занятие без слота, свой слот чужой группы,
роли 403, неизвестная группа площадки, сторож копий условия) — 22 passed.
Межпроектная память: `lms-api.md` §«Группы расписания», `CHANGELOG.md`.
