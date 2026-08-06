# План внедрения — tsk-303: Единый inbox преподавателя (Поток A + Поток B)

**Дата:** 2026-08-06
**Задача:** `D:\Work\Root\tasks\tsk-303-...md`
**Skill:** `/change-plan-architect`
**Статус:** готов к исполнению — 6 фаз, каждая отдельным PR

## Целевая возможность

Preподаватель получает единый экран заявок: (A) лестница помощи по заданию —
уровень 1 (текст → авто-закрытие) → «Вернуть заявку» (KPI) → уровень 2
(индивидуальный разбор по вебинар-ссылке → оценка ученика) → уровень 3
(эскалация методисту при негативной оценке); (B) обращения о
проблемах/контенте/идеях фич — новая сущность, инбокс методиста/админа с
точкой входа у преподавателя. KPI возвратов виден и преподавателю, и
методисту.

## Текущее состояние

- `help_requests` (модель `app/models/help_requests.py`) — заявки `manual_help`
  (ученик жмёт «Запросить помощь» на `HintPanel.tsx`) и `blocked_limit`
  (авто при исчерпании попыток). Учительский CRUD полный: claim/release,
  list/detail, close, reply (`app/api/v1/teacher_help_requests.py` +
  `app/services/help_requests_service.py`). `reply_help_request` уже принимает
  `close_after_reply: bool` — **сейчас это ВЫБОР учителя** (кнопки «Ответить» /
  «Ответить и закрыть» / «Закрыть без ответа» в
  `HelpRequestInlineReply.tsx`), а не авто-поведение.
- Студенческая сторона — **только создание** заявки:
  `POST /learning/tasks/{id}/request-help` (`app/api/v1/learning.py`).
  Ни чтения текущего статуса/ответа заявки, ни «Вернуть заявку», ни
  какого-либо read-эндпоинта для конкретной пары (student, task) не
  существует. Ученик узнаёт об ответе учителя только через
  `/me/notifications` (kind=`help_request_replied`) с CTA «Перейти к
  заданию» → `/courses/{uid}/task/{ext}` — но сама задача (`HintPanel.tsx`)
  ответ **не показывает и не перезапрашивает** (баннер «отправлено» —
  локальный transient state, не персистентный).
- Методист: `GET /methodist/escalations/pending` читает `notifications`
  WHERE `kind IN ('review_escalated','course_pending_review',
  'broken_media_links')` — паттерн эскалации уже есть и переиспользуем
  (`app/services/methodist_notify_service.py`: `inbox_service.create_for_user`
  всем `role=methodist`). Closed-действия для этих kind нет — методист либо
  читает через `/me/notifications`, либо (для нашего случая) закрывает
  через уже существующий `POST /teacher/help-requests/{id}/close` — методист
  уже входит в ACL `can_access_help_request` (роль `methodist` — bypass).
- SPW: `app/(teacher)/teacher/help-requests/` (список+карточка) и
  `app/(methodist)/methodist/help-requests/` (тот же паттерн, методист видит
  всё через ACL) уже есть и задеплоены (tsk-298 Фаза 3-Ⅱ). Компоненты
  `HelpRequestsList`, `HelpRequestPanel`, `HelpRequestInlineReply`,
  `HelpRequestsList` переиспользуемы.
- Поток B (`feedback_reports`) — **в коде нет ни модели, ни роутера, ни
  UI**. Разведка подтверждена оператором 2026-08-06.
- Alembic head (LMS, dev, проверено `alembic heads`): `tsk427_profile_extra_fields`.

## Карта влияния

| Слой | Что меняется |
|---|---|
| LMS DB | 1 миграция: 4 аддитивные колонки `help_requests` + расширение CHECK `request_type`; 1 миграция: новая таблица `feedback_reports` |
| LMS API | Новые эндпоинты: 3 студенческих (`reopen`/`request-individual-review`/`rate-review`) + 1 read (`GET .../help-request` для task-страницы) + 1 учительский (`webinar-link`) + 2 KPI (свой/методист) + 3 Поток B (create/list/close). Изменение существующего: `reply_help_request` — авто-close для `manual_help` (проверить TG_LMS-потребителя перед хардкодом) |
| LMS notifications | Новый `kind='help_request_escalated'` (методисту) + расширение IN-списка `methodist_escalations.py` |
| SPW student | `HintPanel.tsx`/task-страница получают персистентный блок статуса заявки (новый компонент+хук на новом read-эндпоинте): «Вернуть заявку», «Запросить индивидуальный разбор», ссылка «Перейти к разбору» + оценка |
| SPW teacher | `HelpRequestInlineReply.tsx` — новая ветка для `individual_review` (форма ссылки вместо текстового ответа) + `manual_help` авто-close без выбора; новый инбокс-экран (табы A/B); `TeacherReopenKpiCard` |
| SPW methodist | `TeacherReopenKpiTable`; методистский `/methodist/help-requests` — бейдж «эскалация»/фильтр `individual_review`; новый `/methodist/feedback-reports` |
| Cross-project | Проверить `TG_LMS` на прямой вызов `/teacher/help-requests/{id}/reply` (бот преподавателя) — если использует `close_after_reply=false` осознанно, форс-close на сервере сломает бот-сценарий |

## Пробелы и недостающие ресурсы

**Блокирующие:**
- **[БЛОКЕР]** Нет проверки, использует ли `TG_LMS` (бот преподавателя)
  эндпоинт `POST /teacher/help-requests/{id}/reply` с
  `close_after_reply=false` как штатный сценарий. Если да — форс-close на
  сервере для `manual_help` изменит поведение бота без его ведома
  (cross-project breaking change). Закрыть **до** Фазы 3 (`/fastapi-api-developer`,
  grep `TG_LMS/**/*.py` на `help-requests` + `reply`).
- **[БЛОКЕР]** Нет read-эндпоинта «текущая заявка помощи по (student, task)»
  — без него у студенческого UI физически негде показать статус/ответ/кнопки
  «Вернуть заявку»/«Запросить разбор»/оценку. Закрывается в Фазе 2.

**Небlokирующие (допущения, см. ниже):** ACL создания Поток B, точный state
конкретного экрана «единый inbox».

## Допущения и открытые вопросы

Развилки Потока A **все решены оператором 2026-08-06** (см. файл задачи,
раздел «Открытые вопросы… РЕШЕНЫ»). Остаются два **небlokирующих**
допущения — беру дефолт, реверсивно, не переспрашиваю (rutina):

1. **ACL создания `feedback_reports`.** Задача явно называет точку входа
   «у преподавателя», методист/админ — приёмники. Дефолт: создавать могут
   `teacher`/`methodist`/`admin` (не студент — Поток B про систему/контент,
   не про задание конкретного ученика). Список видит `methodist`/`admin`
   целиком, `teacher` — только свои созданные. Закрывать может автор или
   `methodist`/`admin`.
2. **Форма «единого экрана».** Держать два раздельных пункта нав-меню
   (`/teacher/help-requests`, новый `/teacher/feedback-reports`) не даёт
   ощущения «единого inbox» — по UX-guard (см. ниже) объединяю в один
   роут `/teacher/inbox` с двумя табами («Вопросы учеников» / «Обращения»),
   старый `/teacher/help-requests` редиректит на первый таб (deep-link не
   ломается, старые ссылки из уведомлений продолжают работать).

## Решение по дублированию

- KPI считается **одним** агрегирующим SQL в `help_requests_service.py`
  (`get_reopen_kpi(db, teacher_id=None)`); оба потребителя (учитель-себе,
  методист-все) вызывают одну функцию с разным охватом — не дублировать
  запрос в двух сервисах.
- Эскалация методисту (уровень 3) переиспользует **существующий**
  `inbox_service.create_for_user` + существующий
  `GET /methodist/escalations/pending` (только новый `kind` в IN-списке) —
  не создаётся отдельный канал/таблица уведомлений методиста.
- Закрытие эскалации уровня 3 переиспользует **существующий**
  `POST /teacher/help-requests/{id}/close` (методист уже в ACL) — не
  создаётся отдельный close-эндпоинт для методиста.
- SPW: список/карточка Потока B переиспользуют вёрстку-паттерн
  `HelpRequestsList`/`HelpRequestPanel` (не копипаст с нуля).

## Этапы внедрения

### Фаза 1 — LMS: миграция + модель (Поток A) — **ВЫПОЛНЕНА 2026-08-06**
Аддитивные колонки `help_requests`: `webinar_link TEXT NULL`,
`review_understood BOOLEAN NULL`, `escalated_to_methodist_at TIMESTAMPTZ NULL`;
CHECK `request_type` расширен значением `individual_review`; новый CHECK
`ck_help_requests_webinar_link_type` (ссылка только у заявки этого класса и
только непустая). Rollback: DROP колонок, без backfill — безопасно на любом
объёме.

**Отступление от исходного плана (осознанное, разрешено файлом задачи):**
вместо счётчика `reopen_count SMALLINT` на заявке сделана **таблица истории
`help_request_reopens`** (`request_id` CASCADE, `teacher_id` SET NULL,
`reopened_at`). Причины: (а) возврат начисляется тому, чей ответ не помог, а по
ACL к заявке может ответить и методист, и преподаватель по связи с учеником —
счётчик на строке заявки эти случаи не различает и повесил бы чужой возврат на
`assigned_teacher_id`; (б) «возвраты за месяц» через голый счётчик невыразимы.
Счётчик выводится `COUNT(*)`, второго источника правды нет.
**Это меняет формулировки фаз 2 и 4 ниже** — см. пометки там.

**Итог:** цикл upgrade/downgrade/upgrade на dev чист, откат верифицирован через
MCP независимо (0 остатков), 10 тестов схемы + полный pytest зелёные. Прод не
тронут. Артефакт: `reviews/2026-08-06-tsk303-phase1-help-ladder-schema.md`.

### Фаза 2 — LMS: студенческие эндпоинты Потока A
- `GET /learning/tasks/{task_id}/help-request` — текущая заявка
  (student_id=self) для пары (student, task): статус, история ответов,
  число возвратов (`COUNT(*)` из `help_request_reopens`), `webinar_link`
  (только если `status='open'`), `review_understood`. Закрывает блокирующий
  пробел read-стороны.
- `POST /learning/help-requests/{id}/reopen` — «Вернуть заявку»: только
  `status='closed' AND request_type='manual_help'`, owner=student;
  `status→open`, **строка в `help_request_reopens`** (`teacher_id` = тот, чей
  ответ не помог: `closed_by` заявки, при системном закрытии — fallback на
  `assigned_teacher_id`), push учителю (`kind='help_request_reopened'`).
- `POST /learning/help-requests/{id}/request-individual-review` — гейт
  `status='open'` И **есть хотя бы одна строка в `help_request_reopens`** по
  этой заявке; `request_type→individual_review`, push учителю.
- `POST /learning/help-requests/{id}/rate-review` `{understood: bool}` —
  гейт `request_type='individual_review' AND webinar_link IS NOT NULL`;
  `true`→`close_help_request` (авто, `closed_by=None`, как tsk-339);
  `false`→`escalated_to_methodist_at=now()` + `kind='help_request_escalated'`
  всем methodist (переиспользует паттерн `methodist_notify_service`).
**Готовность:** pytest на все 4 эндпоинта (happy + ACL-negative + гейты
состояний), openapi regenerated.

⚠ **Дата/время (находка review-gate фазы 1).** `escalated_to_methodist_at` и
`reopened_at` — TIMESTAMPTZ. Сравнение даты из `text(...)`-запроса с `now` без
нормализации уже давало в этом проекте прод-500
(`docs/ai/ERRORS.md`, 2026-03-03); в `help_requests_service.py` для этого есть
`_normalize_due_at`. Читать эти колонки сырым SQL — только через него.

### Фаза 3 — LMS: учительская сторона + авто-close
- Пре-условие: закрыт БЛОКЕР TG_LMS (см. «Пробелы»).
- `reply_help_request`: для `request_type='manual_help'` —
  `close_after_reply` форсируется `True` сервером (не читает клиентский
  флаг), для `blocked_limit`/`individual_review` — поведение не меняется.
- Новый `POST /teacher/help-requests/{id}/webinar-link {url: str}` —
  гейт `request_type='individual_review' AND status='open'`; пишет
  `webinar_link`, шлёт сообщение+inbox студенту (переиспользует
  `MessagesService`/`inbox_service`, как `reply_help_request`), НЕ закрывает.
- `methodist_escalations.py`: IN-список `kind` + `'help_request_escalated'`.
**Готовность:** pytest на форс-close (в т.ч. регрессия существующих
manual_help тестов), webinar-link happy+ACL+гейт, эскалация видна в
`/methodist/escalations/pending`.

### Фаза 4 — LMS: KPI + Поток B (модель/API)
- `help_requests_service.get_reopen_kpi(db, teacher_id=None, since=None)` —
  агрегат по `help_request_reopens` (`GROUP BY teacher_id`, окно по
  `reopened_at`; индекс `idx_help_request_reopens_teacher_time` заложен под это)
  + `GET /teacher/kpi/reopen-summary` (self) +
  `GET /methodist/kpi/teacher-reopens` (role methodist/admin, по всем).
- Новая модель `app/models/feedback_reports.py` + миграция (аддитивная
  новая таблица, риск нулевой): `id, type CHECK IN ('bug','content',
  'feature_idea'), status CHECK IN ('open','closed') DEFAULT 'open',
  author_id FK users, body TEXT, course_id/material_id/task_id FK NULL,
  created_at, updated_at, closed_at, closed_by FK users NULL,
  resolution_comment TEXT NULL`.
- `app/services/feedback_reports_service.py` + `app/api/v1/feedback_reports.py`:
  `POST /feedback-reports` (teacher/methodist/admin), `GET /feedback-reports`
  (methodist/admin — все; teacher — только свои `author_id=self`),
  `POST /feedback-reports/{id}/close` (автор или methodist/admin).
**Готовность:** pytest KPI-агрегата (несколько учителей/несколько
возвратов — сверка чисел), pytest Поток B CRUD + ACL-negative.

### Фаза 5 — SPW: студенческий UI (Поток A)
- Новый хук `use-help-request-status.ts` на `GET .../help-request`.
- `HintPanel.tsx` (или соседний новый компонент на task-странице):
  персистентный блок — статус заявки, ответ учителя, кнопка «Вернуть
  заявку» (после закрытия), кнопка «Запросить индивидуальный разбор»
  (после `reopen_count>=1`), ссылка «Перейти к разбору» +
  экран/форма оценки «Всё понятно?» (после захода по ссылке).
- `gen:api-types` regenerated.
**Готовность:** vitest на все состояния кнопок (happy path гейтов из
Фазы 2), tsc/eslint/build чисто.

### Фаза 6 — SPW: учительский+методистский UI (Поток A завершение + Поток B)
- `HelpRequestInlineReply.tsx`: ветка `individual_review` (форма ввода
  ссылки вместо текстового ответа), `manual_help` — одна кнопка
  «Ответить» (без выбора close/no-close — сервер сам закрывает).
- `TeacherReopenKpiCard` на `/teacher` (self-KPI).
- `/teacher/inbox` (переименование/редирект `/teacher/help-requests` →
  таб 1) + таб 2 «Обращения» (новый `FeedbackReportsList`+форма создания).
- `/methodist`: `TeacherReopenKpiTable` (новая секция/страница) +
  бейдж «эскалация» на `/methodist/help-requests` + новый
  `/methodist/feedback-reports` (список+закрытие).
**Готовность:** vitest полный набор, tsc/eslint/build, живой прогон
(следующий раздел).

## Маршрутизация по skills

Сокращения — из `skill-routing-standard.md §2`.

| Фаза | Под-задача | Главный исполнитель | Ревью / контроль | Примечания |
|---|---|---|---|---|
| Pre-1 | Блокер: проверить TG_LMS-потребителя `reply` | **FAPI** | — | grep + чтение кода бота, до Фазы 3 |
| 1 | Миграция `help_requests` (4 колонки + CHECK) | **FAPI** | DB (pre+post) → PRR → RG | аддитивно, без backfill |
| 2 | 4 студенческих эндпоинта + сервис-логика гейтов | **FAPI** | PRR | ACL self-only, гейты состояний — race-condition-aware (advisory lock по паттерну `close_blocked_limit_if_resolved`) |
| 3 | Форс-close + webinar-link эндпоинт + IN-список методиста | **FAPI** | TLR (меняет поведение существующего эндпоинта — не рутина) | форс-close — behavior change, нужен строгий ревью |
| 4 | KPI-агрегат + модель/API `feedback_reports` | **FAPI** | DB (миграция) → PRR | новая таблица — низкий риск, но ACL на CRUD проверить |
| 5 | SPW студенческий UI (task-страница) | **executor-pro** (SPW, многофайловый UI + новый хук) | PRR | UX-guard применён (см. ниже) |
| 6 | SPW учительский/методистский UI + единый inbox-экран | **executor-pro** | PRR | редирект старого роута — проверить deep-link из notifications CTA не ломается |
| Каждая фаза | Merge-gate перед интеграцией | — | **RG** | обязателен перед `main` (правило LMS CLAUDE.md) |
| Финал (все 6 фаз) | Сверка с исходными целями задачи | — | **CA** | `/context-auditor` — все решения оператора 2026-08-06 учтены |

**Cross-cutting:**
- `/encoding-guard` — после правок RU-текстов в новых schemas/UI-строках.
- `/db-check` — обязателен до/после миграций Фаз 1 и 4 (`app.audit_actor`
  не забыть, если пишется скриптом, а не через API-путь).
- `/context-auditor` — перед финальным merge-gate, сверка с
  «Открытые вопросы… РЕШЕНЫ» из файла задачи.

## План проверки

1. `pytest` — полный набор после каждой LMS-фазы (не только новые тесты).
2. `npx vitest run` — полный набор после каждой SPW-фазы.
3. `tsc --noEmit` + `eslint` + `next build` — SPW-фазы.
4. `openapi.json` regenerated — все LMS-фазы с новыми/изменёнными эндпоинтами.
5. **Живой прогон полного цикла** (после Фазы 6, реальный браузер,
   MCP `Claude_Browser`/`claude-in-chrome`, конкретный ученик+task_id):
   ученик просит помощь → учитель отвечает текстом (авто-закрытие
   видно) → ученик жмёт «Вернуть заявку» → у ученика появляется кнопка
   «Индивидуальный разбор» → учитель шлёт ссылку → ученик переходит →
   оценивает «непонятно» → методист видит эскалацию в
   `/methodist/escalations/pending` (или help-requests с бейджем) →
   закрывает. Отдельно: учитель создаёт обращение Поток B → методист
   видит в `/methodist/feedback-reports` → закрывает.
6. `/review-gate` после каждой фазы (не только в конце) — план явно
   против одного огромного диффа.

## Риски и меры снижения

| Риск | Мера |
|---|---|
| Форс-close ломает TG_LMS-бота (если он опирается на `close_after_reply=false`) | Блокирующая проверка ДО Фазы 3 (см. «Пробелы») |
| Гонка «два reopen подряд» / «rate-review дважды» теряет инкремент | `pg_advisory_xact_lock(student_id, task_id)` в новых сервис-функциях — тот же паттерн, что `close_blocked_limit_if_resolved` |
| Редирект `/teacher/help-requests`→`/teacher/inbox` ломает deep-link из уже отправленных push/notifications с `payload` без явного request_id-роута | Редирект НЕ меняет query-параметры/hash; CTA notifications ведут на task-страницу (не на help-requests роут вовсе) — риск низкий, но проверить `resolveNotificationCta` вручную в Фазе 6 |
| KPI-агрегат читает большой объём `help_requests` без индекса под `reopen_count>0` | При появлении реальных данных — оценить `EXPLAIN`, индекс добавить отдельной миграцией post-MVP, не блокировать Фазу 4 |

## Критерии Go/No-Go

- Все 6 фаз: pytest + vitest зелёные, `/review-gate` PASS с сохранённым
  файлом в `reviews/`.
- Живой прогон полного цикла (раздел «План проверки» п.5) пройден без
  ручных обходов.
- Блокер TG_LMS закрыт (проверка выполнена, решение зафиксировано —
  либо форс-close безопасен, либо бот адаптирован в той же фазе).
- `docs/ai/PROJECT_MEMORY.md`/cross-project `contracts/lms-api.md` обновлены
  по новым эндпоинтам (правило LMS CLAUDE.md).

## Решение по UX-сложности

- **Убрано:** выбор «Ответить»/«Ответить и закрыть» для `manual_help` —
  явного продуктового смысла у выбора не было (оператор зафиксировал
  жёсткое правило «текст → авто-закрытие»), лишний клик убран.
- **Оставлено как отдельный шаг (`justified`):** экран оценки «Всё понятно?»
  после перехода по вебинар-ссылке — не сворачивается в сам факт перехода,
  потому что это единственный сигнал для эскалации методисту (без явной
  оценки уровень 3 не сработает).
- **Объединение в `/teacher/inbox`:** два потока — одна точка входа с
  табами, а не два отдельных пункта меню (см. допущение №2) — сокращает
  навигационную нагрузку преподавателя, оба потока в одном месте, как и
  просит формулировка задачи «единый inbox».
