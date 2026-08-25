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

tsk-675 закрыл этот механизм, и тест развёрнут в сторожа: он проверяет уже не
наличие очереди, а её отсутствие. Что именно проверяется:
1. из залпа строку переписывает ровно ОДИН запрос (дедупликацию держит условие
   внутри самого оператора, а не договорённость между запросами);
2. запросы больше не складываются по времени: залп с записью идёт вровень с
   залпом, которому писать нечего.

Троттлинг в одну минуту (`_LAST_USED_MIN_INTERVAL`) остался, но сам по себе он
залп не спасал: проверка «пора ли писать» — это чтение, потом запись, без
блокировки между ними, поэтому в открытое окно порога проходили ВСЕ
параллельные запросы разом. Оба свойства из списка выше держатся на
`_TOUCH_LAST_USED_SQL` — там же в докстринге замеры, включая замер варианта,
который выглядел решением, но им не оказался.

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
    """Считает записи в строку сессии: операторы и реально изменённые ими строки.

    Считать надо именно ИЗМЕНЁННЫЕ СТРОКИ (`rows`), а не операторы (`count`).
    После tsk-675 оператор по-прежнему выпускает каждый запрос залпа — иначе и
    не узнать, пора ли писать, — но условие внутри пропускает к строке ровно
    один из них, а остальные обновляют ноль строк и даже не ждут замок
    (`FOR UPDATE SKIP LOCKED`). До tsk-675 обе величины совпадали, поэтому
    операторов хватало как прокси; теперь они разошлись, и прокси врёт.
    """

    def __init__(self) -> None:
        self.count = 0
        self.rows = 0

    def attach(self, sync_engine) -> None:  # noqa: ANN001 — sqlalchemy Engine
        event.listen(sync_engine, "before_cursor_execute", self._before)
        event.listen(sync_engine, "after_cursor_execute", self._after)

    def detach(self, sync_engine) -> None:  # noqa: ANN001
        event.remove(sync_engine, "before_cursor_execute", self._before)
        event.remove(sync_engine, "after_cursor_execute", self._after)

    @staticmethod
    def _is_session_update(statement: str) -> bool:
        return " ".join(statement.split()).upper().startswith("UPDATE USER_SESSION")

    def _before(self, conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        if self._is_session_update(statement):
            self.count += 1

    def _after(self, conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        if self._is_session_update(statement):
            self.rows += max(cursor.rowcount, 0)


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
async def test_stale_mark_makes_only_one_request_in_burst_write(
    db_engine, db_session_factory, student_session
):
    """Из залпа строку сессии переписывает ровно ОДИН запрос (tsk-675).

    Главная проверка, и намеренно БЕЗ стенных часов: она считает изменённые
    строки, а счётчик не зависит ни от загрузки машины, ни от соседних чипов в
    дереве. До tsk-675 в открытое окно порога проходили все шестеро — проверка
    «пора ли писать» была чтением, а потом записью без блокировки между ними.
    Теперь дедупликацию держит сама база: условие внутри оператора.
    """
    counter = _UpdateCounter()
    counter.attach(db_engine.sync_engine)
    try:
        _, durations = await _burst(db_session_factory, student_session["token"])
    finally:
        counter.detach(db_engine.sync_engine)

    print(
        f"[tsk-675] устаревшая отметка: строк изменено {counter.rows} "
        f"(операторов {counter.count}) на {_BURST} запросов, "
        f"худший запрос {max(durations):.2f} с"
    )
    assert counter.rows == 1, (
        f"ожидали ровно 1 изменённую строку `user_session` на {_BURST} "
        f"запросов, получили {counter.rows}. Столько же, сколько запросов, — "
        f"значит дедупликация записи отвалилась и залп снова встаёт в очередь "
        f"за строкой сессии (tsk-675)"
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
async def test_burst_does_not_serialize_even_when_it_writes(
    db_session_factory, student_session, db
):
    """Запись отметки больше не складывает время залпа (tsk-675).

    Здесь стенные часы нужны — вопрос как раз про время. Но сравнение идёт с
    базовой линией, ИЗМЕРЕННОЙ В ЭТОМ ЖЕ ПРОГОНЕ, а не с константой: обе
    величины одинаково растут от посторонней нагрузки, и разность между ними
    устойчива. Абсолютный порог тут уже краснел от соседних чипов в дереве —
    тест, который краснеет от чужой работы, перестают читать, и это ровно тот
    класс, за который в этом же контуре ругали висящий тест tsk-671.

    Замеры на спокойной машине, залп из шести при удержании 0,4 с:

    * до tsk-675 (безусловная запись) — 2,95-3,10 с против 0,90-1,01 с без записи;
    * условный `UPDATE` БЕЗ `SKIP LOCKED` — 2,95-3,00 с, то есть без пользы:
      ждущий встаёт на замок раньше, чем перепроверяет условие;
    * после tsk-675 — 0,95-1,28 с, вровень с залпом без записи.

    Проверка обратная прежней: раньше тест доказывал, что сложение ЕСТЬ
    (так был пойман механизм), теперь сторожит, что его НЕТ.
    """
    # Базовая линия: тот же код, тот же залп, только писать нечего.
    await _set_mark_age(db, student_session["session_id"], stale=False)
    free_wall, _ = await _burst(db_session_factory, student_session["token"])

    # Тот же залп, но отметка устарела — запись появляется.
    await _set_mark_age(db, student_session["session_id"], stale=True)
    writing_wall, durations = await _burst(db_session_factory, student_session["token"])

    print(
        f"[tsk-675] залп без записи {free_wall:.2f} с против {writing_wall:.2f} с "
        f"с записью (разница {writing_wall - free_wall:+.2f} с), "
        f"худший запрос {max(durations):.2f} с"
    )
    # Сравниваем РАЗНИЦУ, а не отношение: накладные расходы стенда (поднять
    # шесть соединений) входят в обе величины одинаково и в разности
    # сокращаются, а в отношении — нет.
    #
    # Порог — одно удержание. Сериализация давала бы разницу в пять удержаний
    # (`_BURST - 1`), так что запас до ложного срабатывания четырёхкратный, а
    # чужая нагрузка на машине входит в обе величины и вычитается.
    allowed_gap = _HOLD_SECONDS
    assert writing_wall - free_wall < allowed_gap, (
        f"с записью залп занял {writing_wall:.2f} с против {free_wall:.2f} с без "
        f"неё — разница {writing_wall - free_wall:.2f} с, допускали меньше "
        f"{allowed_gap:.2f} с. Запись снова выстраивает залп в очередь за "
        f"строкой сессии — вернулась авария tsk-621/tsk-675"
    )
    # Отдельной проверки на худший запрос нет намеренно: запросы идут одним
    # `gather`, поэтому время залпа и есть время худшего из них, и вторая
    # проверка сторожила бы ту же величину. Абсолютный порог на худший запрос
    # тем более не годится: в него входит подъём соединения (NullPool), а это
    # полсекунды стенда поверх удержания.
