# tsk-894 — выпускник (alumni) не заводится в новое место ПОСЛЕ перевода

## Контекст

Слова оператора 2026-09-10, попутно с tsk-891: «выпускники много где остаются,
например, их можно добавить на занятие». `graduation_service` (tsk-673)
чистит расписание и доступ ОДНИМ событием — в момент перевода. Он не
проверяет ничего на будущих операциях записи, поэтому выпускника можно было
завести обратно в любое место для действующих учеников уже ПОСЛЕ перехода.

Архитектура — прямая копия приёма tsk-886 (`course_activity_service`): один
общий страж, отказ ровно там, где создаётся НОВАЯ связь, чтение и уже
начатое не трогаем. Решение оператора (уточнено вопросом до реализации):
жёсткий запрет 409 везде, без исключений ни для сотрудника, ни для
самозаписи ученика.

## Инвентарь мест записи (механически по коду, grep) и что сделано

Новый файл `app/services/alumni_enrollment_guard.py` — `is_alumni()` /
`assert_not_alumni()`, код тарифа берётся из
`graduation_service.ALUMNI_PLAN_CODE` (единственный источник).

Семь точек создания новой связи ученик↔слот/occurrence/курс:

1. `lesson_calendar_service._attach_student_to_slot` — единый choke-point
   для `add_slot_participant` (методист вручную + самозапись ученика через
   `schedule_booking_service.join_slot`, там уже стоит независимый гейт
   `is_audience` — 403 раньше моего 409, поведение не менялось) и
   `transfer_slot_participant` (перевод между слотами), а также
   auto-assign в `schedule_plan_service.py`.
2. `lesson_occurrence_service.create_ad_hoc_occurrence` (через
   `_seat_student_at`) — ученик сам записывается на отработку
   (`POST /lesson-occurrences/ad-hoc`) И преподаватель добавляет вручную
   (`POST /teacher/lesson-occurrences/add-student`). Раньше НИЧЕМ не
   гейтился — реальный пробел, не дубль.
3. `lesson_occurrence_service.add_participant_to_occurrence` — преподаватель
   подключает к уже идущему occurrence. Тоже не гейтился раньше.
4. `lesson_occurrence_service.join_occurrence_as_student` — ученик сам
   присоединяется к уже существующему occurrence. Тоже не гейтился раньше.
5. `user_courses_service.UserCoursesService.create` — одиночное зачисление
   (`POST /user-courses/`).
6. `user_courses_service.UserCoursesService.bulk_assign_courses` —
   пакетное зачисление (`POST /users/{id}/courses/bulk`); отказ только если
   пачка реально что-то добавит (существующие связи не трогаем).
7. `assignment_rules_service.assign_course_to_student` — ручное назначение
   учителем (`source=manual_teacher`) и автоправило (`source=auto_rule`);
   мягкий провал автоправила уже стоял под `except Exception` (tsk-886),
   новое исключение ловится тем же образом.

Все семь мест уже гейтили `courses.is_active` (tsk-886) ИЛИ не гейтили
ничего (occurrence-пути 2–4) — alumni-проверка добавлена рядом, тем же
приёмом (проверка ПОСЛЕ отсечения идемпотентного повтора, ДО фактической
записи).

## Что НЕ тронуто (осознанно)

- `course_dependencies_service._enroll_existing_students` (доназначение
  зависимости уже зачисленным студентам при добавлении новой зависимости к
  курсу методистом) — отдельный batch-путь через
  `ensure_dependencies_assigned` напрямую, МИМО `assign_course_to_student`.
  tsk-886 тоже не гейтил этот путь для `is_active` — оставлено в паритете с
  прецедентом, не расширяю охват задачи произвольно.
- `graduation_service.assert_course_work_allowed` (tsk-673) — работа УЖЕ
  назначенного курса (начать попытку/отправить ответ) гейтится ДРУГИМ,
  существующим стражем по признаку `subscription_plan.course_work`. Здесь не
  дублируется.

## Тесты

`tests/test_tsk894_alumni_reentry_guard.py` — 15 тестов: юнит-уровень гейта
(3) + все семь HTTP-точек денаем для выпускника (409, ALUMNI_DETAIL, ничего
не записалось) + регресс для обычного ученика (слот, occurrence) +
идемпотентность (повторное назначение уже зачисленного выпускника — 200, не
409) + инвариант «отказ на переводе между слотами не оставляет ученика без
слота вовсе» (откат целиком, не наполовину).

Регресс: `test_tsk673_graduation`, `test_course_inactive_tsk886`,
`test_tsk491_slot_transfer`, `test_lesson_bookable_join_tsk443`,
`test_lesson_calendar_tsk428`, `test_reschedule_slot_grid_tsk587`,
`test_tsk805_alumni_debts`, `test_assignment_rules_tsk031`,
`test_schedule_booking_tsk674`, `test_tsk261_dependency_autoassign`,
`test_tsk718_integrations_leads`, `test_user_courses_duplicate_tsk574` —
все зелёные (92+59 passed). Полный `pytest` — см. History задачи в Root.

## Живая проверка (dev-стенд, localhost:8000)

`scratchpad/tsk894_live_check.py`: создан выпускник напрямую в dev БД
(`student_subscription.plan_id` → `alumni`), временный курс через
`POST /courses/`, `POST /user-courses/` на выпускника → **409**,
`detail` дословно совпал с `alumni_enrollment_guard.ALUMNI_DETAIL`.
Контрольный обычный ученик на тот же курс → **201**. Фикстура убрана тем же
скриптом.

## Фронтенд (SPW)

Кода не потребовалось. Все формы, вызывающие эти мутации (`SlotDetail.tsx`
методиста — методом `toggle`/`transfer`, `AddAdHocLessonForm.tsx`
преподавателя, `TeacherLessonCard.tsx` через `useAddParticipant`,
`SlotPicker.tsx` самозаписи ученика), уже показывают `detail` ошибки API
через общую инфраструктуру (`getApiErrorMessage`/`readApiErrorMessage`) —
подтверждено чтением кода каждого компонента. Новый 409 отобразится тем же
механизмом, без отдельной SPW-правки.

## Риски / хвосты

- `course_dependencies_service._enroll_existing_students` — см. «Что НЕ
  тронуто» выше; тот же пробел уже был у tsk-886 для `is_active`, не мой
  новый долг, но стоит иметь в виду при следующей ревизии зачисления.
