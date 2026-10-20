"""Лента активности по ученику и список учеников ленты — tsk-1362.

Раньше фильтр по ученику работал только по уже загруженным N событиям:
ученика без недавней активности нельзя было найти. Проверяем на настоящей БД
(граф фикстуры — из tsk-408):

* ``GET /teacher/activity-feed?student_id=`` — события только этого ученика,
  ACL не ослаблен (чужой ученик — пусто), курсор ``before`` работает внутри отбора;
* ``GET /teacher/activity-feed/students`` — все ученики зоны, включая тех, у кого
  событий нет вовсе; «на занятии» — сверху.
"""
from __future__ import annotations

from sqlalchemy import text

from app.services import lesson_plan_service
from tests.test_activity_feed_tsk408 import _new_user, agraph  # noqa: F401 — фикстура

_FEED = "/api/v1/teacher/activity-feed"
_STUDENTS = "/api/v1/teacher/activity-feed/students"


def _auth(agraph_: dict, who: str) -> dict:
    return {"Authorization": f"Bearer {agraph_['tokens'][who]}"}


async def test_feed_filtered_by_student(agraph, client):
    ids = agraph["ids"]
    resp = await client.get(
        f"{_FEED}?student_id={ids['student_course']}", headers=_auth(agraph, "teacher"),
    )
    assert resp.status_code == 200, resp.text
    events = resp.json()["events"]
    assert len(events) == 3
    assert {e["student_id"] for e in events} == {ids["student_course"]}


async def test_feed_filter_does_not_bypass_acl(agraph, client):
    ids = agraph["ids"]
    resp = await client.get(
        f"{_FEED}?student_id={ids['student_other']}", headers=_auth(agraph, "teacher"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["events"] == []


async def test_feed_filter_paginates_within_student(agraph, client):
    ids = agraph["ids"]
    headers = _auth(agraph, "teacher")
    sid = ids["student_direct"]
    # Фикстура пишет всё одной транзакцией — время событий совпадает, а курсор
    # строгий. Разводим события по времени.
    db = agraph["db"]
    await db.execute(
        text("UPDATE task_results SET submitted_at = now() - interval '1 hour' WHERE user_id = :s"),
        {"s": sid},
    )
    await db.execute(
        text("UPDATE help_requests SET created_at = now() - interval '2 hour' WHERE student_id = :s"),
        {"s": sid},
    )
    await db.commit()
    first = (await client.get(f"{_FEED}?student_id={sid}&limit=2", headers=headers)).json()
    assert len(first["events"]) == 2
    assert first["has_more"] is True
    second = (
        await client.get(
            f"{_FEED}?student_id={sid}&limit=2",
            headers=headers,
            params={"before": first["next_before"]},
        )
    ).json()
    assert second["events"], "дозагрузка по ученику вернула пусто"
    assert {e["student_id"] for e in first["events"] + second["events"]} == {sid}


async def test_students_list_includes_student_without_events(agraph, client):
    """Ученик на курсе преподавателя без единого события — в списке есть."""
    ids, db = agraph["ids"], agraph["db"]
    quiet_id, _ = await _new_user(db, "student", "quiet")
    try:
        await db.execute(
            text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
            {"u": quiet_id, "c": ids["root_course"]},
        )
        await db.commit()

        resp = await client.get(_STUDENTS, headers=_auth(agraph, "teacher"))
        assert resp.status_code == 200, resp.text
        got = {s["id"] for s in resp.json()["students"]}
        assert {ids["student_direct"], ids["student_course"], quiet_id} <= got
        assert ids["student_other"] not in got
        assert ids["teacher"] not in got
    finally:
        await db.rollback()
        for sql in (
            "DELETE FROM user_courses WHERE user_id = :u",
            "DELETE FROM user_session WHERE user_id = :u",
            "DELETE FROM identity_link WHERE user_id = :u",
            "DELETE FROM user_roles WHERE user_id = :u",
            "DELETE FROM users WHERE id = :u",
        ):
            await db.execute(text(sql), {"u": quiet_id})
        await db.commit()


async def test_students_list_on_lesson_first(agraph, client, monkeypatch):
    """На занятии — сверху и с признаком, даже если вне обычных связей (подмена)."""
    ids = agraph["ids"]

    async def fake_live(db, *, teacher_id, now=None):
        return [ids["student_other"]]

    monkeypatch.setattr(lesson_plan_service, "students_on_lesson_now", fake_live)
    resp = await client.get(_STUDENTS, headers=_auth(agraph, "teacher"))
    assert resp.status_code == 200, resp.text
    students = resp.json()["students"]
    assert students[0] == {
        "id": ids["student_other"], "name": "tsk408-sother", "on_lesson": True,
    }
    assert all(not s["on_lesson"] for s in students[1:])


async def test_students_list_methodist_and_stranger(agraph, client):
    ids = agraph["ids"]
    met = await client.get(_STUDENTS, headers=_auth(agraph, "methodist"))
    assert met.status_code == 200, met.text
    got = {s["id"] for s in met.json()["students"]}
    assert {ids["student_direct"], ids["student_course"], ids["student_other"]} <= got

    other = await client.get(_STUDENTS, headers=_auth(agraph, "other"))
    assert other.status_code == 200, other.text
    assert other.json()["students"] == []
