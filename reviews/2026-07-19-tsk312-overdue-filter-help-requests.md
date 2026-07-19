# tsk-312 — Фильтр «только просроченные» в списке заявок помощи

**Дата:** 2026-07-19
**Скиллы:** /fastapi-api-developer (бэкенд+API), /executor-pro (SPW)
**Задача:** UX-follow-up к tsk-298. Ячейка «Просрочено» панели нагрузки teacher-портала
вела в общий список всех заявок — нет фильтра только-просроченных. Добавлена
отдельная ось фильтра `overdue`, ортогональная типу заявки.

## Контекст

- Просрочка = `help_requests.due_at < now()` (TZ-aware, как в `_normalize_due_at`).
- `overdue` — отдельная ось, совместимая с `?type=` (не значение `request_type`).
- Реализация — **серверный параметр** (корректная пагинация): клиентский фильтр
  по `is_overdue` поверх `limit/offset` пропустил бы просроченные за пределами страницы.
- Предикат зеркалит `get_teacher_workload.overdue_total`
  (`hr.due_at IS NOT NULL AND hr.due_at < :now_ts`) — ячейка и её список считаются
  по одному правилу.

## Changed Files

### LMS (backend + API)
- `app/services/help_requests_service.py` — `list_help_requests(..., overdue=False)`:
  при `overdue=True` в WHERE (COUNT и SELECT) добавляется
  `AND hr.due_at IS NOT NULL AND hr.due_at < :now_ts`; `HELP_REQUESTS_ACL_SQL`
  и сортировка сохранены.
- `app/api/v1/teacher_help_requests.py` — GET `/teacher/help-requests`: опциональный
  query-параметр `overdue: bool = Query(False)`, проброшен в сервис.
- `docs/openapi.json` — регенерирован (+12 строк: только новый параметр).
- `tests/test_teacher_help_requests_overdue_tsk312.py` — сид просроченной +
  непросроченной заявки, проверка: `overdue=1` отдаёт только просроченную
  (`is_overdue=True`), без параметра — обе. Сид-строки чистятся в `finally`.

### SPW (frontend)
- `lib/teacher/use-help-requests.ts` — `useHelpRequests(status, requestType, overdue=false)`,
  добавляет `&overdue=1` к запросу; `overdue` в `queryKey`.
- `components/teacher/HelpRequestsList.tsx` — читает `?overdue=1` из URL (URL-driven,
  как тип-фильтр в tsk-298 fix); чип-фильтр «Просроченные»; `buildListHref`
  сохраняет обе оси (`type` + `overdue`) при навигации.
- `components/teacher/WorkloadSummary.tsx` — ячейка «Просрочено» → `?overdue=1`,
  активна при `?overdue=1`; «Запросы помощи» не активна в overdue-виде.
- `lib/api-types.ts` — точечно добавлен `overdue?: boolean` в query операции списка
  (без затягивания несвязанного дрейфа контракта — прочий diff генератора откачен).

## Validation Commands

```bash
# LMS
.venv/Scripts/python.exe tests/test_teacher_help_requests_overdue_tsk312.py   # [PASS]
.venv/Scripts/python.exe tests/test_teacher_next_modes_stage39.py             # регресс [PASS]
.venv/Scripts/python.exe scripts/export_openapi.py                            # openapi regen

# SPW
node_modules/.bin/vitest run tests/unit/help-requests-list.test.tsx tests/unit/workload-summary.test.tsx   # 11 passed
node_modules/.bin/tsc --noEmit                                                # чисто
```

## DB Findings

- Локальная БД `Learn` (localhost), тест сидит/чистит `help_requests` — прод не затронут.
- Предикат `due_at < :now_ts` (Python `datetime.now(timezone.utc)` bind) идентичен
  workload-агрегату → счётчик и список не разойдутся.

## Date/Type Guard Evidence

- `now_ts` — TZ-aware bind (`datetime.now(timezone.utc)`), не raw-строка; сравнение
  `timestamptz < timestamptz` на стороне PG. `_normalize_due_at` уже приводит due_at
  к aware при формировании `is_overdue` для ответа.

## Risks / Follow-ups

- Аддитивный backward-compat параметр: старые клиенты (боты) не передают `overdue` →
  поведение прежнее.
- Деплой: LMS (root на /opt/lms) + SPW (под app). Cross-project: CHANGELOG-запись
  об аддитивном параметре help-requests.
- Живой прод-прогон: `overdue_total` сейчас 0 — проверить и на данных с искусственной
  просрочкой (тестовой заявке, не реальному ученику) через /db-check.
