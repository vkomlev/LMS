# Ревью tsk-1291: смена направления ученика (сервер)

Решение: ПРИНЯТО.

- `POST /api/v1/users/{user_id}/courses/switch-direction` (methodist/admin): `enroll_course_ids` зачисляются или включаются, `deactivate_course_ids` выключаются (`is_active=false`, строка остаётся). Одна транзакция, отказ (вложенный/выведенный курс, выпускник) откатывает всё.
- `UserCourseRead` и `UserCourseWithCourse` получили `is_active` (добавление; TG_LMS поле не читает).
- Тесты `tests/test_tsk1291_switch_direction.py` (7) + 864 смежных зелёные.
- Не блокирует: `PUT /user-courses/{u}/{c}` без проверки роли (старое, вне задачи).
