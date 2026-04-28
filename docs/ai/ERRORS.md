# Журнал ошибок AI-контуров

## Как использовать
1. Добавляйте запись при каждом значимом промахе/сбое.
2. Заполняйте `Класс ошибки` и `Серьезность`.
3. На еженедельном разборе переносите профилактику в правила/skills/workflows.

## Классы ошибок
- `SPEC`, `CONTEXT`, `LOGIC`, `INTEGRATION`, `DATA`, `TEST`, `SAFETY`, `COST`, `PROCESS`

## Серьезность
- `S1` критично
- `S2` высоко
- `S3` средне
- `S4` низко

## Шаблон записи
| Дата | Проект | Контекст | Симптом | Корневая причина | Класс | Severity | Как обнаружено | Исправление | Профилактика | Статус |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-02-27 | LMS | <task-context> | <symptom> | <root-cause> | LOGIC | S2 | smoke test | <fix> | <prevention> | done |
| 2026-03-03 | LMS | teacher help request detail | 500: `TypeError '<' not supported between 'str' and 'datetime.datetime'` | raw SQL/text date value compared with `now` without normalization/type-guard | DATA | S1 | runtime manual test + logs | normalize date via helper before compare; add service type-guards | update Cursor fastapi agents (normalization + negative tests + type-guards), add FastAPI-specific techlead review checks (raw SQL->types->now, runtime smoke detail/list, reproducer test) | done |
| 2026-04-28 | LMS / SPW | Y-1 auth API contract drift cross-repo | SPW Y-2 spec ссылался на `/auth/magic-link/request`, `GET /consume`, `/auth/logout`. Реализация Y-1: `POST /send`, `POST /verify`, `POST /auth/session/logout`. SPW клиент звал бы 404 на всех auth-путях | executor Y-1 переименовал роуты на идиоматичные глаголы (`/send`,`/verify`) и унифицировал `/session/*` префикс, не обновил Y-1 spec / LMS-0001 ADR / CB-0011 ADR. Y-2 spec писался от устаревших артефактов. Review-gate в первом проходе классифицировал drift как non-blocking, считая что нет downstream-потребителей (но Y-2 spec уже существовал в CB) | INTEGRATION | S2 | RCA после анализа Y-2 spec | путь A: LMS spec §6.1-6.5 + ADR-0001 §«Phase 1» обновлены под код; CB ADR-0011 / Y-1 spec-копия / Y-2 spec / brief обновлены | review-gate БЛОКИРУЕТ любые URL/method-расхождения в публичных API файлах (`app/api/v1/auth/*`, `me.py`, `embed_api.py`) — non-blocking классификация для них запрещена; spec обновлять в том же коммите что и роутер; cross-repo grep на ссылки старых путей перед merge; OpenAPI export diff vs spec markdown | done |

## Чеклист weekly review
- Выгрузить все `open` + `in_progress`.
- Отдельно разобрать `S1/S2`.
- Обновить минимум 1 артефакт процесса (rule/skill/workflow) на повторяющиеся причины.
