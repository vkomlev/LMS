# Tech Spec: LMS Import — task_content extensions (images / attached_files / multi-hints)

**Дата:** 2026-05-27
**Status:** PROPOSED
**Skill (запросчик):** /tech-spec-composer (CB-side, North-Star Pivot 2026-05-27)
**Authority chain:** CB architect-system-analyst анализ 2026-05-27 → этот документ → LMS implementation review
**Cross-project mirror:** `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md` §Import (обновляется LMS-стороной после merge)

---

## 1. Цель

Расширить `POST /api/v1/tasks/import/google-sheets` так, чтобы импортируемые задачи могли содержать в `task_content`:
- список изображений в условии (`stem_images`),
- список прикреплённых файлов задания (`attached_file_paths` + `has_attached_file`) для SA_COM Python/XLSX-заданий,
- множественные подсказки текстовые и видео (`hints_text`, `hints_video` + `has_hints`).

**Пользовательский результат:** CB tsk-004 Phase 6.7 boevoi прогон 1040 задач (kpolyakov 340 + kompege 270 + sdamgia 210 + yandex 220 + Krylov PDF 540) проходит через Google Sheets import **без потери** видео-разборов Виктора (главная ценность Phase 6 per CB brief), images в kpolyakov-задачах и прикреплённых XLSX-файлов в Krylov SA_COM-заданиях.

---

## 2. Контекст

### 2.1. Запросчик и обоснование

**CB tsk-004 Phase 6.7 north-star pivot 2026-05-27:** delivery-contour для 1040 задач переведён с CB→LMS bulk-upsert API на CB-генерирует-таблицу → operator-edit → LMS reads через Google Sheets import. Это упрощает operator-flow (ручная правка stem'ов методистом до публикации), но текущий FLAT-формат import-API теряет 3 класса данных, которые LMS schema УЖЕ принимает в `task_content` (jsonb свободной формы, tsk-088 BLOCKER fix 2026-05-24).

**3 gap'а в текущем import-формате:**

| # | Поле | Источник CB | Сейчас в import | Без него |
|---|---|---|---|---|
| G1 | `stem_images: list[str]` | kpolyakov image-resolver (графы/таблицы) | — | SC-задачи с картинками превращаются в текст-без-картинок (бессмысленны) |
| G2 | `attached_file_paths: list[str]` + `has_attached_file: bool` | Krylov fix2 attachments resolver (ADR-0033 CB) для SA_COM tasks 3/9/17/22/24/26/27 (~140 задач) | — | Python/XLSX-задания нерешаемы без файла с данными |
| G3 | `hints_text: list[str]`, `hints_video: list[str]`, `has_hints: bool` | CB adapter `_build_hints_block` (VK-разборы Виктора, 1000+ видео) | только `prompt` (single string) | главная ценность Phase 6 (per CB brief) теряется или схлопывается |

### 2.2. Что УЖЕ работает на LMS-стороне (важно — это значит барьер только в import)

- **DB schema:** `tasks.task_content` — jsonb свободной формы, никаких миграций не требуется.
- **Pydantic schema** ([app/schemas/tasks.py](../../app/schemas/tasks.py)): `TaskContent` принимает `hints_text`/`hints_video`/`has_hints` после tsk-088 BLOCKER fix 2026-05-24 (silent drop устранён).
- **SPW frontend** (Y-3 «5 восхищений + HintPanel», Y-6 review-loop) уже рендерит hints списком.
- **TG_LMS bot** (Y-4 teacher dialog, Y-5 student flow) уже отображает hints.

Барьер — **исключительно** в import-сервисе: он строит `task_content` из flat-колонок и не пробрасывает остальные поля.

### 2.3. Текущая реализация import (что менять)

- **Endpoint:** [app/api/v1/tasks_extra.py:569-646](../../app/api/v1/tasks_extra.py) (`POST /api/v1/tasks/import/google-sheets`, handler `import_from_google_sheets`).
- **Google Sheets fetch:** [app/services/google_sheets_service.py](../../app/services/google_sheets_service.py).
- **Row→task transform + bulk_upsert:** [app/services/tasks_service.py](../../app/services/tasks_service.py).
- **Docs:** [docs/import-api-documentation.md](../import-api-documentation.md) (Google-Sheets-колонки), [docs/import-quick-start.md](../import-quick-start.md), [docs/courses-import-manual.md](../courses-import-manual.md).

### 2.4. CB-side готовность

CB tracker tasks (`D:\Work\Root\tasks\tsk-094`, `tsk-095`, `tsk-096`) описывают use-cases с разных сторон; spec этот ЗАКРЫВАЕТ все три одной LMS-правкой (если выбран Вариант A) или mappings'ом (Вариант B).

---

## 3. Границы задачи

### 3.1. Входит

1. Расширение row→task transform: добавить mapping для новых полей.
2. Backward compat: импорты без новых колонок работают как раньше.
3. Validation: invalid JSON / неподходящие типы → row → `errors` list (не падает весь import).
4. Tests: unit + integration на dry_run + apply.
5. Документация: обновить `docs/import-api-documentation.md` + `docs/import-quick-start.md`; обновить cross-project mirror `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md` §Import.

### 3.2. НЕ входит

- Frontend changes (SPW/TG_LMS уже умеют рендерить эти поля).
- File hosting / upload для attached_files: LMS принимает **только пути/URL строками** в `attached_file_paths`. Физическое хранение файлов — отдельный концерн (CB-side или external CDN; см. §6 Open question).
- Изменения схемы БД (jsonb свободной формы).
- Изменения других import-источников (XLSX upload и т.п. — если таковые есть, scope только Google Sheets).

### 3.3. Не трогать

- DB schema `tasks.task_content` (jsonb остаётся как есть).
- Существующие flat-колонки (external_uid, type, stem, correct_answer, options, max_score, и т.д.) — поведение не меняется.
- Endpoint URL `POST /api/v1/tasks/import/google-sheets` — не меняется.

---

## 4. Стек и ограничения

- Python 3.10+, FastAPI, SQLAlchemy 2.x, Pydantic v2.
- Type hints + docstrings (стиль LMS).
- mypy clean per существующим module-стандартам LMS.
- Backward compat: без новых колонок — import-API возвращает идентичные результаты (regression test).

---

## 5. Обязательные скиллы/правила

- `/fastapi-api-developer` — реализация (LMS-side import service + tests).
- `/techlead-code-reviewer` — ревью (contract change в публичном import-API + Pydantic validation путь).
- `/review-gate` — финальный PASS/FAIL перед merge.
- `/db-check` — НЕ требуется (без миграций).

---

## 6. Архитектурная развилка — выбор формата

**Два варианта:** Вариант A (рекомендуется) — passthrough; Вариант B — dedicated columns. **Выбор за LMS-командой** на основании внутренних security/audit/maintainability соображений. Spec специфицирует оба.

### 6.1. Вариант A (RECOMMENDED) — passthrough `task_content_json`

**Идея:** одна опциональная колонка `task_content_json` со строкой JSON; после построения task_content из flat-колонок — **shallow merge** значений из JSON поверх.

**Контракт колонки:**
- Имя: `task_content_json` (автомаппинг в [tasks_service.py](../../app/services/tasks_service.py) column_mapping).
- Тип: строка с JSON.
- Семантика merge: `task_content_built_from_flat = {...}; task_content_extra = json.loads(value); task_content_final = {**task_content_built_from_flat, **task_content_extra}` — **ключи из JSON перекрывают/добавляются**.
- Validation:
  - Пустая ячейка / пустая строка → ignore (НЕ ошибка).
  - Не парсится JSON → row→`errors` с `error: "task_content_json_invalid: <reason>"`.
  - JSON-значение не dict (например, list/int/string) → row→`errors` с `error: "task_content_json_not_object"`.
  - JSON-dict без невалидных типов внутри → принимается даже с unknown ключами (для future-proofness; ключи попадают в jsonb как есть).
- Backward compat: колонка отсутствует / пуста → текущее поведение точно сохраняется.

**Плюсы:**
1. Закрывает G1+G2+G3 одной правкой LMS, минимальный change.
2. Future-proof: CB добавляет новые поля без новых LMS-правок.
3. LMS Pydantic schema (`TaskContent` после tsk-088 fix) уже принимает hints_text/video; для unknown ключей jsonb приемлемо.
4. Backward compat тривиален (no-op если колонка пуста / отсутствует).

**Минусы:**
1. Меньше строгости типов на уровне Google Sheets (методист может вписать невалидный JSON, выяснит только через `errors` list).
2. Operator-friendly меньше: ячейка содержит «непрозрачный» JSON-blob вместо человеко-читаемых отдельных колонок.

### 6.2. Вариант B (FALLBACK) — 4 dedicated columns

**Идея:** добавить 4 типизированные опциональные колонки.

| Колонка | Тип | Формат | Маппится в `task_content` |
|---|---|---|---|
| `stem_images` | строка | `URL1 \| URL2 \| URL3` (pipe-delimited как `options`) | `stem_images: list[str]` |
| `attached_files` | строка | `path1 \| path2` | `attached_file_paths: list[str]` + derived `has_attached_file: True` если непустой |
| `hints_text` | строка | `text1 \| text2` | `hints_text: list[str]` |
| `hints_video` | строка | `https://vk.com/A \| https://vk.com/B` | `hints_video: list[str]` |

После их парсинга — derived `has_hints = bool(hints_text or hints_video)`.

**Плюсы:**
1. Operator-friendly: методист видит «колонка для картинок», «колонка для подсказок» — понятнее JSON-blob'а.
2. Строгая типизация на уровне формата (LMS-side parser ловит format-ошибки точечно).

**Минусы:**
1. 4 правки маппинга вместо 1.
2. Не future-proof: каждое новое поле `task_content` → новая колонка → новая LMS-правка.
3. Pipe-разделитель может конфликтовать с содержимым (URL с `|` редко, но возможно; нужен escape rule).

### 6.3. Рекомендация

**Вариант A** (passthrough). Future-proof важнее эстетики ячейки; CB-side exporter формирует JSON автоматически (методист правит только то, что хочет переопределить — обычно `stem_text`/`stem_override` уже работающее, а task_content_json остаётся как сгенерировал CB).

LMS-команда: если security audit / column hygiene важнее — выбрать B; spec покрывает оба пути.

---

## 7. Шаги реализации

> Все шаги — для одного из двух выбранных вариантов. LMS-команда выбирает A или B перед стартом.

### 7.1. Вариант A — Шаги реализации

**Шаг A.1. Расширить column_mapping в google-sheets-import парсере**
**Файл:** [app/services/tasks_service.py](../../app/services/tasks_service.py) (функция, делающая row→task transform; точное название определит /fastapi-api-developer по существующему коду).
**Исполнитель:** /fastapi-api-developer
- Добавить в auto-mapping таблицу название `task_content_json` (и опциональные русские синонимы — `task_content json`, `json`, по аналогии с другими колонками).
- В transform: `row.get("task_content_json")` → если non-empty → `json.loads` с try/except.

**Шаг A.2. Merge-логика в построении `task_content`**
**Файл:** [app/services/tasks_service.py](../../app/services/tasks_service.py).
**Исполнитель:** /fastapi-api-developer
**Ревью:** /techlead-code-reviewer (contract path — публичный import schema)
- После построения `task_content_dict` из flat-колонок:
  ```python
  if extra_json_value:
      try:
          extra = json.loads(extra_json_value)
      except json.JSONDecodeError as e:
          # row → errors, не вешать весь import
          ...
      if not isinstance(extra, dict):
          # row → errors с error="task_content_json_not_object"
          ...
      task_content_dict = {**task_content_dict, **extra}  # shallow merge
  ```
- При выявлении ошибки — добавить запись в response `errors` list (как сейчас делается для других validation-ошибок) и **продолжить** import остальных строк (не валить весь batch).

**Шаг A.3. Documentation update**
**Файл:** [docs/import-api-documentation.md](../import-api-documentation.md) + [docs/import-quick-start.md](../import-quick-start.md).
**Исполнитель:** /fastapi-api-developer
- В таблицу «Опциональные колонки» добавить `task_content_json` с описанием семантики (passthrough + shallow merge + example).
- Пример строки с `task_content_json='{"hints_video": ["https://vk.com/video/A"], "stem_images": ["https://cdn.../graph.png"]}'`.

**Шаг A.4. Cross-project mirror update**
**Файл:** [D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md](../../../ContentBackbone/docs/cross-project/contracts/lms-api.md) §Import.
**Исполнитель:** /fastapi-api-developer (LMS-команда, тот же коммит — cross-project policy)
- Описать новую колонку + ссылку на этот spec.
- Запись в [CHANGELOG.md](../../../ContentBackbone/docs/cross-project/CHANGELOG.md) (в начало) с пометкой «LMS Import — task_content_json passthrough column added».

**Шаг A.5. Tests**
**Исполнитель:** /fastapi-api-developer
- Unit-тесты (`tests/test_tasks_import_service.py` или эквивалент):
  - T-A1: row с `task_content_json='{"hints_video": ["url1", "url2"]}'` → `task_content.hints_video == ["url1", "url2"]`.
  - T-A2: row с `task_content_json='{"stem_images": ["png1"], "attached_file_paths": ["f.ods"], "has_attached_file": true}'` → все 3 ключа в `task_content`.
  - T-A3: row с `task_content_json='not-json'` → `errors` содержит запись с `error` начинающимся на `"task_content_json_invalid"`; остальные rows не падают.
  - T-A4: row с `task_content_json='["array"]'` → `errors` содержит `"task_content_json_not_object"`.
  - T-A5: row БЕЗ колонки `task_content_json` → `task_content` строится как раньше (backward compat regression).
  - T-A6: row с `task_content_json=''` (пустая ячейка) → не ошибка, поведение как T-A5.
  - T-A7: merge-семантика — `task_content_json` с ключом `prompt` перекрывает значение из flat-колонки `prompt` (документировать в spec).
- Integration test: dry_run + apply через реальный Google Sheets endpoint с тестовым sheet'ом (если live smoke практикуется в LMS).

### 7.2. Вариант B — Шаги реализации

**Шаг B.1. Auto-mapping 4 колонок** — [tasks_service.py](../../app/services/tasks_service.py).
**Исполнитель:** /fastapi-api-developer
- Добавить: `stem_images`, `attached_files`, `hints_text`, `hints_video` (+ русские синонимы при желании).

**Шаг B.2. Parse pipe-delimited values**
**Исполнитель:** /fastapi-api-developer
**Ревью:** /techlead-code-reviewer
- Helper `_split_pipe_list(value: str) -> list[str]` — split по `|`, strip, drop empty.
- Mapping:
  - `stem_images` → `task_content["stem_images"] = _split_pipe_list(...)` если non-empty.
  - `attached_files` → `attached_file_paths` + derived `has_attached_file = bool(attached_file_paths)`.
  - `hints_text`, `hints_video` → одноимённые ключи; derived `has_hints = bool(hints_text or hints_video)`.

**Шаг B.3. Documentation** — то же что A.3, но описать 4 колонки.

**Шаг B.4. Cross-project mirror** — то же что A.4.

**Шаг B.5. Tests** — аналогично A.5, но 4 раздельных кейса на каждую колонку + edge-case empty pipe (`|`) + escape-rule если введёте.

---

## 8. Контракт навигации

Не применимо — endpoint URL, request body schema, response shape не меняются (только дополнительные опциональные колонки внутри Google Sheets, читаемые import-сервисом).

---

## 9. Запрещённые элементы управления

- **НЕ** менять обязательность существующих flat-колонок (external_uid/type/stem/correct_answer остаются обязательными).
- **НЕ** валить весь import batch при невалидном JSON / format одной строки — row → `errors`, остальные продолжают.
- **НЕ** делать deep-merge (вложенные ключи) в Варианте A — только shallow (top-level merge). Это упрощает семантику и debugging.
- **НЕ** менять DB schema `tasks.task_content` — jsonb остаётся свободной формы.

---

## 10. Критерии приёмки

| # | Критерий | Команда проверки |
|---|---|---|
| AC-1 | Endpoint `POST /api/v1/tasks/import/google-sheets` принимает sheet с выбранным расширением колонок и возвращает 200 + `imported`/`updated` > 0 | Live smoke на тестовом sheet (dev LMS) |
| AC-2 (A) | row с `task_content_json='{"hints_video": ["url"]}'` → запрос `GET /api/v1/tasks/{task_id}` показывает `task_content.hints_video == ["url"]` | pytest + DB-query или API smoke |
| AC-2 (B) | row с `hints_video="url1 \| url2"` → `task_content.hints_video == ["url1", "url2"]` | pytest |
| AC-3 | Invalid JSON (A) или invalid format (B) → row в `errors` list с описательным error message; остальные rows импортированы успешно | pytest unit |
| AC-4 | Backward compat: import без новых колонок производит идентичный результат (то же `task_content`) что и до изменений | regression-test на frozen sheet/fixture |
| AC-5 | mypy LMS clean (existing baseline); pytest LMS все зелёные (existing + новые) | `mypy app/services/tasks_service.py app/api/v1/tasks_extra.py`; `pytest tests/` |
| AC-6 | `docs/import-api-documentation.md` + `docs/import-quick-start.md` обновлены с примером | review markdown |
| AC-7 | Cross-project mirror `lms-api.md` §Import + CHANGELOG entry обновлены в том же merge | review changeset |

---

## 11. Команды проверки

```powershell
# Type-check
python -m mypy app/services/tasks_service.py app/api/v1/tasks_extra.py

# Unit + regression
python -m pytest tests/ -k "import" -v

# Live smoke (LMS dev)
# 1. Создать Google Sheet с минимум 3 rows: один без task_content_json (или dedicated cols), один с валидным JSON/cols, один с invalid
# 2. Запустить:
curl -X POST "http://localhost:8000/api/v1/tasks/import/google-sheets?api_key=bot-key-1" `
  -H "Content-Type: application/json" `
  -d '{"spreadsheet_url": "<URL>", "sheet_name": "Tasks", "course_code": "PY", "difficulty_code": "NORMAL", "dry_run": false}'
# 3. Проверить response: imported >= 2, errors содержит 1 запись с описанием invalid.
# 4. SQL: SELECT task_content FROM tasks WHERE external_uid=<тестовый> — содержит ожидаемые ключи.
```

---

## 12. Артефакты review-gate

- `reviews/2026-05-NN-lms-import-task-content-ext.md` + `reviews/2026-05-NN-lms-import-task-content-ext.diff`.
- `reviews/evidence/2026-05-NN-import-task-content-ext-pytest.log`.
- `reviews/evidence/2026-05-NN-import-task-content-ext-smoke.json` (response с тестового sheet).
- `reviews/evidence/2026-05-NN-import-task-content-ext-db-verify.sql` (SQL-проверка `task_content` после apply).
- Rollback note: `reviews/rollback_import_task_content_ext_2026-05-NN.md`.

---

## 13. Переиспользование общей инфраструктуры

- `GoogleSheetsService` (existing): без изменений в чтении (колонки auto-detect'атся уже существующим механизмом).
- `tasks_service.bulk_upsert`: без изменений в DB-call (jsonb принимает любые ключи).
- `TaskContent` Pydantic schema (`app/schemas/tasks.py`): без изменений — расширения task_content_json валидно проходят (по факту fix tsk-088 BLOCKER 2026-05-24).
- Error-handling pattern: используется существующий `errors: list[ImportRowError]` контракт response.

Никаких новых зависимостей в `requirements.txt`.

---

## 14. Артефакты передачи

- Этот tech-spec (LMS-side).
- После реализации: cross-project CHANGELOG entry + `lms-api.md` §Import update в том же merge.
- Финальный `/review-gate` отчёт.
- CB-side: оператор возвращается в CB-сессию для реализации `monolith/external_tasks/publisher/lms_import_file.py` (CB-side exporter, отдельная задача `/executor-pro`).

---

## 15. Concurrency & Idempotency

- Concurrency: import-handler уже single-process per request (FastAPI default), Google Sheets fetch блокирующий — не меняется.
- Idempotency: повторный import с тем же `external_uid` сейчас обновляет задачу (`updated` counter, см. import-api-documentation.md FAQ). Это поведение не меняется. Если `task_content_json` (или dedicated columns) содержит другие значения — следующий import заменит `task_content` в БД (по той же логике `_merge` или `replace`, как сейчас в `tasks_service`). LMS-команда: подтвердить семантику (replace vs deep-merge `task_content` при update'е существующей задачи) — это **open question** ниже.

---

## 16. Open Question для LMS-команды

**Q1.** При update существующей задачи (тот же `external_uid` повторно импортируется): `task_content` в БД **полностью заменяется** новым из import-row, или **deep-merge'ится** (только ключи из нового перезаписывают, остальные остаются)? Текущее поведение нужно подтвердить и зафиксировать в spec явно — это влияет на operator-flow (CB сгенерирует, methodist правит → re-import → не теряет ли правки методиста из ручного редактирования). Рекомендация CB: replace (atomic) — операторски проще; deep-merge скрывает изменения. Но решение за LMS.

**Q2 (только для Варианта B).** Pipe-разделитель `|` — нужен ли escape-mechanism для содержимого, где `|` встречается естественно (например, в URL с query)? Если да — какой escape (двойной `||`? backslash?). Рекомендация: при выборе B — добавить escape rule в spec явно.

**Q3 (только для Варианта A).** Размер `task_content_json` (длина JSON-строки): есть ли лимит, после которого Google Sheets cell усекается или import отваливается? Тестовый sheet может содержать длинный JSON (например, hints_video с 5 URL × 100 символов). Рекомендация: документировать лимит (если есть) или явно сказать «no limit beyond Google Sheets cell limit».

---

## 17. Preflight / Deployment Checklist

1. Тестовый Google Sheet с пробными рядами доступен (создаст LMS-разработчик в dev/test workspace).
2. `secrets/google_sheets_credentials.json` (Service Account) доступен в LMS env — без изменений.
3. LMS dev DB (alembic head без новой миграции) — без изменений.
4. mypy LMS baseline (pre-change) — зелёный, чтобы регресс был очевиден.
5. pytest LMS baseline — зелёный.
6. `task_content_json` не должен случайно конфликтовать с существующей колонкой `task_content` (если такая используется внутри Google Sheets — маловероятно, но проверить auto-mapping).

---

## 18. Stage Dependency Graph

```
Шаг 7.1.1 (column mapping)
   ↓
Шаг 7.1.2 (merge logic) ── BLOCKED_BY 7.1.1
   ↓
Шаг 7.1.3 (docs update) ── параллельно с 7.1.4
Шаг 7.1.4 (cross-project mirror) ── параллельно с 7.1.3
   ↓
Шаг 7.1.5 (tests) ── BLOCKED_BY 7.1.2
   ↓
review-gate ── BLOCKED_BY 7.1.5
```

(Для Варианта B — аналогично, нумерация B.1→B.5.)

---

## 19. Риски и откат

| # | Риск | Митигация |
|---|---|---|
| R1 | Вариант A: JSON-injection в `task_content` (LMS-side jsonb принимает любые ключи) — может ли вредоносный JSON ломать frontend? | Frontend (SPW/TG_LMS) уже хардит на specific ключи (hints_text/video/stem_images/etc); unknown ключи в jsonb игнорируются consumer'ами. **Дополнительно:** на LMS-стороне можно whitelist'ить ключи (например, отбрасывать ключи не из allowed set) — это дополнительная защита; LMS-команда решает по audit-policy. |
| R2 | Вариант A: methodist случайно вписывает huge JSON → DB row слишком большой | jsonb имеет лимит ~1GB, не препятствие; но для UX cell в Google Sheets имеет limit ~50000 chars — обычно достаточно. Документировать лимит. |
| R3 | Q1 (replace vs merge при update) — несовместимое поведение для operator-flow | Закрывается ответом LMS-команды; обновить spec до start реализации. |
| R4 | Backward compat regression: existing imports начинают вести себя по-другому | AC-4 регресс-тест обязателен; rollback план — revert PR. |

**Rollback:** `git revert <PR-merge-sha>` — атомарно. Колонки опциональные → existing imports без колонок продолжат работать. CB-side не зависит от этой правки до тех пор, пока CB не реализует exporter `lms_import_file.py` (тот будет в отдельной CB-задаче после LMS merge).

---

## 20. Operator handoff

После merge LMS:
1. Оператор передаёт CB-команде «task_content extensions готово, формат — Вариант A (или B)» через TG/чат.
2. Оператор возвращается в CB-сессию (та, которая сейчас на паузе).
3. CB запускает `/executor-pro` на реализацию `monolith/external_tasks/publisher/lms_import_file.py` — сериализация `_build_task_content` в выбранный формат + xlsx-генератор.
4. После CB-side exporter готов → operator dry-run import на 10 polyakov-задач (Фаза III родительского change-plan) → если PASS → продолжение readiness gate'а tsk-004 Phase 6.7.

---

## 21. Связанные CB-tasks (для контекста)

- `tsk-094` (Root) — LMS-IMG: support images in stem.
- `tsk-095` (Root) — LMS-ATTACH: support attached_file_paths for SA_COM.
- `tsk-096` (Root) — LMS-HINTS-MULTI: support list hints_text/hints_video.

**Все три закрываются одной LMS-правкой при Варианте A**, или mapping'ом 4 колонок при Варианте B. После merge — закрыть все три tracker-task'и одним commit message reference'ом.
