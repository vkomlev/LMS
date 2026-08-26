# tsk-703 — список материалов курса отдавал ученику выключенные вместе с телом

**Дата:** 2026-08-26
**Задача:** tsk-703 (LMS)
**Файлы:** `app/api/v1/materials_extra.py`, `tests/test_materials_acl_y51.py`
**Diff:** `reviews/2026-08-26-tsk703-inactive-materials-course-list.diff`

## Контекст

`GET /api/v1/courses/{course_id}/materials` принимал `is_active: bool | None`
со смыслом «по умолчанию — все материалы, и активные, и выключенные»
(параметр заводился под кабинет методиста, tsk-559) и никак не ограничивал
непривилегированного вызывающего. Ученик, имеющий доступ к курсу, одним
запросом получал список выключенных материалов **вместе с телом**:
`MaterialRead` содержит поле `content` (`app/schemas/materials.py:81`).

Место в линии дефектов:

- tsk-695 — тот же класс для ОДНОГО материала (`GET /materials/{id}`);
- tsk-699 — тот же класс для списка ЗАДАНИЙ курса (`GET /tasks/by-course/{id}`),
  хвост про материалы был записан в его истории движения;
- tsk-703 (эта) — оптовая версия tsk-695 и близнец tsk-699 со стороны материалов.

Дополнительное отличие от tsk-699: `list_course_materials` **вообще не
использовал** возвращаемое значение `assert_course_access` (вызов без
присваивания) — признак привилегированности отбрасывался.

## DB Findings (MCP, read-only, прод 26.08.2026)

| Факт | Значение |
|---|---|
| Выключенных материалов | **458** из 3551 |
| Худший курс в дереве ученика 142 | курс **1064** «Трек 1. Создание IT-продуктов» — **64 выключенных из 65** |
| Другие заметные | 1253 (5 из 10), 155 (4 из 9), 148 (4 из 15), 138 (3 из 12) |

Курс 1064 взят как площадка для живой проверки: до правки ученик получал там
практически весь курс в виде снятого с публикации содержимого.

## Инвентарь потребителей (механически, из источника)

| Потребитель | Вызов | Привилегии | Затронут |
|---|---|---|---|
| SPW, программа курса ученика | `lib/learning/use-course-syllabus.ts:141` → `?is_active=true&limit=500` | ученик | нет (уже шлёт `true`) |
| SPW, кабинет методиста | `lib/methodist/use-methodist-content.ts:70,78` (`useCourseMaterials`), экраны `CourseDetail.tsx:92`, `MaterialDetail.tsx:37` | роль methodist/admin | нет (привилегированный) |
| ContentBackbone | `monolith/lms_client/client.py:289` `get_materials_by_course`, потребители — `lms_publish/lesson_publisher.py:487` (prune) и `content_lint/audit.py:145` | сервисный ключ | нет |
| TG_LMS, ученический экран | `src/bots/common/dialogs/student_courses_base.py:278` → `is_active=True` | сервисный ключ (и так `true`) | нет |
| TG_LMS, боты методиста и преподавателя | `src/bots/methodist/dialogs/materials.py:76`, `src/bots/teacher/dialogs/courses.py:145` | сервисный ключ | нет |
| LMS, внутренние вызовы | `materials_service.list_by_course` дёргается только из этого роутера (плюс внутренние `repo.list_by_course` в самом сервисе, мимо API) | — | нет |

Ни один потребитель не завязан на прежнее поведение «ученику отдаём всё».

## Code Changes

1. `app/api/v1/materials_extra.py` — добавлен `_active_filter_for(...)` (близнец
   одноимённой функции в `tasks_extra.py`, tsk-699): привилегированному —
   срез как просил, непривилегированному — принудительно `True`, включая явный
   `?is_active=false`; расхождение запроса и выдачи пишется в лог
   (`tsk-703: force is_active=true ...`).
2. Там же, `list_course_materials`: результат `assert_course_access` теперь
   присваивается в `privileged` и передаётся в фильтр; описание query-параметра
   `is_active` в OpenAPI отражает ограничение.
3. `tests/test_materials_acl_y51.py` — 6 сценариев (см. ниже).

Поля ответа не режутся: у материалов нет секрета уровня `solution_rules`
(см. комментарий в `search_materials`), поэтому правка — только фильтр среза,
без аналога `_task_read_for`.

## Validation Results

| Критерий | Результат |
|---|---|
| Ученик не видит выключенные в списке курса | PASS — `test_student_course_materials_list_hides_inactive` |
| Явный `?is_active=false` ученику не открывает дверь | PASS — `test_student_cannot_request_inactive_materials_explicitly` |
| `total` считается по тому же срезу (пагинация не врёт) | PASS — `test_student_course_materials_total_matches_visible` |
| Методист видит выключенные по-прежнему | PASS — `test_methodist_still_sees_inactive_in_course_list` |
| Преподаватель видит выключенные по-прежнему | PASS — `test_teacher_still_sees_inactive_in_course_list` |
| Сервисный ключ (CB prune, боты) видит выключенные | PASS — `test_service_key_still_sees_inactive_in_course_list` |
| Файл целиком | 19 passed (было 13) |
| Полный прогон pytest | PASS — 2832 passed, 12 skipped (14:47) |

**Тесты доказанно ловят дефект.** Временный откат поведения
(`_active_filter_for` → всегда `return requested`) роняет ровно два новых
теста: `..._list_hides_inactive` и `..._request_inactive_materials_explicitly`.
Файл после проверки восстановлен из копии.

## Живая проверка на проде

Порядок — как в `reviews/2026-08-26-tsk699-inactive-tasks-course-list.md` и
`reviews/2026-08-26-tsk697-inactive-task-direct-api.md`: после выката, в той же
сессии, под учеником 142 (курс **1064**: 64 выключенных из 65) плюс контроль
сервисным ключом. Результат дописывается сюда отдельной правкой.

## Risks / Follow-ups

- Риск сужения выдачи привилегированным — закрыт тремя тестами
  (methodist / teacher / сервисный ключ).
- `GET /courses/{course_id}/materials/stats` остаётся на legacy-гейте
  (`Depends(get_db)`, только сервисный ключ) и отдаёт агрегат `inactive` —
  ученику он недоступен, отдельного дефекта нет.
- Остальные ручки линии tsk-695…tsk-702 закрыты; гостевой и embed-контур —
  предмет tsk-702.
