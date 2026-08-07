# tsk-574 — повторное назначение курса: 409 вместо 500

**Дата:** 2026-08-07
**Скилл:** `/fastapi-api-developer`
**Файлы:** `app/services/user_courses_service.py`, `app/api/v1/user_courses.py`,
`tests/test_user_courses_duplicate_tsk574.py`, `docs/openapi.json`,
`docs/API_STUDENTS_MANAGEMENT.md`, `docs/ai/ERRORS.md`
**Cross-project:** `ContentBackbone/docs/cross-project/contracts/lms-api.md`,
`.../CHANGELOG.md` (захват `clm-310`)
**Diff:** `reviews/2026-08-07-tsk574-user-courses-duplicate-409.diff`

## Контекст

`POST /api/v1/user-courses/` на повторную пару `(user_id, course_id)` отдавал
**500 Internal server error**: `asyncpg.UniqueViolationError` на составном
первичном ключе `user_courses_pkey` доходил до глобального обработчика
`Exception` в `app/api/main.py`. В собственном описании эндпоинта при этом было
обещано «При попытке создать дубликат связи возвращается ошибка 400» — обещание
никогда не выполнялось.

Прод 2026-08-06: скрипт пакетного назначения отработал дважды. Первый прогон
создал все пять связей, второй вернул пять «Internal server error». Отличить
«уже назначено» от отказа сервера оператор не мог и повторял запрос вслепую.

## Решение

**Код ответа — 409 Conflict** (не 400, которое обещало описание). Запрос
корректен, конфликтует состояние ресурса; 409 — уже устоявшийся код конфликта в
LMS (лимит попыток tsk-269/tsk-273, identity-overlap VK, занятая почта в
`PATCH /users/{id}`, привязка к вложенному курсу). Описание эндпоинта,
`responses` и docstring приведены к 409.

Совместимость: 400 на этом пути не существовал никогда (был 500), поэтому смена
кода ничего не ломает. Единственный клиент POST — TG_LMS
(`api_client.add_user_to_course`), он статус не разбирает; SPW использует только
`DELETE /user-courses/{user}/{course}`.

**Защита двухслойная, оба слоя нужны:**

1. Ранняя проверка `repo.get_by_keys` в `UserCoursesService.create` — отсекает
   дубль ДО `ensure_dependencies_assigned`, иначе доназначение зависимостей
   делает вставки впустую и они сносятся откатом.
2. Перехват `IntegrityError` вокруг вставки — закрывает гонку двух параллельных
   назначений: между проверкой и INSERT строку может создать соседний запрос.
   Нарушение уникальности (`SQLSTATE 23505`, с запасным разбором имени
   ограничения) → `DomainError` 409; прочие нарушения целостности (например FK
   на несуществующий курс) пробрасываются как были.

`await db.rollback()` перед выбросом обязателен: после провала вставки сессия
непригодна, следующий запрос по ней упал бы уже на чтении.

**Второй путь назначения.** `assign_course_with_order` дублировал проверку и
отдавал **400** на тот же случай. Собственная проверка убрана — метод делегирует
`create`, код конфликта теперь один на оба пути. (Метод сейчас никем не
вызывается, но расхождение кодов было бы миной.)

**Пакетное назначение не затронуто:** `POST /users/{id}/courses/bulk` идёт через
`bulk_create_user_courses`, который фильтрует уже существующие связи — дубля там
не возникает.

## Тесты

`tests/test_user_courses_duplicate_tsk574.py` — на настоящей БД, в откатываемой
транзакции (без опт-аута изоляции):

1. `test_duplicate_assignment_returns_409` — штатный повтор: 201 → 409, тело
   `{"error": "domain_error", "detail": "Курс уже назначен этому ученику",
   "payload": {...}}`, в БД ровно одна строка связи.
2. `test_duplicate_race_returns_409_and_keeps_session_usable` — гонка: ранняя
   проверка заглушена (`get_by_keys` → None), запрос доходит до INSERT и падает
   на `user_courses_pkey` → 409 через перехват `IntegrityError`. Следом
   проверяется, что соединение осталось рабочим (следующий GET отвечает 200) —
   то есть откат сессии не обрывает транзакцию.

**Контроль на регресс:** оба теста прогонялись на версии сервиса из `HEAD` —
падают с `UniqueViolationError`/500; с правкой — зелёные.

## Validation Commands

```bash
.venv/Scripts/python.exe -m pytest tests/test_user_courses_duplicate_tsk574.py -q
.venv/Scripts/python.exe -m mypy app/services/user_courses_service.py app/api/v1/user_courses.py
.venv/Scripts/python.exe -m pytest -q -p no:randomly
```

- Новый файл тестов: **2 passed**.
- mypy по двум изменённым файлам: **Success: no issues found**.
- Полный прогон: см. раздел «Прогон» ниже.

## Прогон

**Итог: `1854 passed, 11 skipped, 0 failed` (10:50).**

Путь к нему стоит зафиксировать. Первые два полных прогона дали по 3 падения, но
**разных**: сначала `test_migrations.py` (три downgrade/upgrade), затем другие два
миграционных + `test_help_request_autoclose_tsk339.py`. Плавающий набор — признак
внешней помехи, а не дефекта. Проверка процессов подтвердила: в той же dev-БД
параллельно работала соседняя сессия (`pytest tests/ -q` и
`pytest test_migrations.py -k m6_then_upgrade`, PID 36732/40300), двигавшая
alembic-версию под собой; оба задетых файла — из
`SELF_MANAGED_CONNECTION_MODULES`, то есть без транзакционной изоляции.
Дополнительный контроль: `test_migrations.py` даёт 10/10 и на версии из `HEAD`
(без моих правок), и с ними. Третий прогон был запущен в окне тишины (чужих
pytest-процессов нет) и прошёл полностью зелёным.

## Живая проверка на проде (2026-08-07)

Развёрнуто `deploy/vps/deploy.sh` → `23f1a00`. `alembic upgrade head` — пусто
(head `tsk572_llm_usage` уже был на проде), `/health` → ok.

Проверен ровно тот запрос из инцидента — на **уже существующей** паре
`(user_id=3, course_id=1455)`, поэтому проверка ничего не создаёт:

```
POST /api/v1/user-courses/  {"user_id":3,"course_id":1455,"is_active":true}
HTTP 409
{"error":"domain_error","detail":"Курс уже назначен этому ученику",
 "payload":{"user_id":3,"course_id":1455}}
```

В `logs/app.log` вместо прежнего `Unhandled exception ... UniqueViolationError`
теперь `WARNING DomainError at /api/v1/user-courses/ ... (status=409)` — то есть
ошибка перешла из «неизвестный сбой» в штатный отказ и в логе, и в ответе.

## Риски / follow-ups

- FK-нарушение на этом эндпоинте (несуществующий `user_id`/`course_id`) по-прежнему
  даёт 500 — вне охвата задачи, отдельный класс. Кандидат на 404/422.
- Триггер `trg_check_user_course_no_parents` (привязка к вложенному курсу) кидает
  `RaiseError`, а не `IntegrityError` — этот путь тоже остаётся 500 на прямом
  `POST /user-courses/` (в `bulk` он уже обработан, tsk-433).
