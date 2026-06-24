# ADR-0002 — Триггеры автоматического назначения курсов (assignment trigger rules)

- **Статус:** Accepted (фундамент), полная автоматизация — следующий шаг
- **Дата:** 2026-06-24
- **Задача:** tsk-031 «Доп курсы для закрепления»
- **Связано:** ContentBackbone ADR-0042 (редизайн публикатора, граф курсов, `course_uid = wp:<slug>`); tsk-120 (LMS публикатор v2)

## Контекст

LMS умеет привязывать курсы ученику (`user_courses`), но не умеет делать это
**автоматически по условиям прохождения**. Нужны два сценария:

1. **Назначение по ответам.** По результату прохождения назначить мини-курс:
   - на пробном ученик выбрал трек «информатика» (ответ на SC-вопрос) → назначить
     «Вводная информатика»; «Python» → «Вводный Python»;
   - ученик провалил попытки по заданию/теме → назначить мини-курс повторения темы.
2. **Ручное назначение учителем в один клик** — кнопка «назначить мини-курс повторения».

Назначение строится **поверх графовой модели курсов** (multi-parent через
`course_parents`, зависимости через `course_dependencies`), которую наполняет
публикатор ContentBackbone. Курсы именуются устойчивым `course_uid` вида `wp:<slug>`.
Правила обязаны ссылаться на курсы по `course_uid`, а не по числовому `id`, чтобы
переживать пере-импорт/пере-публикацию.

### Существующая модель (AS-IS, релевантное)

| Объект | Где | Заметки |
|---|---|---|
| `courses` | `app/models/courses.py` | `course_uid` (unique, nullable), multi-parent, dependencies |
| `tasks` | `app/models/tasks.py` | `task_content` (JSONB, `type`: SC/MC/SA/SA_COM/TA), `solution_rules` |
| `attempts` | `app/models/attempts.py` | группа результатов; `course_id`, `finished_at` |
| `task_results` | `app/models/task_results.py` | `is_correct`, `answer_json`, `attempt_id`, `score` |
| `user_courses` | `app/models/user_courses.py` | PK `(user_id, course_id)` — **естественная идемпотентность зачисления**; `order_number` ставит триггер БД |
| `learning_events` | raw SQL | журнал учебных событий (паттерн дедупа через advisory-lock) |

### Точки интеграции (хуки)

- **Ответ на задачу:** `POST /attempts/{id}/answers` → `app/api/v1/attempts.py:402`
  после `TaskResultsService.create_from_check_result(...)`. Здесь известны
  `answer`, `check_result.is_correct`, `attempt_id`, `task_id`, `student_id`.
- **Завершение попытки:** `POST /attempts/{id}/finish` → `app/api/v1/attempts.py:620`
  после `AttemptsService.finish_attempt(...)`. Здесь доступны все результаты попытки.

## Решение

### 1. Две новые таблицы

#### `assignment_rule` — определения правил

| Поле | Тип | Назначение |
|---|---|---|
| `id` | serial PK | |
| `code` | text UNIQUE | устойчивый человекочитаемый код правила (`trial-track-python`) |
| `title` | text | описание для UI/админки |
| `trigger_event` | text CHECK | `answer_value` \| `task_failed` \| `course_failed` |
| `task_id` | int FK→tasks NULL | отслеживаемая задача (для `answer_value`/`task_failed`) |
| `course_id` | int FK→courses NULL | отслеживаемая тема=курс (для `course_failed`) |
| `condition` | jsonb NOT NULL `'{}'` | параметры условия (см. ниже) |
| `action_type` | text CHECK `'assign_course'` | пока единственное действие; точка расширения |
| `target_course_uid` | text NOT NULL | курс к назначению, по `course_uid` (`wp:<slug>`) |
| `refire_policy` | text CHECK | `once_per_student` (по умолч.) \| `every_time` |
| `is_active` | bool NOT NULL true | мягкое отключение |
| `created_at`/`updated_at` | timestamptz now() | |

**Формат `condition` по типам триггера:**
- `answer_value`: `{"option_id": "py"}` (для SC/MC) **или** `{"value": "Python"}` (для SA);
- `task_failed`: `{}` — срабатывает при `is_correct = false` (опц. `{"on_attempts_exhausted": true}` — только когда исчерпан лимит попыток);
- `course_failed`: `{"min_correct_ratio": 0.5}` — доля верных задач курса в попытке ниже порога.

#### `assignment_event` — журнал назначений (provenance + идемпотентность)

| Поле | Тип | Назначение |
|---|---|---|
| `id` | serial PK | |
| `student_id` | int FK→users CASCADE | кому назначено |
| `assigned_course_id` | int FK→courses CASCADE | что назначено (резолв `target_course_uid`) |
| `rule_id` | int FK→assignment_rule SET NULL | правило (NULL = ручное) |
| `source` | text CHECK | `auto_rule` \| `manual_teacher` |
| `assigned_by` | int FK→users SET NULL | учитель (для ручного) |
| `attempt_id` | int FK→attempts SET NULL | контекст срабатывания |
| `task_result_id` | int NULL | контекст срабатывания |
| `already_enrolled` | bool NOT NULL false | ученик уже был на курсе на момент события |
| `detail` | jsonb NULL | доп. данные (значение ответа, ratio и т.п.) |
| `created_at` | timestamptz now() | |

Индексы: `(rule_id, student_id)`, `(student_id)`, `(assignment_rule.task_id) WHERE NOT NULL`, `(trigger_event, is_active)`.

### 2. Идемпотентность (многоуровневая)

1. **Зачисление** — `user_courses` PK `(user_id, course_id)`: один курс не зачислится дважды никогда.
2. **Повторное срабатывание правила** — для `once_per_student` сервис проверяет наличие
   `assignment_event` с тем же `(rule_id, student_id)` до назначения. `every_time` пропускает проверку.
3. **Гонки** — `pg_advisory_xact_lock(student_id, course_id)` вокруг check-then-insert
   (паттерн из `learning_events_service`).

`assignment_event` пишется только при **фактическом новом** зачислении (или ручном
действии учителя), чтобы журнал не засорялся повторными кликами.

### 3. Сервис `assignment_rules_service`

- `assign_course_to_student(...)` — **идемпотентное ядро**: резолвит `course_uid`→`course_id`,
  берёт advisory-lock, проверяет `user_courses`, при отсутствии вставляет (триггер БД
  ставит `order_number`), пишет `assignment_event`. Используется и ручным эндпоинтом, и движком.
- `evaluate_rules_for_answer(...)` — после ответа: правила `answer_value`/`task_failed` по `task_id`.
- `evaluate_rules_for_attempt(...)` — после завершения попытки: правила `course_failed` по курсу попытки.

Движок реализован минимально, но рабоче (покрывает оба сценария оператора).
Каждая оценка обёрнута в **soft-fail** (как `_self_heal_student_role`): сбой движка
никогда не ломает учебный поток (ответ/завершение попытки).

### 4. Поведение по умолчанию

Таблица `assignment_rule` пуста после миграции → хуки выполняют **no-op**.
Поведение существующих эндпоинтов и тестов не меняется. Включение автоназначения =
добавление строк правил (через будущую админку/seed), без правок кода.

### 5. Ручное назначение учителем

`POST /api/v1/teacher/students/{student_id}/assignments` — teacher-only.
Тело: `{course_id?: int, course_uid?: str, reason?: str}` (одно из id/uid обязательно).
Идемпотентно: повторный вызов не ошибается, возвращает `already_enrolled=true`.
`source='manual_teacher'`, `assigned_by` = id учителя.

**Иерархия доступа** (`_ensure_can_assign`):
- сервисный токен (бот/интеграция) — полный доступ;
- роль `admin`/`methodist` — полный доступ (любой ученик);
- роль `teacher` — только к своим ученикам (связь `student_teacher_links`); иначе 403;
- без подходящей роли — 403.

## Альтернативы (отклонены)

- **Триггеры/функции в БД** вместо сервис-слоя — нагляднее в коде приложения, проще тестировать, не плодит скрытую логику в PL/pgSQL.
- **Хранить `target_course_id`** вместо `course_uid` — ломается при пере-импорте курсов; `course_uid` стабилен и совпадает с публикатором.
- **Назначать без журнала, опираясь только на `user_courses`** — теряется provenance (каким правилом/кем назначено) и контроль `once_per_student` для уже отчисленных.

## Последствия

- **+** Расширяемо: новые `trigger_event`/`action_type` — добавление веток, не схемы.
- **+** Безопасно: feature-gated пустой таблицей, soft-fail хуки.
- **+** Cross-project-совместимо: правила ссылаются на `course_uid` публикатора.
- **−** Движок оценки правил — синхронный в request-цикле (приемлемо: правил на задачу мало, soft-fail). При росте — вынести в очередь событий (`learning_events` + worker).
- **Follow-up:** UI-кнопка в SPW/teacher API; админка правил; перенос оценки в асинхронный воркер; правило `course_failed` по накопленной истории (не только в рамках одной попытки).
