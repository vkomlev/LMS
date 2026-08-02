# tsk-372 follow-up — реальный разрыв в видимости очереди + "есть материал"

## Контекст

После деплоя первой части tsk-372 (review_kind в портале) оператор указал:
фильтр не раскрывает суть задачи — часть решений SA_COM/TBL_COM/SA всё равно
недоступна преподавателю, независимо от вердикта авто-проверки. Плюс запрос:
уметь выборочно находить сдачи с реальным материалом (код/файл), не просто
короткий текстовый ответ — задел под будущую ИИ-проверку кода.

## Разведка (read-only, прод-БД `learn`, MCP `learn_prod_db`)

1. **Распределение типов заданий × `manual_review_required` × `requires_attachment`**
   на активных заданиях: 1495 активных `SA` с `manual_review_required=false`
   (значительно больше, чем `SA_COM` того же класса — 1915).
2. **task_results с `checked_at IS NULL` по той же разбивке**: 419 сданных,
   непроверенных ответов на `SA` (mrr=false) — **невидимы ни в mandatory, ни
   в старом optional** (`optional_review_sql()` проверял только
   `type IN ('SA_COM','TBL_COM')`). 280 из них авто-верно, 139 авто-неверно —
   честные вердикты есть, просто нет пути их увидеть.
3. Для сравнения: у `mrr=true` (мандаторная очередь) на проде — 0 непроверенных
   по всем типам (TA/SA/SA_COM) на момент проверки — mandatory-очередь
   регулярно разбирается, а SA-дыра копилась молча.
4. Сэмпл стемов 419 SA-ответов — НЕ подтвердил гипотезу «неверная типизация»:
   это легитимные короткие ответы/однострочный код («Впиши число», «Напиши
   команду print»), верно типизированные. Разрыв был в предикате
   `optional_review_sql()`, а не в данных заданий.
5. **Отдельная находка (не смешана с этим фиксом)**: 26 активных `SA_COM`
   заданий текстом просят «приложи файл/скриншот», но `requires_attachment=false`
   — текстовый комментарий формально проходит гейт tsk-419 без реального
   вложения. Это дефект данных (по заданию), а не видимости очереди —
   вынесено отдельным follow-up ниже, не фикшу.
6. Для уже видимых optional `SA_COM`/`TBL_COM`: 308 из 441 и 54 из 70
   соответственно реально содержат `comment` (код) или вложение — обоснование
   для бейджа/фильтра «есть материал» (не все сдачи одинаково стоит смотреть
   в первую очередь).

## Plan

1. `OPTIONAL_REVIEW_TEMPLATE`/`optional_review_sql()` — добавлен тип `SA`
   (симметрично `MANDATORY_REVIEW_TEMPLATE`, который уже включал SA с tsk-247).
   Общий предикат (бот + портал), поэтому фикс закрывает разрыв на ОБЕИХ
   поверхностях разом.
2. `list_pending_reviews()` — новое поле `has_evidence` (SQL: непустой
   `response.comment` ИЛИ непустой `response.meta.attachments`, с
   `jsonb_typeof`-guard от не-массива) + опциональный query-фильтр
   `has_evidence: bool|null`.
3. `PendingReviewItem.has_evidence: bool` — схема, `/teacher/reviews/pending`
   принимает `has_evidence` в query.
4. SPW: бейдж «Есть материал» на карточке + тумблер «Только с материалом»
   (`?evidence=1`, ортогональная ось к `?kind=`, зеркалит `?overdue=1` из
   HelpRequestsList).
5. Найден и исправлен баг в процессе: `has_evidence_sql` изначально
   пропускал строки при `has_evidence=false` фильтре — трёхзначная SQL-логика
   (`false OR NULL = NULL`, не `false`) на строках без `meta.attachments`
   вовсе. Пойман тестом `test_pending_has_evidence_filter_false` ДО деплоя.

## Changed Files

- `app/services/teacher_queue_service.py` — `OPTIONAL_REVIEW_TEMPLATE` +
  `SA`, `has_evidence_sql`/фильтр в `list_pending_reviews`.
- `app/api/v1/teacher_reviews.py` — query-параметр `has_evidence`.
- `app/api/v1/task_results_extra.py` — докстрока (SA теперь тоже в optional).
- `app/schemas/teacher_next_modes.py` — `PendingReviewItem.has_evidence`.
- `tests/test_review_kind_pending_tsk372.py` — было 4 теста, стало 11
  (+7: SA в optional/mandatory, has_evidence вычисление × 3, фильтр × 2).

## Validation Commands

```
"./.venv/Scripts/python.exe" -m pytest -q
```
→ 1527 passed, 11 skipped (было 1519/11 до follow-up).

## Risks / Follow-ups

- **Не в этом фикс: 26 SA_COM-заданий с `requires_attachment=false`, хотя
  стем просит файл/скриншот** — нужна ручная сверка каждого задания
  (методист решает, действительно ли обязателен файл, или комментарий
  достаточен) — отдельная задача, не бандлится.
- Живая проверка на проде — отдельная запись в истории движения tsk-372.
