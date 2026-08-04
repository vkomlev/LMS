# tsk-557 — норматив занятий для учеников без расписания: вывод частоты из ручной цены

## Контекст
Продолжение tsk-556 (нормативный подсчёт пропусков). У ученика без постоянного
расписания норматив вывести неоткуда: `weekly_lessons=0`, а цены за ОДНО
занятие в системе нет. Решение оператора (2026-08-04): выводить частоту
обратным проходом по той же тарифной сетке (`pricing_tariff.
match_kind='attendance_frequency'`), которой `pricing_service.
_resolve_group_price` пользуется в прямую сторону.

Полный контекст, ограничения и прод-разведка — в
`D:\Work\Root\tasks\tsk-557-normativ-zanyatij-dlya-uchenikov-bez-raspisaniya-vyvod-chastoty-iz-ruchnoj-tseny.md`.

## Изменения

- `app/services/pricing_service.py` — `resolve_attendance_frequency` +
  `AttendanceFrequencyResolution`. Расписание первично (если активные слоты
  есть — частота из них, независимо от цены); цена подключается только при
  отсутствии расписания, и то ТОЛЬКО при точном совпадении цены со ступенью
  сетки (никакого `fallback_lower`). Несколько тарифных групп с разными
  выведенными частотами — конфликт → `unknown`.
- `app/schemas/pricing.py` — `FrequencySource =
  Literal["schedule","inferred_from_price","unknown"]`.
- `app/services/student_dashboard_service.py` — `_load_attendance` получил
  `include_norm_diagnostics`; при истине считает `norm_source`/`discrepancy`
  и (только при `inferred_from_price`) `not_conducted` = норматив из цены за
  прошедшую часть периода минус фактически заведённые занятия.
- `app/schemas/student_dashboard.py` — 3 новых `Optional`-поля в
  `StudentDashboardAttendanceRead`.
- `app/api/v1/student_dashboard.py` — `_ensure_dashboard_access` теперь
  возвращает `bool` (какая ветка гейта сработала — персонал или родитель),
  пробрасывается как `viewer_is_staff`.
- `app/api/v1/parent_access_links.py` — без изменения поведения (гостевая
  ссылка не передаёт `viewer_is_staff`, дефолт `False`), только комментарий.
- `docs/openapi.json` — regenerated (265 endpoints, только additive-диф в
  схеме `StudentDashboardAttendanceRead`).

## Место интеграции — согласовано с оператором (AskUserQuestion)
В SPW нет отдельного методистского экрана посещения — единственный
потребитель `/students/{id}/dashboard` — родительский `StudentDashboardView`.
Оператор выбрал: добавить поля в тот же дашборд, видимые только персоналу
(`can_edit_progress`), `null` для родителя/гостевой ссылки. SPW не трогаю —
у него нет UI-потребителя новых полей, деплой SPW не требуется.

## Тесты
`tests/test_tsk557_price_inferred_norm.py` (7 тестов, новый файл):
- точное совпадение → `inferred_from_price`, `not_conducted` считается верно;
- `student_monthly_charge.manual_minor` не участвует в выводе (явный
  регресс-тест на декомпозицию задачи);
- скидка (цена мимо сетки) → `unknown`, не «ближайшая» ступень;
- расписание≠цена → `schedule` + `discrepancy=True`, счёт по расписанию;
- две ступени одной группы с одинаковой ценой → `unknown`;
- конфликт МЕЖДУ тарифными группами → `unknown`;
- видимость: персонал видит поля, родитель — `null`, остальные метрики те же.

`tests/test_tsk494_student_dashboard.py` — 12 существующих ассертов
посещения расширены тремя новыми полями (все используют `teacher`-токен →
персонал → поля заполнены; `unknown` там, где нет ни расписания, ни цены).

Полный `pytest`: **1601 → 1608 passed, 11 skipped**, без падений (полный
прогон — 1607 до добавления регресс-теста на `manual_minor`, целевые файлы
пересчитаны отдельно после его добавления и зелёные).
`mypy` на изменённых файлах: 0 новых ошибок (2 pre-existing в нетронутом
коде `_resolve_group_price`, не в диффе).

## Найдено и исправлено на саморевью (review-gate)
- Truthy-проверка `if resolution.weekly_lessons:` молча пропускала бы
  `not_conducted` при гипотетической частоте `0` (тариф с `match_value="0"`
  проходит валидацию `isdigit()`) — заменена на `is not None`. В проде такого
  тарифа нет, но проверка иначе была бы логически несогласована с
  `norm_source`.
- Добавлен тест на явное игнорирование `student_monthly_charge.manual_minor`
  (декомпозиция задачи требовала явно решить это, но не хватало теста,
  доказывающего решение, а не только докстринга).

## Cross-project
`D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md` §«Дашборд
ученика» и `CHANGELOG.md` обновлены (additive-only изменение контракта,
не breaking). `STATE.md` — обновление после подтверждённого деплоя на прод.

## Решение review-gate
**ПРИНЯТО.** Блокирующих находок нет. Оба замечания выше исправлены до
коммита.
