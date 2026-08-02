# tsk-463 + tsk-467 — дерево курсов (500) и защита от отладочного мусора публикатора

Два независимых LMS-фикса, взятых одним чипом (изолированы друг от друга и от
параллельного tsk-010).

## tsk-463 — `GET /courses/{id}/tree` отдавал 500

### Контекст
`courses_repo.get_course_tree` строил дерево, присваивая потомков в
relationship-атрибут `child_courses` через `object.__setattr__` — расчёт был на
то, что это обходит lazy-load. Ошибочно: `object.__setattr__` НЕ обходит
дата-дескриптор SQLAlchemy (`child_courses` — двусторонний
`relationship(back_populates=...)` с `parent_courses`). Присваивание
триггерило синхронизацию обратной стороны связи, а она в async-контексте
ленивая — падение `MissingGreenlet`. Плюс схема `CourseTreeRead` ждёт поле
`children`, а не `child_courses` — даже без падения дерево уехало бы клиенту
пустым.

### Repro (до фикса)
Тест `tests/test_tsk463_course_tree.py` воспроизводит 500
(`sqlalchemy.exc.MissingGreenlet`) на дереве root → child → grandchild.

### Fix
- `app/repos/courses_repo.py::get_course_tree` — дерево строится из
  `SimpleNamespace`, а не из ORM `Courses`: никаких relationship-присваиваний,
  поле сразу называется `children`. `parent_course_ids` каждого узла считается
  из уже полученных пар `course_parents`, без лишнего запроса.
- `get_all_children` — SQL и конструктор `Courses(...)` дополнены
  `is_public_demo` (раньше не выбирался, схема требует поле).
- `app/services/courses_service.py::get_course_tree` — сигнатура
  `Optional[SimpleNamespace]` вместо `Optional[Courses]`.
- `app/api/v1/courses_extra.py::get_course_tree_endpoint` — переведён на
  `_COURSE_TREE_GATE` (`get_bare_db` + `require_role(teacher, methodist,
  admin)`), как соседние `roots`/`children`/`{id}` (tsk-433). Обновлены
  `responses` (401/403 вместо устаревшего «Invalid or missing API Key»).
- `tests/test_methodist_content_cookie_tsk433.py::_paths` — `/tree` добавлен
  в проверяемые пути гейта (создание/методист/студент/сервис-ключ/аноним).

### Validation
```
pytest tests/test_tsk463_course_tree.py tests/test_methodist_content_cookie_tsk433.py -q
# 9 passed
```
Живая проверка на проде — курс id=112 (26 прямых потомков) через
`/api/v1/courses/112/tree` с методистской cookie-сессией, после деплоя.

---

## tsk-467 — гейт против отладочных прогонов публикатора в проде

Оператор выбрал все три предложенных (не взаимоисключающих) варианта:
**гейт на приёмнике**, **разделение окружений**, **инвариант-детектор**.

### Находка при разборе (меняет диагноз тикета)
Тикет связывал инцидент с «отладочными прогонами публикатора
ContentBackbone» и приводил как улику партии `wp:bulk-*` / `wp:mix-good-*`.
Проверка: эти префиксы — не из ContentBackbone (`grep` по `monolith/`,
`scripts/` — 0 совпадений), а из **этого репозитория**:
`tests/test_materials_bulk_upsert.py` генерирует ровно `ext =
f"wp:bulk-{uuid...}"` и `ext_good = f"wp:mix-good-{uuid...}"`, и использовал
title="Win"/content.text="hello", title="OK row"/content.text="ok" —
дословное совпадение с описанием инцидента. Вероятный механизм: этот тест (и
соседи по `SELF_MANAGED_CONNECTION_MODULES` — реальные коммиты без отката) был
прогнан в контексте, где `DATABASE_URL` резолвился в прод (например, SSH-сессия
на `/opt/lms`, где `.env` — прод по определению), а не отладка публикатора.

`db_write_gate.py` это не ловит осознанно: `test_*.py` исключены из анализа
тела скрипта, и сам хук документирует эту дыру («ОСТАЁТСЯ НЕ ПОКРЫТЫМ» — DSN
из `.env`/конфига приложения, в теле скрипта ни хоста, ни прод-переменной).

Это не отменяет исходную гипотезу тикета (публикатор ContentBackbone мог быть
отдельной причиной трёх заходов 1/3/6 июня), но добавляет вторую, лучше
подтверждённую данными. Обе стороны защищены: гейт на приёмнике не зависит от
источника записи.

### 1. Гейт на приёмнике (`app/schemas/materials.py`)
`MaterialsBulkUpsertItem`:
- `title` не может быть пустым/из пробелов (`field_validator`).
- `_reject_known_junk()` (после валидации content по type):
  - `title` (strip+casefold) в стоп-листе тестовых значений
    (win/ok row/ok/test/hello/foo/bar/lorem…) → reject;
  - `type == "text"` и `content.text` (strip+casefold) целиком — одно из
    тестовых слов-заглушек → reject.
- **Не порог по длине** — критерий содержательный, ровно как решил оператор:
  «Кэш»/«Итог» (короткие легитимные) проходят, «Win»/«OK row» — нет.
- Точка входа: `MaterialsService.bulk_upsert` уже вызывает
  `MaterialsBulkUpsertItem.model_validate(raw)` per-item — reject становится
  `status="error"` в ответе, не рвёт остальной batch (существующий контракт).

### 2. Разделение окружений (`tests/conftest.py`)
`pytest_configure`: сессия pytest отказывает целиком, если `DATABASE_URL`
похож на боевую БД LMS (host `5.42.107.253` / роль `lms_prod`), без
`ALLOW_PROD_TESTS=1`. Защищает разом все `SELF_MANAGED_CONNECTION_MODULES` —
не только `test_materials_bulk_upsert.py`.

### 3. Инвариант-детектор (`scripts/materials_junk_invariant.py`)
Read-only, по образцу `scripts/ege_answer_invariant.py`: ищет в `materials`
строки с title/content.text из тех же стоп-листов (импортированы напрямую из
схемы, чтобы не разъезжались). allowlist по id, JSON-режим, коды возврата
0/1/2.

### Regression: существующий тест содержал ровно инцидент
`tests/test_materials_bulk_upsert.py` использовал `content.text: "hello"` по
умолчанию и титулы `"Win"`/`"OK row"` — под новым гейтом эти вызовы стали бы
`status="error"`. Переписаны на неймусорные значения (`"Winning title"` /
содержательный текст), добавлен отдельный блок, который явно бьёт по гейту
tsk-467: title="Win"+нормальный текст → error, обычный title+content.text=
"hello" → error, короткий легитимный title="Кэш" → создаётся (200,
status=created). Три строки такого мусора в БД не попадают (проверено
`SELECT COUNT`).

### Validation
```
pytest tests/test_materials_bulk_upsert.py -q -s   # [PASS] materials bulk-upsert
python scripts/materials_junk_invariant.py         # прод: 0 нарушений (см. ниже)
DATABASE_URL=postgresql+asyncpg://lms_prod:x@5.42.107.253:5432/learn pytest tests/test_tsk463_course_tree.py
  # → pytest_configure отказывает: "tsk-467: DATABASE_URL похож на БОЕВУЮ БД LMS..."
ALLOW_PROD_TESTS=1 DATABASE_URL=...5.42.107.253... pytest ... --collect-only
  # → override снимает отказ, тесты собираются
```
Прод (read-only, `mcp__learn_prod_db`): 0 строк по тому же критерию — зачистка
tsk-465 держится, детектор синтаксически и логически рабочий на реальной
схеме.

Dev-БД (`localhost/Learn`): детектор нашёл 300 строк «Win»/«OK row» —
накопленный мусор от прошлых прогонов ЭТОГО теста ДО фикса (не прод, не
требование тикета; оставлено как есть, вне scope этой задачи — операторское
решение, чистить или нет).

## Full regression
```
pytest tests/ -q   # 1515 passed, 11 skipped, 0 failed
```

## Changed Files
- `app/repos/courses_repo.py`
- `app/services/courses_service.py`
- `app/api/v1/courses_extra.py`
- `app/schemas/materials.py`
- `tests/conftest.py`
- `tests/test_materials_bulk_upsert.py`
- `tests/test_methodist_content_cookie_tsk433.py`
- `tests/test_tsk463_course_tree.py` (новый)
- `scripts/materials_junk_invariant.py` (новый)

## DB Findings (MCP)
- `learn_prod_db` (read-only): курс id=112 — 26 прямых потомков, подходит для
  живой проверки tsk-463 после деплоя.
- `learn_prod_db` (read-only): 0 строк по критерию инвариант-детектора tsk-467
  — зачистка tsk-465 держится.

## Risks / Follow-ups
- `db_write_gate.py` не защищает `test_*.py`-скрипты с DSN из `.env` в
  принципе (документированная граница) — стоит рассмотреть отдельной задачей
  расширение хука ИЛИ считать `pytest_configure`-гейт в `conftest.py`
  достаточным периметром защиты (он покрывает весь класс
  `SELF_MANAGED_CONNECTION_MODULES` одним местом).
- Разделение окружений реализовано только со стороны LMS (тестовый прогон).
  Если реальный источник — всё-таки публикатор ContentBackbone (гипотеза
  тикета), там аналогичной защиты по-прежнему нет — это уже другой репозиторий
  и, по находке выше, не подтверждённая данными часть диагноза; не делал
  правку в ContentBackbone без более сильных доказательств, чтобы не чинить
  систему, которая, возможно, не виновата.
- 300 строк накопленного тестового мусора в DEV БД (`localhost/Learn`,
  курс "Основы Python") — не прод, вне scope; чистить или нет — на усмотрение
  оператора.
