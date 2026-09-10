"""tsk-743: план занятия — напоминания преподавателю по ходу урока.

Проверяем на НАСТОЯЩЕЙ БД (как `test_teacher_lesson_summary_tsk022_410.py`).

Главное, что здесь проверяется, — не «эндпоинт отвечает 200», а три свойства,
ради которых задача и делалась:

1. **Шаг без данных не приходит вовсе.** Пустых напоминаний на уроке быть не
   должно: у группы, где всё в порядке, план пустой.
2. **Видна только текущая фаза.** Шаги начала не приходят в середине урока и
   наоборот.
3. **Список пропустивших схлопывается.** Отметил разговор — строка исчезла;
   иначе один пропуск всплывал бы на каждом занятии до конца года.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.users import Users
from app.services import lesson_plan_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

UTC = timezone.utc
_TAG = "tsk743"


# ============================== Helpers ==============================


async def _new_user(db, *, role: str | None, name: str) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.commit()
    return u.id, token


async def _occurrence(
    db, *, teacher_id: int, scheduled_at: datetime, students: dict[int, str],
    duration_minutes: int = 60,
) -> int:
    occ = LessonOccurrence(
        slot_id=None, teacher_id=teacher_id, scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    db.add(occ)
    await db.flush()
    for student_id, status in students.items():
        db.add(
            LessonOccurrenceParticipant(
                occurrence_id=occ.id, student_id=student_id, status=status,
            )
        )
    occ_id = occ.id
    await db.commit()
    return occ_id


async def _new_course(db, title: str) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES (:t, 'self_guided') RETURNING id"
            ),
            {"t": title},
        )
    ).scalar()


async def _new_task(db, *, course_id: int, uid: str) -> int:
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    assert difficulty_id is not None, "нет difficulties — граф не собрать"
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, "
                "difficulty_id, external_uid, max_score, order_position) "
                "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 10, 1) "
                "RETURNING id"
            ),
            {
                "tc": json.dumps({"type": "SA", "stem": f"{_TAG} условие {uid}"}),
                "sr": json.dumps({"max_score": 10}),
                "cid": course_id,
                "did": difficulty_id,
                "uid": f"{_TAG}-{uid}-{random.randint(10**8, 10**10)}",
            },
        )
    ).scalar()


async def _result(
    db, *, student_id: int, task_id: int, course_id: int, is_correct: bool, at: datetime,
) -> None:
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :c, 'test') RETURNING id"
            ),
            {"u": student_id, "c": course_id},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, checked_at, source_system) "
            "VALUES (:u, :t, :a, :sc, 10, :ok, :ts, :ts, 0, :ts, 'test')"
        ),
        {
            "u": student_id, "t": task_id, "a": attempt_id,
            "sc": 10 if is_correct else 0, "ok": is_correct, "ts": at,
        },
    )
    await db.commit()


async def _award(db, *, student_id: int, earned_at: datetime) -> int:
    """Выдать ученику значок «10 шагов между занятиями» (tsk-889).

    Значок заводится свой, а не берётся из справочника: план занятия
    разбирает ПОВОД из условия, и тест обязан задавать условие сам, иначе он
    молча зависит от того, что лежит в справочнике этой базы.
    """
    achievement_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO achievements (name, condition, description) "
                    "VALUES (:n, CAST(:c AS jsonb), :d) RETURNING id"
                ),
                {
                    "n": f"{_TAG} 10 шагов между занятиями",
                    "c": json.dumps({"type": "between_lessons_items", "count": 10}),
                    "d": "Десять заданий и материалов, закрытых между уроками.",
                },
            )
        ).scalar_one()
    )
    await db.execute(
        text(
            "INSERT INTO user_achievements (user_id, achievement_id, earned_at) "
            "VALUES (:u, :a, :t)"
        ),
        {"u": student_id, "a": achievement_id, "t": earned_at},
    )
    await db.commit()
    return achievement_id


async def _homework(
    db, *, student_id: int, task_ids: list[int], due_at: datetime, issued_at: datetime,
) -> int:
    homework_id = (
        await db.execute(
            text(
                "INSERT INTO homework_assignment "
                "  (student_id, issued_at, due_at, source, planned_volume) "
                "VALUES (:s, :issued, :due, 'teacher', :volume) RETURNING id"
            ),
            {
                "s": student_id, "issued": issued_at, "due": due_at,
                "volume": len(task_ids),
            },
        )
    ).scalar()
    for position, task_id in enumerate(task_ids, start=1):
        await db.execute(
            text(
                "INSERT INTO homework_item (homework_id, kind, task_id, position) "
                "VALUES (:h, 'task', :t, :p)"
            ),
            {"h": homework_id, "t": task_id, "p": position},
        )
    await db.commit()
    return homework_id


async def _idle_episode(
    db, *, occurrence_id: int, student_id: int, kind: str, silent_since: datetime,
    resolved: bool = False,
) -> None:
    await db.execute(
        text(
            "INSERT INTO lesson_idle_episode "
            "  (occurrence_id, student_id, kind, silent_since, detected_at, resolved_at) "
            "VALUES (:o, :s, :k, :since, now(), :resolved)"
        ),
        {
            "o": occurrence_id, "s": student_id, "k": kind, "since": silent_since,
            "resolved": datetime.now(UTC) if resolved else None,
        },
    )
    await db.commit()


def _step(payload: dict, key: str) -> dict | None:
    return next((s for s in payload["steps"] if s["key"] == key), None)


async def _get_plan(client, *, occ_id: int, teacher_id: int, token: str):
    return await client.get(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/plan",
        params={"teacher_id": teacher_id},
        headers={"Authorization": f"Bearer {token}"},
    )


# ============================== Фазы ==============================


def test_phases_cover_lesson_without_gaps():
    """Границы фаз: до начала, начало, ход, конец, «панель больше не нужна».

    Единственное место, где ошибка была бы тихой: план показал бы шаги начала
    в середине урока — и преподаватель перестал бы ему верить.
    """
    start = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
    ends = start + timedelta(minutes=60)
    wrapup = ends - timedelta(minutes=15)
    phase = lambda minutes: lesson_plan_service.compute_phase(  # noqa: E731
        start + timedelta(minutes=minutes),
        scheduled_at=start, ends_at=ends, wrapup_from=wrapup, lead_minutes=15,
    )[0]

    assert phase(-30) == "before"
    assert phase(-10) == "start"
    assert phase(5) == "start"
    assert phase(20) == "during"
    assert phase(44) == "during"
    assert phase(46) == "wrapup"
    assert phase(85) == "wrapup"
    assert phase(95) == "after"


def test_short_lesson_does_not_start_in_wrapup():
    """30-минутный урок: конец не наступает с первой минуты.

    Тот же дефект, что чинили в кнопке «Подвести итоги» (tsk-741, 02.09):
    без нижней границы окно `wrapup` съело бы весь короткий урок.
    """
    start = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
    ends = start + timedelta(minutes=30)
    wrapup = max(ends - timedelta(minutes=15), start + timedelta(minutes=15))
    assert lesson_plan_service.compute_phase(
        start + timedelta(minutes=5),
        scheduled_at=start, ends_at=ends, wrapup_from=wrapup, lead_minutes=15,
    )[0] == "start"


# ============================== Доступ ==============================


@pytest.mark.asyncio
async def test_plan_403_for_foreign_occurrence(db, client):
    """IDOR: свой teacher_id, чужое занятие — второй гейт обязан отклонить."""
    teacher_a, _ = await _new_user(db, role="teacher", name="ta")
    teacher_b, token_b = await _new_user(db, role="teacher", name="tb")
    student_id, _ = await _new_user(db, role="student", name="st")
    occ_id = await _occurrence(
        db, teacher_id=teacher_a, scheduled_at=datetime.now(UTC),
        students={student_id: "confirmed"},
    )

    resp = await _get_plan(client, occ_id=occ_id, teacher_id=teacher_b, token=token_b)
    assert resp.status_code == 403, resp.text


# ============================== Начало урока ==============================


@pytest.mark.asyncio
async def test_quiet_group_gets_empty_plan(db, client):
    """Группа, где всё в порядке, не получает ни одного напоминания.

    Это главное свойство экрана: не перегрузить. Пустой шаг «не забудьте
    спросить про ДЗ» здесь недопустим.
    """
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    student_id, _ = await _new_user(db, role="student", name="s")
    occ_id = await _occurrence(
        db, teacher_id=teacher_id,
        scheduled_at=datetime.now(UTC) + timedelta(minutes=5),
        students={student_id: "confirmed"},
    )

    resp = await _get_plan(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["phase"] == "start"
    assert payload["steps"] == []


@pytest.mark.asyncio
async def test_start_phase_shows_unfinished_homework_with_names(db, client):
    """Несделанное ДЗ приходит с именем и счётом, а сделанное — как успех.

    tsk-889: срок у должника ПРОШЁЛ. В список идёт только просроченное —
    подпись «спросите, что не получилось» на работе, срок которой ещё не
    наступил, спрашивает не за что.
    """
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    lazy_id, _ = await _new_user(db, role="student", name="lazy")
    diligent_id, _ = await _new_user(db, role="student", name="dili")
    course_id = await _new_course(db, f"{_TAG} курс")
    task_a = await _new_task(db, course_id=course_id, uid="a")
    task_b = await _new_task(db, course_id=course_id, uid="b")

    now = datetime.now(UTC)
    await _homework(
        db, student_id=lazy_id, task_ids=[task_a, task_b],
        issued_at=now - timedelta(days=3), due_at=now - timedelta(hours=1),
    )
    await _homework(
        db, student_id=diligent_id, task_ids=[task_a],
        issued_at=now - timedelta(days=3), due_at=now + timedelta(days=1),
    )
    await _result(
        db, student_id=diligent_id, task_id=task_a, course_id=course_id,
        is_correct=True, at=now - timedelta(days=1),
    )

    occ_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now + timedelta(minutes=5),
        students={lazy_id: "confirmed", diligent_id: "confirmed"},
    )

    payload = (
        await _get_plan(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    ).json()

    homework_step = _step(payload, "homework")
    assert homework_step is not None, payload
    assert [s["student_id"] for s in homework_step["students"]] == [lazy_id]
    assert "0 из 2" in homework_step["students"][0]["detail"]

    wins = _step(payload, "wins")
    assert wins is not None and wins["students"][0]["student_id"] == diligent_id


@pytest.mark.asyncio
async def test_long_step_sends_everyone_and_says_how_many_to_show(db, client):
    """Шаг отдаёт ВСЕХ учеников, а не первых N (tsk-830).

    Замечание оператора 08.09: «„ещё“ не раскрывает список целиком». И не могло
    — лишние имена отбрасывались на сервере, и раскрывать было нечего.
    Ограничение осталось, но стало ограничением ПОКАЗА: `preview_count`
    говорит, сколько показать сразу, а остальные лежат рядом.
    """
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    course_id = await _new_course(db, f"{_TAG} курс")
    task_id = await _new_task(db, course_id=course_id, uid="hw")

    now = datetime.now(UTC)
    students: dict[int, str] = {}
    for i in range(8):
        student_id, _ = await _new_user(db, role="student", name=f"s{i}")
        await _homework(
            db, student_id=student_id, task_ids=[task_id],
            # tsk-889: срок прошёл — иначе в шаг никто не попадёт.
            issued_at=now - timedelta(days=3), due_at=now - timedelta(hours=1),
        )
        students[student_id] = "confirmed"

    occ_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now + timedelta(minutes=5),
        students=students,
    )

    payload = (
        await _get_plan(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    ).json()

    step = _step(payload, "homework")
    assert step is not None, payload
    assert len(step["students"]) == 8, "часть учеников потеряна на сервере"
    assert step["preview_count"] < 8, "смысл короткого списка на уроке потерян"
    assert step["more_count"] == 8 - step["preview_count"]


@pytest.mark.asyncio
async def test_absences_step_lists_unasked_and_disappears_after_followup(db, client):
    """Пропуски без объяснения: список собирается сам и схлопывается после
    отметки разговора.

    Без отметки строка «спросите, почему пропустил» висела бы на каждом
    занятии до конца года — ровно тот шум, из-за которого напоминания
    перестают читать.
    """
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    student_id, _ = await _new_user(db, role="student", name="s")
    now = datetime.now(UTC)

    missed_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now - timedelta(days=7),
        students={student_id: "no_show"},
    )
    # Перенос — это «отметился»: в список он попасть не должен.
    await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now - timedelta(days=3),
        students={student_id: "rescheduled"},
    )
    occ_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now + timedelta(minutes=5),
        students={student_id: "confirmed"},
    )

    payload = (
        await _get_plan(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    ).json()
    absences = _step(payload, "absences")
    assert absences is not None, payload
    row = absences["students"][0]
    assert row["student_id"] == student_id
    assert row["missed_occurrence_ids"] == [missed_id]

    marked = await client.post(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/absence-followup",
        params={"teacher_id": teacher_id},
        json={
            "student_id": student_id,
            "occurrence_ids": [missed_id],
            "reason": "illness",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["marked"] == 1

    after = (
        await _get_plan(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    ).json()
    assert _step(after, "absences") is None, after


@pytest.mark.asyncio
async def test_absence_followup_is_idempotent_and_validates_reason(db, client):
    """Повтор ничего не добавляет (панель открыта и на телефоне, и в браузере),
    неизвестная причина отклоняется."""
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    student_id, _ = await _new_user(db, role="student", name="s")
    now = datetime.now(UTC)
    missed_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now - timedelta(days=2),
        students={student_id: "no_show"},
    )
    occ_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now + timedelta(minutes=5),
        students={student_id: "confirmed"},
    )
    url = f"/api/v1/teacher/lesson-occurrences/{occ_id}/absence-followup"
    headers = {"Authorization": f"Bearer {token}"}

    first = await client.post(
        url, params={"teacher_id": teacher_id}, headers=headers,
        json={"student_id": student_id, "occurrence_ids": [missed_id]},
    )
    second = await client.post(
        url, params={"teacher_id": teacher_id}, headers=headers,
        json={"student_id": student_id, "occurrence_ids": [missed_id]},
    )
    assert first.json()["marked"] == 1
    assert second.json()["marked"] == 0

    bad = await client.post(
        url, params={"teacher_id": teacher_id}, headers=headers,
        json={"student_id": student_id, "occurrence_ids": [missed_id], "reason": "спал"},
    )
    assert bad.status_code == 422, bad.text


# ============================== Ход урока ==============================


@pytest.mark.asyncio
async def test_during_phase_shows_open_idle_and_hides_resolved(db, client):
    """Простой сейчас — на экране; вернувшийся ученик из списка уходит.

    Сигнал уже считается фоновым тиком (tsk-591) и уходит уведомлением, которое
    читают в 17% случаев, — здесь он попадает туда, куда преподаватель смотрит.
    """
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    silent_id, _ = await _new_user(db, role="student", name="silent")
    back_id, _ = await _new_user(db, role="student", name="back")
    now = datetime.now(UTC)
    occ_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now - timedelta(minutes=25),
        students={silent_id: "confirmed", back_id: "confirmed"},
    )
    await _idle_episode(
        db, occurrence_id=occ_id, student_id=silent_id, kind="idle",
        silent_since=now - timedelta(minutes=12),
    )
    await _idle_episode(
        db, occurrence_id=occ_id, student_id=back_id, kind="away",
        silent_since=now - timedelta(minutes=20), resolved=True,
    )

    payload = (
        await _get_plan(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    ).json()
    assert payload["phase"] == "during"
    idle = _step(payload, "idle")
    assert idle is not None, payload
    assert [s["student_id"] for s in idle["students"]] == [silent_id]
    assert "мин" in idle["students"][0]["detail"]
    # Шаги начала в середине урока не показываются.
    assert _step(payload, "homework") is None


@pytest.mark.asyncio
async def test_during_phase_shows_stuck_task_but_not_solved_one(db, client):
    """Буксует на задании — три неверные попытки и задание всё ещё не решено.

    Если ученик в итоге решил, напоминание уходит: звать преподавателя к тому,
    что уже получилось, — это ложный сигнал.
    """
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    stuck_id, _ = await _new_user(db, role="student", name="stuck")
    solved_id, _ = await _new_user(db, role="student", name="solved")
    course_id = await _new_course(db, f"{_TAG} курс")
    task_id = await _new_task(db, course_id=course_id, uid="stuck")

    now = datetime.now(UTC)
    occ_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now - timedelta(minutes=25),
        students={stuck_id: "confirmed", solved_id: "confirmed"},
    )
    for minutes in (20, 15, 10):
        await _result(
            db, student_id=stuck_id, task_id=task_id, course_id=course_id,
            is_correct=False, at=now - timedelta(minutes=minutes),
        )
    for minutes in (20, 15, 10):
        await _result(
            db, student_id=solved_id, task_id=task_id, course_id=course_id,
            is_correct=False, at=now - timedelta(minutes=minutes),
        )
    await _result(
        db, student_id=solved_id, task_id=task_id, course_id=course_id,
        is_correct=True, at=now - timedelta(minutes=5),
    )

    payload = (
        await _get_plan(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    ).json()
    stuck = _step(payload, "stuck")
    assert stuck is not None, payload
    assert [s["student_id"] for s in stuck["students"]] == [stuck_id]
    assert stuck["students"][0]["task_id"] == task_id


# ============================== Конец урока ==============================


@pytest.mark.asyncio
async def test_wrapup_lists_present_students_only(db, client):
    """В конце обсуждаем работу тех, кто был; не пришедших в списке нет."""
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    present_id, _ = await _new_user(db, role="student", name="present")
    absent_id, _ = await _new_user(db, role="student", name="absent")
    now = datetime.now(UTC)
    occ_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now - timedelta(minutes=55),
        students={present_id: "confirmed", absent_id: "no_show"},
    )

    payload = (
        await _get_plan(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    ).json()
    assert payload["phase"] == "wrapup"
    review = _step(payload, "review")
    assert review is not None, payload
    assert [s["student_id"] for s in review["students"]] == [present_id]
    # Разбор работы и разговор про тему — ОДИН шаг: два списка с теми же
    # именами были бы перегрузкой (замечено при живом просмотре 04.09).
    assert _step(payload, "topic") is None


@pytest.mark.asyncio
async def test_wrapup_without_anyone_present_has_no_steps(db, client):
    """Никто не пришёл — подводить итоги не с кем, план пуст."""
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    student_id, _ = await _new_user(db, role="student", name="s")
    now = datetime.now(UTC)
    occ_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now - timedelta(minutes=55),
        students={student_id: "no_show"},
    )

    payload = (
        await _get_plan(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    ).json()
    assert payload["steps"] == []


@pytest.mark.asyncio
async def test_finished_lesson_returns_no_steps(db, client):
    """Через час после конца панель не нужна — шагов нет."""
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    student_id, _ = await _new_user(db, role="student", name="s")
    now = datetime.now(UTC)
    occ_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now - timedelta(hours=3),
        students={student_id: "confirmed"},
    )

    payload = (
        await _get_plan(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    ).json()
    assert payload["phase"] == "after"
    assert payload["steps"] == []


@pytest.mark.asyncio
async def test_absence_followup_rejects_foreign_student_and_foreign_absence(db, client):
    """Отметку нельзя поставить за чужого ученика и за чужой пропуск.

    Занятия и ученик приходят телом запроса, то есть подбираются снаружи.
    Без проверок чужой пропуск молча исчез бы из плана у другого
    преподавателя — а тот больше про него не вспомнил бы.
    """
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    mine, _ = await _new_user(db, role="student", name="mine")
    stranger, _ = await _new_user(db, role="student", name="stranger")
    other_teacher, _ = await _new_user(db, role="teacher", name="other")
    now = datetime.now(UTC)

    foreign_missed = await _occurrence(
        db, teacher_id=other_teacher, scheduled_at=now - timedelta(days=3),
        students={stranger: "no_show"},
    )
    # Занятие МОЕГО ученика, но он на нём был — это не пропуск.
    attended = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now - timedelta(days=2),
        students={mine: "confirmed"},
    )
    occ_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now + timedelta(minutes=5),
        students={mine: "confirmed"},
    )
    url = f"/api/v1/teacher/lesson-occurrences/{occ_id}/absence-followup"
    headers = {"Authorization": f"Bearer {token}"}

    foreign_student = await client.post(
        url, params={"teacher_id": teacher_id}, headers=headers,
        json={"student_id": stranger, "occurrence_ids": [foreign_missed]},
    )
    assert foreign_student.status_code == 422, foreign_student.text

    not_an_absence = await client.post(
        url, params={"teacher_id": teacher_id}, headers=headers,
        json={"student_id": mine, "occurrence_ids": [attended, foreign_missed]},
    )
    assert not_an_absence.status_code == 200, not_an_absence.text
    assert not_an_absence.json()["marked"] == 0

    left = (
        await db.execute(
            text("SELECT count(*) FROM lesson_absence_followup WHERE student_id = ANY(:ids)"),
            {"ids": [mine, stranger]},
        )
    ).scalar()
    assert left == 0


@pytest.mark.asyncio
async def test_far_future_lesson_has_no_steps(db, client):
    """Занятие через неделю: фаза `before`, шагов нет и они не считаются.

    Показывать домашнюю работу и пропуски за неделю до урока бессмысленно —
    смотреть их будут перед занятием, и к тому моменту данные изменятся, — а
    считать их на каждое из пятнадцати занятий экрана ещё и дорого.
    """
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    student_id, _ = await _new_user(db, role="student", name="s")
    now = datetime.now(UTC)
    # У ученика есть и пропуск, и он попал бы в шаг — если бы шаги считались.
    await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now - timedelta(days=3),
        students={student_id: "no_show"},
    )
    occ_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now + timedelta(days=7),
        students={student_id: "scheduled"},
    )

    payload = (
        await _get_plan(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    ).json()
    assert payload["phase"] == "before"
    assert payload["steps"] == []


@pytest.mark.asyncio
async def test_homework_in_progress_is_not_a_debt(db, client):
    """Срок не вышел — человека в «не сделана» нет (tsk-889).

    До задачи в список попадал и тот, кому выдачу сделали вчера: на проде
    10.09 так набиралось четверо из восьми, и половина была не должниками, а
    просто ещё не взявшимися. Подпись у шага — «спросите, что не получилось».
    """
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    student_id, _ = await _new_user(db, role="student", name="s")
    course_id = await _new_course(db, f"{_TAG} курс")
    task_id = await _new_task(db, course_id=course_id, uid="in-progress")

    now = datetime.now(UTC)
    await _homework(
        db, student_id=student_id, task_ids=[task_id],
        issued_at=now - timedelta(hours=2), due_at=now + timedelta(days=2),
    )
    occ_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now + timedelta(minutes=5),
        students={student_id: "confirmed"},
    )

    payload = (
        await _get_plan(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    ).json()
    assert _step(payload, "homework") is None, (
        "человек со сроком впереди назван должником"
    )


@pytest.mark.asyncio
async def test_overdue_student_is_not_praised_for_a_badge(db, client):
    """С просроченным ДЗ значок вслух не называют (tsk-889).

    Замечание оператора 10.09 со снимка экрана: один и тот же ученик стоял и
    в «спросите, что не получилось», и в «назовите вслух». Значок за шаги
    накопительный — его порог мог быть взят неделю назад, а долг стоит
    сейчас.
    """
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    student_id, _ = await _new_user(db, role="student", name="s")
    course_id = await _new_course(db, f"{_TAG} курс")
    task_id = await _new_task(db, course_id=course_id, uid="debt")

    now = datetime.now(UTC)
    await _homework(
        db, student_id=student_id, task_ids=[task_id],
        issued_at=now - timedelta(days=3), due_at=now - timedelta(hours=1),
    )
    await _award(db, student_id=student_id, earned_at=now - timedelta(days=1))

    occ_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now + timedelta(minutes=5),
        students={student_id: "confirmed"},
    )
    payload = (
        await _get_plan(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    ).json()

    assert _step(payload, "homework") is not None, "долг пропал из списка"
    assert _step(payload, "wins") is None, (
        "ученика с просроченным ДЗ предложили похвалить вслух"
    )


@pytest.mark.asyncio
async def test_badge_is_named_by_reason_not_by_title(db, client):
    """Повод похвалы — словами, а не названием значка (tsk-889).

    «Названия ачивок непонятны никому, ни ученикам, ни преподам» — замечание
    оператора 10.09. Строка идёт под подписью «Назовите вслух», её читают
    группе: «10 шагов между занятиями» не сообщает ни что за шаги, ни за
    какой срок.
    """
    teacher_id, token = await _new_user(db, role="teacher", name="t")
    student_id, _ = await _new_user(db, role="student", name="s")

    now = datetime.now(UTC)
    await _award(db, student_id=student_id, earned_at=now - timedelta(days=1))
    occ_id = await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=now + timedelta(minutes=5),
        students={student_id: "confirmed"},
    )
    payload = (
        await _get_plan(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    ).json()

    wins = _step(payload, "wins")
    assert wins is not None, payload
    detail = wins["students"][0]["detail"]
    assert "закрыл 10 заданий и материалов между уроками" == detail, detail
    assert "шагов" not in detail
