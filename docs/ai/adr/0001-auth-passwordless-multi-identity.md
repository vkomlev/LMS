# ADR LMS-0001 — Расширение auth: с api_key (service) на user-level passwordless

**Статус:** ACCEPTED (план)
**Дата:** 2026-04-27
**Связано с:** [ContentBackbone ADR-0011](../../../../ContentBackbone/docs/adr/0011-auth-strategy-passwordless-multi-identity.md), [Cross-project review](../../../../ContentBackbone/reviews/2026-04-27-architect-spw-cross-project-verification.md)
**Триггер:** запуск Student Practice Web (SPW) — публичного веб-клиента для учеников

## Контекст

Текущее состояние LMS API:
- `app/auth/api_key_scheme.py` — фактически пустой файл (1 строка). User-level auth НЕ enforced.
- Все эндпоинты `learning/*`, `attempts/*`, `task_results/*`, `teacher/*` принимают `student_id`/`teacher_id`/`user_id` параметром от вызывающего без проверки.
- Trust model: всё внутри trusted-zone (TG_LMS bots + ContentBackbone CLI).
- `users` таблица содержит:
  - `email: NOT NULL UNIQUE`
  - `password_hash: NOT NULL`
  - `tg_id: BigInteger nullable`
  - НЕТ session-таблицы, НЕТ identity-link таблицы для multi-identity

Запуск SPW открывает учебный API ученикам в публичную сеть. Это требует:
1. Первичного внедрения user-level auth (не «расширения», а **создания**)
2. Сохранения совместимости со service-level access от TG_LMS / ContentBackbone
3. Поддержки 3 identities: email, Telegram, VK ID 2.0 — все опциональные
4. Passwordless (нет паролей) — снижает barrier и устраняет password fatigue
5. Защиты от IDOR: эндпоинты с `student_id` параметром должны валидировать соответствие сессии

## Решение

### Phase 1 миграции БД

`app/db/migrations/versions/<NN>_<date>_auth_extension.py`:

1. **`users.password_hash` — DROP NOT NULL** (passwordless юзеры допускаются)
2. **`users.email` — DROP NOT NULL**; UNIQUE constraint заменяется на partial unique index `WHERE email IS NOT NULL`
3. Новая таблица **`identity_link`**:

```sql
CREATE TABLE identity_link (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind VARCHAR(8) NOT NULL CHECK (kind IN ('email', 'tg', 'vk')),
  value VARCHAR(255) NOT NULL,
  vk_access_token_enc BYTEA,
  vk_refresh_token_enc BYTEA,
  vk_token_expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ,
  UNIQUE(kind, value)
);
CREATE INDEX idx_identity_link_user_id ON identity_link(user_id);
```

4. Новая таблица **`user_session`**:

```sql
CREATE TABLE user_session (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash BYTEA NOT NULL,
  refresh_token_hash BYTEA,
  ua_fingerprint TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  refresh_expires_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_user_session_token_hash ON user_session(token_hash);
CREATE INDEX idx_user_session_user_active ON user_session(user_id, expires_at) WHERE revoked_at IS NULL;
```

5. Новая таблица **`magic_link`**:

```sql
CREATE TABLE magic_link (
  id SERIAL PRIMARY KEY,
  email VARCHAR(255) NOT NULL,
  token_hash BYTEA NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_magic_link_token_hash ON magic_link(token_hash);
CREATE INDEX idx_magic_link_email_created ON magic_link(email, created_at);
```

6. Новая таблица **`audit_event`** (append-only):

```sql
CREATE TABLE audit_event (
  id BIGSERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  event_type VARCHAR(64) NOT NULL,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  ip INET,
  user_agent TEXT,
  details JSONB
);
CREATE INDEX idx_audit_event_user_ts ON audit_event(user_id, ts DESC);
CREATE INDEX idx_audit_event_type_ts ON audit_event(event_type, ts DESC);

-- Trigger to prevent updates/deletes
CREATE OR REPLACE FUNCTION audit_event_immutable() RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'audit_event is append-only';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER audit_event_no_modify BEFORE UPDATE OR DELETE ON audit_event
FOR EACH ROW EXECUTE FUNCTION audit_event_immutable();
```

7. Новая таблица **`product_event`** (partitioned by month, для funnel-аналитики):

```sql
CREATE TABLE product_event (
  id BIGSERIAL,
  user_id INTEGER,
  event_type VARCHAR(64) NOT NULL,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  properties JSONB,
  PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

-- Создание начальных partition (next 6 месяцев) — отдельно
```

8. Новая таблица **`guest_session`** + **`guest_attempt`** для гостевого режима:

```sql
CREATE TABLE guest_session (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ip INET,
  ua_fingerprint TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  attributed_user_id INTEGER REFERENCES users(id)
);

CREATE TABLE guest_attempt (
  id BIGSERIAL PRIMARY KEY,
  guest_session_id UUID NOT NULL REFERENCES guest_session(id) ON DELETE CASCADE,
  task_id INTEGER REFERENCES tasks(id),
  answer_json JSONB,
  is_correct BOOLEAN,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  attributed_user_id INTEGER REFERENCES users(id),
  attributed_at TIMESTAMPTZ
);
CREATE INDEX idx_guest_attempt_session ON guest_attempt(guest_session_id, created_at);
CREATE INDEX idx_guest_attempt_unattributed ON guest_attempt(created_at) WHERE attributed_user_id IS NULL;
```

### Phase 1 — auth-эндпоинты

Новый роутер `app/api/v1/auth/`:

- `POST /api/v1/auth/magic-link/send` — выпуск magic-link на email (rate-limit 5/10мин на IP)
- `POST /api/v1/auth/magic-link/verify` — потребление magic-link, выдача session (constant-time 401 без enumeration)
- `POST /api/v1/auth/tg/init` — верификация `Telegram.WebApp.initData` HMAC, выдача Bearer + cookie
- `POST /api/v1/auth/vk/callback` — обработка VK ID 2.0 OAuth callback (PKCE)
- `POST /api/v1/auth/session/refresh` — ротация: revoke старой + create новой пары токенов
- `POST /api/v1/auth/session/logout` — invalidate текущую сессию + delete cookie

Также:
- `GET /api/v1/me` — профиль текущего пользователя
- `POST /api/v1/me/identity/{kind}/link` — привязать вторую identity через `link_token`
- `POST /api/v1/me/attribute-guest` — атрибуция guest_session → текущий user

Embed-API:
- `GET /embed-api/courses/{course_uid}/task/{external_uid}?token=…` — read-only для WP embed
- `POST /embed-api/auth/issue` — выпуск URL-token (TTL 5 мин, single-use)

Teacher-API расширение (для SA_COM grade — см. [Q-V2](../../../../ContentBackbone/reviews/2026-04-27-architect-spw-cross-project-verification.md)):
- `POST /api/v1/teacher/reviews/{result_id}/grade` — выставление оценки + коммента (если ещё не существует)

### Phase 1 — auth dependency

`app/auth/current_user.py` (новый):

```python
async def get_current_user(
    session_cookie: Optional[str] = Cookie(None, alias="lms_session"),
    bearer: Optional[str] = Header(None, alias="Authorization"),
    url_token: Optional[str] = Query(None, alias="token"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    # Резолв в порядке приоритета:
    # 1. cookie session (web)
    # 2. Bearer (TG App)
    # 3. URL token (embed) — только если path начинается с /embed-api
    # 4. X-API-Key (service-level — TG_LMS bots, ContentBackbone CLI)
    ...
```

`CurrentUser` — dataclass с `id`, `role`, `is_service`, `identities: list[IdentityLinkRead]`.

Применение во всех `learning/*`, `attempts/*`, `task_results/*`, `teacher/*` endpoints — заменяет/дополняет существующий приём `student_id` параметром:

- Если `current_user.is_service` — старое поведение (студ_id передаётся параметром)
- Если `current_user.role == 'student'` — `student_id == current_user.id` или 403 (защита от IDOR)
- Если `current_user.role == 'teacher'` — для teacher endpoints; для student endpoints — 403

### Phase 1 — IDOR sweep test

Автогенерируется по `openapi.json`:

```python
# tests/test_idor_sweep.py
def test_idor_for_all_endpoints_with_id_param():
    """Для каждого эндпоинта с id-параметром: попытка чтения чужого id → 403."""
    ...
```

Обязательный CI gate.

## Обоснование

См. [ContentBackbone ADR-0011](../../../../ContentBackbone/docs/adr/0011-auth-strategy-passwordless-multi-identity.md) — все аргументы применимы.

LMS-side специфика:
- Существующая `users` таблица сохраняется; `password_hash` остаётся для потенциальных legacy-кейсов
- `users.tg_id` — read-only legacy fallback; новые лукапы через `identity_link`
- `tasks` / `attempts` / `task_results` — НЕ меняются (auth добавляется на уровне роутера)

## Последствия

**Положительные:**
- LMS API получает первый production-grade auth-слой
- SPW + TG_LMS студ-бот (через TG Mini App) + WP embed работают на одном auth-ядре
- Service-level api_key (для TG_LMS bots, ContentBackbone CLI) продолжает работать через `X-API-Key` header
- IDOR защита покрывает все эндпоинты с `id` параметром

**Отрицательные:**
- Phase 1 миграция затрагивает существующих юзеров (`password_hash` + `email` NULL допустимы) — требует тестирования на staging
- `users.tg_id` остаётся как legacy field — двойная identity-модель в период coexistence (~6 мес)
- IDOR sweep test может дать false-positive, требует поддержки

**Митигация:**
- Phase 1 миграция тестируется с rollback (Alembic downgrade -1)
- audit_event для `auth.identity.linked / conflict / revoked` даёт postmortem-ready аудит
- IDOR sweep исключения по конкретным endpoints с обоснованием в комментарии

## Связанные документы

- ContentBackbone [ADR-0011](../../../../ContentBackbone/docs/adr/0011-auth-strategy-passwordless-multi-identity.md) — общая auth стратегия
- ContentBackbone [ADR-0013](../../../../ContentBackbone/docs/adr/0013-identity-model-vk-token-encryption.md) — Fernet для VK
- ContentBackbone [ADR-0017](../../../../ContentBackbone/docs/adr/0017-session-storage-per-context.md) — session storage по контекстам
- ContentBackbone [Cross-project review](../../../../ContentBackbone/reviews/2026-04-27-architect-spw-cross-project-verification.md) — конфликты C5/C6/C7
- LMS docs/learning-engine-next-item.md — учебный движок (без изменений)
- LMS docs/frontend-contract-sa-com.md — SA_COM формат (без изменений)
