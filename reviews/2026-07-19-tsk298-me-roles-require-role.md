# Review — tsk-298 Фаза 0: /me.roles[] + централизованный роль-гейт

**Дата:** 2026-07-19
**Задача:** tsk-298 (Root-трекер), Фаза 0 — enabler для SPW-портала преподавателя
**Скилл:** `/fastapi-api-developer`
**Автор:** Victor (Claude)

## Контекст

Enabler для портала преподавателя в SPW. Цель Фазы 0:
1. Отдать роли пользователя в API, чтобы SPW гейтил teacher-зону по роли из `/me`.
2. Централизовать проверку НАЛИЧИЯ роли в единый dependency вместо размазанного per-handler ACL.

Архитектурный разбор: [docs/specs/2026-07-19-arch-teacher-portal.md](../docs/specs/2026-07-19-arch-teacher-portal.md) (разделы 7-10).

## Решение по охвату (развилка вынесена оператору)

Разбор кода показал: у 5 перечисленных в задаче файлов **разные** модели авторизации
(identity-match / service-key-only / role-presence). Буквальное «перевести все 5 на общий
гейт» конфликтует с «не менять внешнее поведение». Оператор выбрал **поведение-нейтральный
охват**: мигрируется только тот файл, где перевод поведение-идентичен.

| Файл | Текущая авторизация | Действие Фазы 0 |
|---|---|---|
| `methodist_escalations.py` | `get_current_user` + `_user_is_methodist()` + service-bypass | ✅ Переведён на `require_role("methodist")` — поведение идентично |
| `teacher_reviews.py` | identity-match (`id==teacher_id`) + service | ⏭️ Не трогаем: `require_teacher` изменил бы edge-case (200→403) |
| `teacher_workload.py` | `get_db` (только сервисный ключ) | ⏭️ Follow-up Фаза 2: открыть cookie-преподавателю |
| `teacher_help_requests.py` | `get_db` (только сервисный ключ) | ⏭️ Follow-up Фаза 2 |
| `teacher_assignments.py` | `get_current_user` + role сплавлен со student-ACL | ⏭️ Не трогаем: ACL запрещён к правке |

## Changed Files

- `app/schemas/me.py` — `MeResponse.roles: list[str]` (аддитивно, default `[]`).
- `app/services/roles_service.py` — `get_user_role_names(db, user_id) -> list[str]` (единый источник имён ролей; `text()`-запрос без ORM-импорта association-таблицы во избежание circular import).
- `app/api/v1/me.py` — `GET /me` и `PATCH /me` заполняют `roles`.
- `app/api/deps.py` — фабрика `require_role(*names)` + алиас `require_teacher`. Сервисный токен — bypass; отсутствие роли — 403; ленивый импорт `roles_service` (deps грузится рано → circular import).
- `app/api/v1/methodist_escalations.py` — `require_role("methodist")` вместо `_user_is_methodist`; удалён неиспользуемый хелпер и импорт `HTTPException`.
- `docs/openapi.json` — регенерирован (поле `roles`, обновлённые описания).

## DB Findings (MCP, learn_prod_db, read-only)

Роли подтверждены запросом (id не хардкодятся, резолв по имени):

| id | name | | id | name |
|----|------|----|----|------|
| 1 | admin | | 4 | student |
| 2 | methodist | | 5 | marketer |
| 3 | teacher | | 6 | customer |

- Имена ролей — английские; русские варианты (`методист`/`преподаватель`) в `teacher_assignments.py` — только защитные lowercase-фолбэки, в БД их нет.
- Все 4 преподавателя также имеют роль `student` (мульти-роль — норма). Users 2, 3 — мульти-роль (admin/methodist/teacher/student).
- `/me.roles` резолвит по `user_roles` M2M, сортировка по имени.

## Validation Results

| Критерий приёмки | Статус |
|---|---|
| `GET /me` возвращает `roles[]` | ✅ PASS (4 теста: student-only=`['student']`, teacher=`['teacher']`, multi=`['student','teacher']`, тип-список) |
| `openapi.json` обновлён | ✅ PASS (`MeResponse.roles` присутствует; 169 endpoints, чужие эндпоинты не просочились) |
| `require_teacher`/`require_role` применён | ✅ PASS (methodist_escalations на `require_role`) |
| require_teacher: 403 не-препод / 200 препод / service-bypass | ✅ PASS (юнит + e2e через methodist-эндпоинт) |
| Регрессия существующих тестов | ✅ PASS (58 passed: me y3/y4, teacher stage38/39, assignment tsk031) |
| bandit clean | ✅ PASS (0 новых замечаний; единственная Low — pre-existing `_self_heal_student_role`) |
| Живой прогон на проде (roles в /me под teacher) | ⏳ После мержа (ветвь А operator-handoff) |

Тесты: `tests/test_me_roles_tsk298.py` (4), `tests/test_require_role_tsk298.py` (7). Итого новые: 11; вместе с me_profile — 18 passed.

## Команды валидации

```bash
.venv/Scripts/python.exe -m pytest tests/test_me_roles_tsk298.py tests/test_require_role_tsk298.py -q
.venv/Scripts/python.exe scripts/export_openapi.py
.venv/Scripts/python.exe -m bandit -q app/api/deps.py app/services/roles_service.py app/api/v1/me.py app/api/v1/methodist_escalations.py app/schemas/me.py
```

## Risks / Follow-ups

- **R (Фаза 2):** `teacher_workload` / `teacher_help_requests` — сервис-ключ-only; для веб-портала их надо открыть cookie-преподавателю (`get_current_user` + `require_teacher`). Отдельная задача — меняет внешнее поведение.
- **R (техдолг R3 arch-doc):** хардкод роли `id==3` в TG-боте (TG_LMS) не тронут — отдельная задача.
- **Общее дерево (ADR-0008):** в рабочем дереве есть чужие незакоммиченные правки (`task_results_extra.py`, `task_results_service.py`) — в коммит Фазы 0 не включаются (commit только по pathspec своих файлов).
- Cross-project: изменение `/me` → обновить mirror `lms-api.md` + `CHANGELOG.md` в ContentBackbone.
