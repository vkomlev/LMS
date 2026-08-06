# Ревью — tsk-231, Фаза 1: LMS backend (доназначение + обогащение контракта)

**Skill:** `/fastapi-api-developer` (реализация) + `/techlead-code-reviewer` (парное ревью race condition, обязательно по skill-routing-standard §5)
**Дата:** 2026-08-06 · **План:** [docs/specs/2026-08-06-plan-tsk231-mini-kursy-blokirovka.md](../docs/specs/2026-08-06-plan-tsk231-mini-kursy-blokirovka.md), Фаза 1

## Контекст

Разведка кода (см. план) показала, что механизм блокировки через `course_dependencies` уже полностью реализован и работает в проде, но с блокирующим пробелом: `CourseDependenciesService.add_dependency`/`bulk_add_dependencies` не доназначал `required_course_id` ученикам, УЖЕ зачисленным на `course_id` — замок без выхода (прецедент tsk-523/tsk-261, только на другом пути записи). Плюс API отдавал только числовой ID курса-зависимости, клиенты не могли показать название.

## Изменения

| Файл | Суть |
|---|---|
| `app/services/course_dependencies_service.py` | Новый `_enroll_existing_students` (вызывается из `add_dependency`/`bulk_add_dependencies`) — доназначает required-курс уже зачисленным ученикам через существующий `course_dependencies_enrollment_service.ensure_dependencies_assigned` (tsk-261). Новый `count_affected_students` — превью для confirm-диалога методиста (Фаза 3). |
| `app/services/learning_engine_service.py` | `resolve_next_item` при `blocked_dependency` дополнительно кладёт `dependency_course_title`/`dependency_course_uid` (уже загруженный ORM-объект `req_course`, доп. запроса не требуется). |
| `app/services/me_service.py` | `_BLOCKED_COURSES_SQL` обогащена JOIN на `courses` — новое поле `blocked_dependencies` в ответе `get_syllabus_states`. `blocked_courses` (старое поле) не изменилось по составу — обратная совместимость. |
| `app/api/v1/course_dependencies.py` | Новый read-only `GET /{required_course_id}/impact` — превью числа затрагиваемых учеников ДО добавления зависимости. |
| `app/schemas/{courses,learning_api,learning_engine,me}.py` | Аддитивные поля/новая схема `CourseDependencyImpact`, `BlockedDependency`. Ничего не удалено и не переименовано. |
| `tests/test_tsk231_phase1_backend.py` | 8 новых тестов (доназначение, идемпотентность, пропуск некорневых, изоляция от посторонних студентов, bulk-путь, impact-превью, обогащение next-item/syllabus-states). |

## Парное ревью race condition (`/techlead-code-reviewer`)

### Decision: **PASS**

### Review Horizon: `microstep implemented` (Фаза 1 самодостаточна и готова к интеграции; полная функция tsk-231 требует ещё фаз 3-5 клиентов)

### Проверенные риски

1. **Конкурентные `add_dependency` на одну пару (course_id, required_course_id).** `repo.add_dependency` вставляет через `INSERT ... ON CONFLICT DO NOTHING` — атомарно на уровне БД, дублей/исключений при гонке не будет. `ensure_dependencies_assigned` (переиспользуется из tsk-261) для самого зачисления использует тот же паттерн (`ON CONFLICT (user_id, course_id) DO NOTHING RETURNING`) — идемпотентно без отдельного advisory-lock. Подтверждено тестом `test_add_dependency_enrollment_is_idempotent`. **Не найдено проблем.**
2. **Транзакционные границы.** `repo.add_dependency` коммитит сам (существующий код, не менялся) — сама зависимость становится видимой раньше, чем завершается `backfill_dependency_state`/`_enroll_existing_students`. Если исключение прервёт цикл доназначения на каком-то студенте — уже обработанные до него доназначения (в той же незакоммиченной части транзакции) откатятся, а зависимость останется. **Остаточный риск (не блокирует PASS):** отсутствующее зачисление, в отличие от кеша `student_course_state` (чинит фоновый тик `course_dependency_state_cron_service`), само себя не лечит. Смягчение: путь идемпотентен — повторный вызов `add_dependency`/UI-кнопки закрывает пробел вручную; сценарий требует одновременно (а) исключения посреди цикла И (б) отсутствия повторной попытки методистом. Задокументировано в docstring `_enroll_existing_students`. Follow-up (не блокирующий): если на практике появятся курсы с сотнями учеников и заметны частичные сбои — обернуть цикл в savepoint (`db.begin_nested()`) по образцу `me/attribute-guest` (Y-5, savepoint-паттерн уже есть в кодовой базе).
3. **Производительность цикла.** Последовательный `await` на каждого активного студента дерева — та же схема, что и существующий `backfill_dependency_state` (tsk-541, в проде без инцидентов). Не новый паттерн, не блокирует.
4. **SQL `_BLOCKED_COURSES_SQL`.** `JOIN courses rc ON rc.id = cd.required_course_id` не меняет набор строк: `required_course_id` защищён `FOREIGN KEY ... ON DELETE CASCADE` — осиротевших `course_dependencies` не бывает, INNER JOIN не теряет строк относительно старого запроса без JOIN.
5. **Обратная совместимость `blocked_courses`.** Состав (distinct course_id) не изменился — подтверждено тестом и Python-side dedup (`dict.fromkeys`). Порядок теоретически может отличаться, но все известные потребители (SPW `use-course-syllabus.ts`) оборачивают в `Set()` — порядок не важен.

### Blocking Findings
Нет.

### Non-Blocking Findings (S3)
- Docstring `_enroll_existing_students` изначально ошибочно называл механизм "advisory-lock" (в реальности — атомарность `ON CONFLICT DO NOTHING`, без отдельного lock) — исправлено в рамках этого же ревью, до PASS.
- Follow-up (backlog, не для этой фазы): savepoint-обёртка цикла `_enroll_existing_students`, если частичные сбои проявятся на практике на курсах с большим числом учеников.

## Validation Results

| Критерий | Результат |
|---|---|
| Новые тесты (`tests/test_tsk231_phase1_backend.py`) | ✅ 8 passed |
| Регрессия tsk-261/tsk-541/tsk-545/y6.2 (`test_tsk261_dependency_autoassign.py`, `test_tsk541_subcourse_dependency_state_backfill.py`, `test_tsk545_next_item_subcourse_dependency_sync.py`, `test_y62_syllabus_states.py`) | ✅ 33 passed, 1 skipped |
| Полный `pytest` LMS | ✅ **1802 passed, 11 skipped**, 0 failed |
| `docs/openapi.json` перегенерирован | ✅ 275 эндпоинтов (+1: `GET /.../dependencies/{id}/impact`) |
| API contract guard (hardcoded URLs) | ✅ 0 совпадений в затронутых файлах (аддитивные read-only поля, новых внешних URL нет) |
| IDOR sweep нового эндпоинта | ✅ `GET /{required_course_id}/impact` под тем же `_WRITE_GATE` (`methodist`/`admin`), что и остальная запись зависимостей — не открывает данные шире существующего гейта |

## Команды валидации

```bash
cd D:/Work/LMS
.venv/Scripts/python.exe -m pytest tests/test_tsk231_phase1_backend.py -v
.venv/Scripts/python.exe -m pytest tests/test_tsk261_dependency_autoassign.py tests/test_tsk541_subcourse_dependency_state_backfill.py tests/test_tsk545_next_item_subcourse_dependency_sync.py tests/test_y62_syllabus_states.py -q
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/export_openapi.py
```

## Residual Risks / Follow-ups

1. Частичный сбой цикла доназначения (см. п.2 выше) — идемпотентно восстановим вручную, savepoint-обёртка в backlog.
2. Фаза 1 не меняет клиентов (SPW/TG_LMS) — они пока не потребляют `dependency_course_title`/`blocked_dependencies`/`impact`. До Фазы 3/4 UX блокировки для ученика не улучшится (по-прежнему покажет старый общий переход/голый ID), несмотря на то что backend уже готов.
3. Обнаружено (не относится к tsk-231): во время реализации Фазы 1 в этом же рабочем дереве параллельно закоммитил изменения другой чип (HEAD ушёл с `8b7552b` на `22eb273`, tsk-324/tsk-431) — `docs/openapi.json` оказался частично зарегенерирован их pre-commit хуком поверх моего незакоммиченного WIP. Коммит Фазы 1 будет сделан pathspec'ом строго по своим файлам (см. `~/.claude/CLAUDE.md` ADR-0008), без `git add -A`.

## Claude Skills Improvement Entries
Нет — работа `/fastapi-api-developer` и `/change-plan-architect` соответствовала контракту, дефектов skill не выявлено.
