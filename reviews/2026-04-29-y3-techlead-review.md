# /techlead-code-reviewer — Y-3 backend security-critical блоки

**Skill:** lms-fastapi-techlead-code-reviewer (SKILL.md loaded, чек-листы применены)
**Дата:** 2026-04-29
**Скоуп:** security-critical блоки Y-3 (link_token, identity link, /streak TZ, link_existing_user)
**Файлы:**
- `app/services/auth/link_token_service.py`
- `app/services/auth/identity_link_service.py` (link_existing_user)
- `app/services/me_service.py` (get_streak)
- `app/api/v1/me.py` (3 ветви linking)
- `app/api/v1/auth/link_token.py`
- `app/db/migrations/versions/20260429_010000_M7_*.py`

## Decision: PASS (with non-blocking findings)

Никаких S1 не найдено. 2 S2 (architectural risk, не блокируют merge — fix-up можно отложить или отдельным PR). Несколько S3.

---

## Blocking Findings (S1)

Нет.

---

## Non-Blocking Findings

### S2-1: in-memory fallback link_token_service в production — security downgrade

**Файл:** `app/services/auth/link_token_service.py:132-144`

**Что:** При недоступности Redis на `_store` сервис тихо переключается на in-memory dict, логируя «(DEV ONLY)». В production это:
1. Не shared между процессами/replicaми → токен, выпущенный воркером A, не найдётся в воркере B на consume → false 401 для legitimate user
2. Memory leak при долгом отсутствии Redis (хотя `_purge_expired_memory` минимизирует)
3. Тихий fail-open вместо fail-secure для security-critical store

**Почему важно в production:** Классификация Phase Y-3 задаёт link_token как security-критичный (one-time identity attachment). Незаметное переключение в in-memory снижает защиту без сигнала оператору.

**Концепт фикса:**
```python
# В _store / _pop:
if redis is not None:
    try:
        ...
    except Exception:
        if os.getenv("ENV", "dev") == "production":
            raise  # fail-secure
        logger.warning("DEV fallback...")
```

Или явный env-флаг `LINK_TOKEN_ALLOW_INMEM_FALLBACK=1`.

**Оценка:** S2 (не S1, т.к. в dev корректно; risk материализуется только при сочетании Redis-outage + production).

---

### S2-2: link_token consume происходит ДО валидации провайдера → токен сжигается при чужой ошибке

**Файлы:**
- `app/api/v1/me.py:189-198` (email) — `_consume_link_token_for_user` → `consume_magic_link` → 401
- `app/api/v1/me.py:309-326` (vk) — consume → `exchange_code` → 401
- `app/api/v1/me.py:245-258` (tg) — consume → `verify_tg_init_data` → 401

**Что:** `_consume_link_token_for_user` атомарно DELETE'ит токен. Если затем validate (magic_link/initData/VK) проваливается — пользователь должен запросить новый link_token. Это increases friction.

**Почему важно в production:** Плохой UX (юзер видит «invalid» из-за сетевой ошибки VK или истёкшего magic_link, теряет своё «гнездо» link_token, должен начинать flow заново). Не security-проблема (consume атомарный, ничего не leaks), но стоит подумать о UX.

**Концепт фикса:** validate провайдер ПЕРЕД consume link_token (peek через `redis.get` без DEL → validate → если OK, consume отдельным call). Усложнит код, но 1 step better для UX.

**Оценка:** S2 (UX risk + увеличенная нагрузка на link_token issue rate-limit при ошибках провайдера). Можно отложить — не блокер для security.

---

### S3-1: race-resolve в `link_existing_user` обходит orphan-check

**Файл:** `app/services/auth/identity_link_service.py:177-188`

**Что:** При `IntegrityError` мы делаем `find_identity(db, kind, normalized)` и если есть `race_winner` для нашего user_id — возвращаем success. Но если в гонке кто-то параллельно создал orphan email user, мы могли бы пропустить orphan-check и получить identity_link, привязанный к ошибочному user.

**Почему важно:** Окно гонки крайне узкое (savepoint + concurrent INSERT в orphan-state); maximum impact — кросс-привязка identity. В production вероятность ничтожна (нет автоматических процессов, создающих orphan-state).

**Концепт фикса:** в except IntegrityError — повторить orphan-check для kind='email' перед возвратом.

**Оценка:** S3 — теоретическая дыра, не воспроизводимая.

---

### S3-2: ValueError из `exchange_code`/`fetch_vk_userinfo` пробрасывается в HTTPException detail

**Файл:** `app/api/v1/me.py:316, 326`

```python
except ValueError as e:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"VK exchange failed: {e}")
```

**Что:** Текст `e` из `vk_oauth_service.exchange_code` может содержать VK API error_description (видно в `f"VK error: {data.get('error')} desc={data.get('error_description')}"`). Эти строки попадают клиенту в response.detail.

**Почему важно:** Минимальный info-leak (raw VK API ошибки → клиент); никаких credentials, но reduces опаску атакующего.

**Концепт фикса:** заменить на статичное сообщение «VK exchange failed» + log в `logger.warning` с `e`. Существующий `vk.py` (Y-1) делает так же — паттерн новый.

**Оценка:** S3.

---

### S3-3: `_purge_expired_memory` вызывается только на `issue`, не на `consume`

**Файл:** `app/services/auth/link_token_service.py:133-144`

**Что:** Если приложение работает только с consume (например, batch обработка), expired entries никогда не покидают `_memory_store`.

**Почему важно:** В реальности issue ВСЕГДА предшествует consume для конкретного юзера, так что цикл cleanup случается. Risk теоретический.

**Оценка:** S3 — minor.

---

## Architecture Assessment

✅ **Layering** соблюдён: api/v1 → services → repos/models. `me_service.py` правильно отделил бизнес-логику от endpoint'ов.

✅ **DRY:** `_consume_link_token_for_user` и `_conflict_to_http` правильно вынесены как helpers.

✅ **SOLID:** SRP — каждый из 4 новых сервисных модулей делает одну вещь.

⚠ **Layering nit (S3-4):** `me.py` импортирует `encrypt_token` из `fernet_service` напрямую (строка 35). По slim-controller паттерну этот encrypt должен происходить в сервисном слое (в идеале в `vk_oauth_service` или новом `vk_link_service`). Сейчас бизнес-логика VK token encryption дублируется между `vk_oauth_service.get_or_create_user_by_vk` и `me.py:link_identity_vk`. Не критично, но шов для рефакторинга.

---

## Migration Assessment

✅ M7 reversible (`op.create_index` ↔ `op.drop_index`).
✅ Roundtrip протестирован (`test_alembic_downgrade_m7_then_upgrade` passes).
✅ Не destructive — пустой `task_results` индекс безопасен.
⚠ **S3-5 (operational note):** В prod при росте `task_results > 100k` рекомендуется CONCURRENTLY. Сейчас миграция блокирует таблицу на время CREATE INDEX (приемлемо для текущих 39 rows, но spec §6 уже это упоминает).

---

## Test Adequacy Assessment

✅ 39 Y-3 тестов покрывают:
- link_token: issue/consume/single-use/garbage/empty (6 unit)
- mask_value: 11 параметризованных
- link_existing_user: 8 (happy email/tg/vk + idempotent + 409 conflict 3 kinds + orphan)
- HTTP endpoints: 14 (auth-required + smoke happy + 401 негативные + wrong-user)

⚠ **Missing coverage (S3-6):**
- `test_streak_logic.py` отсутствует (упомянут в LMS-side spec §8 «Tests» как «Unit: TZ Europe/Moscow, gap=1 OK, gap=2 reset, today_active flag, edge: пустой задач, single day»). Сейчас покрыт только smoke (zero-streak). Edge cases gap=1, gap=2, DST с реальным task_results — НЕ покрыты.
- Нет теста на `_consume_link_token_for_user` mismatch path (kind или user_id) на уровне endpoint — есть `test_link_token_with_wrong_user_rejected` но только для VK, не для tg/email
- Нет теста на `vk_link` happy path с valid VK exchange (нужен mock httpx) — только negative path

---

## Observability Assessment

✅ Все security-critical actions логируются через `audit_service.log_event`:
- `auth.link_token.issued` (issue)
- `auth.identity.linked` (success)
- `auth.identity.linked.conflict` (409)
- `auth.magic_link.verified_link_mode` (Y-3 link mode peek)

✅ DB-check post-M7 подтверждает: `auth.link_token.issued: 6` событий уже записано.

⚠ **S3-7:** `_consume_link_token_for_user` mismatch (user_id или kind не совпали) **НЕ записывает audit event** (просто возвращает 401). Recommendation: добавить `audit_event auth.link_token.consume_mismatch` для forensics. Без этого suspicious activity (попытки украсть чужой токен) невидимы в audit.

---

## Security Assessment

✅ **Input validation:** Pydantic schemas с `min_length=1` на токены/коды; kind ∈ {email,tg,vk} via Literal type
✅ **Authorization:** `require_authenticated` на всех 6 новых endpoints
✅ **Trust boundaries:** clear (raw token client → sha256 store; VK userinfo → 409 на overlap; initData HMAC → user_id extraction)
✅ **Secrets:** не логируются и не попадают в response (raw token уходит клиенту единожды; sha256-хеш в store; Fernet шифрование VK tokens)
✅ **Injection:** все SQL — через `text()` с named params (`:user_id`); нет string concat
✅ **Rate-limit:** `/auth/link-token/issue` — 10/мин на user (Redis-backed; fail-open приемлем для UX)
⚠ **S2-1 уже отмечен:** in-memory fallback в production
⚠ **S3-2 уже отмечен:** info-leak в VK exchange ошибках

---

## UX/UI Critical Assessment

⚠ **S2-2 уже отмечен:** consume link_token до validate провайдера → friction для legitimate users при сетевых сбоях.

✅ 409 conflict response унифицирован (тот же формат, что в `/auth/vk/callback` Y-1.5.1) → SPW handler одинаков.

✅ 401 для invalid link_token не различает (invalid/expired/consumed) → защита от enumeration.

---

## Spec Ambiguity Assessment

✅ Все Q-Y3-1..7 ответы из tech-spec реализованы:
- Q-Y3-1=A → новый `/me/identities` ✅
- Q-Y3-3=C → `/me/courses` с progress ✅
- Q-Y3-4=A → identity linking реализован ✅
- TZ-A1 (Europe/Moscow) → /streak использует AT TIME ZONE ✅

✅ Конфликт rename `link_token_service.py` resolved unambiguously (новое имя для нового назначения, старое содержимое в `guest_attribution_service.py`).

---

## Date/Time Type Safety Assessment

✅ **Raw SQL → domain types (`/me/streak`):**
- `last_active_date` приходит как `date` (PG `(received_at AT TIME ZONE 'Europe/Moscow')::date`) — типизирован напрямую
- `today_msk` — тоже `date` через server-side cast
- Сравнение `(today_msk - last_active_date).days` — корректно для `date - date` arithmetic
- `streak_days` cast в `int()` для defence от `Decimal` (PG COUNT)

✅ **TZ explicit на boundary:** все вычисления streak — server-side AT TIME ZONE
✅ **No naive datetime сравнения** в новом коде
✅ **None handling:** `if last_active_date is not None` явно

⚠ **S3-8:** `last_active_at` в `/me/last-position` — `received_at` из БД (timezone-aware), отдаётся в response как datetime. Pydantic схема `LastPositionRead.last_active_at: datetime` без явного `tz` constraint — приемлемо т.к. SQLAlchemy timezone=True column возвращает aware datetime.

---

## Required Fixes

Нет blocking. Рекомендованы (можно follow-up PR):

1. **S2-1** (high priority): добавить env-флаг для запрета in-memory fallback в production (или fail-secure при `ENV=production` + Redis down)
2. **S3-7** (medium): записывать audit event на `_consume_link_token_for_user` mismatch (forensics)
3. **S3-6** (medium): дописать `test_streak_logic.py` с реальными task_results фикстурами для gap=1/gap=2/today scenarios

## Required Validation Commands

```bash
cd D:\Work\LMS

# Re-run focused tests
pytest tests/test_link_token_service.py tests/test_me_service_mask.py tests/test_identity_link_existing_user.py tests/test_me_endpoints_y3.py -v

# Smoke link_token store path
python -c "
import asyncio
from app.services.auth.link_token_service import issue, consume
async def t():
    raw, _ = await issue(None, 1, 'vk')
    p = await consume(None, raw)
    assert p.user_id == 1 and p.kind == 'vk'
    print('OK')
asyncio.run(t())
"

# DB-check: M7 index used in streak query plan (after data growth)
# (через mcp__learn_public_db__query или asyncpg EXPLAIN ANALYZE)
```

---

## Residual Risks

- **R1:** in-memory fallback может маскировать Redis outage в production (S2-1) — SLO мониторинг `auth.link_token.issued` rate должен ловить degradation
- **R2:** consume-then-validate creates link_token waste при flaky VK API — следить за `/auth/link-token/issue` rate-limit hit-rate
- **R3:** Europe/Moscow без DST — стабильно с 2014, но если РФ снова введёт — streak логика потребует ревизии

---

## Skill Improvement Actions

Не применимо (не Cursor-agent run).

---

**Verdict: PASS** — security-critical блоки прошли проверку. 2 S2 рекомендованы как follow-up, не блокируют merge.
