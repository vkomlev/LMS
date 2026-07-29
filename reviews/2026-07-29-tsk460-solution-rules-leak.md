# tsk-460 — правило проверки с верными ответами уходило ученику

**Дата:** 2026-07-29 · **Задача:** tsk-460 (P1) · **Профиль:** `/fastapi-api-developer`
**Diff:** [2026-07-29-tsk460-solution-rules-leak.diff](2026-07-29-tsk460-solution-rules-leak.diff)

## Что было

Схема `TaskRead` (`app/schemas/tasks.py`) включает `solution_rules` — правило
проверки с верными ответами (`correct_options` у SC/MC, `accepted_answers` у SA).
Три эндпоинта `app/api/v1/tasks_extra.py` отдавали её ученику как есть после
ACL-проверки «задача в дереве моих курсов»:

- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/tasks/by-external/{external_uid}`
- `GET /api/v1/tasks/by-course/{course_id}`

ACL решал «дать/не дать задачу целиком», а не «какие поля отдать». Ученический
фронт SPW зовёт все три (`use-task-attempt.ts`, `use-course-content.ts`,
`use-course-syllabus.ts`), поэтому ученик видел верный ответ во вкладке «Сеть»
до того, как отправлял свой. Через `by-course` — сразу по всему курсу одним
запросом.

## Как подтверждено (живьём, не по коду)

Живой прод-запрос под cookie-сессией, 2026-07-29:

```
GET https://learn.victor-komlev.ru/api/v1/tasks/by-external/wp:task:komlev:chisla-v-python-i-operatsii-s-nimi:cq:0:2
→ 200, тело содержит "solution_rules":{...,"correct_options":["A"],...}
```

Что видит именно ученик без привилегий — доказано автотестом на реальном стеке
FastAPI (решение оператора). Тест `test_student_does_not_see_solution_rules` был
прогнан на коде ДО фикса (правка временно откачена через `git stash push` по
списку файлов):

```
assert {'auto_check': True, 'correct_options': ['B'], 'max_score': 10, ...} is None
1 failed
```

Пользователь в тесте — с единственной ролью `student`, зачислен в курс задания.

## Что сделано (вариант A1, выбран оператором)

Те же три эндпоинта, та же схема; поле `solution_rules` обнуляется для
непривилегированного вызывающего.

- `assert_task_access` / `assert_course_access` теперь возвращают `bool` —
  признак «вызывающий привилегирован» (сервисный ключ либо роль
  `admin` / `methodist` / `teacher`). Сама проверка доступа не изменилась,
  прежние вызывающие возвращаемое значение просто игнорируют.
- `_task_read_for(task, privileged=...)` в `tasks_extra.py` собирает `TaskRead`
  и, если вызывающий не привилегирован, отдаёт копию с `solution_rules=None`.
  Обнуление идёт **на копии Pydantic-модели**: мутация атрибута ORM-строки
  попала бы в autoflush и записала NULL в БД (проверено отдельным тестом).
- Описание поля в схеме и в OpenAPI объясняет, кому оно приходит.

Варианты, которые НЕ выбраны: A2 (отдельная схема `TaskReadPublic` → `Union` в
`response_model` и правка потребителей в SPW), B (отдельный ученический
эндпоинт → синхронный релиз фронта).

## Чего фикс НЕ трогает

- **`hints_text` / `hints_video`** — вопреки исходной формулировке задачи это
  легитимные ученические поля: страница задания показывает по ним подсказки.
- **Ученический фронт SPW** — правило он не читает вовсе. Гейт «требуется
  вложение» с tsk-234 берёт серверный флаг `state.requires_attachment` из
  `GET /learning/tasks/{id}/state`; `attachment-gate.ts` и
  `RequiredAttachment.tsx` только упоминают правило в комментариях. Отдельный
  серверный флаг заводить не понадобилось — он уже есть.
- **Гостевой поток** — идёт по своему `/learning/guest/task/{id}` со схемой
  `GuestTaskResponse`, где правила не было изначально.
- **Кабинет методиста** (`components/methodist/TaskDetail.tsx`) — методист
  привилегирован, правило приходит как раньше. Поправлен устаревший комментарий,
  который ссылался на эту утечку как на незакрытую.
- **`GET /tasks/search`** и генерик-CRUD `GET /tasks` — остаются на legacy-гейте
  `?api_key=`, ученику по cookie недоступны (отдельная задача tsk-461).

## Валидация

```
.venv/Scripts/python.exe -m pytest tests/test_tsk460_solution_rules_hidden_from_student.py -q
→ 7 passed
```

Новые тесты (`tests/test_tsk460_solution_rules_hidden_from_student.py`):

1. ученик зачислен → 200, `solution_rules` = null, `correct_options` нет в теле,
   при этом текст задания и подсказки на месте;
2. `teacher` / `methodist` / `admin` → правило приходит (параметризовано);
3. сервисный ключ → правило приходит (ТГ-боты, ContentBackbone);
4. запрос ученика не затирает правило в БД (autoflush-ловушка);
5. не зачисленный ученик по-прежнему получает 403 (ACL не ослаб).

Регресс по смежным наборам:

```
pytest tests/test_tasks_acl_post_s5.py tests/test_attempts_enrollment_hole_tsk272.py \
       tests/test_methodist_content_cookie_tsk433.py tests/test_requires_attachment_gate_tsk227.py \
       tests/test_learning_api_routes.py tests/test_hints_stage5.py \
       tests/test_task_history_tsk349.py tests/test_task_search_tsk353.py -q
→ 55 passed
```

## DB Findings

Прод-БД только на чтение (MCP `learn_prod_db`): подтверждено, что пользователь
142 — с единственной ролью `student`, и что задания в его дереве курсов имеют
непустой `solution_rules`. Записи в прод-БД в рамках задачи не было.

## Cross-project

Публичный контракт API затронут, обновлено в `D:\Work\ContentBackbone\docs\cross-project\`:
`contracts/lms-api.md` (раздел tsk-460) + `CHANGELOG.md` (запись 2026-07-29 (2)).
Ломающего изменения нет — форма ответа прежняя, поле осталось `Optional`,
потребители по сервисному ключу видят то же, что раньше.

## Risks / Follow-ups

- Ретро-анализ (были ли аномально высокие результаты по SC/MC у веб-активных
  учеников) оператор отложил — не блокер фикса.
- Смежные находки той же разведки чинятся отдельно: tsk-461 (эндпоинты без
  auth-гейта), tsk-463 (`/courses/{id}/tree` отдаёт 500).
