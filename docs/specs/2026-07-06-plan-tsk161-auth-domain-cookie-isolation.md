# Изменение: устранение widescoped-cookie риска (COOKIE_DOMAIN → auth-домен)

**Дата:** 2026-07-06
**Статус:** Ready for review → **Фаза 0-1 done, Фазы 2-3 заменены упрощённым фиксом (см. Amendment)**
**Задача:** tsk-161

---

## Amendment (2026-07-06, после Фазы 1)

При подготовке Фазы 2 обнаружено: **ADR-0014** (ContentBackbone
`docs/adr/0014-domain-layout.md`, принят 2026-04-27 — задолго до этой задачи)
уже явно предписывал `Domain=learn.victor-komlev.ru`, а НЕ
`Domain=victor-komlev.ru`:

> «Cookie scope `Domain=.victor-komlev.ru` нельзя — не должны утекать на
> WordPress; используем cookie scope `learn.victor-komlev.ru` (subdomain only)»

Текущий widescoped cookie (`victor-komlev.ru`) — это **регресс** относительно
уже принятого решения, введённый в этой же сессии при фиксе кросс-поддоменного
логина (коммит `06ddccf`), а не пробел в архитектуре, требующий нового дизайна.

`api.learn.victor-komlev.ru` — поддомен `learn.victor-komlev.ru` (совпадает по
правилу cookie domain-matching: cookie с `Domain=learn.victor-komlev.ru`
отправляется на сам этот хост и на любой хост, оканчивающийся на
`.learn.victor-komlev.ru`). Значит **`COOKIE_DOMAIN=learn.victor-komlev.ru`**
продолжает работать между `learn.*` и `api.learn.*` (не ломает исходный фикс
06ddccf), но перестаёт покрывать `www.victor-komlev.ru`/`victor-komlev.ru`
(WordPress) — закрывает репортированный риск полностью.

**Решение (оператор, 2026-07-06):** применить этот простой конфиг-фикс вместо
Фаз 2-3 плана (SPW backend-for-frontend rewrite-прокси + host-only cookie).
Код-изменений не требуется — только значение `COOKIE_DOMAIN` в prod `.env` LMS.
Остаточный риск (будущий сервис на `*.learn.victor-komlev.ru`) — минимален и
контролируем (это пространство полностью под контролем LMS/SPW-команды, в
отличие от `*.victor-komlev.ru`, где уже живёт WordPress с плагинами).

Фазы 2-3 плана ниже оставлены как задокументированная, но **не выполняемая**
альтернатива — на случай, если в будущем на `*.learn.victor-komlev.ru`
появится недоверенный сервис и понадобится более строгая изоляция (host-only
cookie).

---

## Целевая возможность

Сессия LMS/SPW больше не делит cookie-scope с любым другим сервисом на
`*.victor-komlev.ru` (сейчас — с живым WordPress-сайтом `www.victor-komlev.ru`).
Дополнительно (P0, независимо от основной фазы) — закрыт реально эксплуатируемый
CSRF-подобный gap: state-changing эндпоинты LMS сейчас не защищены от
cross-subdomain session-riding, несмотря на настроенный CORS.

---

## Текущее состояние

| Точка | Состояние |
|---|---|
| Cookie `session` | `httponly=True, secure=True, samesite="lax", domain=settings.cookie_domain` (`=victor-komlev.ru`) — ставится в 5 местах: `session.py`, `vk.py`, `tg.py`, `magic_link.py`, `test_session.py` |
| CORS | `CORS_ALLOWED_ORIGINS=https://learn.victor-komlev.ru` (прод, подтверждено на сервере), `CORSMiddleware(allow_credentials=True, allow_methods=["*"], allow_headers=["*"])` |
| Content-Type enforcement | **Отсутствует.** Grep по `app/api/` не нашёл ни одной проверки `Content-Type` заголовка. Starlette/FastAPI парсят JSON-тело (`Request.json()` → `json.loads(await self.body())`) **независимо от заголовка Content-Type** — это стандартное, широко известное поведение фреймворка. |
| CSRF-токен | Не найден нигде в коде (`grep -ri csrf` — 0 совпадений). |
| Middleware стек | Только `CORSMiddleware` + `RequestIDMiddleware` — больше ничего. |

### Уточнённая модель угрозы (важно — исправляет более раннюю неточную формулировку)

Cookie `httponly` — JS на WordPress **не может прочитать её значение** через
`document.cookie`. Кражи значения нет. Реальный вектор — **session-riding**:

1. Cookie `Domain=victor-komlev.ru` без host-only ограничения → браузер прикладывает
   её к любому запросу на `api.learn.victor-komlev.ru`, инициированному JS с
   **любого поддомена того же eTLD+1-сайта** (`www.victor-komlev.ru` в т.ч.) —
   `SameSite=Lax` **не блокирует** это, так как оба хоста — один "site" по
   определению SameSite (eTLD+1 = `victor-komlev.ru`), а не по Origin/CORS-модели.
   `SameSite=Lax` защищает только от truly cross-site атак (например, `evil.com`),
   но не от co-tenant поддоменов одного сайта.
2. CORS **должен был** быть второй линией защиты для JSON-запросов (preflight
   блокирует `Origin: https://www.victor-komlev.ru`, т.к. он не в allowlist).
   **Но:** атакующий может отправить `Content-Type: text/plain` (CORS-"simple"
   заголовок, preflight не требуется) с JSON-телом внутри — браузер отправит запрос
   без preflight, сервер (FastAPI) всё равно распарсит тело как JSON и выполнит
   бизнес-логику эндпоинта с cookie жертвы. **CORS не защищает от этого сценария
   вообще.**
3. Итог: при XSS на WordPress атакующий может заставить браузер залогиненного
   ученика выполнить **любой** state-changing запрос к LMS API (submit-answer,
   изменение профиля, привязка идентичности и т.п.) от его имени — без чтения
   cookie, в обход текущего CORS. Это **реально эксплуатируемый CSRF-путь уже
   сейчас**, не только гипотетический риск на будущее.

---

## Карта влияния

```
WordPress (www.victor-komlev.ru, XSS-уязвимость)
    │ JS: fetch("https://api.learn.victor-komlev.ru/...", {
    │        method: "POST", credentials: "include",
    │        headers: {"Content-Type": "text/plain"},  // обходит CORS preflight
    │        body: JSON.stringify({...})
    │      })
    ▼
Browser: cookie session (Domain=victor-komlev.ru) прикладывается автоматически
    │ (SameSite=Lax не блокирует — same-site по eTLD+1)
    ▼
api.learn.victor-komlev.ru — FastAPI парсит тело как JSON независимо от
Content-Type заголовка → выполняет endpoint-логику с сессией жертвы
```

Затронуты: **LMS** (cookie-выдача, CORS/Content-Type enforcement, все 3 auth-метода:
VK ID PKCE, TG initData, magic-link), **SPW** (потребитель cookie, серверные вызовы
к LMS API), DNS/SSL (для полной auth-домен фазы).

---

## Пробелы и недостающие ресурсы

| # | Пробел | Блокер? | Комментарий |
|---|---|---|---|
| G1 | Нет Content-Type enforcement на JSON-эндпоинтах | **ДА (P0)** | Единственная причина, по которой CORS не защищает уже сейчас. Дешёвый фикс — не требует архитектурных изменений. |
| G2 | Нет CSRF-токена | Нет (defense-in-depth) | G1-фикс закрывает основной эксплуатируемый путь; CSRF-токен — дополнительный рубеж, не строго обязателен при правильном G1+CORS. |
| G3 | Widescoped `Domain=victor-komlev.ru` cookie | Нет (архитектурный, не блокирует немедленно после G1) | Корневая причина, что WordPress вообще "same-site" с LMS для cookie-модели. Полный фикс — auth-домен. |
| G4 | Нет DNS-записи под новый auth-поддомен (если выбран этот путь) | ДА для Фазы архитектурного фикса | Операторское действие. |
| G5 | SSL-сертификат — текущий SAN покрывает `api.learn.*`+`learn.*`, не покрывает гипотетический `auth.*` | ДА для архитектурной фазы | Certbot `--expand` или новый сертификат. |
| G6 | Миграция live-сессий учеников при смене cookie-механизма | Нет (управляемый риск) | Разовый принудительный релогин допустим как часть релиза, если предупредить заранее (мало активных сессий ночью/в межсезонье). |
| G7 | Все 3 auth-метода (VK/TG/magic-link) пишут cookie в 5 разных местах | Нет | Единая точка (helper-функция `_set_session_cookie`) уже отсутствует — рефакторинг на общую функцию снижает риск рассинхронизации при смене схемы. |

---

## Допущения и открытые вопросы

**Q1 (закрыт рекомендацией архитектора): делать ли G1-фикс (Content-Type
enforcement) отдельным быстрым релизом до полной auth-домен миграции?**
→ **Да, обязательно и немедленно.** Это P0-находка: реально эксплуатируемый путь
уже сейчас, фикс маленький (добавить проверку `Content-Type` в зависимость/
middleware для JSON-эндпоинтов), не требует DNS/SSL/миграции сессий. Полная
auth-домен миграция (Q3 ниже) остаётся отдельной, более медленной фазой —
её откладывание не должно откладывать G1.

**Q2: нужен ли CSRF-токен как отдельный рубеж после G1?**
→ Рекомендация: не обязателен сразу. После G1 (Content-Type enforcement) CORS
снова становится эффективным рубежом для всех JSON-запросов (preflight работает
корректно). CSRF-токен — хорошая defense-in-depth практика, но не блокирует
закрытие непосредственной уязвимости. Можно добавить отдельной низкоприоритетной
задачей.

**Q3: как организовать выделенный auth-домен (архитектурная фаза)?**
→ Два реалистичных варианта:
- **(a) Backend-for-Frontend прокси в SPW:** SPW (Next.js) сам проксирует
  auth-запросы через свой собственный домен (`learn.victor-komlev.ru/api/auth/*`
  как rewrite к `api.learn.victor-komlev.ru`), тогда cookie можно ставить как
  **host-only** для `learn.victor-komlev.ru` (без Domain-атрибута вообще) — LMS API
  остаётся под `api.learn.*`, но сама cookie никогда не относится к parent-домену.
  Не требует нового поддомена/SSL — только рефакторинг SPW rewrite-правил (уже
  используется Next.js `next.config.ts` для похожих целей, см. embed headers).
- **(b) Отдельный auth-домен** (`auth.victor-komlev.ru`) с собственным
  прокси-хендшейком — сложнее (новый DNS/SSL), но чище разделяет ответственность.
- **Рекомендация архитектора: вариант (a).** Дешевле, не требует нового DNS/SSL,
  не расширяет площадь атаки новым публичным поддоменом, использует уже знакомый
  Next.js rewrite-паттерн. Вариант (b) — рассмотреть только если появится
  причина отделить auth от SPW-рантайма полностью (например, multi-frontend
  будущее).

**Q4: что делать с живыми сессиями при смене cookie-схемы?**
→ Принудительный релогин всех активных пользователей при релизе — приемлемо
(разовая операция, magic-link/VK/TG логин быстрый). Уведомить оператора заранее
для выбора低trafic окна.

---

## Решение по дублированию

Cookie выставляется в 5 разных местах (`session.py`, `vk.py`, `tg.py`,
`magic_link.py`, `test_session.py`) с идентичными параметрами
(`httponly=True, secure=True, samesite="lax", domain=...`) — уже сейчас
дублирование, независимо от этой задачи. **Решение:** вынести в общий helper
`app/services/auth/cookie.py::set_session_cookie(response, token)` — переиспользуется
всеми 5 местами, единая точка изменения при будущей смене cookie-параметров
(снижает риск рассинхронизации, если Фаза 2 (Q3) поменяет схему только частично).

---

## Этапы внедрения

### Фаза 0 — P0: Content-Type enforcement (немедленно, не блокируется Фазой 1+)

**Шаг 0.1.** Добавить проверку `Content-Type` для всех JSON-body эндпоинтов —
либо через глобальную `Depends`, либо через lightweight middleware, отклоняющий
`Content-Type` вне allowlist (`application/json`, а также `multipart/form-data`
там, где реально нужны file-uploads — messages/materials attachments уже это
используют, не трогать) с `415 Unsupported Media Type`.

**Шаг 0.2.** Тесты: POST на реальный JSON-эндпоинт (например `/auth/session/refresh`)
с `Content-Type: text/plain` + валидным JSON-телом → ожидать `415`, не 200/дальнейшую
обработку. Плюс regression-тест: тот же запрос с `Content-Type: application/json`
→ работает как раньше.

**Исполнитель:** `/fastapi-api-developer`
**Ревью:** `/lms-fastapi-techlead-code-reviewer` (обязательно — security-критичный
путь, затрагивает все JSON-эндпоинты)

**Предусловие:** нет
**Проверка готовности:** curl с `Content-Type: text/plain` на JSON-эндпоинт → 415;
существующий functional-тест-сьют не сломан (`Content-Type: application/json` —
без изменений).

---

### Фаза 1 — Рефакторинг: единый cookie-helper (подготовка к Фазе 2)

**Шаг 1.1.** Вынести общую логику `response.set_cookie("session", ...)` в
`app/services/auth/cookie.py::set_session_cookie()` + `clear_session_cookie()`.
Заменить 5 мест на вызов helper'а.

**Исполнитель:** `/fastapi-api-developer`
**Ревью:** `/pr-review` (минимум — рефакторинг без изменения поведения)

**Предусловие:** Фаза 0 (не строго обязательно, но логично последовательно)
**Проверка готовности:** существующие auth-тесты (VK/TG/magic-link/session) —
без регрессий, поведение идентично.

---

### Фаза 2 — Backend-for-Frontend прокси в SPW + host-only cookie (архитектурный фикс)

**Шаг 2.1.** SPW: настроить rewrite в `next.config.ts` — все запросы к auth-related
путям (`/api/auth/*` со стороны браузера) проксируются SPW-сервером к
`api.learn.victor-komlev.ru`, так что с точки зрения браузера auth-запросы идут
на `learn.victor-komlev.ru` (тот же origin, что и сама страница).

**Шаг 2.2.** LMS: `set_session_cookie()` (из Фазы 1) — убрать `domain=` параметр
вообще (host-only cookie). Cookie при этом ставится ответом, который браузер
получает как будто от `learn.victor-komlev.ru` (через SPW rewrite-прокси) —
host-only для этого домена, не видна `www.victor-komlev.ru` вообще.

**Шаг 2.3.** Проверить все 3 auth-метода (VK ID PKCE callback, TG initData,
magic-link) — их callback/redirect-флоу должен идти через тот же
prox-маршрут, не напрямую на `api.learn.victor-komlev.ru`.

**Исполнитель:** `/fastapi-api-developer` (LMS-сторона) +
`/telegram-ux-flow-designer`-класс не требуется (это не TG bot dialog, а web).
Frontend-часть (SPW rewrite) — `/executor-pro` (Next.js конфиг, влияет на все
auth-флоу, production-critical).
**Ревью:** `/lms-fastapi-techlead-code-reviewer` (LMS) +
`/techlead-code-reviewer` (SPW, т.к. нет специализированного SPW-reviewer skill
в реестре) — оба обязательны (пара для auth/session изменений).

**Предусловие:** Фаза 1
**Проверка готовности:** живой e2e-логин всеми 3 методами на prod/staging,
cookie в DevTools показывает host-only (`learn.victor-komlev.ru`, без
Domain-атрибута), `curl -b` с этой cookie на `www.victor-komlev.ru`-запрос
(имитация) не проходит.

---

### Фаза 3 — Прод-релиз + миграция live-сессий

**Шаг 3.1.** Уведомить оператора о разовом принудительном релогине всех активных
пользователей (окно низкого трафика).

**Шаг 3.2.** Деплой через уже существующий `deploy.sh`/`rollback.sh` (tsk-005/160) —
откат готов, если что-то пойдёт не так.

**Шаг 3.3.** Смоук: реальный логин каждым из 3 методов на живом проде, подтвердить
cookie host-only через DevTools/curl.

**Исполнитель:** `manual` (оператор — момент релиза) + `/fastapi-api-developer`
(технический деплой)
**Ревью:** `/review-gate` (финальный gate — auth/session изменение, MANDATORY)

**Предусловие:** Фаза 2
**Проверка готовности:** review-gate PASS, живой смоук всех 3 методов логина.

---

## Маршрутизация по skills

Сокращения: **FAPI** = `/fastapi-api-developer`, **LTLR** =
`/lms-fastapi-techlead-code-reviewer`, **TLR** = `/techlead-code-reviewer`,
**PRO** = `/executor-pro`, **PRR** = `/pr-review`, **RG** = `/review-gate`,
**CA** = `/context-auditor`.

| Фаза | Под-задача | Главный исполнитель | Ревью / контроль | Примечания |
|---|---|---|---|---|
| 0 | Content-Type enforcement (415 на не-JSON тела) | **FAPI** | **LTLR** (обязательно) | P0, независимо от остальных фаз |
| 0 | Тесты на Content-Type enforcement | **FAPI** | **LTLR** | Regression + negative case |
| 1 | Единый cookie-helper (рефакторинг 5 мест) | **FAPI** | **PRR** | Без изменения поведения |
| 2 | SPW rewrite-прокси для auth-путей | **PRO** | **TLR** (обязательно) | Production-critical, все 3 auth-метода |
| 2 | LMS: host-only cookie (убрать domain=) | **FAPI** | **LTLR** (обязательно) | Auth/session-критичный путь — парная связка |
| 2 | Проверка VK/TG/magic-link callback через прокси | **FAPI** | **LTLR** | 3 отдельных живых smoke-теста |
| 3 | Прод-релиз + миграция live-сессий | `manual` (оператор) + **FAPI** | **RG** (обязательно) | MANDATORY review-gate — auth/session изменение |

**Cross-cutting skills:**
- `/encoding-guard` — перед коммитом правок `.py`/`.md` с кириллицей
- `/context-auditor` — после Фазы 2 (сверка с целями tsk-161/ADR, если появится)
- `/db-check` — не требуется (нет изменений схемы БД)

---

## План проверки

| Проверка | Когда |
|---|---|
| `Content-Type: text/plain` + JSON-тело → 415 | После Фазы 0 |
| Существующие auth-тесты без регрессий | После Фаз 0-1 |
| Cookie в DevTools — host-only, без Domain | После Фазы 2 |
| Живой логин VK ID / TG / magic-link через прокси | После Фазы 2 |
| `curl` с session-cookie, отправленный как будто с `www.victor-komlev.ru` — не проходит на `api.learn.*` | После Фазы 2 (имитация угрозы) |
| review-gate PASS | Перед Фазой 3 (прод-релиз) |

---

## Риски и меры снижения

| Риск | Вероятность | Митигация |
|---|---|---|
| Content-Type enforcement (Фаза 0) ломает легитимных клиентов, использующих нестандартный Content-Type | Низкая | Все текущие клиенты (SPW, TG_LMS) используют `application/json` для JSON-тел — grep подтвердит перед мержем; file-upload эндпоинты (multipart) явно исключены из enforcement |
| SPW rewrite-прокси (Фаза 2) вносит доп. задержку в auth-флоу | Низкая | Rewrite — тонкий прокси-слой на том же Next.js сервере, не отдельный роутинг-хаб |
| VK ID/TG initData callback URL нужно перерегистрировать в внешних кабинетах (VK ID app, Telegram BotFather) при смене пути | Средняя | Проверить в Фазе 2, зарегистрировать заранее в тестовом окне |
| Живые сессии учеников инвалидируются при релизе (Фаза 3) | Высокая (ожидаемо) | Разовая операция, уведомить оператора, выбрать низкотрафичное окно |
| Content-Type фикс (Фаза 0) один не закрывает архитектурный риск полностью — если появится ДРУГОЙ обходной путь мимо CORS (не через Content-Type) | Низкая | Фаза 2 (host-only cookie) — окончательный фикс, не полагается на CORS/Content-Type как единственный рубеж |

---

## Критерии Go/No-Go

**Go (Фаза 0 — немедленно, не ждать остального плана):**
- Нет — начинать сразу, это P0.

**Go (Фаза 2 — после Фазы 1):**
- Единый cookie-helper готов и протестирован
- Подтверждено, что VK ID/TG/magic-link callback можно провести через SPW rewrite
  без регистрации нового домена во внешних кабинетах

**No-Go (стоп, эскалация оператору):**
- Content-Type enforcement (Фаза 0) ломает реальный production-трафик — откатить,
  разобраться в источнике нестандартных Content-Type
- Один из 3 auth-методов не может работать через SPW-прокси без существенного
  редизайна — пересмотреть Q3 в пользу варианта (b) (отдельный auth-домен)
- review-gate FAIL перед Фазой 3

---

## Решение по UX-сложности

Фаза 0 — невидима пользователю (только серверная валидация). Фаза 2 — единственное
видимое изменение: разовый принудительный релогин при релизе (объяснить оператору
заранее, стандартный "сессия истекла, войдите заново" экран уже существует).
Новых экранов/диалогов нет. Guard пройден.

---

*Следующий шаг: Фаза 0 (Content-Type enforcement) — можно начинать немедленно,
не дожидаясь решения по Фазе 2 (SPW rewrite vs отдельный auth-домен).*
