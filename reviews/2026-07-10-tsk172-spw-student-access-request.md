# tsk-172 (Часть 2) — SPW-регистрация формирует заявку на роль student

**Дата:** 2026-07-10
**Проект:** LMS (auth), затрагивает SPW (вход) и TG_LMS (очередь заявок админ-бота)

## Контекст

Пользователь с уже имеющейся ролью (teacher/methodist/admin) при входе в SPW не
получает роль student автоматически (`ensure_student_role` назначает только при
ПОЛНОМ отсутствии ролей). Чтобы совмещение teacher+student выдавалось штатным
одобрением, SPW-регистрация должна формировать заявку на student.

## Решение (вариант A — только role-holder; выбор оператора)

- `app/services/auth/role_assign_service.py` — новый helper
  `ensure_student_access_request(db, user_id, *, channel)`:
  - нет ролей → no-op (pure student получает роль авто, без approval);
  - есть student → no-op;
  - уже есть заявка на student в ЛЮБОМ статусе → no-op (не дублируем/не
    воскрешаем после rejected/completed);
  - иначе INSERT `access_requests(role_id=4, flag='not_ready')`.
  - Идемпотентно; caller коммитит; soft-fail.
- Вызов добавлен в 3 SPW-эндпоинта после резолва пользователя, перед `commit`,
  в try/except (soft-fail, не блокирует вход):
  `app/api/v1/auth/magic_link.py`, `tg.py`, `vk.py`.

Обычный онбординг чистых студентов не меняется (auto-assign сохранён).

## Проверки

- `pytest tests/test_student_access_request_tsk172.py` (5) + регрессия
  `test_auto_register_{magic_link,tg,vk}.py` + `test_users_create_identity_tsk171.py`
  — **24 прошли**.
- Кейсы: role-holder→заявка not_ready; идемпотентность (нет дублей); нет
  ролей→no-op; есть student→no-op; существующая заявка (rejected)→no-op.

## Риски / follow-ups

- Заявка создаётся при КАЖДОМ входе, но идемпотентна (одна на пользователя, пока
  не удалят строку). После rejected повторно не создаётся — оператор при
  необходимости чистит заявку вручную.
- Прод-деплой LMS — отдельный шаг (пайплайн tsk-005 / deploy-lms).
- Cross-project: обновить `contracts/lms-api.md` + CHANGELOG (auth-поведение).
