# tsk-330 — порядковая зависимость теста эскалации

Дата: 2026-07-20 · Ветка: `tsk-297-manual-progress` · Коммит: `863641d`

## Контекст

`tests/test_y6_review_loop.py::test_y6_escalation_cron_tick_idempotent` проходил
в изоляции, но падал при любом предшествующем тестовом файле:

```
RuntimeError: Task ... got Future ... attached to a different loop
```

Воспроизведение:
```
./.venv/Scripts/python.exe -m pytest tests/test_teacher_reviews_pending_tsk298.py tests/test_y6_review_loop.py -q
→ 1 failed, 14 passed
```

## Первопричина

`escalation_cron_tick()` открывал сессию через глобальную
`app.db.session.async_session_factory`, привязанную к движку с **QueuePool**
(`app/db/session.py:15`). Пул переживает границы тестов, а conftest даёт каждому
тесту свой function-scoped event loop. Соединение, оставшееся в пуле от прошлого
loop'а, переиспользуется в новом — asyncpg падает.

Остальные тесты не задеты: они ходят в БД через фикстуры `db` / `client`, где
NullPool уже стоял. Сервисный код с собственной сессией — единственная дыра
(в `app/` таких мест два: `escalation_service`, `teacher_reviews.py:648`).

## Правка

Выбран вариант «необязательный `session_factory`» — прод-путь не меняется.

| Файл | Что |
|---|---|
| `app/services/escalation_service.py` | параметр `session_factory: Optional[async_sessionmaker[AsyncSession]] = None`; при `None` берётся глобальная фабрика — APScheduler зовёт tick без аргументов, поведение прежнее |
| `tests/conftest.py` | новая function-scoped фикстура `db_engine` (NullPool-движок текущего loop'а); `db` теперь строится поверх неё |
| `tests/test_y6_review_loop.py` | тест передаёт в tick `async_sessionmaker(bind=db_engine)` |

Альтернатива «подменять фабрику глобально в conftest» отклонена: `escalation_service`
импортирует фабрику по значению (`from ... import async_session_factory`), поэтому
подмена модуля не подействовала бы — пришлось бы патчить атрибут сервиса, что
хрупче явного параметра.

## Проверка

```
pytest tests/test_teacher_reviews_pending_tsk298.py tests/test_y6_review_loop.py -q
→ 15 passed                                    (было: 1 failed, 14 passed)

pytest tests/ -q -p no:randomly
→ 777 passed, 5 failed, 10 skipped             (целевой тест в числе прошедших)
```

## Смежные падения (НЕ исправлялись — отдельный охват)

Все пять падают и в полной изоляции, но причины разные — гипотеза «зависимость от
данных dev-БД» верна не для всех:

| Тест | Симптом в изоляции | Природа |
|---|---|---|
| `test_auth_magic_link::test_magic_link_send_valid_email` | Resend 422 на `example.com` | **внешний сервис**, не данные. Чинится дёшево: мок Resend либо адрес `delivered@resend.dev` |
| `test_pending_count_y4::test_pending_count_sees_own_course_pending` | `assert 0 >= 1` | тест создаёт свой pending, но счётчик его не видит — похоже на **отставание теста от логики** (tsk-210 сузил pending до `is_correct IS TRUE`), а не на опору на чужие данные |
| `test_workload_y42::test_pending_manual_reviews_total_excludes_auto_checked` | `baseline=0, after=0` | тот же класс, что и выше |
| `test_tasks_order_position::test_t14_backfill_invariant_on_existing_data` | 3058 нарушений инварианта | тест по замыслу проверяет **состояние всей dev-БД**, а не свои данные. 3058 расхождений — вероятно реальный дрейф после перетега заданий (tsk-318/319). Самодостаточным делать нельзя, не потеряв смысл: это монитор данных, а не unit-тест |
| `test_tsk261_me_courses_open_attempt::test_open_attempt_result_counts_in_progress` | в изоляции **проходит** | ещё один порядко-зависимый, всплыл в полном прогоне; в исходном списке его не было |

### Оценка

Делать самодостаточными стоит два первых класса (magic_link — мок внешнего сервиса;
pending_count/workload — сперва выяснить, тест отстал или счётчик сломан: второе
означало бы продовый баг, а не дефект теста). `test_t14_backfill_invariant` трогать
не надо — он выполняет свою работу и, судя по 3058 нарушениям, прямо сейчас на
что-то указывает; это повод для `/db-check`, а не для правки теста.
`test_tsk261` — тот же класс, что и tsk-330, чинится тем же приёмом.

## Риски

- Прод-путь крона не изменён: сигнатура расширена значением по умолчанию, вызов из
  `start_scheduler()` (`escalation_service.py:146`) остался без аргументов.
- Cross-project impact отсутствует: HTTP-контракт, схема БД и Pydantic-модели не тронуты.
