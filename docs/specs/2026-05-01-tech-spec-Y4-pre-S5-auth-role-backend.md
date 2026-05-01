# Tech-Spec Y-4 pre-S5 (LMS-side): auth role auto-assign + service API key + test session endpoint

**Дата:** 2026-05-01
**Phase:** Y-4 pre-S5 (блокирует Stage S5 в SPW)
**Источник:** [CB authority Y-4 pre-S5 spec](../../../ContentBackbone/docs/tech-specs/tech-spec-Y4-LMS-pre-S5-auth-role-v1.md)
**Статус:** ✅ DONE 2026-05-01
**Alembic head на старт:** `m9_zombie_sanitize`
**Alembic head после:** `m10_role_backfill`

---

## 1. Цель Stage S-PRE

Закрыть три pre-S5 риска перед запуском Y-4 E2E-приёмки:

1. **Bug fix:** при auto-registration пользователю не присваивалась роль
   `student` в `user_roles` → 1147 / 1158 users без ролей (verified MCP),
   включая `users.id=142, victor.v.komlev@gmail.com`. RBAC-проверки
   деградируют.
2. **Service config verification:** `VALID_API_KEYS` (используется TG_LMS
   поллером и SPW E2E live spec'ами).
3. **E2E bootstrap:** `POST /auth/test/issue-session` — служебный endpoint
   для выдачи cookie тестовому студенту в Playwright spec'ах.

## 2. Расхождения с CB authority — адаптации

При pre-flight check выявлены **4 расхождения**, согласованы с оператором
2026-05-01 (Q-A1..Q-A5):

| # | CB authority | Реальность LMS | Адаптация |
|---|---|---|---|
| 1 | `user_roles(user_id, role STRING)` | composite PK `(user_id INT, role_id INT)` + FK на `roles` (id=4 → 'student') | M10 SQL через JOIN на roles, ON CONFLICT (user_id, role_id) |
| 2 | `users.is_service: bool` колонка | `is_service` — runtime-атрибут `CurrentUser`, не stored | Backfill всем users без роли (1147); service-key auth не создаёт user row |
| 3 | `LMS_SERVICE_API_KEY` (single env) | `VALID_API_KEYS=k1,k2,...` (multi-list) | Переиспользуем существующий механизм; constant-time через `secrets.compare_digest` в test-endpoint |
| 4 | `app/api/dependencies/auth.py::get_current_user` | Реально в `app/api/deps.py:50` | Используем правильный путь |
| 5 | self-heal commit | Outer transaction может конфликтовать | `try/except` soft-fail: лог warning + `db.rollback()`; auth НЕ блокируется |
| 6 | M9 имя занято под Y-4.2 zombie sanitize | M10 `m10_role_backfill` (≤32 chars) | Нумерация продолжается |

## 3. API Endpoints

### 3.1. Существующие — расширены side-effect'ом

| Endpoint | Изменение |
|---|---|
| `POST /auth/magic-link/verify` | Auto-assign student в той же savepoint-транзакции с user INSERT |
| `POST /auth/tg/init` | Аналогично |
| `POST /auth/vk/callback` | Аналогично |
| `Depends(get_current_user)` | Defensive self-heal student role для legacy-юзеров (soft-fail) |

### 3.2. Новый endpoint

#### `POST /api/v1/auth/test/issue-session`

- **Auth:** `X-API-Key` header, constant-time `secrets.compare_digest` против `valid_api_keys`
- **Body:** `{user_id: int}`
- **Validation:** `settings.env in {"dev","test"}` AND `settings.test_endpoints_enabled=True`; иначе **404** (path-as-disabled, fail-fast до обработки body)
- **Side-effect:**
  1. Defensive self-heal student role (если у user нет ролей)
  2. `session_service.create_session(user_id, ua_fingerprint)` + override `expires_at = now + 3600s`
  3. Audit `auth.test.session_issued` (БЕЗ значения cookie / API-key)
- **Response 200:** Set-Cookie `session=<access_token>; HttpOnly; Secure=cookie_secure; SameSite=Lax; Max-Age=3600` + JSON `{user_id, expires_at, message: "Test session issued"}`
- **Response 401:** Invalid X-API-Key
- **Response 403:** target user (sub-case если есть `users.is_service`-аналог в будущем; в текущей реализации — N/A)
- **Response 404:** path disabled OR user не существует
- **TTL:** 1 час (Q5.2 — снижение blast-radius при утечке cookie)

## 4. Schema / migration

### M10 миграция — backfill `student` для users без ролей

`app/db/migrations/versions/20260501_010000_M10_role_backfill.py`:
- `revision = "m10_role_backfill"`, `down_revision = "m9_zombie_sanitize"`
- **Upgrade:**
  ```sql
  INSERT INTO user_roles (user_id, role_id)
  SELECT u.id, (SELECT id FROM roles WHERE name = 'student')
  FROM users u
  LEFT JOIN user_roles ur ON ur.user_id = u.id
  WHERE ur.user_id IS NULL
  ON CONFLICT (user_id, role_id) DO NOTHING
  ```
- **Pre-flight count (verified MCP 2026-05-01):** 1147 users без роли (из 1158).
- **Post-flight (verified):** 0 users без роли; user_id=142 → `student`.
- **Идемпотентна** — повторный upgrade match'ит 0 строк.
- **Downgrade no-op** — невозможно отделить M10-rows от ручных INSERT.

### Audit constants (`app/services/audit_service.py`)

```python
STUDENT_ROLE_AUTO_ASSIGNED = "student.role.auto_assigned"
AUTH_ROLE_MISSING_SELF_HEALED = "auth.role.missing_self_healed"
AUTH_TEST_SESSION_ISSUED = "auth.test.session_issued"
```

### Pydantic schemas (`app/schemas/auth_test.py`)

```python
class TestIssueSessionRequest(BaseModel):
    user_id: int = Field(..., gt=0)

class TestIssueSessionResponse(BaseModel):
    user_id: int
    expires_at: datetime
    message: Literal["Test session issued"]
```

### Settings (`app/core/config.py`)

```python
self.test_endpoints_enabled: bool = os.getenv("TEST_ENDPOINTS_ENABLED", "false").lower() in ("true","1","yes")
self.cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() in ("true","1","yes")
```

## 5. Сервисы

### `app/services/auth/role_assign_service.py` (новый)

```python
async def ensure_student_role(db, user_id, *, channel, origin) -> bool:
    """Idempotent: SELECT ролей user → если пусто, INSERT student
    + audit_event (тип = STUDENT_ROLE_AUTO_ASSIGNED для auto_registration
    / AUTH_ROLE_MISSING_SELF_HEALED для defensive_self_heal)."""
```

Используется:
- 3 auth-сервисами (magic_link, tg_init, vk_oauth) — channel ∈ {magic_link, tg_init, vk_callback}, origin=auto_registration
- `get_current_user` defensive self-heal — channel='get_current_user_defensive', origin='defensive_self_heal'
- `auth/test/issue-session` — channel='auth_test_session', origin='test_session_issue'

### `_self_heal_student_role` в `app/api/deps.py`

```python
async def _self_heal_student_role(db, user_id):
    """Soft-fail: на любую ошибку — log warning + rollback; auth не блокируется."""
    try:
        from app.services.auth.role_assign_service import ensure_student_role
        if await ensure_student_role(db, user_id, channel='get_current_user_defensive', origin='defensive_self_heal'):
            await db.commit()
    except Exception:
        log.warning(...)
        await db.rollback()
```

## 6. Тесты

См. §10.

## 7. Acceptance criteria

- [x] M10 upgrade + downgrade зелёные (roundtrip OK)
- [x] Pre-flight count verified: 1147 users без роли → 0 после M10
- [x] `users.id=142, victor.v.komlev@gmail.com` имеет `user_roles.role='student'`
- [x] 3 auth-канала (magic_link/tg_init/vk_callback) auto-assign в той же транзакции
- [x] `get_current_user` defensive self-heal работает для legacy + service skip
- [x] `POST /auth/test/issue-session`: 200 в dev+flag=true; 404 в других случаях; 401 invalid X-API-Key; 404 user not found
- [x] Audit events созданы для трёх новых типов
- [x] `.env.example` с обоими новыми переменными
- [x] `docs/operations/service-api-key.md` создан
- [x] Cross-project mirror обновлён same-commit

## 8. Operator solutions (зафиксированы 2026-05-01)

- **Q-A1 = ADAPT:** M10 через JOIN на roles WHERE name='student'
- **Q-A2 = NO-FILTER:** `is_service` не фильтруется в M10 (нет колонки)
- **Q-A3 = REUSE:** `VALID_API_KEYS` переиспользуется (constant-time compare)
- **Q-A4 = SOFT-FAIL:** self-heal try/except + log warning
- **Q-A5 = SINGLE:** одна миграция (1147 → single UPDATE)

## 9. Связанные документы

- [CB authority pre-S5 spec](../../../ContentBackbone/docs/tech-specs/tech-spec-Y4-LMS-pre-S5-auth-role-v1.md)
- [docs/operations/service-api-key.md](../operations/service-api-key.md)
- [Y-4 backend spec](2026-04-30-tech-spec-Y4-sa-com-teacher-queue-backend.md)
- [Y-4.2 R-3 fix spec](2026-04-30-tech-spec-Y4.2-claim-next-filter-fix.md)

---

**Готовность:** все acceptance criteria выполнены; разблокирован Y-4 Stage S5 в SPW.
