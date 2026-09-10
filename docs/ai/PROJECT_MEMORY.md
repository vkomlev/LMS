# Project Memory

Project: LMS API
Path: `d:\Work\LMS`
Created: 2026-05-26
Profile updated: 2026-05-26

## Purpose

- Responsible for the core LMS REST API: users/roles, courses, materials, assignments, attempts/results, help requests, imports, auth/session support, and API contracts for downstream clients.
- Not responsible for Telegram bot UX, SPW frontend rendering, content strategy, or content pipeline orchestration; those live in TG_LMS, SPW, ContentFactory, and ContentBackbone.

## AI-Facing Profile

- Stack: Python 3.10+, FastAPI, Pydantic v2, SQLAlchemy 2.x async, Alembic, PostgreSQL, Redis where enabled, pytest.
- Main entry: `python run.py` starts Uvicorn on `http://localhost:8000`.
- API docs: `/docs`, `/redoc`, and tracked `docs/openapi.json` when present.
- Auth: API key via `api_key` query parameter from `VALID_API_KEYS`; never hardcode or document real keys.
- Database changes: Alembic only. Trigger logic belongs in migrations, not service code.

## Durable Context

- LMS is a shared upstream contract for TG_LMS and SPW. Endpoint names, response schemas, auth behavior, and OpenAPI drift must be treated as cross-project risks.
- Google Sheets import and LMS content imports may depend on secrets under `.env` or `secrets/`; do not expose values.
- Historical issue pattern: implementation changed endpoint names/schemas without updating specs/ADR, causing downstream 404/schema failures.

## Commands

- Setup: `python -m venv .venv`; `.venv\Scripts\activate`; `pip install -r requirements.txt`.
- DB migrate: `alembic upgrade head`.
- Run locally: `python run.py`.
- Test: `pytest tests/`.
- Focused smoke: `curl http://localhost:8000/health`; `curl "http://localhost:8000/api/v1/users/?api_key=<dev-key>"`.
- Migration create: `alembic revision --autogenerate -m "<message>"`.

### Канонический префикс команды (Windows, Git Bash)

Три факта каждый бьют отдельной ошибкой, если пропущены — собирать одним блоком:

```
PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe <скрипт/модуль>
```

- `.venv/Scripts/python.exe` — интерпретатор Windows-venv (`Scripts`, не `bin`); системный `python` даёт `No module named 'fastapi'`.
- `PYTHONPATH=.` — иначе `No module named 'app'` при запуске скриптов из `scripts/`.
- `PYTHONIOENCODING=utf-8` — иначе mojibake в выводе кириллицы (Windows-консоль).

### Деплой на прод — готовый скрипт, не изобретать свой

`.\deploy\local\deploy-lms.ps1` (или двойной клик `deploy-lms.cmd`) — деплоит `origin/main`
через `ssh -tt lms-spw-vds` на уже настроенную инфраструктуру, без ручного SSH. Требует
закоммиченных и запушенных изменений до запуска. Откат — `deploy/vps/rollback.sh` на
сервере (`ssh lms-spw-vds` → `sudo -u app bash /opt/lms/deploy/vps/rollback.sh`). Детали —
`deploy/local/README.md`.

### Доступ к БД (dev / прод) — не собирать DATABASE_URL вручную

- **MCP-серверы уже настроены** в `.mcp.json`: `learn_public_db` (dev, read-only), `learn_prod_db` (прод, read-only), `content_backbone_db`, `content_backbone_prod_db` — использовать их ПЕРВЫМ делом для чтения, а не пересобирать строку подключения из хоста/пароля.
- Локальный `scripts/connect_db.py` подключается к **dev**-БД из `.env` (`load_dotenv` + `app/db/session`) — быстрый smoke, что БД жива и какие `course_uid` доступны.
- **На прод** формула DSN — только в `docs/ai/operator-runbook.md` (Шаг 3, `DATABASE_URL`); реального пароля нигде в репозитории нет и быть не должно. Любая операция с прод-БД (в т.ч. read вне MCP, любой write/миграция) — через `/db-check`, хук `~/.claude/hooks/db_write_gate.py` блокирует запись без `DBCHECK_OK=1` после реального прохождения `/db-check`.
- Alembic: `alembic current` / `alembic heads` / `alembic upgrade head` / `alembic downgrade -1` — с тем же префиксом выше.

### Прогон тестов идёт в своей временной базе (tsk-872)

`pytest` больше не работает в базе из `.env`. На старте прогона `tests/conftest.py`
создаёт клон базы-шаблона `lms_test_template` (`lms_test_run_<время>_<pid>_<хвост>`),
подменяет `DATABASE_URL` в окружении процесса ДО импорта приложения и удаляет базу
в `pytest_sessionfinish`. Клон идёт по файлам — секунды, а не минуты на миграции.

- Зачем: транзакционная изоляция `tsk-333` защищает от соседнего ТЕСТА, но не от
  соседнего ПРОЦЕССА. 22 модуля из `SELF_MANAGED_CONNECTION_MODULES` пишут
  по-настоящему, `test_migrations.py` гоняет downgrade/upgrade схемы — параллельный
  прогон соседа ронял чужие области (`tsk-868`: 17, следом 7 падений).
- Шаблон создаётся из dev-базы при первом прогоне (нужно, чтобы к ней в этот момент
  не было подключений — dev-сервер выключить) и потом догоняется `alembic upgrade head`
  при смене ревизии. Пересоздать вручную: удалить `lms_test_template`.
- Управление: `LMS_TEST_DB_ISOLATION=auto|on|off` (`auto` по умолчанию — при неудаче
  предупреждает и работает по-старому; `on` — отказать прогону), `LMS_TEST_DB_KEEP=1`
  оставить базу для разбора, `LMS_TEST_TEMPLATE_DB` — другое имя шаблона.
- В шапке прогона печатается строка `tsk-872: …` — в какой базе он идёт. Механизм
  сторожит `tests/test_run_db_isolation_tsk872.py`.
- Следствие: **два полных прогона можно гнать параллельно** — правило ADR-0008
  «полные прогоны одного проекта не параллелить» для LMS снято. Проверено
  2026-09-10 двумя одновременными прогонами: 3521 и 3522 пройденных теста,
  по 22 минуты каждый.
- Чего изоляция базы НЕ закрывает, кроме времени: **каталог загрузок и Redis
  остаются общими на рабочее дерево**. Тест не должен опираться на состояние
  каталога целиком — только на свои файлы: имя чека начинается с id ученика,
  имя вложения попытки — с id попытки, поэтому фильтр `glob(f"{id}_*")` и
  адресация конкретного файла безопасны, а `iterdir()` по всему каталогу —
  нет (tsk-878: чужой файл соседнего прогона уронил проверку «после отказа
  чек не сохранился»).
- Чего изоляция базы НЕ закрывает: тесты, измеряющие ВРЕМЯ. В той же проверке
  `test_tsk621_session_touch_burst.py::test_burst_does_not_serialize_even_when_it_writes`
  упал на пороге 0,4 c (вышло 0,79 c) — два прогона делят процессор и диск;
  в одиночку тот же файл зелёный за 6 секунд. Такое падение при параллельном
  прогоне читать как шум замера, а не как регрессию.

## Required Skills

- Use `fastapi-api-developer` for API feature/debug work.
- Use `db-check` for schema, migration, invariant, or data-sensitive changes.
- Use `context-auditor` when code, OpenAPI, specs, ADR, TG_LMS, or SPW disagree.
- Use `qa-report`/`qa-fix` for endpoint smoke, regression, and QA-driven remediation.
- Use `techlead-code-reviewer` or `lms-fastapi-techlead-code-reviewer` before risky backend integration.
- Use `release-prep` before merge/deploy with migrations, auth/session changes, external writes, or downstream contract impact.

## Architecture Notes

- Core modules: `app/api/v1`, `app/services`, `app/repos`, `app/models`, `app/schemas`, `app/core`, `app/auth`, `app/utils`.
- Data/storage: PostgreSQL via SQLAlchemy; migrations under `app/db/migrations` and `alembic.ini`.
- External services: Google APIs for imports; email/Resend-style services where configured; Redis where enabled.
- Trust boundaries: API keys, session/auth, imported content, file uploads, external service callbacks, and downstream client contracts.

## Known Risks

- Reliability: migrations, triggers, and async DB behavior can break primary API paths.
- Security/privacy: API keys, user identity, session/auth, uploads, Google credentials, and email links are sensitive.
- Data/encoding: Cyrillic docs and imported content require explicit UTF-8 handling.
- Cross-project contract drift: TG_LMS and SPW can fail if OpenAPI/specs are not synced with implemented routes and schemas.

## Smoke Checks

- Health endpoint responds.
- Alembic is at head before DB-dependent work.
- OpenAPI route names match any changed docs/specs.
- For integration work, curl every endpoint named in the task/spec before handoff.
- For auth/session work, verify both success and failure paths without exposing secrets.

## Current Decisions

| Date | Decision | Why | Owner/Source |
| --- | --- | --- | --- |
| 2026-05-26 | Treat OpenAPI/code/spec sync as mandatory for integration changes. | Prevent TG_LMS/SPW contract drift. | IDE_booster error register |

## Prevention Register

| Date | Incident/Risk | Prevention Rule | Related Skill |
| --- | --- | --- | --- |
| 2026-04-28 | Downstream clients hit stale endpoint names/schemas. | If implementation diverges from spec/ADR/OpenAPI, update the contract artifact or record explicit deviation in the same task. | `context-auditor`, `fastapi-api-developer`, `qa-fix` |
| 2026-05-26 | Secrets in local config/docs risk leakage. | Never copy `.env`, API keys, Google credentials, tokens, or session data into docs or final answers. | `encoding-guard`, `project-docs` |

## Handoff Notes

- Current focus: keep LMS as stable upstream API for bot/frontend/content integrations.
- Blockers: live DB, valid dev API key, and external credentials may be needed for full smoke; use placeholders in docs.
- Follow-ups: keep `docs/openapi.json` and integration specs current after endpoint/schema changes.

## Maintenance Rules

- Keep durable facts here; keep transient task notes in session summaries or issue docs.
- Do not store credentials, tokens, cookies, personal secrets, or private keys.
- When implementation intentionally diverges from specs, record the decision and update the relevant specs/docs in the same task.
- Prefer links to canonical docs over duplicating long content.
