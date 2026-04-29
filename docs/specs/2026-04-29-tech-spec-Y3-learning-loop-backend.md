# Tech-Spec Y-3 (LMS backend): /me/* endpoints + identity linking + M7 индекс

**Дата:** 2026-04-29
**Skill:** /executor-pro (executor) + /tech-spec-composer (формат)
**Статус:** READY for executor (Phase Y-3, backend-only LMS scope)
**Phase:** Y-3 (CB authority spec — `D:\Work\ContentBackbone\docs\tech-specs\tech-spec-Y3-learning-loop-v1.md`)

**Authority spec (cross-project):** `D:\Work\ContentBackbone\docs\tech-specs\tech-spec-Y3-learning-loop-v1.md` v1
**LMS local ADR:** `docs/ai/adr/0001-auth-passwordless-multi-identity.md`
**ContentBackbone ADRs:** ADR-0011 (amended), ADR-0017, ADR-0020, ADR-0021 §«Confirmed registration policy»

**Зависимости:**
- ✅ Phase Y-1 + Y-1.5 + Y-1.5.1 в production (Alembic head: `20260428_060000_m6_tg_sync`)
- ✅ Cross-project memory hub синхронизирован (CHANGELOG 2026-04-28)
- ✅ Redis 7 доступен (для `link_token_service` + `rate_limit_service`); fallback in-memory dict для dev

## 1. Скоуп LMS backend

Этот документ — backend-only LMS-side зеркало CB authority spec §7.2 + §8.1 + §8.2 + §10. Frontend (SPW pages, hooks, восхищения, material viewer, hint UI, identity linking UI) — вне scope этого LMS-spec; см. CB authority §6, §8.3-8.8.

### 1.1. В scope

**БД (1 миграция):**
- M7 `idx_task_results_user_received(user_id, received_at DESC)` — для streak query

**Endpoints (6 новых):**
- `GET  /api/v1/me/identities`
- `GET  /api/v1/me/courses`
- `GET  /api/v1/me/last-position`
- `GET  /api/v1/me/streak`
- `POST /api/v1/auth/link-token/issue`
- `POST /api/v1/me/identity/{kind}/link`

**Расширения существующего:**
- `app/services/auth/identity_link_service.py` — метод `link_existing_user(db, user_id, kind, value, ...)` с savepoint pattern + raise `IdentityConflictError` при overlap
- `app/api/v1/auth/magic_link.py` — `POST /auth/magic-link/send` принимает `link_mode: bool = False` body-параметр (для email linking flow per CB §16 risks); `POST /auth/magic-link/verify` отдаёт `magic_link_token` для дальнейшего consume в `/me/identity/email/link` (без создания сессии в link_mode)
- `app/services/audit_service.py` — расширение enum событий: `auth.link_token.issued`, `auth.identity.linked`, `auth.identity.linked.conflict`

**Новые сервисы:**
- `app/services/auth/link_token_service.py` — issue/consume one-time link_token с TTL 5 мин (Redis или in-memory fallback)

**Технический долг (cleanup):**
- Файл `app/services/auth/link_token_service.py` сейчас содержит функцию `attribute_guest_session` (Y-1 misnaming). Шаг 0: переименовать `link_token_service.py` → `guest_attribution_service.py`, обновить 3 импорта (vk.py / magic_link.py / tg.py), затем создать новый `link_token_service.py` с правильным содержимым.

### 1.2. Вне scope (этого LMS-spec)

- SPW frontend (CB §6, §8.3-8.8)
- TG_LMS изменения (Y-3 их не трогает)
- ContentBackbone изменения (Stream X — Y-5)
- SA_COM (Y-4)
- Guest mode (Y-5)
- WP embed (Y-5)
- TG WebApp MainButton (frontend, Y-3 SPW)

### 1.3. Что не трогать

- `users` schema (Y-1.5 финализирована)
- Существующие endpoints `learning/*`, `attempts/*`, `tasks/*`, `materials/*` — Y-3 только потребляет
- `task_results.review_claim_*` поля (для Y-4 SA_COM teacher queue)
- `materials` schema — Y-3 только читает
- Auth model (Y-1 + Y-1.5 финализированы); `current_user` dependency сохраняет паттерн
- TG_LMS api_client (`get_user_by_tg` → `users.tg_id`; sync уже сделана в Y-1.5)

## 2. Архитектура изменений

```
app/
├── api/v1/
│   ├── me.py                              # MOD: +4 endpoint (identities/courses/last-position/streak) +1 endpoint (POST /me/identity/{kind}/link)
│   ├── auth/
│   │   ├── magic_link.py                  # MOD: link_mode=True ветвь (без создания сессии)
│   │   └── link_token.py                  # NEW: POST /auth/link-token/issue
├── services/
│   ├── me_service.py                      # NEW: get_identities/get_courses_with_progress/get_last_position/get_streak
│   ├── learning_engine_service.py         # MOD: +compute_user_courses_with_progress(user_id) batched
│   ├── audit_service.py                   # MOD: enum extension
│   └── auth/
│       ├── guest_attribution_service.py   # NEW (rename из link_token_service.py)
│       ├── link_token_service.py          # NEW (replace, реальный link_token)
│       └── identity_link_service.py       # MOD: +link_existing_user
├── schemas/
│   ├── me.py                              # MOD: +IdentityRead, CourseWithProgressRead, LastPositionRead, StreakRead
│   └── auth.py                            # MOD: +LinkTokenIssueRequest/Response, IdentityLinkRequest (kind union), IdentityLinkResponse
└── db/migrations/versions/
    └── 20260429_010000_M7_task_results_user_received_idx.py   # NEW
tests/
├── test_me_endpoints.py                       # NEW
├── test_streak_logic.py                       # NEW
├── test_link_token_service.py                 # NEW
├── test_identity_link_existing_user.py        # NEW
├── test_me_courses_progress.py                # NEW
├── test_last_position.py                      # NEW
├── test_identity_link_full_flow.py            # NEW
└── test_migrations.py                         # MOD: +M7 upgrade/downgrade roundtrip
```

## 3. Стек и стандарты

| Параметр | Значение |
|---|---|
| Python | 3.10+ |
| FastAPI / SQLAlchemy 2.x async / Alembic | без bump |
| PG | 13+ (текущий) |
| Redis | 7, для `link_token_service`; fallback in-memory dict для dev (документировать) |
| Type hints | обязательны |
| Docstrings | RU |
| `logging` | вместо print |
| Encoding | UTF-8 без BOM |

## 4. Обязательные правила

- **Backsync контракта в same commit:** контракт mirror `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md` обновляется в том же PR с merge endpoint (Y-1 ERRORS #1, #2 урок).
- **Atomic identity linking:** INSERT identity_link с validation link_token в одной транзакции через `db.begin_nested()` savepoint pattern (Y-1.5 урок #3 — НЕ `db.rollback()`).
- **TZ explicit на boundary:** `/me/streak` compute ВСЕГДА в `Europe/Moscow` на стороне сервера через `AT TIME ZONE 'Europe/Moscow'`.
- **link_token single-use:** Redis `DEL` после consume — atomic; second consume → 401 invalid.
- **Rate-limit:** `link_token/issue` 10/мин на user (через `rate_limit_service`).
- **Email linking strict:** в режиме `link_mode=True` `magic_link/send` НЕ создаёт пользователя; `magic_link/verify` НЕ создаёт сессию (только подтверждает владение email и возвращает `magic_link_token` для consume через `/me/identity/email/link`).
- **Защита от identity-takeover:** при привязке identity, уже привязанной к другому user — STRICT 409 (без auto-merge), per ADR-0021 §2; orphan email (Y-1.5.1 lesson) → 409 `email_already_linked_to_orphan_user`.
- **Audit events:** для каждой успешной привязки + 409 conflict — событие через `audit_service.log_event`.

## 5. API Endpoints (LMS-side контракты)

> Frontend routes — см. CB authority spec §6 (НЕ путать с API endpoints, ERRORS #2 урок).

### 5.1. `GET /api/v1/me/identities`

- **Auth:** `Depends(require_authenticated)` (current_user)
- **Response 200:** `[{kind: "email"|"tg"|"vk", value_masked: str, created_at: datetime, last_used_at: datetime | null}]`
- **Value masking:**
  - email → first 3 char + `***` + `@<домен>` (e.g. `vic***@gmail.com`); если local-part короче 3 — `***@<домен>`
  - tg → `***` + последние 4 символа (e.g. `***1234`)
  - vk → первые 8 символов + `...` (e.g. `12345678...`)
- **Response 401:** `auth required`
- **Owner:** `/executor-pro` · **Review:** `/pr-review`

### 5.2. `GET /api/v1/me/courses`

- **Auth:** current_user
- **Response 200:**
  ```json
  [{
    "course_id": int, "course_uid": str, "title": str, "order_number": int,
    "progress": {"tasks_total": int, "tasks_done": int, "materials_total": int, "materials_done": int, "percent": int},
    "last_active_at": datetime | null, "is_completed": bool
  }]
  ```
- **Источник данных:** `user_courses WHERE user_id=current.id AND is_active=true`. Для каждого курса:
  - `tasks_total` — count из courses tree (через `course_parents`)
  - `tasks_done` — count `task_results WHERE user_id AND course_id IN tree AND is_correct=true`
  - `materials_total` / `materials_done` — count из `student_material_progress`
  - `last_active_at` — `MAX(received_at)` от task_results + `MAX(updated_at)` от student_material_progress
  - `is_completed` — `student_course_state.state = 'COMPLETED'`
- **Implementation:** новый метод `LearningEngineService.compute_user_courses_with_progress(user_id)` — batched query, без N+1
- **Sort:** `last_active_at DESC NULLS LAST, order_number ASC`
- **Owner:** `/executor-pro` · **Review:** `/pr-review`

### 5.3. `GET /api/v1/me/last-position`

- **Auth:** current_user
- **Response 200:** `{course_id, course_uid, course_title, type: "task"|"material"|"course_completed"|"none", task_id?: int, external_uid?: str, material_id?: int, last_active_at: datetime} | null`
- **Логика:**
  1. Найти `MAX(received_at)` среди `task_results WHERE user_id` и `MAX(updated_at)` среди `student_material_progress WHERE student_id` → определить last activity record
  2. Из record извлечь `course_id` → resolve `course_uid` + `title`
  3. Если course completed → `type='course_completed'`
  4. Иначе вызвать `learning_engine_service.resolve_next_item(user_id)` → next material/task в этом курсе → вернуть его
  5. Если ученик ничего не открывал → response = `null`
- **Owner:** `/executor-pro` · **Review:** `/pr-review`

### 5.4. `GET /api/v1/me/streak`

- **Auth:** current_user
- **Response 200:** `{streak_days: int, last_active_date: date | null, today_active: bool}`
- **Логика (CTE):**
  ```sql
  -- Y-3.1 fix: gap-detection через `d + rn*1d` для DESC-ордера
  -- (для последовательных дней d_n с rn={1..N} результат `d_n + rn*1d` константа).
  -- Исходный шаблон `d - rn*1d` для DESC был математически некорректен —
  -- каждая запись попадала в свою grp → streak всегда =1 (см. ERRORS LMS 2026-04-29 #2).
  WITH active_days AS (
    SELECT DISTINCT (received_at AT TIME ZONE 'Europe/Moscow')::date AS d
    FROM task_results
    WHERE user_id = :user_id
  ),
  numbered AS (
    SELECT d, d + (ROW_NUMBER() OVER (ORDER BY d DESC))::int * INTERVAL '1 day' AS grp
    FROM active_days
  )
  SELECT COUNT(*) AS streak_days, MAX(d) AS last_active_date
  FROM numbered
  WHERE grp = (SELECT MAX(grp) FROM numbered);
  -- Обнуление streak при gap > 1 day выполняется в Python-слое
  -- (me_service.get_streak) после CTE через сравнение с (now() AT TIME ZONE 'Europe/Moscow')::date.
  ```
- **Streak обнуляется** если `last_active_date < today - 1 day` (вчерашний — последний разрешённый gap; today optional).
- **`today_active`:** true если есть task_result с `(received_at AT TIME ZONE 'Europe/Moscow')::date = today`.
- **TZ-handling:** все compute server-side в `Europe/Moscow`; SPW отображает значения как есть, без TZ конверсии.
- **Performance:** требует **M7 индекс**.
- **Owner:** `/executor-pro` · **Review:** `/techlead-code-reviewer` (TZ-критично + race на сегодняшний день)

### 5.5. `POST /api/v1/auth/link-token/issue`

- **Auth:** current_user
- **Body:** `{kind: "email" | "tg" | "vk"}`
- **Response 200:** `{link_token: str, expires_at: datetime}`
  - `link_token` = 32 байта secure random base64url; one-time
- **Storage:** Redis с TTL 5 мин (key `link_token:{sha256(token)}` value `{user_id, kind, issued_at}`); fallback in-memory dict для dev.
- **Side effect:** `audit_event` `auth.link_token.issued` (details: `{kind}`).
- **Rate-limit:** 10/мин на user (Redis-backed).
- **Owner:** `/executor-pro` · **Review:** `/techlead-code-reviewer` (one-time semantic + TTL)

### 5.6. `POST /api/v1/me/identity/{kind}/link`

- **Auth:** current_user
- **Path param:** `kind` ∈ {"email", "tg", "vk"}
- **Body (kind-specific union):**
  - `email`: `{link_token: str, magic_link_token: str}` — `magic_link_token` получен через `/auth/magic-link/send` + `/auth/magic-link/verify` в режиме `link_mode=true`
  - `tg`: `{link_token: str, init_data: str}` — initData verified server-side через существующий `tg_init_service`
  - `vk`: `{link_token: str, code: str, code_verifier: str, device_id: str}` — VK PKCE flow для existing user (см. CB §22.1: `state="link:<token>"` обрабатывается на vk-relay; backend получает уже очищенный `link_token`)
- **Side effect:**
  1. `link_token_service.consume(raw_token)` → atomic Redis DEL → `{user_id, kind} | None` (если invalid/expired/consumed → 401 `invalid_link_token`)
  2. Validate kind-specific payload (HMAC initData / VK exchange / magic_link consume)
  3. Если `(kind, value)` уже существует:
     - Привязан к **current_user** → idempotent success (UPDATE `last_used_at`)
     - Привязан к **другому user** → 409 `identity_conflict`
     - Existing email-only user без identity_link (orphan, Y-1.5.1) → 409 `email_already_linked_to_orphan_user`
  4. Иначе `identity_link_service.link_existing_user(db, user_id=current.id, kind, value, ...)` через savepoint pattern; для `kind='vk'` — Fernet-encrypted token; для `kind='tg'` — двусторонняя sync `users.tg_id` (через существующий `_sync_users_tg_id`)
  5. `audit_event` `auth.identity.linked` с details `{kind, value_masked, source_payload_kind}`
- **Response 200:** `{ok: true, identity: {kind, value_masked, created_at}}`
- **Response 401:** invalid/expired/consumed `link_token`, или kind-specific validation failure
- **Response 409:** identity_conflict (с body аналогичным `/auth/vk/callback`):
  ```json
  {"detail": {"error": "identity_conflict", "conflict_kind": "email_already_linked"|"tg_already_linked"|"vk_already_linked"|"email_already_linked_to_orphan_user", "existing_kinds": ["email","tg",...]}}
  ```
- **Owner:** `/executor-pro` · **Review:** `/techlead-code-reviewer` (security — link_token verify + identity overlap + atomic INSERT)

## 6. M7 Миграция

`app/db/migrations/versions/20260429_010000_M7_task_results_user_received_idx.py`:

```python
"""M7: индекс task_results(user_id, received_at DESC) для streak query.

Revision ID: 20260429_010000_m7_user_received_idx
Revises: 20260428_060000_m6_tg_sync
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa

revision = '20260429_010000_m7_user_received_idx'
down_revision = '20260428_060000_m6_tg_sync'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        'idx_task_results_user_received',
        'task_results',
        ['user_id', sa.text('received_at DESC')],
    )


def downgrade() -> None:
    op.drop_index('idx_task_results_user_received', table_name='task_results')
```

**Не concurrent** в development. Для production (если объём вырастет с текущих ~39 records) — `CREATE INDEX CONCURRENTLY` отдельным non-transactional шагом.

**Owner:** `/executor-pro` · **Review:** `/db-check` pre+post (perf на streak query) → `/pr-review` → `/review-gate`

## 7. Шаги реализации

### 7.1. Cleanup misnaming `link_token_service.py`

1. Rename `app/services/auth/link_token_service.py` → `guest_attribution_service.py` (содержимое не меняется)
2. Update 3 импорта:
   - `app/api/v1/auth/vk.py`
   - `app/api/v1/auth/magic_link.py`
   - `app/api/v1/auth/tg.py`
3. Update `tests/test_guest_attribution.py` импорт (если есть)
4. Smoke: `pytest tests/test_guest_attribution.py -v` зелёный

### 7.2. M7 миграция

`/db-check` pre: проверить отсутствие индекса `idx_task_results_user_received`; explain analyze streak query (без индекса — full scan).

Создать миграцию (см. §6); `alembic upgrade head` → `alembic downgrade -1` → `alembic upgrade head`.

`/db-check` post: проверить наличие индекса; explain analyze streak query (должен использовать index scan).

### 7.3. `link_token_service.py` (новый, замещающий)

Файл `app/services/auth/link_token_service.py`:

```python
"""Сервис one-time link_token для привязки identity к существующему user (Y-3).

См. ADR-0021 §«Confirmed registration policy» и tech-spec Y-3 §5.5.
"""
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Literal

logger = logging.getLogger(__name__)

LinkTokenKind = Literal["email", "tg", "vk"]
TTL = timedelta(minutes=5)


class LinkTokenError(Exception):
    """Базовая ошибка консьюмации link_token."""
    def __init__(self, reason: Literal["invalid", "expired", "consumed"]) -> None:
        self.reason = reason
        super().__init__(f"link_token_{reason}")


@dataclass(frozen=True)
class LinkTokenPayload:
    user_id: int
    kind: LinkTokenKind
    issued_at: datetime


async def issue(user_id: int, kind: LinkTokenKind) -> tuple[str, datetime]:
    """Выпустить one-time link_token. Возвращает (raw_token, expires_at)."""
    raw = secrets.token_urlsafe(32)
    key = sha256(raw.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + TTL
    payload = LinkTokenPayload(user_id=user_id, kind=kind, issued_at=datetime.now(timezone.utc))
    await _store(key, payload, ttl_seconds=int(TTL.total_seconds()))
    return raw, expires_at


async def consume(raw_token: str) -> LinkTokenPayload:
    """Atomic GET+DEL. Возвращает payload или raise LinkTokenError."""
    if not raw_token:
        raise LinkTokenError("invalid")
    key = sha256(raw_token.encode()).hexdigest()
    payload = await _pop(key)
    if payload is None:
        raise LinkTokenError("invalid")  # либо expired, либо consumed; client не различает
    return payload


# _store / _pop — Redis-backed реализация с in-memory fallback (см. rate_limit_service паттерн)
```

Storage backend: переиспользовать паттерн `rate_limit_service` (Redis с fallback). Detail: `_store` использует `SETEX`; `_pop` — Lua-скрипт `local v = redis.call('GET', KEYS[1]); redis.call('DEL', KEYS[1]); return v` для атомарности.

### 7.4. `identity_link_service.link_existing_user`

Новый метод в `app/services/auth/identity_link_service.py`:

```python
async def link_existing_user(
    db: AsyncSession,
    user_id: int,
    kind: IdentityKind,
    value: str,
    *,
    vk_access_token_enc: bytes | None = None,
    vk_refresh_token_enc: bytes | None = None,
    vk_token_expires_at=None,
) -> IdentityLink:
    """Привязать identity к existing user через savepoint.

    Raise IdentityConflictError если (kind, value) уже занят другим user
    или если orphan email (Y-1.5.1 защита).
    """
    normalized = value.lower() if kind == "email" else value

    # 1. Проверить existing identity_link
    existing = await find_identity(db, kind, normalized)
    if existing is not None:
        if existing.user_id == user_id:
            existing.last_used_at = datetime.now(timezone.utc)
            await db.flush()
            return existing
        # 409: identity занят другим user
        existing_kinds = await _kinds_of_user(db, existing.user_id)
        raise IdentityConflictError(
            conflict_kind=f"{kind}_already_linked",
            existing_kinds=existing_kinds,
        )

    # 2. Для email: orphan check (Y-1.5.1)
    if kind == "email":
        orphan_user = await _find_orphan_user_by_email(db, normalized)
        if orphan_user is not None and orphan_user.id != user_id:
            raise IdentityConflictError(
                conflict_kind="email_already_linked_to_orphan_user",
                existing_kinds=[],
            )

    # 3. INSERT через savepoint (Y-1.5 lesson #3)
    async with db.begin_nested():
        link = await upsert_identity(
            db, user_id, kind, normalized,
            vk_access_token_enc=vk_access_token_enc,
            vk_refresh_token_enc=vk_refresh_token_enc,
            vk_token_expires_at=vk_token_expires_at,
        )
    return link
```

### 7.5. `magic_link_service` extension под `link_mode`

В `app/api/v1/auth/magic_link.py`:
- `POST /auth/magic-link/send` принимает body `{email, link_mode: bool = False}`. При `link_mode=True` НЕ выполняется `get_or_create_user_by_email`; письмо отправляется с QSP `link_mode=1` (для UX страницы consume).
- `POST /auth/magic-link/verify` принимает body `{token, link_mode: bool = False, guest_session_id?}`. При `link_mode=True`:
  - НЕ создаёт user (если ещё не существует — 401 `email_unknown_in_link_mode`)
  - НЕ создаёт session
  - Возвращает `{magic_link_token: <token>, email_verified: true}` (тот же `token`, помеченный как valid для последующего consume в `/me/identity/email/link`)
  - НЕ помечает `magic_link.consumed_at` (consume произойдёт в `/me/identity/email/link`)

Все изменения backwards-compatible: `link_mode=False` по умолчанию → существующее поведение Y-1.5 не меняется.

### 7.6. 4 `/me/*` endpoints

- Реализация в `app/api/v1/me.py` через расширение существующего `router = APIRouter(prefix="/me", tags=["me"])`
- Сервисный слой: `app/services/me_service.py` — методы `get_identities`, `get_courses_with_progress`, `get_last_position`, `get_streak`
- Pydantic схемы: `app/schemas/me.py` — `IdentityRead`, `CourseWithProgressRead`, `LastPositionRead`, `StreakRead`
- `compute_user_courses_with_progress` — новый метод в `learning_engine_service.py`, batched query через CTE (избегаем N+1)

### 7.7. `POST /api/v1/auth/link-token/issue` + `POST /api/v1/me/identity/{kind}/link`

- `app/api/v1/auth/link_token.py` (новый файл): `POST /auth/link-token/issue` (см. §5.5)
- `app/api/v1/me.py`: `POST /me/identity/{kind}/link` (см. §5.6) — 3 ветви по kind

### 7.8. Аудит-события

В `app/services/audit_service.py` — добавить enum константы (если они там определены) или просто использовать новые event_type строки:
- `auth.link_token.issued`
- `auth.identity.linked`
- `auth.identity.linked.conflict`

### 7.9. Backsync OpenAPI + cross-project memory

После merge endpoint:
1. `python -m app.cli.gen_openapi` (или эквивалент) → обновить `docs/openapi.json`
2. Обновить `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md` — добавить 6 новых endpoints
3. Обновить `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-db-schema.md` — Alembic head → `m7_user_received_idx`
4. Append в `D:\Work\ContentBackbone\docs\cross-project\CHANGELOG.md`
5. Update `D:\Work\ContentBackbone\docs\cross-project\STATE.md` — Y-3 backend phase status
6. `git add docs/cross-project && git commit -m "cross-project: LMS Y-3 backend"` в **ContentBackbone**

## 8. Tests

**Unit:**
- `tests/test_me_endpoints.py` — happy + edge для `/identities`, `/courses`, `/last-position`, `/streak`
- `tests/test_streak_logic.py` — TZ Europe/Moscow, gap=1 OK, gap=2 reset, today_active flag, edge: пустой задач, single day
- `tests/test_link_token_service.py` — issue/consume/expired/already_consumed/invalid + Redis vs in-memory parity
- `tests/test_identity_link_existing_user.py` — happy (email/tg/vk) + 409 conflict (each kind) + orphan email + idempotent (current_user) + Fernet for VK

**Integration:**
- `tests/test_me_courses_progress.py` — multi-course tree, progress correct, no N+1 (verify через query log/profiler)
- `tests/test_last_position.py` — task vs material, current course vs other, course_completed
- `tests/test_identity_link_full_flow.py` — issue token → consume → identity_link created → audit_event written
- `tests/test_migrations.py` extension — M7 upgrade + downgrade roundtrip

**Owner:** `/qa-fix` · **Review:** `/review-gate`

## 9. Acceptance criteria

- [ ] M7 миграция apply + downgrade roundtrip зелёные
- [ ] Все 4 `/me/*` endpoint и 2 linking endpoint имеют тесты + manual smoke
- [ ] `/me/streak` корректно считает streak в Europe/Moscow с edge cases (gap=1, gap=2, today)
- [ ] `/me/courses` возвращает progress без N+1 (verified через query log)
- [ ] `/me/last-position` корректно для всех 3 случаев (никогда не открывал / открыт task / course_completed)
- [ ] Identity linking 409 conflict path работает (existing user → 409, orphan email → 409, current_user → 200 idempotent)
- [ ] `/auth/link-token/issue` rate-limit 10/мин enforced
- [ ] link_token single-use: повторный consume → 401
- [ ] magic-link `link_mode=True` НЕ создаёт user/session
- [ ] Audit events записаны для каждой успешной привязки + 409 conflict + token issue
- [ ] `pytest tests/ -m "not slow" -v` зелёный
- [ ] `bandit -r app/ -ll` без HIGH severity
- [ ] OpenAPI `docs/openapi.json` regenerated
- [ ] Cross-project memory backsync: `contracts/lms-api.md` + `lms-db-schema.md` + `CHANGELOG.md` + `STATE.md` updated в одном PR

## 10. Команды проверки

```bash
cd D:\Work\LMS

# Миграция
alembic upgrade head
alembic downgrade -1
alembic upgrade head

# Тесты (focused Y-3)
pytest tests/test_me_endpoints.py tests/test_streak_logic.py tests/test_link_token_service.py tests/test_identity_link_existing_user.py tests/test_me_courses_progress.py tests/test_last_position.py tests/test_identity_link_full_flow.py -v

# Полный прогон (не slow)
pytest tests/ -m "not slow" -v

# SAST
bandit -r app/ -ll

# DB проверка через MCP postgresql (read-only)
# - SELECT indexname FROM pg_indexes WHERE indexname='idx_task_results_user_received'
# - EXPLAIN ANALYZE <streak query> для тестового user_id

# Manual smoke (live)
# 1. uvicorn app.main:app --reload
# 2. login через magic-link → token → curl с Bearer
# 3. GET /api/v1/me/identities → проверить masking
# 4. GET /api/v1/me/streak → проверить today_active
# 5. POST /api/v1/auth/link-token/issue {kind:"vk"} → получить token
# 6. POST /api/v1/me/identity/vk/link {link_token, code, code_verifier, device_id} → mock VK answer или real
```

## 11. Артефакты review-gate

- `D:\Work\LMS\reviews\2026-MM-DD-y3-backend.md` (markdown с заголовком, контекстом, начало diff)
- `D:\Work\LMS\reviews\2026-MM-DD-y3-backend.diff` (`git diff main...HEAD`)
- `D:\Work\LMS\reviews\evidence\2026-MM-DD-y3-pytest.log`
- `D:\Work\LMS\reviews\evidence\2026-MM-DD-y3-bandit.log`
- `D:\Work\LMS\reviews\evidence\2026-MM-DD-y3-db-check-pre.md` (explain analyze без индекса)
- `D:\Work\LMS\reviews\evidence\2026-MM-DD-y3-db-check-post.md` (explain analyze с индексом)

## 12. Риски и откат

| Риск | Митигация |
|---|---|
| `/me/streak` query slow на >100k task_results | M7 индекс + кэш на TanStack Query staleTime=300сек на стороне SPW |
| Race на link_token consume (две вкладки) | atomic Redis Lua-скрипт GET+DEL; second consume → 401 |
| Identity linking через magic-link для linking | LMS magic_link различает «register» vs «link» через `link_mode: bool` параметр; backsync mirror |
| Misnamed `link_token_service.py` rename ломает Y-1 auth flow | сначала переименовать с обновлением 3 импортов + smoke `test_guest_attribution.py`, и только потом создавать новый файл |
| Redis недоступен в dev | `link_token_service` имеет in-memory dict fallback (см. `rate_limit_service` паттерн) |
| `compute_user_courses_with_progress` падает на пустом `course_parents` | unit test с курсом без parents → пустое tree → вернуть progress 0/0 |

**Rollback:**
- `alembic downgrade -1` (M7 → M6) — индекс снимается
- revert LMS Y-3 commits — 6 новых endpoint исчезают; SPW Y-3 frontend начнёт показывать 404 на новых маршрутах (приемлемо т.к. SPW Y-3 frontend ещё не в production)

## 13. Skill-routing summary (LMS-side)

| Под-задача | Главный | Ревью |
|---|---|---|
| 7.1 cleanup rename link_token_service.py | `/executor-lite` | `/pr-review` |
| 7.2 M7 миграция | `/executor-pro` | `/db-check` pre+post → `/pr-review` → `/review-gate` |
| 7.3 link_token_service.py (новый) | `/executor-pro` | `/techlead-code-reviewer` (one-time + TTL) |
| 7.4 identity_link_service.link_existing_user | `/executor-pro` | `/techlead-code-reviewer` (savepoint + 409) |
| 7.5 magic_link link_mode | `/executor-pro` | `/pr-review` |
| 7.6 4 `/me/*` endpoints | `/executor-pro` | `/pr-review` (3 шт) + `/techlead-code-reviewer` (для `/streak`) |
| 7.7 link-token/issue + identity/{kind}/link | `/executor-pro` | `/techlead-code-reviewer` (security) |
| 7.8 audit_service enum | `/executor-lite` | `/pr-review` |
| 7.9 OpenAPI + cross-project memory | `/executor-pro` | `/context-auditor` |
| Tests (unit + integration) | `/qa-fix` | `/review-gate` |
| Финальный merge | — | `/review-gate` (12 измерений) + `/context-auditor` |

Cross-cutting:
- `/encoding-guard` — после правок RU-текстов в audit/spec
- `/db-check` — pre+post M7 миграции (perf на streak query)
- `/context-auditor` — перед финальным merge

## 14. Связанные документы

- [CB tech-spec Y-3 (authority)](../../../ContentBackbone/docs/tech-specs/tech-spec-Y3-learning-loop-v1.md)
- [LMS tech-spec Y-1](2026-04-27-tech-spec-Y1-auth-extension.md)
- [LMS ADR-0001](../ai/adr/0001-auth-passwordless-multi-identity.md)
- [CB ADR-0021](../../../ContentBackbone/docs/adr/0021-user-auto-registration-unified-flow.md)
- [LMS learning-engine-next-item.md](../learning-engine-next-item.md)
- [LMS frontend-contract-sa-com.md](../frontend-contract-sa-com.md)
- [Cross-project contracts/lms-api.md](../../../ContentBackbone/docs/cross-project/contracts/lms-api.md)
- [Cross-project contracts/lms-db-schema.md](../../../ContentBackbone/docs/cross-project/contracts/lms-db-schema.md)

---

**Размер:** L (5-8 рабочих дней backend-only). Если оператор видит риск перерасхода — разбить на Y-3-be-a (cleanup §7.1 + M7 §7.2 + 4 `/me/*` §7.6 + tests) + Y-3-be-b (linking flow §7.3-7.5, §7.7-7.8 + tests + cross-project memory). По умолчанию **рекомендуется** одно слияние для целостности (4 `/me/*` без linking всё равно работают, но linking без 4 `/me/*` бесполезен — UI его не покажет).

**Готовность к executor:** ТЗ полный; пометки исполнителей расставлены; чек-лист предзапуска готов; учтены все 4 урока из LMS ERRORS (#1 синхронизация контракта, #2 разделение фронт/API, #3 savepoint вместо rollback, #4 защита от orphan email).
