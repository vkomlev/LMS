# ТЗ tsk-297 — Штатный механизм правки прогресса ученика

> Дата: 2026-07-20. Задача: `tsk-297` (P0, Волна 0). Проекты: LMS (backend) + SPW (портал преподавателя).
> Опора: `docs/specs/2026-07-19-arch-teacher-portal.md` (tsk-298, портал на проде).

## 1. Суть

Не миграция из внешнего источника (его нет), а **функция продукта**: преподаватель/админ в любой
момент штатно правит прогресс ученика из системы. Цель для ученика — придя с наработками,
продолжать со своего места, а не с нуля.

## 2. Решение по модели данных (ключевая развилка)

### Рассмотренные варианты

| Вариант | Вердикт | Обоснование |
|---|---|---|
| `task_results` с `attempt_id = NULL` | **Отклонён** | `compute_task_state` (`learning_engine_service.py:311`) делает `INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL`. Результат без попытки движок не увидит — зачёт не сработает. В проде таких строк 0. |
| Отдельная таблица зачётов | **Отклонён** | «Пройдено» вычисляется в 6 местах: `_first_incomplete_task`, `compute_task_state`, `compute_course_state` (UNION), `_COURSES_PROGRESS_SQL`, `_SYLLABUS_TASKS_SQL`, `_compute_syllabus_task_status`. Правка каждого на P0 — неоправданный регрессионный риск. |
| **Синтетическая попытка** | **ПРИНЯТ** | Ноль изменений в движке; согласованность («зачтённое = пройденное») получается конструктивно, а не поддержкой в 6 местах. |

### Принятая модель — задания

Зачёт = **синтетическая попытка + результат** :

```
attempts:      user_id=<student>, course_id=<курс задания>, root_course_id=NULL,
               source_system='manual_teacher', meta={'granted_by':<teacher>,'task_ids':[<task>]}
task_results:  user_id, task_id, attempt_id=<синтетическая>, score=max_score, max_score=<из task>,
               is_correct=true, checked_at=now(), checked_by=<teacher>,
               source_system='manual_teacher', metrics={'manual_grant': true}
```

Почему именно так:

1. **`root_course_id = NULL`** — документированная семантика модели (`attempts.py:79-84`): «путь неизвестен —
   такая попытка не расходует лимит ни в одном корне». Счёт попыток фильтрует
   `a.root_course_id = :root_course_id`, NULL не совпадёт → **зачёт не съедает попытку ученика**.
   В проде уже 7 таких попыток — путь штатный, не выдуманный.
2. **`source_system='manual_teacher'`** — провенанс без миграции. Поле `String(50)` существует на обеих
   таблицах; в проде 3 значения (`spw_web`, `learning_api`, `lms`), схема free-form. Отчёт «реально решено»
   = `WHERE source_system <> 'manual_teacher'`.
3. **`checked_at`/`checked_by` заполняются** — иначе зачтённое задание ручного типа (`SA_COM`/`TA`) упадёт
   в очередь проверки преподавателя (предикат очереди — `tr.checked_at IS NULL`).
4. **`score = max_score`** → `ratio = 1.0 >= PASS_THRESHOLD_RATIO (0.5)` → `compute_task_state` вернёт `PASSED`
   → `_first_incomplete_task` пропустит задание → **next-item не выдаст заново**. Требуемая согласованность.

### Принятая модель — материалы

`student_material_progress` (`status='completed'`) + **новая колонка `source`** — провенанса там нет
(проверено: колонки `student_id, material_id, status, completed_at, skipped_at`).

Миграция: `source VARCHAR(32) NOT NULL DEFAULT 'system'`, CHECK `source IN ('system','manual_teacher')`.
Дефолт покрывает 638 существующих строк без бэкфилла.

### Обратимость (снятие отметки)

- **Задание:** `UPDATE attempts SET cancelled_at=now(), cancel_reason='manual_progress_revoked'`
  на синтетической попытке. Движок отсекает её тем же `a.cancelled_at IS NULL` → задание возвращается
  к состоянию, которое дают его **реальные** попытки: `OPEN`, если ученик задание не решал, иначе
  `IN_PROGRESS` / `FAILED` / `BLOCKED_LIMIT` (реальные попытки не трогаются).
  **Строки не удаляются** — история правок сохраняется. В проде 97 отменённых попыток, путь обкатан.
- **Материал:** `DELETE` строки, только если `source='manual_teacher'` (реальное прохождение ученика не трогаем).

### Инварианты, которые не ломаем

- **tsk-264** (попытки по паре курс+задание): синтетическая попытка с `root_course_id=NULL` не участвует
  в счёте лимита ни в одном корне; прогресс `PASSED` остаётся общим по корням — как и задумано.
- **tsk-111** (обязательность/пропуск): `student_task_progress` не трогаем вовсе (CHECK там `status = 'skipped'`,
  равенство; в проде таблица пуста). Зачёт и пропуск — разные сущности, не смешиваем.
- Массовые операции идут по `requirement_level IN ('required','skippable')` — тот же фильтр, что у движка.

## 3. Контракт API (LMS)

Базовый префикс: `/api/v1/teacher/students/{student_id}/progress`.

| Метод | Путь | Смысл |
|---|---|---|
| `GET` | `?course_id={id}` | Прогресс ученика по дереву курса для преподавателя: элементы + статус + флаг `manual` |
| `POST` | `/tasks/{task_id}` | Зачесть задание |
| `DELETE` | `/tasks/{task_id}` | Снять зачёт задания |
| `POST` | `/materials/{material_id}` | Отметить материал пройденным |
| `DELETE` | `/materials/{material_id}` | Снять отметку материала |
| `POST` | `/courses/{course_id}` | Массово зачесть всё дерево узла |
| `DELETE` | `/courses/{course_id}` | Массово снять зачёты в дереве узла |

Тело POST (опционально): `{"comment": "<причина, до 500 симв>"}`.

Ответ единичных операций:
```json
{"student_id": 1, "item_type": "task", "item_id": 2, "granted": true, "already": false, "source": "manual_teacher"}
```
Ответ массовых:
```json
{"student_id": 1, "course_id": 3, "tasks_affected": 5, "materials_affected": 7, "skipped_already": 2}
```

Ответ `GET`:
```json
{"student_id": 1, "course_id": 3,
 "items": [{"item_type":"task","item_id":2,"title":"...","status":"PASSED","manual":true,
            "granted_by":10,"granted_at":"2026-07-20T10:00:00Z"}]}
```

**Идемпотентность:** повторный POST не создаёт вторую попытку — возвращает `already: true`.
Реализуется под `pg_advisory_xact_lock` по паре `(student_id, task_id)` — как в `set_task_skipped`.

**ACL:** `require_role("teacher","methodist","admin")` + scoped-проверка:
teacher допускается, если ученик в `student_teacher_links` ИЛИ курс попадает под `teacher_course_acl`
(рекурсия вверх по `course_parents`); `methodist`/`admin` — bypass. Паттерн — как
`teacher_can_override_limit` (`teacher_queue_service.py:1102`).

**Аудит:** каждая операция → `audit_service.log_event` с типами
`teacher.progress.granted` / `teacher.progress.revoked`,
`details = {student_id, item_type, item_id, course_id, bulk, affected, comment}`.

## 4. SPW (портал преподавателя)

Карточки ученика **сейчас нет** — ростер (`/teacher/students`) плоский список без ссылок.
Создаётся: маршрут `app/(teacher)/teacher/students/[student_id]/page.tsx`, компонент
`components/teacher/StudentProgress.tsx`, хуки в `lib/teacher/use-student-progress.ts`.

- Дерево курса ученика со статусами; переиспользовать готовый `components/course/StatusBadge.tsx`
  (покрывает 9 статусов, в teacher-зоне ещё ни разу не подключён).
- Бейдж «зачтено вручную» на элементах с `manual: true`.
- Кнопки: зачесть/снять на элементе; зачесть/снять на узле темы (массово, с подтверждением).
- Ссылка на карточку — из `StudentCard` в `RosterList.tsx`.
- Guard наследуется от `app/(teacher)/layout.tsx` автоматически.

## 5. Гейты

Тесты (LMS pytest + SPW vitest) → `/review-gate` → review-артефакты в `reviews/` →
cross-project mirror в ContentBackbone (новый эндпоинт + миграция) → деплой
(LMS `deploy/vps`, SPW `deploy/vps/deploy.sh` под пользователем `app`) → живой прод-прогон.
