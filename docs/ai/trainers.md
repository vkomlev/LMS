# Интерактивные тренажёры (tsk-1247)

Сухие факты для агента. Бриф: `docs/briefs/tsk-1247-interactive-trainers.md`. Формат
сценария: `docs/design/trainers/scenarios/README.md` §1. Движок — в SPW; LMS хранит
сценарий, отдаёт его, принимает события и подсказывает тренажёр после ошибки.

## Модель

| Что | Где |
|---|---|
| Тип материала `trainer` | enum `content_type` (ревизия `tsk1247_material_type_trainer`, только `ADD VALUE`) |
| `content = {slug, scenario}` | `app/schemas/material_content.py` `TrainerContent`; сценарий хранится как прислан |
| Схема `trainer-scenario/1` | `app/schemas/trainer_scenario.py` (`validate_scenario`) |
| Развилка «Всё понятно?» | `content.checkpoint = {question, trainer_material_id}` у любого не-trainer материала (`TrainerCheckpoint`). Не путать с `content.checkpoints` (tsk-998) |
| Уровень обязательности | всегда `recommended` (`schemas/materials.py` `TRAINER_REQUIREMENT_LEVEL`; PATCH/PUT/bulk-upsert приводят) |
| Сервис | `app/services/trainers_service.py` |
| Ручки | `app/api/v1/trainers.py`: `GET /trainers/{slug}`, `POST /trainers/{slug}/events` |
| Рубильник | `TRAINERS_ENABLED` (по умолчанию `false`), читается из окружения процесса на каждый вызов (как `NODE_SEQUENCE_ENABLED`: на проде — в окружении сервиса, не только в `.env`) |
| Уникальность slug | один slug — один сценарий: запись тренажёра с занятым slug и другим сценарием — 409 (create/PUT/PATCH) или `validation` в bulk-upsert (`trainers_service.slug_conflict`). Копии того же сценария в разных курсах разрешены |
| Разметка сценария | разрешены только `` `код` `` и `**жирный**`: отказ на `<` + буква/`/`/`!`/`?` вне `` `…` `` и на схемах `javascript:`/`vbscript:`/`data:` (после удаления пробелов и управляющих). Листинги и вывод (`code`, `lines`, `print`, `error`, `crash`) не проверяются. Это вторая линия: SPW обязан выводить тексты как текст |

## Почему `recommended`, а не `skippable`

В LMS `skippable` входит в обязательное (`requirement_override_service.REQUIRED_LEVELS =
("required", "skippable")`): next-item выдаёт такой материал следующим шагом, процент курса и
`compute_course_state` его ждут. «Необязательный и не блокирует продвижение» — это
`recommended`: вне обхода и вне всех счётчиков, но в программе курса виден, и курсор
next-item (`after_material_id`) на нём работает. Поэтому бриф («skippable») реализован
уровнем `recommended`; порядок узла и счётчики не правились ни в одном месте.

## Порядок узла

Свой порядок не заводится: тренажёр — материал узла, место ему даёт `node_sequence` (П7) по
`order_position`. Программа курса (`me_service` syllabus) показывает его на этом месте при
`TRAINERS_ENABLED=true` и не показывает при `false`. next-item его не выдаёт никогда
(`recommended`). «Да» на развилке — `GET /learning/next-item?after_material_id=<тренажёр>`;
«Нет» — открыть тренажёр (`GET /materials/{id}` → `content.slug` → `GET /trainers/{slug}`).

## Выдача ученику (непривилегированному)

`trainers_service.student_visible`, вызывается в `GET /materials/{id}` и
`GET /courses/{id}/materials`:
- рубильник выкл — тренажёров нет (одиночный — 404), `checkpoint` снят;
- вкл — `checkpoint` остаётся, только если `trainer_material_id` — активный `trainer` того же курса.

Сервисный ключ и teacher/methodist/admin видят всё (как у tsk-998). ТГ-бот ходит сервисным
ключом — тип `trainer` ему виден независимо от рубильника (шаг 6 плана: показывать ссылкой).

## События

| context | Кто | Куда |
|---|---|---|
| `student` | ученик из cookie `session`/Bearer; сервисный ключ — 401 | `learning_events` (`event_type` = тип, payload `{slug, trainer_material_id, context, level, step, iter, ok, misconception, ms, stars, answer}`, без `task_id`) |
| `guest` | cookie `guest_session` (существующая сессия, иначе 400) | `guest_attempt`, `task_id = NULL`, `answer_json.kind = "trainer_event"`; атрибутируется при входе тем же `attribute-guest` |
| `embed` | cookie не читаются | только `embed_view`/`trainer_start`/`trainer_done` → `product_event` (`user_id = NULL`, `properties {slug, context, parent_domain}`); прочие события отбрасываются. Сбой записи (нет партиции месяца) — 202 и warning в журнале |

Новых таблиц нет. Проверки: ≤ 200 событий, `answer` ≤ 64, `misconception` только из
`scenario.misconceptions` (422 `unknown_misconception`), `parent_domain` — только у `embed`,
доменное имя без схемы. Лимиты (Redis, fail-open): чтение 600/мин с IP; события 120/мин с IP
и 30/мин на пишущего (ученик / гостевая сессия / slug+IP у встройки). IP — `request.client.host`,
как у гостевых ручек: за прокси нужен uvicorn `--proxy-headers`.

Известные ограничения (не сделано): повтор пакета из очереди досылки пишет дубли
(нет `event_id` от клиента); строки тренажёра гостя увеличивают `attributed_count` в ответе
`attribute-guest`, а аналитика по `guest_attempt` должна фильтровать `answer_json->>'kind'`;
индекса по `content->>'slug'` нет (материалов ~3,5 тыс., ответ кешируется ETag).

Чужие потребители `learning_events` фильтруют по `event_type` или `payload.task_id` —
новые типы их не задевают; `lesson_idle_cron_service` засчитывает любое событие как
признак активности на занятии (тренажёр — настоящая учебная активность).

## `trainer_suggestion`

`{slug, title}` первого активного тренажёра узла задания (`materials.course_id = tasks.course_id`,
по `order_position`), иначе `null`. Где:
- `POST /attempts/{id}/answers` → `results[].trainer_suggestion`, когда `check_result.is_correct is False`;
- `GET /learning/next-item` → когда следующий шаг — задание и последний `task_results.is_correct = false`.

При выключенном рубильнике запросов не делается, поле всегда `null`. Подсчёт — в точке
сохранения: сбой не роняет ни сдачу, ни next-item. Узел — `tasks.course_id` (для заданий
прозрачного подкурса в списке хозяина тренажёр хозяина не предлагается), последний ответ — без
учёта корня попытки (tsk-264).

## Откат

Рубильник `TRAINERS_ENABLED=false`. Ревизия обратима: downgrade пересоздаёт `content_type`
без `trainer` и отказывает, если тренажёры есть в `materials` (удалить их явно).

## Тесты

`tests/test_tsk1247_trainers.py`.
