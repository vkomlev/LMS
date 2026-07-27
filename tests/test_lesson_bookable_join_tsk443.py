"""tsk-021/443: ученик выбирает из ближайших ДОСТУПНЫХ занятий и
ПРИСОЕДИНЯЕТСЯ к уже существующему occurrence — вместо свободного ввода
даты/времени, который создавал отдельный ad-hoc occurrence на то же время.

Реальный инцидент: `POST /lesson-occurrences/ad-hoc` создал occurrence
id=460 (1 участник, Денис Ильин) на то же время, что уже существующий
occurrence id=23 (slot_id=12, 3 участника) — тот же преподаватель,
та же минута. Оператор: "Под него не делается отдельный слот, а он
присоединяется к существующему".

Покрывает:
- `list_bookable_occurrences_for_student`: ближайшие БУДУЩИЕ occurrence
  привязанных преподавателей, исключая уже прошедшие и те, где ученик уже
  участник; имена преподавателей (в т.ч. несколько для со-преподавания,
  tsk-443); пустой список преподавателей → пусто.
- `join_occurrence_as_student`: создаёт участие, идемпотентно при повторе,
  404 несуществующему occurrence, 409 прошедшему occurrence, 409 при
  пересечении с другим активным занятием этого ученика.
- API `GET /me/lesson-occurrences/bookable` + `POST .../join`.
- Регресс на сам инцидент: НЕ создаётся отдельный occurrence — participant
  count существующего растёт на 1, total occurrence count не меняется.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, time, timezone as dt_timezone

import pytest
from sqlalchemy import text

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.users import Users
from app.repos.lesson_calendar_repository import LessonOccurrenceTeacherRepository
from app.services import lesson_occurrence_service
from app.services.auth.session_service import create_session
from app.utils.exceptions import DomainError


async def _create_user(db, *, role: str | None = None, prefix: str = "tsk443bk") -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-user", tg_id=None)
    db.add(u)
    await db.flush()
    if role:
        r = await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
        row = r.fetchone()
        if row is None:
            await db.execute(
                text("INSERT INTO roles (name) VALUES (:n) ON CONFLICT DO NOTHING"),
                {"n": role},
            )
            r = await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
            row = r.fetchone()
        role_id = int(row[0])
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "VALUES (:u, :r) ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "r": role_id},
        )
    await db.commit()
    return u.id


async def _link_student_teacher(db, *, student_id: int, teacher_id: int) -> None:
    await db.execute(
        text("INSERT INTO student_teacher_links (student_id, teacher_id) VALUES (:s, :t)"),
        {"s": student_id, "t": teacher_id},
    )
    await db.commit()


async def _create_occurrence(
    db, *, teacher_id: int, scheduled_at: datetime, duration_minutes: int = 60,
    co_teacher_ids: list[int] | None = None,
) -> int:
    occ = LessonOccurrence(
        slot_id=None, teacher_id=teacher_id, scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    db.add(occ)
    await db.flush()
    occ_id = occ.id
    repo = LessonOccurrenceTeacherRepository()
    await repo.create(db, occurrence_id=occ_id, teacher_id=teacher_id)
    for co_id in co_teacher_ids or []:
        await repo.create(db, occurrence_id=occ_id, teacher_id=co_id)
    await db.commit()
    return occ_id


def _future(hours: int = 2) -> datetime:
    return datetime.now(dt_timezone.utc) + timedelta(hours=hours)


def _past(hours: int = 2) -> datetime:
    return datetime.now(dt_timezone.utc) - timedelta(hours=hours)


@pytest.mark.asyncio
async def test_list_bookable_excludes_past_and_already_joined(db):
    teacher_id = await _create_user(db, role="teacher", prefix="tsk443bk-t1")
    student_id = await _create_user(db, role="student", prefix="tsk443bk-s1")

    future_id = await _create_occurrence(db, teacher_id=teacher_id, scheduled_at=_future(3))
    past_id = await _create_occurrence(db, teacher_id=teacher_id, scheduled_at=_past(3))
    already_joined_id = await _create_occurrence(db, teacher_id=teacher_id, scheduled_at=_future(5))
    db.add(
        LessonOccurrenceParticipant(
            occurrence_id=already_joined_id, student_id=student_id, status="scheduled",
        )
    )
    await db.commit()

    result = await lesson_occurrence_service.list_bookable_occurrences_for_student(
        db, student_id=student_id, teacher_ids=[teacher_id], limit=10,
    )
    ids = [o.id for o, _names in result]
    assert future_id in ids
    assert past_id not in ids
    assert already_joined_id not in ids


@pytest.mark.asyncio
async def test_list_bookable_empty_without_teacher_ids(db):
    student_id = await _create_user(db, role="student", prefix="tsk443bk-s2")
    result = await lesson_occurrence_service.list_bookable_occurrences_for_student(
        db, student_id=student_id, teacher_ids=[], limit=10,
    )
    assert result == []


@pytest.mark.asyncio
async def test_list_bookable_returns_all_co_teacher_names(db):
    teacher_a = await _create_user(db, role="teacher", prefix="tsk443bk-coa")
    teacher_b = await _create_user(db, role="teacher", prefix="tsk443bk-cob")
    student_id = await _create_user(db, role="student", prefix="tsk443bk-cos")

    occ_id = await _create_occurrence(
        db, teacher_id=teacher_a, scheduled_at=_future(2), co_teacher_ids=[teacher_b],
    )

    result = await lesson_occurrence_service.list_bookable_occurrences_for_student(
        db, student_id=student_id, teacher_ids=[teacher_a], limit=10,
    )
    match = next(o for o, _names in result if o.id == occ_id)
    names = next(names for o, names in result if o.id == occ_id)
    assert match.id == occ_id
    assert len(names) == 2


@pytest.mark.asyncio
async def test_join_occurrence_creates_participant(db):
    teacher_id = await _create_user(db, role="teacher", prefix="tsk443bk-ja")
    student_id = await _create_user(db, role="student", prefix="tsk443bk-js")
    occ_id = await _create_occurrence(db, teacher_id=teacher_id, scheduled_at=_future(2))

    occurrence, participant = await lesson_occurrence_service.join_occurrence_as_student(
        db, occurrence_id=occ_id, student_id=student_id,
    )
    assert occurrence.id == occ_id
    assert participant.student_id == student_id
    assert participant.status == "scheduled"


@pytest.mark.asyncio
async def test_join_occurrence_idempotent(db):
    teacher_id = await _create_user(db, role="teacher", prefix="tsk443bk-ia")
    student_id = await _create_user(db, role="student", prefix="tsk443bk-is")
    occ_id = await _create_occurrence(db, teacher_id=teacher_id, scheduled_at=_future(2))

    _occ1, p1 = await lesson_occurrence_service.join_occurrence_as_student(
        db, occurrence_id=occ_id, student_id=student_id,
    )
    _occ2, p2 = await lesson_occurrence_service.join_occurrence_as_student(
        db, occurrence_id=occ_id, student_id=student_id,
    )
    assert p1.id == p2.id

    count = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM lesson_occurrence_participant "
                "WHERE occurrence_id=:oid AND student_id=:sid"
            ),
            {"oid": occ_id, "sid": student_id},
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_join_occurrence_404_not_found(db):
    student_id = await _create_user(db, role="student", prefix="tsk443bk-nf")
    with pytest.raises(DomainError) as exc_info:
        await lesson_occurrence_service.join_occurrence_as_student(
            db, occurrence_id=999_999_999, student_id=student_id,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_join_occurrence_409_already_past(db):
    teacher_id = await _create_user(db, role="teacher", prefix="tsk443bk-pa")
    student_id = await _create_user(db, role="student", prefix="tsk443bk-ps")
    occ_id = await _create_occurrence(db, teacher_id=teacher_id, scheduled_at=_past(1))

    with pytest.raises(DomainError) as exc_info:
        await lesson_occurrence_service.join_occurrence_as_student(
            db, occurrence_id=occ_id, student_id=student_id,
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_join_occurrence_409_overlap(db):
    teacher_id = await _create_user(db, role="teacher", prefix="tsk443bk-oa")
    student_id = await _create_user(db, role="student", prefix="tsk443bk-os")
    at = _future(2)
    occ_1 = await _create_occurrence(db, teacher_id=teacher_id, scheduled_at=at)
    occ_2 = await _create_occurrence(db, teacher_id=teacher_id, scheduled_at=at)

    await lesson_occurrence_service.join_occurrence_as_student(
        db, occurrence_id=occ_1, student_id=student_id,
    )
    with pytest.raises(DomainError) as exc_info:
        await lesson_occurrence_service.join_occurrence_as_student(
            db, occurrence_id=occ_2, student_id=student_id,
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_api_bookable_and_join_end_to_end(db, client):
    teacher_id = await _create_user(db, role="teacher", prefix="tsk443bk-apit")
    student_id = await _create_user(db, role="student", prefix="tsk443bk-apis")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    occ_id = await _create_occurrence(db, teacher_id=teacher_id, scheduled_at=_future(2))

    token, _, _ = await create_session(db, user_id=student_id)

    resp = await client.get(
        "/api/v1/me/lesson-occurrences/bookable",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert any(i["id"] == occ_id for i in items)
    matched = next(i for i in items if i["id"] == occ_id)
    assert matched["teacher_names"] == ["tsk443bk-apit-user"]

    join_resp = await client.post(
        f"/api/v1/lesson-occurrences/{occ_id}/join",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert join_resp.status_code == 201, join_resp.text
    assert join_resp.json()["id"] == occ_id
    assert join_resp.json()["my_status"] == "scheduled"


@pytest.mark.asyncio
async def test_api_bookable_requires_auth(client):
    resp = await client.get("/api/v1/me/lesson-occurrences/bookable")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_regression_join_does_not_create_duplicate_occurrence(db, client):
    """Регрессия на реальный инцидент: occurrence id=460 (Денис Ильин, ad-hoc)
    дублировал уже существующий id=23 (slot_id=12) на то же время. Через
    новый bookable+join путь этого не должно происходить — присоединение к
    существующему occurrence НЕ создаёт нового."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk443bk-rega")
    existing_student_id = await _create_user(db, role="student", prefix="tsk443bk-regexisting")
    new_student_id = await _create_user(db, role="student", prefix="tsk443bk-regnew")
    await _link_student_teacher(db, student_id=new_student_id, teacher_id=teacher_id)

    at = _future(2)
    occ_id = await _create_occurrence(db, teacher_id=teacher_id, scheduled_at=at)
    db.add(
        LessonOccurrenceParticipant(
            occurrence_id=occ_id, student_id=existing_student_id, status="scheduled",
        )
    )
    await db.commit()

    total_before = (
        await db.execute(text("SELECT COUNT(*) FROM lesson_occurrence"))
    ).scalar_one()

    token, _, _ = await create_session(db, user_id=new_student_id)
    bookable = await client.get(
        "/api/v1/me/lesson-occurrences/bookable",
        headers={"Authorization": f"Bearer {token}"},
    )
    picked = next(i for i in bookable.json() if i["id"] == occ_id)
    join_resp = await client.post(
        f"/api/v1/lesson-occurrences/{picked['id']}/join",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert join_resp.status_code == 201, join_resp.text

    total_after = (
        await db.execute(text("SELECT COUNT(*) FROM lesson_occurrence"))
    ).scalar_one()
    assert total_after == total_before  # НЕ создалось новое occurrence

    participants = (
        await db.execute(
            text("SELECT student_id FROM lesson_occurrence_participant WHERE occurrence_id=:oid"),
            {"oid": occ_id},
        )
    ).scalars().all()
    assert set(participants) == {existing_student_id, new_student_id}
