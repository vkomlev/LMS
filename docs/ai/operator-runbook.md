# Operator runbook — действия требующие оператора

Этот файл — реестр сценариев, где задача упирается во внешнюю систему или ручное действие.
Для каждого сценария: **autonomous workaround** (что я делаю сам пока оператор настраивает) + **operator instruction** (что нужно от оператора).

При повторении сценария Claude обязан сначала прочитать этот файл и применить готовый workaround,
а не блокироваться на operator handoff.

---

## R-001: Resend API не настроен — magic-link письма не уходят

**Симптом в логах:**
```
WARNING | app.services.auth.magic_link_service | RESEND_API_KEY не задан, письмо не отправлено
```

или

```
ERROR | app.services.auth.magic_link_service | Resend API error 401: ...
```

**Корневая причина:** в `.env` отсутствует `RESEND_API_KEY` или ключ невалидный/домен не верифицирован.
В коде `magic_link_service.send_magic_link_email` — graceful degradation: запись magic_link в БД
создаётся, но HTTP-вызов к Resend пропускается.

### Autonomous workaround (Claude делает сам)

Выпустить magic-link токен напрямую в БД и отдать пользователю готовую команду для verify:

```bash
cd D:/Work/LMS && python -c "
from dotenv import load_dotenv; load_dotenv('.env', encoding='utf-8-sig')
import asyncio, hashlib, os
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from app.db.session import async_session_factory

EMAIL = 'EMAIL_TARGET'  # подставить email пользователя

async def main():
    async with async_session_factory() as db:
        r = await db.execute(text(
            \"SELECT u.id FROM users u LEFT JOIN identity_link il \"
            \"ON il.user_id=u.id AND il.kind='email' \"
            \"WHERE u.email ILIKE :e OR il.value=:e LIMIT 1\"
        ), {'e': EMAIL})
        if r.scalar() is None:
            print('NO_USER'); return
        raw = os.urandom(32); h = hashlib.sha256(raw).digest()
        await db.execute(text(
            'INSERT INTO magic_link(email, token_hash, expires_at) VALUES (:e, :h, :exp)'
        ), {'e': EMAIL, 'h': h, 'exp': datetime.now(timezone.utc)+timedelta(minutes=15)})
        await db.commit()
        print('TOKEN_HEX:', raw.hex())

asyncio.run(main())
"
```

Дать пользователю PowerShell-команду:
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/auth/magic-link/verify" `
  -Method POST -ContentType "application/json" `
  -Body '{"token":"<TOKEN_HEX>"}'
```

### Operator instruction (только когда требуется production-настройка Resend)

1. **Получить ключ.** https://resend.com → API Keys → Create. Permission Full access. Копировать `re_…` ключ (показывается один раз).
2. **Домен.** Для теста — `SMTP_FROM=onboarding@resend.dev` (sandbox, шлёт только владельцу аккаунта). Для prod — добавить `victor-komlev.ru` в Resend → Domains, прописать DNS (SPF/DKIM/return-path), дождаться **Verified**.
3. **`.env`:** прописать `RESEND_API_KEY=re_…` и `SMTP_FROM=...`.
4. **Restart:** Ctrl+C в окне uvicorn → `python run.py`.
5. **Smoke:** `Invoke-RestMethod -Uri "http://localhost:8000/api/v1/auth/magic-link/send" -Method POST -ContentType "application/json" -Body '{"email":"<твой email>"}'` → жди письмо ≤1 мин.

**Диагностика если не пришло:**
- `grep "Resend API error" D:/Work/LMS/logs/app.log` — HTTP-код в строке.
- 401 → ключ невалидный, проверить копипаст без пробелов.
- 403 / 422 → домен не верифицирован или sandbox-адрес используется не-владельцем.
- Письмо в спаме у пользователя.

---

## R-004: Magic-link verify → 401 при валидном токене (email_not_linked)

**Симптом в логах:**
```
UPDATE magic_link SET consumed_at=...   -- токен погашен (валиден)
SELECT ... FROM identity_link WHERE kind='email' AND value='<email>'
ROLLBACK
"POST /api/v1/auth/magic-link/verify HTTP/1.1" 401
```

То есть `consume_magic_link` нашёл и погасил токен, но `get_user_by_identity` вернул None.
Из-за constant-time fix (B1) клиент видит то же 401 что и при битом токене —
без логов не отличить.

**Корневая причина:** email пользователя не привязан к `identity_link.kind='email'`.
В Y-1 нет публичного эндпоинта `/me/identity/.../link` (это Y-2), и единственный
backfill — M2-миграция из `users.email`. Любой *новый* email можно привязать
только через прямой INSERT.

### Autonomous workaround (Claude делает сам)

```bash
cd D:/Work/LMS && python -c "
from dotenv import load_dotenv; load_dotenv('.env', encoding='utf-8-sig')
import asyncio, hashlib, os
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from app.db.session import async_session_factory

EMAIL = '<TARGET_EMAIL>'
USER_ID = <USER_ID>  # подставить или искать через identity_link/users

async def main():
    async with async_session_factory() as db:
        ex = (await db.execute(text(\"SELECT user_id FROM identity_link WHERE kind='email' AND value=:v\"), {'v': EMAIL})).scalar()
        if ex is None:
            await db.execute(text(\"INSERT INTO identity_link(user_id, kind, value) VALUES (:u, 'email', :v)\"), {'u': USER_ID, 'v': EMAIL})
        raw = os.urandom(32); h = hashlib.sha256(raw).digest()
        await db.execute(text('INSERT INTO magic_link(email, token_hash, expires_at) VALUES (:e, :h, :exp)'),
                         {'e': EMAIL, 'h': h, 'exp': datetime.now(timezone.utc)+timedelta(minutes=15)})
        await db.commit()
        print('TOKEN_HEX:', raw.hex())

asyncio.run(main())
"
```

### Operator instruction (нужна, только если непонятно к какому user_id привязывать)

Если email от незнакомого человека и неясно создавать ли нового user или привязывать
к существующему — спросить оператора прежде чем INSERT. Пример вопроса:

> «Email `<EMAIL>` пытается войти, но не привязан ни к одному пользователю.
> Это (а) тот же ты с другой почтой → привязать к user_id=2,
> (б) новый ученик → создать users-запись + identity_link?»

### Долгосрочно (Y-2)
- `POST /me/identity/email/link` через `link_token` — пользователь после первичного
  входа сможет привязать дополнительный email сам, без вмешательства.

---

## R-002: VK ID 2.0 OAuth — не настроены credentials

**Симптом:** `POST /auth/vk/callback` → 401 "VK token exchange failed" в логах.

### Autonomous workaround
Создать тестового пользователя с identity_link `kind='vk'` напрямую в БД, выпустить
session_token через `session_service.create_session()` — обойти OAuth flow для тестов.

### Operator instruction
1. https://id.vk.com → Создать приложение → SPA (PKCE).
2. Получить `client_id`, `redirect_uri=https://learn.victor-komlev.ru/auth/vk/callback`.
3. `.env`: `VK_ID_CLIENT_ID=…`, `VK_ID_CLIENT_SECRET=…`, `VK_ID_REDIRECT_URI=…`.
4. Restart server.

---

## R-003: Telegram bot token для initData verify — не настроен

**Симптом:** `/auth/tg/init` → 503 "TG auth не настроен".

### Autonomous workaround
Для unit-тестов — мокать `verify_tg_init_data()`. Для smoke — создать identity_link
`kind='tg'` напрямую и проверить session creation в обход HMAC.

### Operator instruction
1. Открыть BotFather в Telegram → найти бот, который выдаёт WebApp с initData.
2. `/token` → скопировать токен (формат `123456:ABC-…`).
3. `.env`: `TG_BOT_TOKEN_FOR_INITDATA=…`.
4. Restart server.

---

## Шаблон для новых записей

```
## R-NNN: <название>

**Симптом:** <грепабельная строка в логах или поведение>

### Autonomous workaround
<точная команда / скрипт что делает Claude сам>

### Operator instruction
<пронумерованные шаги, готовые команды, что увидит как результат>
```
