"""tsk-996 (хвост ревью фазы D): якорь задания банка против снятия прозрачности.

Две записи одной пары «банк — урок» захватывают строки в обратном порядке:

* установка якоря (PATCH): строка задания захватывается UPDATE-ом, затем
  BEFORE-триггер-страж `tasks_anchor_guard` берёт `FOR SHARE` материал и
  прозрачную связь `course_parents`;
* снятие прозрачности: UPDATE строки `course_parents`, затем AFTER-триггер
  `course_parents_anchor_on_unlink` обнуляет якоря заданий банка на
  материалы урока — захватывает строки заданий.

Если у задания уже стоит якорь на материал урока (методист переставляет его
на другой материал того же урока), обе транзакции тянутся к строке друг друга
— взаимоблокировка. Сцены:

- `test_guard_order_deadlocks_...` — контрольная: порядок стража (задание,
  затем связь) против снятия связи даёт 40P01. PostgreSQL снимает одну
  транзакцию целиком; повтор снятой проходит, данные согласованы. Без этой
  сцены зелёные тесты правки ничего не доказывали бы.
- `test_validate_anchor_prelock_...` — рабочий путь: `validate_anchor` до
  записи задания берёт `FOR SHARE` материал и связь (порядок стража). Снятие
  связи ждёт записи якоря и затем обнуляет его; либо, успев первым, оставляет
  методисту 422 (`DomainError`). Взаимоблокировки нет.

Модуль работает вне общей откатываемой транзакции: нужны настоящие
параллельные транзакции. Уборка своя.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.error_handlers import is_deadlock_error
from app.core.config import Settings
from app.models.tasks import Tasks
from app.services.tasks_service import TasksService
from app.utils.exceptions import DomainError
from tests.test_tsk996_anchor import _anchor
from tests.test_tsk996_node_sequence import _course, _link, _material, _task

pytestmark = [pytest.mark.no_tx_isolation]

_settings = Settings()

#: Пауза, за которую вторая транзакция успевает захватить свою первую строку.
_HANDSHAKE_PAUSE_S = 0.4
#: PostgreSQL объявляет взаимоблокировку не раньше `deadlock_timeout` (1 с).
_DEADLOCK_WAIT_S = 15.0

_UNLINK_SQL = (
    "UPDATE course_parents SET is_transparent = false "
    "WHERE course_id = :bank AND parent_course_id = :lesson"
)
_SET_ANCHOR_SQL = "UPDATE tasks SET anchor_material_id = :m, anchor_source = 'manual' WHERE id = :t"


@pytest_asyncio.fixture(scope="function")
async def engine():
    eng = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture(scope="function")
async def scene(engine):
    """Урок (m1, m2), банк прозрачен для урока; задание банка с якорем на m1."""
    async with AsyncSession(engine, expire_on_commit=False) as s:
        lesson = await _course(s, "якорь-блокировки урок")
        bank = await _course(s, "якорь-блокировки банк")
        await _link(s, lesson, bank, 1, transparent=True)
        m1 = await _material(s, lesson, 1)
        m2 = await _material(s, lesson, 2)
        task = await _task(s, bank, 1, host_position=100)
        await _anchor(s, task, m1)
        await s.commit()
    try:
        yield dict(lesson=lesson, bank=bank, m1=m1, m2=m2, task=task)
    finally:
        async with AsyncSession(engine) as s:
            # Задания и материалы уходят каскадом вместе с курсами.
            await s.execute(
                text("DELETE FROM courses WHERE id = ANY(:ids)"), {"ids": [bank, lesson]}
            )
            await s.commit()


async def _state(engine, sc: dict[str, Any]) -> tuple[Any, Any, bool]:
    async with AsyncSession(engine) as s:
        anchor, source = (
            await s.execute(
                text("SELECT anchor_material_id, anchor_source FROM tasks WHERE id = :t"),
                {"t": sc["task"]},
            )
        ).one()
        transparent = (
            await s.execute(
                text(
                    "SELECT is_transparent FROM course_parents "
                    "WHERE course_id = :bank AND parent_course_id = :lesson"
                ),
                {"bank": sc["bank"], "lesson": sc["lesson"]},
            )
        ).scalar_one()
    return anchor, source, bool(transparent)


async def _unlink(engine, sc: dict[str, Any]) -> None:
    async with AsyncSession(engine) as s:
        await s.execute(text(_UNLINK_SQL), sc)
        await s.commit()


async def _wait_until_blocked(engine, pid: int) -> None:
    """Дождаться, пока сеанс `pid` встанет в ожидание блокировки строки.

    Надёжнее паузы: на загруженной машине второе соединение могло не успеть
    захватить свою строку за фиксированное время, и сцена молча менялась бы.
    """
    async with AsyncSession(engine) as s:
        for _ in range(int(_DEADLOCK_WAIT_S / 0.05)):
            waiting = (
                await s.execute(
                    text(
                        "SELECT wait_event_type = 'Lock' FROM pg_stat_activity "
                        "WHERE pid = :pid"
                    ),
                    {"pid": pid},
                )
            ).scalar()
            if waiting:
                return
            await s.rollback()  # свежий снимок pg_stat_activity
            await asyncio.sleep(0.05)
    raise AssertionError(f"сеанс {pid} так и не встал в ожидание блокировки")


async def _set_anchor_via_service(engine, sc: dict[str, Any], material: int) -> None:
    async with AsyncSession(engine) as s:
        task = await s.get(Tasks, sc["task"])
        await TasksService().validate_anchor(s, task, material)
        await s.execute(text(_SET_ANCHOR_SQL), {"m": material, "t": sc["task"]})
        await s.commit()


async def test_guard_order_deadlocks_against_unlink_and_retry_heals(engine, scene) -> None:
    """Контроль: порядок стража (задание → связь) против снятия связи — 40P01; повтор лечит."""
    sc = scene
    task_locked = asyncio.Event()
    unlink_pid: list[int] = []

    async def set_anchor_guard_order() -> None:
        async with AsyncSession(engine) as s:
            # Строка задания захвачена до стража — как у UPDATE с BEFORE-триггером.
            await s.execute(
                text("SELECT 1 FROM tasks WHERE id = :t FOR UPDATE"), {"t": sc["task"]}
            )
            task_locked.set()
            # Снятие связи захватило связь и ждёт строку задания.
            while not unlink_pid:
                await asyncio.sleep(0.01)
            await _wait_until_blocked(engine, unlink_pid[0])
            await s.execute(text(_SET_ANCHOR_SQL), {"m": sc["m2"], "t": sc["task"]})
            await s.commit()

    async def unlink() -> None:
        await task_locked.wait()
        async with AsyncSession(engine) as s:
            unlink_pid.append(int((await s.execute(text("SELECT pg_backend_pid()"))).scalar_one()))
            await s.execute(text(_UNLINK_SQL), sc)
            await s.commit()

    results = await asyncio.wait_for(
        asyncio.gather(set_anchor_guard_order(), unlink(), return_exceptions=True),
        timeout=_DEADLOCK_WAIT_S,
    )
    deadlocks = [r for r in results if isinstance(r, BaseException) and is_deadlock_error(r)]
    assert len(deadlocks) == 1, f"сцена не воспроизвела взаимоблокировку: {results!r}"
    assert [r for r in results if r is not None and r not in deadlocks] == []

    # Снятая транзакция откачена целиком; данные согласованы уже сейчас.
    anchor, source, transparent = await _state(engine, sc)
    if results[0] is None:
        # Выжил якорь: связь осталась прозрачной, якорь — новый.
        assert (anchor, source, transparent) == (sc["m2"], "manual", True)
        await _unlink(engine, sc)  # повтор снятия
    else:
        # Выжило снятие: связь непрозрачна, якорь на урок обнулён.
        assert (anchor, source, transparent) == (None, None, False)
        with pytest.raises(DomainError):  # повтор PATCH — честный 422
            await _set_anchor_via_service(engine, sc, sc["m2"])
    assert await _state(engine, sc) == (None, None, False)


async def test_validate_anchor_prelock_then_unlink_waits(engine, scene) -> None:
    """Рабочий путь, якорь первым: снятие связи ждёт и затем обнуляет новый якорь.

    Без предварительной блокировки в `validate_anchor` снятие успевает раньше
    записи, и запись якоря падает на страже (DBAPIError) — тест это отличает.
    """
    sc = scene
    prelocked = asyncio.Event()
    unlink_pid: list[int] = []

    async def set_anchor() -> None:
        async with AsyncSession(engine) as s:
            task = await s.get(Tasks, sc["task"])
            await TasksService().validate_anchor(s, task, sc["m2"])
            prelocked.set()
            while not unlink_pid:
                await asyncio.sleep(0.01)
            # Снятие связи действительно ждёт нашей блокировки связи.
            await _wait_until_blocked(engine, unlink_pid[0])
            await s.execute(text(_SET_ANCHOR_SQL), {"m": sc["m2"], "t": sc["task"]})
            await s.commit()

    async def unlink() -> None:
        await prelocked.wait()
        async with AsyncSession(engine) as s:
            unlink_pid.append(int((await s.execute(text("SELECT pg_backend_pid()"))).scalar_one()))
            await s.execute(text(_UNLINK_SQL), sc)
            await s.commit()

    results = await asyncio.wait_for(
        asyncio.gather(set_anchor(), unlink(), return_exceptions=True),
        timeout=_DEADLOCK_WAIT_S,
    )
    assert results == [None, None], results
    assert await _state(engine, sc) == (None, None, False)


async def test_validate_anchor_after_pending_unlink_is_domain_error(engine, scene) -> None:
    """Рабочий путь, снятие первым: проверка ждёт его коммита и отказывает (422)."""
    sc = scene
    unlinked = asyncio.Event()

    async def unlink() -> None:
        async with AsyncSession(engine) as s:
            await s.execute(text(_UNLINK_SQL), sc)
            unlinked.set()
            await asyncio.sleep(_HANDSHAKE_PAUSE_S)
            await s.commit()

    async def set_anchor() -> None:
        await unlinked.wait()
        await _set_anchor_via_service(engine, sc, sc["m2"])

    results = await asyncio.wait_for(
        asyncio.gather(unlink(), set_anchor(), return_exceptions=True),
        timeout=_DEADLOCK_WAIT_S,
    )
    assert results[0] is None, results
    assert isinstance(results[1], DomainError), results
    assert await _state(engine, sc) == (None, None, False)
