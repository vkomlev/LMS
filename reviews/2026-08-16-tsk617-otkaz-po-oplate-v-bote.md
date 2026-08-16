# tsk-617 — отказ по неоплате: бот говорит про оплату и больше не служит обходом

**Дата:** 2026-08-16 · **Скиллы:** `/fastapi-api-developer` (контракт отказа в LMS),
`/telegram-ux-flow-designer` (что видит ученик), `/review-gate`
**Контракт:** [права подписки §11](../../LMS/docs/specs/2026-08-08-contract-entitlements.md),
[cross-project lms-api.md](../../ContentBackbone/docs/cross-project/contracts/lms-api.md)
**Диff:** [2026-08-16-tsk617-otkaz-po-oplate-v-bote.diff](2026-08-16-tsk617-otkaz-po-oplate-v-bote.diff)
(TG_LMS) и одноимённый файл в `D:\Work\LMS\reviews\` (LMS)

**MANDATORY review-gate:** изменена форма ответа публичных путей LMS, снят
сервисный bypass (ломающее для клиентов по `X-API-Key`), затронуты два проекта.

## Что оказалось не так, как в постановке

Задача ставилась как продолжение tsk-301 фазы 8: «отказ по оплате показывается
ученику в боте техническим сбоем — разобрать его так же, как тарифный». Первая же
сверка с кодом дала другую картину:

**в боте этот отказ не возникал вообще.** Все 11 точек гейта в `learning.py` и
гейт в `POST /attempts` стояли под `if not current_user.is_service`, а боты ходят
по сервисному ключу (`X-API-Key` → `CurrentUser(id=0, is_service=True)`). Значит
ученик, у которого занятия закрыты за неоплату, спокойно продолжал учиться через
Telegram: в браузере закрыто, в боте открыто. Это тот же класс, что tsk-433, и
разбор отказа без правки гейта остался бы мёртвым кодом.

Развилку («закрывать ли обход») вынес оператору — решение: **закрывать, гейт по
ученику**. Живых пострадавших пока нет: на проде 39 открытых начислений, все за
август, блокировка по ним наступает 05.09.2026 (`due` = конец месяца +
`PAYMENT_BLOCK_AFTER_DAYS=5`). Более ранних открытых начислений нет — проверено
запросом к боевой БД.

## Changed Files

### LMS

| Файл | Что |
|---|---|
| `app/services/payment_access_service.py` | `blocking_debt()` вместо булева `has_blocking_debt` (сумма + месяцы), `DomainError` с `payload.code = payment_overdue`, `payments_url`, текст с суммой и месяцами |
| `app/api/v1/learning.py` | снято `if not current_user.is_service` на 10 точках, объявлен 403 в `responses` |
| `app/api/v1/attempts.py` | то же на `POST /attempts` |
| `docs/specs/2026-08-08-contract-entitlements.md` | §11 — второй доменный отказ и новое правило гейта |
| `tests/test_tsk010_access_block.py` | +5 тестов, patch переведён на `blocking_debt` |

### TG_LMS

| Файл | Что |
|---|---|
| `src/common/utils/dialogs.py` | разбор `payment_overdue`, флаг `is_payment_overdue`, `payments_url`, `format_payment_block_text`, `send_denial_message` |
| `src/bots/common/dialogs/student_next_item_base.py` | экран «следующий шаг» и автостарт попытки говорят про оплату вместо «попробуйте позже» |
| `src/bots/common/dialogs/student_attempts_base.py` | показ отказа через `send_denial_message`, отказ по оплате не пишется в журнал как ошибка |
| `tests/unit/test_tsk617_payment_messages.py` (новый) | 11 тестов |

### ContentBackbone (cross-project)

`docs/cross-project/contracts/lms-api.md` — раздел про `payment_overdue` и
таблицу «что изменилось для сервисного вызова»; `CHANGELOG.md` — запись
2026-08-16.

## Ключевые решения

**Признак машинный, разбор общий.** `payload.code`, а не поиск слов: формулировка
меняется вместе с ценами, разбор по тексту сломался бы молча. Разбор
`subscription_denied` и `payment_overdue` — один кусок кода (`_domain_denial`),
иначе третий доменный отказ завёл бы третью копию.

**Сумма и месяцы считает LMS, не бот.** Тот же довод, что в фазе 8 про пороги
наставника: свой счётчик однажды назовёт число, отличное от сайта, и это
выглядит как сбой, а не как разная настройка.

**Отказ по оплате уходит сообщением, а не всплывающим окном.** Ссылку из
alert-окна Telegram нажать нельзя, а «оплатите» без адреса в боте — тупик: своего
раздела «Оплата» у бота нет. Остальные отказы ведут себя как прежде.

**Гейт снят не везде.** Там, где ученика в запросе назвать нечем
(`assert_material_access` / `assert_task_access` по своей сессии), сервисный
bypass остался: иначе методист и CB CLI потеряли бы чтение материалов. Также
намеренно открыты для должника: чтение состояния своей заявки помощи и оценка
уже полученного разбора — закрыт учебный контент, а не поддержка.

**Дорога к оплате не перекрыта.** Инвариант tsk-010 сохранён и покрыт прежним
тестом: кабинет начислений и приложение чека работают у заблокированного.

## Validation Commands

```bash
cd D:\Work\TG_LMS && .venv/Scripts/python.exe -m pytest tests/ -q
```

```bash
cd D:\Work\TG_LMS && .venv/Scripts/python.exe scripts/tg_stack_guard.py --requirements requirements.txt --src src
```

```bash
cd D:\Work\LMS && .venv/Scripts/python.exe -m pytest -q
```

- TG_LMS: **438 passed** (11 новых), version guard **PASS**.
- LMS точечно: `test_tsk010_access_block.py` **14 passed**, лестница помощи
  tsk-303 **44 passed**, срез `learning|attempt|tsk010|tsk301|tsk272|tsk303|acl|entitlement`
  **506 passed, 1 skipped**, деньги (`tsk010|tsk615|marketer|charge`) **160 passed**.
- LMS полностью — см. раздел ниже.

## Живая проверка (2026-08-16, прод)

См. отдельный раздел после деплоя.

## Risks / Follow-ups

- **Первые живые отказы — 05.09.2026.** До этой даты правку не проверить на
  настоящем долге: сегодня блокирующих начислений на проде нет.
- Ученики с долгом потеряют доступ и в боте. Это восстановление задуманного
  поведения tsk-010, но канал, где ограничения не было, его получит.
- В `learning.py` два `summary` испорчены кодировкой (`material_skip`,
  `task_skip`) — они попадают в публичный OpenAPI. Дефект существовал до этой
  задачи, не правился здесь, чтобы не смешивать scope.
