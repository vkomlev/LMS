# План: API-контракт периодного дашборда ученика (tsk-494)

**Дата:** 2026-08-01 · **Задача:** [[tsk-494]] (P1, LMS) · зависимая: [[tsk-478]] (кабинет родителя)
**Skill:** `/change-plan-architect`

## Целевая возможность

Один GET-эндпоинт, отдающий периодный срез по ученику (курсы+прогресс+прогноз,
итог за период, посещение, ДЗ между занятиями, произвольный `[from, to]`),
пригодный как основа и для будущего кабинета родителя (tsk-478, read-only,
минимизированные данные), так и для текущих привилегированных потребителей
(teacher/methodist/admin). Контракт **auth-agnostic** по `student_id` — ACL
навешивается по вызывающему на уровне роута, эта задача его не проектирует.

## Текущее состояние

- `app/services/teacher_lesson_summary_service.py::_load_homework_window(db, student_id, window_from, window_to)`
  УЖЕ принимает произвольные `window_from`/`window_to` (не привязана к occurrence
  жёстко — просто единственный вызывающий (`get_occurrence_summary`) передаёт
  `window_from` = конец предыдущего occurrence). Обобщать SQL не нужно —
  нужно **сделать функцию переиспользуемой** (снять `_`-приватность или
  дать публичную обёртку) и звать её напрямую с произвольным периодом.
- `_load_course_progress_and_blocked` — % прогресса/текущая позиция/blocked
  через `manual_progress_service.get_student_progress` — переиспользуется как
  есть, без изменений.
- `_load_help_requests` — отдаёт ТЕКСТ (`message`/`resolution_comment`) —
  для дашборда нужны только счётчики, эта функция **не переиспользуется**,
  нужен отдельный лёгкий COUNT-запрос.
- Нигде нет: агрегации "в часы занятий", прогноза окончания курса,
  запроса "незакрытые пропуски за период".
- `LessonOccurrenceParticipant.status` — CHECK-constraint, значения
  `scheduled|confirmed|declined|rescheduled|no_show|completed` **взаимно
  исключающие**. Проверено (`lesson_occurrence_service.reschedule_occurrence`,
  app/services/lesson_occurrence_service.py:463): перенос переводит СТАРУЮ
  строку участника в `status='rescheduled'` — то есть строка со статусом
  `no_show`/`declined` **по построению** ещё не перенесена (иначе статус был
  бы уже `rescheduled`). Отдельная проверка цепочки `rescheduled_to_occurrence_id`
  не нужна — упрощение относительно черновой формулировки в tsk-494.

## Карта влияния

| Компонент | Изменение |
|---|---|
| `app/services/teacher_lesson_summary_service.py` | Убрать `_`-префикс у `_load_homework_window` (или тонкая публичная обёртка `load_homework_window`) — **сигнатура и поведение не меняются**, ноль риска для существующих вызывающих (tsk-022/tsk-410) |
| `app/services/student_dashboard_service.py` (новый) | Новый сервис: агрегация "в часы занятий", прогноз окончания, посещение за период, сборка ответа из 5 блоков |
| `app/schemas/student_dashboard.py` (новый) | `StudentDashboardRead` + вложенные схемы — без полей текста заявок/`solution_rules` на уровне контракта |
| `app/api/v1/student_dashboard.py` (новый) или расширение `app/api/v1/teacher_lesson_occurrences.py` | `GET /students/{student_id}/dashboard?from=&to=` — роут пока под тем же гейтом, что и `/teacher/lesson-occurrences/summary` (self-or-service, методист/админ) |
| Тесты `tests/test_tsk494_student_dashboard.py` (новый) | См. «План проверки» |

Никаких миграций БД в этой задаче — только чтение существующих таблиц.

## Пробелы и недостающие ресурсы

Блокирующих пробелов нет — весь нужный сырьевой материал (occurrence,
participant, task_results, student_material_progress, help_requests) уже
есть в схеме и подтверждён `student_teacher_links`-паттерном для будущего
ACL. Один открытый технический выбор ниже (не блокер, решение зафиксировано
внутри плана):

### Дизайн агрегации "в часы занятий" vs "между занятиями" (без задвоения)

`_load_homework_window(window_from=from, window_to=to)` даёt **итог за
период целиком** (совпадает с "ИТОГ" п.2 по составу метрик). Отдельно
нужна **только** агрегация "в часы занятий" — тем же составом метрик
(`tasks_completed`/`theory_completed`/`first_try`/`help_requested`), но
ограниченная объединением окон `[occurrence.scheduled_at, +duration_minutes]`
всех occurrence ученика, пересекающихся с `[from, to]`.

Новый SQL в `student_dashboard_service.py` — та же форма CTE, что в
`_load_homework_window`, но с добавленным `EXISTS`-условием против
`lesson_occurrence` JOIN `lesson_occurrence_participant` (ученик — участник
этого occurrence, событие по времени попадает в его окно):

```sql
EXISTS (
    SELECT 1 FROM lesson_occurrence lo
    JOIN lesson_occurrence_participant lop
        ON lop.occurrence_id = lo.id AND lop.student_id = :student_id
    WHERE tr.submitted_at BETWEEN lo.scheduled_at
        AND lo.scheduled_at + make_interval(mins => lo.duration_minutes)
)
```

**Между занятиями (п.4) = ИТОГ (from/to) − В часы занятий, пометрично**
(арифметика в Python, не третий SQL-запрос). Это гарантирует отсутствие
задвоения и потери событий по построению (в часы занятий ⊆ итог за период
по времени), а не за счёт согласования двух независимых временных фильтров.
`first_try` считается по факту отсутствия более раннего результата **во всей
истории**, не только в окне — это уже так в существующей функции, инвариант
не ломается при вычитании.

### Прогноз окончания курса

Простая эвристика (подтверждено оператором: последние N недель, N=4 по
умолчанию, вынести в `Settings` как `student_forecast_pace_weeks: int = 4`
для последующей настройки без релиза):

1. `done_last_n_weeks` = `tasks_completed + theory_completed` за окно
   `[now − N недель, now]` тем же `_load_homework_window`-запросом (без
   привязки к occurrence — просто период).
2. `remaining` = `total − done` по дереву курса, уже посчитано в
   `_load_course_progress_and_blocked` (countable items минус done).
3. `pace_per_week = done_last_n_weeks / N`.
4. Если `pace_per_week == 0` **или** `remaining == 0` → `forecast_date = None`
   (не делить на ноль; курс завершён → тоже `None`, отдельный флаг
   `is_completed` на уровне ответа, если `remaining == 0`).
5. Иначе `weeks_left = remaining / pace_per_week`, `forecast_date = now +
   weeks_left * 7 дней`. Округление до дня, не до часа — это эвристика,
   не обязательство.

### Незакрытые пропуски за период (п.3)

```sql
SELECT lop.status, COUNT(*) FROM lesson_occurrence_participant lop
JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
WHERE lop.student_id = :student_id
  AND lo.scheduled_at BETWEEN :from AND :to
  AND lop.status IN ('no_show', 'declined', 'rescheduled')
GROUP BY lop.status
```

`missed_total = no_show + declined + rescheduled`, `missed_unresolved =
no_show + declined` (см. инвариант статуса выше — `rescheduled` уже закрыт
по построению).

## Решение по дублированию

`_load_help_requests` НЕ переиспользуется для дашборда (отдаёт текст —
запрещено принципом минимизации). Вместо неё — прямой `COUNT(*) FILTER
(WHERE status='open')`/`COUNT(*) FILTER (WHERE status='closed' AND closed_at
BETWEEN :from AND :to)` по `help_requests` в новом сервисе — три строки SQL,
не стоит городить общий слой ради этого; дублирование минимально и с разной
целью (одна ветка — текст для учителя, другая — счётчик для дашборда).

## Схема ответа (контракт минимизации — на уровне Pydantic, не постфильтром)

```python
class StudentDashboardCourseRead(BaseModel):
    course_id: int
    title: str
    percent_complete: int
    current_section_title: Optional[str]
    current_item_title: Optional[str]
    forecast_completion_date: Optional[date]  # None = нет темпа или уже завершён
    is_completed: bool

class StudentDashboardMetricsRead(BaseModel):
    tasks_completed: int
    theory_completed: int
    first_try: int
    help_requested_count: int  # ТОЛЬКО счётчик, без текста

class StudentDashboardAttendanceRead(BaseModel):
    total_occurrences: int
    missed_total: int
    missed_unresolved: int

class StudentDashboardRead(BaseModel):
    student_id: int
    period_from: datetime
    period_to: datetime
    courses: list[StudentDashboardCourseRead]
    period_total: StudentDashboardMetricsRead      # п.2 ИТОГ
    in_class_hours: StudentDashboardMetricsRead     # часть п.2 (новая агрегация)
    between_lessons: StudentDashboardMetricsRead    # п.4 (= period_total − in_class_hours)
    attendance: StudentDashboardAttendanceRead      # п.3
```

Полей `solution_rules`, `help_requests[].message/resolution_comment`,
`blocked_tasks` с деталями задания текстом — в контракте нет вообще (не
"добавили и скрыли", а не добавляли). Если привилегированному кабинету
(teacher/methodist) когда-то понадобится текст заявок поверх этого дашборда
— отдельный эндпоинт/поле по аналогии с `_task_read_for(privileged=...)` из
tsk-460, не расширение этой схемы.

## Роут

```
GET /students/{student_id}/dashboard?from=<date>&to=<date>
```

Гейт в этой задаче: тот же паттерн `_ensure_self_or_service`-подобной
проверки, что `teacher_lesson_occurrences.py` — сервисный ключ ИЛИ роль
`teacher`/`methodist`/`admin` (без identity-ветки "это мой ученик", т.к.
здесь нет отдельного "владельца" — привязка teacher↔student не то же самое,
что access control для дашборда; уточнить финальный ACL при code review,
если появится требование ограничить учителя только своими учениками).
**Родительский ACL — НЕ в этой задаче**, добавляется в tsk-478 отдельным
Depends/проверкой поверх того же сервиса.

`from`/`to` — обязательные `date`/`datetime` query-параметры (без дефолта:
явный период безопаснее скрытого "последние 30 дней", решает SPW при
вызове). Валидация `to > from`, иначе 422.

## Этапы внедрения

| Шаг | Предусловие | Skill-исполнитель | Проверка готовности |
|---|---|---|---|
| 1. Публичная обёртка `_load_homework_window` | нет | `/fastapi-api-developer` | Существующие тесты `teacher_lesson_summary_service` зелёные без изменений |
| 2. `student_dashboard_service.py`: агрегация "в часы занятий" + арифметика "между занятиями" | Шаг 1 | `/fastapi-api-developer` | Юнит-тест: сумма in_class_hours+between_lessons == period_total по каждой метрике |
| 3. Прогноз окончания + `Settings.student_forecast_pace_weeks` | нет | `/fastapi-api-developer` | Тест: pace=0 → None, remaining=0 → is_completed=True, forecast_date=None |
| 4. Посещение за период (missed_total/unresolved) | нет | `/fastapi-api-developer` | Тест на всех 3 статусах + за границей периода |
| 5. Схема `StudentDashboardRead` + роут `GET /students/{id}/dashboard` | Шаги 1-4 | `/fastapi-api-developer` | OpenAPI регенерирован, `curl` smoke на dev |
| 6. Тесты полного набора (см. План проверки) | Шаг 5 | `/fastapi-api-developer` | `pytest tests/test_tsk494_student_dashboard.py -q` зелёный |
| 7. Живая проверка на проде под реальным учеником | Шаг 6 | `/fastapi-api-developer` (MCP `learn_prod_db` read-only) | Ручная сверка агрегатов по факту БД совпадает с ответом API |
| 8. Review-gate | Шаг 7 | `/review-gate` | PASS перед интеграцией в main |

### Маршрутизация по skills

| Фаза | Под-задача | Главный исполнитель | Ревью / контроль | Примечания |
|---|---|---|---|---|
| Реализация | Обёртка + новая агрегация + прогноз + посещение + роут + тесты | `/fastapi-api-developer` | `/techlead-code-reviewer` (по требованию CLAUDE.md для date/time логики — прогноз использует date-арифметику, обязателен Date/Type Guard Evidence) | Один сквозной инкремент, не дробить — все части одного эндпоинта |
| DB-проверка | Живая сверка агрегатов на проде | `/fastapi-api-developer` (MCP `learn_prod_db`, read-only) | — | Прод только на чтение, применимо без `/db-check` (миграций нет) |
| Pre-merge | Итоговый гейт | `/review-gate` | — | Обязателен перед main (CLAUDE.md) |

**Cross-cutting skills:** `/encoding-guard` не требуется (новый код, не bulk-правка существующих markdown); `/context-auditor` — точечно перед review-gate, сверить итоговый контракт с 5 пунктами состава из tsk-494 (риск: молча потерять минимизацию данных при рефакторинге в процессе реализации).

## План проверки

1. `period_total == in_class_hours + between_lessons` по каждой метрике
   (свойство, не хардкод конкретных чисел) — на синтетических данных с
   известными task_results внутри и вне occurrence-окон.
2. Пустой период (`from == to` или период без активности) → все метрики 0,
   `forecast_completion_date` не падает.
3. Период на границе occurrence (событие ровно в `scheduled_at` или ровно в
   `scheduled_at + duration_minutes`) — граница включена (`BETWEEN`
   инклюзивен с обеих сторон, проверить явно тестом).
4. `first_try` не меняется от вычитания (успех вне окна "в часы" не должен
   портить флаг "с первого раза" в "между занятиями" и наоборот) — тест на
   задание, сданное дважды: 1-й раз мимо occurrence, 2-й раз в occurrence.
5. Прогноз: pace=0 (нет активности за N недель) → `None`; remaining=0
   (курс пройден) → `is_completed=True`, `forecast_date=None`; нормальный
   случай — конкретная дата, тест на детерминированных input (без
   `datetime.now()` в самом тесте — фиксированный `now` через параметр/mock).
6. Посещение: по одному ученику с occurrence во всех статусах
   (`scheduled`/`confirmed`/`declined`/`rescheduled`/`no_show`/`completed`)
   — `missed_total`/`missed_unresolved` считают только нужные статусы,
   `completed`/`scheduled`/`confirmed` не попадают ни в один счётчик.
7. Минимизация: сериализованный JSON ответа не содержит подстрок
   `solution_rules`, `message`, `resolution_comment` — простой assert на
   сырой ответ эндпоинта, а не только на форму Pydantic-схемы (ловит
   регресс, если кто-то потом добавит поле мимо схемы через `.dict()`
   вручную).
8. Regression: существующие тесты `teacher_lesson_summary_service`
   (`get_occurrence_summary`) — без изменений в поведении.

## Риски и меры снижения

- **Риск:** Приватная функция `_load_homework_window` де-факто становится
  публичным API модуля — если её сигнатура позже изменится ради
  `teacher_lesson_summary_service`, тихо сломает дашборд.
  **Мера:** явный docstring-комментарий "используется и `student_dashboard_service`,
  менять сигнатуру с оглядкой", либо (предпочтительнее при реализации) —
  вынести саму SQL-функцию в отдельный низкоуровневый модуль
  (`app/services/_homework_window_sql.py` или аналог), от которого зависят
  оба сервиса. Решение — на усмотрение `/fastapi-api-developer` при
  реализации, не блокер плана.
- **Риск:** `date`/`datetime` в query-параметрах — naive vs timezone-aware
  (CLAUDE.md Date/Time Safety). **Мера:** явный type-guard на входе роута,
  negative-тесты (naive datetime, `None` в обязательном параметре — 422).
- **Риск:** N=4 недели зашито числом в нескольких местах.
  **Мера:** `Settings.student_forecast_pace_weeks`, один источник.

## Критерии Go/No-Go

**Go:** все тесты из «Плана проверки» зелёные, живая сверка на проде (Шаг 7)
совпадает с фактом БД, `/review-gate` — PASS, ответ не содержит
запрещённых полей (проверено сериализацией, не только схемой).
**No-Go:** любое расхождение агрегатов на реальных данных прод-ученика с
ручным SQL-подсчётом — блокирует до устранения (не деплоить "почти
верное").

## Решение по UX-сложности

Не применимо — это чисто backend-контракт без пользовательского интерфейса;
UX-гейт актуален для tsk-478 (UI кабинета родителя), не для этой задачи.

## Допущения и открытые вопросы

- ACL финального эндпоинта для teacher (свои/все ученики) — решается при
  code review реализации, не блокирует эту задачу (родительский ACL точно
  отдельно, в tsk-478).
- Размещение низкоуровневой SQL-функции (общий модуль vs публичная обёртка
  в `teacher_lesson_summary_service`) — на усмотрение исполнителя, оба
  варианта не меняют внешний контракт.
