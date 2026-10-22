# Прочёска: запросы без полного порядка (tsk-996)

**Контекст.** 22.10 в `me_service._BLOCKED_COURSES_SQL` и `CourseDependenciesRepository.list_dependencies` нашёлся боевой дефект (4af0a4f, `reviews/2026-10-22-flaky-and-tails-review.md`): запрос без ORDER BY, порядок строк решал план PostgreSQL, и движок мог называть ученику разную блокирующую зависимость. Здесь весь `app/` прочёсан на тот же класс дефекта.

Ветка `claude/order-by-sweep` от `main` (3ccbc67). Дифф: `reviews/2026-10-22-order-by-sweep.diff`.

## Как искали

1. **Механический инвентарь.** Скрипт `scripts/order_by_inventory.py` (`python scripts/order_by_inventory.py [--all]`) разбирает `app/` через `ast`, миграции и `app/tests` пропускает. Он находит:
   - SQL-строки: `text(...)`, константы `*_SQL`, f-строки и конкатенации;
   - цепочки SQLAlchemy `select(...)`.

   Для каждого запроса скрипт помечает признаки: ORDER BY и его последний ключ, DISTINCT / DISTINCT ON / GROUP BY / LIMIT, а также как потребляется результат в функции (`.first()`, `.scalar()`, `[0]`, `.all()`, цикл).

   Механически отсеиваются:
   - агрегаты без GROUP BY;
   - EXISTS / `SELECT 1`;
   - поиск по `id` / `external_uid` / `uid` без JOIN;
   - запросы, у которых ORDER BY уже заканчивается на id.

   Всего найдено **842 запроса**, после отсева осталось **574 кандидата**.
2. **Классификация вручную.** Каждый кандидат прочитан по коду: сам запрос, что делается с результатом и кто вызывает, вплоть до эндпоинта. Работу делали 6 агентов по непересекающимся файлам. По ходу они нашли 10 мест, которые скрипт пропустил: окна `row_number`/`LAG`, `relationship` без `order_by`, сортировку в Python, наследующую порядок SQL, LATERAL. Ревью добавило ещё 4.
3. **Правки и тесты.** 5 агентов по непересекающимся группам файлов. Каждую классификацию перепроверяли по коду ещё раз (одно место переведено из А в В). Затем независимое ревью и поправки по нему.

## Итог по классам (всего 589 мест)

| Класс | Сколько | Что сделано |
|---|---|---|
| **А** — от порядка меняется поведение | **50** | исправлены все 50, на каждое есть тест, который без правки красный |
| **Б** — порядок виден пользователю и может «прыгать» | **86** | исправлены 84, 2 оставлены оператору |
| **В** — безобидно | **123** | только в отчёт, с причиной |
| **Х** — исключено при чтении (уникальный ключ/UNIQUE, агрегат, EXISTS, множество, полный ORDER BY) | **330** | — |

Правки минимальные: в конец существующего ORDER BY добавлен ключ-добивка (id / PK) в направлении последнего ключа. Там, где ORDER BY не было, он поставлен в том порядке, который давал обычный план: Sort+Unique — по ключам DISTINCT, скан PK — по PK, чтение кучи — примерно по id. Порядок учебных элементов (`node_sequence`, `_ordered_material_rows`, `task_key`, DISTINCT ON попыток в движке) проверен: ключи там уже полные, ничего не правилось.

## Боевые дефекты класса А: что могло происходить

Во всех случаях речь о **ничьей** по ключу сортировки. Она возникает при вставках в одной транзакции (у них одинаковый `now()`), при двух занятиях в одно время, у тёзок, при пакетной проверке и при массовом зачёте. Без ничьей поведение не менялось.

**Ученик**
- **Лимит попыток** (`GET /attempts/{id}`, `/finish`). `results[0]` брался без порядка, поэтому `attempts_used`, лимит и `last_based_status` могли считаться по другому заданию попытки.
- **Открытая попытка** (`start-or-get-attempt`, повтор `retrieval_service._course_attempt`). При двух открытых попытках с равным `created_at` ответ уходил то в одну, то в другую.
- **Курсы ученика для движка** (`user_courses_repo.get_user_courses`). Ключ `order_number, added_at` неполный, а курсы, добавленные одной транзакцией, имеют одинаковый `added_at`. Порядок обхода курсов (next-item) решал план.
- **Правила авто-назначения** (`assignment_rules`: answer / course_failed / quiz_scale) и **доназначение зависимостей**. Порядок срабатывания определял `order_number` новых курсов в плане ученика. При двух правилах на один курс в событии оказывался то один `rule_id`, то другой.
- **Переопределения требований** (`requirement_override._RESOLVE_SQL`, DISTINCT ON). Когда у подкурса два родителя на одной глубине и записи сделаны в одной транзакции, план решал **уровень** требования (demoted/promoted) и причину, которую видят ученик и учитель.
- **«Продолжить с места»** (`/me/last-position`) и **«сейчас / позади N» в карточке курса** (`course_position.anchor_for`). При пакетной проверке ученик мог видеть разное место.
- **Опрос «Будете на занятии?»** (`absence_poll_service`). Ответ ученика привязывался к одному из двух одновременных занятий случайно: уведомление получали не те преподаватели, причина пропуска записывалась не туда.
- **Авто-явка** (`get_current_scheduled_for_student`). При перекрытии окон (early_grace) явка ставилась то на одно занятие, то на другое.
- **Выдача ДЗ после занятия** (`lesson_attendance_cron`). После простоя ДЗ привязывалось к одному из двух закончившихся занятий, а от этого зависели срок и `occurrence_id`.
- **Запись на слот / поиск занятия** (`list_active`, `list_for_teacher`, ведущие занятия). При двух слотах или занятиях в одно время ученик попадал в разные.
- **Темп в мастерстве тем** (`topic_mastery` LAG). При равных отметках времени нулевой промежуток доставался разным заданиям.

**Преподаватель / методист**
- **claim-next заявок о помощи и работ на проверку** (`teacher_queue_service`). При равных приоритете, сроке и времени двум преподавателям, двум нажатиям, выдавалась та запись, которую выбрал план. Тест без правки выдавал **5 разных заявок под 5 планами**.
- **Кому назначается заявка о помощи** (`resolve_assigned_teacher` → `student_teacher_links.list_teachers`, запасной путь `teacher_courses_service.list_teachers`). Если у ученика 2 и больше преподавателей, заявка и уведомление уходили разным людям.
- **Какую заявку обновить по событию** (`help_requests` LIMIT 1), **какая открытая ручная помощь вернётся**, **какие ответы попадут в промпт черновика подсказки** (`hint_drafts`).
- **Эскалация методисту** и **пачка код-ревью** (граница LIMIT): кто попадёт в текущий тик.
- **Сводка и план занятия**:
  - «предыдущее занятие» и окно ДЗ;
  - серия пропусков (`attendance_service`);
  - «первая попытка → пора усложнить» (`row_number` без `tr.id`);
  - «застрял на задании X» (`stuck_tasks`);
  - какое достижение «назвать вслух» (`lesson_plan` DISTINCT ON).
- **Сигналы пробелов** (`learning_gap_signals` ×4, `learning_gaps`). При ничьих на границе top-N план решал, **кому заведётся сигнал**.
- **Уведомления**:
  - владелец утерянного вложения в тексте уведомления (`attachment_audit` ×3);
  - «где встретилась битая ссылка» (`link_audit`);
  - примеры тёзок в тревогах о начислениях (`charge_cron`, `charge_service`).
- **Боты**:
  - пачка напоминаний, предпочтений и эскалаций (`student_lesson_reminders`, `schedule_preferences`, `methodist_escalations`) при равном `modified_at` одного тика;
  - пачка гостей воронки (`quiz_funnel_bot.list_due`).
- **Inbox сообщений**: `last_message` диалога (`row_number` по `sent_at` без id).

## Выбор порядка, который стоит подтвердить оператору

Правки сделаны, выбор детерминированный. Везде, где можно было определить, взят **тот порядок, который давал обычный план**.

| Место | Что выбрано при ничьей | Альтернатива |
|---|---|---|
| `student_teacher_links.list_teachers` → назначение заявки | преподаватель с меньшим `teacher_id` (порядок PK, его же даёт обычный план) | первый/последний по `linked_at` |
| `teacher_courses_service.list_teachers` (запасной путь) | `linked_at DESC`, при равенстве больший id | — |
| `lesson_attendance_cron` (ДЗ после двух занятий) | хронологически, ДЗ получает **раннее** занятие (как давал обычный план) | позднее: «ученик живёт следующим занятием». Агент сначала выбрал его, координатор вернул прежнее |
| авто-явка при перекрытии окон | раннее (идущее) занятие | — |
| `course_position.anchor_for` | задание, из них с меньшим `task_id` (как давал обычный план; ревью вернуло это после варианта «больший id») | самый дальний по учебному порядку элемент: выбор в Python |
| `/me/last-position` при равном ts | задание раньше материала, бóльший id сдачи (совпадает с обычным планом) | — |
| `requirement_override._RESOLVE_SQL` | при полной ничьей побеждает **бóльший** id записи, то есть вставленная позже. Обычный план отдавал меньший, так что **поведение меняется**. Это согласуется с действующим правилом «более позднее решение побеждает» (`created_at DESC`) | меньший id |
| `lesson_plan` (достижение «назвать вслух») | бóльший `achievement_id` | — |
| сводка: предыдущее из одновременных занятий | бóльший id | — |
| `stuck_tasks` | `last_at DESC, task_id DESC` | — |

## Оставлено на решение оператора (не правилось)

1. **`courses_repo.get_course_tree` (`GET /courses/{id}/tree`).** Дети узла идут в порядке плана, примерно в порядке кучи `course_parents`. Правка `parent_course_id, order_number NULLS LAST, course_id` поставит их как в `/children`, но **видимый порядок дерева изменится**.
2. **Курсор `before` ленты преподавателя (`GET /teacher/activity-feed`).** Курсор `< :before` строгий и только по времени, поэтому события с тем же временем на стыке страниц **теряются**. Добивка ORDER BY сделана, но нужен составной курсор `(ts, id)`. Это меняет контракт API для SPW, задача межпроектная.
3. **`subscription_service.purchasable_plans` (`GET /payments/plans`).** Если в группе больше одного активного тарифа с `match_kind IS NULL`, план на витрине задваивается: NULL не конфликтуют в `uq_pricing_tariff_axis_point`. При оплате выигрывает самый дорогой. Порядок добит, задвоение не трогалось.
4. **`user_courses_repo` bulk/reorder (`POST /users/{id}/courses/bulk`, `PATCH .../reorder`).** Ответ упорядочен по `course_id`, как при скане PK. Предлагалось `order_number`, но это меняет видимый порядок.

## Порядок в ответах API (для SPW / бота)

Почти везде правка лишь фиксирует порядок при ничьих: **старый порядок — как выберет план, новый — по id в направлении основного ключа**. Привычный порядок без ничьих не меняется. Затронутые эндпоинты:

- **ученик:** `/me/notifications`, `/me/history`, `/me/identities`, `/me/courses`, `/me/homework`, `/me/lesson-occurrences`, `/me/lesson-occurrences/bookable`, `/me/children`;
- **преподаватель:**
  - `/teacher/help-requests`;
  - `/teacher/reviews/pending`;
  - `/teacher/activity-feed`;
  - `/teacher/lesson-occurrences/{id}/summary` и `/plan`;
  - `/teacher/students/{id}/urgent-channels`;
  - `/task-results/by-pending-review`;
- **сообщения:** `/messages/inbox`, `/messages/senders`, `/messages/unread/by-sender`;
- **маркетолог:** `/marketer/charges`, `/marketer/price-overrides`, `/marketer/pricing/courses`, `/marketer/payments/reminders`, `/marketer/payments/export`, `/marketer/students/search`, `/marketer/lead-sources`;
- **платежи и подписки:** `/payments/plans`, `/subscriptions/students/{id}`;
- **куратор:** `/curator/board`, `/curator/coverage`, `/curator/weekly-report`, `/curator/derive-*`;
- **методист:** `/methodist/breaks`, `/methodist/schedule-slot-requests`, `/methodist/ready-for-harder-summary`, `/learning-gaps/*`, `/topic-mastery/*`;
- **пользователи и курсы:** `/users/`, `/users/search`, `/users/duplicates`, `/users/{id}/roles/`, `/users/{id}/courses`, `/users/{id}/students`, `/users/{id}/parents`, `/courses/{id}/users`, `/courses/{id}/teachers`, `/courses/roots`, `/courses/search`, `/teacher/courses/search`, `/teacher-courses/`, `/operating-hours`;
- **общий CRUD** `GET /{resource}/` (`repos/base.py paginate`). Без явной сортировки теперь `ORDER BY PK`, раньше порядок был по плану. При частичной сортировке PK дописывается в конец. Между страницами больше нет дублей и пропусков: тест без правки давал дубль `[1, 3, 1]`.

Отдельно: `parent_course_ids` в ответах курсов (`relationship parent_courses`) теперь по `parent_course_id`.

## Тесты

Шесть файлов `tests/test_tsk996_order_g1..g6.py` (74 теста) и общий помощник `tests/plan_order.py`:

- `PLANS`: custom-sort / hashagg / generic / heap-order / index-order через `SET LOCAL`;
- `run_under_plans`, `assert_same`;
- `move_to_heap_end`.

Каждый тест строит ничью, перекладывает строку в куче или вставляет id не по порядку и проверяет, что под всеми планами результат один и тот же и равен ожидаемому по id. **Все 50 мест А закрыты тестом, который без правки красный.** У 49 тест свой, назначение заявки о помощи проверено тестом источника (`list_teachers`). Красные тесты есть и для важных списков Б: пагинация, ленты, очереди. Каждый файл проверен откатом, ревьюер повторил откат на 9 местах, все красные по проверке результата. Девять тестов в `g6` написаны после ревью, для мест, которые сначала остались без доказательства.

Тесты, которые фиксируют инвариант, но без правки не краснеют:
- `task_results.get_detail_by_user` (Б): окно `ROW_NUMBER() OVER (PARTITION BY task_id)` сортирует вход при любом плане;
- `payment_reminder` LATERAL: ничья невозможна из-за UNIQUE, переведено в В;
- `curator_coverage`: все планы уже давали `u.id`.

Часть Б-списков без пагинации тестом не покрыта, в таблице у них «—».

Попутная находка, записана в `plan_order.py`: `UPDATE ... SET id = id` — это HOT-обновление, индекс продолжает указывать на прежнюю запись. Если план читает по индексу, перекладка порядок не меняет. В таких тестах меняется ключ индекса или строки вставляются с заранее взятыми id в обратном порядке.

## Независимое ревью (отдельный агент): ПРИНЯТО

Ревьюер проверил SQL каждой правки: ключи DISTINCT ON, ORDER BY при SELECT DISTINCT, алиасы UNION, GROUP BY, порядок `order_by` перед `limit`. Проверил и `with_pk_tiebreak` на реальных выражениях: составной PK, `nullslast(desc())`, `text()`. На 9 местах откатил правки, все тесты стали красными. Ошибок SQL нет.

| Находка | Решение |
|---|---|
| **Блокирующая:** `course_position.anchor_for` с `id DESC` переворачивал привычный выбор якоря (обычный план давал меньший `task_id`) | возвращено `id` ASC, тест переписан: семь заданий, сдачи в обратном порядке. Без правки красный (heap-order даёт задание 3), с правкой зелёный |
| `requirement_override`: при полной ничьей теперь побеждает бóльший id, а обычный план давал меньший | оставлено: согласуется с правилом «позднее побеждает» (`created_at DESC`). Вынесено оператору в таблицу выше |
| 4 теста top-N в g4 зависели от отсутствия чужих данных (глобальный LIMIT) | ожидание берётся из полного списка (`_assert_top_same`), без правки по-прежнему красные |
| `with_plan`: упавший RESET прятал исходную ошибку | RESET выполняется только при успехе, при ошибке настройки уходят с откатом транзакции |
| Пропуски в тех же файлах: `lesson_plan_service:315`, `payment_service:496`, `pricing_service:577` | добивка id |
| `messenger_channel_service._CURATOR_SQL` LIMIT 1 | Х: частичный UNIQUE `uq_student_curator_current` |
| `teacher_courses_repo`: колонка-добивка может повториться в ORDER BY | безвредно, оставлено |

Кроме того, координатор вернул выбор правщика в `lesson_attendance_cron` (ДЗ получало позднее занятие) к порядку обычного плана (раннее).

## Проверки

Облако, PG 16 + Redis, `alembic upgrade head`, сиды `seed_roles.sql` и `seed_difficulties.py`. Полные прогоны идут списком файлов (`pytest $(ls tests/test_*.py | sort)`).

- **main (3ccbc67, worktree), полный прогон дважды:** 172 failed / 17 errors / 4549 passed, оба раза один и тот же набор (191 id).
- **Ветка, полный прогон дважды:** 172 failed / 17 errors, passed 4614 и 4623 (+65 и +74 новых: второй прогон уже с g6). **Набор падений в точности совпадает с main**, новых падений нет.
- Почти все базовые падения из-за среды: в dev-БД нет контента («нет ни одного задания»). Поэтому затем в `Learn` добавлен минимальный dev-контент (курс id=1, задание, две ссылки-материала, методист, преподаватель, ученик) и пересоздан шаблон. 33 упавших файла прогнаны на main и на ветке: **34 failed / 254 passed в обоих, набор совпадает**. Так тесты очередей, claim-next, заявок о помощи и pending-review (155 тестов), которые трогают изменённый SQL, проходят на ветке. Остальные 34 падения — те же, что в отчёте 22.10: VK/Fernet-заглушка из `.env.example`, сид настроек `tsk721`, `tsk975`, `tsk557`, `tsk478`, `io_tests_tsk953`, `pd_consents_tsk1335`.
- Новые тесты `tests/test_tsk996_order_g1..g6.py` и `test_tsk996_node_sequence.py` (вместе 97): зелёные, `g6` дважды подряд.

Без skip, xfail и retry.

Перекрёстная память (`D:\Work\ContentBackbone\docs\cross-project\`) из облака недоступна. Поля контракта API не меняются, меняется только порядок строк при ничьих (список эндпоинтов выше). Запись в CHANGELOG при влитии делает оператор. Стоит отметить изменение `requirement_override` и `GET /{resource}/` без сортировки (теперь по PK).

## Что проверить на проде

```sql
-- у ученика несколько преподавателей: кому уходили заявки о помощи (теперь меньший teacher_id)
SELECT student_id, count(*) FROM student_teacher_links GROUP BY 1 HAVING count(*) > 1;
-- курсы ученика с одинаковым order_number/added_at: порядок обхода движка зависел от плана
SELECT user_id, order_number, count(*) FROM user_courses GROUP BY 1, 2 HAVING count(*) > 1;
-- одновременные занятия у одного ученика: явка, опрос, ДЗ, «предыдущее занятие»
SELECT lop.student_id, lo.scheduled_at, count(*) FROM lesson_occurrence_participant lop
  JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id GROUP BY 1, 2 HAVING count(*) > 1;
-- заявки о помощи с полной ничьей в очереди claim-next
SELECT priority, due_at, created_at, count(*) FROM help_requests WHERE status = 'open'
  GROUP BY 1, 2, 3 HAVING count(*) > 1;
-- два открытых переопределения одного элемента с равным created_at (уровень требования)
SELECT student_id, target_kind, target_id, created_at, count(*) FROM student_requirement_override
  WHERE revoked_at IS NULL GROUP BY 1, 2, 3, 4 HAVING count(*) > 1;
```
Если такие строки есть, именно у этих учеников и преподавателей поведение раньше могло «прыгать». После выката ничьи разрешаются по id. Запросы проверены на схеме `alembic head`.

## Таблица всех кандидатов

Классы: **А** — порядок меняет поведение, **Б** — порядок виден пользователю, **В** — безобидно, **Х** — исключено при чтении. Номера строк для А и Б — после правки, для В и Х — по инвентарю на main. Сначала идут А и Б по файлам, затем В и Х. Последние 4 строки — находки ревью. Полный машинный инвентарь: `python scripts/order_by_inventory.py --all`.

| # | Файл:строка | Класс | Решение | Правка / причина | Тест (без правки) |
|---|---|---|---|---|---|
| 1 | `app/api/v1/attempts.py:226` | А | исправлено | .order_by(TaskResults.id). GET /attempts/{id}, POST /attempts/{id}/finish: results и results[0] (база attempts_used/limit/last_based_status) — по id результата (≈ порядок сдачи, как давал скан индекса attempt_id) | `test_attempt_first_task_same_under_any_plan` (красный) |
| 2 | `app/api/v1/learning.py:631` | А | исправлено (доп., не в TSV) | .order_by(Attempts.created_at.desc(), Attempts.id.desc()). POST /learning/tasks/{id}/start-or-get-attempt: при двух открытых попытках с равным created_at — больший id | `test_start_or_get_attempt_same_open_attempt_plan` (красный) |
| 3 | `app/api/v1/methodist_escalations.py:117` | А | исправлено | ORDER BY n.modified_at DESC, n.id DESC. GET /methodist/escalations/pending: порядок при ничьих: по id (убыв.) | `test_methodist_escalations_limit_plan` (красный) |
| 4 | `app/api/v1/schedule_preferences.py:176` | А | исправлено | ORDER BY n.modified_at ASC, n.id ASC. GET /students/{id}/schedule-preference-reminders/pending: порядок при ничьих: по id | `test_schedule_preference_reminders_limit_plan` (красный) |
| 5 | `app/api/v1/student_lesson_reminders.py:100` | А | исправлено | ORDER BY n.modified_at ASC, n.id ASC. GET /students/{id}/lesson-reminders/pending: порядок при ничьих: по id | `test_lesson_reminders_limit_plan` (красный) |
| 6 | `app/api/v1/task_results_extra.py:680` | Б | исправлено | .order_by(TaskResults.submitted_at.desc(), TaskResults.id.desc()). GET /task-results/by-pending-review: порядок при ничьих: по id (убыв.) | `test_pending_review_queue_pages_plan` (красный) |
| 7 | `app/api/v1/user_courses_extra.py:107` | Б | исправлено | order_number ASC NULLS LAST, added_at ASC, course_id ASC / added_at ASC, course_id ASC. GET /users/{id}/courses (ученик), switch-direction: порядок при ничьих: по course_id | `test_user_courses_api_lists_plan` (красный) |
| 8 | `app/api/v1/user_courses_extra.py:75` | Б | исправлено | linked_at DESC, courses.id DESC / linked_at ASC, courses.id ASC. GET /users/{id}/courses (преподаватель): порядок при ничьих: по id курса | `test_user_courses_api_lists_plan` (красный) |
| 9 | `app/models/courses.py:146` | Б | исправлено | relationship parent_courses order_by=course_parents.parent_course_id. parent_course_ids в ответах курса: по id родителя | — |
| 10 | `app/repos/base.py:385` | Б | исправлено | search_text: ORDER BY <order_by>, <PK> (без order_by — по PK asc); общий помощник with_pk_tiebreak. GET /courses/search: порядок при ничьих: по id | `test_course_search_pages_plan` (красный) |
| 11 | `app/repos/base.py:445` | Б | исправлено | paginate: order_by + PK в направлении последнего ключа (PK не дублируется); без order_by — ORDER BY PK ASC. CRUD GET /{users,courses,tasks,...}/, /meta/tasks: без сортировки — по id (как seq scan); task_results/attempts/messages (submitted_at/created_at/sent_at) — при ничьих по id в том же направлении | `test_base_paginate_pages_plan` (красный) |
| 12 | `app/repos/base.py:466` | Б | исправлено | перезагрузка Courses с parent_courses — тот же полный order_by (теперь всегда задан). порядок перезагруженной страницы = порядку первой выборки | `test_base_paginate_pages_plan` (красный) |
| 13 | `app/repos/courses_repo.py:183` | Б | исправлено | .order_by(Courses.id). GET /courses/roots: по id (привычный seq scan); title — решение оператора | — |
| 14 | `app/repos/courses_repo.py:230` | Б | исправлено | .order_by(Courses.title, Courses.id). GET /teacher/courses/search: порядок при ничьих: по id | `test_root_course_search_pages_plan` (красный) |
| 15 | `app/repos/courses_repo.py:267` | Б | решение оператора | не правлено (предлагалось ORDER BY cp.parent_course_id, cp.order_number NULLS LAST, cp.course_id). GET /courses/{id}/tree: сейчас children узлов в порядке плана (≈ порядок кучи course_parents); правка поставит детей по order_number, как /children — меняет видимый порядок | — |
| 16 | `app/repos/lesson_calendar_repository.py:107` | А | исправлено | .order_by(LessonSlot.weekday, LessonSlot.start_time, LessonSlot.id). GET /lesson-slots; _find_slot_at при ничьих: по id | `test_slots_same_time_order_does_not_depend_on_plan` (красный) |
| 17 | `app/repos/lesson_calendar_repository.py:180` | Б | исправлено | .order_by(LessonSlotTeacher.id). GET /lesson-slots*, teacher_ids: по id (≈ порядок добавления) | — |
| 18 | `app/repos/lesson_calendar_repository.py:217` | А | исправлено | .order_by(LessonOccurrenceTeacher.id). GET /lesson-occurrences/{id}/teachers; перенос (_leading_teacher_ids): по id | `test_occurrence_teachers_order_does_not_depend_on_plan` (красный) |
| 19 | `app/repos/lesson_calendar_repository.py:247` | Б | исправлено | .order_by(LessonSlotStudent.id). GET /lesson-slots/{id}/participants, student_ids: по id | — |
| 20 | `app/repos/lesson_calendar_repository.py:267` | Б | исправлено; переклассифицировано А→Б: влияет только на текст 409 _find_conflicting_slot | .order_by(LessonSlotStudent.id) (не slot_id: id совпадает с прежним порядком кучи). перевод ученика между слотами (409): первый конфликт по id участия | — |
| 21 | `app/repos/lesson_calendar_repository.py:31` | Б | исправлено | .order_by(OperatingHours.weekday, OperatingHours.id). GET /operating-hours; при ничьих (несколько окон дня): по id ≈ прежний порядок кучи. Предложенный start_time сменил бы видимый порядок — не взят (решение оператора, если нужно по времени) | — |
| 22 | `app/repos/lesson_calendar_repository.py:337` | А | исправлено | .order_by(LessonOccurrence.scheduled_at.asc(), LessonOccurrence.id.asc()). GET /teacher/lesson-occurrences; _find_occurrence_at: порядок при ничьих: по id | `test_teacher_same_time_occurrence_does_not_depend_on_plan` (красный) |
| 23 | `app/repos/lesson_calendar_repository.py:365` | Б | исправлено | .order_by(LessonOccurrence.scheduled_at.asc(), LessonOccurrence.id.asc()). GET /me/lesson-occurrences/bookable: порядок при ничьих: по id | — |
| 24 | `app/repos/lesson_calendar_repository.py:387` | Б | исправлено | .order_by(LessonOccurrenceTeacher.id) (occurrence_id лишний — группировка в dict). bookable teacher_names: по id связи | — |
| 25 | `app/repos/lesson_calendar_repository.py:456` | Б | исправлено | .order_by(LessonOccurrenceParticipant.id). summary / панель плана: участники по id (как соседний list_for_occurrences) | — |
| 26 | `app/repos/lesson_calendar_repository.py:494` | Б | исправлено | .order_by(LessonOccurrence.scheduled_at.asc(), LessonOccurrence.id.asc()). GET /me/lesson-occurrences: порядок при ничьих: по id | `test_student_occurrences_same_time_order_does_not_depend_on_plan` (красный) |
| 27 | `app/repos/lesson_calendar_repository.py:51` | Б | исправлено; переклассифицировано А→Б: влияет только на текст 409, исход (отказ) одинаков | .order_by(OperatingHours.id). POST/PATCH operating-hours (409): называется первое пересечение по id (≈ прежний seq scan) | — |
| 28 | `app/repos/lesson_calendar_repository.py:572` | А | исправлено; выбор порядка — оператор может пересмотреть | .order_by(LessonOccurrence.scheduled_at.asc(), LessonOccurrence.id.asc()). авто-явка при сдаче: при перекрытии окон (early_grace) явка идущему (раннему) занятию; раньше — тому, чьё участие раньше в куче (в тесте — следующее) | `test_current_scheduled_participation_does_not_depend_on_plan` (красный) |
| 29 | `app/repos/materials_repo.py:164` | Б | исправлено | order_position ASC NULLS LAST, materials.id ASC. POST /courses/{id}/materials/reorder: порядок при ничьих: по id | — |
| 30 | `app/repos/parent_student_links_repository.py:35` | Б | исправлено | .order_by(parent_student_links.student_id). GET /me/children: по id ученика (как скан PK) | — |
| 31 | `app/repos/parent_student_links_repository.py:55` | Б | исправлено | .order_by(parent_student_links.parent_id). GET /users/{id}/parents: по id родителя | — |
| 32 | `app/repos/student_teacher_links_repository.py:39` | А | исправлено; выбор порядка — оператор может пересмотреть | .order_by(student_teacher_links.teacher_id). resolve_assigned_teacher: заявка о помощи/уведомление — преподавателю с меньшим id; /me/teachers, /users/{id}/teachers — по id. Обоснование: PK (student_id, teacher_id) → nested loop/index scan даёт teacher_id; EXPLAIN н | `test_assigned_teacher_from_links_plan` (красный) |
| 33 | `app/repos/student_teacher_links_repository.py:59` | Б | исправлено | .order_by(student_teacher_links.student_id). GET /users/{id}/students: по id ученика | — |
| 34 | `app/repos/teacher_courses_repo.py:64` | Б | исправлено | <sort_by> ±, teacher_id ±, course_id ± (то же направление). GET /teacher-courses/: порядок при ничьих: по (teacher_id, course_id) | `test_teacher_course_links_pages_plan` (красный) |
| 35 | `app/repos/user_courses_repo.py:110` | Б | исправлено | .order_by(UserCourses.course_id). POST /users/{id}/courses/bulk: существующие после созданных — по course_id | — |
| 36 | `app/repos/user_courses_repo.py:156` | Б | исправлено | .order_by(UserCourses.course_id). PATCH /users/{id}/courses/reorder: по course_id (как скан PK); TSV предлагал order_number — меняет видимый порядок, решение оператора | — |
| 37 | `app/repos/user_courses_repo.py:182` | Б | исправлено | added_at ASC, user_id ASC. GET /courses/{id}/users: порядок при ничьих: по user_id | `test_course_users_pages_plan` (красный) |
| 38 | `app/repos/user_courses_repo.py:51` | А | исправлено | order_number ASC NULLS LAST, added_at ASC, course_id ASC; иначе added_at ASC, course_id ASC. resolve_next_item без корня: обход курсов при ничьих — по course_id | `test_user_courses_engine_order_plan` (красный) |
| 39 | `app/repos/user_courses_repo.py:93` | Б | исправлено | .order_by(UserCourses.course_id). POST /users/{id}/courses/bulk (все уже есть): по course_id (как скан PK). order_number-порядок — решение оператора | — |
| 40 | `app/repos/user_roles.py:27` | Б | исправлено | .order_by(Roles.id). GET /users/{id}/roles/: по id роли | — |
| 41 | `app/repos/users_repo.py:106` | Б | исправлено | nullslast(<sort>), users.id в направлении sort (через with_pk_tiebreak). GET /users/: порядок при ничьих: по id | `test_users_list_pages_plan` (красный) |
| 42 | `app/repos/users_repo.py:185` | Б | исправлено | full_name ASC, users.id ASC. GET /users/search: порядок при ничьих: по id | `test_users_search_pages_plan` (красный) |
| 43 | `app/services/absence_poll_service.py:141` | А | исправлено | ORDER BY ap.asked_at DESC, ap.id DESC LIMIT 1. ответ ученика в мессенджере: при равном asked_at — опрос с большим id (≈ прежнее: последний вставленный) | `test_absence_answer_binds_same_poll_under_any_plan` (красный) |
| 44 | `app/services/assignment_rules_service.py:277` | А | исправлено | ORDER BY id. авто-назначение после ответа: user_courses.order_number новых курсов = порядок id правил (было — порядок плана); assignment_event.rule_id при двух правилах на один курс — меньший id | `test_answer_rules_enroll_in_rule_order_under_any_plan` (красный) |
| 45 | `app/services/assignment_rules_service.py:384` | А | исправлено | ORDER BY id. авто-назначение после попытки (course_failed): порядок курсов в плане ученика по id правил | `test_attempt_rules_enroll_in_rule_order_under_any_plan[course_failed]` (красный) |
| 46 | `app/services/assignment_rules_service.py:553` | А | исправлено | ORDER BY id. авто-назначение после квиза (quiz_scale): порядок курсов в плане ученика по id правил | `test_attempt_rules_enroll_in_rule_order_under_any_plan[quiz_scale]` (красный) |
| 47 | `app/services/attachment_audit_service.py:101` | А | исправлено | ORDER BY id. то же для messages (тот же приём, отдельно не тестировалось) | `test_attachment_audit_message_owner_same_under_any_plan` (красный, g6) |
| 48 | `app/services/attachment_audit_service.py:113` | А | исправлено | ORDER BY id. то же для student_payment.receipt_file (отдельно не тестировалось) | `test_attachment_audit_receipt_owner_same_under_any_plan` (красный, g6) |
| 49 | `app/services/attachment_audit_service.py:89` | А | исправлено | ORDER BY id. уведомление missing_attachments и attachment_missing_seen.owner_id: владелец дубля имени — task_result с меньшим id (≈ прежний seq scan) | `test_attachment_audit_owner_same_under_any_plan` (красный) |
| 50 | `app/services/attendance_service.py:181` | А | исправлено | ORDER BY lo.scheduled_at, lo.id. сводка преподавателю (серия/погашение): порядок при ничьих: по id | `test_attendance_outcomes_order_does_not_depend_on_plan` (красный) |
| 51 | `app/services/break_service.py:63` | Б | исправлено | ORDER BY b.starts_on DESC, u.full_name, b.id. GET /api/v1/methodist/breaks: порядок при ничьих: по id | — |
| 52 | `app/services/charge_cron_service.py:178` | А | исправлено | ORDER BY u.full_name, u.id. inbox-уведомление «без начисления» (findings[:max_examples]); порядок при ничьих: по id. Дублей строк от JOIN sub нет: одна открытая подписка (uq_student_subscription_active) | `test_unbilled_namesakes_order_does_not_depend_on_plan` (красный) |
| 53 | `app/services/charge_service.py:1254` | Б | исправлено | ORDER BY u.full_name, pg.name, ch.student_id. GET /api/v1/marketer/charges; порядок при ничьих: по id ученика | — |
| 54 | `app/services/charge_service.py:1432` | А | исправлено | ORDER BY ch.period DESC, u.full_name, pg.name, ch.id. inbox-тревога о сдвиге прошлого (shifted[:max_examples]); порядок при ничьих: по группе (имя), затем id строки | `test_shifted_past_months_order_does_not_depend_on_plan` (красный) |
| 55 | `app/services/charge_service.py:1495` | Б | исправлено | ORDER BY u.full_name, pg.name, o.student_id. GET/PUT /api/v1/marketer/price-overrides; (student,group) UNIQUE → ключ полный; порядок при ничьих: по id ученика | — |
| 56 | `app/services/code_review_cron_service.py:96` | А | исправлено | ORDER BY tr.submitted_at ASC, tr.id ASC. фоновая оценка кода: очерёдность пачки тика при ничьих: по id; тестом не доказывалось | `test_code_review_tick_batch_same_under_any_plan` (красный, g6) |
| 57 | `app/services/course_dependencies_enrollment_service.py:123` | А | исправлено | ORDER BY c.id. доназначенные зависимости встают в план ученика по возрастанию id курса (= sorted(required)) | `test_dependencies_assigned_in_id_order_under_any_plan` (красный) |
| 58 | `app/services/course_position.py:86` | А | исправлено (по ревью: возвращён прежний выбор) | ORDER BY at DESC NULLS LAST, kind DESC, id — при пакетной сдаче якорь: задание, из них с меньшим task_id (так давал обычный план, GroupAggregate по task_id). Карточка курса ученика / сводка преподавателя: «сейчас» и «позади N» | `test_course_anchor_same_under_any_plan` (красный) |
| 59 | `app/services/curator_activity_service.py:310` | Б | исправлено | ORDER BY u.full_name, c.id. GET /api/v1/curator/weekly-report + inbox; порядок при ничьих: по id куратора | — |
| 60 | `app/services/curator_board_service.py:131` | Б | исправлено | ORDER BY u.full_name, u.id. GET /api/v1/curator/board; Python-sort стабилен → тёзки по id | — |
| 61 | `app/services/curator_service.py:233` | Б | исправлено | ORDER BY s.full_name, s.id. derive-preview/derive-apply; порядок при ничьих: по id | — |
| 62 | `app/services/curator_service.py:570` | Б | исправлено | ORDER BY o.full_name, o.id. weekly report students_without_curator (LIMIT 200); порядок при ничьих: по id | — |
| 63 | `app/services/curator_service.py:639` | Б | исправлено (выбор порядка — оператор может пересмотреть) | ORDER BY count(*) DESC, u.id. GET /api/v1/curator/coverage; u.id = порядок, который уже дают все планы (GroupAggregate по u.id / индекс roster по curator_id; hashagg на тесте тоже), поэтому видимый порядок не меняется; альтернатива u.full_name сменил | `test_curator_coverage_ties_do_not_depend_on_plan` (зелёный) |
| 64 | `app/services/escalation_service.py:152` | А | исправлено | ORDER BY tr.submitted_at ASC, tr.id ASC. cron escalation_cron_tick: при ничьей на границе LIMIT 100 — меньшие id первыми; тестом не доказывалось (только очерёдность тика) | `test_escalation_tick_batch_same_under_any_plan` (красный, g6) |
| 65 | `app/services/graduation_service.py:299` | Б | исправлено | ORDER BY ls.weekday, ls.start_time, ls.id. graduation-preview schedule.slots: порядок при ничьих: по id | — |
| 66 | `app/services/graduation_service.py:502` | Б | исправлено | SELECT DISTINCT ur.user_id ... ORDER BY ur.user_id. escalated_to: по user_id (= Sort+Unique) | — |
| 67 | `app/services/help_requests_service.py:1119` | Б | исправлено | ORDER BY hrr.created_at ASC, hrr.id ASC. GET /api/v1/teacher/help-requests/{id} replies: порядок при ничьих: по id | — |
| 68 | `app/services/help_requests_service.py:1768` | Б | исправлено | ORDER BY hrr.created_at ASC, hrr.id ASC. GET /api/v1/learning/tasks/{task_id}/help-request replies: порядок при ничьих: по id | — |
| 69 | `app/services/help_requests_service.py:213` | А | исправлено в источнике: student_teacher_links_repository.list_teachers (teacher_id) и teacher_courses_service.list_teachers (linked_at DESC, id DESC) | не правилось: источник порядка — app/repos/student_teacher_links_repository.py:19 list_teachers (без ORDER BY) и TeacherCoursesService.list_teachers (linked_at . назначение заявки помощи (assigned_teacher_id) и уведомление преподавателю: у ученика с 2+ преподавателями получатель зависит от плана | через источник: `test_tsk996_order_g1` (list_teachers, красный) |
| 70 | `app/services/help_requests_service.py:257` | А | исправлено | SELECT id FROM help_requests WHERE event_id = :event_id ORDER BY id LIMIT 1. help-request по заданию (learning): при дубле по event_id бампается и возвращается заявка с меньшим id | `test_help_request_event_dedupe_same_under_any_plan` (красный) |
| 71 | `app/services/help_requests_service.py:373` | А | исправлено | ORDER BY created_at DESC, id DESC LIMIT 1. помощь по материалу: при равном created_at — заявка с большим id; под advisory-lock, тестом не доказывалось (тот же приём, что 720) | `test_material_help_dedupe_same_under_any_plan` (красный, g6) |
| 72 | `app/services/help_requests_service.py:720` | А | исправлено | ORDER BY created_at DESC, id DESC LIMIT 1. POST /api/v1/teacher/help-requests/oral already_open: при ничьей — request_id с большим id; тестом не доказывалось | `test_oral_help_already_open_same_under_any_plan` (красный, g6) |
| 73 | `app/services/help_requests_service.py:859/861/864` | Б | исправлено | due_at: ORDER BY hr.due_at ASC NULLS LAST, hr.created_at ASC, hr.id ASC; created_at: ORDER BY hr.created_at ASC, hr.id ASC; priority: ORDER BY hr.priority ASC, . GET /api/v1/teacher/help-requests: порядок при ничьих: по id, без дублей/потерь между страницами (без правки heap-order давал дубль id на стыке) | `test_help_requests_list_pages_same_under_any_plan` (красный (ветка priority — первая в цикле; created_at/due_at без правки отдельно не прогонялись)) |
| 74 | `app/services/hint_drafts_service.py:148` | А | исправлено | ORDER BY r.created_at DESC, r.id DESC. POST /api/v1/tasks/{task_id}/hint-drafts/generate: на границе REPLIES_LIMIT=8 при ничьих берутся большие id; тестом не доказывалось | `test_hint_source_replies_same_under_any_plan` (красный, g6) |
| 75 | `app/services/hint_drafts_service.py:528` | Б | исправлено | ORDER BY r.created_at, r.id. GET /api/v1/tasks/hint-drafts/queue: порядок при ничьих: по id | — |
| 76 | `app/services/homework_service.py:1116 (+1099)` | Б | исправлено | внешний: ORDER BY o.kind, coalesce(o.task_id, o.material_id); DISTINCT ON: ... cancelled_at, old_homework_id. GET /api/v1/me/homework: хвост «решено из прошлых выдач» — позиции по виду и id элемента (совпадает с типичным Sort от DISTINCT ON); tier при равном cancelled_at — из более ранней выдачи | `test_orphaned_homework_items_same_under_any_plan` (красный) |
| 77 | `app/services/inbox_service.py:78` | Б | исправлено | ORDER BY modified_at DESC, id DESC. GET /api/v1/me/notifications: порядок при ничьих: по id (новые сверху), страницы без дублей | `test_inbox_pages_same_under_any_plan` (красный) |
| 78 | `app/services/individual_courses_service.py:159` | Б | исправлено | ... ORDER BY c.id. POST /api/v1/users/{user_id}/individual-courses (текст 409): порядок при ничьих: по id (= типичный index scan по id) | — |
| 79 | `app/services/individual_courses_service.py:261` | Б | исправлено | GROUP BY r.id, r.title ORDER BY r.id. POST /api/v1/users/{user_id}/individual-courses (текст 409): по id (= типичный Sort+GroupAgg) | — |
| 80 | `app/services/individual_courses_service.py:370` | Б | исправлено | ORDER BY uc.order_number, c.id. GET /api/v1/users/{user_id}/individual-courses: порядок при ничьих: по id | — |
| 81 | `app/services/individual_courses_service.py:385` | Б | исправлено | ORDER BY p.order_number, p.course_id. GET /api/v1/users/{user_id}/individual-courses (topic_ids): порядок при ничьих: по id (ASC уже NULLS LAST) | — |
| 82 | `app/services/lead_service.py:260` | Б | исправлено | ORDER BY u.full_name, u.id. GET /api/v1/marketer/students/search (LIMIT); порядок при ничьих: по id | `test_student_search_limit_does_not_depend_on_plan` (красный) |
| 83 | `app/services/lead_service.py:401` | Б | исправлено | ORDER BY l.created_at DESC, l.id DESC, r.external_id. GET /api/v1/integrations/leads/external; как у соседнего списка лидов (created_at DESC, id DESC) + ref | — |
| 84 | `app/services/lead_service.py:69` | Б | исправлено | ORDER BY sort_order, name, id. GET /api/v1/marketer/lead-sources; порядок при ничьих: по id | — |
| 85 | `app/services/learning_gap_signals_service.py:121` | А | исправлено | ORDER BY wrong_rate DESC, submissions DESC, tr.user_id, t.course_id. cron scan_and_create_signals: при равной доле — больше сдач, затем по ученику/теме (submissions DESC — как у соседнего find_topic_gaps) | `test_student_gaps_ties_same_under_any_plan` (красный) |
| 86 | `app/services/learning_gap_signals_service.py:231` | А | исправлено | ORDER BY COUNT(*) FILTER (WHERE r.flagged) DESC, r.user_id, roots.root_course_id. cron → ai_authorship: на границе LIMIT по id ученика/корня | `test_ai_authorship_ties_same_under_any_plan` (красный) |
| 87 | `app/services/learning_gap_signals_service.py:414` | А | исправлено | ORDER BY w.last_own_work, u.id. cron → dropout_risk: на границе LIMIT по id ученика | `test_dropout_risk_ties_same_under_any_plan` (красный) |
| 88 | `app/services/learning_gap_signals_service.py:542` | А | исправлено | ORDER BY a.last_active, u.id. cron → dropout_risk (без расписания): на границе LIMIT по id ученика | `test_dropout_by_activity_ties_same_under_any_plan` (красный) |
| 89 | `app/services/learning_gap_signals_service.py:946` | Б | исправлено | … END DESC, s.created_at DESC, s.id DESC. GET /api/v1/learning-gaps/students, /topics (+teacher_attention): порядок при ничьих: по id (новее выше) | `test_signal_feed_ties_same_under_any_plan` (красный) |
| 90 | `app/services/learning_gaps_service.py:248` | А | исправлено | ORDER BY wrong_rate DESC, submissions DESC, t.course_id. cron → сигналы тем: на границе LIMIT по id темы (без LIMIT вход и так шёл по course_id — COUNT(DISTINCT) даёт только GroupAggregate; краснеет только top-N) | `test_topic_gaps_ties_same_under_any_plan` (красный) |
| 91 | `app/services/lesson_attendance_cron_service.py:270` | А | исправлено (координатор: выбран порядок типичного плана) | ORDER BY lo.scheduled_at, lo.id, lop.student_id. cron → ДЗ: занятия обрабатываются хронологически, ДЗ привязывается к раннему из двух закончившихся (как давал типичный план — куча в порядке вставки); раньше зависело от плана | `test_homework_after_lessons_chronological_under_any_plan` (красный) |
| 92 | `app/services/lesson_plan_service.py:206` | Б | исправлено | ORDER BY lo.scheduled_at DESC, lo.id DESC. plan, шаг absences: порядок при ничьих: по id (убыв.) | — |
| 93 | `app/services/lesson_plan_service.py:283` | А | исправлено; выбор порядка — оператор может пересмотреть | ORDER BY ua.user_id, ua.earned_at DESC, ua.achievement_id DESC (id у user_achievements нет, PK user_id+achievement_id). plan, шаг wins: из одновременных вех — с большим achievement_id | `test_lesson_plan_achievement_same_moment_under_any_plan` (красный) |
| 94 | `app/services/link_audit_service.py:285` | А | исправлено | ORDER BY 1, 2 (kind, id: material раньше task, внутри по id). уведомление link_audit методистам: owners[:3]/first детерминированы; совпадает с типичным Append двух seq scan; тестом не доказывалось | `test_link_audit_owners_same_under_any_plan` (красный, g6) |
| 95 | `app/services/me_service.py:255` | Б | исправлено | order_by(IdentityLink.created_at.asc(), IdentityLink.id.asc()). GET /api/v1/me/identities: порядок при ничьих: по id | `test_identities_same_under_any_plan` (красный) |
| 96 | `app/services/me_service.py:414` | Б | исправлено | … ORDER BY la.last_active_at DESC NULLS LAST, uc.order_number ASC NULLS LAST, uc.course_id. GET /api/v1/me/courses: порядок при ничьих: по id курса | — |
| 97 | `app/services/me_service.py:491/500/510` | А | исправлено (выбор задание/материал при равном ts — оператор может пересмотреть) | last_task: ORDER BY tr.received_at DESC NULLS LAST, tr.id DESC; last_material: ORDER BY smp.completed_at DESC NULLS LAST, smp.material_id DESC; внешний: ORDER B. GET /api/v1/me/last-position: при пакетной проверке — последняя по id сдача; при равном ts задания и материала — задание (как давал обычный план: задание первым в UNION ALL, сортировка 2 строк устойчивая) | `test_last_position_same_under_any_plan` (красный) |
| 98 | `app/services/me_service.py:832` | Б | исправлено | ORDER BY tr.received_at DESC, tr.id DESC. GET /api/v1/me/history (пагинация): порядок при ничьих: по id (новее выше), страницы без дублей/потерь | `test_history_pages_same_under_any_plan` (красный) |
| 99 | `app/services/messages_service.py:459` | Б | исправлено | order_by(func.count().desc(), model.sender_id.asc()). GET /api/v1/messages/senders: порядок при ничьих: по sender_id | — |
| 100 | `app/services/messages_service.py:497` | Б | исправлено | order_by(func.count().desc(), model.sender_id.asc()). GET /api/v1/messages/unread/by-sender: порядок при ничьих: по sender_id | — |
| 101 | `app/services/messages_service.py:621` | А | исправлено | row_number() OVER (PARTITION BY peer ORDER BY m.sent_at DESC, m.id DESC). GET /api/v1/messages/inbox: last_message при равном sent_at — с большим id; тестом не доказывалось | `test_inbox_last_message_same_under_any_plan` (красный, g6) |
| 102 | `app/services/messages_service.py:662` | Б | исправлено | order_by(base.c.last_sent_at.desc(), base.c.last_message_id.desc()). GET /api/v1/messages/inbox: порядок диалогов при ничьих: по id последнего сообщения (DESC); тестом не доказывалось | — |
| 103 | `app/services/messenger_channel_service.py:583` | Б | исправлено | ORDER BY channel (уникально по (user_id, channel)). urgent-channels / urgent-message: по channel (= прежний порядок индекса) | — |
| 104 | `app/services/payment_reminder_service.py:250` | В (переклассифицировано: А→В) | исправлено по указанию координатора (добивка-страховка) | ORDER BY s.starts_on DESC, s.id DESC. ничьей нет: WHERE ends_on IS NULL + уникальный частичный индекс uq_student_subscription_active (≤1 открытой подписки на ученика); тест проверяет наличие индекса и что смена тарифа тем же днём даёт «учится» под всеми план | `test_reminder_last_subscription_choice_does_not_depend_on_plan` (зелёный) |
| 105 | `app/services/payment_reminder_service.py:266` | Б | исправлено (иначе, чем предлагалось) | ORDER BY u.full_name, ch.period, ch.student_id, pg.name. GET /marketer/payments/reminders, /alumni-debts, рассылка; добивка ДОПИСАНА в конец (предложение вставляло student_id перед period — это меняло бы уже определённый порядок тёзок по месяцу); порядок при ничьих: по id учен | `test_reminder_debtors_order_does_not_depend_on_plan` (красный) |
| 106 | `app/services/payment_service.py:1080` | Б | исправлено | ORDER BY on_date, u.full_name, p.id. GET /api/v1/marketer/payments/export; порядок при ничьих: по id платежа | — |
| 107 | `app/services/pricing_service.py:360` | Б | исправлено | ORDER BY (cp.sale_status = 'paid') DESC NULLS LAST, c.title, c.id. GET /api/v1/marketer/pricing/courses; порядок при ничьих: по id курса | — |
| 108 | `app/services/quiz_funnel_bot_service.py:151` | А | исправлено | .order_by(QuizFunnelBotLead.next_reminder_at, QuizFunnelBotLead.id). GET /integrations/quiz-funnel/bot/due; при равном сроке на границе LIMIT — младшие id (план по индексу ix_quiz_funnel_bot_lead_due отдавал по TID) | `test_bot_due_batch_does_not_depend_on_plan` (красный) |
| 109 | `app/services/requirement_override_service.py:363` | А | исправлено (выбор порядка — оператор может пересмотреть) | ORDER BY kind, item_id, (source = 'human') DESC, depth ASC, created_at DESC, override_id DESC (id строки протянут через ov/node/cand). GET me (requirement_view), teacher_progress: при равных источнике/глубине/created_at побеждает строка переопределения с большим id. Уточнение к инвентарю: у двух родителей может различаться и level, не только причина — о | `test_requirement_reason_tie_same_under_any_plan` (красный) |
| 110 | `app/services/retrieval_service.py:530` | А | исправлено | .order_by(Attempts.created_at.desc(), Attempts.id.desc()). сдача повтора (retrieval): ответ ложится в открытую попытку с большим id при ничьей | `test_course_attempt_picks_same_open_attempt_plan` (красный) |
| 111 | `app/services/schedule_booking_service.py:783` | Б | исправлено | ORDER BY (r.status = 'open') DESC, r.created_at DESC, r.id DESC LIMIT :lim. GET /api/v1/methodist/schedule-slot-requests: порядок при ничьих: по id (убыв.) | — |
| 112 | `app/services/stuck_tasks_service.py:103` | А | исправлено; выбор порядка — оператор может пересмотреть | ORDER BY w.last_at DESC, w.task_id DESC. сводка занятия/план урока: при равном last_at первым (названным) идёт задание с большим task_id. Предложенный в fix3 ключ w.user_id, w.task_id (ASC) заменён на task_id DESC по правилу направления; user_id не нужен — резу | `test_stuck_tasks_named_task_same_under_any_plan` (красный) |
| 113 | `app/services/subscription_service.py:352` | Б | исправлено | ORDER BY t.price_minor, p.sort_order, p.code, t.id. GET /api/v1/payments/plans; при равной цене: sort_order, code, id тарифа. Задвоение плана при >1 активного тарифа с match_kind IS NULL в группе — НЕ правлено, решение оператора (dict по code в payments_gateway берёт посл | — |
| 114 | `app/services/subscription_service.py:797` | Б | исправлено | ORDER BY ch.period, g.name. GET /subscriptions/students/{id} manual_pricing.monthly_amounts; порядок при ничьих: по имени группы (UNIQUE) | — |
| 115 | `app/services/task_results_service.py:366` | Б | исправлено | ORDER BY task_id (в SQL; Python-сортировка уроков стабильна). GET /api/v1/task-results/detail/by-user/{user_id}: порядок уроков при равном first_at — по id первого задания (как давал Sort). Без правки не краснел: ROW_NUMBER() OVER (PARTITION BY task_id) уже сортирует вход, планиров | `test_detail_lessons_ties_same_under_any_plan` (зелёный) |
| 116 | `app/services/teacher_activity_feed_service.py:133 (курсор before)` | Б | решение оператора | не правилось: курсор before — строгое tr.submitted_at < :before (и аналоги в 4 источниках), next_before = время последнего события. События с тем же временем, ч. GET /api/v1/teacher/activity-feed: при равных timestamp на стыке страниц события пропадают | — |
| 117 | `app/services/teacher_activity_feed_service.py:154` | Б | исправлено | ORDER BY tr.submitted_at DESC, tr.id DESC. GET /api/v1/teacher/activity-feed: порядок при ничьих: по id (DESC), набор на границе LIMIT стабилен | `test_activity_feed_top_same_under_any_plan` (красный) |
| 118 | `app/services/teacher_activity_feed_service.py:230` | Б | исправлено | ORDER BY hr.created_at DESC, hr.id DESC. то же; порядок при ничьих: по id | — |
| 119 | `app/services/teacher_activity_feed_service.py:312` | Б | исправлено | ORDER BY smp.completed_at DESC, smp.student_id DESC, smp.material_id DESC. то же; порядок при ничьих: по PK (student_id, material_id) DESC | — |
| 120 | `app/services/teacher_activity_feed_service.py:385` | Б | исправлено | ORDER BY e.detected_at DESC, e.id DESC. то же; порядок при ничьих: по id | — |
| 121 | `app/services/teacher_courses_service.py:121` | Б | исправлено (доп., тот же класс в list_courses) | все ветки list_courses (title/created_at/linked_at): + courses.id в том же направлении. GET /teachers/{id}/courses (list_courses): порядок при ничьих: по id курса | — |
| 122 | `app/services/teacher_courses_service.py:59` | А | исправлено; выбор порядка — оператор может пересмотреть | все ветки list_teachers: + users.id в том же направлении. resolve_assigned_teacher (fallback limit=1): при равном linked_at — больший users.id (при DESC); GET /courses/{id}/teachers — ничьи по id | `test_assigned_teacher_from_course_plan` (красный) |
| 123 | `app/services/teacher_lesson_summary_service.py:1360` | Б | исправлено | key=lambda kv: (-kv[1]["percent"], kv[0]). ready-for-harder-summary: при равном % — по student_id (= прежний порядок GROUP BY) | — |
| 124 | `app/services/teacher_lesson_summary_service.py:183` | А | исправлено | row_number() OVER (PARTITION BY tr.user_id, tr.task_id ORDER BY tr.received_at, tr.id); внешний ORDER BY s.user_id (стр. 243). summary / ready-for-harder: первая попытка при ничьей — меньший id | `test_ready_for_harder_first_try_under_any_plan` (красный) |
| 125 | `app/services/teacher_lesson_summary_service.py:267` | А | исправлено; выбор порядка — оператор может пересмотреть | ORDER BY lo.scheduled_at DESC, lo.id DESC. summary / attention-summary: из одновременных «предыдущим» считается больший id | `test_prev_occurrence_same_time_under_any_plan` (красный) |
| 126 | `app/services/teacher_lesson_summary_service.py:313` | Б | исправлено | внутри: ORDER BY tr.submitted_at DESC, tr.id DESC LIMIT 1. summary last_activity: при ничьих — больший id | — |
| 127 | `app/services/teacher_lesson_summary_service.py:332` | Б | исправлено | ORDER BY smp.completed_at DESC, smp.material_id DESC LIMIT 1 (id у smp нет, PK student_id+material_id). summary last_activity: при ничьих — больший material_id | — |
| 128 | `app/services/teacher_queue_service.py:1583` | Б | исправлено | ORDER BY tr.submitted_at ASC, tr.id ASC. GET /api/v1/teacher/reviews/pending: порядок при ничьих: по id (совпадает с claim-next), страницы без дублей | `test_pending_reviews_pages_same_under_any_plan` (красный) |
| 129 | `app/services/teacher_queue_service.py:300` | А | исправлено | ORDER BY hr.priority ASC, hr.due_at ASC NULLS LAST, hr.created_at ASC, hr.id ASC. POST /api/v1/teacher/help-requests/claim-next: при полной ничьей выдаётся заявка с меньшим id (раньше — по плану/куче: план-зависимо 1..5) | `test_claim_next_help_request_same_under_any_plan` (красный) |
| 130 | `app/services/teacher_queue_service.py:700` | А | исправлено | ORDER BY tr.submitted_at ASC, tr.id ASC. POST /api/v1/teacher/reviews/claim-next: при равном submitted_at — работа с меньшим id | `test_claim_next_review_same_under_any_plan` (красный) |
| 131 | `app/services/topic_mastery_service.py:253` | А | исправлено | в real_subs: tr.id AS result_id; LAG(...) OVER (PARTITION BY user_id, course_id ORDER BY received_at, result_id). GET /api/v1/topic-mastery/topics/{course_id} (tasks pace): при равном received_at реальный промежуток — сдаче с меньшим id, нулевой — с большим | `test_topic_pace_tie_same_under_any_plan` (красный) |
| 132 | `app/services/topic_mastery_service.py:505` | Б | исправлено | Python: (t.signal == SIGNAL_OK, -t.wrong_rate, -t.submissions, t.course_id). GET /api/v1/topic-mastery/overview: порядок при ничьих: по id темы | — |
| 133 | `app/services/topic_mastery_service.py:677` | Б | исправлено | ORDER BY p.wrong_rate DESC, p.submissions DESC, p.user_id. GET /api/v1/topic-mastery/topics/{course_id} (students): порядок при ничьих: по id ученика | `test_topic_students_ties_same_under_any_plan` (красный) |
| 134 | `app/services/users_dedup_service.py:110` | Б | исправлено | .order_by(Users.id) (sort по score стабилен — отдельная Python-добивка не нужна). GET /api/v1/users/duplicates: user_a — меньший id, равные по score пары по (a, b) — совпадает с типичным seq scan ≈ порядок вставки | `test_duplicate_pairs_same_under_any_plan` (красный) |
| 135 | `app/api/v1/admin_new_users.py:96` | Х | не правится | MAX(id) — агрегат-число. | — |
| 136 | `app/api/v1/admin_new_users.py:105` | В | не правится | Внешний ORDER BY u.id полный; подзапросы reg_event/plan_code по id. Атрибуция guest_session ORDER BY g.created_at LIMIT 1 без добивки, но сессии создаются разными запросами — равные created_at практически невозможны. | — |
| 137 | `app/api/v1/ai_tutor.py:497` | В | не правится | ORDER BY s.created_at DESC LIMIT; сессии создаются отдельными запросами (now() разный), равенство практически невозможно. | — |
| 138 | `app/api/v1/auth_signals.py:60` | В | не правится | ORDER BY ts DESC без добивки, но строки сворачиваются в dict по email и затем сортируются в Python; совпадение ts у двух заказов ссылки (разные запросы) практически невозможно — максимум другой last_ip. | — |
| 139 | `app/api/v1/courses_extra.py:529` | Х | не правится | Поиск по PK course_parents (course_id, parent_course_id) — проверка существования. | — |
| 140 | `app/api/v1/courses_extra.py:748` | Х | не правится | Сворачивается в dict по Users.id (PK); порядок задаёт user_courses (см. доп. строку про user_courses_repo.get_course_users). | — |
| 141 | `app/api/v1/embed_api.py:90` | Х | не правится | course_uid UNIQUE (courses_course_uid_key), scalar_one_or_none. | — |
| 142 | `app/api/v1/embed_api.py:180` | Х | не правится | course_uid UNIQUE; task по (course_id, external_uid) scalar_one_or_none. | — |
| 143 | `app/api/v1/grading_criteria.py:295` | Х | не правится | ORDER BY course_id, order_position, id добавляется при execute — полный. | — |
| 144 | `app/api/v1/learning.py:621` | В | не правится | created_at DESC LIMIT 1 среди активных попыток; создание сериализовано advisory-lock, разные транзакции → разные now(); дубль с равным created_at практически невозможен. | — |
| 145 | `app/api/v1/student_lesson_reminders.py:149` | Х | не правится | ORDER BY n.id DESC — полный. | — |
| 146 | `app/api/v1/tasks_extra.py:344` | Х | не правится | ORDER BY course_id, order_position, id — полный. | — |
| 147 | `app/api/v1/tasks_extra.py:521` | Х | не правится | Поиск по PK Tasks.id. | — |
| 148 | `app/api/v1/tasks_extra.py:539` | Х | не правится | .order_by(Tasks.id) — полный. | — |
| 149 | `app/api/v1/tasks_extra.py:1331` | Х | не правится | dict по course_uid (UNIQUE). | — |
| 150 | `app/api/v1/tasks_extra.py:1358` | Х | не правится | dict по DifficultyLevels.uid (difficulties_uid_key). | — |
| 151 | `app/api/v1/tasks_extra.py:1377` | В | не правится | dict по upper(code); UNIQUE только по code с учётом регистра — теоретически Easy/EASY дали бы «последний выигрывает» по плану; в справочнике таких дублей нет. | — |
| 152 | `app/api/v1/teacher_assignments.py:55` | Х | не правится | Сворачивается в set ролей. | — |
| 153 | `app/api/v1/teacher_learning.py:93` | Х | не правится | PK (student_id, task_id). | — |
| 154 | `app/api/v1/teacher_learning.py:198` | Х | не правится | PK (student_id, task_id). | — |
| 155 | `app/api/v1/user_courses_extra.py:48` | Х | не правится | any() по ролям — порядок не важен. | — |
| 156 | `app/core/settings_store.py:59` | Х | не правится | dict по key (PK pk_system_setting). | — |
| 157 | `app/repos/access_requests_repository.py:22` | В | не правится | ORDER BY requested_at без id, но метод не вызывается из API (только сервисная обёртка без вызывающих). | — |
| 158 | `app/repos/access_requests_repository.py:34` | В | не правится | ORDER BY requested_at без id; заявки создаются отдельными запросами — равенство практически невозможно. | — |
| 159 | `app/repos/base.py:51` | Х | не правится | scalar_one_or_none по ключам — дубль дал бы исключение, не выбор. | — |
| 160 | `app/repos/base.py:97` | В | не правится | OFFSET/LIMIT без ORDER BY — латентно, но BaseService.list нигде не вызывается. | — |
| 161 | `app/repos/base.py:112` | В | не правится | Единственный вызов — achievements get_by_name по UNIQUE name. | — |
| 162 | `app/repos/base.py:241` | В | не правится | filter_by_json_key нигде не вызывается. | — |
| 163 | `app/repos/base.py:455` | Х | не правится | Подзапрос для COUNT(*). | — |
| 164 | `app/repos/course_dependencies_repository.py:53` | Х | не правится | EXISTS-проверка (LIMIT 1, first() is not None). | — |
| 165 | `app/repos/course_dependencies_repository.py:127` | Х | не правится | dict по Courses.id; результат строится по порядку входного списка. | — |
| 166 | `app/repos/courses_repo.py:66` | Х | не правится | order_number NULLS LAST, Courses.id — полный. | — |
| 167 | `app/repos/courses_repo.py:103` | Х | не правится | order_number NULLS LAST, course_id — полный (parent фиксирован). | — |
| 168 | `app/repos/courses_repo.py:127` | Х | не правится | SELECT DISTINCT ... ORDER BY id — полный. | — |
| 169 | `app/repos/courses_repo.py:322` | В | не правится | Сворачивается в set parent_course_id (дальнейшие to_remove/to_add — порядок Python-set, не SQL). | — |
| 170 | `app/repos/courses_repo.py:443` | Х | не правится | Сворачивается в set id. | — |
| 171 | `app/repos/lesson_calendar_repository.py:35` | В | не правится | scalar_one_or_none по неуникальному weekday — дубль дал бы MultipleResultsFound, но метод нигде не вызывается. | — |
| 172 | `app/repos/lesson_calendar_repository.py:96` | Х | не правится | Подзапрос IN. | — |
| 173 | `app/repos/lesson_calendar_repository.py:127` | Х | не правится | Только булев ответ «есть пересечение». | — |
| 174 | `app/repos/lesson_calendar_repository.py:133` | Х | не правится | Подзапрос IN. | — |
| 175 | `app/repos/lesson_calendar_repository.py:162` | Х | не правится | UNIQUE (slot_id, teacher_id). | — |
| 176 | `app/repos/lesson_calendar_repository.py:194` | Х | не правится | UNIQUE (occurrence_id, teacher_id). | — |
| 177 | `app/repos/lesson_calendar_repository.py:228` | Х | не правится | UNIQUE (slot_id, student_id). | — |
| 178 | `app/repos/lesson_calendar_repository.py:249` | В | не правится | list_for_slots нигде не вызывается. | — |
| 179 | `app/repos/lesson_calendar_repository.py:284` | Х | не правится | EXISTS-подзапрос. | — |
| 180 | `app/repos/lesson_calendar_repository.py:292` | Х | не правится | Подзапрос IN. | — |
| 181 | `app/repos/lesson_calendar_repository.py:394` | Х | не правится | UNIQUE (slot_id, scheduled_at) uq_lesson_occurrence_slot_scheduled_at. | — |
| 182 | `app/repos/lesson_calendar_repository.py:407` | В | не правится | Цикл создаёт/удаляет участников/ведущих по каждому будущему занятию — итог не зависит от порядка (меняется лишь порядок выдачи id новым строкам). | — |
| 183 | `app/repos/lesson_calendar_repository.py:427` | Х | не правится | UNIQUE (occurrence_id, student_id). | — |
| 184 | `app/repos/lesson_calendar_repository.py:511` | Х | не правится | Только булев ответ. | — |
| 185 | `app/repos/materials_repo.py:47` | Х | не правится | Все ветки с добивкой Materials.id. | — |
| 186 | `app/repos/materials_repo.py:109` | Х | не правится | Поиск по PK. | — |
| 187 | `app/repos/materials_repo.py:124` | Х | не правится | course_id, order_position, id — полный. | — |
| 188 | `app/repos/materials_repo.py:170` | Х | не правится | MAX — агрегат. | — |
| 189 | `app/repos/materials_repo.py:266` | Х | не правится | base не исполняется (мёртвая переменная). | — |
| 190 | `app/repos/materials_repo.py:268` | Х | не правится | COUNT. | — |
| 191 | `app/repos/materials_repo.py:269` | Х | не правится | COUNT. | — |
| 192 | `app/repos/materials_repo.py:273` | Х | не правится | GROUP BY type → dict по type. | — |
| 193 | `app/repos/materials_repo.py:305` | Х | не правится | dict по (course_id, external_uid) — UNIQUE uq_materials_course_external_uid. | — |
| 194 | `app/repos/messages_repo.py:60` | Х | не правится | DISTINCT ON peer_id с ORDER BY peer_id, sent_at DESC, id DESC — полный. | — |
| 195 | `app/repos/parent_student_links_repository.py:68` | Х | не правится | EXISTS по PK. | — |
| 196 | `app/repos/tasks_repo.py:24` | Х | не правится | Сворачивается в set. | — |
| 197 | `app/repos/tasks_repo.py:43` | Х | не правится | order_position NULLS LAST, id — полный. | — |
| 198 | `app/repos/tasks_repo.py:90` | Х | не правится | order_position NULLS LAST, id — полный. | — |
| 199 | `app/repos/teacher_courses_repo.py:91` | Х | не правится | PK (teacher_id, course_id). | — |
| 200 | `app/repos/teacher_courses_repo.py:182` | Х | не правится | PK (teacher_id, course_id). | — |
| 201 | `app/repos/teacher_courses_repo.py:221` | Х | не правится | EXISTS по PK. | — |
| 202 | `app/repos/teacher_courses_repo.py:253` | В | не правится | Одноколоночная сортировка + LIMIT, но метод нигде не вызывается. | — |
| 203 | `app/repos/teacher_courses_repo.py:300` | В | не правится | Одноколоночная сортировка + LIMIT, но метод нигде не вызывается. | — |
| 204 | `app/repos/teacher_courses_repo.py:332` | В | не правится | OFFSET/LIMIT без ORDER BY, но метод нигде не вызывается. | — |
| 205 | `app/repos/teacher_courses_repo.py:348` | В | не правится | OFFSET/LIMIT без ORDER BY, но метод нигде не вызывается. | — |
| 206 | `app/repos/teacher_courses_repo.py:362` | Х | не правится | COUNT. | — |
| 207 | `app/repos/teacher_courses_repo.py:376` | Х | не правится | COUNT. | — |
| 208 | `app/repos/teacher_courses_repo.py:391` | Х | не правится | COUNT. | — |
| 209 | `app/repos/user_courses_repo.py:75` | Х | не правится | Свёрнуто в set course_id. | — |
| 210 | `app/services/absence_poll_service.py:58` | В | не правится | Каждая строка обрабатывается независимо (вопрос + INSERT по participant_id UNIQUE); порядок влияет только на очерёдность отправки в одном тике. | — |
| 211 | `app/services/absence_poll_service.py:161` | Х | не правится | Свёрнуто в set teacher_id. | — |
| 212 | `app/services/absence_poll_service.py:184` | В | не правится | Каждый опрос применяется независимо (INSERT ... ON CONFLICT DO NOTHING, UPDATE по id); participant_id UNIQUE — дублей пары нет. | — |
| 213 | `app/services/ai_tutor/session_service.py:141` | Х | не правится | ORDER BY submitted_at DESC, id DESC — полный. | — |
| 214 | `app/services/ai_tutor/session_service.py:224` | Х | не правится | Частичный UNIQUE uq_ai_tutor_session_open (student_id, task_id) WHERE status='open' — не более одной строки. | — |
| 215 | `app/services/ai_tutor/session_service.py:304` | Х | не правится | UPDATE по PK id. | — |
| 216 | `app/services/ai_tutor/session_service.py:358` | Х | не правится | UPDATE по PK id; jsonb_agg внутри упорядочен ORDER BY i. | — |
| 217 | `app/services/alumni_enrollment_guard.py:65` | Х | не правится | Частичный UNIQUE uq_student_subscription_active (student_id) WHERE ends_on IS NULL — одна строка. | — |
| 218 | `app/services/alumni_enrollment_guard.py:89` | Х | не правится | Свёрнуто в set. | — |
| 219 | `app/services/assignment_rules_service.py:76` | Х | не правится | courses.course_uid UNIQUE (courses_course_uid_key). | — |
| 220 | `app/services/assignment_rules_service.py:391` | Х | не правится | Агрегат COUNT. | — |
| 221 | `app/services/assignment_rules_service.py:457` | Х | не правится | DISTINCT ON (task_id) с ORDER BY task_id, submitted_at DESC, id DESC — полный. | — |
| 222 | `app/services/attachment_audit_service.py:129` | Х | не правится | Свёрнуто в set. | — |
| 223 | `app/services/attachment_audit_service.py:167` | Х | не правится | count(*). | — |
| 224 | `app/services/attachment_audit_service.py:180` | В | не правится | Список получателей; каждому кладётся одинаковое уведомление, порядок не наблюдаем. | — |
| 225 | `app/services/attempt_attachments.py:114` | Х | не правится | Свёрнуто в set имён. | — |
| 226 | `app/services/attempts_service.py:111` | В | не правится | Используется для проверки «просрочен ли любой дедлайн» (any) — порядок не важен. | — |
| 227 | `app/services/attempts_service.py:116` | В | не правится | То же — any по задачам курса. | — |
| 228 | `app/services/attendance_service.py:65` | Х | не правится | weeks = UNION (уникальная пара student_id, week); ORDER BY w.student_id, w.week полный. | — |
| 229 | `app/services/attendance_service.py:376` | Х | не правится | count(DISTINCT). | — |
| 230 | `app/services/auth/identity_link_service.py:34` | Х | не правится | UNIQUE uq_identity_link_kind_value; scalar_one_or_none. | — |
| 231 | `app/services/auth/identity_link_service.py:104` | Х | не правится | Свёрнуто в sorted(set). | — |
| 232 | `app/services/auth/identity_link_service.py:156` | Х | не правится | scalar_one_or_none: при дублях lower(email) (индекс users_email_unique_partial регистрозависим) падает MultipleResultsFound, а не выбирает по порядку — порядок не влияет. | — |
| 233 | `app/services/auth/magic_link_service.py:108` | Х | не правится | Проверка существования (.first() is not None). | — |
| 234 | `app/services/auth/magic_link_service.py:160` | Х | не правится | token_hash UNIQUE (uq_magic_link_token_hash). | — |
| 235 | `app/services/auth/magic_link_service.py:192` | Х | не правится | token_hash UNIQUE. | — |
| 236 | `app/services/auth/magic_link_service.py:239` | Х | не правится | scalar_one_or_none — дубли дают исключение, не выбор по порядку. | — |
| 237 | `app/services/auth/role_assign_service.py:154` | Х | не правится | Проверка членства (STUDENT_ROLE_ID in roles). | — |
| 238 | `app/services/auth/session_service.py:235` | Х | не правится | token_hash UNIQUE (uq_user_session_token_hash). | — |
| 239 | `app/services/auth/session_service.py:345` | Х | не правится | scalar_one_or_none по refresh_token_hash (SHA-256 случайного токена) + FOR UPDATE; дубль дал бы исключение. | — |
| 240 | `app/services/auth/session_service.py:439` | Х | не правится | UPDATE ... RETURNING, используется только len(). | — |
| 241 | `app/services/auth/vk_oauth_service.py:345` | Х | не правится | scalar_one_or_none — порядок не влияет. | — |
| 242 | `app/services/charge_cron_service.py:235` | Х | не правится | count(*). | — |
| 243 | `app/services/charge_cron_service.py:248` | В | не правится | Список получателей, всем одно уведомление. | — |
| 244 | `app/services/charge_cron_service.py:318` | В | не правится | Список получателей, всем одно уведомление. | — |
| 245 | `app/services/charge_service.py:239` | Х | не правится | Агрегат count(*) в одну строку. | — |
| 246 | `app/services/charge_service.py:450` | Х | не правится | UNIQUE uq_student_price_override (student_id, group_id). | — |
| 247 | `app/services/charge_service.py:560` | Х | не правится | UNIQUE uq_monthly_charge (student_id, group_id, period). | — |
| 248 | `app/services/charge_service.py:656` | Х | не правится | UNIQUE uq_monthly_charge. | — |
| 249 | `app/services/charge_service.py:710` | Х | не правится | UNIQUE uq_monthly_charge. | — |
| 250 | `app/services/charge_service.py:971` | В | не правится | Сворачивается в set и sorted(). | — |
| 251 | `app/services/charge_service.py:997` | В | не правится | Пересчёт по каждому ученику независим (свои вложенные транзакции), возвращается только len(). | — |
| 252 | `app/services/charge_service.py:1069` | В | не правится | Сворачивается в sorted(set). | — |
| 253 | `app/services/charge_service.py:1105` | В | не правится | refresh_frozen_total по каждой строке независимо. | — |
| 254 | `app/services/charge_service.py:1148` | В | не правится | Пары независимы; skipped в API отдаётся только числом (skipped_locked=len). | — |
| 255 | `app/services/content_grace_service.py:191` | В | не правится | Рёбра раскладываются в узлы; обход сортирует child_order по child_seq_key/_order_key с child_id — порядок SQL не наблюдаем. | — |
| 256 | `app/services/content_grace_service.py:208` | В | не правится | Список узлов используется как множество (ANY(:tree_ids), dict nodes). | — |
| 257 | `app/services/content_grace_service.py:386` | В | не правится | Корни объединяются в множества tasks/materials. | — |
| 258 | `app/services/content_grace_service.py:456` | В | не правится | Сворачивается во frozenset. | — |
| 259 | `app/services/course_activity_service.py:61` | В | не правится | Только лог отказа; но docstring обещает «в порядке возрастания» — контракт формально нарушен. | — |
| 260 | `app/services/course_activity_service.py:74` | Х | не правится | Свёрнуто в set. | — |
| 261 | `app/services/course_dependencies_enrollment_service.py:44` | В | не правится | DISTINCT сворачивается в sorted(set). | — |
| 262 | `app/services/course_dependency_state_cron_service.py:122` | В | не правится | Пары раскладываются в dict/set, обход sorted по student_id и course_id. | — |
| 263 | `app/services/courses_acl_service.py:39` | Х | не правится | EXISTS-подобная проверка (SELECT 1 ... LIMIT 1). | — |
| 264 | `app/services/courses_service.py:121` | Х | не правится | course_uid UNIQUE; scalar_one_or_none. | — |
| 265 | `app/services/courses_service.py:645` | Х | не правится | count(*). | — |
| 266 | `app/services/curator_activity_service.py:149` | Х | не правится | GROUP BY student_id → dict по уникальному ключу. | — |
| 267 | `app/services/curator_activity_service.py:337` | В | не правится | Список id передаётся параметром ANY(:curator_ids). | — |
| 268 | `app/services/curator_report_cron_service.py:72` | В | не правится | Список получателей, всем одно уведомление. | — |
| 269 | `app/services/curator_service.py:245` | В | не правится | Список id — параметр ANY(:excluded). | — |
| 270 | `app/services/curator_service.py:302` | Х | не правится | Частичный UNIQUE uq_student_curator_current (student_id) WHERE ended_at IS NULL. | — |
| 271 | `app/services/curator_service.py:336` | В | не правится | Проверка членства (student_id in roster). | — |
| 272 | `app/services/curator_service.py:641` | Х | не правится | count(*). | — |
| 273 | `app/services/dependency_lock_service.py:34` | В | не правится | Сворачивается в dict root→set(node). | — |
| 274 | `app/services/dependency_lock_service.py:244` | В | не правится | Список id далее sorted(ids). | — |
| 275 | `app/services/dependency_lock_service.py:285` | Х | не правится | dict по course_id (PK user_courses (user_id, course_id) при фиксированном user_id). | — |
| 276 | `app/services/dependency_lock_service.py:331` | Х | не правится | dict по users.id (PK). | — |
| 277 | `app/services/entitlements_service.py:91` | Х | не правится | _PLAN_SQL: student_id + ends_on IS NULL — частичный UNIQUE uq_student_subscription_active, максимум одна строка. | — |
| 278 | `app/services/entitlements_service.py:115` | Х | не правится | student_ai_quota: UniqueConstraint(student_id, period) — одна строка. | — |
| 279 | `app/services/entitlements_service.py:128` | Х | не правится | Агрегат sum() — одно число. | — |
| 280 | `app/services/feedback_reports_service.py:91` | Х | не правится | COUNT(*). | — |
| 281 | `app/services/feedback_reports_service.py:100` | Х | не правится | ORDER BY fr.created_at DESC, fr.id DESC — полный. | — |
| 282 | `app/services/final_check_service.py:217` | Х | не правится | ORDER BY cp.order_number NULLS LAST, cp.course_id; PK course_parents(course_id, parent_course_id) при фиксированном parent — полный. | — |
| 283 | `app/services/final_check_service.py:274` | Х | не правится | DISTINCT ON с ORDER BY task_id, submitted_at DESC, id DESC; внешний ORDER BY l.task_id (уникален) — полный. | — |
| 284 | `app/services/final_check_service.py:334` | Х | не правится | uq_final_check_runs_student_course, scalar_one_or_none. | — |
| 285 | `app/services/final_check_service.py:486` | Х | не правится | Сворачивается в dict по tasks.id (PK). | — |
| 286 | `app/services/final_check_service.py:732` | Х | не правится | Сворачивается в dict by_id по PK; порядок задаёт run.task_ids. | — |
| 287 | `app/services/free_enrollment_service.py:62` | В | не правится | Результат используется только как признак «есть платные» + список в лог. | — |
| 288 | `app/services/free_enrollment_service.py:71` | Х | не правится | user_courses PK (user_id, course_id). | — |
| 289 | `app/services/graduation_service.py:309` | Х | не правится | Агрегат count(*) FILTER — одна строка. | — |
| 290 | `app/services/graduation_service.py:701` | Х | не правится | Частичный UNIQUE uq_student_subscription_active. | — |
| 291 | `app/services/guest_diagnostic_service.py:179` | Х | не правится | courses.course_uid UNIQUE. | — |
| 292 | `app/services/guest_diagnostic_service.py:192` | Х | не правится | ORDER BY order_position NULLS LAST, Tasks.id — полный. | — |
| 293 | `app/services/guest_diagnostic_service.py:234` | Х | не правится | DISTINCT ON task_id с ORDER BY task_id, id DESC; сворачивается в dict. | — |
| 294 | `app/services/guest_diagnostic_service.py:460` | Х | не правится | courses.course_uid UNIQUE. | — |
| 295 | `app/services/guest_quiz_service.py:72` | Х | не правится | courses.course_uid UNIQUE. | — |
| 296 | `app/services/guest_quiz_service.py:88` | Х | не правится | ORDER BY order_position NULLS LAST, Tasks.id — полный. | — |
| 297 | `app/services/guest_quiz_service.py:120` | Х | не правится | DISTINCT ON task_id, ORDER BY task_id, id DESC; dict. | — |
| 298 | `app/services/guest_quiz_service.py:297` | Х | не правится | ORDER BY id — полный. | — |
| 299 | `app/services/guest_quiz_service.py:312` | Х | не правится | courses.course_uid UNIQUE. | — |
| 300 | `app/services/help_requests_service.py:473` | Х | не правится | Частичный UNIQUE uniq_help_requests_open_blocked_limit_student_task — не больше одной строки. | — |
| 301 | `app/services/help_requests_service.py:567` | Х | не правится | Агрегаты COUNT/MIN. | — |
| 302 | `app/services/help_requests_service.py:829` | Х | не правится | request_id + message_id: каждый ответ создаёт своё сообщение — строка одна по построению. | — |
| 303 | `app/services/help_requests_service.py:928` | Х | не правится | COUNT(*). | — |
| 304 | `app/services/help_requests_service.py:1410` | Х | не правится | Частичный UNIQUE на открытую blocked_limit (student, task). | — |
| 305 | `app/services/help_requests_service.py:1503` | В | не правится | ACTIVE_TEACHERS_SQL: в KPI идёт в UNION и потом items.sort(...) c teacher_id; в дашборде сворачивается в dict names. | — |
| 306 | `app/services/help_requests_service.py:1574` | В | не правится | Результат сортируется в Python полным ключом (..., teacher_id). | — |
| 307 | `app/services/help_requests_service.py:1677` | Х | не правится | Сумма двух COUNT(*). | — |
| 308 | `app/services/help_requests_service.py:1717` | Х | не правится | ORDER BY hr.created_at DESC, hr.id DESC LIMIT 1 — полный. | — |
| 309 | `app/services/help_requests_service.py:1876` | Х | не правится | COUNT(*). | — |
| 310 | `app/services/help_requests_service.py:2400` | Х | не правится | UNIQUE uq_help_request_replies_req_idem (request_id, idempotency_key). | — |
| 311 | `app/services/hint_drafts_service.py:114` | В | не правится | Результат сортируется в Python (-len, task_id); array_agg упорядочен по reply_id. | — |
| 312 | `app/services/hint_drafts_service.py:383` | Х | не правится | ORDER BY TaskHintDrafts.id LIMIT 1 — полный. | — |
| 313 | `app/services/hint_drafts_service.py:498` | Х | не правится | count(*). | — |
| 314 | `app/services/hint_drafts_service.py:504` | Х | не правится | ORDER BY created_at, id — полный. | — |
| 315 | `app/services/homework_service.py:131` | Х | не правится | Финальный ключ r.root уникален (order_number у корня один) — полный. | — |
| 316 | `app/services/homework_service.py:784` | В | не правится | Возвращаются только scheduled_at/duration; цикл берёт max(...) и первый scheduled_at > moment — результат от перестановки равных не зависит. | — |
| 317 | `app/services/homework_service.py:1019` | Х | не правится | ORDER BY hi.position: позиции пишутся enumerate при выдаче — уникальны внутри homework_id по построению (констрейнта нет; при желании добавить hi.id). | — |
| 318 | `app/services/homework_service.py:1284` | Х | не правится | ORDER BY issued_at DESC, id DESC LIMIT 1 — полный. | — |
| 319 | `app/services/homework_service.py:1326` | Х | не правится | ORDER BY issued_at DESC, id DESC — полный. | — |
| 320 | `app/services/homework_service.py:1362` | Х | не правится | DISTINCT ON student_id с id DESC — полный; результат в dict по student_id. | — |
| 321 | `app/services/homework_service.py:1407` | Х | не правится | Счётчик по student_id → dict. | — |
| 322 | `app/services/homework_service.py:1526` | Х | не правится | Агрегаты по student_id → dict. | — |
| 323 | `app/services/homework_volume_service.py:543` | Х | не правится | Одна строка из четырёх count(). | — |
| 324 | `app/services/homework_volume_service.py:611` | В | не правится | Сворачивается в set. | — |
| 325 | `app/services/homework_volume_service.py:696` | Х | не правится | Одна строка агрегатов. | — |
| 326 | `app/services/homework_volume_service.py:754` | Х | не правится | ORDER BY week — окна уникальны. | — |
| 327 | `app/services/homework_volume_service.py:810` | В | не правится | Группы весов суммируются _seconds_for_rows — порядок не наблюдаем. | — |
| 328 | `app/services/homework_volume_service.py:873` | В | не правится | Строки раскладываются в dict по week и суммируются; порядок недель задаёт weeks_order. | — |
| 329 | `app/services/homework_volume_service.py:1018` | Х | не правится | Скаляр least(min(...)). | — |
| 330 | `app/services/homework_volume_service.py:1047` | Х | не правится | count(DISTINCT). | — |
| 331 | `app/services/homework_volume_service.py:1068` | В | не правится | _OWN_LESSON_WEIGHTS_SQL: группы суммируются в _lesson_norm_minutes. | — |
| 332 | `app/services/homework_volume_service.py:1101` | Х | не правится | Агрегаты count. | — |
| 333 | `app/services/inbox_service.py:107` | Х | не правится | count(*). | — |
| 334 | `app/services/individual_courses_service.py:59` | В | не правится | order_number нумерует триггер (уникален по построению); список корней дальше используется как множество (ANY, фильтры, вставка зависимостей ON CONFLICT), locked_root_ids сортируются. | — |
| 335 | `app/services/individual_courses_service.py:74` | Х | не правится | ORDER BY c.id при GROUP BY c.id — полный. | — |
| 336 | `app/services/individual_courses_service.py:145` | Х | не правится | count(*). | — |
| 337 | `app/services/individual_courses_service.py:209` | Х | не правится | PK user_courses. | — |
| 338 | `app/services/individual_courses_service.py:312` | В | не правится | Сворачивается в set ever. | — |
| 339 | `app/services/individual_courses_service.py:403` | Х | не правится | dict по courses.id. | — |
| 340 | `app/services/lead_magnet_service.py:49` | Х | не правится | uq_lead_source_code. | — |
| 341 | `app/services/lead_magnet_service.py:113` | Х | не правится | ORDER BY m.course_uid — UNIQUE. | — |
| 342 | `app/services/lead_service.py:45` | Х | не правится | _LEAD_SELECT: используется с ORDER BY l.created_at DESC, l.id DESC и с WHERE l.id = :id. | — |
| 343 | `app/services/lead_service.py:299` | Х | не правится | uq_lead_external_ref (source, external_id). | — |
| 344 | `app/services/lead_service.py:346` | Х | не правится | uq_lead_external_ref. | — |
| 345 | `app/services/learning_engine_service.py:184` | Х | не правится | EXISTS. | — |
| 346 | `app/services/learning_engine_service.py:196` | В | не правится | Список корней: вызывающие обходят его целиком (ACL «есть ли», пересчёт по всем корням, проверка BLOCKED_LIMIT во всех) — порядок влияет лишь на лог. Порядок учебных элементов не трогается. | — |
| 347 | `app/services/learning_engine_service.py:216` | В | не правится | Список учеников для backfill — обрабатываются все, порядок не наблюдаем. | — |
| 348 | `app/services/learning_engine_service.py:273` | Х | не правится | PK student_task_limit_override (student_id, task_id). | — |
| 349 | `app/services/learning_engine_service.py:476` | Х | не правится | ORDER BY submitted_at DESC, id DESC LIMIT 1 — полный. | — |
| 350 | `app/services/learning_engine_service.py:575` | Х | не правится | COUNT(*). | — |
| 351 | `app/services/learning_engine_service.py:596` | Х | не правится | ORDER BY submitted_at DESC, id DESC LIMIT 1 — полный. | — |
| 352 | `app/services/learning_engine_service.py:755` | Х | не правится | Сворачивается в dict по tasks.id (override PK student+task). | — |
| 353 | `app/services/learning_engine_service.py:789` | Х | не правится | COUNT по task_id → dict. | — |
| 354 | `app/services/learning_engine_service.py:818` | Х | не правится | DISTINCT ON task_id, ORDER BY task_id, submitted_at DESC, id DESC — полный; dict. | — |
| 355 | `app/services/learning_engine_service.py:960` | В | не правится | Сворачивается в set excluded_tasks. | — |
| 356 | `app/services/learning_engine_service.py:982` | В | не правится | Сворачивается в set excluded_materials. | — |
| 357 | `app/services/learning_engine_service.py:995` | Х | не правится | DISTINCT ON с id DESC; итог COUNT(*). | — |
| 358 | `app/services/learning_engine_service.py:1041` | Х | не правится | COUNT(*). | — |
| 359 | `app/services/learning_engine_service.py:1106` | В | не правится | pending_ids идут в escalate_course_completion как множество (ANY, idempotency); порядок виден только в payload уведомления. | — |
| 360 | `app/services/learning_engine_service.py:1724` | В | не правится | Сворачивается в set completed_ids; порядок задают entries. | — |
| 361 | `app/services/learning_engine_service.py:1734` | В | не правится | Сворачивается в set skipped_ids. | — |
| 362 | `app/services/learning_engine_service.py:1936` | Х | не правится | ORDER BY order_position ASC NULLS LAST, Materials.id ASC — ключ полный (порядок не менять). | — |
| 363 | `app/services/learning_engine_service.py:1975` | В | не правится | Без ORDER BY в SQL, но rows.sort по task_key, последний элемент ключа — task_id: ключ полный (порядок не менять). | — |
| 364 | `app/services/learning_engine_service.py:2091` | В | не правится | Сворачивается в dict по courses.id. | — |
| 365 | `app/services/learning_engine_service.py:2150` | В | не правится | dict code_by_id; порядок задаёт _ordered_task_rows. | — |
| 366 | `app/services/learning_engine_service.py:2174` | В | не правится | Сворачивается в set solved. | — |
| 367 | `app/services/learning_engine_service.py:2318` | В | не правится | set completed_ids; порядок задают material_ids из _effective_material_rows. | — |
| 368 | `app/services/learning_engine_service.py:2367` | В | не правится | set skipped_ids; порядок задают task_ids из _effective_task_rows. | — |
| 369 | `app/services/learning_events_service.py:50` | В | не правится | Дедуп help_requested под pg_advisory_xact_lock(student,task): второй строки с тем же ключом в окне не возникает, а created_at=now() разных транзакций не совпадает; возвращаемый id однозначен. | — |
| 370 | `app/services/learning_events_service.py:149` | В | не правится | Дедуп hint_open под advisory lock (attempt,task) — дублей в окне нет, равных created_at быть не может. | — |
| 371 | `app/services/learning_events_service.py:221` | Х | не правится | Агрегат COUNT(*) — одна строка. | — |
| 372 | `app/services/learning_events_service.py:407` | Х | не правится | Выборка по уникальному ключу (student_id, material_id) — ON CONFLICT (student_id, material_id). | — |
| 373 | `app/services/learning_events_service.py:434` | Х | не правится | Уникальный ключ (student_id, material_id). | — |
| 374 | `app/services/learning_events_service.py:460` | Х | не правится | Уникальный ключ (student_id, material_id). | — |
| 375 | `app/services/learning_events_service.py:496` | Х | не правится | PK student_task_progress (student_id, task_id). | — |
| 376 | `app/services/learning_gaps_service.py:300` | В | не правится | source_breakdown нигде не вызывается (диагностика); GROUP BY по source_system, порядок ни на что не влияет. | — |
| 377 | `app/services/learning_guest_service.py:112` | Х | не правится | Результат сворачивается в set task_id, используется только len/membership. | — |
| 378 | `app/services/learning_guest_service.py:146` | Х | не правится | len(set(...)) — счётчик. | — |
| 379 | `app/services/learning_guest_service.py:167` | Х | не правится | course_uid UNIQUE (models/courses.py unique=True), scalar_one_or_none. | — |
| 380 | `app/services/learning_time_service.py:61` | В | не правится | ORDER BY ended_at DESC LIMIT 1 FOR UPDATE: ended_at=now() пишется каждой транзакцией отдельно, равенство у двух сеансов практически невозможно. | — |
| 381 | `app/services/learning_time_service.py:152` | В | не правится | То же для сеанса плеера: равных ended_at нет на практике. | — |
| 382 | `app/services/lesson_attendance_cron_service.py:69` | В | не правится | Каждая строка обрабатывается независимо (напоминание каждому участнику), порядок влияет лишь на порядок id уведомлений. | — |
| 383 | `app/services/lesson_attendance_cron_service.py:152` | В | не правится | Каждый участник помечается no_show и уведомляется независимо; порядок не наблюдаем. | — |
| 384 | `app/services/lesson_attendance_cron_service.py:205` | Х | не правится | Сворачивается в set teacher_id (UNIQUE occurrence_id+teacher_id). | — |
| 385 | `app/services/lesson_calendar_service.py:449` | Х | не правится | ORDER BY ls.weekday, ls.start_time, ls.id — полный; array_agg(DISTINCT) упорядочен. | — |
| 386 | `app/services/lesson_idle_cron_service.py:205` | В | не правится | пропуск скрипта: LATERAL task_opened ORDER BY le.created_at DESC LIMIT 1 — события пишутся по одному на запрос, равных created_at нет; влияет на текст «где» в уведомлении о простое. | — |
| 387 | `app/services/lesson_idle_cron_service.py:346` | Х | не правится | dict по (occurrence_id, student_id) — частичный UNIQUE uq_lesson_idle_episode_open. | — |
| 388 | `app/services/lesson_idle_cron_service.py:372` | Х | не правится | set teacher_id, далее sorted(). | — |
| 389 | `app/services/lesson_occurrence_generator_service.py:138` | В | не правится | Слоты обрабатываются независимо (ON CONFLICT по slot_id, scheduled_at); порядок влияет только на порядок id новых занятий. | — |
| 390 | `app/services/lesson_occurrence_generator_service.py:144` | В | не правится | Вставка участников ON CONFLICT DO NOTHING; порядок не наблюдаем. | — |
| 391 | `app/services/lesson_occurrence_generator_service.py:152` | В | не правится | Вставка преподавателей ON CONFLICT DO NOTHING; порядок не наблюдаем. | — |
| 392 | `app/services/lesson_occurrence_service.py:176` | Х | не правится | dict по users.id (PK). | — |
| 393 | `app/services/lesson_plan_service.py:133` | Х | не правится | dict по users.id (PK). | — |
| 394 | `app/services/lesson_plan_service.py:306` | В | не правится | ORDER BY h.created_at DESC: заявки создаются по одной на транзакцию, равных created_at у одного ученика нет; берётся help_items[0]. | — |
| 395 | `app/services/lesson_plan_service.py:346` | Х | не правится | dict по student_id при фиксированном occurrence — частичный UNIQUE (occurrence_id, student_id) WHERE resolved_at IS NULL. | — |
| 396 | `app/services/lesson_plan_service.py:485` | В | не правится | live_ids идут только в ANY(:ids); порядок не наблюдаем. | — |
| 397 | `app/services/lesson_plan_service.py:514` | Х | не правится | Есть ORDER BY p.student_id — полный для DISTINCT student_id. | — |
| 398 | `app/services/lesson_plan_service.py:947` | Х | не правится | set occurrence_id, далее sorted(). | — |
| 399 | `app/services/link_audit_service.py:155` | Х | не правится | count(*). | — |
| 400 | `app/services/link_audit_service.py:184` | В | не правится | Каждому методисту одно и то же уведомление; порядок не наблюдаем. | — |
| 401 | `app/services/manual_progress_service.py:231` | Х | не правится | ORDER BY uc.order_number ASC NULLS LAST, c.id — полный. | — |
| 402 | `app/services/manual_progress_service.py:796` | Х | не правится | Уникальный ключ (student_id, material_id). | — |
| 403 | `app/services/manual_progress_service.py:1184` | Х | не правится | DISTINCT ON (task_id) ORDER BY task_id, submitted_at DESC, tr.id DESC — полный. | — |
| 404 | `app/services/manual_progress_service.py:1215` | Х | не правится | DISTINCT ON (task_id) ORDER BY task_id, created_at DESC, hr.id DESC — полный. | — |
| 405 | `app/services/manual_progress_service.py:1236` | Х | не правится | Сворачивается в set task_id. | — |
| 406 | `app/services/manual_progress_service.py:1252` | Х | не правится | dict по material_id при фиксированном student — уникальный ключ. | — |
| 407 | `app/services/manual_progress_service.py:1274` | Х | не правится | dict по courses.id (PK). | — |
| 408 | `app/services/manual_progress_service.py:1284` | Х | не правится | ORDER BY order_number NULLS LAST, parent_course_id; PK (course_id, parent_course_id) → для setdefault по course_id порядок полный. | — |
| 409 | `app/services/manual_progress_service.py:1304` | Х | не правится | Сворачивается в set task_id. | — |
| 410 | `app/services/marketer_dashboard_service.py:214` | Х | не правится | GROUP BY month → dict по month. | — |
| 411 | `app/services/marketer_dashboard_service.py:258` | Х | не правится | GROUP BY month → dict по month. | — |
| 412 | `app/services/marketer_dashboard_service.py:301` | Х | не правится | count(*). | — |
| 413 | `app/services/material_checkpoints_service.py:151` | Х | не правится | count(*). | — |
| 414 | `app/services/materials_acl_service.py:51` | Х | не правится | EXISTS-проверка (SELECT 1 … LIMIT 1). | — |
| 415 | `app/services/materials_acl_service.py:173` | В | не правится | Проверка any(): доступ дан, если хоть один course_id в дереве; от порядка зависит лишь число запросов и текст лога. | — |
| 416 | `app/services/me_service.py:514` | Х | не правится | ORDER BY (c.id = leaf) DESC, c.id — полный (tsk-264). | — |
| 417 | `app/services/me_service.py:725` | Х | не правится | Агрегат (серия) — одна строка; ROW_NUMBER по DISTINCT d — полный. | — |
| 418 | `app/services/me_service.py:845` | Х | не правится | DISTINCT ON (task_id) … submitted_at DESC, tr.id DESC; внешний ORDER BY t.course_id, order_position, t.id — полный. | — |
| 419 | `app/services/me_service.py:1004` | Х | не правится | Уже исправлено в 4af0a4f: ORDER BY cd.course_id, cd.required_course_id. | — |
| 420 | `app/services/messages_service.py:632` | Х | не правится | Подзапрос GROUP BY sender_id, джойнится по ключу; порядок не важен. | — |
| 421 | `app/services/messenger_channel_service.py:165` | Х | не правится | Используется только membership (c in rows); UNIQUE (user_id, channel). | — |
| 422 | `app/services/messenger_channel_service.py:189` | Х | не правится | UNIQUE (channel, external_id). | — |
| 423 | `app/services/messenger_channel_service.py:312` | В | не правится | Отправка в каждый канал (UNIQUE user_id+channel) независимо; возвращается только счётчик. | — |
| 424 | `app/services/messenger_channel_service.py:512` | Х | не правится | UNIQUE (channel, external_id). | — |
| 425 | `app/services/messenger_channel_service.py:519` | Х | не правится | ORDER BY m.sent_at DESC, m.id DESC — полный. | — |
| 426 | `app/services/messenger_channel_service.py:530` | Х | не правится | Частичный UNIQUE uq_student_curator_current (student_id) WHERE ended_at IS NULL — строка одна. | — |
| 427 | `app/services/messenger_channel_service.py:669` | Х | не правится | ORDER BY id LIMIT 1 — полный. | — |
| 428 | `app/services/methodist_notify_service.py:39` | В | не правится | Всем методистам одинаковое уведомление; порядок не наблюдаем. | — |
| 429 | `app/services/methodist_notify_service.py:58` | Х | не правится | COUNT(*). | — |
| 430 | `app/services/parent_access_link_service.py:76` | В | не правится | ORDER BY created_at DESC: ссылки создаются по одной на транзакцию (create_link коммитит), равных created_at нет. | — |
| 431 | `app/services/parent_access_link_service.py:111` | Х | не правится | token_hash UNIQUE. | — |
| 432 | `app/services/parent_student_links_service.py:56` | Х | не правится | roles.name UNIQUE. | — |
| 433 | `app/services/payment_access_service.py:133` | В | не правится | Суммирование долга и sorted(periods) — порядок строк не влияет. | — |
| 434 | `app/services/payment_block_hold_service.py:65` | В | не правится | Фрагмент _SELECT: в active_hold ORDER BY until DESC LIMIT 1 — используется только факт наличия и until (одинаковый при равенстве); в list_holds ORDER BY created_at DESC — отсрочки создаются по одной на транзакцию. | — |
| 435 | `app/services/payment_service.py:233` | В | не правится | Суммы и sorted(periods) — порядок строк не влияет. | — |
| 436 | `app/services/payment_service.py:322` | Х | не правится | GROUP BY (student_id, group_id, period) → dict по тому же ключу. | — |
| 437 | `app/services/payment_service.py:414` | Х | не правится | ORDER BY ch.period DESC, pg.name: у ученика (group_id, period) уникально (uq_monthly_charge), pricing_group.name UNIQUE — полный. | — |
| 438 | `app/services/payment_service.py:490` | В | не правится | ORDER BY created_at DESC: платежи вставляются по одному на транзакцию, равных created_at в одной группе/месяце нет. | — |
| 439 | `app/services/payment_service.py:781` | Х | не правится | dict по users.id (PK). | — |
| 440 | `app/services/payment_service.py:814` | В | не правится | ORDER BY (status='pending') DESC, created_at DESC: платежи вставляются по одному на транзакцию; равенство практически невозможно. | — |
| 441 | `app/services/payment_service.py:1096` | В | не правится | Используется как множество/перебор до первого совпадения; начисление принадлежит одному ученику — результат однозначен. | — |
| 442 | `app/services/preview_navigation.py:69` | Х | не правится | ORDER BY order_number NULLS LAST, parent_course_id — полный (PK course_id+parent_course_id). | — |
| 443 | `app/services/preview_navigation.py:123` | Х | не правится | Строки сортируются в node_sequence по ключу task_key/order_key, включающему id. | — |
| 444 | `app/services/preview_navigation.py:159` | Х | не правится | Материалы сортируются в node_sequence по material_seq_key (order_position, id). | — |
| 445 | `app/services/preview_navigation.py:213` | Х | не правится | ORDER BY order_position NULLS LAST, Materials.id — полный. | — |
| 446 | `app/services/preview_navigation.py:224` | Х | не правится | Сортируется в Python sorted(task_key(..., id)) — полный. | — |
| 447 | `app/services/pricing_service.py:79` | Х | не правится | DISTINCT ON с ORDER BY student_id, starts_on DESC, id DESC — полный; результат сворачивается в dict по student_id (уникален после FULL JOIN с current_row, у которой partial UNIQUE uq_student_subscription_active). | — |
| 448 | `app/services/pricing_service.py:148` | Х | не правится | ends_on IS NULL — partial UNIQUE uq_student_subscription_active(student_id): одна строка на ученика, сворачивается в dict. | — |
| 449 | `app/services/pricing_service.py:190` | В | не правится | Список групп используется через `in`, len()==1/[0] (единственный элемент) и sorted(set) в charge_service/subscription_service — порядок не наблюдаем. | — |
| 450 | `app/services/pricing_service.py:211` | Х | не правится | ORDER BY is_active DESC, name — pricing_group.name UNIQUE (uq_pricing_group_name), ключ полный. | — |
| 451 | `app/services/pricing_service.py:233` | Х | не правится | Ложное срабатывание: SQL дособирается `ORDER BY group_id, sort_order, id` — порядок полный. | — |
| 452 | `app/services/pricing_service.py:415` | Х | не правится | ORDER BY sort_order, id — полный. | — |
| 453 | `app/services/pricing_service.py:501` | В | не правится | Строки сворачиваются в Python по student_id/(student_id, group_id); список учеников потом sorted по (full_name, student_id), группы внутри ученика идут по pg.name (UNIQUE). Ничья только при одноимённых курсах одного учен | — |
| 454 | `app/services/pricing_service.py:550` | В | не правится | Одна строка на ученика (ids из unnest уникальны), итог сортируется в Python по (full_name, student_id). | — |
| 455 | `app/services/pricing_service.py:822` | Х | не правится | count(*) — агрегат-число. | — |
| 456 | `app/services/pricing_service.py:849` | В | не правится | Строки override уникальны (student_id, group_id), результат сворачивается в set частот — порядок не влияет. | — |
| 457 | `app/services/pricing_service.py:928` | Х | не правится | Ложное срабатывание: ORDER BY u.full_name, o.student_id, o.group_id — полный (UNIQUE uq_student_price_override(student_id, group_id)); own[0] детерминирован. | — |
| 458 | `app/services/program_scope_service.py:144` | В | не правится | GROUP BY без ORDER BY, но строки суммируются в dict по course_id, приоритеты/резка дальше через sorted с добивкой по id. Остаточный эффект — порядок сложения float-минут (последние биты), практической разницы нет. | — |
| 459 | `app/services/program_scope_service.py:203` | Х | не правится | GROUP BY course_id — сворачивается в dict по уникальному course_id. | — |
| 460 | `app/services/program_scope_service.py:686` | Х | не правится | WHERE student_id AND program_kind — UNIQUE uq_student_program_scope_student_kind. | — |
| 461 | `app/services/program_scope_service.py:839` | В | не правится | Несколько программ ученика → пересечение множеств (коммутативно). | — |
| 462 | `app/services/program_scope_service.py:881` | В | не правится | Несколько программ → max порога по подкурсу (коммутативно). | — |
| 463 | `app/services/quiz_funnel_bot_service.py:105` | Х | не правится | start_token UNIQUE (uq_quiz_funnel_bot_lead_token). | — |
| 464 | `app/services/quiz_funnel_bot_service.py:216` | Х | не правится | lead_source.code UNIQUE (uq_lead_source_code). | — |
| 465 | `app/services/quiz_funnel_claim_service.py:122` | Х | не правится | courses.course_uid UNIQUE. | — |
| 466 | `app/services/quiz_funnel_service.py:106` | Х | не правится | course_uid UNIQUE, quiz_funnel_spec PK course_id — не более одной строки. | — |
| 467 | `app/services/quiz_funnel_service.py:132` | Х | не правится | Сворачивается в dict по коду из external_uid — tasks.external_uid глобально UNIQUE. | — |
| 468 | `app/services/quiz_funnel_service.py:162` | Х | не правится | DISTINCT ON (task_id) ORDER BY task_id, id DESC — полный. | — |
| 469 | `app/services/quiz_funnel_service.py:573` | Х | не правится | UNIQUE (guest_session_id, quiz_course_id). | — |
| 470 | `app/services/quiz_funnel_service.py:603` | Х | не правится | Внешний уровень: DISTINCT branch ORDER BY branch — ключ уникален. | — |
| 471 | `app/services/quiz_funnel_service.py:660` | Х | не правится | course_uid UNIQUE + spec PK. | — |
| 472 | `app/services/registration_notify_service.py:36` | В | не правится | Уведомление создаётся каждому маркетологу; порядок влияет лишь на порядок INSERT уведомлений. | — |
| 473 | `app/services/requirement_override_service.py:115` | В | не правится | Результат перекладывается в sorted(set(...)). | — |
| 474 | `app/services/requirement_override_service.py:153` | Х | не правится | Активные строки уникальны по (student_id, target_kind, target_id) — partial UNIQUE uq_sro_active_target; dict/set по target. | — |
| 475 | `app/services/requirement_override_service.py:302` | Х | не правится | dict по target — уникален среди активных (uq_sro_active_target). | — |
| 476 | `app/services/requirement_override_service.py:491` | Х | не правится | min() — агрегат-число. | — |
| 477 | `app/services/requirement_override_service.py:550` | Х | не правится | dict по (kind, id) — id уникальны в своей таблице. | — |
| 478 | `app/services/requirement_override_sync_cron_service.py:43` | Х | не правится | DISTINCT user_id ORDER BY user_id — полный. | — |
| 479 | `app/services/retention_service.py:69` | В | не правится | События сворачиваются в множества дней/недель/элементов и max — порядок не влияет. | — |
| 480 | `app/services/retention_service.py:274` | Х | не правится | PK user_achievements(user_id, achievement_id) — dict по уникальному ключу. | — |
| 481 | `app/services/retention_service.py:424` | В | не правится | Список id идёт в award_pending и в SQL `= ANY(:ids)` (ready-for-harder) — порядок не наблюдаем. | — |
| 482 | `app/services/retrieval_service.py:401` | В | не правится | Сворачивается в set task_id. | — |
| 483 | `app/services/retrieval_service.py:565` | Х | не правится | UNIQUE uq_task_review_schedule_student_task_course. | — |
| 484 | `app/services/roles_service.py:36` | Х | не правится | roles.name UNIQUE, (user, role) уникальна — ORDER BY r.name полный. | — |
| 485 | `app/services/schedule_booking_service.py:155` | Х | не правится | Ложное срабатывание: GROUP BY ls.id ... ORDER BY weekday, start_time, ls.id — полный. | — |
| 486 | `app/services/schedule_booking_service.py:457` | Х | не правится | count(*). | — |
| 487 | `app/services/schedule_booking_service.py:555` | Х | не правится | _REQUEST_SELECT — фрагмент; get_open_request(LIMIT 1) идёт по partial UNIQUE uq_schedule_slot_request_open_student, выборка по id — уникальна. Порядок list_requests — см. отдельную строку «пропуск скрипта». | — |
| 488 | `app/services/schedule_booking_service.py:707` | В | не правится | Уведомление каждому методисту; порядок — только порядок INSERT. | — |
| 489 | `app/services/schedule_booking_service.py:792` | Х | не правится | count(*). | — |
| 490 | `app/services/schedule_group_service.py:36` | Х | не правится | ORDER BY is_default DESC, name — name UNIQUE (schedule_group_name_key). | — |
| 491 | `app/services/schedule_group_service.py:62` | Х | не правится | is_default — partial UNIQUE index (одна группа по умолчанию). | — |
| 492 | `app/services/schedule_group_service.py:161` | Х | не правится | Фрагмент подзапроса для IN/EXISTS; внешнее использование упорядочено ORDER BY gid / ORDER BY eg.id. | — |
| 493 | `app/services/schedule_group_service.py:166` | Х | не правится | Фрагмент подзапроса для IN; внешнее использование упорядочено ORDER BY gid / ORDER BY eg.id. | — |
| 494 | `app/services/schedule_group_service.py:178` | Х | не правится | ORDER BY gid — id уникален. | — |
| 495 | `app/services/schedule_group_service.py:217` | Х | не правится | dict по LessonSlot.id (PK). | — |
| 496 | `app/services/schedule_group_service.py:249` | Х | не правится | ORDER BY sg.id LIMIT 1 — полный. | — |
| 497 | `app/services/schedule_group_service.py:266` | Х | не правится | ORDER BY ss.starts_on DESC, ss.id DESC LIMIT 1 — полный (и ends_on IS NULL уникален на ученика). | — |
| 498 | `app/services/schedule_plan_service.py:436` | Х | не правится | ORDER BY weekday, start_time, ls.id — полный; array_agg ORDER BY student_id. | — |
| 499 | `app/services/schedule_plan_service.py:483` | Х | не правится | ORDER BY active_slots DESC, full_name NULLS LAST, u.id — полный. | — |
| 500 | `app/services/schedule_preference_reminder_service.py:117` | Х | не правится | ORDER BY u.id — полный. | — |
| 501 | `app/services/schedule_preference_service.py:257` | Х | не правится | u.id = :uid, значение pending зависит только от u — любые дубли строк дают одно и то же. | — |
| 502 | `app/services/schedule_preference_service.py:293` | Х | не правится | Агрегаты count/max. | — |
| 503 | `app/services/schedule_preference_service.py:315` | Х | не правится | UNIQUE uq_student_schedule_preference_student. | — |
| 504 | `app/services/schedule_preference_service.py:327` | Х | не правится | ORDER BY weekday, start_time — UNIQUE (preference_id, weekday, start_time), ключ полный. | — |
| 505 | `app/services/schedule_preference_service.py:499` | В | не правится | Уведомление каждому методисту; порядок — только порядок INSERT. | — |
| 506 | `app/services/schedule_preference_service.py:576` | Х | не правится | UNIQUE по student_id. | — |
| 507 | `app/services/schedule_preference_service.py:670` | Х | не правится | ORDER BY created_at DESC, id DESC — полный. | — |
| 508 | `app/services/schedule_preference_service.py:719` | Х | не правится | ORDER BY filled, full_name NULLS LAST, u.id — полный. | — |
| 509 | `app/services/schedule_preference_service.py:806` | Х | не правится | GROUP BY weekday, start_time ORDER BY оба — полный. | — |
| 510 | `app/services/student_dashboard_service.py:165` | Х | не правится | DISTINCT ON (task_id) ORDER BY task_id, submitted_at: при ничьей выбирается одинаковое значение submitted_at (других колонок нет), результат — COUNT. | — |
| 511 | `app/services/student_dashboard_service.py:199` | Х | не правится | COUNT. | — |
| 512 | `app/services/student_dashboard_service.py:211` | Х | не правится | COUNT. | — |
| 513 | `app/services/student_dashboard_service.py:301` | Х | не правится | Агрегаты count/max. | — |
| 514 | `app/services/student_dashboard_service.py:405` | Х | не правится | max(). | — |
| 515 | `app/services/student_dashboard_service.py:461` | В | не правится | Список пиров идёт в SQL = ANY и в ранжирование долей (порядко-независимо). | — |
| 516 | `app/services/student_dashboard_service.py:491` | Х | не правится | dict по user_id (unnest уникальных пиров). | — |
| 517 | `app/services/student_dashboard_service.py:539` | Х | не правится | GROUP BY student_id → dict по ключу. | — |
| 518 | `app/services/student_dashboard_service.py:585` | Х | не правится | GROUP BY user_id → суммирование в dict. | — |
| 519 | `app/services/student_dashboard_service.py:607` | Х | не правится | GROUP BY student_id → суммирование в dict. | — |
| 520 | `app/services/student_dashboard_service.py:750` | Х | не правится | COUNT(DISTINCT). | — |
| 521 | `app/services/student_dashboard_service.py:761` | Х | не правится | COUNT(DISTINCT). | — |
| 522 | `app/services/student_dashboard_service.py:852` | В | не правится | Сворачивается в set task_id; сумма весов по set — порядок не наблюдаем. | — |
| 523 | `app/services/student_dashboard_service.py:866` | Х | не правится | COUNT(DISTINCT). | — |
| 524 | `app/services/student_dashboard_service.py:1164` | В | не правится | Сворачивается в set id служебных курсов. | — |
| 525 | `app/services/subscription_service.py:50` | Х | не правится | ends_on IS NULL — partial UNIQUE uq_student_subscription_active. | — |
| 526 | `app/services/subscription_service.py:513` | Х | не правится | ORDER BY p.sort_order, p.code — code UNIQUE (uq_subscription_plan_code). | — |
| 527 | `app/services/subscription_service.py:814` | Х | не правится | ORDER BY g.name — UNIQUE (student_id, group_id) + pricing_group.name UNIQUE, ключ полный. | — |
| 528 | `app/services/system_settings_service.py:80` | Х | не правится | dict по key (уникален). | — |
| 529 | `app/services/system_settings_service.py:123` | Х | не правится | dict по users.id. | — |
| 530 | `app/services/system_settings_service.py:238` | Х | не правится | По ключу настройки (уникален). | — |
| 531 | `app/services/task_effort_service.py:79` | Х | не правится | GROUPING SETS → dict по ячейке; внутренний LATERAL ORDER BY created_at DESC LIMIT 1 выбирает только created_at — при ничьей значение одинаково. | — |
| 532 | `app/services/task_effort_service.py:126` | Х | не правится | Агрегат (count, percentile). | — |
| 533 | `app/services/task_effort_service.py:267` | Х | не правится | dict по tasks.id. | — |
| 534 | `app/services/task_history_service.py:292` | В | не правится | Используется через any() — порядок не влияет. | — |
| 535 | `app/services/task_results_service.py:261` | В | не правится | ROW_NUMBER с ORDER BY submitted_at DESC, id DESC (полный); список далее только суммируется/считается. | — |
| 536 | `app/services/task_results_service.py:382` | Х | не правится | GROUP BY task_id → dict. | — |
| 537 | `app/services/task_results_service.py:396` | Х | не правится | dict по tasks.id. | — |
| 538 | `app/services/task_results_service.py:416` | Х | не правится | dict по courses.id. | — |
| 539 | `app/services/task_results_service.py:546` | Х | не правится | Агрегаты avg/sum/count. | — |
| 540 | `app/services/task_results_service.py:601` | В | не правится | Список id идёт в _last_attempts_flat (= ANY) — порядок не наблюдаем. | — |
| 541 | `app/services/task_search_service.py:58` | Х | не правится | Префикс-константа _TASK_ROW_SQL; используется либо по PK (WHERE t.id=:task_id, fetchone), либо с ORDER BY t.id LIMIT — порядок полный. | — |
| 542 | `app/services/tasks_acl_service.py:68` | Х | не правится | SELECT 1 … LIMIT 1 — проверка существования (bool), значение не зависит от строки. | — |
| 543 | `app/services/tasks_service.py:750` | Х | не правится | Сворачивается в dict {external_uid: {course_id: id}}; ключ (course_id, external_uid) уникален (uq_materials_course_external_uid). | — |
| 544 | `app/services/tasks_service.py:859` | Х | не правится | Сворачивается в dict/set; courses.course_uid UNIQUE (courses_course_uid_key) — дублей ключа twins нет. | — |
| 545 | `app/services/tasks_service.py:936` | Х | не правится | Внешний UPDATE без порядка не нужен; ROW_NUMBER внутри окна упорядочен с добивкой id ASC — ключ полный. | — |
| 546 | `app/services/tasks_service.py:1071` | В | не правится | tasks.external_uid UNIQUE, ответ — пары uid→id для импорт-конвейера (POST /api/v1/tasks/find-by-external), клиент использует как словарь; порядок items семантики не несёт. | — |
| 547 | `app/services/tasks_service.py:1129` | Х | не правится | ORDER BY order_position NULLS LAST, id — ключ полный (порядок движка не трогаем). | — |
| 548 | `app/services/tasks_service.py:1146` | Х | не правится | Ветка with_transparent: результат сортируется в Python по task_key(order_position, id, …) — id в ключе, порядок полный. | — |
| 549 | `app/services/teacher_activity_feed_service.py:507` | В | не правится | Результат пересортировывается в Python по (not on_lesson, name.casefold(), id) — ключ полный. | — |
| 550 | `app/services/teacher_activity_feed_service.py:520` | В | не правится | То же: Python-sort с id в ключе. | — |
| 551 | `app/services/teacher_attention_service.py:35` | Х | не правится | Агрегат count/min — одна строка. | — |
| 552 | `app/services/teacher_attention_service.py:43` | Х | не правится | GROUP BY kind, сворачивается в dict по kind (уникален в выборке). | — |
| 553 | `app/services/teacher_attention_service.py:63` | Х | не правится | Агрегат count/min — одна строка. | — |
| 554 | `app/services/teacher_courses_service.py:114` | В | не правится | Та же проблема пагинации (linked_at/title/created_at без id), но у метода нет вызывающих в app/ (мёртвый код). | — |
| 555 | `app/services/teacher_dashboard_service.py:119` | В | не правится | Строки только агрегируются в счётчики/списки реакций (median порядок-независим); внутренний LATERAL уже ORDER BY created_at DESC, id DESC; HELP_REQUEST_OWNER_SQL с добивкой rr.id. | — |
| 556 | `app/services/teacher_dashboard_service.py:176` | Х | не правится | GROUP BY closed_by → dict по уникальному ключу. | — |
| 557 | `app/services/teacher_dashboard_service.py:192` | В | не правится | dict teacher_id→full_name; при дублях строк user_roles значение одинаковое; итог сортируется в Python с teacher_id в ключе. | — |
| 558 | `app/services/teacher_dashboard_service.py:221` | Х | не правится | WHERE id = ANY → dict по PK. | — |
| 559 | `app/services/teacher_dashboard_service.py:317` | Х | не правится | Агрегат (COUNT, percentile_cont) — одна строка. | — |
| 560 | `app/services/teacher_lesson_summary_service.py:380` | Х | не правится | DISTINCT ON (task_id) ORDER BY task_id, submitted_at: при равенстве берётся одно и то же значение submitted_at (min), дальше только COUNT. | — |
| 561 | `app/services/teacher_lesson_summary_service.py:417` | Х | не правится | COUNT(*). | — |
| 562 | `app/services/teacher_lesson_summary_service.py:434` | Х | не правится | COUNT(*). | — |
| 563 | `app/services/teacher_lesson_summary_service.py:461` | Х | не правится | DISTINCT ON (task_id) выбирает только task_id — значение не зависит от строки; дальше COUNT. | — |
| 564 | `app/services/teacher_lesson_summary_service.py:591` | Х | не правится | Сворачивается в set id. | — |
| 565 | `app/services/teacher_lesson_summary_service.py:715` | В | не правится | GROUP BY (occurrence, student) → dict по student_id: при нескольких занятиях ученика (школьная сводка) значение перезаписывается последней строкой, но там используется только факт наличия idle (reason/rank), в панели зан | — |
| 566 | `app/services/teacher_lesson_summary_service.py:766` | Х | не правится | Сворачивается в set student_id. | — |
| 567 | `app/services/teacher_lesson_summary_service.py:1037` | Х | не правится | WHERE id = ANY → dict по PK. | — |
| 568 | `app/services/teacher_lesson_summary_service.py:1215` | Х | не правится | GROUP BY student_id → dict по уникальному ключу. | — |
| 569 | `app/services/teacher_lesson_summary_service.py:1247` | В | не правится | Строки сворачиваются в множества/пары; дальнейшие загрузчики работают по sorted student_ids, итог — счётчики. | — |
| 570 | `app/services/teacher_lesson_summary_service.py:1351` | Х | не правится | WHERE id = ANY → dict по PK (порядок списка задаётся строкой 1359). | — |
| 571 | `app/services/teacher_queue_service.py:1370` | Х | не правится | COUNT/MIN — одна строка. | — |
| 572 | `app/services/teacher_queue_service.py:1541` | Х | не правится | COUNT(*). | — |
| 573 | `app/services/teacher_queue_service.py:1718` | Х | не правится | Агрегат COUNT FILTER — одна строка. | — |
| 574 | `app/services/teacher_queue_service.py:1751` | Х | не правится | Агрегат COUNT — одна строка. | — |
| 575 | `app/services/topic_mastery_service.py:504` | Х | не правится | COUNT(DISTINCT) — скаляр. | — |
| 576 | `app/services/unseen_constructs_service.py:466` | В | не правится | Содержимое материалов разбирается в множества кодов, объединяется; len(rows) — счётчик. | — |
| 577 | `app/services/unseen_constructs_service.py:475` | В | не правится | Объединение множеств кодов — порядок-независимо. | — |
| 578 | `app/services/user_courses_service.py:203` | Х | не правится | Сворачивается в set course_id. | — |
| 579 | `app/services/user_courses_service.py:266` | Х | не правится | dict course_id→is_active; (user_id, course_id) уникален. | — |
| 580 | `app/services/user_courses_service.py:312` | В | не правится | ORDER BY order_number NULLS LAST, added_at без добивки (added_at равен при bulk-зачислении), но возвращаемый список эндпоинт switch-direction игнорирует (отдаёт _get_student_courses). | — |
| 581 | `app/services/user_merge_service.py:130` | Х | не правится | COUNT(*). | — |
| 582 | `app/services/user_merge_service.py:158` | Х | не правится | Действующая подписка уникальна: частичный UNIQUE uq_student_subscription_active (student_id) WHERE ends_on IS NULL. | — |
| 583 | `app/services/user_merge_service.py:168` | Х | не правится | То же, uq_student_subscription_active. | — |
| 584 | `app/services/user_merge_service.py:476` | Х | не правится | count(*). | — |
| 585 | `app/services/users_dedup_service.py:112` | Х | не правится | DISTINCT user_id → set. | — |
| 586 | `app/services/lesson_plan_service.py:315` | Б | исправлено (находка ревью) | ORDER BY h.created_at DESC, h.id DESC — открытые заявки в плане урока | — |
| 587 | `app/services/payment_service.py:496` | Б | исправлено (находка ревью) | ORDER BY created_at DESC, id DESC — платежи ученика | — |
| 588 | `app/services/pricing_service.py:577` | Б | исправлено (находка ревью) | ORDER BY u.full_name, m.student_id, m.group_id — тёзки в ценах группы | — |
| 589 | `app/services/messenger_channel_service.py:533` | Х | не правится (находка ревью) | _CURATOR_SQL LIMIT 1: частичный UNIQUE uq_student_curator_current — один действующий куратор | — |
