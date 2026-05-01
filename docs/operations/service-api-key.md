# Service API Key (`X-API-Key`) — операционный гайд

**Создано:** 2026-05-01 (Phase Y-4 pre-S5)
**Авторитет:** [Y-1 spec §6.7 service auth](../specs/2026-04-27-tech-spec-Y1-auth-extension.md), [Y-4 pre-S5 spec](../specs/2026-05-01-tech-spec-Y4-pre-S5-auth-role-backend.md)

## Что это

`X-API-Key` — header для **service-level auth** в LMS API. Резолвится в
`CurrentUser(id=0, is_service=True)` через `Depends(get_current_user)` и
позволяет:

- Bypass IDOR-проверок (RBAC всегда true для service)
- Прямой доступ к endpoints, защищённым `Depends(get_current_user)` —
  без cookie / Bearer
- Использование тестового endpoint `POST /auth/test/issue-session`
  (Phase Y-4 pre-S5, выдача cookie для Playwright spec'ов)

В env: `VALID_API_KEYS=<key1>,<key2>` — список через запятую.

## Кто использует ключ

| Consumer | Использование | Где хранит |
|---|---|---|
| **TG_LMS teacher-bot** | Poller `GET /teacher/reviews/pending-count`; диалог проверки `POST /teacher/reviews/{id}/grade`, `claim-next`, `release` | `D:\Work\TG_LMS\.env` → `LMS_SERVICE_API_KEY` |
| **TG_LMS legacy api_client** | Старые CRUD-маршруты (legacy `?api_key=`) | Тот же `.env` |
| **ContentBackbone CLI** | Pipeline-импорт задач, материалов | `D:\Work\ContentBackbone\.env` |
| **SPW E2E live spec** (Y-4 S5) | `POST /auth/test/issue-session` для bootstrap cookie тестового студента | `D:\Work\spw\tests\e2e-live\.env` (НЕ в основном `.env` SPW) |

> **Никогда не использовать в frontend (SPW main app)** — frontend ходит через
> cookie-сессии или Bearer (TG App), не через X-API-Key.

## Генерация нового ключа

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Длина output ≥ 48 символов URL-safe base64. Минимум 32 символа — Settings
валидирует на старте (см. `app/core/config.py`).

## Хранение

`.env` файлы помечены в `.gitignore`. **Никогда** не коммитить значение
ключа. Шаблон без секрета — в `.env.example`.

## Ротация

Рекомендация: **раз в 6 месяцев** или при подозрении компрометации
(утечка в access-log, share с третьим лицом, рекомендация security-аудита).

Процедура (downtime ~30 сек на каждом consumer'е):

1. **Сгенерировать новый ключ** (см. выше).
2. **Расширить `VALID_API_KEYS` LMS** новым ключом В НАЧАЛЕ списка:
   ```
   VALID_API_KEYS=NEW_KEY,old-key-1,old-key-2
   ```
   Перезапустить LMS dev-сервер. Оба ключа теперь валидны (graceful overlap).
3. **Обновить consumer'ов синхронно** — каждый замены свой старый ключ на новый
   и перезапускается:
   - TG_LMS: `D:\Work\TG_LMS\.env` → `LMS_SERVICE_API_KEY=NEW_KEY`; перезапустить
     teacher-bot poller.
   - ContentBackbone: `D:\Work\ContentBackbone\.env` → `LMS_SERVICE_API_KEY=NEW_KEY`;
     перезапустить активные pipeline'ы.
   - SPW E2E live env: `D:\Work\spw\tests\e2e-live\.env` → `LMS_SERVICE_API_KEY=NEW_KEY`.
4. **Smoke-проверка каждого consumer'а** на новом ключе.
5. **Удалить старые ключи** из `VALID_API_KEYS` LMS:
   ```
   VALID_API_KEYS=NEW_KEY
   ```
   Перезапустить LMS. Теперь только новый ключ валиден.

## Что нельзя делать

- ❌ Логировать значение ключа в `audit_event.details`, access log, или stdout
  (только masked: `key[:6] + "***"`)
- ❌ Хранить ключ в frontend `.env` (SPW main, WP embed)
- ❌ Использовать как persistent сессию пользователя — service-key даёт
  bypass IDOR, поэтому role-проверки фактически отсутствуют
- ❌ Передавать ключ через query-параметр `?api_key=...` за пределами
  legacy TG_LMS endpoints (header `X-API-Key` предпочтительнее)

## Что НЕ хранится в этом ключе

- Persistent prod-данные (только internal trusted-zone communication)
- Пользовательские сессии (для них — `user_session` table + cookies)
- VK / TG OAuth токены (Fernet-encrypted в `identity_link`)

## Связанные документы

- [Y-1 spec §«Service-level auth»](../specs/2026-04-27-tech-spec-Y1-auth-extension.md)
- [Y-4 pre-S5 spec §5 «Обязательные правила»](../specs/2026-05-01-tech-spec-Y4-pre-S5-auth-role-backend.md)
- [`.env.example`](../../.env.example) — шаблон с placeholder'ами
- [`app/auth/service_api_key.py`](../../app/auth/service_api_key.py) — `is_valid_service_key()`
- [`app/api/deps.py:get_current_user`](../../app/api/deps.py) — pipeline auth
