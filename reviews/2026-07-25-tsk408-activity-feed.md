# tsk-408 — Лента активности учителя: события учеников по дате, топ-100

**Дата:** 2026-07-25
**Скиллы:** `/fastapi-api-developer` (LMS), `/executor-pro` (SPW), `/review-gate` (этот отчёт)
**Трекер:** `D:\Work\Root\tasks\tsk-408-lenta-aktivnosti-uchitelya-sobytiya-uchenikov-po-date-top-100.md` (active → done после этого коммита)

## Контекст

Учителю нужен единый поток последних событий по ВСЕМ своим ученикам — решение
задания (успешно/неуспешно/на проверке), запрос помощи, изучение материала —
отсортированный по дате (убывание), топ-100. Не листать каждого ученика
отдельно.

Разграничено с `tsk-303` (единый inbox преподавателя, backlog): inbox — то,
что требует ДЕЙСТВИЯ учителя (эскалации, ждёт ответа); эта лента — просто
ПОТОК происходящего, без обязательства реагировать. Backend `tsk-303` не
затронут, дублирования функционала нет.

## Реализация

### LMS (backend, read-only, миграций нет)

- `GET /api/v1/teacher/activity-feed?limit=&before=` — роль
  `teacher`/`methodist`/`admin`.
- `app/services/teacher_activity_feed_service.py` — три отдельных ACL-scoped
  SQL-запроса (`task_results`, `help_requests`, `student_material_progress`),
  слитые и обрезанные до топ-`limit` в Python (k-way merge; per-источнику
  запросить top-`limit` строго достаточно для корректного глобального
  топ-`limit` объединения — обоснование в докстринге).
- Синтетические зачёты преподавателя (tsk-297, `source_system`/`source`
  `= 'manual_teacher'`) исключены из всех трёх источников.
- ACL — тот же принцип, что `manual_progress_service.can_edit_progress`
  (tsk-297): `student_teacher_links` ИЛИ `teacher_course_acl` (иерархия по
  `course_parents`); bypass у methodist/admin (роль резолвится один раз в
  Python, не на SQL-уровне, в отличие от `HELP_REQUESTS_ACL_SQL`/`REVIEW_ACL_SQL`,
  где bypass — только methodist в SQL).
- Курсорная пагинация (`before`/`has_more`/`next_before`), не offset — три
  источника разного размера не имеют общего сквозного смещения после слияния.

### SPW (frontend)

- Новый экран `/teacher/activity-feed` (`components/teacher/ActivityFeed.tsx`,
  `lib/teacher/use-activity-feed.ts`), пункт навигации в `TeacherHeader`.
- Клик по событию про задание (`task_solved`/`help_requested`) открывает
  `TaskHistoryTrigger` (tsk-406/349) — новая карточка не строилась.
  `material_studied` не кликабелен (детальной карточки материала нет).
- Иконка/цвет по типу и исходу: верно — зелёный, неверно — красный, на
  проверке — синий, помощь — жёлтый, материал — нейтральный.
- «Показать ещё» — курсорная подгрузка (append к локальному state), не
  React Query offset-пагинация.

## Находка ревью (исправлена до этого коммита)

**Блокирующая, dimension 2 (корректность).** Первая версия `has_more`
считалась как `any(len(source) == limit for source in sources)` — «хотя бы
один источник капнулся». Это пропускает случай, когда НИ ОДИН источник не
достигает `limit` по отдельности, но их сумма превышает `limit`: например,
task_solved=60, help_requested=60, material_studied=60 (180 строк, все три
источника реально исчерпаны, `< limit=100`), но слитая страница (топ-100)
молча отбрасывает 80 РЕАЛЬНЫХ событий — `has_more` оставался `False`, кнопка
«показать ещё» не появлялась, событий 101-180 учитель не увидел бы никогда.

**Фикс:** `has_more = len(merged) > limit or any(len(source) == limit ...)` —
первое условие ловит «слитый набор больше страницы» (общий случай), второе
осталось как отдельная проверка на случай, когда только ОДИН источник
капнулся, а слитый список при этом не длиннее `limit` (тогда часть данных
того источника вообще не запрашивалась — не в памяти, а не просто обрезана).

**Регресс-тест:** `test_has_more_when_merged_total_exceeds_limit_without_single_source_capping`
— на фикстуре (2 источника-студента × 3 события = 6 строк) с `limit=5`
воспроизводит ровно этот сценарий: ни один из трёх источников не достигает
`limit=5` (у каждого по 2 строки), но 6 > 5 → `has_more` обязан быть `True`.
Тест падал бы на старой версии кода.

## Cross-project memory (ContentBackbone)

Новый публичный эндпоинт LMS, потребитель — SPW. Обновлено:
- `docs/cross-project/contracts/lms-api.md` — §«Лента активности учеников для
  преподавателя (tsk-408, 2026-07-25)» + обновлена строка `Last verified`.
- `docs/cross-project/CHANGELOG.md` — запись в начало (2026-07-25).
- `STATE.md` не тронут (это аддитивный эндпоинт, не смена фазы/версии проекта).

Коммит в ContentBackbone — отдельным шагом после этого review (см. Follow-ups).

## Validation Results

**LMS:**
```
pytest tests/test_activity_feed_tsk408.py -q → 7 passed
pytest -q (полный сьют)                      → 960 passed, 10 skipped
```
(полный прогон — до фикса has_more; фикс точечный, затрагивает только
`get_activity_feed`, повторный полный прогон после фикса избыточен — целевой
файл + 2 смежных сьюта (manual_progress/task_history, тот же ACL-слой)
перепрогнаны отдельно: `59 passed`)

- Hardcoded URL guard (`grep -rE "https?://(learn|api|tg)\.victor-komlev\.ru|https?://localhost:[0-9]+" app/services/ app/core/`) — 0 совпадений в затронутых файлах.
- IDOR sweep — эндпоинт не принимает `student_id`/чужой идентификатор от клиента; scope всегда `current_user.id` сервер-side. IDOR-вектора нет.
- `docs/openapi.json` регенерирован (`scripts/export_openapi.py`), diff — только новый путь `/api/v1/teacher/activity-feed` (проверено `git diff docs/openapi.json | grep '"/api/v1/'`).

**SPW:**
```
npx tsc --noEmit          → ошибки только в deploy/poligon/ (чужая сессия, не мои файлы)
npx eslint <новые файлы>  → 0 findings
npx vitest run tests/unit/activity-feed.test.tsx → 8 passed
npx vitest run (полный сьют)                     → 539 passed
```
- `lib/api-types.ts` регенерирован из свежего `openapi.json` (`npx openapi-typescript`), diff содержит только `ActivityFeedEvent`/`ActivityFeedResponse`/новый путь.
- Hardcoded URL guard — 0 совпадений в изменённых файлах.

## Changed Files

**LMS:**
- `app/schemas/activity_feed.py` (new)
- `app/services/teacher_activity_feed_service.py` (new)
- `app/api/v1/teacher_activity_feed.py` (new)
- `app/api/main.py` (router registration)
- `docs/openapi.json` (regenerated)
- `tests/test_activity_feed_tsk408.py` (new, 7 tests)

**SPW:**
- `lib/teacher/use-activity-feed.ts` (new)
- `components/teacher/ActivityFeed.tsx` (new)
- `app/(teacher)/teacher/activity-feed/page.tsx` (new)
- `components/layout/TeacherHeader.tsx` (nav entry)
- `lib/api-types.ts` (regenerated)
- `tests/unit/activity-feed.test.tsx` (new, 8 tests)

**ContentBackbone (cross-project, отдельный коммит):**
- `docs/cross-project/contracts/lms-api.md`
- `docs/cross-project/CHANGELOG.md`

## Решение review-gate

**ПРИНЯТО** (после исправления блокирующей находки `has_more` и синка
cross-project памяти — см. выше; оба сделаны до этой записи).

## Operator handoff

Категория А (рутина, выполняю сам без подтверждения):
- Коммит + push в LMS/SPW/ContentBackbone (стоячая авторизация оператора,
  `~/.claude/CLAUDE.md` §Operator handoff).
- Деплой на прод (стоячая авторизация) + живой прогон через браузер под
  ролью учителя — следующий шаг после коммита в этой же сессии.

## Rollback note

Три новых файла + 4-строчная регистрация роутера в `main.py` — откат: удалить
`app/schemas/activity_feed.py`, `app/services/teacher_activity_feed_service.py`,
`app/api/v1/teacher_activity_feed.py`, убрать регистрацию из `main.py`,
перегенерировать `openapi.json`. Миграций нет — откат БД не требуется.
SPW: удалить новый экран/хук/компонент, убрать пункт навигации, откатить
`lib/api-types.ts` до предыдущей генерации.

## Follow-ups (не блокируют)

- Коммит cross-project docs в ContentBackbone — отдельным шагом сразу после
  этого review (см. основной поток задачи).
- Живой прогон под ролью учителя на проде после деплоя — обязателен по
  стоячей авторизации (см. Operator handoff выше), будет выполнен в этой же
  сессии сразу после деплоя.
