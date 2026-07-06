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

## R-005: Настройка тарифов и сервисов в панели Timeweb Cloud (деплой LMS+SPW+TG_LMS)

**Симптом:** нужно вручную выбрать тарифы и создать сервисы в панели Timeweb Cloud —
требует логина в личный кабинет, привязки карты/баланса и решений по конкретным тарифным
планам, которые Claude не видит (динамический калькулятор, не в статичной документации).
Контекст решений — `docs/briefs/deploy-timeweb-cloud.md` (tsk-005).

### Autonomous workaround
Нет — это первое столкновение с этим сценарием, полностью operator-only (оплата, логин
в чужой личный кабинет). После первого прохода все конкретные значения (адреса, порты,
имена БД) фиксируются здесь и в `docs/briefs/`, повторный деплой пойдёт по накатанному пути.

### Operator instruction

**Что нужно:** создать в панели Timeweb Cloud 4 сервиса и передать мне 6 значений для
prod-конфигурации. Время: 30–40 минут (без учёта ожидания подтверждения оплаты).

#### Шаг 1 — вход и баланс
1. Открой https://timeweb.cloud/ → войди или зарегистрируйся.
2. Пополни баланс в разделе «Финансы» на сумму, покрывающую первый месяц всех 4 сервисов
   (точные цены — только в личном кабинете, у меня нет доступа к калькулятору).
3. **Результат:** в личном кабинете виден баланс > 0.

#### Шаг 2 — DBaaS-кластер (Postgres + Valkey)
1. Раздел «Облачные базы данных» → «Создать» → PostgreSQL, минимальный тариф
   (1 vCPU / 2 ГБ RAM / 20–30 ГБ диск — этого достаточно для учебного проекта с
   десятками-сотнями учеников; при росте нагрузки тариф можно увеличить без пересоздания).
   Регион — Москва или Санкт-Петербург (там доступны приватные сети).
2. Внутри кластера создай 2 базы данных и 2 пользователя с правами только на «свою» БД:
   - БД `learn`, пользователь `lms_prod` — права только на `learn`
   - БД `content_backbone`, пользователь `cb_prod` — права только на `content_backbone`
3. Включи публичный доступ (Public IP) — платная опция, но обязательна, пока App Platform
   не поддерживает VPC (см. бриф, раздел 5).
4. Отдельно добавь Valkey (Redis-совместимый) в том же разделе DBaaS, минимальный тариф —
   для LMS (rate-limit/session/link_token).
5. **Результат — сохрани и пришли мне:**
   - хост + порт Postgres, `lms_prod` пароль, `cb_prod` пароль
   - хост + порт Valkey, пароль (если есть)

#### Шаг 3 — App Platform: LMS (backend)
1. Раздел «App Platform» → «Создать» → подключить GitHub-репозиторий `vkomlev/LMS`, ветка `main`.
2. Рантайм — Python. Команда запуска:
   ```
   uvicorn app.api.main:app --host 0.0.0.0 --port $PORT
   ```
3. Health-check path: `/health`
4. Переменные окружения — перенеси список из `D:\Work\LMS\.env.example`, со следующими
   изменениями под прод:
   - `DATABASE_URL` — собери из хоста/пароля `lms_prod` с шага 2 (формат `postgresql+asyncpg://lms_prod:<пароль>@<хост>:<порт>/learn`)
   - `REDIS_URL` — адрес Valkey с шага 2
   - `PUBLIC_BASE_URL=https://learn.victor-komlev.ru`
   - `CORS_ALLOWED_ORIGINS=https://learn.victor-komlev.ru`
   - `ENV=production`, `COOKIE_SECURE=true`
   - `MAGIC_LINK_SECRET`, `SESSION_SIGNING_KEY`, `FERNET_MASTER_KEY` — **сгенерируй новые
     значения, не копируй из локального `.env`** (например `python -c "import secrets; print(secrets.token_urlsafe(32))"` на своей машине)
   - `RESEND_API_KEY`, `VK_ID_*`, `TG_BOT_TOKEN_FOR_INITDATA` — если уже настроены (см. R-001/R-002/R-003 выше), перенести реальные значения
   - **Не включай автодеплой** (галочка «автоматически деплоить при пуше») — по решению из брифа, первые недели деплой ручной
5. Привяжи домен `api.learn.victor-komlev.ru` в разделе «Домены» приложения (SSL Let's Encrypt выпустится автоматически).
6. **Результат:** после первого ручного деплоя `https://api.learn.victor-komlev.ru/health` отдаёт `200 OK`.

#### Шаг 4 — App Platform: SPW (frontend)
1. «Создать» → подключить `vkomlev/spw`, ветка `main`.
2. Рантайм — Node.js. Команда сборки: `pnpm install && pnpm build`. Команда запуска: `pnpm start`.
3. Переменные из `D:\Work\spw\.env.example`:
   - `LMS_UPSTREAM_URL=https://api.learn.victor-komlev.ru`
   - `NEXT_PUBLIC_VK_CLIENT_ID`, `NEXT_PUBLIC_VK_REDIRECT_URI=https://learn.victor-komlev.ru/auth/vk/callback`
4. Домен `learn.victor-komlev.ru`, автодеплой — тоже выключен на первые недели.
5. **Результат:** `https://learn.victor-komlev.ru` открывает главную страницу без ошибок в консоли браузера.

#### Шаг 5 — VDS для TG_LMS (5 ботов)
1. Раздел «Облачные серверы» → «Создать» → Ubuntu 22.04 LTS, минимальный тариф
   (2 vCPU / 2–4 ГБ RAM достаточно для 5 лёгких Python-процессов + локальный Redis).
   Тот же регион, что и DBaaS-кластер (для VPC-связи с базой без публичного трафика).
2. Подключи VDS к той же приватной сети (VPC), что и DBaaS-кластер — тогда боты будут
   ходить к `learn`/`content_backbone` не понадобятся, но пригодится, если позже TG_LMS
   станет обращаться к БД напрямую.
3. **Результат:** получаешь IP-адрес VDS и root/SSH-доступ (ключ или пароль).
4. Дальше настройку systemd-юнитов и деплой кода на этот VDS сделаю я сам (`executor-pro`) —
   пришли мне только IP и способ подключения (SSH-ключ или пароль).

**Сделано 2026-07-04:** VDS `msk-1-vm-7owh` создан. По умолчанию Timeweb выдал только IPv6
(`2a03:6f00:a::2:beb8`) — с локальной машины оператора нет исходящей IPv6-связности
(ping не проходит), пришлось дополнительно заказывать/уточнять IPv4 (`72.56.247.22`).
**На будущее:** при заказе нового VDS сразу спрашивать/проверять наличие IPv4, не полагаться
на IPv6-адрес по умолчанию. Доступ настроен по SSH-ключу (`~/.ssh/tg_lms_vds`, alias
`tg-lms-vds` в `~/.ssh/config`) — пароль root использован один раз только для добавления
ключа и нигде не сохранён. Подключение: `ssh tg-lms-vds`.

#### Что мне прислать в конце
1. Postgres: хост, порт, пароли `lms_prod`/`cb_prod`
2. Valkey: хост, порт, пароль
3. Подтверждение, что `https://api.learn.victor-komlev.ru/health` и `https://learn.victor-komlev.ru`
   открываются
4. IP + SSH-доступ к VDS для ботов

### Если не сработало
- **Оплата не проходит** → раздел «Финансы» → «Способы оплаты», проверить лимиты карты.
- **Домен не привязывается / SSL не выпускается** → проверь, что A/CNAME-запись на
  `api.learn.victor-komlev.ru` / `learn.victor-komlev.ru` уже указывает на технический
  домен приложения (в панели App Platform есть точная инструкция «Добавить домен» с нужным
  значением записи — скопировать оттуda, не придумывать самому).
- **App Platform не видит репозиторий** → GitHub → Settings → Integrations → проверить,
  что Timeweb Cloud получил доступ к `vkomlev/LMS` / `vkomlev/spw` / `vkomlev/TG_LMS`
  (OAuth-разрешение при первом подключении).
- **`/health` отдаёт 500** → пришли мне лог из панели App Platform (раздел «Логи»
  приложения) — разберу по логам, это моя часть работы.
- **DBaaS не даёт создать 2 пользователя с раздельными правами** → создай сначала обе
  БД под одним суперпользователем кластера, пришли мне доступ — я выполню `GRANT`/`REVOKE`
  сам через `db-check` (read-only проверка) + отдельный write-скрипт.

### Что я сделаю после
Как только пришлёшь 4 пункта выше — я:
1. Прогоню `alembic upgrade head` на `learn` (33 миграции).
2. Проверю `/health` и базовый smoke по тест-плану `docs/qa/2026-07-04-deploy-timeweb-smoke-testplan.md`.
3. Подготовлю Dockerfile/systemd-юниты для TG_LMS и задеплою на VDS (`executor-pro`).
4. Обновлю `docs/briefs/deploy-timeweb-cloud.md` и `tsk-005` фактическими адресами/портами
   (без паролей — только хосты и структура).

---

## R-006: App Platform Timeweb Cloud — edge/TLS не маршрутизирует к «здоровым» контейнерам (ИСПОЛНЕНО — переезд на VPS)

**Статус на 2026-07-05: резервный план исполнен, прод переехал на VPS.** Поддержка
подтвердила: связка «App Platform + балансировщик» официально не поддерживается, а
балансировщик обязателен для бесплатного Let's Encrypt на кастомный домен в App Platform —
то есть исходная связка была тупиковой в принципе, не просто временным сбоем.

**Итоговая архитектура:**
- `learn.victor-komlev.ru` (SPW) + `api.learn.victor-komlev.ru` (LMS) → один VPS
  `5.42.102.20` (alias `lms-spw-vds`), Nginx + Certbot, systemd (`lms.service`, `spw.service`).
  Реальный сертификат Let's Encrypt (SAN на оба домена), автопродление настроено.
- TG_LMS (4 бота: admin/methodist/teacher/student) → отдельный VPS `72.56.247.22`
  (alias `tg-lms-vds`), systemd template `tg-lms-bot@{admin,methodist,teacher,student}`,
  локальный Redis.
- Schema `learn`: снята через `pg_dump --schema-only` с рабочей локальной dev-БД `Learn`
  (сама Alembic-история не прогонялась с нуля — см. TODOS.md «Восстановить корректную
  начальную Alembic-миграцию»), применена на прод, `alembic stamp head`.
- App Platform-приложения LMS/SPW и оба балансировщика (Nimble Woodpecker, Polite
  Horologium) **пока не удалены** — оставлены как путь отката до подтверждения стабильности
  VPS в течение нескольких дней.

### Симптом (для истории)

**Симптом:** приложения LMS (Brave Stork) и SPW (Polite Aquila) в App Platform проходят
собственный деплой-цикл платформы («Health status: healthy» → «App is healthy» →
«Deploy succeeded»), но внешние запросы — через кастомные домены, через технические
`*.twc1.net`, напрямую по публичному IP приложения — не проходят. Сначала это было
`503 Service Unavailable` / «No server is available to handle this request» (HAProxy-style
страница), затем после очередного передеплоя деградировало до полного отсутствия ответа
на TLS-рукопожатии (`SSL handshake has read 0 bytes`, `unexpected eof while reading`).

**Что уже исключено (2026-07-04..05, tsk-005):** домен/DNS, SSL-сертификат (перевыпущен,
совпадает), БД (порт 5432 доступен, и у SPW вообще нет зависимости от БД — а ошибка та же),
путь проверки здоровья (задан явно, платформа подтверждает `Health status: healthy`),
несколько полных передеплоев и ручных рестартов. Похоже на сбой edge-роутинга на стороне
Timeweb Cloud для этих двух конкретных сервисов — заведено обращение в поддержку.

### Autonomous workaround
Нет — сбой на стороне внешней платформы, ждём ответа поддержки. Пока готовлю резервный
план параллельно (не применяется без явного решения оператора переключиться):

- `D:\Work\LMS\deploy\vps\` — `lms.service` (systemd), `nginx-lms.conf`, `deploy.sh`, `README.md`
- `D:\Work\spw\deploy\vps\` — `spw.service` (systemd), `nginx-spw.conf`, `deploy.sh`, `README.md`

План: отдельный новый VPS (НЕ тот, что для ботов TG_LMS — там всего 2 ГБ RAM, впритык
даже для самих ботов), 2 vCPU / 4 ГБ RAM, Ubuntu 22.04. Nginx + Certbot вместо App
Platform edge — убирает саму категорию проблемы (обычная A-запись на IP VPS, без
NS-делегирования и без специфичных багов Cloud-платформы).

### Operator instruction (только если решаем переключаться на VPS)

1. Создать новый VDS в панели Timeweb Cloud (Ubuntu 22.04, 2 vCPU/4 ГБ) — по тому же
   рецепту, что и VDS для ботов (см. R-005, шаг 5). Прислать IP + доступ (пароль root
   для одноразовой установки SSH-ключа, как в прошлый раз).
2. Дальше первичную настройку и деплой я сделаю сам по `deploy/vps/README.md` в обоих
   репозиториях (`executor-pro`).
3. Когда сервисы поднимутся и пройдут smoke-тест на IP напрямую — переключить DNS-записи
   `api.learn.victor-komlev.ru` и `learn.victor-komlev.ru` на IP нового VPS (у реестратора,
   там же, где сейчас A-записи на балансировщики).
4. Убрать оба балансировщика (Nimble Woodpecker, Polite Horologium) и App Platform-приложения
   LMS/SPW после подтверждённого переключения — не раньше, чтобы был путь отката.

---

## R-007: Бриф для независимой проверки в Codex (tsk-005/tsk-159, переезд на VPS)

**Симптом/повод:** оператор использует Codex как независимую подстраховку по критичным
инфраструктурным изменениям. Полный переезд LMS+SPW+TG_LMS+ContentBackbone на Timeweb Cloud
(App Platform → VPS) и перенос данных — повод для внешней проверки другим агентом.

### Autonomous workaround
Нет — эскалация по определению требует независимого агента вне контекста этой сессии.
Бриф ниже подготовлен текстом, оператор сам вставляет в свою сессию Codex.

### Operator instruction
Скопировать текст брифа (см. ответ Claude в чате на запрос «эскалировать в Codex»,
2026-07-06) и вставить в новую сессию Codex с явной задачей: независимо проверить
конфигурацию и данные без доверия к выводам Claude. Результат проверки — вернуть
оператору, при расхождениях — сообщить Claude для расследования.

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
