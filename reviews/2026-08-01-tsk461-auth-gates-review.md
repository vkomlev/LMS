# Review-gate: tsk-461 — гейты авторизации check/task, by-pending-review, courses/by-code

**Решение: ПРИНЯТО**

## Контекст

Три находки из разведки под tsk-433 (2026-07-29), заведены отдельной задачей tsk-461
(P1). Решения по каждой получены от оператора через `AskUserQuestion` 2026-08-01 —
не додуманы.

## Изменения

| Эндпоинт | Было | Стало |
|---|---|---|
| `POST /check/task`, `POST /check/tasks-batch` | без `Depends` вообще | `Depends(get_current_user)` |
| `GET /task-results/by-pending-review` | `Depends(get_db)` (legacy api_key, без CurrentUser) | `Depends(get_async_db)` + `Depends(_STATS_GATE)` = `require_role("teacher","methodist","admin")` |
| `GET /courses/by-code/{code}` | без ACL (только auth) | без изменений в коде — закреплено ADR-0004 |

Файлы: `app/api/v1/checking.py`, `app/api/v1/task_results_extra.py`,
`app/api/v1/courses_extra.py` (только docstring), `docs/ai/adr/0004-courses-by-code-public-resolver.md`,
`tests/test_check_task_and_pending_review_auth_gate_tsk461.py`.

## Проверка по 12 измерениям

1. **Соответствие целям** — все три пункта декомпозиции tsk-461 закрыты решениями оператора. DRIFT нет.
2. **Корректность** — `get_current_user` требует ровно один источник (cookie/Bearer/token/X-API-Key/legacy api_key), иначе 401; `require_role` даёт service bypass. Проверено тестами.
3. **БД/миграции** — не затронуты (Data Impact: none).
4. **Безопасность/IDOR** — check/task стал закрыт auth (не IDOR, а полное отсутствие гейта — устранено). by-pending-review IDOR-поверхность (`user_id`/`course_id` query) не расширена, только сужен круг вызывающих. courses/by-code — риск перечисления `course_uid` признан приемлемым и задокументирован (ADR-0004).
5. **Тесты** — 8 новых HTTP-level тестов (`tests/test_check_task_and_pending_review_auth_gate_tsk461.py`), не моки: реальный `AsyncClient` + реальная БД (роли/сессии). Полный прогон 1306 тестов: 1295 passed, 11 skipped, 0 failed (456s).
6. **Docs/Config drift** — докстринги обоих эндпоинтов обновлены в этом же коммите; новый ADR-0004; `openapi.json` пересоберётся pre-commit хуком.
7. **Phase integrity** — scope = ровно 3 пункта декомпозиции tsk-461, без побочных правок.
8-10. Не применимо (нет данных/доменных справочников/дат в этом изменении).
11. **Cross-project memory** — `ContentBackbone/docs/cross-project/CHANGELOG.md` (новая запись 2026-08-01) и `contracts/lms-api.md` (переподтверждение по courses/by-code) обновлены. Коммит в ContentBackbone — до завершения задачи.
12. **Public API Contract Sync** — статус-коды изменились (401 на check/task, 403 на by-pending-review) → задокументировано в докстрингах/responses в том же коммите; hardcoded URL нет (grep пуст); URL/метод не менялись — cross-repo grep на старые пути не требуется.

## Блокирующие проблемы
Нет.

## Улучшения без блокировки
- Мойибейк (encoding corruption) в существующем докстринге `get_pending_review_results` — предсуществующий дефект, вне scope этой задачи (не трогать по anti-bloat, отдельная работа для `/encoding-guard`).

## Известный побочный эффект коммита (не блокирует)

Коммит `5e85985` включает в `docs/openapi.json` схему эндпоинта
`/api/v1/students/{student_id}/dashboard` — это НЕ часть tsk-461. Причина:
pre-commit хук перегенерирует `docs/openapi.json` из **всего** рабочего дерева
(`scripts/export_openapi.py` импортирует `app.api.main` целиком), а в дереве
параллельно работала другая сессия Claude над tsk-494 (`app/api/v1/student_dashboard.py`,
`app/api/main.py` — на тот момент не закоммичены). Сознательно не исправлялось
хирургически (риск тронуть чужие незакоммиченные файлы выше пользы от
косметически чистого diff); leaked-схема не функциональна (роутер той сессии
ещё не закоммичен — на проде его нет) и не содержит секретов. Заведён отдельный
чип на сужение хука (`task_450073cf`, D:\Work\LMS — сузить pre-commit регенерацию
openapi.json).

## Operator handoff
- Категория А (review-gate пройден → коммит/пуш сам, деплой сам + живая проверка в этой же сессии, per operator-handoff-rules и durable-авторизация tsk-359).
