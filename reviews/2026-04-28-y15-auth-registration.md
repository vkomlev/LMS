# Review: Phase Y-1.5 — Auth registration unified flow

## Цель (из tech-spec)

Закрыть user-creation gap из Y-1: any first-time visitor может зарегистрироваться через любой из трёх auth-flow и получить рабочую сессию. Двусторонняя sync `users.tg_id` ↔ `identity_link kind='tg'`. См. [ADR-0021](../../ContentBackbone/docs/adr/0021-user-auto-registration-unified-flow.md), [tech-spec Y-1.5](../../ContentBackbone/docs/tech-specs/tech-spec-Y1.5-auth-registration-v1.md).

## Затронутые файлы

**Service layer:**
- `app/services/auth/identity_link_service.py` — `_sync_users_tg_id` через `IS DISTINCT FROM` (NULL-safe); вызывается из `upsert_identity` для `kind='tg'`
- `app/services/auth/magic_link_service.py` — `get_or_create_user_by_email` (race-safe через partial unique + retry-select)
- `app/services/auth/tg_init_service.py` — `get_or_create_user_by_tg`, `extract_tg_full_name`; fallback `Гость TG-{last4}`; resync `users.tg_id` для existing с устаревшим значением
- `app/services/auth/vk_oauth_service.py` — `IdentityConflictError` (custom exc), `fetch_vk_userinfo` (email + full_name), `get_or_create_user_by_vk` с защитой от identity-takeover

**Routers:**
- `app/api/v1/auth/magic_link.py` — `verify` теперь auto-create вместо 401 при отсутствии identity_link
- `app/api/v1/auth/tg.py` — `init` auto-create + извлечение full_name из initData
- `app/api/v1/auth/vk.py` — `callback` auto-create + 409 `identity_conflict` при VK email overlap

**Migration:**
- `app/db/migrations/versions/20260428_060000_M6_users_tg_id_backfill.py` — backfill двухсторонний; downgrade no-op (forward-only, lossy)

**Schema backsync (drift с M1):**
- `app/schemas/users.py` — `UserRead.email` и `UserCreate.email` → `Optional[EmailStr]` (после M1 nullable; auto-create через TG/VK создаёт users без email)

**Tests:**
- `tests/test_auto_register_magic_link.py` — first-time, idempotent reuse, lowercase normalization, concurrent verify race
- `tests/test_auto_register_tg.py` — full_name из first+last, fallback Гость, sync users.tg_id, resync устаревшего, audit_event
- `tests/test_auto_register_vk.py` — with-email/without-email auto-create, 409 conflict, Fernet roundtrip, token rotation на existing identity
- `tests/test_identity_link_sync.py` — двусторонняя sync включая edge-cases (NULL→value, value→другой value, kind='email' не трогает tg_id)
- `tests/test_migrations.py` — расширен `test_alembic_head_is_m6`, M6 backfill двух типов; локальные NullPool engines per scenario для cross-loop изоляции

**Cross-project memory (CB):**
- `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md` — auto-create + 409 path, плоский `MeResponse`
- `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-db-schema.md` — M6 секция, `users.tg_id` sync
- `D:\Work\ContentBackbone\docs\cross-project\CHANGELOG.md` — Y-1.5 запись
- `D:\Work\ContentBackbone\docs\cross-project\STATE.md` — Phase Y-1 → Y-1.5 complete

**Скрипт:**
- `scripts/cleanup_test_emails.py` — очистка legacy `@example.test` users.email (UPDATE NULL, DELETE identity_link); `audit_event` trigger не позволяет каскадный DELETE

## Регрессионный тест

**Не bugfix, а feature** — поведение фиксируется новыми тестами:
- `test_first_time_email_creates_user_and_identity` — до Y-1.5 вернул бы 401, теперь 200 + user в БД
- `test_first_time_tg_creates_user_with_full_name` — до Y-1.5 вернул бы 404
- `test_first_time_vk_with_email_creates_user` — до Y-1.5 вернул бы 404
- `test_vk_email_conflict_raises_409` — новый negative path (защита от identity-takeover)
- `test_M6_backfill_*` — новая миграция, fresh roundtrip

Все Y-1 тесты остаются зелёными (regression-safe).

## Результаты валидации (после review-gate ОТКЛОНЕНО → fix B1-B4 + NB1)

```
Y-1 + Y-1.5 functional regression: 65 passed, 3 skipped, 7 warnings in 41.37s
Migration roundtrip + M6 backfill:  6 passed, 11 warnings in 22.59s
Bandit security scan (LOW/MEDIUM/HIGH severity):
  app/services/auth/, app/api/v1/auth/ — No issues identified (1 Low игнорируется -ll)
URL-guard:
  grep "victor-komlev.ru|localhost:..." в app/services — только config.py через env ✓
  grep "magic-link/request|consume|/auth/logout" в app/ — пусто ✓
Total: 71 passed (включая B1 regression test), 3 skipped (live smoke gated)
```

## Изменения после review-gate ОТКЛОНЕНО

**B1+B2 fix:** все три `get_or_create_user_by_*` переписаны через `db.begin_nested()`
(SAVEPOINT pattern) вместо `try/db.rollback()`. IntegrityError откатывает только
nested-savepoint — основная транзакция (с magic_link consume / VK token / guest
attribution) сохраняется. Не создаются orphan users при race на UNIQUE(kind,value).

**B1 regression test:** `test_b1_regression_consumed_at_persists_after_savepoint_rollback` —
эмулирует partial unique race через два verify same-email; проверяет что token_b
после второй попытки имеет `consumed_at IS NOT NULL`, и третий verify тем же
токеном даёт 401 (single-use enforced).

**B3 fix:** `tests/test_y15_live_smoke.py` создан с тремя skipped-by-default тестами
(magic-link/tg/vk), gated через `CB_LMS_LIVE_SMOKE_Y15`. Operator handoff в docstring
каждого теста (R-001/R-002/R-003 в operator-runbook).

**B4 fix:** LMS spec §6.2-6.4 + шапка обновлены в этом коммите:
- §6.2: убран «email_not_linked» из 401 enumeration list, добавлены **Side effects**
  с auto-create + savepoint + audit_event.
- §6.3: 404 заменён на side-effect auto-create + sync `users.tg_id`. Добавлены 400
  для невалидного tg_id и phrase «двусторонняя sync».
- §6.4: 404 удалён, добавлен **409 identity_conflict** body schema + side-effect
  auto-create с savepoint + Fernet token rotation на existing identity.
- Шапка: ссылка на ADR-0021 + tech-spec-Y1.5 в `Предшествующие артефакты`.

**NB1 fix:** `pip install bandit` (1.9.4) — bandit gate теперь работает; clean.

## Решения по ходу реализации

1. **`UPDATE … WHERE tg_id != X`** изначально не работало для NULL (NULL != X → NULL → row не matched). Заменил на `Users.tg_id.is_distinct_from(tg_id)` — корректная NULL-семантика.
2. **FERNET_MASTER_KEY** отсутствовал в `.env` для dev — сгенерировал (Fernet.generate_key) и добавил. Production ключ — отдельная operator-задача в password manager (Y-1 spec §15).
3. **`@example.test` Pydantic не принимает** (RFC 6761 special-use TLD) — заменил на `@example.com` в новых тестах + очистил legacy записи через `cleanup_test_emails.py` (UPDATE NULL для users.email — DELETE заблокирован audit_event trigger).
4. **`UserRead.email: EmailStr` required** vs БД nullable после M1 — backsync drift, обнаружен через `test_legacy_api_key_still_works`. Поправил schema.
5. **Migration tests cross-loop pool issue** — module-level `async_session_factory` падал при втором scenario из-за asyncpg + ProactorEventLoop. Заменил на локальный NullPool engine per scenario (паттерн из conftest).

## Rollback note

```powershell
# 1. Откат migration
alembic downgrade 20260428_050000_m5_guest

# 2. Revert commit
git revert <commit-hash>
```

После downgrade auto-create перестанет работать — first-time visitor вернётся к 401/404. Existing users остаются (forward-only данные). `users.tg_id` значения, заполненные M6, сохраняются (lossy downgrade задокументирован).

Cross-project memory обновлять не нужно — он описывает желаемое состояние; для отката достаточно revert mirror commit'а в CB.
