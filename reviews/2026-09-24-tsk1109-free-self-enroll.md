# Review-gate: tsk-1109 — самозапись ученика на бесплатный курс

Дата: 2026-09-24. Решение: **ПРИНЯТО** (после исправления одной блокирующей находки).

## Объём
LMS: `app/services/free_enrollment_service.py` (новый), `app/api/v1/me.py`
(`POST /me/courses/{course_id}/enroll-free`), `app/schemas/me.py` (`FreeEnrollmentResponse`),
`app/services/audit_service.py` (`STUDENT_COURSE_SELF_ENROLLED`), `tests/test_tsk1109_free_self_enroll.py`,
`docs/openapi.json` (только два куска этой задачи — tsk-669).
SPW: `lib/me/use-free-enrollment.ts`, `components/course/CourseNotAssigned.tsx`,
`app/(authed)/courses/[course_uid]/page.tsx`, `tests/unit/course-not-assigned.test.tsx`, `lib/api-types.ts`.
CB: `docs/cross-project/{CHANGELOG.md, contracts/lms-api.md, contracts/spw.md}`.
Незакоммиченные файлы соседней сессии tsk-1108 в тех же деревьях — вне объёма, в коммит не входят.

## Находки
1. **[Блокирующая, исправлена] Корректность/безопасность (изм. 2, 4).** `UserCoursesService.create`
   доназначает `course_dependencies` с `auto_assign`. Бесплатный курс с платной зависимостью открыл бы платный
   курс даром. Исправлено: перед записью все транзитивные зависимости обязаны быть `free`, иначе 403.
   Тесты `test_free_course_with_paid_dependency_is_forbidden`, `..._free_dependency_enrolls_both`.
2. **[Не блок] Роли.** Записаться может любой вошедший (не сервисный ключ): родитель, преподаватель.
   Вреда нет — курс бесплатный, доступ сотрудника и так шире. Ограничивать без требования не стал.
3. **[Не блок] Аудит после коммита связи.** `create` коммитит сам; если запись события упадёт, связь останется
   без следа, а повтор ответит `created=false`. Вероятность мала (тот же поток БД); на уровне задачи допустимо.
4. **[Не блок] Ссылка формы `id-N`** на курс незачисленного ведёт на общий экран «Курс не найден» (было и до
   правки): `CourseNotFoundResolver` ищет название только по `course_uid`. Лендинг ведёт по `course_uid`.

## Измерения
- Цели: эндпоинт (корень, free, is_active, идемпотентно, 403, лимит, аудит) + кнопка SPW — покрыто.
- IDOR: `user_id` берётся только из сессии; `course_id` — публичный идентификатор, проверка на сервере.
- Контракт API: openapi и типы SPW в тех же коммитах; cross-project обновлён.
- Реестр ошибок LMS: tsk-669 (openapi из общего дерева) — соблюдено, чужие куски не взяты; tsk-574 (дубль 409)
  — гонка превращена в идемпотентный успех перечитыванием.

## Проверки
- LMS: `pytest tests/test_tsk1109_free_self_enroll.py` — 12 passed; с соседними наборами
  (`test_me_endpoints_y3`, `test_me_profile_update`, `test_user_courses_duplicate_tsk574`,
  `test_tsk1070_public_course_offer`, `test_tsk261_me_courses_open_attempt`) — 46 passed до правки п.1.
- SPW: `vitest tests/unit/course-not-assigned.test.tsx` — 7 passed; `tsc --noEmit` и `eslint` — чисто.
- Живая проверка на проде новым пользователем — после выката (обязательна, `live-browser-testing.md`).

## Откат
Код-only, без миграций. Откат — `git revert` коммитов tsk-1109 в LMS и SPW + повторный выкат.
Созданные самозаписью связи — строки `user_courses` с событием `student.course.self_enrolled`; при откате
их не трогать (доступ ученика к бесплатному курсу сохраняется).
