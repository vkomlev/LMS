# Glossary — LMS Core API

Доменные термины. Цель — однозначная трактовка в коде, документации и общении с агентами.

## Пользователи и роли

- **User** — любой человек в системе. Может иметь несколько ролей одновременно.
- **Role** — роль пользователя. Поддерживаются русские и английские имена. Известные: `student`, `teacher`, `methodist`, `admin` (и др. из таблицы `roles`).
- **Student↔Teacher link** — прикрепление ученика к преподавателю. Отдельная таблица связей.
- **Access Request** — заявка на получение роли; подтверждается методистом/админом.

## Курсы и материалы

- **Course** — курс. Поддерживает иерархию (M2M parent/child через `course_parents`) и жёсткие зависимости (`course_dependencies`).
- **Course Parent** — родительский курс; связь M2M с `order_number` для порядка.
- **Course Dependency** — зависимость «курс B требует прохождения курса A». Без самоссылок (триггер).
- **UserCourse** — привязка студента к курсу с авто-`order_number`. Нумерация обновляется триггером при удалении.
- **TeacherCourse** — привязка преподавателя к курсу. Исторически была авто-синхронизация дочерних (снята в миграции `20260127_230000`, сейчас — parent-check).
- **Material** — учебный материал курса. Типы: `text`, `video`, `link`, `pdf`, `script`, `document` (расширяются).

## Задания и проверка

- **Task** — задача (quiz). Имеет тип, solution-правила, уровень сложности.
- **Meta Task** — обёртка/группировка задач (мета-задания).
- **Attempt** — попытка решения задачи студентом. Может быть отменена (stage 3.5).
- **Task Result** — итоговый результат по задаче (агрегат попыток).
- **Hint Event** — открытие подсказки в попытке (для учёта).
- **Help Request** — запрос помощи от ученика (stage 3.8). Имеет `type` и `context` (stage 3.8.1).
- **Help Request Reply** — ответ преподавателя на Help Request.
- **Next Mode** — режим выдачи следующего задания (stage 3.9): teacher-driven / auto / by-difficulty и др.

## Инфраструктура и интеграции

- **Learning Engine** — подсистема выдачи/проверки заданий (stages 1-7 в истории миграций).
- **Import (GSheets)** — импорт курсов/материалов/задач из Google Sheets через service-account.
- **DomainError** — доменное исключение (`app/utils/exceptions.py`); всегда со `status_code`.
- **API key** — аутентификация через query-параметр `api_key`. Список валидных — в `VALID_API_KEYS`.
- **MCP PostgreSQL** — dev-инструмент для read-only SQL из AI-агентов; алиас `postgresql`.

## SPW + Auth (Phase Y-1)

- **SPW** (Student Practice Web) — веб-клиент для учеников; Next.js 15 + TS + Tailwind + shadcn. Домен `learn.victor-komlev.ru`.
- **identity_link** — таблица multi-identity: один `user_id` может иметь привязки `kind IN ('email','tg','vk')`. `UNIQUE(kind, value)`.
- **user_session** — сессия пользователя; `token_hash BYTEA`, TTL 15 мин / 30 дней refresh, `revoked_at`.
- **magic_link** — одноразовая ссылка для email-авторизации; `token_hash`, `expires_at`, `consumed_at`.
- **CurrentUser** — dataclass dep: `id`, `role`, `is_service`, `identities: list[IdentityLinkRead]`.
- **X-API-Key** — header для service-level access от TG_LMS bots / ContentBackbone CLI; bypasses per-user IDOR check.
- **IDOR** (Insecure Direct Object Reference) — проверка: `student_id == current_user.id` на всех id-параметрах. CI gate: IDOR sweep test.
- **guest_session / guest_attempt** — анонимный пользователь; атрибуция при регистрации.
- **audit_event** — append-only лог: login/logout/identity-change; immutable trigger.
- **product_event** — funnel-аналитика; partitioned by month.
- **Phase Y-1** — фаза проекта: 5 Alembic-миграций + auth-расширение LMS API. Исполнитель: `/executor-pro`. Tech-spec: [docs/specs/2026-04-27-tech-spec-Y1-auth-extension.md](../specs/2026-04-27-tech-spec-Y1-auth-extension.md).
- **FernetService** — шифрование VK access_token (FERNET_MASTER_KEY в .env); `vk_access_token_enc BYTEA` в `identity_link`.

## Типы задач (Task types)

- **SA** (Short Answer) — короткий ответ; автопроверка.
- **SC** (Single Choice) — один правильный вариант; автопроверка.
- **MC** (Multiple Choice) — несколько правильных вариантов; автопроверка.
- **SA_COM** (Short Answer with Code/Comment) — короткий ответ с полем-комментарием. **По умолчанию — автопроверка** по `accepted_answers`/regex (как SA); задание считается выполненным без участия преподавателя. Преподаватель может опционально пересмотреть результат (regrade). Уходит на **обязательную** ручную проверку, только если задание помечено `solution_rules.manual_review_required=true` (tsk-230) — тогда авто-вердикта нет, и вступает оптимистичный зачёт (см. ниже). **Комментарий ИЛИ файл обязателен** (tsk-419). Контракт: [docs/frontend-contract-sa-com.md](../frontend-contract-sa-com.md). FSM: [docs/ai/design/teacher-queue-states.md](design/teacher-queue-states.md).
- **TBL_COM** (Table with Comment, tsk-366) — табличный ответ: `value` одной строкой, ячейки ряда через пробел, ряды через перевод строки; сравнение поячеечное, правила — тот же блок `short_answer`, что у SA/SA_COM. Во всём остальном ведёт себя как SA_COM, включая обязательность комментария-или-файла. `table.columns=1` + мини-тесты Python (tsk-383): при многострочном ответе строка не режется по пробелу — целая строка (с внутренними пробелами) = одна ячейка, чтобы фраза «Первое число больше» (вывод одного запуска программы) не дробилась на слова; однострочный ответ по-прежнему режется по словам. Плюс fallback: если построчное сравнение не даёт балл, ответ сверяется ещё и целиком против эталона целиком (как SA_COM) — инвариант «TBL_COM не строже SA_COM на том же правиле».
- **TA** (Text Answer) — развёрнутый ответ (эссе). **По умолчанию — ручная проверка** (`is_correct=NULL` до grade). Автопроверка (regex/ИИ) — на будущее.
- **`manual_review_required`** (флаг `SolutionRules`, tsk-230) — единый переключатель обязательной ручной проверки. При `true` авто-вердикт не выставляется, и работа попадает в обязательную очередь преподавателя, даже если тип авто-проверяем. Ось обязательности — **этот флаг, а не тип задания** (tsk-420): предикат очереди (`teacher_queue_service.mandatory_review_sql`, общий для бота и портала) берёт `TA` безусловно, а `SA`/`SA_COM`/`TBL_COM` — только с флагом. Default `false`. Читается в `checking_service` для SA/SA_COM/TBL_COM; TA манулен по умолчанию независимо от флага.
- **Оптимистичный зачёт** (optimistic-pass, tsk-210) — для `TA` и для `SA_COM`/`TBL_COM` без авто-вердикта (`is_correct=None`, в т.ч. при `manual_review_required=true`) на приёме ответа ставится `score=max_score, is_correct=true`, чтобы учебный поток не блокировался; `checked_at` при этом остаётся пустым — работа висит в очереди, преподаватель может зачёт снять через `/regrade`. У плоского `SA` этого нет (tsk-438). Перекрывается гейтами вложения (tsk-227), доказательства (tsk-419) и непустоты развёрнутого ответа (tsk-654: `TA` без текста и без файла → `score=0, is_correct=false`), если те не выполнены.

## Гейты (процессные)

- **Spec-Gate** — фиксация scope и acceptance criteria до реализации.
- **Execution-Gate** — имплементация + minimal smoke.
- **Review-Gate** — независимое PASS/FAIL до merge в `main`.
- **Merge-Gate** — интеграция только при PASS review-gate.

## Даты и время

- **Naive datetime** — `datetime` без tzinfo; в проекте — reject или normalize до сравнения.
- **Raw SQL text()** — SQLAlchemy `text(...)` вернёт `str`, если тип колонки не явный; перед сравнением с `datetime` — обязательная нормализация.
- **SLA/TTL compare** — любое сравнение даты-времени в бизнес-логике требует explicit type-guard в сервисе.
