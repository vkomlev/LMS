"""tsk-572 фаза 7: сигналы «нужно повторение» и роль преподавателя.

Проверяется главное продуктовое решение: у датчика ДВА адресата, и они не
взаимозаменяемы. Тема — методисту (работа с контентом), ученик — преподавателю
(он ведёт занятия и видит человека живьём). Если потоки смешать, методист начнёт
получать личные затыки, которых не может решить, а преподаватель — заявки на
переписывание курса, к которым не имеет отношения. Оба перестанут читать.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import learning_gap_signals_service as sig
from app.services.auth import identity_link_service


async def _user(db, prefix: str) -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=prefix, tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id


async def _course(db, title: str) -> int:
    res = await db.execute(text(
        "INSERT INTO courses (title, access_level, is_required, course_uid) "
        "VALUES (:t,'self_guided',false,:u) RETURNING id"
    ), {"t": title, "u": f"sig-{random.randint(10**8, 10**10)}"})
    cid = int(res.scalar_one())
    await db.commit()
    return cid


async def _cleanup(db, users: list[int], courses: list[int]) -> None:
    await db.execute(text("DELETE FROM learning_gap_signal WHERE course_id = ANY(:c)"),
                     {"c": courses})
    await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": users})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": users})
    await db.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": users})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:c)"), {"c": courses})
    await db.commit()


@pytest.mark.asyncio
async def test_topic_and_student_signals_go_to_different_people(db):
    """Тема и ученик не смешиваются в одном списке.

    Методисту незачем разбирать личный затык — он не ведёт занятий. Преподавателю
    незачем получать заявку на переписывание курса.
    """
    course = await _course(db, "Тема сигналов")
    student = await _user(db, "sig-student")
    try:
        await sig.upsert_signal(db, course_id=course, student_id=None,
                                submissions=40, students=5, wrong_rate=0.6)
        await sig.upsert_signal(db, course_id=course, student_id=student,
                                submissions=10, students=1, wrong_rate=0.7)
        await db.commit()

        to_teacher = await sig.list_signals(db, for_student=True)
        to_methodist = await sig.list_signals(db, for_student=False)

        mine_t = [s for s in to_teacher if s["course_id"] == course]
        mine_m = [s for s in to_methodist if s["course_id"] == course]
        assert len(mine_t) == 1 and mine_t[0]["student_id"] == student
        assert len(mine_m) == 1 and mine_m[0]["student_id"] is None
        # Имя ученика подставлено: преподавателю нужен человек, а не номер.
        assert mine_t[0]["student_name"]
    finally:
        await _cleanup(db, [student], [course])


@pytest.mark.asyncio
async def test_repeat_scan_does_not_pile_up_duplicates(db):
    """Cron ходит по расписанию — без защиты за неделю накопится семь копий.

    Это не про чистоту базы, а про то, что список из семи одинаковых строк
    перестают читать.
    """
    course = await _course(db, "Повторный проход")
    try:
        first = await sig.upsert_signal(db, course_id=course, student_id=None,
                                        submissions=40, students=5, wrong_rate=0.6)
        await db.commit()
        second = await sig.upsert_signal(db, course_id=course, student_id=None,
                                         submissions=41, students=5, wrong_rate=0.61)
        await db.commit()
        assert first is not None
        assert second is None, "второй проход завёл дубль открытого сигнала"
    finally:
        await _cleanup(db, [], [course])


@pytest.mark.asyncio
async def test_teacher_comment_travels_with_escalation(db):
    """Комментарий преподавателя — то, ради чего он в цепочке.

    Он видел ученика вживую и знает контекст, которого в долях ошибок нет.
    Эскалация без этого текста — просто цифра, которую методист уже видел.
    """
    course = await _course(db, "Эскалация с комментарием")
    student = await _user(db, "sig-esc-student")
    teacher = await _user(db, "sig-esc-teacher")
    try:
        sid = await sig.upsert_signal(db, course_id=course, student_id=student,
                                      submissions=10, students=1, wrong_rate=0.7)
        await db.commit()
        ok = await sig.acknowledge_signal(
            db, signal_id=sid, teacher_id=teacher,
            comment="Болел две недели, пропустил тему целиком. Путает ввод и вывод, не циклы.",
            escalate=True,
        )
        assert ok

        row = (await db.execute(text(
            "SELECT status, teacher_id, teacher_comment, escalated_at, acknowledged_at "
            "FROM learning_gap_signal WHERE id = :i"
        ), {"i": sid})).mappings().first()
        assert row["status"] == "escalated"
        assert row["teacher_id"] == teacher
        assert "Болел две недели" in row["teacher_comment"]
        assert row["escalated_at"] is not None
        assert row["acknowledged_at"] is not None
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_acknowledge_without_escalation_is_a_valid_outcome(db):
    """«Принял, разберусь сам на занятии» — нормальный исход, а не бездействие.

    У преподавателя есть живой канал, которого у методиста нет. Если бы каждый
    сигнал обязан был уходить методисту, тот получал бы заявки на то, что уже
    решается на ближайшем занятии.
    """
    course = await _course(db, "Разберусь сам")
    student = await _user(db, "sig-ack-student")
    teacher = await _user(db, "sig-ack-teacher")
    try:
        sid = await sig.upsert_signal(db, course_id=course, student_id=student,
                                      submissions=9, students=1, wrong_rate=0.55)
        await db.commit()
        assert await sig.acknowledge_signal(
            db, signal_id=sid, teacher_id=teacher, comment="Разберём на занятии в четверг"
        )
        row = (await db.execute(text(
            "SELECT status, escalated_at FROM learning_gap_signal WHERE id = :i"
        ), {"i": sid})).mappings().first()
        assert row["status"] == "acknowledged"
        assert row["escalated_at"] is None, "сигнал уехал методисту без решения преподавателя"
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_dismissed_signal_keeps_the_reason(db):
    """Отклонение сохраняет причину: по ней видно, что датчик шумит.

    Без текста «отклонено» — это потерянный сигнал о ложных срабатываниях, и
    пороги никто никогда не пересмотрит.
    """
    course = await _course(db, "Ложное срабатывание")
    student = await _user(db, "sig-dis-student")
    teacher = await _user(db, "sig-dis-teacher")
    try:
        sid = await sig.upsert_signal(db, course_id=course, student_id=student,
                                      submissions=9, students=1, wrong_rate=0.6)
        await db.commit()
        assert await sig.dismiss_signal(
            db, signal_id=sid, teacher_id=teacher,
            comment="Сломан эталон в задании, ученик отвечал верно",
        )
        row = (await db.execute(text(
            "SELECT status, teacher_comment FROM learning_gap_signal WHERE id = :i"
        ), {"i": sid})).mappings().first()
        assert row["status"] == "dismissed"
        assert "эталон" in row["teacher_comment"]

        # Закрытый сигнал освобождает место: тема может всплыть снова позже.
        again = await sig.upsert_signal(db, course_id=course, student_id=student,
                                        submissions=12, students=1, wrong_rate=0.6)
        await db.commit()
        assert again is not None
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_scan_reports_counters_even_when_empty(db):
    """Проход возвращает счётчики всегда.

    Молчащий cron неотличим от отсутствующего — именно так молчаливый отказ
    живёт годами. Итог должен быть виден и когда сигналов ноль.
    """
    res = await sig.scan_and_create_signals(db, days=1)
    for key in ("topics_found", "topic_signals_created",
                "students_found", "student_signals_created"):
        assert key in res and isinstance(res[key], int)


@pytest.mark.asyncio
async def test_escalated_student_signal_reaches_the_methodist(db):
    """Переданный сигнал обязан появиться у методиста.

    Дефект, найденный живой проверкой: список методиста фильтровал только темы,
    и ученический сигнал, который преподаватель нажатием «передать методисту»
    отправил ему, не появлялся нигде. Преподаватель считал, что передал;
    методист не видел ничего. Молчаливая потеря целого действия.
    """
    course = await _course(db, "Эскалация доезжает")
    student = await _user(db, "esc-reach-student")
    teacher = await _user(db, "esc-reach-teacher")
    try:
        sid = await sig.upsert_signal(db, course_id=course, student_id=student,
                                      submissions=10, students=1, wrong_rate=0.8)
        await db.commit()
        await sig.acknowledge_signal(
            db, signal_id=sid, teacher_id=teacher,
            comment="Разбирали дважды, не идёт", escalate=True,
        )

        desk = await sig.list_signals(
            db, for_student=False, statuses=("new", "acknowledged", "escalated"),
        )
        mine = [r for r in desk if r["id"] == sid]
        assert mine, "переданный сигнал не доехал до методиста"
        assert "не идёт" in (mine[0]["teacher_comment"] or ""), (
            "комментарий преподавателя потерялся по дороге"
        )
    finally:
        await _cleanup(db, [student, teacher], [course])
