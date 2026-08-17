"""tsk-626: взаимоблокировка на кеше `student_course_state`.

Авария 17.08.2026, 12:56 UTC на проде: `GET /api/v1/learning/next-item` упал с
`asyncpg.exceptions.DeadlockDetectedError` на upsert `student_course_state`
(ученик 3, курс 1455) — ученик получил 500. Два параллельных писателя одного
ученика захватывали строки кеша в разном порядке и встали в цикл ожидания.

Сцена здесь ровно та: две транзакции пишут ПЕРЕСЕКАЮЩИЕСЯ наборы курсов одного
ученика в ОБРАТНОМ порядке. Проверяется два раза одной и той же расстановкой,
меняется только способ записи:

- `test_raw_upsert_deadlocks_...` — контрольный. Голый upsert (как было до
  правки) обязан дать взаимоблокировку. Без этого теста «зелёный» второй тест
  ничего не доказывал бы: он мог бы проходить просто потому, что сцена не
  воспроизводит гонку.
- `test_locked_upsert_survives_...` — тот же порядок через
  `upsert_course_state`, который берёт блокировку ученика. Обе транзакции
  доходят до конца.

Модуль работает вне общей откатываемой транзакции: взаимоблокировку нельзя
воспроизвести на одном соединении — нужны настоящие параллельные транзакции.
Уборка своя.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.api.error_handlers import is_deadlock_error
from app.core.config import Settings
from app.services.course_dependency_state_cron_service import (
    _COURSE_DEPENDENCY_STATE_LOCK_KEY,
    course_dependency_state_cron_tick,
)
from app.services.learning_engine_service import upsert_course_state

pytestmark = [pytest.mark.no_tx_isolation]

_settings = Settings()

#: Пауза, за которую вторая транзакция успевает сделать свою первую запись.
#: PostgreSQL объявляет взаимоблокировку не раньше `deadlock_timeout` (по
#: умолчанию 1 с), поэтому ждать приходится чуть дольше него.
_HANDSHAKE_PAUSE_S = 0.4
_DEADLOCK_WAIT_S = 15.0


async def _raw_upsert(db: AsyncSession, student_id: int, course_id: int) -> None:
    """Запись кеша БЕЗ блокировки ученика — код в том виде, в каком он упал."""
    await db.execute(
        text(
            "INSERT INTO student_course_state (student_id, course_id, state, updated_at) "
            "VALUES (:s, :c, 'NOT_STARTED', now()) "
            "ON CONFLICT (student_id, course_id) "
            "DO UPDATE SET state = EXCLUDED.state, updated_at = now()"
        ),
        {"s": student_id, "c": course_id},
    )


async def _locked_upsert(db: AsyncSession, student_id: int, course_id: int) -> None:
    """Запись кеша через рабочий путь приложения (tsk-626)."""
    await upsert_course_state(db, student_id, course_id, "NOT_STARTED")


@pytest_asyncio.fixture(scope="function")
async def scene():
    """Ученик и два курса, чьи строки кеша будут захватываться крест-накрест."""
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        student_id = (
            await s.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES ('tsk626 взаимоблокировка кеша', :e, true) RETURNING id"
                ),
                {"e": f"tsk626-deadlock-{uuid.uuid4().hex[:12]}@example.test"},
            )
        ).scalar_one()
        course_ids = []
        for suffix in ("A", "B"):
            course_ids.append(
                int(
                    (
                        await s.execute(
                            text(
                                "INSERT INTO courses (title, access_level) "
                                "VALUES (:t, 'self_guided') RETURNING id"
                            ),
                            {"t": f"tsk626 курс {suffix} {uuid.uuid4().hex[:8]}"},
                        )
                    ).scalar_one()
                )
            )
        await s.commit()

    try:
        yield int(student_id), course_ids[0], course_ids[1]
    finally:
        async with AsyncSession(engine) as s:
            await s.execute(
                text("DELETE FROM student_course_state WHERE student_id = :s"),
                {"s": student_id},
            )
            await s.execute(
                text("DELETE FROM courses WHERE id = ANY(:ids)"), {"ids": course_ids}
            )
            await s.execute(text("DELETE FROM users WHERE id = :s"), {"s": student_id})
            await s.commit()
        await engine.dispose()


async def _cross_write(scene, writer) -> list[BaseException | None]:
    """Две транзакции пишут курсы A и B одного ученика в обратном порядке.

    Расстановка односторонняя и потому детерминированная: вторая транзакция
    стартует только после того, как первая уже держит строку курса A. Дальше
    вторая берёт B и тянется к A, первая — тянется к B. На голом upsert это
    замкнутый круг, который PostgreSQL разрывает, сняв одну из транзакций.
    """
    student_id, course_a, course_b = scene
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    first_row_taken = asyncio.Event()

    async def tx_forward() -> None:
        async with AsyncSession(engine) as s:
            await writer(s, student_id, course_a)
            first_row_taken.set()
            await asyncio.sleep(_HANDSHAKE_PAUSE_S)
            await writer(s, student_id, course_b)
            await s.commit()

    async def tx_reverse() -> None:
        async with AsyncSession(engine) as s:
            await first_row_taken.wait()
            await writer(s, student_id, course_b)
            await writer(s, student_id, course_a)
            await s.commit()

    try:
        return await asyncio.wait_for(
            asyncio.gather(tx_forward(), tx_reverse(), return_exceptions=True),
            timeout=_DEADLOCK_WAIT_S,
        )
    finally:
        await engine.dispose()


async def test_raw_upsert_deadlocks_on_reversed_course_order(scene):
    """Контроль: без блокировки ученика сцена ДЕЙСТВИТЕЛЬНО даёт взаимоблокировку.

    Этот тест обязан падать на исправленном коде, если из
    `upsert_course_state` убрать блокировку, — он проверяет саму сцену, а не
    правку. Зелёный тест правки без него не значил бы ничего.
    """
    results = await _cross_write(scene, _raw_upsert)

    deadlocks = [r for r in results if isinstance(r, BaseException) and is_deadlock_error(r)]
    assert len(deadlocks) == 1, (
        "сцена не воспроизвела взаимоблокировку — значит тест правки ниже "
        f"ничего не проверяет; результаты: {results!r}"
    )
    # `is_deadlock_error` обязан отличать взаимоблокировку по SQLSTATE 40P01,
    # а не по тексту: именно на этом различении построен повтор в next-item.
    assert isinstance(deadlocks[0], DBAPIError)
    assert getattr(deadlocks[0].orig, "sqlstate", None) == "40P01"


async def test_locked_upsert_survives_reversed_course_order(scene):
    """Правка: та же сцена через `upsert_course_state` проходит целиком."""
    student_id, course_a, course_b = scene
    results = await _cross_write(scene, _locked_upsert)

    failures = [r for r in results if isinstance(r, BaseException)]
    assert not failures, f"взаимоблокировка не устранена: {failures!r}"

    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT course_id, state FROM student_course_state "
                        "WHERE student_id = :s ORDER BY course_id"
                    ),
                    {"s": student_id},
                )
            ).fetchall()
    finally:
        await engine.dispose()

    assert {int(r[0]) for r in rows} == {course_a, course_b}, (
        f"кеш записан не полностью: {rows!r}"
    )
    assert all(r[1] == "NOT_STARTED" for r in rows), rows


async def test_deadlock_detector_ignores_other_db_errors(scene):
    """`is_deadlock_error` не должен принимать за взаимоблокировку любую ошибку базы.

    Повтор в `next-item` опирается на этот предикат: если он станет отвечать
    «да» на обычную ошибку запроса, эндпоинт будет молча повторять заведомо
    безнадёжную транзакцию вместо честной 500.
    """
    student_id, course_a, _ = scene
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as s:
            with pytest.raises(DBAPIError) as caught:
                # Нарушение внешнего ключа: курса с таким id нет.
                await s.execute(
                    text(
                        "INSERT INTO student_course_state "
                        "(student_id, course_id, state, updated_at) "
                        "VALUES (:s, -1, 'NOT_STARTED', now())"
                    ),
                    {"s": student_id},
                )
            await s.rollback()
    finally:
        await engine.dispose()

    assert not is_deadlock_error(caught.value), (
        "ошибка внешнего ключа принята за взаимоблокировку — повтор в next-item "
        "начнёт срабатывать не на своём классе ошибок"
    )
    assert not is_deadlock_error(ValueError("не ошибка базы вовсе"))


# ---------------------------------------------------------------------------
# Фоновый тик состояний (tsk-541) — вторая сторона той же взаимоблокировки.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def dep_scene():
    """Дерево с зависимостью подкурса и ДВА активных ученика в нём.

    Двое нужны по существу: замер на проде показал 460 строк кеша по 40
    ученикам с одинаковым `updated_at`, то есть весь проход тика шёл одной
    транзакцией и держал блокировки всех учеников до конца. Проверить, что
    коммит теперь идёт по ученику, на одном ученике нельзя.
    """
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    ids: dict[str, int] = {}
    students: list[int] = []
    async with AsyncSession(engine, expire_on_commit=False) as s:
        for key, title in (
            ("root", "tsk626 root"),
            ("child_a", "tsk626 пререквизит"),
            ("child_b", "tsk626 закрытый подкурс"),
        ):
            ids[key] = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO courses (title, access_level) "
                            "VALUES (:t, 'self_guided') RETURNING id"
                        ),
                        {"t": f"{title} {uuid.uuid4().hex[:8]}"},
                    )
                ).scalar_one()
            )
        for child in ("child_a", "child_b"):
            await s.execute(
                text(
                    "INSERT INTO course_parents (course_id, parent_course_id) "
                    "VALUES (:c, :p)"
                ),
                {"c": ids[child], "p": ids["root"]},
            )
        await s.execute(
            text(
                "INSERT INTO course_dependencies (course_id, required_course_id) "
                "VALUES (:c, :r)"
            ),
            {"c": ids["child_b"], "r": ids["child_a"]},
        )
        for n in (1, 2):
            sid = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO users (full_name, email, is_active) "
                            "VALUES (:n, :e, true) RETURNING id"
                        ),
                        {
                            "n": f"tsk626 ученик {n}",
                            "e": f"tsk626-tick-{n}-{uuid.uuid4().hex[:10]}@example.test",
                        },
                    )
                ).scalar_one()
            )
            students.append(sid)
            await s.execute(
                text(
                    "INSERT INTO user_courses (user_id, course_id, is_active) "
                    "VALUES (:u, :c, true)"
                ),
                {"u": sid, "c": ids["root"]},
            )
        await s.commit()

    try:
        yield ids, students
    finally:
        async with AsyncSession(engine) as s:
            await s.execute(
                text("DELETE FROM student_course_state WHERE student_id = ANY(:ids)"),
                {"ids": students},
            )
            await s.execute(
                text("DELETE FROM user_courses WHERE user_id = ANY(:ids)"),
                {"ids": students},
            )
            await s.execute(
                text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": students}
            )
            await s.execute(
                text("DELETE FROM course_dependencies WHERE course_id = :c"),
                {"c": ids["child_b"]},
            )
            await s.execute(
                text("DELETE FROM course_parents WHERE parent_course_id = :p"),
                {"p": ids["root"]},
            )
            await s.execute(
                text("DELETE FROM courses WHERE id = ANY(:ids)"),
                {"ids": list(ids.values())},
            )
            await s.commit()
        await engine.dispose()


async def _lock_key_is_free(engine) -> bool:
    """Свободен ли ключ одного worker'а — проверка ЧУЖИМ соединением."""
    async with AsyncSession(engine) as s:
        taken = bool(
            (
                await s.execute(
                    text("SELECT pg_try_advisory_lock(:k)"),
                    {"k": _COURSE_DEPENDENCY_STATE_LOCK_KEY},
                )
            ).scalar()
        )
        if taken:
            await s.execute(
                text("SELECT pg_advisory_unlock(:k)"),
                {"k": _COURSE_DEPENDENCY_STATE_LOCK_KEY},
            )
        return taken


def test_cron_worker_guard_is_transaction_scoped():
    """Сторож одного worker'а обязан быть ТРАНЗАКЦИОННЫМ, а не сессионным.

    Это регрессия на дефект первой версии правки tsk-626, найденный только на
    проде. Сессионная блокировка (`pg_try_advisory_lock`) привязана к
    КОНКРЕТНОМУ соединению, а `Session` после коммита возвращает соединение в
    пул и на следующем запросе берёт свободное. Пока пул свободен (dev,
    одиночный тест) это то же самое соединение, и всё выглядит исправным; на
    боевом пуле — почти всегда другое. Итог замера на проде 17.08 19:51:
    блокировка осталась висеть на первом соединении, `pg_advisory_unlock`
    отработал вхолостую на чужом, следующий тик счёл бы, что работает другой
    worker, — фоновый пересчёт замолчал бы до перезапуска сервиса, не выдав ни
    одной ошибки.

    Поведенческий тест эту разницу ловит только при удачном совпадении: чтобы
    соединение сменилось, кто-то должен успеть забрать его между коммитом и
    следующим запросом. Поэтому правило закреплено ТЕКСТОМ — как и сторож
    списка исключений из изоляции (`test_tx_isolation_optout.py`).
    """
    source = (
        project_root / "app" / "services" / "course_dependency_state_cron_service.py"
    ).read_text(encoding="utf-8")

    assert "pg_try_advisory_xact_lock" in source, (
        "сторож одного worker'а должен брать ТРАНЗАКЦИОННУЮ блокировку"
    )
    for forbidden in ("pg_try_advisory_lock(", "pg_advisory_unlock("):
        assert forbidden not in source.replace("pg_try_advisory_xact_lock(", ""), (
            f"{forbidden} — сессионная блокировка. Она привязана к соединению, "
            "а соединение из пула за сессией не закреплено: блокировка утечёт, "
            "и фоновый пересчёт молча выключится"
        )


async def test_cron_tick_leaves_no_worker_lock(dep_scene):
    """После тика ключ worker'а свободен — на пуле, как на бою.

    Движок с `QueuePool` (а не `NullPool`) здесь принципиален: `NullPool`
    закрывает соединение вместе с сессией и снимает любую утечку сам, то есть
    прячет ровно тот дефект, который проверяется. Пул при этом намеренно
    «размешивается» посторонними потребителями — так рабочая сессия после
    коммита получает не то соединение, что раньше.

    Проверка обязана идти ДО `engine.dispose()`: dispose закрывает соединения
    и снимает утечку.
    """
    engine = create_async_engine(_settings.database_url, pool_size=5, max_overflow=5)
    probe = create_async_engine(_settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    stop = asyncio.Event()

    async def churn() -> None:
        """Посторонний потребитель пула — забирает освободившиеся соединения."""
        while not stop.is_set():
            conn = await engine.connect()
            await conn.execute(text("SELECT 1"))
            await conn.close()
            await asyncio.sleep(0)

    churner = asyncio.create_task(churn())
    try:
        summary = await course_dependency_state_cron_tick(session_factory=factory)
    finally:
        stop.set()
        await churner

    assert summary["locked"] is True, summary
    assert await _lock_key_is_free(probe), (
        "после тика ключ одного worker'а остался занят — следующий тик решит, "
        "что работу делает другой worker, и фоновый пересчёт выключится"
    )
    await engine.dispose()
    await probe.dispose()


async def test_cron_tick_commits_per_student(dep_scene):
    """Тик пишет учеников разными транзакциями, а не одной на весь проход.

    `now()` в PostgreSQL — время НАЧАЛА транзакции, поэтому одинаковый
    `updated_at` у разных учеников означает ровно одно: их строки писала одна
    транзакция и держала блокировки до самого конца прохода. Так и было на
    проде 17.08.2026 (460 строк, 40 учеников, один `updated_at`) — и именно
    это столкнулось с параллельным `next-item`.
    """
    ids, students = dep_scene
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        await course_dependency_state_cron_tick(session_factory=factory)

        async with AsyncSession(engine) as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT student_id, updated_at FROM student_course_state "
                        "WHERE student_id = ANY(:ids) AND course_id = :c "
                        "ORDER BY student_id"
                    ),
                    {"ids": students, "c": ids["child_a"]},
                )
            ).fetchall()
    finally:
        await engine.dispose()

    assert len(rows) == 2, f"тик не пересчитал обоих учеников: {rows!r}"
    assert rows[0][1] != rows[1][1], (
        "у обоих учеников одинаковый updated_at — значит проход снова идёт "
        f"одной транзакцией на всех: {rows!r}"
    )
