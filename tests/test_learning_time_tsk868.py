"""tsk-868: сеансы работы ученика над элементом программы.

Время прохождения нигде не сохранялось: `student_presence` — снимок на ученика,
`product_event` заведена в апреле и пуста. Здесь проверяется сшивка сеансов из
пульса присутствия и учёт просмотра видео.

Свойства, за которыми следим (каждое при поломке молчит, а не падает):

1. пульс ПРОДЛЕВАЕТ сеанс, а не плодит строки — иначе получим ту самую историю
   пульсов, от которой отказались в tsk-591;
2. долгий перерыв начинает новый сеанс, а не растягивает старый на всю ночь;
3. «открыт кабинет» сеансом не становится: блуждание по оглавлению — не работа
   над элементом;
4. у видео секунды приходят от плеера и НЕ равны времени по часам (паузы,
   перемотка), поэтому берётся присланный итог, а не приращение.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import learning_time_service as lts
from app.services.auth import identity_link_service

pytestmark = pytest.mark.asyncio


async def _student(db, prefix: str) -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=prefix, tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id


async def _course(db) -> int:
    cid = int((await db.execute(text(
        "INSERT INTO courses (title, access_level, is_required, course_uid) "
        "VALUES ('tsk868','self_guided',false,:u) RETURNING id"
    ), {"u": f"tsk868-{random.randint(10**8, 10**10)}"})).scalar_one())
    await db.commit()
    return cid


async def _task(db, course_id: int) -> int:
    did = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()
    tid = int((await db.execute(text(
        "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid) "
        "VALUES (jsonb_build_object('type','SA','stem','x'), :c, :d, :u) RETURNING id"
    ), {"c": course_id, "d": did, "u": f"tsk868-{random.randint(10**8, 10**10)}"})).scalar_one())
    await db.commit()
    return tid


async def _sessions(db, student_id: int) -> list[dict]:
    rows = (await db.execute(text(
        "SELECT id, item_type, task_id, material_id, seconds, beats, interactions, "
        "       source, payload, started_at, ended_at "
        "FROM learning_time_session WHERE student_id = :s ORDER BY id"
    ), {"s": student_id})).mappings().all()
    return [dict(r) for r in rows]


async def _age_session(db, session_id: int, *, seconds: int) -> None:
    """Отодвинуть сеанс в прошлое — иначе «прошло две минуты» не проверить."""
    await db.execute(text(
        "UPDATE learning_time_session "
        "SET started_at = started_at - make_interval(secs => :s), "
        "    ended_at = ended_at - make_interval(secs => :s) "
        "WHERE id = :id"
    ), {"s": seconds, "id": session_id})
    await db.commit()


async def _cleanup_data(db, student_ids: list[int], course_ids: list[int]) -> None:
    """Убрать данные, но НЕ самого ученика.

    У ученика, который входил в кабинет, есть запись в `audit_event`, а та
    append-only по триггеру: каскадное удаление упирается в него с ошибкой
    «audit_event is append-only». Тестам достаточно убрать следы работы.
    """
    await db.execute(text("DELETE FROM learning_time_session WHERE student_id = ANY(:i)"),
                     {"i": student_ids})
    await db.execute(text("DELETE FROM student_presence WHERE student_id = ANY(:i)"),
                     {"i": student_ids})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:i)"), {"i": course_ids})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:i)"), {"i": course_ids})
    await db.commit()


async def _cleanup(db, student_ids: list[int], course_ids: list[int]) -> None:
    await db.execute(text("DELETE FROM learning_time_session WHERE student_id = ANY(:i)"),
                     {"i": student_ids})
    await db.execute(text("DELETE FROM student_presence WHERE student_id = ANY(:i)"),
                     {"i": student_ids})
    await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:i)"), {"i": student_ids})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:i)"), {"i": student_ids})
    await db.execute(text("DELETE FROM users WHERE id = ANY(:i)"), {"i": student_ids})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:i)"), {"i": course_ids})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:i)"), {"i": course_ids})
    await db.commit()


async def test_beat_extends_session_instead_of_adding_rows(db):
    """Второй пульс продлевает первый сеанс, а не создаёт второй.

    Ради этого таблица и заведена: писать каждый пульс — вернуться к истории,
    от которой отказались в tsk-591.
    """
    course = await _course(db)
    task = await _task(db, course)
    student = await _student(db, "lt-a")
    try:
        first = await lts.record_beat(
            db, student_id=student, item_type="task", course_id=course, task_id=task,
            interacted=True,
        )
        await db.commit()
        await _age_session(db, first, seconds=120)

        second = await lts.record_beat(
            db, student_id=student, item_type="task", course_id=course, task_id=task,
            interacted=False,
        )
        await db.commit()

        assert second == first, "пульс завёл новый сеанс вместо продления"
        (row,) = await _sessions(db, student)
        assert row["beats"] == 2
        assert 110 <= row["seconds"] <= 130, row["seconds"]
        assert row["interactions"] == 1, "пульс без взаимодействия не должен считаться"
    finally:
        await _cleanup(db, [student], [course])


async def test_long_break_starts_new_session(db):
    """Перерыв больше порога — это новый заход, а не длинный сеанс."""
    course = await _course(db)
    task = await _task(db, course)
    student = await _student(db, "lt-b")
    try:
        first = await lts.record_beat(
            db, student_id=student, item_type="task", course_id=course, task_id=task,
        )
        await db.commit()
        await _age_session(db, first, seconds=lts.SESSION_GAP_SECONDS + 60)

        second = await lts.record_beat(
            db, student_id=student, item_type="task", course_id=course, task_id=task,
        )
        await db.commit()

        assert second != first
        rows = await _sessions(db, student)
        assert len(rows) == 2
        # Старый сеанс не растянулся на весь перерыв.
        assert rows[0]["seconds"] == 0
    finally:
        await _cleanup(db, [student], [course])


async def test_beat_never_counts_more_than_the_cap(db):
    """«Разбудили ноутбук» не превращается в час работы.

    Внутри окна сшивки промежуток всё равно ограничен: иначе сеанс растянулся
    бы на весь перерыв, а человека за экраном в это время не было.
    """
    course = await _course(db)
    task = await _task(db, course)
    student = await _student(db, "lt-c")
    try:
        first = await lts.record_beat(
            db, student_id=student, item_type="task", course_id=course, task_id=task,
        )
        await db.commit()
        # Ровно на границе окна: сеанс ещё продлевается, но зачесть можно не
        # больше предела.
        await _age_session(db, first, seconds=lts.SESSION_GAP_SECONDS - 5)
        await lts.record_beat(
            db, student_id=student, item_type="task", course_id=course, task_id=task,
        )
        await db.commit()

        (row,) = await _sessions(db, student)
        assert row["seconds"] <= lts.MAX_BEAT_SECONDS
    finally:
        await _cleanup(db, [student], [course])


async def test_switching_item_opens_another_session(db):
    """Перешёл с задания на материал — это два разных сеанса."""
    course = await _course(db)
    task = await _task(db, course)
    student = await _student(db, "lt-d")
    try:
        await lts.record_beat(
            db, student_id=student, item_type="task", course_id=course, task_id=task,
        )
        await lts.record_beat(
            db, student_id=student, item_type="material", course_id=course, material_id=None,
        )
        await db.commit()

        rows = await _sessions(db, student)
        assert {r["item_type"] for r in rows} == {"task", "material"}
    finally:
        await _cleanup(db, [student], [course])


async def test_browsing_the_cabinet_is_not_a_session(db):
    """«Открыт кабинет» временем работы не считается.

    Складывать блуждание по оглавлению со временем на задании значит выдать
    одно за другое — и завысить бюджет ДЗ у того, кто просто листал курс.
    """
    course = await _course(db)
    student = await _student(db, "lt-e")
    try:
        for context in ("course", "other", "чушь-которой-нет"):
            assert await lts.record_beat(
                db, student_id=student, item_type=context, course_id=course,
            ) == 0
        await db.commit()
        assert await _sessions(db, student) == []
    finally:
        await _cleanup(db, [student], [course])


async def test_video_progress_takes_player_total_not_wall_clock(db):
    """У видео секунды приходят от плеера и не равны времени по часам.

    Плеер шлёт накопленный итог: связь с кросс-доменным iframe может оборваться
    на любом сообщении, и приращения «добавь десять секунд» после потери пары
    отчётов дали бы недосчёт, который уже не восстановить.
    """
    course = await _course(db)
    student = await _student(db, "lt-f")
    try:
        first = await lts.record_video_progress(
            db, student_id=student, video_id="-53400615_456240160",
            watched_seconds=30, duration_seconds=733, course_id=course,
        )
        await db.commit()
        second = await lts.record_video_progress(
            db, student_id=student, video_id="-53400615_456240160",
            watched_seconds=95, duration_seconds=733, completed=False, course_id=course,
        )
        await db.commit()

        assert second == first, "второй отчёт о том же видео завёл новый сеанс"
        (row,) = await _sessions(db, student)
        assert row["item_type"] == "video" and row["source"] == "player"
        assert row["seconds"] == 95, "итог плеера должен вытеснять прежний, а не суммироваться"
        assert row["payload"]["duration_seconds"] == 733
        assert row["payload"]["completed"] is False
    finally:
        await _cleanup(db, [student], [course])


async def test_video_of_another_lesson_is_another_session(db):
    """Два разных видео — два сеанса, даже если смотрели подряд."""
    course = await _course(db)
    student = await _student(db, "lt-g")
    try:
        await lts.record_video_progress(
            db, student_id=student, video_id="vid-1", watched_seconds=10, course_id=course,
        )
        await lts.record_video_progress(
            db, student_id=student, video_id="vid-2", watched_seconds=10, course_id=course,
        )
        await db.commit()

        rows = await _sessions(db, student)
        assert len(rows) == 2
        assert {r["payload"]["video_id"] for r in rows} == {"vid-1", "vid-2"}
    finally:
        await _cleanup(db, [student], [course])


async def test_presence_pulse_writes_a_session(db):
    """Пульс присутствия и сеанс пишутся одной транзакцией.

    Проверяется именно связка: снимок без истории — это состояние до tsk-868,
    и молчаливый возврат к нему заметить было бы нечем.
    """
    from app.services import student_presence_service

    course = await _course(db)
    task = await _task(db, course)
    student = await _student(db, "lt-h")
    try:
        await student_presence_service.touch(
            db, student, interacted=True, context="task",
            course_id=course, task_id=task,
        )
        await db.commit()

        rows = await _sessions(db, student)
        assert len(rows) == 1
        assert rows[0]["item_type"] == "task" and rows[0]["task_id"] == task
        assert rows[0]["source"] == "presence"
    finally:
        await _cleanup(db, [student], [course])

async def test_video_progress_endpoint_writes_a_session(db, client):
    """Кабинет шлёт отчёт плеера — в базе появляется сеанс просмотра.

    Проверяется весь путь целиком: схема запроса, гейт входа и запись. До
    tsk-868 просмотр не оставлял следа вовсе — «плеер на экране» знал только
    пульс присутствия (tsk-835), а сколько человек посмотрел, не знал никто.
    """
    from app.services.auth.session_service import create_session

    course = await _course(db)
    student = await _student(db, "lt-i")
    token, _, _ = await create_session(db, user_id=student)
    await db.commit()
    try:
        resp = await client.post(
            "/api/v1/me/video-progress",
            json={
                "video_id": "-53400615_456240160",
                "watched_seconds": 42,
                "duration_seconds": 733,
                "completed": False,
                "course_id": course,
            },
            cookies={"session": token},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["next_report_seconds"] > 0

        (row,) = await _sessions(db, student)
        assert row["item_type"] == "video" and row["seconds"] == 42
        assert row["payload"]["video_id"] == "-53400615_456240160"
    finally:
        await _cleanup_data(db, [student], [course])


async def test_video_progress_endpoint_needs_login(db, client):
    """Без входа отчёт не принимается: сеанс всегда про того, кто пришёл."""
    resp = await client.post(
        "/api/v1/me/video-progress",
        json={"video_id": "x", "watched_seconds": 1},
    )
    assert resp.status_code in (401, 403), resp.text

