# tsk-298 follow-up: человекочитаемый заголовок задания в teacher-портале

**Дата:** 2026-07-19
**Задача:** tsk-298 (follow-up, косметика)
**Скиллы:** /fastapi-api-developer + /db-check (read-only сверка прода)
**БД:** `learn` (prod, read-only через MCP)

## Проблема

В веб-портале преподавателя (SPW `learn.victor-komlev.ru/teacher`) задание везде
показывалось сырым `external_uid`, напр.
`authored:vstupitelnye-it-vuz:1-2-harakteristiki-komponentov#q4` — неинтуитивно
(фидбэк оператора). Источник — MVP-заглушка `_task_title_display`, отдававшая
`external_uid` как есть.

## Диагностика на живых данных прода (MCP learn_prod_db)

| Поле | Факт |
|---|---|
| `task_content->>'title'` | ключ есть у 7000/7001, **непустой лишь у 105** |
| `task_content->>'stem'` | **непустой у всех 7001** |
| stem с HTML-разметкой | 3053/7001 (`<html>`, `<p>`, `<pre><code>`, `&quot;`) |
| длина stem | p50=237, p90=1153, **max=462081** символов |

Вывод: устоявшийся паттерн `COALESCE(title, external_uid)` (из `grade_review`,
`me_service`) для портала недостаточен — ~99% заданий упали бы обратно на
external_uid. Правильный источник подписи — `stem`, но его нужно чистить (снять
HTML, раскодировать сущности, схлопнуть пробелы) и обрезать.

## Решение

Единый helper `app/utils/task_title.py::humanize_task_title` по приоритету:
**curated title → очищенный stem (обрезка до 80 симв. + «…») → external_uid → «Задание #id»**.
Подключён во всех местах отображения teacher-портала:

- `help_requests_service.py`: `list_help_requests`, `get_help_request_detail`
  (через `_task_title_display`, теперь принимает title/stem);
- `teacher_queue_service.py`: `claim_next_review`, `claim_review_by_id`
  (ReviewClaimItem), `list_pending_reviews` (PendingReviewItem).

SQL: во все четыре SELECT добавлены `task_content->>'title'` и `->>'stem'`
(в конец списка колонок — индексы существующих полей не сдвинуты).

Схема: описания `ReviewClaimItem.task_title` и `PendingReviewItem.task_title`
обновлены (было «external_uid задания»). Структура ответа не изменилась —
`task_title` всегда был `Optional[str]`; изменилась только семантика значения.
Контракт мягкий, обратно совместимый.

## Не трогали (осознанно, вне объёма)

- `grade_review`/`regrade_review` (task_title → inbox+email ученика) и
  `me_service` (student inbox) — student-facing, оператор указал teacher-портал.
  Там остаётся `COALESCE(title, external_uid)`. Возможный отдельный follow-up.

## Валидация

- `tests/test_task_title_humanize_tsk298.py` — 15 unit-тестов (HTML-strip,
  сущности, пробелы, обрезка, все fallback-ветви, кейс из фидбэка) — зелёные.
- Смежные сервис-тесты (pending/claim/help-requests/override/title-normalize) —
  37 passed.
- `openapi.json` регенерирован (только описания, 3 строки).

## Артефакты

- Код: `app/utils/task_title.py`, `app/services/help_requests_service.py`,
  `app/services/teacher_queue_service.py`, `app/schemas/teacher_next_modes.py`
- Diff: `reviews/2026-07-19-tsk298-task-title-humanize.diff`

## Risks / Follow-ups

- Регресс-риск низкий: изменение читающее, без миграций; fallback сохраняет
  external_uid, если stem пуст.
- Живой прогон на проде — после деплоя (в этой сессии).
- cross-project: `contracts/lms-api.md` — семантика task_title (описание).
