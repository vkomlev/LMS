# Review: Phase Y-1 — Auth Extension для SPW

**Дата:** 2026-04-27
**Scope:** Passwordless multi-identity auth (email magic-link + TG initData + VK ID 2.0) + guest session для SPW

---

## Цель

Расширить LMS API для обслуживания Next.js SPW-клиента: добавить аутентификацию без пароля, гостевой режим и атрибуцию попыток при регистрации. Сохранить полную backward compat с TG_LMS ботами через legacy `?api_key=`.

## Затронутые файлы

### Новые файлы
- `app/models/identity_link.py` — таблица привязки идентификаторов
- `app/models/user_session.py` — сессии с UUID PK и token_hash
- `app/models/magic_link.py` — одноразовые email-ссылки
- `app/models/audit_event.py` — append-only журнал событий
- `app/models/guest_session.py` — анонимные сессии
- `app/models/guest_attempt.py` — попытки гостей
- `app/services/fernet_service.py` — Fernet-шифрование VK токенов
- `app/services/rate_limit_service.py` — sliding window rate limiter (Redis, fail-open)
- `app/services/audit_service.py` — запись в audit_event
- `app/services/auth/identity_link_service.py` — CRUD identity_link
- `app/services/auth/session_service.py` — create/validate/revoke user_session
- `app/services/auth/magic_link_service.py` — create/consume/send magic_link
- `app/services/auth/tg_init_service.py` — HMAC верификация initData
- `app/services/auth/vk_oauth_service.py` — VK ID 2.0 PKCE exchange
- `app/services/auth/link_token_service.py` — атрибуция guest → user
- `app/auth/current_user.py` — CurrentUser dataclass
- `app/auth/service_api_key.py` — валидация service key
- `app/schemas/auth.py` — Pydantic схемы для auth
- `app/schemas/me.py` — схема /me
- `app/api/v1/auth/magic_link.py` — POST /auth/magic-link/send + /verify
- `app/api/v1/auth/tg.py` — POST /auth/tg/init
- `app/api/v1/auth/vk.py` — POST /auth/vk/callback
- `app/api/v1/auth/session.py` — POST /auth/session/refresh + /logout
- `app/api/v1/me.py` — GET /me
- `app/api/v1/embed_api.py` — POST /embed/session + /session/{id}/attempts
- `app/db/migrations/versions/20260428_010000_M1_*.py` — M1: users relax
- `app/db/migrations/versions/20260428_020000_M2_*.py` — M2: identity_link
- `app/db/migrations/versions/20260428_030000_M3_*.py` — M3: user_session + magic_link
- `app/db/migrations/versions/20260428_040000_M4_*.py` — M4: audit_event + product_event
- `app/db/migrations/versions/20260428_050000_M5_*.py` — M5: guest_session + guest_attempt

### Изменённые файлы
- `app/api/deps.py` — добавлен `get_current_user`, `get_bare_db`, `require_authenticated`
- `app/api/main.py` — CORS из env, регистрация новых роутеров
- `app/core/config.py` — SPW auth env vars
- `app/db/base.py` — импорт 6 новых моделей
- `app/models/users.py` — email/password_hash nullable, relationships
- `requirements.txt` — cryptography, redis, PyJWT

---

## Результаты валидации

### Миграции
```
alembic upgrade head
→ 20260428_050000_m5_guest (head) ✅
```

Все 5 миграций применены успешно. Промежуточный инцидент: revision ID M3 был 42 chars > varchar(32) — исправлено до ≤32 chars.

### Smoke tests
```
POST /api/v1/auth/magic-link/send {"email":"test@example.com"}
→ 202 {"message":"Письмо отправлено"} ✅

POST /api/v1/embed/session
→ 201 {"guest_session_id":"3961cc5f-..."} ✅

GET /api/v1/me (no auth)
→ 401 {"detail":"Not authenticated"} ✅

GET /api/v1/users/?api_key=bot-key-1 (legacy)
→ 200 [{users...}] ✅

GET /api/v1/users/?api_key=invalid_key
→ 403 {"detail":"Invalid or missing API Key"} ✅
```

### Import check
```
python -c "from app.api.main import app; print('OK')"
→ OK ✅
```

---

## Известные ограничения / Risks

- **Redis не запущен локально:** rate limiter работает в fail-open режиме, логирует warning. В prod Redis обязателен.
- **RESEND_API_KEY не задан:** письма не отправляются, только логируется warning (background task не падает).
- **VK PKCE:** не протестирован end-to-end (нет VK app credentials локально).
- **IDOR sweep:** `get_current_user` добавлен в deps.py, но существующие learning/attempts endpoints ещё не обновлены — будет в Phase Y-2.
- **product_event:** нет ORM-модели (partitioned table), только миграция. Работа с ней через raw SQL в будущих фазах.

---

## Rollback Note

```bash
alembic downgrade 20260428_040000_m4_audit  # откатить M5
alembic downgrade 20260428_030000_m3_sessions  # откатить M4
alembic downgrade 20260428_020000_m2_identity_link  # откатить M3
alembic downgrade 20260428_010000_m1_users_relax  # откатить M2
alembic downgrade teacher_next_modes_stage39  # откатить M1
```

Файлы новых сервисов/роутеров можно удалить без риска для legacy функциональности.
