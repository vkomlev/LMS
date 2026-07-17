# tsk-278 — Аудит `\b`/`\B` в регулярках, исполняемых PostgreSQL

Дата: 2026-07-17 · Скилл: `/db-check` (данные) · Класс: regex-dialect-mismatch (follow-up tsk-261/262)

## Контекст

В PostgreSQL ARE `\b` — это **backspace**, а не граница слова (граница — `\y`, начало `\m`,
конец `\M`). Регулярка в синтаксисе Python/PCRE, переданная в PG (`~`, `~*`, `!~`,
`regexp_*`, `SIMILAR TO`, `substring(... from ...)`), **не падает** — ветка с `\b` молча
мертва. Известный уже устранённый случай — `CODE_RX` в `fix_code_answer_lower_tsk261.py`
(tsk-261 `83bdefb` + tsk-262 AST-флаг). Задача tsk-278 — найти **остальные** такие же.

## Метод

1. Полный список литералов `\b`/`\B` по `.py`/`.sql` трёх деревьев (LMS, ContentBackbone, TG_LMS).
2. Отдельно — все PG-regex-операторы (`~`, `~*`, `!~`, `regexp_match/replace/matches`,
   `SIMILAR TO`, `~ $N`).
3. **Кросс-сверка:** дефект = файл, где встречаются ОБА сигнала И `\b` уходит в SQL-строку.
   Голый grep путает PG-`\b`, Python-`re`-`\b` и `\b` в комментариях — разбор глазами.
4. Живой ущерб подтверждён на **реальных данных прода** (read-only, MCP `learn_prod_db`),
   не чтением кода.

## Находки и вердикты

| Проект | Что найдено | Вердикт |
|---|---|---|
| LMS `app/` | PG-regex-операторов нет вообще; `\b` нет | Чисто |
| LMS `scripts/` | `CODE_RX` с `\bprint\b\|\bimport\b\|\bdef\b` в `fix_code_answer_lower_tsk261.py` и `measure_ast_normalization_tsk262.py` | Известный дефект, историч., задокументирован, данные починены. Не трогаю |
| LMS `scripts/` (прочие PG-regex) | `fix_source_link_leak`, `strip_wp_nav_block`, `fix_qsm_shortcode_a3` — `\s`, `\.`, `href="/[a-z]`, alternation | Валидный PG-синтаксис, `\b` нет. Чисто |
| LMS `scripts/` (прочие `\b`) | `fix_stem_markdown_*`, `fix_source_link_leak`, `strip_wp_nav_block` — `re.compile(...)` | Python `re`, корректно |
| ContentBackbone | 18 файлов с `\b`; 6 файлов с PG-regex. Пересечение — только `wp_nav_import.py`, и там `\b` в `re.compile` (level-паттерны), а SQL `~* 'zadanie\|...'` без `\b` | Чисто. Все PG-regex CB (`\s`, `\.`, `^[0-9]+$`, `<[^>]+>`) валидны |
| TG_LMS | Ни `\b`, ни PG-regex-операторов | Чисто |

**Итог аудита: класс единичный.** Других PG-исполняемых регулярок с `\b`/`\B` в трёх
проектах нет. Systemic-фикса не требуется.

## Остаточный ущерб на данных (следствие той же мёртвой ветки)

Прод-запрос активных заданий с `lower` + код-эталоном выявил задания, которые мёртвая
ветка должна была поймать, но пропустила. tsk-262 добрал их AST-классификатором, но
**неполные сниппеты** (`ast.parse` не разбирает) остались с `lower` → ложный зачёт:

| id | эталон | ущерб |
|---|---|---|
| 5527 | `for jivotnoe in spisok:` | «напиши код сам» → `FOR ... IN ...:` засчитан |
| 5613 | `return rezultat` | `RETURN REZULTAT` засчитан |
| 5636 | `return rezultat` | то же |
| 5768 | `while True:` | `while true:` засчитан (а `true` — не Python) |

Оставлены сознательно (решение оператора, линия tsk-262): «назови слово маленькими
буквами» — 8626 `print`, 6124 `def`, 8653 `return`; спорный keyword-fill — 5463/5567
`elif`, 6222 `for`, 6224 `input`.

## Правка данных

- Скрипт `scripts/fix_code_lower_incomplete_tsk278.py` (dry-run по умолчанию; `--apply` под `DBCHECK_OK=1`).
- Снят **только** шаг `lower` (порядок и остальные шаги сохранены). `code_ast` НЕ добавлялся:
  по движку `app/services/checking_service.py::_matches_short_answer` он — лишь доп. путь к
  зачёту и для непарсящихся сниппетов инертен; ложный зачёт чинит именно регистрозависимое
  текстовое сравнение после снятия `lower`.
- Верификация read-only после apply: у 5527/5613/5636/5768 `lower` отсутствует, `code_ast` не появился.

## Guard (решение)

**Не заводить** (anti-bloat). Находок `\b`-в-PG вне известной `CODE_RX` — ноль. Grep-правило
«`\b` в строке для PG» шумело бы на десятках легитимных Python-`re` с `\b` (парсеры HTML/PDF)
при нулевой ценности для единичного исторического случая. Запись в `docs/ai/ERRORS.md`
(2026-07-17, tsk-278) достаточна.

## Изменённые файлы

- `scripts/fix_code_lower_incomplete_tsk278.py` — новый разовый фикс-скрипт данных.
- `docs/ai/ERRORS.md` — prevention-запись класса (строка tsk-278).
- Данные прода `learn.tasks`: снят `lower` у id 5527/5613/5636/5768.

## Риски / follow-up

- Продуктовый код (`checking_service`, `app/`) не менялся — деплой/`/review-gate` не требуются.
- Спорные keyword-задания (elif/for/input/print/def/return) — вне scope tsk-278 по решению оператора.
