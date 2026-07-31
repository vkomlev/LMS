# Review-gate: tsk-494 — периодный дашборд ученика (данные/API)

**Решение: ПРИНЯТО**

## Контекст

Данные/API для будущего кабинета родителя (tsk-478, зависимая задача — доступ
+ UI, ещё не реализовано). План: `docs/specs/2026-08-01-plan-tsk494-student-dashboard-api.md`
(`/change-plan-architect`). Реализация — `/fastapi-api-developer`.

## Изменения

Новый `GET /students/{student_id}/dashboard?from=&to=`:
- `app/services/student_dashboard_service.py` (новый) — сборка ответа: итог за
  период (переиспользует `teacher_lesson_summary_service.load_homework_window`),
  "в часы занятий" (новая SQL-агрегация, EXISTS против occurrence), "между
  занятиями" = итог минус "в часы занятий" (арифметика), посещение за период,
  прогноз окончания курса (эвристика — темп за N недель).
- `app/schemas/student_dashboard.py` (новый) — контракт без `solution_rules`/
  текста заявок помощи.
- `app/api/v1/student_dashboard.py` (новый) — роут, гейт
  `ensure_can_edit_progress`, валидация `from`/`to` (timezone-aware, `to>from`).
- `app/api/main.py` — регистрация роутера.
- `app/core/config.py` — `Settings.student_forecast_pace_weeks` (дефолт 4).
- `app/services/teacher_lesson_summary_service.py` — переименование
  `_load_homework_window`→`load_homework_window`,
  `_MANUAL_SOURCE`→`MANUAL_SOURCE`, `_DONE_STATUSES`→`DONE_STATUSES`
  (публичный API модуля, переиспользуется новым сервисом). Поведение и
  сигнатуры не изменились.
- `tests/test_tsk494_student_dashboard.py` (новый) — 12 тестов.

## Проверка по 12 измерениям

1. **Соответствие целям** — все 5 пунктов состава дашборда из tsk-494
   покрыты (курсы+прогресс+прогноз, итог за период, посещение, ДЗ между
   занятиями, произвольный период) + принцип минимизации данных заложен с
   нуля. DRIFT нет.
2. **Корректность** — арифметика "между занятиями = итог - в часы занятий"
   доказана не задваивать/не терять события (subset-инвариант на уровне
   task_id, включая нетривиальный случай `first_try`; см. docstring
   `_subtract_metrics` + `max(0, ...)` как defensive backstop). Подтверждено
   на реальных прод-данных (см. DB Findings).
3. **БД/миграции** — Data Impact: read (без миграций).
4. **Безопасность/IDOR** — `ensure_can_edit_progress(student_id)` без
   `course_id`: сервис/admin/methodist полный доступ, teacher — только через
   прямую связку `student_teacher_links` (без отдельной course-ACL проверки
   на уровне роута). Это ИЗВЕСТНОЕ ограничение (строже необходимого — teacher
   с доступом только через `teacher_course_acl` без прямой привязки получит
   403), не дыра. Задокументировано в contracts/lms-api.md, не блокирует —
   решение по расширению ACL отложено до реального запроса (план явно
   пометил это открытым вопросом code review).
5. **Тесты** — 12 HTTP-level тестов на реальной БД (не моки): ACL,
   валидация входа, свойство `period_total==in_class+between` (не хардкод
   чисел), граница occurrence, посещение по всем 6 статусам, прогноз (3
   ветки + деление на ноль при misconfigured `pace_weeks=0`), минимизация
   данных (сырой JSON). Полный прогон: 1307 passed, 11 skipped, 0 failed.
6. **Docs/Config drift** — `docs/openapi.json` уже содержит схему нового
   эндпоинта (утекла в коммит `5e85985` параллельной сессии tsk-461,
   задокументировано в `reviews/2026-08-01-tsk461-auth-gates-review.md` —
   не трогаю этот файл в своём коммите, он уже в актуальном состоянии).
7. **Phase integrity** — scope = ровно данные/API из декомпозиции tsk-494,
   без UI/доступа (это tsk-478).
8-10. Не применимо (нет доменных справочников; Date/Time Safety — см. п.10
   отдельно ниже).
10. **Date/Time Critical** — `from`/`to` обязаны быть timezone-aware (422 на
   naive datetime, тест есть); `get_student_dashboard` теперь САМ бросает
   `ValueError` на naive `period_from`/`period_to` вместо тихого fallback на
   `datetime.now()` без tz (defense-in-depth для будущих вызывающих, включая
   tsk-478).
11. **Cross-project memory** — `ContentBackbone/docs/cross-project/CHANGELOG.md`
   (запись 2026-08-01 (3)) и `contracts/lms-api.md` (§«Дашборд ученика…»)
   обновлены и закоммичены (`64739b5`) + запушены в ContentBackbone до этой
   записи.
12. **Public API Contract Sync** — новый эндпоинт, документирован в mirror
   контракта в том же цикле; hardcoded URL — 0 совпадений (grep); cross-repo
   grep на старые пути не требуется (эндпоинт новый, не переименование).

## Блокирующие проблемы (найдены и исправлены в ходе этого ревью)

- **[Исправлено]** `_load_course_pace_and_forecast`: `pace_per_week =
  done / pace_weeks` не проверяло `pace_weeks<=0` до деления —
  `STUDENT_FORECAST_PACE_WEEKS=0` в env уронил бы запрос
  `ZeroDivisionError`/500. Добавлен guard `if pace_weeks <= 0: return None,
  False` до вычисления `since`; тест
  `test_forecast_none_when_pace_weeks_misconfigured_to_zero` добавлен.
- **[Исправлено]** Cross-project contracts не были обновлены на момент
  первого прохода — теперь обновлены и закоммичены (см. п.11).

## Улучшения без блокировки (применены)

- Удалена мёртвая функция `_empty_metrics()` (объявлена, нигде не вызывалась).
- `get_student_dashboard` теперь явно валидирует timezone-awareness входных
  дат (ValueError), а не молча деградирует к naive `datetime.now()`.

## DB Findings

MCP `learn_prod_db` (read-only). Реальный ученик 4508, период
`2026-07-20..2026-08-02`: ручной пересчёт SQL "в часы занятий" vs "итог за
период" (45 tasks_completed итог / 8 в-часы-занятий / 38 first_try итог / 4
first_try в-часы) подтвердил subset-инвариант (неотрицательная разница по
обеим метрикам). Посещение (1 `confirmed` + 1 `no_show` в периоде) совпало с
прямым просмотром сырых строк `lesson_occurrence_participant`. Запись в
прод-БД не производилась.

## Date/Type Guard Evidence

- Роут: `from_dt.tzinfo is None or to_dt.tzinfo is None` → 422 (тест
  `test_dashboard_422_naive_datetime`).
- Роут: `to_dt <= from_dt` → 422 (тест `test_dashboard_422_to_before_from`).
- Сервис: `period_from.tzinfo is None or period_to.tzinfo is None` →
  `ValueError` (defense-in-depth для будущих вызывающих, минуя роут).
- Прогноз: `pace_weeks <= 0` → `None` вместо деления на ноль (тест
  `test_forecast_none_when_pace_weeks_misconfigured_to_zero`).

## Necessary tests
Покрыто (см. «Тесты» выше) — дополнительных тестов не требуется.

## Operator handoff
Категория А (review-gate ПРИНЯТО → коммит/пуш сам; деплой в прод и живая
проверка — следующий шаг этой же сессии, per operator-handoff-rules и
durable-авторизация tsk-359).
