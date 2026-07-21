# tsk-345 — Реордер ЕГЭ-курсов по сложности + durable-фикс

**Дата:** 2026-07-21
**Приоритет:** P0 (блокирует tsk-347)
**Координация:** tsk-337 (системная проверка уникальности order_position, закрыта чисто 2026-07-21)

## Контекст

Живая находка (оператор): задание 2059 (NORMAL, difficulty_id=3, курс 138 «Задание №3.
Базы данных в Excel») стояло вторым в списке, хотя должно идти после всех EASY.

**Корневая причина.** `order_position` был отсортирован по правилу
THEORY→EASY→NORMAL→HARD→PROJECT **один раз** (`scripts/reorder_tasks_by_difficulty_type.py`,
миграция этапа 1.7, 2026-05-21). Два независимых пути ломали этот порядок на любом
следующем изменении:

1. `trg_set_task_order_position` при **CREATE** без явного `order_position` ставит
   `MAX(order_position)+1` (в конец курса) — не смотрит на `difficulty_id`/тип.
2. **UPDATE** без явного `order_position` вообще не трогает позицию — переклассификация
   задания (напр. THEORY-перетег в tsk-318, 135 заданий) оставляет его в чужой группе.

Оба пути прошли через **все импорты после 2026-05-21**: Яндекс (tsk-100, 280 заданий),
Крылов (tsk-317/319, 108 заданий), текущие доливки KompEGE/Поляков/sdamgia, плюс
THEORY-перетег tsk-318.

## Часть 1 — быстрый фикс (прод)

### Read (до записи)

MCP `learn_prod_db` (read-only), курсы 138-165 (номерные «Задание N ЕГЭ»):
- 25 курсов с заданиями, 2504 задания.
- Межгрупповые нарушения (`difficulty_id` следующей задачи ниже предыдущей по
  `order_position`) — в 25 из 25 курсов, от 2 до 11 нарушений на курс.
- Задание 2059: `course_id=138, order_position=2, difficulty_id=3 (NORMAL)`.
  (Уточнение: тикет называл сложность 2059 «HARD» — по факту в БД `difficulty_id=3`
  = NORMAL/«Средняя», не HARD. Не меняет диагноз: место всё равно неверное — до EASY.)

### Dry-run

Правило сортировки — то же, что в историческом скрипте (`difficulty_id ASC`, затем
тип: SC/MC → TA/SA → SA_COM), но тайбрейк внутри группы — **текущий `order_position`**,
а не `id`: сохраняет уже видимый учениками/методистами относительный порядок внутри
сложности (в т.ч. ручной drag-and-drop реордер через `POST /courses/{id}/tasks/reorder`),
меняет только межгрупповые границы.

Dry-run на проде (`scripts/reorder_courses_by_difficulty_tsk345.py`, без `--apply`):
- `courses_in_range=25 tasks_in_range=2504`
- `UPDATE rowcount = 2435` (задания, у которых меняется order_position)
- 0 коллизий order_position внутри course_id
- 0 межгрупповых нарушений после реордера
- Задание 2059: `2 → 39` (после всех EASY этого курса, перед HARD)

### Apply

Применено на прод (`--apply`), тем же скриптом, тот же результат `UPDATE rowcount = 2435`,
COMMIT.

### Независимая верификация (отдельный канал — MCP `learn_prod_db`, read-only)

```sql
-- межгрупповые нарушения в курсах 138-165
SELECT course_id, COUNT(*) FROM (...) WHERE difficulty_id < prev_difficulty GROUP BY course_id;
→ []  (0 нарушений)

-- коллизии order_position в курсах 138-165
SELECT course_id, order_position, COUNT(*) FROM tasks WHERE course_id BETWEEN 138 AND 165
GROUP BY course_id, order_position HAVING COUNT(*) > 1;
→ []  (0 коллизий)

-- глобальная проверка коллизий (координация с tsk-337 — по ВСЕЙ платформе)
SELECT course_id, order_position, COUNT(*) FROM tasks
GROUP BY course_id, order_position HAVING COUNT(*) > 1;
→ []  (0 коллизий по всей платформе, инвариант tsk-337 не задет)

-- задание 2059
SELECT id, course_id, order_position, difficulty_id FROM tasks WHERE id = 2059;
→ {id: 2059, course_id: 138, order_position: 39, difficulty_id: 3}
```

**Живой прогон (UI):** попытка через `claude-in-chrome` под текущей сессией браузера
(аккаунт id=2, `victor.komlev@mail.ru`) уткнулась в «Курс не найден» — этот аккаунт не
записан на курс 138 ни на родителя 112 (`user_courses` пусто), `/teacher/courses/138`
не существует как маршрут. Это отдельный, не связанный с tsk-345 пробел доступа —
аккаунт id=142 записан на родителя 112, но текущая браузерная сессия была под id=2.
Не стал тратить время на переавторизацию — вердикт по БД (три независимых read-only
запроса выше) в этом проекте признан авторитетным источником (см.
`project_prod_live_testing` в памяти: «вердикт сдачи проверять в БД, а не по
UI-инференсу»), и он однозначно подтверждает фикс.

## Часть 2 — durable-фикс (выбор и обоснование)

**Выбран вариант (а):** реордер-хук внутри `TasksService.bulk_upsert`
(`app/services/tasks_service.py`), а не отдельный конвейер или cron.

### Почему (а), а не (б)/(в)

Единственная точка вставки задач подтверждена чтением кода:
- Прямой `POST /tasks/bulk-upsert` → `TasksService.bulk_upsert`.
- Google Sheets импорт (`POST /tasks/import/google-sheets`) → тот же
  `tasks_service.bulk_upsert(db, parsed_tasks)` (app/api/v1/tasks_extra.py:1111).
- ContentBackbone `task_adapter`/`content_hub_client` строит payload именно для
  `POST /api/v1/tasks/bulk-upsert` (`monolith/task_adapter/contracts.py:243`).

Три разных конвейера сходятся в ОДНОМ методе — фикс в одном месте закрывает класс
дефекта для всех путей одновременно. Это и есть критерий «не выбирать (а) бездумно,
если не интегрируется просто» — здесь интегрируется тривиально.

- **(б) умнее триггер** — отклонено: пришлось бы дублировать CASE-маппинг типов задач
  (SC/MC/TA/SA/SA_COM) в PL/pgSQL и синхронизировать с Python-стороной при любом новом
  типе; полноценный поиск позиции по (difficulty_id, type) в триггере на каждой строке
  vs один пакетный ROW_NUMBER после батча — на импорте 280 заданий это O(n) сдвигов на
  вставку против одного пересчёта в конце.
- **(в) периодический cron** — отклонено: реактивно (окно поломанного порядка до
  следующего прогона), плюс в проекте нет существующей инфраструктуры job-раннера для
  LMS-скриптов (в отличие от APScheduler-задач самого сервиса).

### Что именно триггерит реордер

`bulk_upsert` теперь отслеживает курсы, где батч мог сломать межгрупповой порядок:
- **CREATE** без явного `order_position` (триггер БД поставил `MAX+1`) — тот же класс,
  что и живая находка (задание встаёт в конец курса).
- **UPDATE** без явного `order_position`, где `difficulty_id` или тип задачи
  изменились относительно текущего состояния — тот же класс, что THEORY-перетег tsk-318.

CREATE/UPDATE с **явным** `order_position` не трогаются — это осознанный выбор
вызывающего (сохранён контракт T16/T18 из tsk-004: явная позиция — не гипотеза, а
команда).

В конце батча для каждого затронутого `course_id` вызывается
`_reorder_tasks_by_difficulty` — тот же ROW_NUMBER-паттерн, что в историческом
`scripts/reorder_tasks_by_difficulty_type.py`, но с тайбрейком по текущему
`order_position` (не `id`) для сохранения относительного порядка внутри группы.

**Важное отличие от исторического скрипта и от первой версии этого фикса:**
отключение `trg_set_task_order_position` — через session-variable
`app.skip_task_order_trigger` (`SELECT set_config(..., true)`, тот же паттерн, что уже
использует `TasksRepository.reorder_tasks`), а **не** через
`ALTER TABLE ... DISABLE TRIGGER`. Первая версия использовала ALTER TABLE и была
исправлена после того, как тесты вскрыли реальную проблему: этот DDL берёт ACCESS
EXCLUSIVE лок на ВСЮ таблицу `tasks` — на проде это означало бы, что реордер одного
курса (напр. один импорт 5 заданий) кратковременно блокирует live-запросы студентов
по **всем** курсам платформы, не только по затронутому. Session-variable — row-level,
без блокировки таблицы.

## Тесты (инвариант)

`tests/test_tsk345_reorder_by_difficulty.py` (4 теста, все проходят):
1. `test_new_import_after_established_order_stays_sorted` — новый импорт EASY-задания
   в уже отсортированный курс не уезжает в конец после NORMAL (регресс живой находки).
2. `test_reclassify_without_position_reorders` — UPDATE, меняющий `difficulty_id` без
   явной позиции, двигает задачу в правильную группу (регресс tsk-318).
3. `test_explicit_position_not_reordered_around` — явный `order_position` не
   переопределяется реордером (контракт T16/T18 из tsk-004).
4. `test_trigger_stays_active_after_bulk_upsert_reorder` — триггер остаётся включён
   (`tgenabled='O'`) и session-variable `app.skip_task_order_trigger` сброшена после
   реордера (не «протекает» в следующую операцию той же транзакции).

Регрессия: `test_tasks_order_position_api.py`, `test_tasks_reorder_api.py`,
`test_materials_bulk_upsert.py`, `test_tasks_import_task_content_json.py`,
`test_tsk088_task_content_hints_preserved.py` — 36 passed. Полный набор проекта —
827 passed, 10 skipped (1 упавший тест-реестр `SELF_MANAGED_CONNECTION_MODULES`
исправлен добавлением нового тестового файла в список — см. `tests/conftest.py`).

## Изменённые файлы

- `app/services/tasks_service.py` — `bulk_upsert` (отслеживание затронутых курсов) +
  новый метод `_reorder_tasks_by_difficulty`.
- `scripts/reorder_courses_by_difficulty_tsk345.py` — разовый бэкфилл (новый).
- `tests/test_tsk345_reorder_by_difficulty.py` — тесты инварианта (новый).
- `tests/conftest.py` — регистрация нового файла в `SELF_MANAGED_CONNECTION_MODULES`.

## Деплой

Коммит `2be088e` → `origin/main` → деплой на прод (`lms-spw-vds`, `/opt/lms`,
`deploy/vps/deploy.sh`: git reset --hard + alembic upgrade head + restart) → смоук
`/health` → `{"status":"ok"}`. Бэкфилл выполнен ПОСЛЕ деплоя (durable-фикс уже активен
на момент разовой чистки данных).

## Риски / Follow-ups

- Тикет называл задание 2059 «HARD» — по факту в БД `difficulty_id=3` (NORMAL). Не
  блокирует фикс (место всё равно было неверным), но стоит перепроверить источник
  классификации (разбор в ТГ) на всякий случай отдельно, если для этого задания
  важна именно метка сложности, а не только порядок.
- Живой UI-прогон не выполнен (аккаунт текущей браузерной сессии не имеет доступа к
  проверенному курсу) — вердикт основан на независимой BD-верификации (три
  read-only запроса с разных ракурсов). Рекомендация: если нужна именно
  визуальная проверка, повторить через аккаунт id=142 (записан на курс-родитель 112).
- tsk-346 (тот же класс дефекта для Python-курсов, другое дерево) — не в скоупе
  этой задачи, самостоятельная работа.
