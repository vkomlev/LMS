# tsk-010 — формула итога месяца сведена в одно место

**Дата:** 2026-08-02
**Задача:** [tsk-010](../../Root/tasks/tsk-010-sistema-oplat-lms.md), пункт 3 «Осталось»
**Скилл:** `/fastapi-api-developer`
**Review-gate:** ПРИНЯТО (см. раздел «Решение review-gate» в конце файла)

## Контекст

Формула итога месяца `COALESCE(manual_minor, calculated_minor) + adjustments`
была задублирована в пяти местах денежного контура:

1. `charge_service.list_charges` — Python (`base + int(r.adjustments_minor)`)
2. `payment_service.list_student_charges` — Python (тот же паттерн)
3. `payment_service.list_payments` — raw SQL, поле `charge_total_minor`
4. `payment_reminder_service.list_overdue` — raw SQL, поле `total_minor`
5. `payment_access_service.has_blocking_debt` — raw SQL, поле `total_minor`

(В постановке задачи были явно названы места 1, 3, 4, 5; место 2
[`list_student_charges`] найдено дополнительно при чтении кода.)

Риск: пять копий одной формулы могли разойтись (например, при правке
приоритета `manual` vs `calculated` в одном месте и забытой синхронизации
остальных) — список платежей, напоминание о просрочке и проверка блокировки
показали бы РАЗНЫЕ суммы по одному и тому же месяцу.

## Решение

Единая функция `charge_service.charge_total_minor(*, calculated_minor,
manual_minor, adjustments_minor) -> int` — единственное место формулы.

Выбран вариант «единая Python-функция», а не SQL VIEW/CTE:
- Три raw-SQL места уже получают `calculated_minor`/`manual_minor`/`adj.total`
  отдельными колонками результата (JOIN/LATERAL не менялись, только состав
  SELECT) — из raw SQL убрана только арифметика `COALESCE(...) + COALESCE(...)`,
  остальная структура запроса (в т.ч. LATERAL-подзапросы, CAST-параметры)
  осталась прежней.
- Итог считается в Python после фетча строки — тем же способом, каким уже
  работали `list_charges` и `list_student_charges` (не новый паттерн, а
  распространение существующего на оставшиеся 3 места).
- Не требует Alembic-миграции (нет DDL/VIEW) — меньше риска для денежного
  контура, где по регламенту проекта повышенная осторожность.
- Контракт API не изменился: `ChargeRead`/`PaymentRead`/`StudentChargeRead`
  отдают те же поля с теми же значениями — переменился только внутренний
  способ их получения.

## Изменённые файлы

- `app/services/charge_service.py` — добавлена `charge_total_minor()`
  (экспортирована в `__all__`), `list_charges` использует её вместо инлайна.
- `app/services/payment_service.py` — `list_student_charges` и `list_payments`
  используют `charge_service.charge_total_minor()`; SQL `list_payments`
  больше не считает `COALESCE(...) + COALESCE(...)` сам, а отдаёт сырые
  `calculated_minor`/`manual_minor`/`adjustments_minor`.
- `app/services/payment_reminder_service.py` — импортирует `charge_service`,
  `list_overdue` считает `total_minor` через общую функцию.
- `app/services/payment_access_service.py` — импортирует `charge_service`,
  `has_blocking_debt` считает `total_minor` через общую функцию.
- `tests/test_tsk010_charge_total_consistency.py` — новый regression-тест
  (см. ниже).

Не тронуты: `teacher_reviews.py`, `task_results_extra.py`, SPW `/teacher` —
параллельный независимый чип tsk-372 (зафиксировано в
`.skill-engaged-note.md`). Диагностика подтвердила: во время работы этот же
чип параллельно менял `app/services/teacher_queue_service.py` и добавил
`tests/test_review_kind_pending_tsk372.py` в том же рабочем дереве — эти
файлы **не входят** в коммит данной задачи (проверено `git status` перед
коммитом, коммит делается с явным pathspec).

## DB Findings (MCP)

Данных не потребовалось — правка не меняет схему, только внутренний способ
вычисления уже существующих полей (`total_minor`, `charge_total_minor`).
Использованные таблицы (`student_monthly_charge`, `charge_adjustment`,
`student_payment`) не менялись со времён tsk-511/512/513/010 — миграция не
нужна.

## Regression-тест

`tests/test_tsk010_charge_total_consistency.py::test_total_matches_across_all_three_consumers`

Сценарий: один ученик, один месяц, `manual_minor` (600000) ≠ `calculated_minor`
(550000) + ручная поправка `charge_adjustment` (15000) → ожидаемый итог 615000.
Проверено, что ВСЕ три потребителя видят именно это число:

1. `charge_service.list_charges` — `total_minor == 615000`.
2. `payment_service.list_payments` — `charge_total_minor == 615000`,
   `charge_due_minor == 615000 - 100000` (после частичной оплаты).
3. `payment_reminder_service.list_overdue` — `due_minor` совпадает с тем же
   остатком.
4. `payment_access_service.has_blocking_debt` — граничная проверка: не хватает
   ровно 1 копейки до 615000 → блокировка есть; копейка доплачена → блокировка
   снята. Это доказывает, что число внутри `has_blocking_debt` (не отдаваемое
   наружу напрямую) равно тому же 615000, а не отличается на округление или
   приоритет `manual`/`calculated`.

## Validation Results

- Baseline (до правок): `1515 passed, 11 skipped`.
- После правок (полный прогон, включая параллельно созданные тесты чипа
  tsk-372 в этом же рабочем дереве): `1520 passed, 11 skipped` — дельта +5 =
  +1 мой новый тест + 4 теста параллельного чипа. Файлы этой задачи проверены
  отдельно: `test_tsk010_payments.py`, `test_tsk010_reminders.py`,
  `test_tsk010_access_block.py`, `test_tsk010_gateway.py`,
  `test_tsk511_charges_breaks.py`, `test_tsk010_charge_total_consistency.py`
  → `88 passed`.
- `grep -rE "https?://(learn|api|tg)\.victor-komlev\.ru|https?://localhost:[0-9]+"` по
  изменённым файлам — 0 совпадений (нет hardcoded URL).
- `docs/openapi.json` — без изменений (контракт API не менялся).

## Risks / Follow-ups

- Денег правка не создаёт и не отменяет — только читает уже существующие
  таблицы. Приоритет округления/`manual` vs `calculated` сохранён буквально
  (тот же порядок `COALESCE`).
- Пункты 1–2 «Осталось» в tsk-010 (боевой режим ЮKassa, регулярные списания) —
  за оператором, эта правка их не касается.

## Решение review-gate

**ПРИНЯТО.** Проверка по 12 измерениям, блокирующих находок нет:

1. **Соответствие целям** — единственный оставшийся инженерный хвост tsk-010
   закрыт: формула сведена, старая семантика (`manual` побеждает `calculated`,
   округление не менялось) сохранена буквально.
2. **Корректность** — edge case `manual_minor IS NULL` (расчёт побеждает)
   проверен явно в новом тесте (`calculated_minor == 550000` при активном
   `manual_minor`), плюс граничная проверка блокировки на стыке "не хватает 1
   копейки / хватает ровно".
3. **Миграции/схема** — не требуются, DDL не менялся.
4. **Секреты/IDOR** — не затронуты, эндпоинты и права доступа не менялись.
5. **Тесты** — новый regression-тест на согласованность трёх потребителей +
   88/88 существующих тестов платежей/начислений зелёные.
6. **Docs/Config drift** — история tsk-010 будет обновлена после деплоя и
   живой проверки (см. ниже); cross-project контракты не задеты (см. п.11).
7. **Phase integrity** — scope ограничен 4 сервисными файлами + 1 тестом;
   `teacher_reviews.py`/`task_results_extra.py`/SPW `/teacher` не тронуты
   (подтверждено `git status` — эти файлы меняет параллельный чип tsk-372).
8-9. **Data/Domain completeness** — не применимо, классификаторов и справочников
   изменение не касается.
10. **Date/Time** — период/дата проходят через формулу без изменений типов.
11. **Cross-project sync** — не требуется: публичный контракт API, схема БД,
    CORS/rate-limits не менялись — только внутренний способ вычисления уже
    существующих полей ответа.
12. **Public API contract** — `app/api/v1/**` не менялся, hardcoded URL — 0
    совпадений, `docs/openapi.json` не изменился.

**Operator handoff:** ветвь А целиком (код + тесты + коммит на этой сессии).
Деплой на прод и живая read-only сверка через MCP `learn_prod_db` — тоже
ветвь А (durable-авторизация коммит/пуш/деплой, `~/.claude/CLAUDE.md`
§Operator handoff). Пункты 1-2 «Осталось» в tsk-010 (боевой режим ЮKassa,
решение по регулярным списаниям) вне scope этой правки и остаются за
оператором.
