# Архитектурный разбор — SPW Портал преподавателя (tsk-298)

> Скилл: `/architect-system-analyst`. Дата: 2026-07-19. Задача: `tsk-298` (Root-трекер).
> Пульт: `D:\Work\Root\docs\lms-product-roadmap.md` (Эпик B, блокирует Эпик C).
> Статус: **AS-IS завершён; TO-BE = GO для Фаз 0-1; охват MVP (Фаза 2 vs 2+3) ждёт решения оператора.**

---

## 1. Problem Framing

- **Objective.** Дать преподавателю веб-кабинет в SPW (`learn.victor-komlev.ru`). Сегодня teacher-facing — только TG-бот. Портал разблокирует Эпик C (отчёты, сводки, дашборд, inbox, сообщения).
- **Scope.** SPW (новый UI), LMS API (отдать роль в `/me` + централизовать роль-гейт + позже — net-new backend отчётов/посещаемости/расписания), TG_LMS (не трогаем — остаётся для пушей).
- **Non-goals (MVP).** Календарь/расписание, посещаемость, прогноз-аналитика, оплата — отдельные задачи/волны, не в этом MVP.
- **Success criteria.** Преподаватель входит на `learn.victor-komlev.ru`, видит свою очередь проверки, оценивает/переоценивает работы (балл + комментарий + вложения), видит своих учеников + нагрузку + запросы помощи + переписку — **всё без TG-бота**. Измеримо: полный цикл оценки работы проходит в вебе; паритет с ядром ботового флоу проверки.
- **NFR.** Performance — низкая нагрузка (N=4 препода). Security — роль-гейт на сервере (клиентский гейт = только UX). Maintainability — переиспользовать инфраструктуру SPW + эндпоинты LMS. Reliability — на уровне ученического приложения.

## 2. Context Anchors (прочитано)

- Cross-project mirror: `STATE.md`, `CHANGELOG.md`, `contracts/lms-api.md`, `contracts/lms-db-schema.md`.
- 3 AS-IS-разведки (агенты): LMS-код (`app/`), SPW-структура, TG_LMS teacher-боты.
- Прод-БД `learn` (read-only MCP): web-готовность teacher-аккаунтов.

## 3. AS-IS Snapshot

### C4-Context
Акторы: **ученик** (веб + бот), **преподаватель** (сегодня только бот), **методист** (бот), **админ**. Системы: LMS API (FastAPI), SPW (ученический веб), TG_LMS (4 бота), PostgreSQL `learn`, Redis.

### C4-Container
- **LMS API** — владелец всех teacher-эндпоинтов `/api/v1/teacher/*`, auth, БД.
- **SPW (Next 16, App Router)** — на 100% ученический; потребляет `/me` + student-эндпоинты по httpOnly-cookie.
- **TG_LMS teacher-бот** — тонкий клиент над `/teacher/*` через сервисный `api_key` (query-параметр) + явный `teacher_id`.
- **PostgreSQL `learn`** — `roles`/`user_roles`/`teacher_courses`/`student_teacher_links`/`task_results`/`help_requests`/`messages`.

### Что УЖЕ построено (факты)
- **Teacher REST API — полный:** reviews (`claim-next`/`claim`/`release`/`grade`/`regrade`/`pending-count`), `teacher/workload`, help-requests (`claim-next`/list/get/`close`/`release`/`reply`), `task-limits/override`, `students/{id}/assignments`, teacher_courses CRUD, `/users/{teacher_id}/students` (ростер), `messages/send/to-students/{teacher_id}`, `methodist/escalations/pending`.
- **RBAC в БД:** `roles` + `user_roles` (M2M); `teacher_courses` (триггер — только корневые курсы); `student_teacher_links` (ученик↔препод).
- **Веб-auth готов:** `user_session` (httpOnly-cookie, access 24ч / refresh 30д), `get_current_user` = cookie OR Bearer OR `X-API-Key`. Teacher-эндпоинты **identity-based** → веб-cookie их дёргает напрямую, они не «ботовые».
- **Teacher-аккаунты web-ready:** 4 препода / 2 методиста / 1 админ — **у всех есть email + TG + ФИО** → magic-link-вход в веб работает уже сегодня, миграция аккаунтов не нужна.
- **Инфраструктура SPW переиспользуема:** `ApiClient` (3-контекстный), TanStack Query, shadcn/base-ui, дизайн-токены oklch, VPS-деплой (`/opt/spw`, systemd, nginx); `lib/api-types.ts` **уже содержит всю teacher-поверхность** (сгенерирована из OpenAPI).
- **Ядро ботового флоу** (что переносим): review queue + grade/regrade (балл/коммент/рубрики/вложения, TTL-локи), help-requests (reply/close/override), переписка + рассылка, списки учеников + статистика, workload-дашборд.

### Что ОТСУТСТВУЕТ (факты → gaps)
- **SPW:** ноль teacher-маршрутов; серверный guard = no-op (`proxy.ts` возвращает `NextResponse.next()`); `GET /api/v1/me` **не отдаёт роль** (`CurrentUser` без role).
- **LMS:** нет dependency `require_teacher` — ACL размазан по хендлерам (ручная сверка). Роль препода **захардкожена `id==3`** в TG-боте (3 места).
- **Посещаемость** — нет ни таблицы, ни модели, ни эндпоинта (нигде).
- **Единый teacher-агрегат успеваемости/отчётов** — нет (есть только `/teacher/workload`).
- **Расписание/календарь** — нет нигде (greenfield).

## 4. Gaps & Ambiguities (нужно подтверждение оператора)

1. **Охват MVP** — какие функции в первом релизе (см. Go/No-Go).
2. **Как отдавать роль** — расширить `/me` полем `roles: string[]` (рекоменд.) vs отдельный эндпоинт.
3. **Где роль-гейт** — серверный в SPW (`proxy.ts`/layout server-component) + централизованный `require_teacher` в LMS. Клиентский гейт — только UX, не безопасность.
4. **Мульти-роль** — пользователь может быть и преподавателем, и учеником (совмещение через `user_roles` M2M штатно). Как портал переключает контекст teacher↔student.

## 5. Anti-patterns & Duplication

| Находка | Класс | Решение |
|---|---|---|
| Проверка роли троится: хардкод `id==3` (бот) + размазанный ACL (LMS) + новый гейт (SPW) | **must-centralize** | Один источник роли (`/me.roles`) + один `require_teacher` в LMS; SPW читает роль из `/me` |
| Два клиента над `/teacher/*` (бот + веб) | **acceptable divergence** | Осознанно: бот = пуши/быстрый мобильный вход, веб = богатый интерактив; оба тонкие над одним API |
| Риск дублировать логику оценки в вебе | **must-reuse** | Веб вызывает те же эндпоинты, свою grade-логику не пишет |

## 6. Ревью по 6 измерениям

| Измерение | Статус | Обоснование |
|---|---|---|
| Scalability | **OK** | N=4 препода, низкая нагрузка |
| Security | **WATCH** | Роль-гейт обязан быть на сервере (клиентский = UX). LMS-ACL — реальная граница, уже есть, но размазан → централизовать `require_teacher`. IDOR закрыт серверно |
| Maintainability | **OK / WATCH** | Переиспользование инфраструктуры; WATCH — централизация роль-хардкода |
| Performance | **OK** | — |
| Deployment / Operability | **OK** | Тот же VPS SPW, новая route-группа; нового сервиса нет |
| Documentation / Observability | **WATCH** | `audit_event` покрывает grade; новые report-эндпоинты (позже) тоже нужно аудировать |

## 7. Target Architecture (TO-BE, MVP)

- **Портал — новая route-группа `app/(teacher)/` в ТОМ ЖЕ SPW-приложении.** Переиспользует `ApiClient`/auth/токены/ui-kit. Новые teacher query-хуки поверх существующих `/teacher/*`. Деплой — тот же VPS, отдельного приложения/сервиса нет.
- **LMS:** отдать роль в `/me` (`roles: string[]`, аддитивно) + ввести `require_teacher`/`require_role` dependency (централизация) + перевести размазанный ACL на него (не ломая контракт).
- **SPW:** роль-осознанный роутинг — после логина, если у пользователя роль teacher, доступна teacher-зона; гейт `(teacher)` по роли из `/me`; **реализовать серверный guard** (сейчас no-op).

### Simplification Decisions (что сознательно НЕ вводим в MVP)
Календарь, посещаемость, новый микросервис, отдельное приложение, новую БД под портал, прогноз-аналитику. Всё teacher-facing переиспользует готовый backend и инфраструктуру SPW.

## 8. Contract Changes

- `GET /api/v1/me` → **добавить `roles: string[]`** (аддитивно, не ломает; SPW перегенерирует `api-types.ts`).
- Внутр.: новый dependency `require_teacher`/`require_role` в LMS (не меняет внешний контракт).
- (Опц. удобство) `GET /api/v1/me/teacher/summary` — агрегат для лендинга портала — **или** переиспользовать `/teacher/workload`.
- **Позже (Эпик C, не MVP):** таблицы+эндпоинты посещаемости (`tsk-022`), расписания (`tsk-021`), report/прогноз-агрегат (`tsk-023`).

## 9. ADR (черновики — финализировать в `docs/ai/adr/` после решений оператора)

- **ADR-draft-A.** Портал преподавателя — route-группа в существующем приложении SPW, не отдельное приложение. *Контекст:* инфраструктура и деплой готовы, N мал. *Следствие:* минимум нового кода, единый деплой; риск смешения student/teacher-кода — изолируется route-группой.
- **ADR-draft-B.** Роль отдаётся через `/me.roles[]`; безопасность держит серверный ACL (`require_teacher` в LMS + серверный guard в SPW); клиентский guard — только UX.
- **ADR-draft-C.** Веб переиспользует существующие `/teacher/*`; веб — тонкий клиент; бот сохраняется для пушей. Backend не переписывается.

## 10. Implementation Phases

| Фаза | Содержание | Exit criteria | Rollback |
|---|---|---|---|
| **0. Enabler (LMS)** | `/me.roles[]` + `require_teacher` dep + перевод ACL на него | `/me` отдаёт roles; teacher-эндпоинты через central guard; тесты зелёные | Аддитивно; откат dependency |
| **1. Скелет + роль-гейтинг (SPW)** | route-группа `(teacher)`, серверный роль-guard, layout/nav препода, login→teacher-лендинг | Препод входит, видит пустой teacher-shell; ученик в зону не попадает | Route-группа изолирована, снять |
| **2. Review queue MVP (SPW)** | pending-список + claim-next + grade/regrade + просмотр вложений | Препод проходит полный цикл оценки в вебе, паритет с ядром бота | Фича-флаг/страница |
| **3. Ростер + нагрузка + помощь + сообщения** | список учеников + статистика, workload-панель, help-requests reply/close/override, переписка | Паритет с ботом по ежедневным операциям препода | По страницам |
| **Позже (Эпик C)** | Календарь/посещаемость/отчёты | — | Отдельные задачи `tsk-021/022/023` (backend greenfield) |

Каждая фаза: живой прогон на проде под аккаунтом преподавателя (сессия Виктора), `/review-gate`, деплой в этой же сессии (правила operator-handoff, ветвь А).

## 11. Risk Register

| ID | Риск | Митигация |
|---|---|---|
| R1 | Мульти-роль (teacher+student) — переключение контекста | Явный переключатель роли в шапке |
| R2 | Серверный guard SPW сейчас no-op | Если только клиентский — URL угадываемы, но серверный ACL режет данные; всё равно реализовать серверный guard |
| R3 | Хардкод роли `id==3` в боте | Бот на MVP не трогаем, централизуем постепенно; зафиксировать техдолг |
| R4 | `blocked_limit`-заявки в боте: reply/close выключены | Зеркалить то же правило в вебе |

## 12. Validation Plan
Живой прогон на проде под одним из 4 teacher-аккаунтов — оценить реальную работу из pending-очереди. Переиспользовать harness SPW `e2e-live` (playwright-live).

## 13. NFR compliance — соответствует (performance/security/maintainability/reliability закрыты разделами 6-7).

## 14. Пересмотр сайзинга
В приоритизации (пульт) `tsk-298` оценён **XL**. AS-IS показал, что backend готов → **грейдинг-MVP (Фазы 0-2) — ближе к M-L, не XL.** Полный паритет с ботом (Фаза 3) добавляет M. XL остаётся только если тянуть в MVP Эпик C (календарь/посещаемость/отчёты) — что не рекомендуется.

## 15. Go / No-Go
- **GO** для Фаз 0-1 (enabler `/me.roles` + `require_teacher` + скелет с роль-гейтом) — риск низкий, всё аддитивно.
- **NEEDS-DECISION** по охвату MVP: Фаза 2 (только очередь проверки) vs Фазы 2+3 (полный паритет с ботом). Решение оператора.
- Handoff-пакет для реализации: этот документ + `tsk-298` + пульт-роадмап + 3 AS-IS-разведки.
