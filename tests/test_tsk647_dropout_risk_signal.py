"""tsk-647: признак «затих» — ученик, который вот-вот перестанет ходить.

Что закрываем — по одному тесту на каждое решение, принятое НА ДАННЫХ
(разбор: docs/qa/2026-08-28-tsk647-dropout-signal.md):

- датчик находит ученика, мимо которого идут занятия и который сам не работает;
- ручная простановка преподавателя не считается работой ученика — иначе датчик
  мерит активность ПРЕПОДАВАТЕЛЯ (73 % строк `task_results` на бою именно такие);
- пропуски сами по себе сигналом не становятся: ученик, который ходит, но
  молчит в кабинете, не помечается, и наоборот;
- оформленный перерыв снимает сигнал, причём по пересечению с окном, а не по
  сегодняшнему дню;
- ученик без истории своей работы вне охвата — сознательная граница, а не
  недосмотр;
- разобранный сигнал не поднимается заново на следующем же проходе.
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.users import Users
from app.services import learning_gap_signals_service as sig
from app.services.auth import identity_link_service


async def _user(db, prefix: str, *, role: str | None = "student") -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=prefix, tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    if role is not None:
        await db.execute(text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, id FROM roles WHERE name = :r ON CONFLICT DO NOTHING"
        ), {"u": u.id, "r": role})
    await db.commit()
    return u.id


async def _course(db, title: str) -> int:
    cid = int((await db.execute(text(
        "INSERT INTO courses (title, access_level, is_required, course_uid) "
        "VALUES (:t,'auto_check',false,:u) RETURNING id"
    ), {"t": title, "u": f"tsk647-{random.randint(10**8, 10**10)}"})).scalar_one())
    await db.commit()
    return cid


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(text(
        "INSERT INTO user_courses (user_id, course_id) VALUES (:u, :c)"
    ), {"u": user_id, "c": course_id})
    await db.commit()


async def _lesson(
    db, *, student_id: int, teacher_id: int, days_ago: int, status: str,
) -> int:
    """Прошедшее занятие с участием ученика."""
    occ = LessonOccurrence(
        slot_id=None, teacher_id=teacher_id,
        scheduled_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        duration_minutes=60,
    )
    db.add(occ)
    await db.flush()
    db.add(LessonOccurrenceParticipant(
        occurrence_id=occ.id, student_id=student_id, status=status,
    ))
    await db.flush()
    occ_id = occ.id
    await db.commit()
    return occ_id


async def _submission(
    db, *, user_id: int, course_id: int, days_ago: int,
    source: str = "spw_web",
) -> None:
    task_id = int((await db.execute(text(
        "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
        " course_id, difficulty_id, is_active) "
        "VALUES (:e, 1, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, 1, true) RETURNING id"
    ), {
        "e": f"tsk647-t-{random.randint(10**8, 10**10)}",
        "c": json.dumps({"type": "SA", "stem": "2+2"}),
        "r": json.dumps({"answers": ["4"]}),
        "cid": course_id,
    })).scalar_one())
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    await db.execute(text(
        "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, "
        " received_at, max_score, source_system, is_correct, answer_json) "
        "VALUES (1, :u, :t, :w, 0, :w, 1, :src, true, CAST(:a AS jsonb))"
    ), {"u": user_id, "t": task_id, "w": when, "src": source,
        "a": json.dumps({"type": "SA", "response": {"value": "4"}})})
    await db.commit()


async def _cleanup(db, users: list[int], courses: list[int]) -> None:
    await db.execute(text(
        "DELETE FROM task_results WHERE task_id IN "
        "(SELECT id FROM tasks WHERE course_id = ANY(:c))"
    ), {"c": courses})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:c)"), {"c": courses})
    await db.execute(text("DELETE FROM user_courses WHERE course_id = ANY(:c)"), {"c": courses})
    await db.execute(text(
        "DELETE FROM learning_gap_signal WHERE course_id = ANY(:c) OR student_id = ANY(:u)"
    ), {"c": courses, "u": users})
    await db.execute(text("DELETE FROM student_break WHERE student_id = ANY(:u)"), {"u": users})
    await db.execute(text(
        "DELETE FROM lesson_occurrence_participant WHERE student_id = ANY(:u)"
    ), {"u": users})
    await db.execute(text("DELETE FROM lesson_occurrence WHERE teacher_id = ANY(:u)"), {"u": users})
    await db.execute(text("DELETE FROM notifications WHERE user_id = ANY(:u)"), {"u": users})
    await db.commit()
    if users:
        try:
            await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": users})
            await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": users})
            await db.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": users})
            await db.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": users})
            await db.commit()
        except Exception:
            await db.rollback()
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:c)"), {"c": courses})
    await db.commit()


async def _setup_silent_student(db, prefix: str) -> tuple[int, int, int]:
    """Ученик, который затих: два занятия мимо, своя работа — три недели назад.

    Возвращает (student_id, teacher_id, course_id).
    """
    course = await _course(db, f"{prefix} курс")
    teacher = await _user(db, f"{prefix}-teacher", role="teacher")
    student = await _user(db, f"{prefix}-student")
    await _enroll(db, student, course)
    await _submission(db, user_id=student, course_id=course, days_ago=21)
    await _lesson(db, student_id=student, teacher_id=teacher, days_ago=10, status="no_show")
    await _lesson(db, student_id=student, teacher_id=teacher, days_ago=3, status="no_show")
    return student, teacher, course


def _mine(rows: list[dict], student_id: int) -> list[dict]:
    return [r for r in rows if r["student_id"] == student_id]


# ───────────────────────────── Датчик ────────────────────────────────────────


@pytest.mark.asyncio
async def test_finds_student_who_went_quiet(db):
    """Главный случай: занятия шли, ученика на них не было, сам он не работал."""
    student, teacher, course = await _setup_silent_student(db, "tsk647-main")
    try:
        found = await sig.find_dropout_risk(db)
        mine = _mine(found, student)
        assert len(mine) == 1, "затихший ученик не найден"
        assert mine[0]["course_id"] == course
        assert mine[0]["lessons_in_window"] == 2
        assert mine[0]["silence_days"] >= 14
        # Он ни разу не был — это не то же самое, что «давно не был», и
        # преподавателю это разные разговоры.
        assert mine[0]["last_attended"] is None
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_manual_teacher_marks_are_not_student_work(db):
    """Ручная простановка преподавателя не спасает ученика от сигнала.

    На боевой базе 73 % строк `task_results` — `manual_teacher`. Первый вариант
    запроса считал их работой ученика и «видел» активность там, где человек не
    заходил месяц.
    """
    student, teacher, course = await _setup_silent_student(db, "tsk647-manual")
    try:
        await _submission(
            db, user_id=student, course_id=course, days_ago=1,
            source="manual_teacher",
        )
        found = await sig.find_dropout_risk(db)
        assert _mine(found, student), "ручная отметка не должна считаться работой ученика"

        # А своя сдача — считается и снимает сигнал.
        await _submission(db, user_id=student, course_id=course, days_ago=1)
        found = await sig.find_dropout_risk(db)
        assert not _mine(found, student), "после своей сдачи ученик не затихший"
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_attendance_alone_and_silence_alone_are_not_enough(db):
    """Ни пропуски сами по себе, ни тишина в кабинете сама по себе — не сигнал.

    Проверено на бою и потому проверяется здесь: `no_show` — четверть всех
    участий, а паузы в кабинете по 7–19 дней есть у самых прилежных, включая
    ученика с 1486 сдачами.
    """
    course = await _course(db, "tsk647-half курс")
    teacher = await _user(db, "tsk647-half-teacher", role="teacher")
    # Ходит на занятия, но в кабинете молчит месяц.
    goes = await _user(db, "tsk647-goes")
    # В кабинете работает каждый день, но занятия пропускает.
    works = await _user(db, "tsk647-works")
    try:
        for student in (goes, works):
            await _enroll(db, student, course)
        await _submission(db, user_id=goes, course_id=course, days_ago=30)
        await _lesson(db, student_id=goes, teacher_id=teacher, days_ago=9, status="confirmed")
        await _lesson(db, student_id=goes, teacher_id=teacher, days_ago=2, status="no_show")

        await _submission(db, user_id=works, course_id=course, days_ago=30)
        await _submission(db, user_id=works, course_id=course, days_ago=1)
        await _lesson(db, student_id=works, teacher_id=teacher, days_ago=9, status="no_show")
        await _lesson(db, student_id=works, teacher_id=teacher, days_ago=2, status="no_show")

        found = await sig.find_dropout_risk(db)
        assert not _mine(found, goes), "ученик, который ходит, затихшим не считается"
        assert not _mine(found, works), "ученик, который работает сам, затихшим не считается"
    finally:
        await _cleanup(db, [goes, works, teacher], [course])


@pytest.mark.asyncio
async def test_no_lessons_in_window_is_not_a_signal(db):
    """Занятий не было вовсе — «не был» ничего не значит.

    Иначе каникулы всей группы превращаются в список тревог на всю школу.
    """
    course = await _course(db, "tsk647-nolessons курс")
    teacher = await _user(db, "tsk647-nolessons-teacher", role="teacher")
    student = await _user(db, "tsk647-nolessons")
    try:
        await _enroll(db, student, course)
        await _submission(db, user_id=student, course_id=course, days_ago=30)
        await _lesson(db, student_id=student, teacher_id=teacher, days_ago=40, status="no_show")

        found = await sig.find_dropout_risk(db)
        assert not _mine(found, student)
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_break_overlapping_window_removes_signal(db):
    """Оформленный перерыв снимает сигнал — даже если сегодня он уже закончился.

    Перерыв заводят задним числом и на месяц вперёд. Сверка «идёт ли он прямо
    сейчас» пропустила бы ученика, у которого окно целиком внутри отъезда.
    """
    student, teacher, course = await _setup_silent_student(db, "tsk647-break")
    try:
        today = date.today()
        await db.execute(text(
            "INSERT INTO student_break (student_id, starts_on, ends_on, note) "
            "VALUES (:u, :s, :e, 'Отъезд')"
        ), {"u": student, "s": today - timedelta(days=20), "e": today - timedelta(days=2)})
        await db.commit()

        found = await sig.find_dropout_risk(db)
        assert not _mine(found, student), "у ученика оформлен перерыв — это не уход"
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_student_without_own_work_history_is_out_of_scope(db):
    """Ученик, который никогда не работал сам, датчиком не покрывается.

    Сознательная граница: без неё правило вырождается в «не был 14 дней» и на
    боевых данных давало 11 тревог вместо 3 при том же числе находок.
    """
    course = await _course(db, "tsk647-noown курс")
    teacher = await _user(db, "tsk647-noown-teacher", role="teacher")
    student = await _user(db, "tsk647-noown")
    try:
        await _enroll(db, student, course)
        await _submission(
            db, user_id=student, course_id=course, days_ago=20,
            source="manual_teacher",
        )
        await _lesson(db, student_id=student, teacher_id=teacher, days_ago=8, status="no_show")
        await _lesson(db, student_id=student, teacher_id=teacher, days_ago=1, status="no_show")

        found = await sig.find_dropout_risk(db)
        assert not _mine(found, student)
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_teacher_is_never_flagged(db):
    """Преподаватель заведён и как ученик — одна карточка про коллегу
    обесценивает весь список."""
    course = await _course(db, "tsk647-teacher курс")
    teacher = await _user(db, "tsk647-teacher-both", role="student")
    await db.execute(text(
        "INSERT INTO user_roles (user_id, role_id) "
        "SELECT :u, id FROM roles WHERE name = 'teacher' ON CONFLICT DO NOTHING"
    ), {"u": teacher})
    await db.commit()
    other = await _user(db, "tsk647-teacher-peer", role="teacher")
    try:
        await _enroll(db, teacher, course)
        await _submission(db, user_id=teacher, course_id=course, days_ago=25)
        await _lesson(db, student_id=teacher, teacher_id=other, days_ago=9, status="no_show")
        # Он ведёт собственное занятие — по этому признаку и отсекается.
        await _lesson(db, student_id=other, teacher_id=teacher, days_ago=2, status="confirmed")

        found = await sig.find_dropout_risk(db)
        assert not _mine(found, teacher)
    finally:
        await _cleanup(db, [teacher, other], [course])


# ───────────────────────── Сигнал и его жизнь ────────────────────────────────


@pytest.mark.asyncio
async def test_scan_creates_signal_with_readable_numbers(db):
    """Проход заводит карточку, и в ней есть, что прочитать.

    Доля ошибок у этого повода честно нулевая — числа лежат в `meta`, иначе
    карточка выехала бы преподавателю с бейджем «0 % ошибок», то есть выглядела
    бы как «всё в порядке» (ровно так уже стрелял повод про ИИ-авторство).
    """
    student, teacher, course = await _setup_silent_student(db, "tsk647-scan")
    try:
        res = await sig.scan_and_create_signals(db)
        assert res["dropout_signals_created"] >= 1

        row = (await db.execute(text(
            "SELECT reason, status, submissions, wrong_rate, meta "
            "FROM learning_gap_signal WHERE student_id = :u"
        ), {"u": student})).mappings().one()
        assert row["reason"] == sig.REASON_DROPOUT_RISK
        assert row["status"] == "new"
        assert row["submissions"] == 2, "число занятий, прошедших мимо ученика"
        assert row["wrong_rate"] == 0.0
        assert row["meta"]["window_days"] == sig.DROPOUT_WINDOW_DAYS
        assert row["meta"]["lessons_missed"] == 2
        assert row["meta"]["silence_days"] >= 14
        assert row["meta"]["last_attended"] is None

        # Повтор прохода не плодит карточки: cron ходит ежесуточно, и без этого
        # за неделю накопилось бы семь одинаковых.
        again = await sig.scan_and_create_signals(db)
        assert again["dropout_signals_created"] == 0
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_signal_sorts_first_among_reasons(db):
    """«Затих» идёт первым: у остальных поводов разговор можно отложить до
    занятия, здесь ученика может не оказаться уже на следующем."""
    student, teacher, course = await _setup_silent_student(db, "tsk647-order")
    peer = await _user(db, "tsk647-order-peer")
    try:
        await _enroll(db, peer, course)
        await sig.upsert_signal(
            db, course_id=course, student_id=peer, submissions=20, students=1,
            wrong_rate=0.9,
        )
        await db.commit()
        await sig.scan_and_create_signals(db)

        rows = await sig.list_signals(db, for_student=True)
        mine = [r for r in rows if r["student_id"] in (student, peer)]
        assert mine[0]["student_id"] == student, (
            "сигнал о риске ухода должен стоять выше сигнала о доле ошибок"
        )
    finally:
        await _cleanup(db, [student, teacher, peer], [course])


@pytest.mark.asyncio
async def test_dismissed_signal_does_not_return_next_day(db):
    """Разобранный сигнал не поднимается заново на следующем же проходе.

    Признак остаётся истинным, пока ученик не вернулся, — без поправки датчик
    заводил бы ту же карточку каждый день до бесконечности.
    """
    student, teacher, course = await _setup_silent_student(db, "tsk647-closed")
    try:
        await sig.scan_and_create_signals(db)
        signal_id = int((await db.execute(text(
            "SELECT id FROM learning_gap_signal WHERE student_id = :u"
        ), {"u": student})).scalar_one())
        await sig.dismiss_signal(db, signal_id=signal_id, teacher_id=teacher)

        res = await sig.scan_and_create_signals(db)
        assert res["dropout_signals_created"] == 0
        assert not _mine(await sig.find_dropout_risk(db), student)

        # А вот если ученик вернулся и затих снова — сигнал обязан вернуться.
        await _submission(db, user_id=student, course_id=course, days_ago=0)
        assert not _mine(await sig.find_dropout_risk(db), student), (
            "сразу после возвращения ученик ещё не затихший"
        )
    finally:
        await _cleanup(db, [student, teacher], [course])
