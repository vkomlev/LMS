"""tsk-621/tsk-655: залп параллельных запросов ОДНОГО ученика встаёт в очередь
за строкой его сессии.

Зачем этот тест существует. Заторы на границе занятия разбираются с 18.08, и
семь правдоподобных версий подряд были опровергнуты замерами. Восьмая — эта:
`validate_session` обновляет `user_session.last_used_at` в начале КАЖДОГО
запроса, а `UPDATE` держит блокировку строки до конца транзакции запроса.
Значит параллельные запросы одного ученика (кабинет открывает дерево курса
веером, предел параллельности SPW — 6) выстраиваются друг за другом, и один
медленный запрос держит остальные всё своё время.

Ключевое — воспроизвести это ДЕТЕРМИНИРОВАННО, а не ждать затора на бою:
окно караула уже один раз закрылось за две минуты до события (25.08), и цена
такого ожидания — сутки на попытку. Если механизм здесь не воспроизводится,
версия падает восьмой за полчаса, а не за неделю окон.

Что именно проверяется:
1. троттлинг в одну минуту (`_LAST_USED_MIN_INTERVAL`) залп НЕ спасает:
   проверка «пора ли писать» — это чтение, потом запись, без блокировки между
   ними, поэтому в открытое окно порога проходят ВСЕ параллельные запросы;
2. запросы реально сериализуются: суммарное время растёт кратно их числу, а не
   остаётся временем одного.

`no_tx_isolation` обязателен: на одном общем соединении блокировок строк не
бывает вовсе, и проверка обнулилась бы (см. докстринг `db_conn` в conftest).

Про стенные часы. Две проверки из трёх считают ОПЕРАТОРЫ, а не секунды, и от
загрузки машины не зависят вовсе. Третьей часы нужны по существу вопроса, но
она сравнивает с базовой линией, измеренной в том же прогоне, и по РАЗНИЦЕ, а
не по отношению: накладные расходы стенда входят в обе величины и в разности
сокращаются. Замер под намеренной параллельной нагрузкой: разница 1,94 с
против 2,05 с на спокойной машине, а отношение просело с 3,4 до 2,8 — то есть
порог по отношению был бы уже на грани.

Известное и НЕ объяснённое: один раз из восьми прогонов набор упал двумя
тестами, воспроизвести не удалось ни разу, в том числе под нагрузкой. Причина
неизвестна; ближайший знакомый класс — соседняя сессия на той же dev-базе.
Если увидите повтор — не списывайте на «мигает», зафиксируйте вывод.
"""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from sqlalchemy import event, text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import (
    _LAST_USED_MIN_INTERVAL,  # noqa: PLC2701 — тест про сам порог
    create_session,
    validate_session,
)

pytestmark = pytest.mark.no_tx_isolation

#: Сколько параллельных запросов делает вкладка ученика. Ровно предел
#: параллельности SPW (`COURSE_TREE_CONCURRENCY = 6`, tsk-622): дерево курса
#: выпускает запросы веером, и это максимум одновременных.
_BURST = 6

#: Сколько «работает» каждый запрос после проверки сессии. Малая величина:
#: тест меряет НЕ скорость, а есть ли сложение — при сериализации суммарное
#: время кратно `_BURST`, без неё равно одному удержанию.
_HOLD_SECONDS = 0.4


@pytest.fixture
async def student_session(db):
    """Ученик с сессией, отметка которой заведомо старше порога троттлинга."""
    email = f"tsk621-burst-{uuid.uuid4().hex[:12]}@example.test"
    user = Users(email=email, password_hash=None, full_name="tsk621 burst", tg_id=None)
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", email)
    access_token, _, session = await create_session(db, user_id=user.id)
    await db.commit()

    # Отметка старше порога — значит очередной запрос обязан её обновить.
    await db.execute(
        text(
            "UPDATE user_session SET last_used_at = now() - CAST(:gap AS interval) "
            "WHERE id = :sid"
        ),
        {"gap": _LAST_USED_MIN_INTERVAL * 5, "sid": session.id},
    )
    await db.commit()

    data = {"user_id": user.id, "session_id": session.id, "token": access_token}
    yield data

    await db.execute(text("DELETE FROM user_session WHERE user_id = :u"), {"u": user.id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = :u"), {"u": user.id})
    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user.id})
    await db.commit()


class _UpdateCounter:
    """Считает операторы `UPDATE user_session` на движке теста."""

    def __init__(self) -> None:
        self.count = 0

    def attach(self, sync_engine) -> None:  # noqa: ANN001 — sqlalchemy Engine
        event.listen(sync_engine, "before_cursor_execute", self._before)

    def detach(self, sync_engine) -> None:  # noqa: ANN001
        event.remove(sync_engine, "before_cursor_execute", self._before)

    def _before(self, conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        normalized = " ".join(statement.split()).upper()
        if normalized.startswith("UPDATE USER_SESSION"):
            self.count += 1


async def _one_request(session_factory, token: str) -> float:
    """Один запрос: проверка сессии, работа, коммит. Возвращает своё время."""
    started = time.perf_counter()
    async with session_factory() as db_session:
        found = await validate_session(db_session, token)
        assert found is not None, "сессия обязана быть валидной"
        # Транзакция запроса ещё открыта — блокировка строки сессии держится
        # именно здесь, а не только на время самого UPDATE.
        await asyncio.sleep(_HOLD_SECONDS)
        await db_session.commit()
    return time.perf_counter() - started


async def _burst(session_factory, token: str) -> tuple[float, list[float]]:
    """Залп из `_BURST` параллельных запросов. Возвращает (общее время, времена)."""
    started = time.perf_counter()
    durations = await asyncio.gather(
        *(_one_request(session_factory, token) for _ in range(_BURST))
    )
    return time.perf_counter() - started, list(durations)


async def _set_mark_age(db, session_id, *, stale: bool) -> None:
    """Состарить отметку сессии за порог троттлинга или, наоборот, освежить."""
    await db.execute(
        text(
            "UPDATE user_session "
            "SET last_used_at = now() - CAST(:gap AS interval) WHERE id = :sid"
        ),
        {"gap": _LAST_USED_MIN_INTERVAL * 5 if stale else _LAST_USED_MIN_INTERVAL * 0,
         "sid": session_id},
    )
    await db.commit()


@pytest.mark.asyncio
async def test_stale_mark_makes_every_request_in_burst_write(
    db_engine, db_session_factory, student_session
):
    """Троттлинг в минуту залп НЕ спасает: пишут все шестеро.

    Главная проверка механизма, и намеренно БЕЗ стенных часов: она считает
    операторы, а счётчик не зависит ни от загрузки машины, ни от соседних
    чипов в дереве. Проверка «пора ли писать» — это чтение, потом запись без
    блокировки между ними, поэтому в открытое окно порога проходят все
    параллельные запросы разом.
    """
    counter = _UpdateCounter()
    counter.attach(db_engine.sync_engine)
    try:
        _, durations = await _burst(db_session_factory, student_session["token"])
    finally:
        counter.detach(db_engine.sync_engine)

    print(
        f"[tsk-621] устаревшая отметка: записей UPDATE user_session "
        f"{counter.count} на {_BURST} запросов, худший запрос {max(durations):.2f} с"
    )
    assert counter.count == _BURST, (
        f"ожидали {_BURST} записей `UPDATE user_session`, получили "
        f"{counter.count}. Стало меньше — значит дедупликация появилась "
        f"(этого и добивается tsk-675), и тест пора переписывать под неё"
    )


@pytest.mark.asyncio
async def test_fresh_mark_makes_burst_write_nothing(
    db_engine, db_session_factory, student_session, db
):
    """Свежая отметка — записи нет вовсе. Тоже только счётчик, без часов."""
    await _set_mark_age(db, student_session["session_id"], stale=False)

    counter = _UpdateCounter()
    counter.attach(db_engine.sync_engine)
    try:
        await _burst(db_session_factory, student_session["token"])
    finally:
        counter.detach(db_engine.sync_engine)

    print(f"[tsk-621] свежая отметка: записей UPDATE user_session {counter.count}")
    assert counter.count == 0, "свежую отметку переписывать незачем"


@pytest.mark.asyncio
async def test_burst_serializes_only_when_it_writes(
    db_session_factory, student_session, db
):
    """Сложение времени даёт именно запись: сравниваем залп с ней и без неё.

    Здесь стенные часы нужны — вопрос как раз про время. Но сравнение идёт с
    базовой линией, ИЗМЕРЕННОЙ В ЭТОМ ЖЕ ПРОГОНЕ, а не с константой: обе
    величины одинаково растут от посторонней нагрузки, и отношение между ними
    устойчиво. Абсолютный порог тут уже краснел от соседних чипов в дереве —
    тест, который краснеет от чужой работы, перестают читать, и это ровно тот
    класс, за который в этом же контуре ругали висящий тест tsk-671.

    Замер на спокойной машине: 0,93 с без записи против 2,99 с с записью.
    """
    # Базовая линия: тот же код, тот же залп, только писать нечего.
    await _set_mark_age(db, student_session["session_id"], stale=False)
    free_wall, _ = await _burst(db_session_factory, student_session["token"])

    # Тот же залп, но отметка устарела — появляется запись и очередь за строкой.
    await _set_mark_age(db, student_session["session_id"], stale=True)
    writing_wall, durations = await _burst(db_session_factory, student_session["token"])

    print(
        f"[tsk-621] залп без записи {free_wall:.2f} с против {writing_wall:.2f} с "
        f"с записью (отношение {writing_wall / free_wall:.1f}), "
        f"худший запрос {max(durations):.2f} с"
    )
    # Сравниваем РАЗНИЦУ, а не отношение: накладные расходы стенда (поднять
    # шесть соединений) входят в обе величины одинаково и в разности
    # сокращаются, а в отношении — нет. Под сильной нагрузкой отношение
    # ползёт к единице даже при живой сериализации, и тест снова начал бы
    # краснеть от чужой работы.
    expected_gap = _HOLD_SECONDS * (_BURST - 2)
    assert writing_wall - free_wall > expected_gap, (
        f"с записью залп занял {writing_wall:.2f} с против {free_wall:.2f} с без "
        f"неё — разница {writing_wall - free_wall:.2f} с, ожидали больше "
        f"{expected_gap:.2f} с. Сложения времени НЕТ, версия про очередь за "
        f"строкой сессии не подтверждается"
    )
    # Последний в очереди ждёт почти весь залп — именно так выглядит
    # «лёгкий by-course простоял 44 секунды» в журнале медленных.
    assert max(durations) > writing_wall * 0.8, (
        f"худший запрос {max(durations):.2f} с при залпе {writing_wall:.2f} с — "
        f"очереди за строкой сессии не видно, запросы шли независимо"
    )
