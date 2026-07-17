# tsk-267 — колонка `normalization` в шаблоне Sheets-импорта

**Дата:** 2026-07-17
**Скилл:** /fastapi-api-developer
**Data Impact:** none (schema БД и миграции не затронуты; меняется только парсинг импорта)

## Контекст

`sheets_parser_service.py:215` хардкодил `normalization=["trim","lower"]` для ЛЮБОГО
SA/SA_COM из Google Sheets. Для задания с ответом-кодом неверно дважды: без `code_ast`
валидный Python с лишним пробелом (`print(slovo .lower())`) ловит ложный незачёт, а
`lower` вдобавок засчитывает `IMPORT RANDOM` за `import random` (жалоба QA, разово
закрытая data-скриптом в tsk-261). Источник — импорт, поэтому чиним импорт, а не данные.

Парная к tsk-262 (движок `code_ast`, сделано), tsk-265 (`/methodist` выдаёт
`normalization` в JSON, сделано), tsk-266 (ContentBackbone).

## Реализация

Вид ответа объявляет автор — колонка `normalization` в листе `Tasks`. Автодетект «код ли
это» не делается принципиально (`тест-кейс` = вычитание имён, `example.ru` = обращение к
атрибуту).

**`app/services/sheets_parser_service.py`**
- Импорт `NormalizationStep` из схемы; константы `CODE_NORMALIZATION`,
  `TEXT_NORMALIZATION`, `DEFAULT_NORMALIZATION`, пресеты-алиасы, множество допустимых шагов.
- Новый метод `_parse_normalization(row, column_mapping)`: алиас `code`/`код`/`text`/`текст`,
  либо явный список через `,`/`|` из закрытого словаря, либо дефолт при пустой колонке.
  Опечатка/пустое значение → `DomainError(normalization_invalid, 400)`.
- Хардкод `["trim","lower"]` заменён вызовом метода; в дефолтный маппинг добавлена колонка.

**`app/api/v1/tasks_extra.py`**
- `normalization` в known_fields и в авто-распознавании заголовков (`normalization` /
  `нормализация`).
- Описание эндпоинта дополнено правилом выбора.

**`docs/openapi.json`** — перегенерирован (описание эндпоинта).

**`tests/test_sheets_normalization_tsk267.py`** — новый файл, 11 тестов.
**`tests/generate_tasks_import_xlsx.py`** — образец шаблона получил колонку.

## Решение по дефолту (осознанное)

Пустая колонка → `["trim","lower"]` — **точное** поведение импорта до tsk-267, НЕ
четырёхшаговый текстовый набор из § 4b. Причина: ре-импорт таблицы перезаписывает
`solution_rules`, и щедрый дефолт вернул бы `strip_punctuation` заданиям, у которых его
точечно сняли в tsk-218 (SA с двоеточием в эталоне). Молчание колонки = «как было», а не
«ответ — текст». Кто хочет текстовый набор — объявляет `text` явно.

## Валидация (acceptance)

| Критерий | Итог |
|---|---|
| Пустая колонка / колонки нет → `["trim","lower"]` (не ломать таблицы) | PASS (`test_empty_column_*`) |
| `code`/`код` → `[trim, strip_punctuation, collapse_spaces, code_ast]` без `lower` | PASS |
| `text`/`текст` → четыре текстовых шага | PASS |
| Явный список (`trim, code_ast`), порядок автора сохраняется | PASS |
| Опечатка (`code-ast`) → строка отклонена, а не дефолт | PASS (`test_typo_rejects_row`) |
| Признак «код» не выводится регуляркой — ставится автором | PASS (реализация: колонка, не эвристика) |
| Словарь закрыт, опечатка → ошибка | PASS |

Прогон: `pytest tests/test_sheets_normalization_tsk267.py tests/test_tasks_import_task_content_json.py tests/test_checking_code_ast_tsk262.py tests/test_requires_attachment_gate_tsk227.py` → **58 passed**.

## Cross-project

- `~/.claude/skills/methodist/references/lms-wp-export.md` § 1.2 — колонка добавлена в
  раскладку листа Tasks (захват clm-252).
- `ContentBackbone/docs/cross-project/contracts/lms-api.md` — раздел «Импорт из Sheets».
- `ContentBackbone/docs/cross-project/CHANGELOG.md` — запись tsk-267.
- STATE.md не трогался: фаза/версия LMS не сменилась.

## Live smoke (реальный Google Sheets, 2026-07-17)

Оператор дал `GSHEETS_SERVICE_ACCOUNT_JSON` (.env) и тестовую таблицу
`16xdksyZnll09VQ5tGnwSEFCtiK4f9wF2tTxW14GKZjA`. Прогон через реальный Sheets API,
лист `Tasks` (19 строк), без записи в БД.

1. **Обратная совместимость — на живых данных.** У листа `Tasks` колонки `normalization`
   нет. Все SA/SA_COM разобраны с `["trim","lower"]` (прежнее поведение), 0 отклонений,
   19 строк OK. Молчащая таблица не сломана — главный критерий.
2. **Заполненная колонка — на реальных строках таблицы.** Service Account имеет scope
   `spreadsheets.readonly` → в сам документ писать нельзя (и не нужно). Взял реальные строки
   с сервера Google и подставил колонку в память. `build_mapping` эндпоинта **распознал**
   заголовок `normalization`; парсер отработал 6/6:
   - `text` → `[trim, lower, strip_punctuation, collapse_spaces]`
   - `code` / кириллица `код` → `[trim, strip_punctuation, collapse_spaces, code_ast]` (без `lower`)
   - явный список `trim, code_ast` → как указано
   - опечатка `code-ast` → строка **отклонена** (`normalization_invalid`)

## Риски / follow-up

- Полностью сквозной прогон именно из ЯЧЕЕК Google (заполненная колонка прямо в документе
  → HTTP-эндпоинт → dry_run) не выполнялся: для этого нужна запись в тестовую таблицу
  (внешний документ + текущий scope SA только на чтение) — ветвь оператора. Путь
  распознавания заголовка и парсинг проверены на реальных строках с сервера (см. выше).
- Генератор XLSX локально не запущен (`openpyxl` не установлен в .venv); правка образца
  проверена по синтаксису и арифметике колонок.
