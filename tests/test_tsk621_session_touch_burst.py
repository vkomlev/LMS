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


@pytest.mark.asyncio
async def test_burst_of_one_user_all_write_and_serialize(
    db_engine, db_session_factory, student_session
):
    """Залп одного ученика: пишут все шестеро и стоят друг за другом.

    Это и есть механизм, который объясняет заторы: время запросов складывается,
    хотя работы в них нет — они ждут строку сессии.
    """
    counter = _UpdateCounter()
    counter.attach(db_engine.sync_engine)
    try:
        started = time.perf_counter()
        durations = await asyncio.gather(
            *(_one_request(db_session_factory, student_session["token"]) for _ in range(_BURST))
        )
        wall = time.perf_counter() - started
    finally:
        counter.detach(db_engine.sync_engine)

    alone = _HOLD_SECONDS
    serialized = _HOLD_SECONDS * _BURST
    print(
        f"[tsk-621] устаревшая отметка: залп {_BURST} запросов за {wall:.2f} с "
        f"(удержание {alone:.2f} с на запрос), записей UPDATE user_session: "
        f"{counter.count}, худший запрос {max(durations):.2f} с"
    )

    assert counter.count == _BURST, (
        f"порог троттлинга открыт для всех сразу: ожидали {_BURST} записей "
        f"`UPDATE user_session`, получили {counter.count}. Меньше — значит "
        f"дедупликация появилась и версия требует пересмотра"
    )
    assert wall > serialized * 0.7, (
        f"залп прошёл за {wall:.2f} с при удержании {alone:.2f} с на запрос — "
        f"сериализации НЕТ, версия про строку сессии не подтверждается"
    )
    # Последний в очереди ждёт почти всё время залпа — именно так выглядит
    # «лёгкий запрос простоял 44 секунды» в журнале медленных.
    assert max(durations) > alone * (_BURST - 1), (
        f"худший запрос {max(durations):.2f} с при удержании {alone:.2f} с — "
        f"очереди за строкой сессии не видно"
    )


@pytest.mark.asyncio
async def test_burst_is_free_when_mark_is_fresh(
    db_engine, db_session_factory, student_session, db
):
    """Свежая отметка — записи нет вовсе, залп идёт параллельно.

    Контроль к тесту выше: он доказывает, что сложение времени даёт именно
    `UPDATE`, а не что-нибудь другое в `validate_session` или в стенде.
    Заодно видно цену вопроса — тот же залп без записи проходит за время
    ОДНОГО запроса.
    """
    await db.execute(
        text("UPDATE user_session SET last_used_at = now() WHERE id = :sid"),
        {"sid": student_session["session_id"]},
    )
    await db.commit()

    counter = _UpdateCounter()
    counter.attach(db_engine.sync_engine)
    try:
        started = time.perf_counter()
        await asyncio.gather(
            *(_one_request(db_session_factory, student_session["token"]) for _ in range(_BURST))
        )
        wall = time.perf_counter() - started
    finally:
        counter.detach(db_engine.sync_engine)

    print(
        f"[tsk-621] свежая отметка: залп {_BURST} запросов за {wall:.2f} с, "
        f"записей UPDATE user_session: {counter.count}"
    )
    assert counter.count == 0, "свежую отметку переписывать незачем"
    # Порог берём от СЕРИАЛИЗОВАННОГО случая, а не от одного удержания: залп
    # из шести запросов поднимает шесть новых соединений, и накладные расходы
    # стенда (~0,4 с) к делу не относятся. Важно, что без записи время НЕ
    # складывается: 0,9 с против 2,5 с с записью.
    assert wall < _HOLD_SECONDS * _BURST * 0.5, (
        f"без записи залп занял {wall:.2f} с — при отсутствии сериализации "
        f"ожидали заметно меньше {_HOLD_SECONDS * _BURST:.2f} с; "
        f"значит сериализует что-то ещё, и версия про строку сессии неполна"
    )
