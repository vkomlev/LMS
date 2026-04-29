# /techlead-code-reviewer — финальное PASS/FAIL по Y-3 backend + Y-3.1

**Skill:** techlead-code-reviewer (SKILL.md loaded; чек-листы review-checklist + testing + security + datetime + migration + observability + spec-ambiguity + claude-skills-improvement-loop применены)
**Дата:** 2026-04-29
**Скоуп:** комбинированное состояние Y-3 backend (8 endpoints + M7 + 4 сервиса + 39 тестов) + Y-3.1 follow-ups (fail-secure + audit consume_mismatch + 8 streak tests + SQL bug fix)
**Diff:** 22 файла, +2645/-64 строк
**Артефакты предыдущих ревью этого раунда:**
- `reviews/2026-04-29-y3-backend-review.md` (review-gate первого прохода)
- `reviews/2026-04-29-y3-techlead-review.md` (techlead первого прохода)
- `reviews/2026-04-29-y3-pr-review.md` (pr-review с fix /me/last-position)
- `reviews/2026-04-29-y3-context-auditor.md` (контекст-аудит ALIGNED)
- `reviews/2026-04-29-y3.1-followups.md` (Y-3.1 fastapi-api-developer report)

---

## Decision: **PASS**

**Review Horizon:** `current repository integration-safe` — backend-microstep Y-3 + Y-3.1 готов к merge в `main`. Phase Y-3 в целом (включая SPW frontend) — НЕ complete; фронтенд явно вне scope этой поставки.

---

## Blocking Findings (S1)

Нет.

---

## Non-Blocking Findings (S2)

### S2-A1: Y-3 spec backsync под исправленный streak SQL не выполнен

- **Где:** `D:\Work\LMS\docs\specs\2026-04-29-tech-spec-Y3-learning-loop-backend.md` §5.4 + `D:\Work\ContentBackbone\docs\tech-specs\tech-spec-Y3-learning-loop-v1.md` §7.2.4 — содержат буггованный SQL шаблон `d - rn*1d` с `ORDER BY d DESC`. Код исправлен в `me_service.py:336-345` (`d + rn*1d`), но spec остался устаревшим.
- **Production impact:** будущие читатели спеки (включая Claude-агентов на следующих фазах или возможных контрибьюторов) скопируют буггованный шаблон в новый код. Y-3 ERRORS lesson #1 (2026-04-28 contract drift) явно требует «spec обновлять в том же commit что и роутер».
- **Direction fix:** заменить SQL шаблон в обоих spec на `d + rn*1d` для DESC ИЛИ `d - rn*1d` для ASC; добавить inline-комментарий «pattern: gap-detection через ROW_NUMBER + arithmetic offset». Cross-project mirror `lms-api.md` шаблон не упоминает — затрагивает только tech-spec.

### S2-A2: Live VK linking smoke отсутствует

- **Где:** `tests/test_me_endpoints_y3.py` покрывает только negative path для `/me/identity/vk/link` (invalid link_token, wrong-user). Real VK PKCE flow (с настоящим VK API) не тестировался.
- **Production impact:** review-checklist «Live API / External Write-Path Check» — для VK external API write-path требуется gated live smoke (по образцу `CB_LMS_LIVE_SMOKE_Y15`). При деплое в продакшн возможны сюрпризы: VK API может вернуть unexpected payload shape, retry/timeout edge cases.
- **Direction fix:** добавить gated test `tests/test_y3_vk_link_live.py` с маркером `@pytest.mark.skipif(not env.CB_LMS_LIVE_SMOKE_Y3, ...)` — делает реальный PKCE-обмен (или принимает заранее выпущенный code+verifier через env vars). Pattern существующий в Y-1.5: `tests/test_y15_live_smoke.py`.
- **Альтернатива:** operator-handoff из CB §22.1 содержит manual VK linking step — оператор может проверить вручную перед деплоем; в этом случае S2-A2 закрывается через operator chain.

---

## Non-Blocking Findings (S3)

### S3-A1: spec authority бaг в streak SQL формуле — root cause

- **Где:** CB authority spec § 7.2.4. Формула `d - rn*1d` для `ORDER BY d DESC` математически некорректна.
- **Action:** см. S2-A1 — backsync обоих spec.

### S3-A2: encrypt_token импортируется напрямую в `me.py` (controller layer)

- **Где:** `app/api/v1/me.py:35` + использование на `:329-330`.
- **Production impact:** дублирование business-logic VK encryption между `vk_oauth_service.get_or_create_user_by_vk` и `me.py:link_identity_vk`. Refactor desirable, но не блокирует.
- **Direction fix:** вынести в `vk_oauth_service.link_vk_to_existing_user(...)` или новый `vk_link_service`. Y-3.2 follow-up.

### S3-A3: Magic strings 'completed', 'COMPLETED'

- **Где:** `me_service.py:251`, multiple SQL CTE.
- **Direction fix:** ввести `MATERIAL_STATUS_COMPLETED = "completed"`, `COURSE_STATE_COMPLETED = "COMPLETED"` constants (или импорт из `learning_engine_service`).

### S3-A4: PASS_THRESHOLD_RATIO дублирован в двух модулях

- **Где:** `me_service.py:21` + `learning_engine_service.py:38`.
- **Direction fix:** вынести в `app/services/learning/constants.py` или импортировать.

### S3-A5: Rate-limit отсутствует на `/me/identity/{kind}/link`

- **Где:** `app/api/v1/me.py:172-371` — три linking endpoints без rate-limit.
- **Production impact:** atтакующий с уже-валидным session-токеном может пытаться brute-force link_token (хоть он 32-байтовый и DEL'ится после consume — атака непрактична). Подавление defence-in-depth.
- **Direction fix:** добавить rate-limit 30/мин на user через `is_rate_limited(redis, f"link_identity:{user_id}", 30, 60)`. Y-3.2 follow-up.

---

## Architecture Assessment

✅ Layering preserved: api/v1 → services → repos/models  
✅ SRP: каждый из 4 новых сервисов — одна ответственность  
✅ DRY: `_consume_link_token_for_user`, `_conflict_to_http`, `_link_token_invalid_http` корректно вынесены  
⚠ Один шов для рефакторинга (S3-A2): VK encryption логика в controller

## Migration Assessment

✅ M7 reversible (`op.create_index` ↔ `op.drop_index`)  
✅ Roundtrip протестирован (`test_alembic_downgrade_m7_then_upgrade` PASS)  
✅ Не destructive  
⚠ Не CONCURRENTLY — приемлемо для текущих 39 rows; spec §6 уже отмечает «при росте до >100k — отдельный CONCURRENTLY шаг»

## Test Adequacy Assessment

✅ **58/58 focused tests PASS** (39 Y-3 + 8 Y-3.1 streak + 4 guest_attribution + 7 migrations)  
✅ Edge cases для streak: empty, today_only, gap=1, gap=2 reset, continuous 3 days, dedup same day, gap-too-large, today+yesterday — 8 сценариев  
✅ Identity linking: 8 unit + 14 HTTP integration  
✅ link_token: 6 unit (single-use, garbage, empty, two-tokens-independent)  
⚠ S2-A2: Live VK smoke отсутствует (gated test pattern есть в Y-1.5, можно повторить)  
⚠ Pre-existing: 28 тестов в test_teacher_*, test_hint_*, test_materials_bulk проваливаются в общем прогоне из-за fixture state contamination (подтверждено: проходят индивидуально). Не регрессия Y-3, но сигнал о общей хрупкости test infrastructure.

## Observability Assessment

✅ 5 новых event types: `auth.link_token.issued`, `auth.identity.linked`, `auth.identity.linked.conflict`, `auth.magic_link.verified_link_mode`, `auth.link_token.consume_mismatch`  
✅ Y-3.1 добавил forensics-event для mismatch (закрыл S3-7 первого прохода)  
✅ Y-3.1 добавил `logger.error` маркер «PRODUCTION» для fail-secure paths  
✅ Sensitive data: raw token не логируется, sha256-hash[:8] для tracing only

## Security Assessment

✅ link_token: secrets.token_urlsafe(32), sha256 storage, atomic Lua GET+DEL, single-use  
✅ Y-3.1 fail-secure в production (закрыл S2-1 первого прохода)  
✅ identity_link: savepoint pattern (Y-1.5 lesson), orphan-defense (Y-1.5.1 lesson), 409 для cross-user overlap  
✅ HMAC validation для TG initData (через существующий `tg_init_service`)  
✅ Fernet encryption для VK tokens  
✅ SQL injection: все queries через `text()` с named params, нет string concat  
✅ Bandit: 0/0/0 (Low/Med/High) на изменённых файлах  
⚠ S3-A5: rate-limit отсутствует на /me/identity/{kind}/link (defence-in-depth)

## UX/UI Critical Assessment

✅ /me/last-position возвращает NEXT (per resolve_next_item) — Continue widget UX корректен (зафиксировано в pr-review раунде)  
✅ 401 на invalid link_token не различает причину (anti-enumeration)  
✅ 503 при production Redis-outage чётко сигнализирует SPW (фронт может показать «Сервис временно недоступен»)  
N/A: SPW UI вне scope backend-микрошага

## Spec Ambiguity Assessment

⚠ **S2-A1 (репортируется отдельно):** spec §5.4 SQL шаблон remains buggy после Y-3.1 fix в коде → spec drift  
✅ /me/last-position spec §5.3 шаг 4 (resolve_next_item) — реализация соответствует после pr-review fix  
✅ Q-Y3-1..7 — все backend-portions реализованы  
✅ ADR-0021 §«Confirmed registration policy» — соблюдён

## Date/Time Type Safety Assessment

✅ /me/streak server-side `AT TIME ZONE 'Europe/Moscow'` → `date` ↔ `date` сравнения typed  
✅ `today_msk: date`, `last_active_date: date` — гарантированно одного типа  
✅ `if last_active_date is not None` — explicit None handling  
✅ `int(row["streak_days"])` cast — defensive против `Decimal` от COUNT  
⚠ Y-3.1 SQL fix: формула gap-detection была математически неверна для DESC-ордера. **Отношение к type safety: косвенное** — type был ОК, но семантика broken; type-safety check не ловит logical bugs in SQL window functions. Тестами поймано.

---

## Required Fixes (для Y-3.2 / Y-3.1.x backsync)

### Immediate (до merge backend в main)

Нет — текущее состояние можно мерджить.

### Next-iteration (Y-3.1.x → Y-3.2)

1. **(S2-A1)** Backsync streak SQL шаблона:
   - `D:\Work\LMS\docs\specs\2026-04-29-tech-spec-Y3-learning-loop-backend.md` §5.4 — заменить `d - (ROW_NUMBER() OVER (ORDER BY d DESC))::int * INTERVAL '1 day'` на `d + (ROW_NUMBER() OVER (ORDER BY d DESC))::int * INTERVAL '1 day'`
   - `D:\Work\ContentBackbone\docs\tech-specs\tech-spec-Y3-learning-loop-v1.md` §7.2.4 — то же
   - Добавить запись в `D:\Work\ContentBackbone\docs\cross-project\CHANGELOG.md`: «Y-3.1: streak SQL formula corrected»
2. **(S2-A2)** Добавить gated live VK smoke `tests/test_y3_vk_link_live.py` (опц.; альтернатива — operator-chain через CB §22.1)
3. **(S3-A5)** Rate-limit на /me/identity/{kind}/link — 30/мин на user

### Backlog

4. (S3-A2) Refactor encrypt_token из `me.py` controller в `vk_oauth_service.link_vk_to_existing_user`
5. (S3-A3, S3-A4) Магические константы вынести в shared module

---

## Required Validation Commands

```bash
cd D:\Work\LMS

# 1. Migration roundtrip
alembic upgrade head
alembic downgrade -1
alembic upgrade head

# 2. Полный фокусный pytest
pytest tests/test_link_token_service.py \
       tests/test_me_service_mask.py \
       tests/test_identity_link_existing_user.py \
       tests/test_me_endpoints_y3.py \
       tests/test_streak_logic.py \
       tests/test_guest_attribution.py \
       tests/test_migrations.py -v

# Ожидается: 58 passed

# 3. Bandit
bandit -r app/services/auth/ app/api/v1/me.py app/api/v1/auth/link_token.py \
       app/services/me_service.py -ll
# Ожидается: 0 High, 0 Medium, 0 Low

# 4. Routes registration smoke
python -c "
from dotenv import load_dotenv; load_dotenv()
from app.api.main import app
y3_paths = ['/me/identities','/me/courses','/me/last-position','/me/streak',
            '/auth/link-token/issue','/me/identity/email/link',
            '/me/identity/tg/link','/me/identity/vk/link']
found = {r.path: list(r.methods)[0] for r in app.routes if hasattr(r,'methods')
         and any(p in r.path for p in y3_paths)}
for p in y3_paths:
    full = next((k for k in found if p in k), None)
    print(f'{found[full]:5s} {full}' if full else f'MISSING {p}')
"
# Ожидается: все 8 endpoints зарегистрированы

# 5. Production fail-secure smoke (опц.)
ENV=production python -c "
import asyncio
from app.services.auth.link_token_service import issue, LinkTokenServiceUnavailableError
async def t():
    # Mock-Redis with broken connection
    class BrokenRedis:
        async def set(self, *a, **k): raise ConnectionError('redis down')
    try:
        await issue(BrokenRedis(), 1, 'vk')
        print('FAIL: should have raised')
    except LinkTokenServiceUnavailableError:
        print('OK: fail-secure works')
asyncio.run(t())
"
# Ожидается: OK

# 6. Production fail-secure: dev mode fallback всё ещё работает
python -c "
import asyncio, os
os.environ.pop('ENV', None)  # default = dev
from app.services.auth.link_token_service import issue, consume
async def t():
    raw, _ = await issue(None, 1, 'vk')  # in-memory
    p = await consume(None, raw)
    print(f'OK: dev fallback p.user_id={p.user_id}')
asyncio.run(t())
"
# Ожидается: OK: dev fallback p.user_id=1
```

---

## Residual Risks

- **R1 (Y-3.1):** spec drift до завершения backsync (S2-A1) — если кто-то скопирует SQL из spec, повторит bug
- **R2 (Y-3.1):** in-memory fallback теперь не активен в production, но **operator должен явно установить `ENV=production`** в env vars при деплое — иначе fail-secure не сработает. Документировать в operator runbook
- **R3:** Live VK PKCE smoke missing (S2-A2) — production deploy потребует ручной проверки оператором (CB §22.1)
- **R4:** test fixture state contamination в общем прогоне `pytest tests/` — pre-existing, не Y-3 регрессия, но снижает confidence в CI

---

## Claude Skills Improvement Entries

Логирую 3 skill defects в `D:\Work\LMS\docs\ai\ERRORS.md` (project-specific register).

### Entry #1: /executor-pro / inline executor — пропустил test_streak_logic.py

```
| Дата | Проект | Контекст | Симптом | Корневая причина | Класс | Severity | Как обнаружено | Исправление | Профилактика | Статус |
| 2026-04-29 #1 | LMS | Y-3 backend execution | Spec §8 «Tests» listed test_streak_logic.py с edge cases (gap=1, gap=2, today_active, single day) как обязательный — executor написал 4 других test files но НЕ test_streak_logic. Acceptance criterion §11 «/me/streak корректно считает streak с edge cases» прошёл по умолчанию (отсутствие failing-тестов трактовано как PASS). В Y-3.1 раунде edge tests добавлены — поймали критический bug в SQL формуле | executor-pro inline path не проверил line-by-line spec §«Tests» список перед closure; SKILL.md не имеет явного шага «грепнуть spec §Tests, подтвердить каждый перечисленный test file существует» | test-gap + scope-violation | S2 (поймал реальный bug — streak SQL давал streak=1 вместо N для всех multi-day users) | Y-3.1 раунд /fastapi-api-developer добавил test_streak_logic.py с 8 edge cases | Y-3.1 написал тесты + исправил SQL bug (см. reviews/2026-04-29-y3.1-followups.md) | (a) `~/.claude/skills/executor-pro/SKILL.md` секция «Output Contract» — добавить пункт «Spec Test Coverage Audit: для каждого .py файла, упомянутого в spec §«Tests» / §«Tests / Unit» / §«Tests / Integration», подтвердить существование в репозитории и pass/fail»; (b) `~/.claude/skills/fastapi-api-developer/SKILL.md` Шаг 4 — после prompt «pytest на затронутые модули» добавить bullet «grep spec на конкретные имена test_*.py — все ли созданы» | OPEN |
```

**Pattern repeat:** аналогичный класс (test gap пропуск) уже зафиксирован в ERRORS 2026-04-28 #3 («`tests/test_y15_live_smoke.py` не создан вопреки tech-spec §16»). Это **2-й случай подряд** для /executor-pro inline.

→ **ESCALATION в /claude-booster Режим A** для аудита /executor-pro и /fastapi-api-developer на «spec test list adherence».

### Entry #2: /tech-spec-composer (or /spec-writer) — приняли SQL формулу без semantic validation

```
| 2026-04-29 #2 | LMS | LMS-side spec написание (Y-3 backend) | LMS-side spec §5.4 содержит SQL шаблон для streak gap-detection с буггованной формулой `d - rn*1d` для `ORDER BY d DESC` (математически неверно — каждый день в своей grp). Формула скопирована verbatim из CB authority spec §7.2.4 без semantic validation | tech-spec-composer/spec-writer paths не имеют шага «mentally trace SQL formula on 3-day input». Принят upstream-формулу как есть | api-drift (точнее: «specification-reuse-without-verification») | S2 (bug ушёл бы на прод без edge-тестов) | Y-3.1 — fix в коде; backsync spec — Y-3.2 follow-up (S2-A1) | (a) `~/.claude/skills/tech-spec-composer/SKILL.md` или `~/.claude/skills/spec-writer/SKILL.md` Шаг «Анализ» — добавить «если spec включает raw SQL с window-функциями (ROW_NUMBER, gap-detection), trace на 3-day и edge inputs до фиксации»; (b) cross-link в `~/.claude/skills/claude-booster/references/api-contract-rules.md` — добавить раздел «SQL formula verification» | OPEN |
```

### Entry #3: /techlead-code-reviewer (1st pass) — недооценил severity test gap

```
| 2026-04-29 #3 | LMS | Y-3 backend первый techlead-review проход (`reviews/2026-04-29-y3-techlead-review.md`) | Найден gap: «test_streak_logic.py отсутствует ... gap=1, gap=2 не покрыты» — классифицирован S3 medium (non-blocking). С учётом того, что отсутствие тестов укрыло реальный bug в SQL формуле, severity должен был быть S2 | review-checklist.md «Testing Checks» не содержит явного правила: «если spec §«Tests» перечисляет required test files и они отсутствуют → S2, не S3» | test-gap (meta: severity-misclassification) | S3 (сам по себе review-defect), но привёл к S2 implication | Y-3.1 раунд закрыл gap | `~/.claude/skills/techlead-code-reviewer/references/testing-checks.md` — добавить новый раздел «Spec-Mandated Test Files»: «если spec явно перечисляет required test files (через §«Tests» / §«Test Coverage» секцию) и они отсутствуют — это S2, не S3, потому что spec author явно классифицировал их как обязательные для acceptance» | OPEN |
```

---

## Skill Improvement Actions

| Skill | Файл | Суть правки | Приоритет |
|---|---|---|---|
| `/executor-pro` | `~/.claude/skills/executor-pro/SKILL.md` § Output Contract | Добавить пункт «Spec Test Coverage Audit: для каждого test_*.py упомянутого в spec §Tests/§Tests/Unit/§Tests/Integration — подтвердить существование в репо + статус pass/fail». См. ERRORS 2026-04-29 #1 + repeat 2026-04-28 #3. | **immediate** (повторяющийся, 2-й случай подряд) |
| `/fastapi-api-developer` | `~/.claude/skills/fastapi-api-developer/SKILL.md` Шаг 4 | После «pytest на затронутые модули» добавить bullet «grep spec на конкретные имена test_*.py — все ли созданы и зелёные». См. ERRORS 2026-04-29 #1. | next-iteration |
| `/tech-spec-composer` | `~/.claude/skills/tech-spec-composer/SKILL.md` Шаг анализа | Добавить «SQL formula verification: если spec включает raw SQL с window-функциями (ROW_NUMBER, gap-detection, рекурсивные CTE) — trace на 3-input примере». См. ERRORS 2026-04-29 #2. | next-iteration |
| `/spec-writer` | `~/.claude/skills/spec-writer/SKILL.md` (аналогично) | То же что для tech-spec-composer (если spec-writer тоже формирует executable SQL). | next-iteration |
| `/techlead-code-reviewer` | `~/.claude/skills/techlead-code-reviewer/references/testing-checks.md` | Новый раздел «Spec-Mandated Test Files»: «отсутствие test files перечисленных в spec §Tests → S2, не S3». См. ERRORS 2026-04-29 #3. | next-iteration |
| **escalation** | `/claude-booster` Режим A | Аудит /executor-pro и /fastapi-api-developer skills на «spec test list adherence» — повторение паттерна 2 раза подряд (2026-04-28 #3 + 2026-04-29 #1). | **immediate** |

---

## Final Verdict

**PASS** — Y-3 backend + Y-3.1 follow-ups готовы к merge в `main`.

**Что закрыто этим раундом ревью:**
- ✅ S2-1 (in-memory fallback в prod) → fail-secure через `Settings.env`
- ✅ S3-7 (audit consume_mismatch) → event добавлен с forensics details
- ✅ P1 (test_streak_logic gap) → 8 edge tests + bug в SQL fix

**Что остаётся как Y-3.2 follow-up (не блокирует merge):**
- S2-A1 (spec backsync под исправленный SQL) — **высокий приоритет** перед Y-3.2 кодом
- S2-A2 (live VK smoke) — приемлемо через operator chain CB §22.1
- S3-A2..A5 (мелкие refactor opportunities)

**Skill defects:** 3 entries OPEN в ERRORS.md + escalation /executor-pro / /fastapi-api-developer в /claude-booster Режим A.

**Repository state:** integration-safe. Бизнес-цель «backend для SPW Y-3 / TG Mini App» достигнута: 8 endpoints + 1 миграция + полное тестовое покрытие + cross-project memory backsync + 4 review-артефакта.
