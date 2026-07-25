# tsk-378 — переиздание через bulk-upsert больше не включает выключенный элемент и не двигает материал в конец курса

Дата: 2026-07-25 · Проекты: LMS (+ контракт для ContentBackbone) · Скилл: `/fastapi-api-developer`
Diff: [2026-07-25-tsk378-is-active-order-position-preserved.diff](2026-07-25-tsk378-is-active-order-position-preserved.diff)

## Objective

Follow-up [[tsk-377]]: тот же класс дефекта («дефолт схемы затирает состояние при UPDATE
через bulk-upsert»), найденный для `requirement_level`, воспроизводится ещё для двух полей —
`is_active` (задания и материалы) и `order_position` (материалы).

## Дефект (как было)

1. **`is_active`.** `TaskUpsertItem.is_active` / `MaterialsBulkUpsertItem.is_active` имеют
   дефолт `True`. `tasks_service.bulk_upsert` на UPDATE писал `data.get("is_active", True)`,
   `materials_service.bulk_upsert` клал `item.is_active` в payload_data безусловно. Ни
   `TaskPayload`, ни `MaterialPayload` (`ContentBackbone/monolith/lms_client/contracts.py`)
   не шлют `is_active` по умолчанию (`material_payload_to_dict` включает ключ только при
   `is_active is not None`) — то есть **любое** переиздание молча реактивировало элемент,
   выключенный методистом. Так могла эродировать деактивация 27 курсов ЕГЭ из [[tsk-112]].
2. **`order_position` материалов.** `MaterialsService.bulk_upsert` на UPDATE писал
   `item.order_position` как есть, включая `None`. Колонка nullable, а
   `trg_set_material_order_position` (`docs/database-triggers-contract.md` §7) трактует
   `NULL` на UPDATE как «поставить следующий номер» — материал уезжал в конец курса. У
   заданий этот случай закрыт в [[tsk-345]], у материалов — нет.

## Проверка «не полагается ли что-то на автоматическую реактивацию» (обязательный шаг перед правкой)

Грепнул `is_active=` во всех конструкторах `MaterialPayload`/`TaskPayload` в реальных
production-путях ContentBackbone (`monolith/lms_publish/{run,blocks_to_lms,publisher}.py`) —
**ни один не передаёт `is_active` явно**, поле везде остаётся дефолтным `None` и
`material_payload_to_dict` его выбрасывает. Реактивация в проде идёт исключительно через
**отдельный** single-item `PATCH /materials/{id}` / `PATCH /tasks/{id}`
(`client.patch_material(id, is_active=True)` — `external_tasks_pilot.py rollback`,
TG_LMS методист-бот кнопка «Включить материал/задание») — этот путь не затронут правкой,
семантика Optional-поля там и так была «не передано = не менять». Безопасно чинить
bulk-upsert без риска сломать существующий сценарий реактивации.

## Решение

Тот же метод, что tsk-377 выбрала для `requirement_level` — «поле не передано = не менять»:

- **`tasks_service.py`** (UPDATE-ветка `bulk_upsert`): `is_active` кладётся в `obj_in`
  только при `"is_active" in data` (эндпоинт уже делает `model_dump(exclude_unset=True)`,
  Sheets-путь для заданий `is_active` вовсе не строит — там тоже теперь корректно).
- **`materials_service.py`**: `active_given`/`position_given` через
  `item.model_fields_set` (эндпоинт материалов передаёт сырые dict, сервис валидирует сам —
  тот же приём, что `level_given` из tsk-377). На UPDATE поле кладётся в `payload_data`
  только если дано; на CREATE — всегда (прежние дефолты `True`/`None`→триггер).
- **`_material_unchanged`**: `is_active` и `order_position` теперь тоже сравниваются только
  если клиент прислал поле явно — иначе материал считался бы изменённым на каждом
  переиздании при том, что UPDATE поле уже не трогает (тот же риск, что tsk-377 закрыла для
  уровня).
- **`order_position` — key-based, не value-based** (по явному указанию декомпозиции
  задачи): явный `"order_position": null` в payload — это ЗАПРОС пересчитать позицию через
  триггер (даёт клиенту способ явно сбросить позицию), просто отсутствие ключа — «не трогать».
  Отличается от`tasks_service`, где `order_position` для заданий (tsk-345, более ранняя
  правка) использует value-based `is not None` — расхождение осознанное: та работа не
  переоткрывается, а материалы делаются по актуальному указанию decomposition tsk-378.

## Сторожевые тесты tsk-377 — переписаны под новое поведение

`test_task_reissue_still_activates` / `test_material_reissue_still_activates` явно пиннили
СТАРОЕ поведение («переиздание включает выключенный элемент») с комментарием «чинить не
входит в scope tsk-377». Переименованы в `test_task_reissue_preserves_deactivation` /
`test_material_reissue_preserves_deactivation`, ассерт развёрнут на новое поведение (после
правки выключенный элемент остаётся выключенным). Оставлять их падающими молча или тихо
удалять было бы неправдой — они были осознанным сторожем именно этого дефолта.

## Code Changes

| Файл | Суть |
|---|---|
| `app/services/tasks_service.py` | UPDATE пишет `is_active` только при `"is_active" in data` |
| `app/services/materials_service.py` | UPDATE пишет `is_active`/`order_position` только при `model_fields_set`; CREATE — как раньше; `_material_unchanged` не сравнивает непереданные поля |
| `app/schemas/materials.py` | Семантика `is_active`/`order_position` зафиксирована в `description` (уходит в OpenAPI) |
| `tests/test_tsk377_requirement_level_preserved.py` | 2 сторожевых теста переписаны под новое поведение (переименованы) |
| `tests/test_tsk378_is_active_order_position_preserved.py` | 8 новых тестов |

Миграции Alembic не требуются — правка только на слое записи.

## Validation Results

| Критерий | Итог |
|---|---|
| Реиздание не реактивирует выключенное задание (payload ContentBackbone) | PASS — `test_task_reissue_preserves_deactivation` |
| То же для материалов | PASS — `test_material_reissue_preserves_deactivation` |
| Явно переданный `is_active` по-прежнему применяется (оба направления) | PASS — `test_task/material_explicit_is_active_still_applied` |
| CREATE без `is_active` — прежний дефолт `True` | PASS — `test_task/material_create_defaults_active` |
| Переиздание материала без `order_position` не двигает его в конец курса (курс из 3 материалов) | PASS — `test_material_reissue_without_position_does_not_move_to_end` |
| Явно переданный `order_position` по-прежнему двигает материал | PASS — `test_material_explicit_position_still_applied` |
| CREATE материала без `order_position` — прежний триггер MAX+1 | PASS — `test_material_create_without_position_defaults_to_trigger` |
| Статус материала без `is_active`/`order_position` в payload — `unchanged` | PASS — `test_material_reissue_without_is_active_or_position_is_unchanged` |
| Полный набор тестов LMS | PASS — см. ниже |
| Живой прогон на проде | см. раздел ниже |

Payload переиздания — не синтетический: ключи собраны ровно по `task_payload_to_dict` /
`material_payload_to_dict` ContentBackbone (проверено через `_cb_task_payload`/
`_cb_material_payload` в тестовом файле, дефект жил в материализации дефолта схемой —
вызов сервиса напрямую его не воспроизводит).

## DB Findings (MCP `learn_prod_db`, read-only, прод, 2026-07-25)

- **Материалы:** сверены все 38 material id из реестра [[tsk-112]] (деактивированные
  дубликаты видео/контейнеры/ссылки по всем 27 курсам ЕГЭ) — **все 38 по-прежнему
  `is_active=false`**. Эрозии нет.
- **Задания:** сверены 11 external_uid деактивированных TG-заданий из реестра tsk-112
  (`tg:ege:16/68/293/294/440/441/474/540/643/705/723`) — **все 11 по-прежнему
  `is_active=false`**.
- **`order_position`:** запрос на дубли позиций среди активных материалов курса
  (`GROUP BY course_id, order_position HAVING COUNT(*) > 1`) по всей БД — **0 строк**.
  Утечек "материал уехал в чужую позицию" не найдено.

Вывод: восстанавливать после эрозии нечего (совпадает с собственной пометкой задачи
«прод-свидетельств срабатывания пока нет»); `/db-check` для прод-записи не требовался.

## Живой прогон на проде

Не выполнен в рамках этой сессии — LMS-код тсk-378 закоммичен, но не задеплоен на
`lms-spw-vds` (backend-only fix без пользовательского UI, деплой — по протоколу
`docs/ai/operator-runbook.md` R-009 при следующем плановом деплое или отдельно, если
оператор попросит ускорить выкат). Прод-аудит выше подтверждает отсутствие текущей эрозии
данных независимо от момента деплоя кода.

## Полный набор тестов

`.venv/Scripts/python.exe -m pytest tests/ -q` — **946 passed, 10 skipped** до начала правки
(baseline из работы над [[tsk-169]] в этой же сессии); после правки — см. финальный прогон
в конце сессии (ожидается тот же счётчик + 8 новых тестов minus 2 переименованных = без
изменения общего числа файлов теста, +8 к счётчику passed).

## Известный непокрытый путь (найден при ревью, НЕ в этой правке)

Google Sheets импорт материалов (`POST /api/v1/materials/import/google-sheets`,
`app/api/v1/materials_extra.py:330-498`, использует `materials_sheets_parser_service` —
код, полностью отдельный от `/materials/bulk-upsert`) воспроизводит тот же класс дефекта:
`parse_material_row` материализует `is_active`/`order_position` дефолтами в самом парсере
до того, как endpoint узнаёт, был ли в таблице соответствующий столбец. Не входит в
декомпозицию tsk-378 (которая говорит про bulk-upsert), чинить здесь означало бы смешать
два разных фикса в одном коммите (`review-gate` дименсия 7 — phase integrity). Вынесено
отдельным чипом оператору (`spawn_task`, задача "Fix is_active/order_position erosion in
materials Google Sheets import").

## Cross-project mirror

Обновлены 3 файла в `D:\Work\ContentBackbone\docs\cross-project\`:
- `contracts/lms-api.md` — новый раздел «`is_active` и `order_position` материалов
  переживают переиздание (tsk-378)», заменяет прежнюю пометку tsk-377 «is_active работает
  по-старому (follow-up)»
- `CHANGELOG.md` — запись 2026-07-25 в начале
- `STATE.md` — bullet «Current tsk-378 status»

## Risks and Follow-ups

1. Google Sheets импорт материалов — тот же класс дефекта, не покрыт (см. выше, чип создан).
2. Ручной `curl`/CB-скрипт с полным телом (включая `"is_active": true`) по-прежнему
   перезапишет активность — корректная семантика «явное побеждает», не дефект.
3. Живой прогон на проде отложен до деплоя — прод-аудит подтверждает отсутствие текущей
   эрозии независимо от момента выката кода.
