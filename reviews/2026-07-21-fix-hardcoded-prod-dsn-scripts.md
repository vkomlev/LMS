# Убрать хардкод прод-DSN/пароля из scripts/ (tsk-357)

## Контекст

Оператор указал на `grep -rl "5.42.107.253" scripts/` — 28 файлов упоминают прод-хост,
что нарушало бы правило CLAUDE.md «Secrets только в env-переменных, никогда в коде».

## Находка

Реальный масштаб меньше, чем grep по хосту. 25 из 28 файлов — **guard-паттерн**:
проверяют, что уже полученный из `LEARN_PROD_DSN`/`.mcp.json` DSN действительно указывает
на прод (`if "5.42.107.253" not in dsn: raise ...`), защита от случайной записи в dev.
Секрет там не хардкодится.

Реальный пароль роли `lms_prod` был захардкожен буквально в 3 файлах:

| Файл | Что было |
|---|---|
| `scripts/backfill_close_stale_blocked_limit_tsk339.py:20` | полная DSN-строка (asyncpg) |
| `scripts/fix_stem_markdown_bold_tsk212.py:61` | `password=` в словаре `psycopg2.connect(**PROD)` |
| `scripts/fix_stem_markdown_italic_tsk215.py:65` | то же |

## Правка

- `backfill_close_stale_blocked_limit_tsk339.py` — DSN только из `LEARN_PROD_DSN`
  (существующая конвенция репозитория, уже используется в 9 других файлах scripts/).
- `fix_stem_markdown_bold_tsk212.py`, `fix_stem_markdown_italic_tsk215.py` — пароль только
  из новой `LEARN_PROD_DB_PASSWORD` (host/port/dbname/user остаются явными — это не секреты).
- Во всех трёх — явный `RuntimeError` с понятным текстом, если переменная не задана.

`.mcp.json` не тронут: конфиг MCP-сервера, не отслеживается git (подтверждено
`git ls-files --error-unmatch .mcp.json` → not found; строка 51 в `.gitignore`).

## Validation Commands

```bash
grep -rl "5.42.107.253" scripts/          # 28 файлов, из них 25 — guard-паттерн (без секрета)
grep -rn "password=\|1MVd16z" scripts/*.py  # после правки — 0 совпадений
grep -rl "1MVd16z" --include="*.py" --include="*.md" --include="*.json" --include="*.txt" .
  # единственное совпадение вне scripts/ — .mcp.json (не в git)
python -c "import ast; ast.parse(open(f, encoding='utf-8').read())"  # синтаксис 3 файлов — OK
python scripts/backfill_close_stale_blocked_limit_tsk339.py   # без env -> RuntimeError (ожидаемо)
python scripts/fix_stem_markdown_bold_tsk212.py                # без env -> RuntimeError (ожидаемо)
python scripts/fix_stem_markdown_italic_tsk215.py              # без env -> RuntimeError (ожидаемо)
```

## DB Findings

Не выполнялось — задача не трогает данные, только код чтения секрета. Живых
подключений к БД в рамках этой правки не делалось.

## Risks / Follow-ups

- **Пароль роли `lms_prod` уже был закоммичен в git-историю** (эти же 3 файла попадали в
  коммиты с хардкодом) и переиспользуется в `.mcp.json`. Ротация — вне полномочий агента
  (прод-доступ/DBA-операция), рекомендация оператору: сменить пароль роли `lms_prod` на
  проде и обновить его в `.mcp.json` + переменные окружения `LEARN_PROD_DSN` /
  `LEARN_PROD_DB_PASSWORD` там, где они выставляются (локальный shell/CI) — иначе скрипты
  перестанут подключаться сразу после ротации.
- Задача зафиксирована в трекере: [tsk-357](../../Root/tasks/tsk-357-ubrat-khardkod-prod-dsn-parolya-iz-scripts-lms.md)
  (код-часть закрыта, ротация пароля — открытый пункт за оператором).
- Изменения не закоммичены — ждут решения оператора (правило: коммит только по явному запросу).
