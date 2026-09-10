# tsk-886 — курс вне работы: признак наружу, скрытие в формах, запрет новых связей

**Дата:** 2026-09-10 · **Проекты:** LMS + SPW · **Трекер:** `tsk-886`
**Артефакт diff:** [2026-09-10-tsk886-kurs-vne-raboty.diff](2026-09-10-tsk886-kurs-vne-raboty.diff)

## Контекст

`courses.is_active` завёл tsk-873 (boolean NOT NULL, default `true`, уже на
проде), но **поведения за признаком не было никакого**: выключенный курс так же
предлагался в формах, принимал зачисление и попадал в подбор домашней работы.
Выключенных курсов на 10.09 — **ноль из 843** (сверено read-only по проду), то
есть поведение меняется, ничего не ломая.

## Что сделано

### 1. Признак наружу
- `CourseRead.is_active: bool` — новое поле у каждого ответа с курсом.
- `CourseCardPatch.is_active: bool | None` — `PATCH /courses/{id}/card`.
- `docs/openapi.json` пересобран, типы SPW перегенерированы.
- `is_exam` / `is_service` не тронуты — граница задачи.

### 2. Один страж на все пути
`app/services/course_activity_service.py` (новый):
`inactive_among`, `load_inactive_course_ids`, `assert_courses_active`,
`assert_course_active_for_student`. Предикат ОДИН и зовётся во всех точках —
не копируется. Роль владельца берётся у соседнего стража
(`tasks_acl_service._user_has_extended_role`), четвёртой копии запроса нет.

### 3. Отказ 409 там, где появляется НОВАЯ связь
| Путь | Место гейта |
|---|---|
| `POST /user-courses/` | `UserCoursesService.create` — после отсечения дубля, до доназначения зависимостей |
| `POST /users/{id}/courses/bulk` | `bulk_assign_courses` — только по курсам, которых у ученика ещё нет |
| `POST /teacher/students/{id}/assignments` | `assign_course_to_student` — внутри ветки «связи ещё нет» |
| `POST /courses/{cid}/teachers/{tid}`, `POST /teacher-courses/` | `TeacherCoursesService.add_link` — только если связи ещё нет |
| `POST /attempts`, `POST /learning/tasks/{id}/start-or-get-attempt` | оба узла: курс задания и корень навигации |

### 4. Отбор в формах ВЫБОРА
- `GET /teacher/courses/search` — `only_active=True` (селектор «Назначить курс»).
- `homework_service._next_items` — выключенные курсы обходятся на КАЖДОМ узле.
- SPW `RootCoursePicker` — отбор на клиенте (тот же список кормит дерево, где
  выключенный курс обязан остаться видимым).

### 5. Экран методиста (SPW)
Переключатель «Курс в работе» на карточке с пояснением последствий; признак
«вне работы» в строке дерева (`CourseRow`) и в заголовке курса.

## Проверки по критериям приёмки

| Критерий | Результат |
|---|---|
| Признак читается и правится с экрана | PASS — `test_course_read_exposes_is_active`, `test_patch_card_toggles_is_active`, 3 теста vitest |
| `is_exam` / `is_service` не правятся карточкой | PASS — `test_patch_card_does_not_touch_exam_and_service` |
| Зачисление отказывает (3 пути) | PASS — `test_enroll_denied…`, `test_bulk_enroll_denied…`, `test_teacher_manual_assign_denied` |
| Закрепление преподавателя отказывает | PASS — `test_teacher_course_link_denied` (409, не 404) |
| Попытка не начинается (2 пути) | PASS — `test_start_attempt_denied…`, `test_create_attempt_endpoint_denied` |
| Уже начатое не тронуто | PASS — `test_started_attempt_survives`, `test_teacher_manual_assign_idempotent_on_enrolled`, `test_reading_existing_enrollment_still_works` |
| Выключенный курс не в подборе ДЗ | PASS — `test_homework_pick_skips_inactive_course` |
| Выключенный курс не в форме выбора | PASS — `test_teacher_course_search_hides_inactive` + vitest на `RootCoursePicker` |
| Возврат в работу снимает всё | PASS — `test_reactivation_restores_everything` |
| Соседние пути не задеты | PASS — `test_bulk_enroll_passes_for_active_courses`, `test_teacher_course_link_allowed_on_active`, `test_start_attempt_allowed_on_active_course` |

## Прогоны

- `pytest tests/test_course_inactive_tsk886.py` — **18 passed**.
- `vitest` целиком — **213 файлов / 1783 теста passed**; `tsc --noEmit` чисто.
- `pytest` целиком — **зелёный, двумя последовательными проходами** (машина в
  этот день тормозила: убитые прогоны бросили 9 своих баз по 406 МБ и забили
  диск C до 8 ГБ; после уборки скорость восстановилась):
  - проход 1 — весь набор по порядку, дошёл до 90% (~2170 тестов): только
    точки и 7 пропусков, ни одного падения;
  - проход 2 — `tests/test_t*.py … test_y*.py` целиком (это основная масса
    набора, все файлы `test_tsk*`): **2040 passed, 12 skipped**, падений нет.
  - объединение проходов покрывает набор целиком: файлы до буквы «t» лежат
    внутри первых 90% прохода 1, остальное закрыто проходом 2.

## Риски и хвосты

- **Автоназначение курса-зависимости не гейтится.** `ensure_dependencies_assigned`
  идёт по графу, а не по выбору человека; если выключить курс, который является
  зависимостью включённого, зависимость всё равно доназначится. Альтернатива
  (пропускать) заперла бы ученика навсегда: замок снимается только по
  `COMPLETED` зависимости. Оставлено сознательно, зафиксировано здесь.
- **Правка структуры графа не гейтится** — привязка родителя/подкурса остаётся
  инструментом методиста (иначе выведенный курс нельзя вынести из программы).
- **Каскада нет:** выключение корня не выключает подкурсы (граница tsk-873).
- Ничего на проде не выключено — разметка остаётся решением оператора.
