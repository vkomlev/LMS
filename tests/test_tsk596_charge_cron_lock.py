"""tsk-596: защита суточного прохода начислений от двойного запуска.

Вынесено в отдельный модуль намеренно, как у tsk-521: здесь нужны два
НЕЗАВИСИМЫХ соединения к базе, то есть собственный engine. Общая тестовая
фикстура держит одно соединение внутри savepoint — два параллельных прохода
подрались бы на уровне SQLAlchemy, не дойдя до advisory-lock, и проверяли бы
обвязку теста, а не механизм. Модуль объявлен в
`SELF_MANAGED_CONNECTION_MODULES` (tests/conftest.py) и убирает за собой сам.

Зачем механизм: на проде приложение крутится несколькими worker'ами, проход
заведён в каждом. Без блокировки один и тот же месяц пересчитывали бы все разом,
а методист получил бы столько же одинаковых уведомлений.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.services import charge_cron_service

_TAG = "tsk596lock"


async def _seed(factory) -> dict:
    """Методист-получатель и ученик-аномалия (ходит, начисления нет).

    Без них проверка бессодержательна: уведомлять некого, и «дубля не было»
    выполняется само собой. Модуль идёт без транзакционной изоляции, поэтому
    данные заводятся и убираются руками.
    """
    async with factory() as db:
        methodist_id = (
            await db.execute(
                text(
                    "INSERT INTO users (email, full_name) "
                    "VALUES (:e, :n) RETURNING id"
                ),
                {"e": f"{_TAG}-m@example.com", "n": f"{_TAG}-методист"},
            )
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = 'methodist'"
            ),
            {"u": methodist_id},
        )
        teacher_id = (
            await db.execute(
                text("INSERT INTO users (email, full_name) VALUES (:e, :n) RETURNING id"),
                {"e": f"{_TAG}-t@example.com", "n": f"{_TAG}-препод"},
            )
        ).scalar_one()
        student_id = (
            await db.execute(
                text("INSERT INTO users (email, full_name) VALUES (:e, :n) RETURNING id"),
                {"e": f"{_TAG}-s@example.com", "n": f"{_TAG}-ученик"},
            )
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = 'student'"
            ),
            {"u": student_id},
        )
        slot_id = (
            await db.execute(
                text(
                    "INSERT INTO lesson_slot "
                    "(teacher_id, weekday, start_time, duration_minutes, timezone, is_active) "
                    "VALUES (:t, 3, '10:00', 60, 'Europe/Moscow', true) RETURNING id"
                ),
                {"t": teacher_id},
            )
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO lesson_slot_student (slot_id, student_id, is_active) "
                "VALUES (:s, :u, true)"
            ),
            {"s": slot_id, "u": student_id},
        )
        await db.commit()
    return {
        "methodist_id": int(methodist_id),
        "teacher_id": int(teacher_id),
        "student_id": int(student_id),
        "slot_id": int(slot_id),
    }


async def _cleanup(factory, env: dict | None) -> None:
    async with factory() as db:
        await db.execute(
            text("DELETE FROM notifications WHERE kind = :k"),
            {"k": charge_cron_service.NOTIFICATION_KIND},
        )
        await db.execute(
            text("DELETE FROM student_monthly_charge WHERE period = :p"),
            {"p": date(2026, 9, 1)},
        )
        if env is not None:
            ids = [env["methodist_id"], env["teacher_id"], env["student_id"]]
            await db.execute(
                text("DELETE FROM lesson_slot_student WHERE slot_id = :s"),
                {"s": env["slot_id"]},
            )
            await db.execute(
                text("DELETE FROM lesson_slot WHERE id = :s"), {"s": env["slot_id"]}
            )
            await db.execute(
                text("DELETE FROM user_roles WHERE user_id = ANY(:ids)"), {"ids": ids}
            )
            await db.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": ids})
        await db.commit()


@pytest.mark.asyncio
async def test_notification_phase_is_not_duplicated_after_commit(monkeypatch):
    """Второй worker, зашедший ПОСЛЕ коммита пересчёта, уведомление не задваивает.

    Это и есть настоящая дыра, ради которой лок берётся дважды: проверка
    одновременного старта (ниже) её не видит, потому что там второй проход
    упирается в лок ещё до первого коммита. `recalculate_month` коммитит внутри
    себя, коммит освобождает транзакционный лок — и с этого момента второй
    worker свободно доходит до фазы уведомления.

    Здесь второй проход стартует ровно в этот момент — из подменённого
    пересчёта первого. Методист обязан получить одно письмо, не два.
    """
    engine = create_async_engine(Settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    today = date(2026, 9, 15)
    rival: dict[str, dict] = {}
    real_notify = charge_cron_service._notify_methodists
    env = await _seed(factory)

    async def _notify_letting_rival_in(db, **kwargs):
        """Впустить второй проход ровно в критическую секцию первого.

        Точка выбрана намеренно: первый уже прошёл проверку отсрочки, но ещё
        ничего не записал. Без лока второй читает ту же пустоту, пишет и
        коммитит первым — и методист получает два одинаковых письма.
        """
        if "summary" not in rival:
            # Соперник идёт полным проходом, но уже без подмены: иначе они
            # запускали бы друг друга без конца.
            monkeypatch.setattr(charge_cron_service, "_notify_methodists", real_notify)
            rival["summary"] = await charge_cron_service.charge_cron_tick(
                factory, today=today
            )
        return await real_notify(db, **kwargs)

    monkeypatch.setattr(
        charge_cron_service, "_notify_methodists", _notify_letting_rival_in
    )
    try:
        first = await charge_cron_service.charge_cron_tick(factory, today=today)
        second = rival.get("summary", {"notified": 0})

        assert first["notified"] == 0 or second["notified"] == 0, (
            f"уведомление ушло дважды: {first['notified']} + {second['notified']}"
        )

        async with factory() as db:
            per_methodist = (
                await db.execute(
                    text(
                        "SELECT user_id, count(*) AS n FROM notifications "
                        " WHERE kind = :k GROUP BY user_id"
                    ),
                    {"k": charge_cron_service.NOTIFICATION_KIND},
                )
            ).all()
        assert per_methodist, "уведомлять оказалось некого — проверка бессодержательна"
        assert all(int(r.n) == 1 for r in per_methodist), (
            f"методист получил одно и то же уведомление дважды: {per_methodist}"
        )
    finally:
        await _cleanup(factory, env)
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_workers_do_not_duplicate_notification():
    """Два прохода, стартовавшие разом, методисту дважды не пишут.

    Требовать здесь «ровно один взял лок» было бы неверно и дало бы плавающий
    тест: `recalculate_month` коммитит внутри, коммит освобождает лок, и второй
    проход, стартовавший чуть позже, законно берёт лок уже после этого. Именно
    поэтому важна не единственность входа, а единственность уведомления —
    остальная работа идемпотентна.
    """
    engine = create_async_engine(Settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    today = date(2026, 9, 15)
    env = await _seed(factory)
    try:
        first, second = await asyncio.gather(
            charge_cron_service.charge_cron_tick(factory, today=today),
            charge_cron_service.charge_cron_tick(factory, today=today),
        )
        assert any(s["locked"] for s in (first, second)), (first, second)
        assert first["notified"] == 0 or second["notified"] == 0, (first, second)

        async with factory() as db:
            per_methodist = (
                await db.execute(
                    text(
                        "SELECT user_id, count(*) AS n FROM notifications "
                        " WHERE kind = :k GROUP BY user_id"
                    ),
                    {"k": charge_cron_service.NOTIFICATION_KIND},
                )
            ).all()
        assert per_methodist, "уведомлять оказалось некого — проверка бессодержательна"
        assert all(int(r.n) == 1 for r in per_methodist), per_methodist
    finally:
        # Модуль без транзакционной изоляции — убираем за собой руками.
        await _cleanup(factory, env)
        await engine.dispose()
