# task_result_audit — журнал правок и удалений оценок (`task_results.score` / `is_correct`)

**Источники:** таблица `public.task_result_audit` (append-only через триггер `task_result_audit_no_modify`), триггеры `trg_task_result_audit_update` и `trg_task_result_audit_delete` на `task_results` (общая функция `log_task_result_audit`), модель `app/models/task_result_audit.py`.
**Миграции:** `2026_09_07_tsk803_task_result_audit.py` (правка оценки), `2026_09_07_tsk803b_task_result_delete_audit.py` (удаление результата).
**Связано:** [task-audit.md](task-audit.md) — тот же паттерн для содержимого заданий; [audit-events-contract.md](audit-events-contract.md) — событийный журнал уровня приложения; `docs/database-triggers-contract.md` §15 — принцип session-var `app.skip_*`.
**Задача:** tsk-803 (+ продолжение tsk-803b — аудит удаления, решение оператора 07.09). Заведена по следам tsk-801: правка боевых оценок 17 учеников (51 ложный незачёт) оказалась откатываемой только по бэкапу в скратчпаде сессии и списку id, вписанному в задачу руками.

## Зачем эта таблица, если оценки уже пишутся в `audit_event`

Штатный путь и правда прикрыт: `POST /review/grade` пишет событие `teacher.review.graded` (на 06.09 — 142 события), `POST /review/{id}/regrade` — `teacher.review.regraded`. Пробелов было два, и оба закрыты tsk-803:

| Путь изменения оценки | След до tsk-803 | След после |
|---|---|---|
| Оценка преподавателем через кабинет | `audit_event`, но **без прежнего значения** | `audit_event` (с `previous_score`) + `task_result_audit` |
| Переоценка `/regrade` | `audit_event` со старым и новым баллом | то же + `task_result_audit` |
| **Прямой SQL / ad-hoc скрипт** | **никакого** | `task_result_audit` |
| **Удаление результата** (в том числе каскадом от `users`/`tasks`) | **никакого** | `task_result_audit`, запись `action = 'DELETE'` |
| Автопроверка (создание записи) | сама запись `task_results` | без изменений (INSERT не аудируется) |

`audit_event` — журнал уровня приложения: он в принципе не видит изменения, сделанного в обход приложения. А правки в обход — не экзотика: именно ими чинилась бо́льшая часть дефектов августа-сентября (tsk-760, tsk-796, tsk-801), все по протоколу `/db-check`, и ни одна не попала в `audit_event`. Триггер уровня БД ловит любой путь.

Вторая половина задачи — **прежнее значение**. Событие `graded` отвечало на вопрос «что стало», но не «что было»: откатить переоценку по нему было нельзя, а `previous_score` в уведомлении ученику заполнялся жёстким `None`. Теперь и событие, и payload уведомления несут `previous_score` / `previous_is_correct`.

## Границы журнала

- **INSERT не аудируется.** Сама запись результата и есть его первое состояние, а поток вставок на порядок больше: на момент миграции прод показывал 22 122 вставки против 4 944 обновлений и 39 удалений. Аудит на INSERT обогнал бы исходную таблицу за неделю, ничего не добавив.
- **DELETE аудируется с tsk-803b** (решение оператора 07.09; в первой версии журнала удаление было выведено за границы). Причина: без него пара «удалить и вставить заново» позволяла заменить оценку неотслеживаемо — тот самый обход, ради закрытия которого журнал заводился. Пишется снимок последнего состояния: `old_score`/`old_is_correct` заполнены, `new_*` пусты. Каскад от `users`/`tasks` — основной путь удаления на проде, и именно там след ценнее всего: строки исчезают безвозвратно.
- **Ретроспективы нет и не будет.** Восстановить, кто правил оценки ДО выката триггера, нельзя — данных нет. Правки tsk-801 задокументированы списком id в самой задаче; более ранние (tsk-760, tsk-796) — нет.
- **Только `score` и `is_correct`.** Остальные UPDATE (`review_claim_*` при захвате проверки, `metrics`, `code_review`, `checked_at`) функцию не вызывают — условие в `WHEN` держит журнал от роста на обычном трафике очереди преподавателя.

## Как расследовать («что было с этой оценкой»)

```sql
-- Вся история правок одной работы
SELECT changed_at, changed_by, db_role,
       old_score, new_score, old_is_correct, new_is_correct
FROM task_result_audit
WHERE result_id = :result_id
ORDER BY changed_at;

-- Правили ли оценки конкретного ученика (индекс idx_task_result_audit_user)
SELECT result_id, task_id, changed_at, changed_by,
       old_score, new_score, old_is_correct, new_is_correct
FROM task_result_audit
WHERE user_id = :user_id
ORDER BY changed_at DESC;

-- Что вообще менялось за неделю — массовая правка вроде tsk-801
SELECT changed_by, db_role, count(*) AS правок,
       min(changed_at) AS начало, max(changed_at) AS конец
FROM task_result_audit
WHERE changed_at >= now() - interval '7 days'
GROUP BY 1, 2
ORDER BY правок DESC;

-- Незачёты, ставшие зачётами (сигнатура починки ложных вердиктов)
SELECT result_id, user_id, task_id, changed_at, changed_by
FROM task_result_audit
WHERE old_is_correct IS FALSE AND new_is_correct IS TRUE
ORDER BY changed_at DESC;

-- Какие оценки были у удалённых работ (строк task_results уже нет)
SELECT result_id, user_id, task_id, old_score, old_is_correct,
       changed_at, changed_by, db_role
FROM task_result_audit
WHERE action = 'DELETE'
ORDER BY changed_at DESC;

-- Подмена оценки в обход журнала правок: работа удалена, у ученика по тому же
-- заданию появилась новая. Сама по себе не улика (пересдача выглядит так же),
-- но даёт список для разбора.
SELECT a.result_id AS udalyonnaya_rabota, a.user_id, a.task_id,
       a.old_score AS bylo, a.changed_at AS udalena, a.changed_by,
       tr.id AS novaya_rabota, tr.score AS stalo
FROM task_result_audit a
JOIN task_results tr
  ON tr.user_id = a.user_id AND tr.task_id = a.task_id
 AND tr.submitted_at > a.changed_at
WHERE a.action = 'DELETE'
ORDER BY a.changed_at DESC;
```

## Как откатить правку

```sql
BEGIN;
-- Откат не должен сам попасть в журнал как новая правка: если он попадёт,
-- история перестанет читаться «что было -> что стало».
SELECT set_config('app.skip_task_result_audit_trigger', 'true', true);

UPDATE task_results tr
SET score = a.old_score, is_correct = a.old_is_correct
FROM task_result_audit a
WHERE a.id = :audit_id AND tr.id = a.result_id;

SELECT set_config('app.skip_task_result_audit_trigger', 'false', true);
COMMIT;
```

Рубильник — тот же принцип, что `app.skip_task_order_trigger` и `app.skip_task_audit_trigger`: `set_config(..., is_local=true)` виден только текущей транзакции, сбрасывается на `COMMIT`/`ROLLBACK` и не утекает в чужие сессии пула. Откат оценок — операция с боевыми данными: выполняется по протоколу `/db-check` (прочитать текущее состояние, показать выборку, транзакция, проверка после).

## Источник правки (`changed_by`)

Триггер читает session-var `app.audit_actor` — ту же метку, что `task_audit`. Проставляется через `app/db/audit_context.py:set_audit_actor`:

| Место | Метка | Когда |
|---|---|---|
| `teacher_queue_service.grade_review` | `user:<teacher_id>` | Оценка через кабинет преподавателя (tsk-803) |
| `teacher_queue_service.regrade_review` | `user:<actor_user_id>` | Переоценка (tsk-803) |
| `app/api/deps.py:get_db` | `service:api_key` | Клиенты сервисного ключа |

Эндпоинты оценки идут через `get_bare_db`, где метка раньше не проставлялась вовсе — без правки tsk-803 штатная оценка была бы в журнале неотличима от прямого SQL (`changed_by IS NULL`).

**Ad-hoc скрипты правки оценок обязаны назваться сами** — иначе `changed_by` останется `NULL`:

```python
await db.execute(
    text("SELECT set_config('app.audit_actor', :actor, true)"),
    {"actor": "script:tsk123_fix_scores.py"},
)
```

`NULL` — не дефект, а честный сигнал «источник не назвался»: `db_role` (роль соединения) заполняется всегда и не зависит от кооперации кода. Помните про построчный `commit`: `set_config(..., is_local=true)` живёт только до ближайшего коммита, поэтому в цикле метку надо ставить перед каждой правкой (та же грабля описана в [task-audit.md](task-audit.md)).

## Структура таблицы

| Поле | Тип | Описание |
|---|---|---|
| `id` | bigserial | Primary key. |
| `result_id` | integer NOT NULL | `task_results.id` на момент изменения. **Без FK** — запись обязана пережить удаление результата (каскад от `users`/`tasks`), иначе история исчезнет вместе с тем, что она объясняет. |
| `task_id` / `user_id` | integer NULL | Снимки на момент изменения: за какое задание оценка и чья. |
| `action` | varchar(16) NOT NULL | `'UPDATE'` (правка оценки) или `'DELETE'` (результат удалён). |
| `old_score` / `new_score` | integer NULL | Балл до и после. У `DELETE` заполнен только `old_score` — «стало» не существует. |
| `old_is_correct` / `new_is_correct` | boolean NULL | Вердикт до и после (`NULL` — ручная проверка ещё не давала вердикта). |
| `changed_at` | timestamptz NOT NULL DEFAULT `clock_timestamp()` | Реальный момент записи строки (не `now()` — тот вернул бы начало транзакции, что важно при пакетной правке). |
| `changed_by` | text NULL | Метка источника, см. выше. |
| `db_role` | text NOT NULL | `current_user` соединения — заполняется всегда. |

Индексы: `idx_task_result_audit_result` (история одной работы), `idx_task_result_audit_changed_at` (недавние правки по всем работам), `idx_task_result_audit_user` (правки оценок ученика).

## Проверка, что журнал жив

```sql
SELECT tgname, tgenabled FROM pg_trigger
WHERE tgrelid = 'task_results'::regclass AND NOT tgisinternal;
-- ожидаются trg_task_result_audit_update и trg_task_result_audit_delete
-- со статусом 'O' (включён)
```

Регрессия закрыта тестами `tests/test_tsk803_task_result_audit.py` (в том числе сквозной: оценка через кабинет пишет `changed_by = 'user:<id>'` и прежний балл в уведомление).
