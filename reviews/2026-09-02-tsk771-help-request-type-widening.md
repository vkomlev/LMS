# tsk-771 — узкая копия перечисления «вид заявки» ломала два экрана преподавателя

**Дата:** 2026-09-02 · **Приоритет:** P0 · **Скилл:** `/fastapi-api-developer`

## Что было

`GET /api/v1/teacher/students/4543/tasks/4129/history` → 500. Дословно из прод-лога:

```
Unhandled exception at /api/v1/teacher/students/2/tasks/43/history:
1 validation error for TaskHistoryResponse
help_requests.0.request_type
  Input should be 'manual_help' or 'blocked_limit'
  [type=literal_error, input_value='individual_review', input_type=str]
```

Тип заявки `individual_review` завели в tsk-303 (лестница помощи). Общий
псевдоним `HelpRequestType` в `app/schemas/teacher_help_requests.py:15` знает все
три вида, `teacher_next_modes.py:17` тоже. Схема истории задания держала СВОЮ
копию литерала на двух значениях — и падала на сериализации ответа.

## Второе место того же дефекта — нашлось, было живым

`app/api/v1/teacher_progress.py:144`, поле `open_help_request_type`, такая же
своя копия. Запрос-источник (`manual_progress_service.py:1048`) берёт открытые
заявки ЛЮБОГО вида, фильтра по типу нет. Подтверждено на проде до правки:

```
progress course=112 -> 500   (Python для ЕГЭ — курс, где ученик учится)
progress course=156 -> 500
progress course=88 / 1467 / 1474 -> 200
```

Лог:

```
Unhandled exception at /api/v1/teacher/students/4543/progress:
1 validation error for ProgressTreeResponse
items.187.open_help_request_type
  Input should be 'manual_help' or 'blocked_limit' [input_value='individual_review']
```

То есть у преподавателя не открывались ДВА экрана: история по заданию и дерево
прогресса ученика по курсу — второе задача не знала.

## Масштаб (боевая база, read-only MCP)

| Вид | Заявок |
|---|---|
| `blocked_limit` | 127 |
| `manual_help` | 83 |
| `individual_review` | **2** |

Заявки разбора: id=133 (ученик 2, задание 43, закрыта, **06.08**) и id=260
(ученик 4543, задание 4129, открыта, 02.09). Уточнение к постановке: обе
заведены НЕ сегодня — история пары 2/43 падала почти месяц, просто на неё никто
не заходил. Дерево прогресса ломает только ОТКРЫТАЯ заявка (запрос берёт
`status='open'`), история — любая.

## Что изменено

| Файл | Правка |
|---|---|
| `app/schemas/task_history.py` | `request_type` и `status` → общие `HelpRequestType` / `HelpRequestStatus` вместо своих литералов |
| `app/api/v1/teacher_progress.py` | `open_help_request_type` → `Optional[HelpRequestType]` |
| `tests/test_tsk771_help_request_type_widening.py` | новый сторож класса дефекта (5 тестов) |

Литерал НЕ дописан третьим значением руками — иначе четвёртый вид заявки сломал
бы то же место тем же способом (прямое требование задачи).

## Сторож вместо перечисления руками

Тест не перечисляет три вида (такой список состарился бы вместе с кодом), а
проверяет два механических правила:

1. `HelpRequestType` совпадает с CHECK-ограничением `help_requests_request_type_check`
   в базе (источник истины читается из `pg_constraint`);
2. ни одно поле pydantic-моделей в `app/schemas/**` и `app/api/v1/**` с именем
   `*request_type*` не объявляет литерал УЖЕ, чем разрешает база (значения
   фильтров вроде `all` допускаются).

Проверено, что сторож краснеет: временный откат `task_history.py` к узкому
литералу → `2 failed, 3 passed`; после возврата → `5 passed`.

## Проверка соседних перечислений (пункт 3 задачи)

Сверены все CHECK-ограничения вида `IN (…)` в боевой базе (37 штук) с
`Literal[...]` в схемах и фактическими значениями в данных:

- `participant_status` (7 видов, включая `on_break`), `learning_gap_signal.status`,
  `ai_tutor_session.status` — в схемах отдаются как `str`, сузить не могут;
- `student_course_state.state` (4), `users.category` (5), `requirement_level` (3),
  `payment.*`, `pricing.*`, `presence.context`, `homework.source`,
  `curator.source`, `feedback_reports.*`, `assignment_rules.*` — литералы
  совпадают с базой один в один;
- `task_content.type`: литерал знает 8 типов, в базе живут 7 — литерал шире, это
  безопасно.

Другого места того же класса в LMS нет.

## Узкие копии у клиентов

- `SPW/lib/api-types.ts` — генерируется из `openapi.json`, расширится сам после
  регенерации;
- `SPW/components/task-history/TaskHistoryCard.tsx:369` — подпись заявки
  тернарником: всё, что не `manual_help`, подписывается «Разблокировка лимита».
  Не 500, но заявка на разбор была бы подписана неверно ровно на том экране,
  который чинится. Исправлено отдельным коммитом в SPW;
- `SPW/components/teacher/StudentProgress.tsx:538` — значок «заявка» только для
  `manual_help`: сужение намеренное (у `blocked_limit` свой сценарий), не дефект;
- `TG_LMS` — своих литералов вида заявки не держит (`str` в моделях контракта).

## Валидация

- `pytest tests/test_tsk771_help_request_type_widening.py` → 5 passed
- `pytest tests/test_task_history_tsk349.py tests/test_manual_progress_tsk297.py
  tests/test_teacher_help_requests_stage381.py tests/test_help_ladder_endpoints_tsk303.py
  + новый` → **84 passed**
- Прод до деплоя: history 4543/4129 → 500, history 2/43 → 500, progress 112 → 500
- Прод после деплоя (`5649405`, боевое API): history 4543/4129 → **200** (2 заявки,
  виды `individual_review` + `manual_help`), history 2/43 → **200**, progress
  ученика 4543 по курсам 112 / 156 / 88 / 1467 / 1474 → **200 все пять**;
  контрольные истории без таких заявок (2/4129, 4506/4129, 4543/4130) → 200
- Живая проверка глазами (профиль оператора, аккаунт 2 teacher/admin):
  `/teacher/students/4543` → курс «ЕГЭ по информатике» → клик по заданию
  «Двойное дописывание по чётности суммы» → панель истории открылась целиком
  (условие, эталон «86», попытки, обе заявки с диалогом, «Подсказок открыто: 4»),
  `GET .../tasks/4129/history` в сетевом логе → 200, «Internal server error» на
  экране нет. Тем же прогоном найдена неверная подпись заявки в SPW (см. выше),
  исправлена и перепроверена после выката SPW `8bff8e1`: «Индивидуальный разбор».

## Риски

Схема ответа расширяется (в поле может прийти третье значение) — для клиентов
это обратно совместимо: старые значения остаются валидными. Правок в боевой базе
не потребовалось — данные корректны, дефект был только в схеме ответа.
