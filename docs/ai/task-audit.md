# task_audit — аудит изменений tasks.course_id / tasks.is_active

**Источники:** таблица `public.task_audit` (append-only через триггер `task_audit_no_modify`), триггеры `trg_task_audit_update` / `trg_task_audit_delete` на `tasks` (функция `log_task_audit`), модель `app/models/task_audit.py`.
**Миграция:** `app/db/migrations/versions/20260805_100000_tsk114_task_audit.py`.
**Связано:** `docs/database-triggers-contract.md` (раздел 15 — тот же паттерн session-var, что `app.skip_task_order_trigger`), `app/db/audit_context.py`.
**Задача:** tsk-114, профилактика повтора tsk-113 (353 задания курса «Python для ЕГЭ» тихо переехали в архивный курс сменой `course_id`; расследовать причину и дату не удалось — в `tasks` не было `created_at`/`updated_at`, а `audit_event` пишет только login-события).

## Зачем эта таблица, а не `audit_event`

`audit_event` (см. [audit-events-contract.md](audit-events-contract.md)) — событийный журнал уровня приложения, emit'ится вручную из кода через `log_event(db, EVENT_TYPE, ...)`. Он в принципе не может поймать изменение, сделанное в обход приложения (ad-hoc SQL-скрипт, прямой `UPDATE tasks ... `psql`) — именно так и произошёл tsk-113. `task_audit` — триггер уровня БД: ловит ЛЮБОЙ путь записи, включая тот, что в момент инцидента никто не предвидел.

## Как расследовать инцидент («что случилось с заданием X»)

```sql
-- Вся история изменений course_id/is_active одного задания (по id)
SELECT action, old_course_id, new_course_id, old_is_active, new_is_active,
       changed_at, changed_by, db_role
FROM task_audit
WHERE task_id = :task_id
ORDER BY changed_at;

-- То же самое, если задание уже удалено и id неизвестен — искать по external_uid
SELECT * FROM task_audit WHERE external_uid = 'lms:...' ORDER BY changed_at;

-- «Что вообще поменялось за последние N дней» (массовый инцидент вроде tsk-113)
SELECT task_id, external_uid, action, old_course_id, new_course_id,
       old_is_active, new_is_active, changed_at, changed_by, db_role
FROM task_audit
WHERE changed_at >= now() - interval '7 days'
ORDER BY changed_at DESC;

-- Массовое перемещение между двумя конкретными курсами (сигнатура tsk-113)
SELECT task_id, external_uid, changed_at, changed_by
FROM task_audit
WHERE action = 'UPDATE' AND old_course_id = :from_course AND new_course_id = :to_course
ORDER BY changed_at;
```

Каждая строка `UPDATE` — это ПОЛНЫЙ снимок обоих полей (`course_id` и `is_active`) до/после, а не только того, что изменилось: если менялся только `is_active`, `old_course_id = new_course_id` в этой же строке — можно всегда увидеть, в каком курсе задание было в момент изменения, не JOIN'я соседние строки.

## Структура таблицы

| Поле | Тип | Описание |
|---|---|---|
| `id` | bigserial | Primary key. |
| `task_id` | integer NOT NULL | `tasks.id` на момент изменения. **Без FK** — запись обязана пережить `DELETE` самого задания (один из аудируемых случаев). |
| `external_uid` | text NULL | Снимок `tasks.external_uid` — переживает и удаление, и смену `course_id`; способ найти историю задания, если `task_id` уже не существует. |
| `action` | text NOT NULL | `'UPDATE'` \| `'DELETE'` (CHECK-ограничение). |
| `old_course_id` / `new_course_id` | integer NULL | `NULL` в `new_*` только для `DELETE`. |
| `old_is_active` / `new_is_active` | boolean NULL | Аналогично. |
| `changed_at` | timestamptz NOT NULL DEFAULT `clock_timestamp()` | Реальный момент записи строки (не `now()` — тот вернул бы момент начала транзакции, что важно при пакетной обработке). |
| `changed_by` | text NULL | Кооперативная метка источника, см. ниже. `NULL` = источник не назвался. |
| `db_role` | text NOT NULL | `current_user` соединения — заполняется ВСЕГДА, независимо от кооперации кода. |

## Источник изменения (`changed_by`) — как это работает

Триггер читает session-var `app.audit_actor` (`current_setting('app.audit_actor', true)`) — тот же принцип изоляции, что `app.skip_task_order_trigger` из `docs/database-triggers-contract.md` §15: `set_config(name, value, is_local=true)` видно только текущей транзакции, автоматически сбрасывается на `COMMIT`/`ROLLBACK`, не утекает в чужие сессии пула соединений.

Приложение проставляет метку в двух местах (`app/db/audit_context.py:set_audit_actor`):

| Место | Метка | Когда |
|---|---|---|
| `app/api/deps.py:get_db` | `'service:api_key'` | Единственный auth-путь generic CRUD-роутера `tasks` (`PATCH`/`PUT`/`DELETE /tasks/{id}`, см. `app/api/main.py`) — TG_LMS-бот и другие клиенты сервисного ключа. |
| `app/services/tasks_service.py:TasksService.bulk_upsert` | `'bulk_upsert'` | Импорт из Google Sheets / ContentBackbone через `POST /tasks/bulk-upsert` — перекрывает более общую метку `service:api_key`, т.к. ставится позже, непосредственно перед `create`/`update`. |

**Важно для похожего кода в будущем:** `repo.create`/`repo.update` коммитят построчно (`commit=True` по умолчанию), а `set_config(..., is_local=true)` живёт только до ближайшего `COMMIT`. Поэтому в `bulk_upsert` метка проставляется заново **перед каждым вызовом** `create`/`update` внутри цикла — если этого не сделать, после первого закоммиченного задания все следующие строки батча получат `changed_by = NULL`.

### Ad-hoc скрипты правки данных

Скрипты, которые меняют `tasks.course_id`/`is_active` напрямую (не через FastAPI-приложение — например, `scripts/*.py`, использующие `async_session_factory` напрямую), **обязаны** проставить метку сами, иначе `changed_by` останется `NULL`:

```python
await db.execute(
    text("SELECT set_config('app.audit_actor', :actor, true)"),
    {"actor": "script:tsk123_fix_something.py"},
)
```

`NULL` в `changed_by` — не дефект, а честный сигнал «источник не назвался»: колонка `db_role` всё равно заполнена (роль БД-соединения), это минимальный след, не зависящий от кооперации кода — им и приходится довольствоваться для скриптов, которые это правило не соблюли.

### Известное ограничение

Прямых `PATCH`/`PUT`-эндпоинтов для `tasks.course_id`/`is_active` с привязкой к конкретному аутентифицированному пользователю (`get_current_user`, cookie-сессия методиста) на момент tsk-114 в проекте нет — только сервисный API-ключ (`service:api_key`) и explicit `bulk_upsert`. Если такой эндпоинт появится, ему нужно проставить `app.audit_actor = f"user:{current_user.id}"` тем же способом — для полной атрибуции конкретному человеку, не только классу клиента.

## Safety-valve: временное отключение

Если будущему bulk-фиксу (например, массовой миграции старых данных) НЕ нужно шуметь в аудите тысячами технических строк, доступен тот же паттерн, что у `app.skip_task_order_trigger`:

```sql
SELECT set_config('app.skip_task_audit_trigger', 'true', true);  -- is_local
-- ... UPDATE'ы ...
-- значение сбросится само на COMMIT/ROLLBACK
```

**НЕ использовать `ALTER TABLE tasks DISABLE TRIGGER`** — берёт ACCESS EXCLUSIVE лок на всю таблицу `tasks`, блокируя live-трафик студентов по ВСЕМ курсам, не только по затрагиваемым скриптом строкам (тот же принцип, что задокументирован для `app.skip_task_order_trigger`). Использовать эту задвижку экономно: смысл всей задачи tsk-114 в том, чтобы изменения `course_id`/`is_active` НЕ проходили незамеченными — по умолчанию пусть аудит работает, даже для массовых правок.

## Инварианты

1. **Append-only** — `UPDATE`/`DELETE` строк `task_audit` запрещены триггером `task_audit_no_modify` (`RAISE EXCEPTION`), зеркало `audit_event`/`audit_event_no_modify`. Даже скрипт, способный незаметно передвинуть `course_id`, не может незаметно стереть собственный след.
2. **Аудируются только реальные изменения** — `WHEN`-условие триггера (`OLD.course_id IS DISTINCT FROM NEW.course_id OR OLD.is_active IS DISTINCT FROM NEW.is_active`) не вызывает функцию на `UPDATE`, где эти поля не менялись (правка `task_content`/`solution_rules`/`order_position` и т.п.) — обычный трафик методиста и Learning Engine не создаёт лишних строк и не замедляется.
3. **INSERT не аудируется** — у новой строки нет «было», сравнивать не с чем. Аудит начинается с первого `UPDATE`/`DELETE` существующего задания.
4. **Без FK на `tasks.id`** — намеренно: запись обязана пережить `DELETE` самого задания.
