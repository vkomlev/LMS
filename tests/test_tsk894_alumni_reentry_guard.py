"""tsk-894 — выпускника (alumni) нельзя ЗАВЕСТИ в новое место ПОСЛЕ перевода.

`graduation_service` (tsk-673) чистит расписание и доступ ОДНИМ событием, в
момент перевода на тариф, и ничего не проверяет на будущих операциях записи.
На проде это давало живую дыру: выпускника можно было добавить в слот
расписания, посадить на отработку или зачислить на курс уже ПОСЛЕ того, как
он стал выпускником.

Единый страж — `app.services.alumni_enrollment_guard` (по образцу
`course_activity_service`, tsk-886): 409 на всех точках создания НОВОЙ связи
ученик↔слот/occurrence/курс. Решение оператора (tsk-894): жёсткий запрет
везде, без исключений ни для сотрудника, ни для самозаписи.

Тесты держат обе стороны границы:
* **новую связь заводить нельзя** — слот (добавление и перевод), occurrence
  (ad-hoc от преподавателя и от ученика, присоединение к идущему занятию),
  курс (одиночное и пакетное зачисление, ручное назначение учителем);
* **уже существующее не трогаем** — идемпотентный повтор назначения курса,
  а отказ на переводе между слотами не оставляет ученика без слота вовсе
  (транзакция целиком откатывается).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services import alumni_enrollment_guard, subscription_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

pytestmark = pytest.mark.asyncio

_settings = Settings()
_DENY = alumni_enrollment_guard.ALUMNI_DETAIL


def _service_headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


async def _user(db, role: str | None) -> tuple[int, str]:
    """Пользователь с сессией (по образцу test_tsk491_slot_transfer)."""
    u = Users(
        email=f"t894-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t894-{role or 'norole'}",
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
                "SELECT :u, r.id FROM roles r WHERE r.name = :r ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "r": role},
        )
    await db.commit()
    return u.id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_alumni(db, student_id: int) -> None:
    """Перевести ученика на тариф «Выпускник» через штатный `change_plan`
    (закрывает действующую строку, если она уже есть — например, tsk-301
    успел автоматически поднять с demo на base при добавлении в слот — и
    открывает новую). Побочные действия `graduation_service.apply` (tsk-673)
    здесь не зовём намеренно: эта задача про то, что происходит ПОСЛЕ такого
    перевода, а не про сам перевод."""
    await subscription_service.change_plan(
        db, student_id, "alumni", reason="tsk-894 test setup"
    )
    await db.commit()


async def _slot(
    client, token: str, teacher_id: int, *, weekday: int = 1, start: str = "10:00:00",
) -> int:
    r = await client.post(
        "/api/v1/lesson-slots",
        json={
            "teacher_id": teacher_id,
            "weekday": weekday,
            "start_time": start,
            "duration_minutes": 60,
            "timezone": "Asia/Yekaterinburg",
            "student_ids": [],
        },
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _slot_membership(db, slot_id: int, student_id: int) -> bool | None:
    row = await db.execute(
        text(
            "SELECT is_active FROM lesson_slot_student "
            "WHERE slot_id = :s AND student_id = :u"
        ),
        {"s": slot_id, "u": student_id},
    )
    return row.scalar_one_or_none()


async def _future_occurrence(db, *, slot_id: int | None, teacher_id: int, days: int = 3) -> int:
    row = await db.execute(
        text(
            "INSERT INTO lesson_occurrence "
            "(slot_id, teacher_id, scheduled_at, duration_minutes) "
            "VALUES (:sl, :t, :at, 60) RETURNING id"
        ),
        {
            "sl": slot_id,
            "t": teacher_id,
            "at": datetime.now(timezone.utc) + timedelta(days=days),
        },
    )
    occurrence_id = int(row.scalar_one())
    await db.commit()
    return occurrence_id


async def _root_course(db, mark: str) -> int:
    return int(
        (
            await db.execute(
                text(
                    "INSERT INTO courses (title, access_level, is_active) "
                    "VALUES (:t, 'self_guided', true) RETURNING id"
                ),
                {"t": f"{mark}"},
            )
        ).scalar_one()
    )


# ---------------------------------------------------------------------------
# Юнит-уровень: сам страж
# ---------------------------------------------------------------------------


async def test_is_alumni_false_without_subscription(db):
    """Тариф не назначен вовсе — не выпускник (см. docstring гейта)."""
    student_id, _ = await _user(db, "student")
    assert await alumni_enrollment_guard.is_alumni(db, student_id) is False


async def test_is_alumni_true_after_transfer(db):
    student_id, _ = await _user(db, "student")
    await _make_alumni(db, student_id)
    assert await alumni_enrollment_guard.is_alumni(db, student_id) is True


async def test_assert_not_alumni_raises_409(db):
    from fastapi import HTTPException

    student_id, _ = await _user(db, "student")
    await _make_alumni(db, student_id)
    with pytest.raises(HTTPException) as exc:
        await alumni_enrollment_guard.assert_not_alumni(db, student_id, action="тест")
    assert exc.value.status_code == 409
    assert exc.value.detail == _DENY


# ---------------------------------------------------------------------------
# Слот расписания
# ---------------------------------------------------------------------------


async def test_add_slot_participant_denied_for_alumni(db, client):
    teacher_id, _ = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    _, methodist_token = await _user(db, "methodist")
    await _make_alumni(db, student_id)
    slot_id = await _slot(client, methodist_token, teacher_id)

    resp = await client.post(
        f"/api/v1/lesson-slots/{slot_id}/participants",
        json={"student_id": student_id},
        headers=_auth(methodist_token),
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text
    assert await _slot_membership(db, slot_id, student_id) is None


async def test_add_slot_participant_allowed_for_active_student(db, client):
    """Регресс: обычный ученик по-прежнему добавляется как раньше."""
    teacher_id, _ = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    _, methodist_token = await _user(db, "methodist")
    slot_id = await _slot(client, methodist_token, teacher_id)

    resp = await client.post(
        f"/api/v1/lesson-slots/{slot_id}/participants",
        json={"student_id": student_id},
        headers=_auth(methodist_token),
    )
    assert resp.status_code == 201, resp.text
    assert await _slot_membership(db, slot_id, student_id) is True


async def test_transfer_slot_participant_denied_for_alumni_keeps_source(db, client):
    """Отказ на переводе не оставляет ученика без слота вовсе (rollback целиком).

    Ученик уже состоял в исходном слоте ДО того, как стал выпускником — ровно
    живой случай 25.08 (4497/4500), который tsk-673 чистит в момент перехода,
    но не защищает от повторного заведения потом.
    """
    teacher_id, _ = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    _, methodist_token = await _user(db, "methodist")
    source = await _slot(client, methodist_token, teacher_id, weekday=0, start="09:00:00")
    target = await _slot(client, methodist_token, teacher_id, weekday=3, start="15:00:00")

    add = await client.post(
        f"/api/v1/lesson-slots/{source}/participants",
        json={"student_id": student_id},
        headers=_auth(methodist_token),
    )
    assert add.status_code == 201, add.text

    await _make_alumni(db, student_id)

    resp = await client.post(
        f"/api/v1/lesson-slots/{source}/participants/{student_id}/transfer",
        json={"target_slot_id": target},
        headers=_auth(methodist_token),
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text
    assert await _slot_membership(db, source, student_id) is True, (
        "отказ на переводе откатился не целиком — ученик остался без слота"
    )
    assert await _slot_membership(db, target, student_id) is None


# ---------------------------------------------------------------------------
# Занятие вне слота (ad-hoc / присоединение)
# ---------------------------------------------------------------------------


async def test_teacher_add_student_ad_hoc_denied_for_alumni(db, client):
    """`POST /teacher/lesson-occurrences/add-student` — преподаватель добавляет
    выпускника на отработку вручную."""
    teacher_id, teacher_token = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    await _make_alumni(db, student_id)

    resp = await client.post(
        "/api/v1/teacher/lesson-occurrences/add-student",
        json={
            "teacher_id": teacher_id,
            "student_id": student_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            "duration_minutes": 60,
        },
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text


async def test_student_self_ad_hoc_denied_for_alumni(db, client):
    """`POST /lesson-occurrences/ad-hoc` — ученик сам себе выпускник не запишет."""
    teacher_id, _ = await _user(db, "teacher")
    student_id, student_token = await _user(db, "student")
    await _make_alumni(db, student_id)

    resp = await client.post(
        "/api/v1/lesson-occurrences/ad-hoc",
        json={
            "teacher_id": teacher_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            "duration_minutes": 60,
        },
        headers=_auth(student_token),
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text


async def test_add_participant_to_occurrence_denied_for_alumni(db, client):
    """`POST /lesson-occurrences/{id}/participants` — преподаватель подключает
    выпускника к уже идущей группе."""
    teacher_id, teacher_token = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    await _make_alumni(db, student_id)
    occurrence_id = await _future_occurrence(db, slot_id=None, teacher_id=teacher_id)

    resp = await client.post(
        f"/api/v1/teacher/lesson-occurrences/{occurrence_id}/participants"
        f"?teacher_id={teacher_id}",
        json={"student_id": student_id},
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text


async def test_join_occurrence_denied_for_alumni(db, client):
    """`POST /lesson-occurrences/{id}/join` — выпускник сам не присоединится."""
    teacher_id, _ = await _user(db, "teacher")
    student_id, student_token = await _user(db, "student")
    await _make_alumni(db, student_id)
    occurrence_id = await _future_occurrence(db, slot_id=None, teacher_id=teacher_id)

    resp = await client.post(
        f"/api/v1/lesson-occurrences/{occurrence_id}/join",
        headers=_auth(student_token),
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text


async def test_join_occurrence_allowed_for_active_student(db, client):
    """Регресс: обычный ученик присоединяется как раньше."""
    teacher_id, _ = await _user(db, "teacher")
    student_id, student_token = await _user(db, "student")
    occurrence_id = await _future_occurrence(db, slot_id=None, teacher_id=teacher_id)

    resp = await client.post(
        f"/api/v1/lesson-occurrences/{occurrence_id}/join",
        headers=_auth(student_token),
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Курс
# ---------------------------------------------------------------------------


async def test_enroll_denied_for_alumni(db, client):
    student_id, _ = await _user(db, "student")
    await _make_alumni(db, student_id)
    course_id = await _root_course(db, f"t894-course-{random.randint(10**6, 10**8)}")

    resp = await client.post(
        "/api/v1/user-courses/",
        json={"user_id": student_id, "course_id": course_id},
        headers=_service_headers(),
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text
    assert (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM user_courses WHERE user_id = :u AND course_id = :c"
            ),
            {"u": student_id, "c": course_id},
        )
    ).scalar() == 0


async def test_bulk_enroll_denied_for_alumni_blocks_whole_batch(db, client):
    student_id, _ = await _user(db, "student")
    await _make_alumni(db, student_id)
    mark = f"t894-bulk-{random.randint(10**6, 10**8)}"
    course_a = await _root_course(db, f"{mark}-a")
    course_b = await _root_course(db, f"{mark}-b")

    resp = await client.post(
        f"/api/v1/users/{student_id}/courses/bulk",
        json={"course_ids": [course_a, course_b]},
        headers=_service_headers(),
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text
    assert (
        await db.execute(
            text("SELECT COUNT(*) FROM user_courses WHERE user_id = :u"),
            {"u": student_id},
        )
    ).scalar() == 0, "отказ выпускнику не должен зачислять ни один курс пачки"


async def test_teacher_manual_assign_denied_for_alumni(db, client):
    student_id, _ = await _user(db, "student")
    await _make_alumni(db, student_id)
    course_id = await _root_course(db, f"t894-manual-{random.randint(10**6, 10**8)}")

    resp = await client.post(
        f"/api/v1/teacher/students/{student_id}/assignments",
        json={"course_id": course_id},
        headers=_service_headers(),
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text


async def test_teacher_manual_assign_idempotent_on_already_enrolled_alumni(db, client):
    """Уже зачисленного ДО выпуска повторный вызов не ломает: новой связи нет
    (то же правило, что и у `course_activity_service`, tsk-886)."""
    student_id, _ = await _user(db, "student")
    course_id = await _root_course(db, f"t894-existing-{random.randint(10**6, 10**8)}")
    await db.execute(
        text("INSERT INTO user_courses (user_id, course_id) VALUES (:u, :c)"),
        {"u": student_id, "c": course_id},
    )
    await db.commit()
    await _make_alumni(db, student_id)

    resp = await client.post(
        f"/api/v1/teacher/students/{student_id}/assignments",
        json={"course_id": course_id},
        headers=_service_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["already_enrolled"] is True, resp.text
