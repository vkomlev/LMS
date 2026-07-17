# tsk-272 — приём ответа не проверяет запись ученика на курс (диагностика)

**Дата:** 2026-07-17
**Задача:** `D:\Work\Root\tasks\tsk-272-...md`
**Скилл:** `/fastapi-api-developer` (read-only диагностика; фикс не реализован — ждёт решения оператора)
**Источник гипотезы:** ревью tsk-269, раздел Risks/Follow-ups п.3

## Гипотеза

Ученик без единой активной записи `user_courses (is_active = true)` может успешно
отправлять ответы через `POST /api/v1/attempts/{id}/answers` и наращивать `task_results` —
то есть на приёме ответа нет проверки доступа к заданию.

## Вердикт: ПОДТВЕРЖДЕНО на живых данных

Воспроизведено на dev-БД `Learn.public` тестом `tests/test_attempts_enrollment_hole_tsk272.py`
(бьёт по HTTP, не по сервису). Аутентификация — **обычный ученик** (`is_service=False`,
`current_user.id == user_id`), а не сервисный `X-API-Key`: проверяется именно клиентский путь.

| Шаг | Наблюдение |
|---|---|
| Инвариант фикстуры | у ученика `COUNT(user_courses WHERE is_active) == 0` |
| `POST /attempts` (курс без записи) | `201`, `root_course_id = null` |
| 4× `POST /attempts/{id}/answers` | коды `[200, 200, 200, 200]` |
| `task_results` по заданию | `0 → 4` |

Совпадает с пробником независимого рецензента (`[200,200,200,200]`, рост task_results).

## Первопричина (по коду + подтверждена данными)

В проекте **есть** конвенция ACL — `assert_task_access` / `assert_material_access` /
`assert_course_access` (`app/services/*_acl_service.py`). Устроена правильно:
`is_service` (X-API-Key — боты TG_LMS, CB CLI) и роли teacher/methodist/admin — bypass;
студент проходит, только если `task.course_id` лежит в дереве его `user_courses`
(recursive по `course_parents`).

**Но ACL висит только на ЧТЕНИИ:**

| Эндпоинт | ACL |
|---|---|
| `GET /tasks/{id}`, `/tasks/by-external` | `assert_task_access` (`tasks_extra.py:112,145`) |
| `GET /materials/...`, `/courses/...` | `assert_material_access` / `assert_course_access` |
| **`POST /attempts`** (создание попытки) | **нет никакой ACL** |
| **`POST /attempts/{id}/answers`** (приём ответа, запись `task_results`) | **нет никакой ACL** |

Проверки на приёме (`attempts.py:307–711`): `404` (нет попытки) → `403` (не владелец
попытки/не сервис) → попытка не завершена → задание в дереве **корня попытки** → тип
ответа → квиз → лимит (tsk-269). Проверки «а есть ли у ученика доступ к курсу задания»
(`user_courses.is_active`) нет нигде. Гейт дерева корня (2.1.1) не спасает: у не записанного
ученика `resolve_attempt_root` возвращает `None` (нет активных корней), корень пустой →
и tree-check, и лимит-гейт молча выключаются.

Итог: **прочитать** задание ученику без записи нельзя (403), а **ответить** на него —
можно (200). Дыра — в асимметрии чтение/запись.

## Радиус поражения на боевой БД (read-only, MCP `learn_prod_db`)

| Метрика | Значение | Вывод |
|---|---|---|
| tasks с `course_id IS NULL` | **0** из 7000 | риск «легаси-задачи без курса → 403 студенту» чисто теоретический |
| `task_results` от «не записанных» учеников | **2** из 634 | почти нулевой |
| разных таких учеников | **1** | это `user_id=2` (Виктор Комлев, без строк `user_courses`) — собственные dev/тест-сдачи (`lms`, `spw_web`), не реальный ученический трафик |

Вывод: включение проверки не отбивает ни одного легитимного ученика.

## Смежные контуры (проверено — фикс их не заденет)

- **Гости / embed** — ходят через ОТДЕЛЬНЫЙ эндпоинт `POST /learning/guest/attempts`
  (`learning_guest.py:153`, сервис `submit_guest_attempt`), а не через `/api/v1/attempts/answers`.
- **TG_LMS, ContentBackbone CLI** — `X-API-Key` → `is_service=True` → `assert_task_access` их bypass'ит.
- **SPW** — ученики записаны на курс (`user_courses`), доступ пройдёт штатно.
- **Импорт заданий** — идёт не через приём ответов.

## Предлагаемый фикс (НЕ реализован)

Идиоматично, по существующей конвенции — в цикле `submit_attempt_answers` после резолва
`task`, до записи результата:

```python
await assert_task_access(db, current_user=current_user, task_course_id=task.course_id)
```

`is_service` / extended-role bypass уже встроены — сервисные и преподавательские сценарии
не ломаются. Опционально (hardening) — та же проверка в `create_attempt` (`POST /attempts`),
чтобы попытка на чужой курс вообще не создавалась; основной вред (запись `task_results`)
закрывается гейтом на приёме.

### Открытый вопрос к оператору

Закрытие меняет поведение публичного write-эндпоинта. Реального трафика фикс не задевает
(данные выше), но это осознанное изменение контракта → согласовать до реализации.

## Фикс реализован (решение оператора: гейт на приёме ответа)

`app/api/v1/attempts.py`, шаг 2.1.0b в `submit_attempt_answers` — per-item, после резолва
задачи, до записи `task_results`:

```python
await assert_task_access(db, current_user=current_user, task_course_id=task.course_id)
```

- Bypass helper'а сохраняет сервисные и преподавательские сценарии (is_service,
  teacher/methodist/admin). Гости — отдельный эндпоинт, не затронуты.
- `docs/openapi.json` перегенерирован (`scripts/export_openapi.py`): добавлен 403 к
  `POST /attempts/{id}/answers` (единственное изменение диффа).
- `create_attempt` (POST /attempts) намеренно НЕ трогали — оператор выбрал минимальный
  гейт на записи; создание пустой попытки без записи результата вреда не наносит.

## Валидация

| Проверка | Тест | Итог |
|---|---|---|
| Ученик без записи → 403, task_results не растёт | `test_unenrolled_student_denied` | PASS |
| Записанный ученик → 200 (регресс не сломан) | `test_enrolled_student_allowed` | PASS |
| Сервисный ключ (X-API-Key) → 200 (bypass) | `test_service_key_still_allowed` | PASS |
| Преподаватель (teacher) → 200 (bypass) | `test_teacher_role_allowed` | PASS |

Регрессия смежных контуров (гейт в общем пути приёма): tsk-269 (лимит), квиз, ручная
проверка, learning-engine, syllabus, last-attempt — **159 passed, 0 failed**.

## Артефакты

- Регрессионный тест: `tests/test_attempts_enrollment_hole_tsk272.py` (4 теста, за собой чистит).
- `app/api/v1/attempts.py` (гейт 2.1.0b + 403 в OpenAPI-responses), `docs/openapi.json`.
- Диагностика на проде — read-only; боевая БД не изменялась. Dev-БД правил только самоочищающийся тест.

## Деплой + живая проверка на проде (2026-07-17)

Развёрнуто на прод VPS `lms-spw-vds` (`deploy/vps/deploy.sh`, origin/main `75dd907`).
`/db-check` перед деплоем: repo alembic head == прод head (`tsk264_attempts_root_course`) →
`alembic upgrade head` — no-op, схема не менялась. `/health` → 200, сервис active,
прод openapi отдаёт новый 403.

**Живой функциональный прогон под тестовым учеником 142** (плайн-`student`, без bypass).
Сессия выпущена на сервере (`session_service.create_session`, opaque-токен по хешу),
всё убрано за собой (попытки/сессия/результат удалены, baseline восстановлен, побочных
назначений нет):

| Тест | Условие | Ответ прода |
|---|---|---|
| DENY | курс 561 (142 НЕ в дереве), задание 388 | **403** `"Доступ к задаче запрещён: вы не зачислены в этот курс"` |
| ALLOW | курс 1 (142 записан), задание 40 | **200**, ответ принят и оценён |
| Без авторизации | — | **401** |

Вывод о живом поведении — **вердикт, а не гипотеза**: дыра на проде закрыта, легитимный
ученик не заблокирован.

## Статус

Задеплоено, проверено вживую на проде. tsk-272 закрыта.
