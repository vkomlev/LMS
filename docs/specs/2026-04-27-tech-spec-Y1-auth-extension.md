# Tech-Spec Y-1: миграции БД + auth-расширение LMS API

**Дата:** 2026-04-27
**Skill:** /tech-spec-composer
**Статус:** READY for executor (Phase Y-1)
**Phase:** Y-1 из Stream Y (SPW — Student Practice Web)
**Предшествующие артефакты:**
- `docs/ai/adr/0001-auth-passwordless-multi-identity.md` — arch ADR (LMS-local)
- ContentBackbone: `docs/adr/0011`, `0013`, `0014`, `0017` (стратегия + session/domain/encryption)
- ContentBackbone: `reviews/2026-04-27-db-check-spw-phase1-readiness.md` (GO статус БД)

**Целевой исполнитель:** `/executor-pro` (контракты, миграции, security-критичные пути)

## 1. Контекст

LMS API сейчас не имеет user-level auth (`app/auth/api_key_scheme.py` — пустой файл). Phase Y-1 вводит первичный auth-слой для SPW и сохраняет совместимость со service-level доступом (TG_LMS bots, ContentBackbone CLI) через `X-API-Key` header. Это самая большая фаза проекта — затрагивает БД, контракты и все эндпоинты.

Текущий Alembic head: `teacher_next_modes_stage39`.

## 2. Стек и стандарты

- Python 3.10+, FastAPI, SQLAlchemy 2.x async, Alembic
- PG 13+ (нужно подтвердить версию для declarative partitioning)
- Redis 7 для rate-limiting + (опц.) session cache (`REDIS_URL` DB=2; DB=0 занят TG_LMS)
- Type hints обязательны; docstrings RU; `logging` вместо print; UTF-8 без BOM
- Pytest + httpx-test для integration; bandit для SAST; `pytest-asyncio`

## 3. Архитектура изменений

```
app/
├── api/v1/
│   ├── auth/                         <- NEW роутер
│   │   ├── __init__.py
│   │   ├── magic_link.py
│   │   ├── tg.py
│   │   ├── vk.py
│   │   └── session.py
│   ├── me.py                         <- NEW
│   ├── embed_api.py                  <- NEW
│   ├── learning.py                   <- MOD (добавить current_user dep)
│   ├── attempts.py                   <- MOD (добавить current_user dep)
│   ├── teacher_reviews.py            <- MOD (добавить teacher RBAC)
│   └── (остальные)                   <- MOD (добавить current_user dep по списку §6.5)
├── auth/                             <- MAJOR EXPANSION
│   ├── api_key_scheme.py             <- DELETE (или пустой с deprecation note)
│   ├── current_user.py               <- NEW (dep)
│   └── service_api_key.py            <- NEW (X-API-Key header model)
├── services/auth/                    <- NEW
│   ├── __init__.py
│   ├── magic_link_service.py
│   ├── tg_init_service.py
│   ├── vk_oauth_service.py
│   ├── session_service.py
│   ├── identity_link_service.py
│   └── link_token_service.py
├── services/
│   ├── audit_service.py              <- NEW
│   ├── fernet_service.py             <- NEW
│   ├── rate_limit_service.py         <- NEW (Redis-backed)
│   └── (existing)
├── models/
│   ├── identity_link.py              <- NEW
│   ├── user_session.py               <- NEW
│   ├── magic_link.py                 <- NEW
│   ├── audit_event.py                <- NEW
│   ├── product_event.py              <- NEW
│   ├── guest_session.py              <- NEW
│   └── guest_attempt.py              <- NEW
├── schemas/
│   ├── auth.py                       <- NEW
│   ├── me.py                         <- NEW
│   └── (existing — без изменений)
├── db/migrations/versions/
│   ├── 20260428_010000_M1_users_relax_constraints.py    <- NEW
│   ├── 20260428_020000_M2_identity_link.py              <- NEW
│   ├── 20260428_030000_M3_user_session_magic_link.py    <- NEW
│   ├── 20260428_040000_M4_audit_product_events.py       <- NEW
│   └── 20260428_050000_M5_guest_session_attempt.py      <- NEW
└── core/
    └── config.py                     <- MOD (новые env)
tests/
├── test_idor_sweep.py                <- NEW (auto-generated по openapi.json)
├── test_auth_magic_link.py           <- NEW
├── test_auth_tg_init.py              <- NEW
├── test_auth_vk_callback.py          <- NEW
├── test_session_lifecycle.py         <- NEW
├── test_identity_linking.py          <- NEW
├── test_guest_attribution.py         <- NEW
└── test_migrations.py                <- NEW (upgrade/downgrade roundtrip)
```

## 4. Миграции (детально)

### M1 — users relax constraints

```python
# 20260428_010000_M1_users_relax_constraints.py
revision = '20260428_010000_m1_users_relax'
down_revision = 'teacher_next_modes_stage39'

def upgrade():
    # Phase 1: pgcrypto extension (для gen_random_uuid)
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    # Phase 2: relax password_hash NOT NULL
    op.alter_column('users', 'password_hash', existing_type=sa.String(), nullable=True)

    # Phase 3: relax email NOT NULL + replace UNIQUE on full constraint with partial index
    op.drop_constraint('users_email_key', 'users', type_='unique')
    op.alter_column('users', 'email', existing_type=sa.String(), nullable=True)
    op.create_index(
        'users_email_unique_partial',
        'users',
        ['email'],
        unique=True,
        postgresql_where=sa.text('email IS NOT NULL'),
    )

def downgrade():
    op.drop_index('users_email_unique_partial', table_name='users')
    op.execute("UPDATE users SET email = id::text || '@placeholder.invalid' WHERE email IS NULL;")
    op.execute("UPDATE users SET password_hash = '' WHERE password_hash IS NULL;")
    op.alter_column('users', 'email', existing_type=sa.String(), nullable=False)
    op.alter_column('users', 'password_hash', existing_type=sa.String(), nullable=False)
    op.create_unique_constraint('users_email_key', 'users', ['email'])
    # pgcrypto extension оставляем (не вреден)
```

### M2 — identity_link

```python
def upgrade():
    op.create_table('identity_link',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(8), nullable=False),
        sa.Column('value', sa.String(255), nullable=False),
        sa.Column('vk_access_token_enc', sa.LargeBinary, nullable=True),
        sa.Column('vk_refresh_token_enc', sa.LargeBinary, nullable=True),
        sa.Column('vk_token_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('email','tg','vk')", name='identity_link_kind_check'),
    )
    op.create_unique_constraint('uq_identity_link_kind_value', 'identity_link', ['kind', 'value'])
    op.create_index('idx_identity_link_user_id', 'identity_link', ['user_id'])

    # Backfill из users.tg_id
    op.execute("""
        INSERT INTO identity_link (user_id, kind, value, created_at)
        SELECT id, 'tg', tg_id::text, created_at FROM users WHERE tg_id IS NOT NULL;
    """)

    # Backfill из users.email
    op.execute("""
        INSERT INTO identity_link (user_id, kind, value, created_at)
        SELECT id, 'email', lower(email), created_at FROM users WHERE email IS NOT NULL;
    """)

def downgrade():
    op.drop_index('idx_identity_link_user_id')
    op.drop_constraint('uq_identity_link_kind_value', 'identity_link')
    op.drop_table('identity_link')
```

### M3 — user_session + magic_link

```python
def upgrade():
    op.create_table('user_session',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.LargeBinary, nullable=False),
        sa.Column('refresh_token_hash', sa.LargeBinary, nullable=True),
        sa.Column('ua_fingerprint', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('refresh_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('uq_user_session_token_hash', 'user_session', ['token_hash'], unique=True)
    op.create_index('idx_user_session_user_active', 'user_session', ['user_id', 'expires_at'],
                    postgresql_where=sa.text('revoked_at IS NULL'))

    op.create_table('magic_link',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('token_hash', sa.LargeBinary, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('uq_magic_link_token_hash', 'magic_link', ['token_hash'], unique=True)
    op.create_index('idx_magic_link_email_created', 'magic_link', ['email', 'created_at'])

def downgrade():
    op.drop_index('idx_magic_link_email_created')
    op.drop_index('uq_magic_link_token_hash')
    op.drop_table('magic_link')
    op.drop_index('idx_user_session_user_active')
    op.drop_index('uq_user_session_token_hash')
    op.drop_table('user_session')
```

### M4 — audit_event + product_event

```python
def upgrade():
    op.create_table('audit_event',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('event_type', sa.String(64), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('ip', postgresql.INET, nullable=True),
        sa.Column('user_agent', sa.Text, nullable=True),
        sa.Column('details', postgresql.JSONB, nullable=True),
    )
    op.create_index('idx_audit_event_user_ts', 'audit_event', ['user_id', sa.text('ts DESC')])
    op.create_index('idx_audit_event_type_ts', 'audit_event', ['event_type', sa.text('ts DESC')])

    op.execute("""
        CREATE OR REPLACE FUNCTION audit_event_immutable() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_event is append-only';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER audit_event_no_modify
            BEFORE UPDATE OR DELETE ON audit_event
            FOR EACH ROW EXECUTE FUNCTION audit_event_immutable();
    """)

    # product_event с monthly partitioning
    op.execute("""
        CREATE TABLE product_event (
            id BIGSERIAL,
            user_id INTEGER,
            event_type VARCHAR(64) NOT NULL,
            ts TIMESTAMPTZ NOT NULL DEFAULT now(),
            properties JSONB,
            PRIMARY KEY (id, ts)
        ) PARTITION BY RANGE (ts);
    """)
    op.execute("""
        DO $$
        DECLARE
            start_dt DATE := date_trunc('month', now())::DATE;
            i INT;
            partition_name TEXT;
            from_dt DATE;
            to_dt DATE;
        BEGIN
            FOR i IN 0..6 LOOP
                from_dt := (start_dt + (i || ' month')::INTERVAL)::DATE;
                to_dt := (start_dt + ((i + 1) || ' month')::INTERVAL)::DATE;
                partition_name := 'product_event_' || to_char(from_dt, 'YYYY_MM');
                EXECUTE format(
                    'CREATE TABLE %I PARTITION OF product_event FOR VALUES FROM (%L) TO (%L);',
                    partition_name, from_dt, to_dt
                );
            END LOOP;
        END $$;
    """)
    op.create_index('idx_product_event_user_ts', 'product_event', ['user_id', sa.text('ts DESC')])
    op.create_index('idx_product_event_type_ts', 'product_event', ['event_type', sa.text('ts DESC')])

def downgrade():
    op.drop_index('idx_product_event_type_ts')
    op.drop_index('idx_product_event_user_ts')
    op.execute("DROP TABLE product_event CASCADE;")  # CASCADE уберёт partitions
    op.execute("DROP TRIGGER audit_event_no_modify ON audit_event;")
    op.execute("DROP FUNCTION audit_event_immutable();")
    op.drop_index('idx_audit_event_type_ts')
    op.drop_index('idx_audit_event_user_ts')
    op.drop_table('audit_event')
```

### M5 — guest_session + guest_attempt

```python
def upgrade():
    op.create_table('guest_session',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('ip', postgresql.INET, nullable=True),
        sa.Column('ua_fingerprint', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('attributed_user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )

    op.create_table('guest_attempt',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('guest_session_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('guest_session.id', ondelete='CASCADE'), nullable=False),
        sa.Column('task_id', sa.Integer, sa.ForeignKey('tasks.id', ondelete='SET NULL'), nullable=True),
        sa.Column('answer_json', postgresql.JSONB, nullable=True),
        sa.Column('is_correct', sa.Boolean, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('attributed_user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('attributed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('idx_guest_attempt_session', 'guest_attempt', ['guest_session_id', 'created_at'])
    op.create_index('idx_guest_attempt_unattributed', 'guest_attempt', ['created_at'],
                    postgresql_where=sa.text('attributed_user_id IS NULL'))

def downgrade():
    op.drop_index('idx_guest_attempt_unattributed')
    op.drop_index('idx_guest_attempt_session')
    op.drop_table('guest_attempt')
    op.drop_table('guest_session')
```

## 5. Auth-сервисы (детальные сигнатуры)

### 5.1. `app/services/auth/magic_link_service.py`

```python
class MagicLinkService:
    """Email magic-link auth."""

    EXPIRES_MINUTES: int = 15
    RATE_LIMIT_PER_EMAIL: int = 3   # per minute
    RATE_LIMIT_PER_IP: int = 10     # per minute

    async def request(
        self, db: AsyncSession, email: str, ip: str | None,
        rate_limiter: RateLimiter, mailer: ResendClient,
    ) -> None:
        """
        Создать magic-link, выслать email. Constant-time response (no enumeration).
        Raises RateLimitError при превышении.
        """
        ...

    async def consume(
        self, db: AsyncSession, raw_token: str,
        ua_fingerprint: str | None, ip: str | None,
    ) -> tuple[Users, UserSession]:
        """
        Атомарно UPDATE magic_link SET consumed_at=now() WHERE token_hash=? AND consumed_at IS NULL AND expires_at > now()
        RETURNING email. Если rowcount=0 — TokenInvalid/TokenExpired/TokenAlreadyUsed.
        Создаёт user если нужно (по email lookup в identity_link); создаёт session.
        """
        ...
```

### 5.2. `app/services/auth/tg_init_service.py`

```python
class TgInitService:
    """Verify Telegram WebApp initData."""

    INITDATA_TTL_SECONDS: int = 60
    BOT_TOKEN_ENV: str = "TG_BOT_TOKEN_FOR_INITDATA"

    def verify_init_data(self, init_data: str) -> dict:
        """
        Parses query-string. Computes data_check_string.
        secret = HMAC_SHA256("WebAppData", bot_token).
        expected = HMAC_SHA256(secret, data_check_string).
        Constant-time compare. Validate auth_date > now() - TTL.
        Raises InvalidInitDataError или InitDataExpiredError.
        """
        ...

    async def init_session(
        self, db: AsyncSession, init_data: str, ua_fingerprint: str | None, ip: str | None,
    ) -> tuple[Users, UserSession, str]:  # user, session, bearer_token
        """Полный flow: verify → identity_link upsert → user upsert → session."""
        ...
```

### 5.3. `app/services/auth/vk_oauth_service.py`

```python
class VkOAuthService:
    """VK ID 2.0 OAuth."""

    CLIENT_ID_ENV: str = "VK_ID_CLIENT_ID"
    CLIENT_SECRET_ENV: str = "VK_ID_CLIENT_SECRET"

    async def exchange_code(self, code: str, redirect_uri: str) -> dict:
        """Обмен code на access_token + id_token + refresh_token."""
        ...

    def verify_id_token(self, id_token: str) -> dict:
        """Validate JWT signature + exp."""
        ...

    async def callback(
        self, db: AsyncSession, code: str, state: str, expected_state: str,
        ua_fingerprint: str | None, ip: str | None,
    ) -> tuple[Users, UserSession]:
        """
        1. CSRF check state.
        2. Exchange code.
        3. Verify id_token.
        4. Encrypt vk_access_token via Fernet.
        5. Upsert identity_link kind='vk' with token enc.
        6. Upsert user.
        7. Create session.
        """
        ...
```

### 5.4. `app/services/auth/session_service.py`

```python
class SessionService:
    """User session lifecycle."""

    ACCESS_TTL_MINUTES: int = 15
    REFRESH_TTL_DAYS: int = 30
    SLIDING_REFRESH: bool = True

    async def create(self, db: AsyncSession, user_id: int, ua_fingerprint: str | None) -> tuple[UserSession, str, str]:
        """Returns (session, access_token, refresh_token)."""
        ...

    async def resolve(self, db: AsyncSession, raw_token: str) -> Users | None:
        """Look up by token_hash + check expires_at + revoked_at."""
        ...

    async def refresh(self, db: AsyncSession, raw_refresh: str) -> tuple[UserSession, str, str]:
        """Rotate session: revoke old, create new."""
        ...

    async def revoke(self, db: AsyncSession, session_id: UUID) -> None:
        ...
```

### 5.5. `app/services/auth/identity_link_service.py`

```python
class IdentityLinkService:
    async def find_user_by_identity(self, db: AsyncSession, kind: str, value: str) -> Users | None:
        ...

    async def upsert_identity(
        self, db: AsyncSession, user_id: int, kind: str, value: str,
        vk_access_token: str | None = None,
        vk_refresh_token: str | None = None,
        vk_expires_at: datetime | None = None,
    ) -> IdentityLink:
        """
        Upsert. Если kind='tg', синхронизирует users.tg_id (двухстороннее).
        Если kind='vk', шифрует tokens через Fernet.
        """
        ...

    async def link_existing(
        self, db: AsyncSession, current_user_id: int, link_token: str, kind: str, value: str,
        vk_access_token: str | None = None, ...
    ) -> IdentityLink:
        """Через одноразовый link_token (защита от хайджека)."""
        ...
```

### 5.6. `app/services/auth/link_token_service.py`

```python
class LinkTokenService:
    TTL_MINUTES: int = 5

    async def issue(self, db: AsyncSession, user_id: int) -> str:
        """Random 32-byte token, store hash + expires_at; return raw."""
        ...

    async def consume(self, db: AsyncSession, raw_token: str) -> int:  # user_id
        """Atomic consume; raise InvalidLinkToken / LinkTokenExpired / LinkTokenAlreadyUsed."""
        ...
```

### 5.7. `app/services/audit_service.py`

```python
class AuditService:
    async def log(
        self, db: AsyncSession, event_type: str, user_id: int | None,
        ip: str | None = None, user_agent: str | None = None,
        details: dict | None = None,
    ) -> None:
        """Append-only INSERT."""
        ...
```

### 5.8. `app/services/fernet_service.py`

```python
class FernetService:
    """Wrapper для Fernet encryption (VK access_token)."""

    def __init__(self, master_key: str):
        self._fernet = Fernet(master_key.encode())

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        return self._fernet.decrypt(ciphertext).decode()
```

### 5.9. `app/services/rate_limit_service.py`

```python
class RateLimiter:
    """Redis-based fixed-window rate limiter."""

    async def check(
        self, redis: Redis, key: str, limit: int, window_sec: int,
    ) -> bool:
        """Returns True если запрос в лимите. Increment + EXPIRE."""
        ...

# Использование:
# await rate_limiter.check(redis, f"magic_link:email:{email.lower()}", 3, 60)
# await rate_limiter.check(redis, f"magic_link:ip:{ip}", 10, 60)
```

## 6. API эндпоинты (полная спецификация)

### 6.1. `POST /api/v1/auth/magic-link/request`

```http
POST /api/v1/auth/magic-link/request
Content-Type: application/json

{ "email": "user@example.ru" }

Response 200 (constant-time):
{ "ok": true, "message": "Если email зарегистрирован, ссылка отправлена" }

Response 429 (rate limit):
{ "error": "rate_limit_exceeded", "retry_after_seconds": 60 }
```

### 6.2. `GET /api/v1/auth/magic-link/consume?token=...`

```http
GET /api/v1/auth/magic-link/consume?token=<raw_token>

Response 200: 302 Redirect → /courses
  Set-Cookie: lms_session=<session_token>; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=2592000
  Set-Cookie: lms_refresh=<refresh_token>; HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth/session/refresh

Response 400:
{ "error": "token_invalid" | "token_expired" | "token_already_used" }
```

### 6.3. `POST /api/v1/auth/tg/init`

```http
POST /api/v1/auth/tg/init
Content-Type: application/json

{ "init_data": "auth_date=...&hash=...&user=..." }

Response 200:
{
  "user": { "id": 42, "full_name": "Виктор", "tg_id": 12345, "identities": [...] },
  "access_token": "<bearer>",
  "refresh_token": "<bearer>",
  "expires_in": 900
}

Response 401:
{ "error": "init_data_invalid" | "init_data_expired" }
```

### 6.4. `POST /api/v1/auth/vk/callback`

```http
POST /api/v1/auth/vk/callback
Content-Type: application/json

{ "code": "...", "state": "..." }

Response 200: устанавливает cookie + body как magic-link consume
Response 401: { "error": "csrf_state_mismatch" | "code_invalid" | "id_token_invalid" }
```

### 6.5. Дополнительные эндпоинты

- `POST /api/v1/auth/session/refresh` — продление session (rotate)
- `POST /api/v1/auth/logout` — invalidate session
- `GET /api/v1/me` — профиль текущего пользователя
- `POST /api/v1/me/identity/{kind}/link` — привязать вторую identity через `link_token`
- `POST /api/v1/me/attribute-guest` — атрибуция guest_session → текущий user
- `GET /embed-api/courses/{course_uid}/task/{external_uid}?token=…` — read-only для WP embed
- `POST /embed-api/auth/issue` — выпуск URL-token (TTL 5 мин, single-use)

### 6.6. Список endpoints, получающих `Depends(get_current_user)`

Все ниже **должны** получить authentication dependency. Логика: для каждого с `student_id`/`user_id` параметром — проверка `current_user.id == param OR current_user.is_service`:

| Endpoint | Файл | Действие |
|---|---|---|
| `GET /learning/next-item` | learning.py | проверка student_id |
| `POST /learning/materials/{id}/complete` | learning.py | проверка body.student_id |
| `POST /learning/tasks/{id}/start-or-get-attempt` | learning.py | проверка body.student_id |
| `GET /learning/tasks/{id}/state` | learning.py | проверка query student_id |
| `POST /learning/request-help` | learning.py | проверка body.student_id |
| `POST /learning/hint-event` | learning.py | проверка body.student_id |
| `POST /attempts/...` | attempts.py | проверка |
| `POST /attempts/{id}/answers` | attempts.py | проверка через attempt.user_id |
| `GET /attempts/...` | attempts.py | проверка |
| `POST /check/task` | checking.py | stateless — service-only |
| `POST /check/tasks-batch` | checking.py | stateless — service-only |
| `POST /teacher/reviews/claim-next` | teacher_reviews.py | RBAC: teacher only + проверка teacher_id |
| `POST /teacher/reviews/{id}/release` | teacher_reviews.py | RBAC: teacher only + проверка teacher_id |

**Сквозной паттерн:**

```python
@router.get("/next-item")
async def get_next_item(
    student_id: int = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # IDOR check
    if not current_user.is_service and current_user.id != student_id:
        raise HTTPException(403, "Access denied")
    # ... existing logic
```

**Auth dependency (резолв в порядке приоритета):**
1. `lms_session` cookie (web SPW)
2. `Authorization: Bearer` header (TG Mini App)
3. `token` query param (WP embed, только `/embed-api/*`)
4. `X-API-Key` header (service-level — TG_LMS bots, ContentBackbone CLI)

## 7. IDOR sweep test (CI gate)

```python
# tests/test_idor_sweep.py
import json, re, pytest

OPENAPI = json.load(open("docs/openapi.json"))
ID_PARAM_PATTERN = re.compile(r"\{(student_id|user_id|attempt_id|result_id|teacher_id)\}")
QUERY_ID_PARAM = {"student_id", "user_id", "attempt_id", "teacher_id"}

@pytest.fixture
def two_users(db, client):
    return user_a, user_b

@pytest.mark.parametrize("path,method,id_param", _enumerate_endpoints_with_id())
async def test_idor_user_cannot_access_other(client, two_users, path, method, id_param):
    """Проверка: user A не может прочитать данные user B по {param}=B.id."""
    user_a, user_b = two_users
    auth_headers = {"Authorization": f"Bearer {user_a.access_token}"}
    actual_path = path.replace(f"{{{id_param}}}", str(user_b.id))
    response = await client.request(method, actual_path, headers=auth_headers)
    assert response.status_code in (403, 404), \
        f"IDOR vulnerability: {method} {actual_path} returned {response.status_code}"
```

## 8. Конфигурация (.env)

```
# Existing
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/learn
VALID_API_KEYS=bot-key-1,admin-key-1
LOG_LEVEL=INFO

# New for SPW auth
RESEND_API_KEY=re_xxxxxxxxxxxxx
SMTP_FROM=noreply@victor-komlev.ru

MAGIC_LINK_SECRET=<32-byte random base64>
SESSION_SIGNING_KEY=<32-byte random base64>
FERNET_MASTER_KEY=<32-byte url-safe base64 — Fernet.generate_key()>

TG_BOT_TOKEN_FOR_INITDATA=<bot token для верификации initData>
VK_ID_CLIENT_ID=<from id.vk.com app>
VK_ID_CLIENT_SECRET=<from id.vk.com app>
VK_ID_REDIRECT_URI=https://learn.victor-komlev.ru/auth/vk/callback

REDIS_URL=redis://localhost:6379/2     # DB=2; DB=0 занят TG_LMS

CORS_ALLOWED_ORIGINS=http://localhost:3000,https://learn.victor-komlev.ru,https://web.telegram.org,https://victor-komlev.ru,https://www.victor-komlev.ru
```

## 9. Test matrix

| Файл | Покрытие | Mock |
|---|---|---|
| test_migrations.py | upgrade/downgrade roundtrip + state checks | PG testdb |
| test_auth_magic_link.py | request rate limit + consume happy/replay/expired/race | mock Resend |
| test_auth_tg_init.py | HMAC verify happy/invalid/expired + identity_link upsert | mock TG bot token |
| test_auth_vk_callback.py | OAuth happy/CSRF/expired id_token | mock VK API |
| test_session_lifecycle.py | create/refresh/revoke + sliding expiration | — |
| test_identity_linking.py | link_token issue/consume + conflict scenarios | — |
| test_guest_attribution.py | guest attempt → register → атрибуция | — |
| test_idor_sweep.py | IDOR negative matrix | 2 пользователя |

CI gates:
- `pytest -m "not slow"` passes
- `bandit -r app -ll` passes
- IDOR sweep zero failures
- migration roundtrip green

## 10. Критерии готовности (acceptance)

- [ ] Все 5 миграций upgrade + downgrade проходят без ошибок (test_migrations.py зелёный)
- [ ] IDOR sweep на 100% endpoints зелёный
- [ ] E2E magic-link: request → email (mock Resend logs) → consume URL → cookie set → /me возвращает профиль
- [ ] E2E TG init: mock initData → POST /auth/tg/init → Bearer token → /me возвращает профиль
- [ ] E2E VK callback: mock OAuth code → POST /auth/vk/callback → cookie + identity_link с зашифрованным токеном
- [ ] Service-level api_key через `X-API-Key` header — TG_LMS bots работают без модификации
- [ ] audit_event записан для login/logout/identity-change
- [ ] product_event запись работает (партиции созданы)
- [ ] Bandit clean
- [ ] Performance: magic-link request < 200ms; consume < 100ms; tg/init < 150ms

## 11. Rollback procedure

```bash
# 1. Откатить commits
git revert <Y1-merge-commit>..HEAD

# 2. Alembic downgrade
alembic -c alembic.ini downgrade teacher_next_modes_stage39

# 3. Restart service
python run.py
```

`pgcrypto` extension оставить (не вреден).

## 12. Что вынесено в Phase Y-2+

- SPW frontend каркас — Phase Y-2
- `/me/last-position`, `/me/streak` — Phase Y-3 (опц. в Y-1)
- `POST /teacher/reviews/{result_id}/grade` (Phase Y-4)

## 13. Связанные документы (LMS-local)

- [docs/ai/adr/0001-auth-passwordless-multi-identity.md](../ai/adr/0001-auth-passwordless-multi-identity.md) — arch ADR
- [docs/ai/design/teacher-queue-states.md](../ai/design/teacher-queue-states.md) — FSM очереди SA_COM
- [docs/ai/data-model.md](../ai/data-model.md) — схема БД
- [docs/ai/architecture.md](../ai/architecture.md) — архитектура API
