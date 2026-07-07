# tsk-171 — POST /users/ создаёт identity_link (email + tg)

**Дата:** 2026-07-08
**Проект:** LMS (fix), затрагивает TG_LMS (боты) и SPW (вход)

## Контекст / проблема

Для тестов нужен режим совмещения ролей teacher+student на одном email.
Роли в LMS уже M2M (совмещение работает), но пользователь, созданный через
`POST /api/v1/users/` (в т.ч. преподаватель из ботов TG_LMS), получал
`users.email` **без** записи `identity_link(kind='email')`. Для auth-флоу SPW
это «orphan»:
- magic-link: `magic_link_service.get_or_create_user_by_email` не находит юзера
  через `identity_link`, находит «висячий» `users.email` → **409**
  «email в нестандартном состоянии» (ADR-0021 §2, запрет auto-merge).
- VK: та же orphan-ветка `vk_oauth_service.get_or_create_user_by_vk`.
- Более того, такой преподаватель без единой identity не мог войти в SPW вообще.

## Решение (вариант A — фикс в источнике)

1. **`app/services/users_service.py:UsersService.create`** — при создании
   пользователя с `email`/`tg_id` синхронно вызывает
   `identity_link_service.upsert_identity` в ТОЙ ЖЕ транзакции
   (`repo.create(..., commit=False)` → upsert email/tg → `commit`). Покрывает
   единственный путь `POST /users/` (внутренних вызовов сервиса нет).
2. **`scripts/backfill_identity_links_tsk171.py`** — идемпотентный бэкфилл
   недостающих `identity_link` для уже существующих orphan-пользователей
   (`ON CONFLICT (kind, value) DO NOTHING`, флаг `--dry-run`).
3. Роль ученика добавляется штатно (`/assign_role <user_id> 4` в admin-боте или
   auto при первом входе, если ролей нет) — вне scope кода.

Не размывает ADR-0021: данные консистентны на входе, auth-флоу не меняется.

## Проверки

- `pytest` новые + связанные: `test_users_create_identity_tsk171.py` (2),
  `test_identity_linking.py` (4), `test_auto_register_{magic_link,tg,vk}.py`,
  `test_auth_test_session_y4_pre_s5.py` (22), `test_identity_link_existing_user.py`,
  `test_role_assign_y4_pre_s5.py` (14) — **все прошли (42)**.
- Backfill `--dry-run` на локальной БД: кандидатов email=33, tg=0.
- Backfill применён локально: вставлено email=33; повторный dry-run → email=0
  (идемпотентность подтверждена).

## Риски / follow-ups

- Локальная БД (`localhost/Learn`) починена. **Прод** требует отдельно:
  (а) деплой кода LMS, (б) прогон backfill против прод-БД — оба через
  пайплайн деплоя (tsk-005), не выполнялось из этой сессии.
- Тест-преподавателю нужно добавить роль student (`/assign_role <id> 4`).
- `/review-gate` перед merge в main (обязательно по правилам LMS).
- Cross-project: обновлён CHANGELOG в ContentBackbone (auth-контракт LMS).
