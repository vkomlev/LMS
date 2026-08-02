# tsk-372 — портал преподавателя: фильтр опциональных работ в очереди проверки

## Контекст

Портал преподавателя (`GET /api/v1/teacher/reviews/pending`, tsk-298 Фаза 2a)
показывал только обязательную очередь (TA либо SA_COM/TBL_COM с
`manual_review_required=true`). ТГ-бот эту ось (`review_kind=mandatory|optional`)
уже поддерживал с tsk-230 через отдельный эндпоинт
`GET /task-results/by-pending-review`. Разрыв закрыт: портал получил тот же
параметр, бот не менялся.

## Plan

1. Вынести предикат «опциональная проверка» из `task_results_extra.py`
   (инлайн-условия) в `teacher_queue_service.py` как `optional_review_sql()` —
   парный к уже существующему `mandatory_review_sql()`. Единый источник для
   обоих потребителей (бот + портал).
2. `list_pending_reviews()` — параметр `review_kind: mandatory|optional|all`,
   default `mandatory` (аддитивно, поведение до tsk-372 не меняется).
3. `GET /teacher/reviews/pending` — параметр `review_kind` прокинут в сервис.
4. `claim-next`/`pending-count`/`workload` — **не менялись**. Эти три остаются
   привязаны к mandatory-очереди: `claim-next` — потому что случайная выдача
   опциональной работы, которую teacher не просил, была бы сюрпризом; счётчик
   «На проверке» в шапке — потому что он обязан совпадать с тем, что
   `claim-next` реально выдаёт (см. существующий комментарий у
   `mandatory_review_sql`, мотив бага tsk-210/247 — рассинхронизация счётчика
   и очереди делает бейдж необнуляемым). Захват опциональной работы под
   оценку — уже работающий `POST /{result_id}/claim` (`claim_review_by_id`):
   он проверяет `MANUAL_REVIEW_TASK_TYPES` (SA/SA_COM/TBL_COM/TA) без деления
   на mandatory/optional, правок не потребовалось.
5. SPW: URL-driven переключатель «Обязательные / Опциональные / Все»
   (`?kind=`) в `ReviewQueue` — зеркалит уже принятый паттерн `?type=` в
   `HelpRequestsList.tsx`. Карточка очереди показывает бейдж авто-вердикта
   (`is_correct`) для опциональных работ — виден честно-заваленный ответ без
   захода в карточку.

## Changed Files

**LMS:**
- `app/services/teacher_queue_service.py` — `OPTIONAL_REVIEW_TEMPLATE` /
  `optional_review_sql()`; `list_pending_reviews(review_kind=...)`.
- `app/api/v1/teacher_reviews.py` — `GET /pending` принимает `review_kind`.
- `app/api/v1/task_results_extra.py` — рефакторинг: `by-pending-review`
  переиспользует `optional_review_sql()` вместо инлайн-дубликата условий.
- `tests/test_review_kind_pending_tsk372.py` — новый (4 теста).

**SPW:**
- `lib/teacher/use-review-queue.ts` — `usePendingReviews(courseId, reviewKind)`.
- `components/teacher/ReviewQueue.tsx` — переключатель вида + бейдж авто-вердикта.
- `app/(teacher)/teacher/page.tsx`, `app/(methodist)/methodist/reviews/page.tsx` —
  `Suspense` (компонент теперь читает `useSearchParams`).
- `tests/unit/review-queue.test.tsx` — +8 тестов (переключатель, badge).

## Validation Commands

```
"./.venv/Scripts/python.exe" -m pytest -q
```
→ 1519 passed, 11 skipped (было 1515/11 до tsk-372 — +4 новых теста).

```
cd /d/Work/SPW && npx vitest run
```
→ 902 passed, 1 failed (`prism-highlight.test.tsx`, не связан с tsk-372 —
подтверждено прогоном в изоляции: проходит; флейк полного прогона). Было
895/895 baseline — +8 новых тестов (review-queue.test.tsx: 12 вместо 4).

```
cd /d/Work/SPW && npx tsc --noEmit && npx eslint <изменённые файлы>
```
→ чисто.

## DB Findings

Data impact: read-only (новый query-параметр существующего SELECT). Схема БД
не менялась, миграция не нужна.

## Risks / Follow-ups

- Бейдж «На проверке» в шапке портала (`WorkloadSummary`) остаётся
  mandatory-only — сознательный выбор (см. Plan п.4), не баг.
- Живая проверка на проде — см. отдельную запись в истории движения tsk-372.
