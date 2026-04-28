# Review: Y-1.5.1 hotfix — orphan email guard + VK PKCE diagnostic

## Цель (из handoff)

Закрыть S2 bug из `D:\Work\LMS\docs\handoff\2026-04-28-spw-y2-vk-debug.md` §2:
`get_or_create_user_by_*` падали в 500 (UniqueViolationError) при orphan email
(`users.email` exists без `identity_link kind='email'`). По ADR-0021 §2 этот
случай должен возвращать **409 identity_conflict** (защита от identity-takeover).

Воспроизведение в продакшене: live smoke MG-VK на стороне SPW (commit `d022d77`)
для `victor.v.komlev@gmail.com` после magic-link first-time create + manual cleanup
identity_link.

Также handoff §5: закоммитить диагностические правки vk_oauth_service.py
(client_id в userinfo body — обязательное требование VK ID 2.0 ; logger.error
на error-path для prod-диагностики).

## Затронутые файлы

**Auth services:**
- `app/services/auth/exceptions.py` — **NEW** shared `IdentityConflictError`
  (вынесен из `vk_oauth_service` для re-use в magic_link)
- `app/services/auth/vk_oauth_service.py`:
  - Удалён локальный class IdentityConflictError; `from .exceptions import` + `__all__` re-export для backward compat
  - Добавлен orphan SELECT `users WHERE lower(email)=lower(input)` перед savepoint
  - Логирование на error-path exchange/userinfo (handoff §1.1, остаются в commit)
  - `client_id` в userinfo POST body (handoff §1.1, обязательное VK ID 2.0)
- `app/services/auth/magic_link_service.py`:
  - Импорт `IdentityConflictError` из exceptions
  - Симметричный orphan SELECT перед savepoint
  - Raise `IdentityConflictError("email_already_linked_to_orphan_user", existing_kinds=[])`

**Routers:**
- `app/api/v1/auth/magic_link.py` — except `IdentityConflictError` → HTTP 409
  с body `{error, conflict_kind, existing_identity_kinds, message}`
- `app/api/v1/auth/vk.py` — rebase import: `from app.services.auth.exceptions`

**Tests (regression):**
- `tests/test_auto_register_vk.py::test_orphan_email_returns_409` — pre-condition
  orphan user без identity_link → ожидаем `IdentityConflictError(email_already_linked_to_orphan_user)`
- `tests/test_auto_register_magic_link.py::test_orphan_email_returns_409` —
  HTTP-уровень: POST verify → 409 с body schema

**Docs (backsync, same commit):**
- `docs/specs/2026-04-27-tech-spec-Y1-auth-extension.md` §6.2 + §6.4 —
  добавлен 409 path для orphan_user
- `docs/ai/ERRORS.md` — запись 2026-04-28 #4 (S2 INTEGRATION, FIXED)

## Регрессионный тест

`test_orphan_email_returns_409` (оба файла) — fail до фикса (повторил бы
500 IntegrityError), pass после фикса (явный 409). Сценарий handoff §2
покрыт точно.

## Результаты валидации

```
Y-1 + Y-1.5 + Y-1.5.1 functional regression: 67 passed, 7 warnings in 45.91s
Bandit security scan (severity LOW/MEDIUM/HIGH):
  app/services/auth/, app/api/v1/auth/ — No issues identified
URL-guard:
  grep "victor-komlev.ru|localhost:..." в app/services — только config.py через env ✓
```

## Решение по семантике (deviation note)

Handoff option A применяется **симметрично** (409 для обоих сервисов) — это
точно соответствует ADR-0021 §2 «email overlap → 409, no auto-merge».

**Альтернатива (не реализована, в Risks):** для magic-link orphan recovery
(restore identity_link, return existing user) — magic-link consumer владеет email
(proven by clicking link), это data corruption recovery, не identity-takeover.
Текущее 409 даёт UX dead-end в этом сценарии. **Это отдельное архитектурное
решение** — требует apсхитектурного review (`/architect-system-analyst`) и
amendment к ADR-0021. Не Y-1.5.1 scope.

## Rollback note

```powershell
git revert <commit-hash>
```

Rollback вернёт 500 на orphan email (regression). Production риск — если
в БД появятся orphan email-states (например, через operator-driven cleanup
identity_link) — соответствующий пользователь не сможет завершить login через
magic-link/vk, увидит 500. Mitigation: до отката — не трогать identity_link
manually.

## Risks / Follow-ups

1. **Magic-link orphan recovery** — отдельная архитектурная задача (см.
   «Решение по семантике»). Если product-владелец хочет UX без dead-end —
   вызвать `/architect-system-analyst` для ADR-0021 §2 amendment.

2. **TG init не покрыт orphan-defense** — `tg_user_id` НЕ имеет UNIQUE
   constraint на `users.tg_id` (только identity_link UNIQUE). Orphan-state
   через manual ops для TG невозможен в том же сценарии. Но симметричный
   defense можно добавить если правила безопасности усилятся.

3. **Логирование `logger.info`** в vk_oauth_service.py может быть шумным
   для prod (handoff §1.1 §«Что можно убрать»). Рекомендация handoff —
   понизить до `logger.debug` или conditional `isEnabledFor(DEBUG)`.
   Не сделано в этом hotfix (handoff отметил как опциональное).

4. **Test для race orphan creation** (B2 race с orphan): handoff §2
   упоминает что race из B2 теоретически может создать orphan. Текущий
   B2 fix через savepoke это блокирует — orphan не должен возникать
   автоматически. Manual operator action — единственный путь. Если
   подтвердится production race — добавить race-test через subprocess.
