# tsk-616 — сверка со шлюзом называет деньги, которые учесть нечем

**Дата:** 2026-08-16
**Задача:** [tsk-616](file:///D:/Work/Root/tasks/tsk-616-platyozh-yukassa-10-bez-nachisleniya-sverka-ne-pokazyvaet-chto-za-dengi-visyat-v-without-charge.md)
**Класс:** правка контракта эндпоинта + клиент SPW. Боевые данные НЕ менялись.

## Контекст

Сверка LMS с ЮKassa за 01–16.08.2026 показала расхождение 10 ₽: у шлюза есть
успешный платёж `32029d8b-000f-5001-9000-116bf07a398a` от 03.08.2026 12:30 UTC,
`metadata {"student_id": "142", "charge_id": "47"}`, в `student_payment` его нет.

Разбор потребовал ручного похода в кабинет ЮKassa, потому что сверка отдавала о
таком платеже ровно один номер — ни суммы, ни даты, ни ученика. Это и есть
системная часть задачи.

## DB Findings (MCP, read-only, боевая БД)

| Проверка | Результат |
|---|---|
| `student_payment` по номеру шлюза и по ученику 142 | строк нет |
| `student_monthly_charge` `id=47` | нет; живых строк 39 при `max(id)=55` — 47 в дырке между 43 и 48 |
| начисления ученика 142 за любой период | не было никогда |
| `users.id=142` | «Комлев Виктор» — тестовый аккаунт оператора |
| соседние начисления 48–52 | созданы 03.08 13:10 UTC, то есть пересчёт прошёл ПОСЛЕ оплаты (12:30 UTC) |

Вывод: оператор оплатил своё тестовое начисление, уведомление не дошло, затем
`charge_service.recalculate_student_group` удалил открытую строку месяца.
`ON DELETE RESTRICT` не сработал, потому что строки платежа не существовало.

**Решение оператора:** платёж тестовый, БД не трогаем; чиним сверку.

## Code Changes

| Файл | Суть |
|---|---|
| `app/schemas/payment.py` | новые `ReconcileUnmatchedPayment`, `ReconcileResult`, тип `ReconcileReason` |
| `app/api/v1/marketer_payments.py` | `without_charge` — объекты вместо строк; три причины вместо одной; `response_model=ReconcileResult`; предупреждение в лог с подробностями |
| `app/services/payment_service.py` | `student_names()` — ФИО по списку номеров одним запросом (строки платежа у таких денег нет, обычные соединения не помогают) |
| `docs/openapi.json` | схемы `ReconcileResult` / `ReconcileUnmatchedPayment` вместо безымянного `object` |
| `tests/test_tsk010_gateway.py` | новый тест на подробности + правка ожидания в старом |
| SPW `lib/payments/use-payments.ts` | тип `UnmatchedPayment` |
| SPW `components/marketer/PaymentsQueue.tsx` | список с суммой, датой, учеником, причиной; итог больше не врёт |
| SPW `tests/unit/payments.test.tsx` | тест на экран сверки |

### Что именно поменялось в ответе

Было: `"without_charge": ["32029d8b-000f-5001-9000-116bf07a398a"]`

Стало:

```json
"without_charge": [{
  "payment_id": "32029d8b-000f-5001-9000-116bf07a398a",
  "amount_minor": 1000,
  "captured_at": "2026-08-03T12:30:00Z",
  "student_id": 142,
  "student_name": "Комлев Виктор",
  "reason": "charge_missing",
  "reason_text": "Начисление №47 не найдено: его удалили после оплаты"
}]
```

Причины: `charge_missing` — начисление удалили после оплаты; `charge_unknown` —
в платеже нет номера начисления; `package_meta_missing` — у разовой покупки нет
ученика или объёма пакета (было единственной причиной с tsk-615).

### Побочная находка, исправлена здесь же

Кабинет при `added=0` писал «Расхождений нет: у сервиса N, все учтены» и тут же
дописывал список платежей, которые учесть не смог. Экран противоречил сам себе
ровно в том месте, где человеку надо принять решение о деньгах.

## Validation Results

| Критерий | Результат |
|---|---|
| `pytest tests/test_tsk010_gateway.py tests/test_tsk615_one_off_payments.py` | PASS (24) |
| `pytest -k "payment or charge or gateway or marketer"` | PASS (141) |
| SPW `vitest run tests/unit/payments.test.tsx` | PASS (18) |
| SPW `tsc --noEmit` | мои файлы чисты; в дереве есть чужая ошибка `tests/unit/subscription-panel.test.tsx(114,19)` из коммита tsk-301 — не трогал |
| OpenAPI регенерирован | 293 эндпоинта; diff ограничен только этим эндпоинтом (чужой работы в дереве не захватил) |

## Risks and Follow-ups

- **Ломающее изменение контракта.** Единственный клиент — SPW, обновлён тем же
  коммитом. Внешних потребителей у эндпоинта нет (кабинет маркетолога).
- `SPW/lib/api-types.ts` генерируется из `openapi.json` пакетно и остаётся
  устаревшим по этому эндпоинту. Рабочий тип объявлен вручную в
  `use-payments.ts`, поэтому на поведение это не влияет.
- В дереве LMS параллельно работает другая сессия (tsk-617: `attempts.py`,
  `learning.py`, `payment_access_service.py`). Коммит собран pathspec'ом только
  по своим файлам.
- Остаётся выкат LMS + SPW и живая проверка экрана сверки на проде.
