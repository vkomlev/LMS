# План: Календарь LMS (tsk-021) — расписание, гибкая явка, посещаемость

**Дата:** 2026-07-26
**Скилл:** `/architect-system-analyst` (report-only)
**Задача:** tsk-021 (блокирует tsk-410)

## Execution Posture

`report-only`. Этот документ — архитектурный план, готовый к исполнению следующими
чипами по фазам. Ни одна миграция, ни одна строка production-кода этим чипом не
написана и не применена.

## Problem Framing

**Objective:** дать LMS источник истины «кто занимается с кем, когда, и кто реально
присутствовал на конкретном занятии» — сейчас этого источника нет вообще (ни модели
группы, ни модели расписания, ни модели посещаемости).

**Non-goals (явно вне охвата этого плана):**
- Групповые занятия (несколько учеников на одном слоте) — оператор подтвердил
  индивидуальную модель «один ученик — один преподаватель — один слот».
- Видео-интеграция с Яндекс.Телемостом (создание комнат API, запись, участники
  через API конференции) — ссылка на комнату константная, посещаемость фиксируется
  отдельным действием в LMS, не через видео-API.
- Оплата/биллинг занятий — не упомянут оператором, не проектируется.
- Автоматическое перепланирование при массовых конфликтах (например, отпуск
  преподавателя на 2 недели, N учеников) — MVP покрывает единичный перенос по
  инициативе ученика.

**Success criteria:**
1. Администратор создаёт индивидуальный слот расписания (ученик, преподаватель,
   день недели, время, таймзона) через API.
2. Система генерирует конкретные занятия-инстансы из слотов на горизонт вперёд.
3. Ученик получает напоминание и может подтвердить/отказаться/перенести явку из SPW.
4. Через 10 минут после начала неотвеченное занятие подсвечивается преподавателю
   и ученику как «пропущено».
5. У преподавателя есть панель занятия: кто должен прийти, кто пришёл, кто не
   пришёл, ручное добавление ученика на конкретное занятие.
6. tsk-410 может запросить «кто присутствовал на занятии X» одним детерминированным
   API-вызовом.

## AS-IS Snapshot

### Существующие сущности, пригодные как основа
- `Users` (`app/models/users.py`) — нет поля timezone/tz_name. Roles: student/teacher/
  methodist/admin через `user_roles` (M2M, `association_tables.py`).
- `t_student_teacher_links` (`association_tables.py:49`) — M2M student↔teacher,
  `linked_at`. Это уже пара «ученик-преподаватель», на которую можно вешать слот
  расписания. Нет ограничения «1 преподаватель на ученика» — связь multi-multi,
  план должен это учитывать (слот привязан к конкретной паре, а не просто к ученику).
- `Courses.access_level` enum содержит значения `group_sessions`, `personal_teacher`
  (`app/models/courses.py:32-40`) — курс уже умеет быть маркирован как «требует живых
  занятий», но никакой сущности времени/слота/присутствия к этому не привязано.
  Это подтверждает, что калейдарь — ожидаемое, но не реализованное расширение модели
  курса, а не изолированная фича.
- `Notifications` (inbox-семантика Y-4, `app/models/notifications.py`) — готовый
  канал in-app уведомлений ученику: `user_id`, `kind`, `title`, `payload JSONB`,
  `read_at`. Подходит для «напоминание о занятии» и «занятие пропущено» без новой
  таблицы уведомлений.
- `escalation_service.py` — готовый рабочий паттерн периодического тика:
  `AsyncIOScheduler` (APScheduler) + `pg_try_advisory_xact_lock` для
  multi-worker-safe cron внутри каждого gunicorn-воркера. Это шаблон для
  «напомнить о занятии» и «пометить no-show через 10 минут» — не изобретать новый
  механизм периодичности.
- `methodist_notify_service.py` — паттерн сервиса, который дергает `Notifications`
  из cron-тика.

### Чего нет вообще
- Групп/расписания/посещаемости — ни одной модели, ни одной миграции.
- Понятия «часы работы школы» (operating hours).
- Понятия таймзоны пользователя (все `DateTime(timezone=True)` в UTC, но нет
  поля, в какой локальной зоне живёт ученик/преподаватель — важно для отображения
  времени слота).
- Канала push-напоминаний вне LMS inbox: в TG_LMS (`d:\Work\TG_LMS\src`) нет
  реализации reminder/scheduler — проверено, файлов с этой темой не найдено.
  Значит канал «напоминание о занятии» либо (а) только in-app баннер SPW поверх
  `Notifications`, либо (б) требует нового кода в TG_LMS. Оператор не уточнял канал
  явно (п.4 требований говорит «напоминание/плашка» и отдельно «SPW спрашивает при
  открытии») — трактуем как SPW-приоритет для MVP, TG-дублирование — Фаза 4 (опционально).

## Gaps and Ambiguities

Зафиксированы как факты для уточнения при взятии в работу, не додуманы вслепую:

1. **Таймзона.** Нет `users.timezone`. Слот хранится в конкретной таймзоне
   (скорее всего Europe/Moscow как единственной для MVP, раз оператор не поднимал
   вопрос мультизоны). Решение по умолчанию: **захардкодить `Europe/Moscow`**
   на уровне `operating_hours` и `lesson_slot` в Фазе 1, добавить `timezone` в
   `users`, только если реально понадобится мульти-таймзонный ученик — не проектировать
   заранее.
2. **Канал напоминания вне SPW.** Не подтверждено, нужен ли TG-пуш параллельно
   in-app баннеру. Вынесено в Фазу 4 (опционально), не блокирует MVP.
3. **Формат «перенос».** Оператор описал «отказ → система предлагает другое время
   в рамках расписания/доступности», но не уточнил: это (а) любой свободный слот в
   `operating_hours` этой пары, или (б) только следующий регулярный слот. Решение
   по умолчанию для MVP: показать ближайшие N свободных получасовых интервалов в
   `operating_hours`, где ни ученик, ни преподаватель не заняты другим занятием —
   не привязывать «перенос» жёстко к тому же дню недели.
4. **Кто задаёт `operating_hours`.** Общие для всей школы или per-teacher? Оператор
   не разграничил. Решение по умолчанию: **общие для школы** (одна строка
   конфигурации), per-teacher — не проектируется, пока не запрошено.
5. **Что считается «слот занят» при гибкой отработке вне расписания.** Нужна
   проверка коллизий и преподавателя, и ученика на пересекающиеся `lesson_occurrence`
   — иначе один и тот же преподаватель получит два занятия в одно время.

## Current-State Assessment

Оценка: **чистый лист** в части домена. Риск низкий (нет legacy-данных для миграции,
нет обратной совместимости, которую нужно беречь), но объём немаленький — 3 новых
сущности + cron-инфраструктура + API-контракты для 3 ролей (admin/student/teacher) +
UI в SPW. Не проектируется одним PR — обязательна фазовая поставка (см. ниже).

## Target Architecture

### Схема БД (4 новые таблицы, Alembic-миграции; auth/course-модели не трогаются)

**`operating_hours`** — часы работы школы (одна активная конфигурация, MVP — без
per-teacher разреза):
- `id` PK
- `weekday` SMALLINT (0-6)
- `start_time` TIME, `end_time` TIME
- `timezone` TEXT NOT NULL DEFAULT `'Europe/Moscow'`

**`lesson_slot`** — закреплённый повторяющийся слот пары ученик-преподаватель:
- `id` PK
- `student_id` FK→`users.id`, `teacher_id` FK→`users.id`
- `weekday` SMALLINT (0-6)
- `start_time` TIME, `duration_minutes` SMALLINT
- `timezone` TEXT NOT NULL DEFAULT `'Europe/Moscow'`
- `is_active` BOOLEAN DEFAULT true (деактивация вместо удаления — сохраняет историю
  прошлых `lesson_occurrence`, привязанных к этому слоту)
- `created_by` FK→`users.id` (admin/operator, кто создал)
- `created_at`
- CHECK: `student_id <> teacher_id`
- Индекс на `(teacher_id, weekday, start_time) WHERE is_active`

**`lesson_occurrence`** — конкретное занятие (сгенерированное из слота или
ad-hoc отработка):
- `id` PK
- `slot_id` FK→`lesson_slot.id` NULL (NULL = ad-hoc отработка вне расписания)
- `student_id` FK→`users.id`, `teacher_id` FK→`users.id` (денормализовано из слота
  для ad-hoc случая и для устойчивости к будущей деактивации слота)
- `scheduled_at` TIMESTAMPTZ NOT NULL, `duration_minutes` SMALLINT
- `status` ENUM: `scheduled`, `confirmed`, `declined`, `rescheduled`, `no_show`,
  `completed` — `completed` проставляется либо по факту `attendance=joined` после
  окончания времени, либо явно учителем/оператором позже (не проектируется в MVP,
  фиксируется как поле на будущее для tsk-410)
- `rescheduled_to_id` FK→`lesson_occurrence.id` NULL (цепочка переноса)
- `created_at`, `updated_at`
- UNIQUE-ish защита от двойного бронирования: partial exclusion на пересечение
  времени по `teacher_id` и отдельно по `student_id` среди `status NOT IN
  ('declined','rescheduled')` — через `EXCLUDE USING gist` (нужен `btree_gist`) или
  проверка на уровне сервиса + advisory lock, если `EXCLUDE` избыточен для MVP-нагрузки
  (единичные операторские вставки, не высокая конкурентность) — **решение по
  простоте: сервисная проверка коллизии в транзакции, без `EXCLUDE`, эскалировать
  до constraint, если в проде возникнут дубли**.

**`attendance_event`** — журнал действий по посещаемости конкретного occurrence
(append-only, не перезаписываем — как `audit_event`):
- `id` PK
- `occurrence_id` FK→`lesson_occurrence.id`
- `actor_user_id` FK→`users.id` (кто нажал: ученик/преподаватель/оператор)
- `action` ENUM: `joined`, `declined`, `manual_present`, `manual_absent`,
  `auto_no_show`
- `created_at`
- Текущий статус на `lesson_occurrence.status` — проекция последнего события,
  не пересчёт истории при каждом чтении (простая денормализация, не event-sourcing).

### Cron-инфраструктура (переиспользует паттерн `escalation_service.py`)

Два периодических тика, каждый — новый `AsyncIOScheduler` job с собственным
`pg_try_advisory_xact_lock`-ключом (не переиспользовать ключ Y-6):

1. **`lesson_occurrence_generator_tick`** — генерирует `lesson_occurrence` из
   активных `lesson_slot` на скользящий горизонт (например, +14 дней), идемпотентно
   (`ON CONFLICT` по `(slot_id, scheduled_at)`).
2. **`lesson_reminder_tick`** — находит occurrence в ближайшие N минут без
   `attendance_event`, шлёт `Notifications(kind='lesson_reminder')` ученику.
3. **`lesson_no_show_tick`** — находит occurrence `now() > scheduled_at + 10min`
   без `joined`/`manual_present` события, проставляет `status='no_show'`,
   пишет `attendance_event(action='auto_no_show')`, шлёт
   `Notifications(kind='lesson_missed')` ученику и преподавателю.

(2) и (3) можно объединить в один tick с двумя ветками SQL, чтобы не плодить
lock-ключи — решение по простоте, финализировать при исполнении Фазы 2.

### API-контракты (по слоям `api → services → repos → models`, `/api/v1` префикс)

| Роль | Эндпоинт | Назначение |
|---|---|---|
| admin | `POST /lesson-slots` | создать слот (student_id, teacher_id, weekday, time, duration) |
| admin | `GET/PATCH/DELETE /lesson-slots/{id}` | правка/деактивация слота |
| admin | `GET/PUT /operating-hours` | часы работы школы |
| student | `GET /me/lesson-occurrences?from=&to=` | предстоящие/прошедшие занятия ученика |
| student | `POST /lesson-occurrences/{id}/attendance` | `{action: joined\|declined}` |
| student | `GET /lesson-occurrences/available-slots?occurrence_id=` | кандидаты для переноса в рамках `operating_hours` без коллизий |
| student | `POST /lesson-occurrences/{id}/reschedule` | `{new_scheduled_at}` → создаёт новый occurrence, помечает старый `rescheduled` |
| student | `POST /lesson-occurrences/ad-hoc` | отработка вне расписания в `operating_hours` (если слот свободен у обоих) |
| teacher | `GET /teacher/lesson-occurrences?from=&to=` | занятия преподавателя, с признаком no-show (10-мин порог) |
| teacher | `POST /teacher/lesson-occurrences/{id}/attendance` | ручная отметка присутствия/отсутствия ученика |
| teacher | `POST /teacher/lesson-occurrences/{id}/add-student` | добавить ученика на конкретное занятие вручную |

### Каналы напоминаний

MVP (Фаза 2): только `Notifications` inbox → SPW читает через существующий
inbox-API и рендерит баннер/плашку. TG-дублирование — опционально, Фаза 4, только
если оператор подтвердит нужду (в TG_LMS сейчас нет reminder-инфраструктуры вообще
— это был бы новый компонент, не расширение существующего).

## Simplification Decisions

- Индивидуальные слоты как FK-пара, не отдельная сущность «группа» — групповых
  занятий нет по требованию оператора.
- `operating_hours` — одна школьная конфигурация, не per-teacher — не запрошено.
- Коллизии слотов — сервисная проверка в транзакции, не `EXCLUDE` constraint —
  нагрузка (единичные операторские вставки) не оправдывает сложность GiST-индекса
  в MVP; эскалировать при первом инциденте дублей.
- `attendance_event` append-only + денормализованный `status` на occurrence —
  не полноценный event-sourcing с пересчётом, следуя паттерну `audit_event`.
- Таймзона — захардкожен `Europe/Moscow` на уровне записи, не `users.timezone` —
  не запрошено мультизонье.

## Duplication Risk Decision

- Cron/advisory-lock паттерн — **переиспользовать** `escalation_service.py` как
  референс (must-centralize паттерн, не копипаст новой инфраструктуры).
- Уведомления — **переиспользовать** `Notifications` inbox (must-centralize,
  тот же канал, что и `methodist_notify_service`), не создавать новую таблицу.
- Напоминания в Telegram — **temporarily local / not-in-scope**: TG_LMS не имеет
  инфраструктуры, добавлять её ради этой задачи в MVP не оправдано (может подождать
  Фазу 4 с явным запросом оператора).

## Contract Changes

- Новый namespace `/api/v1/lesson-slots`, `/api/v1/lesson-occurrences`,
  `/api/v1/teacher/lesson-occurrences`, `/api/v1/operating-hours` — требует
  обновления `docs/openapi.json` (авто-генерация FastAPI) и
  `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md` (cross-project
  contract, обязательное обновление по правилу `CLAUDE.md` LMS после факта поставки
  каждой фазы, добавляющей эндпоинт).
- Новые таблицы → обязательное обновление
  `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-db-schema.md` после
  каждой Alembic-миграции.
- `docs/ai/data-model.md` и `docs/ai/architecture.md` — добавить новый раздел
  «Календарь/посещаемость» по завершении Фазы 1-2 (не в этом плановом документе —
  он делается при исполнении, не при планировании).

## Product Review Snapshot

**User value:** ученик перестаёт теряться в «когда у меня занятие и был ли я
засчитан», преподаватель получает единый список «кто должен прийти сегодня» вместо
ручного слежения за парами в голове/внешней таблице. tsk-410 («подвести итоги
занятия») становится реализуемым — сейчас у него физически нет источника данных.

**Acceptance path:** оператор (admin) заводит слоты на реальных учеников →
ближайшее занятие генерируется автоматически → ученик видит напоминание и жмёт
«я на занятии» → преподаватель видит подтверждённый список в своей панели.

**Scope tradeoffs:** MVP не покрывает групповые занятия и мульти-таймзонье —
осознанный вырез по прямому требованию оператора, не технический долг.

## Engineering Review Snapshot

**Architecture:** 4 новые таблицы, 0 изменений в существующих моделях (кроме
опциональной `users.timezone`, отложенной). Слой `api → services → repos → models`
не нарушается. Cron — copy-the-pattern от `escalation_service.py`, не новый
механизм.

**Trust boundaries:** student-эндпоинты должны проверять, что `occurrence`
принадлежит вызывающему студенту (IDOR-риск — тот же класс, что и существующий
IDOR sweep test из Phase Y-1, новые эндпоинты обязаны попасть под этот CI-гейт).
teacher-эндпоинты аналогично скоуплены на `teacher_id`.

**Test strategy:** date/time safety — критично (проект уже имеет инцидентную
историю сравнения `str`/`datetime`, см. `docs/ai/architecture.md` Date/Time
safety). Обязательные негативные тесты для генератора occurrence (naive datetime,
DST-переход, если когда-либо расширится за пределы Europe/Moscow) и для 10-минутного
порога no-show (граница ровно 10:00, 9:59, 10:01).

**Rollback:** каждая Alembic-миграция — с явным `downgrade()`, таблицы новые →
откат безопасен (нет данных для сохранения на момент выката Фазы 1).

## Implementation Phases

Каждая фаза — самостоятельно ценная, тестируемая, с ответственным skill'ом.

### Фаза 1 — Модель данных + admin-создание расписания
**Объём:** миграции `operating_hours`, `lesson_slot`, `lesson_occurrence`,
`attendance_event`; occurrence-generator cron (пока без reminder/no-show);
admin API `POST/GET/PATCH/DELETE /lesson-slots`, `GET/PUT /operating-hours`.
**Exit criteria:** admin может создать слот через API, через 14 дней в БД видно
сгенерированные occurrence на будущее; unit-тесты генератора (идемпотентность,
DST/timezone edge cases).
**Ответственный skill:** `/fastapi-api-developer` (миграции — под `/db-check`
протоколом записи).
**Разблокирует:** ничего пользователю напрямую, но это твёрдый фундамент —
без него Фазы 2-3 невозможны.

### Фаза 2 — Явка ученика + напоминания + no-show
**Объём:** `POST /lesson-occurrences/{id}/attendance`, `GET
/me/lesson-occurrences`; reminder-tick и no-show-tick (переиспользуют advisory-lock
паттерн); `Notifications(kind='lesson_reminder'|'lesson_missed')`.
**Exit criteria:** ученик видит предстоящее занятие в SPW, может подтвердить/
отказаться, через 10 минут неотвеченное занятие помечено `no_show` и видно в
inbox.
**Ответственный skill:** `/fastapi-api-developer`, SPW-сторона — `/eng-review`
(архитектура) → исполнение в SPW-проекте (вне LMS-репо, отдельная задача).
**Разблокирует:** первый пользовательский эффект — ученик реально что-то видит
и подтверждает.

### Фаза 3 — Панель преподавателя + перенос/отработка
**Объём:** `GET /teacher/lesson-occurrences`, `POST
/teacher/lesson-occurrences/{id}/attendance`, `.../add-student`; ученический
`available-slots` + `reschedule` + `ad-hoc`.
**Exit criteria:** преподаватель видит список занятий на день с подсветкой no-show,
может вручную отметить/добавить ученика; ученик может перенести/отработать вне
слота без коллизий.
**Ответственный skill:** `/fastapi-api-developer`.
**Разблокирует:** **это фаза, которая напрямую разблокирует tsk-410** — после
неё существует API-источник «кто присутствовал на occurrence X»
(`lesson_occurrence.status` + `attendance_event`), которого tsk-410 ждёт.

### Фаза 4 (опционально, по запросу оператора) — TG-дублирование напоминаний
**Объём:** новый компонент в TG_LMS для push-напоминаний параллельно SPW-баннеру.
**Exit criteria:** ученик получает напоминание в Telegram, если не открыл SPW.
**Ответственный skill:** `/telegram-ux-flow-designer` → `/fastapi-api-developer`
(если нужен новый LMS→TG_LMS webhook).
**Не блокирует tsk-410** — чистое UX-улучшение канала доставки.

## Risk Register

| Риск | Митигация |
|---|---|
| Двойное бронирование преподавателя/ученика при ad-hoc отработке | Сервисная проверка коллизии в транзакции (Фаза 3); эскалация до `EXCLUDE USING gist`, если возникнут дубли в проде |
| Таймзона захардкожена, ученик из другого пояса | Осознанный вырез MVP; поле легко добавить позже (nullable, backfill default) |
| Cron generator не успевает на горизонт при большом числе слотов | Горизонт настраиваемый (env), `LIMIT`+пагинация по образцу `escalation_cron_tick` |
| Совпадение advisory-lock ключа с существующими (`0x59365453` Y-6) | Явно задокументировать новые ключи в коде рядом с константой, как это сделано в `escalation_service.py` |
| SPW должен реализовать новый UI (баннер/подтверждение) — отдельный репозиторий, отдельный релизный цикл | Фаза 2 явно указывает SPW-сторону как отдельную ответственность `/eng-review` + SPW-исполнение, не блокирует LMS backend поставку |

## Validation Plan

- Alembic upgrade/downgrade cycle на каждую миграцию (`/db-check` протокол).
- Unit-тесты генератора occurrence: идемпотентность повторного тика, weekday
  edge cases (воскресенье=0 vs 6 — зафиксировать конвенцию явно при исполнении).
- Date/time negative tests (naive datetime, str-сравнение) — обязательны по
  проектному стандарту.
- IDOR sweep на новые student/teacher эндпоинты (CI gate, существующий с Y-1).
- Smoke: создать слот → дождаться generated occurrence → confirm → проверить
  `attendance_event` → проверить no-show tick на неотвеченном occurrence.

## Handoff Artifacts

- Этот документ — вход для Фазы 1 исполнителя (`/fastapi-api-developer`).
- Дочерние задачи в `D:\Work\Root\tasks/` на каждую фазу (созданы этим же чипом,
  см. tsk-021 «Декомпозиция»).
- `docs/ai/data-model.md` / `architecture.md` обновляются при исполнении Фазы 1
  (не сейчас — это план, не факт).

## Go/No-Go

**GO** на Фазу 1 как первый безопасный шаг: чистый лист, нет legacy-данных для
миграции, риск изолирован (новые таблицы, ноль изменений в существующих моделях).
Фазы 2-4 — go после подтверждения exit-criteria предыдущей фазы, не параллельно.
