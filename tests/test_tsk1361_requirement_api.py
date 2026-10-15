"""tsk-1361 Б3: API обязательности — учитель задаёт, ученик видит причину.

Учитель: предпросмотр масштаба, решение по элементу или подкурсу, снятие;
чужой преподаватель — 403; «сделать обязательным» необязательное по курсу —
422 (повышение пока не поддерживается потребителями). Ученик: личный уровень
и причина на странице задания — ровно то, что действует (рубильник Б2).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services.content_grace_service import invalidate_grace_cache
from tests.test_manual_progress_tsk297 import _new_user, graph  # noqa: F401 — фикстура

pytestmark = pytest.mark.asyncio


def _base(ids: dict) -> str:
    return f"/api/v1/teacher/students/{ids['student']}/progress"


async def _cleanup_overrides(db, student_id: int) -> None:
    await db.execute(
        text("DELETE FROM student_requirement_override WHERE student_id = :s"), {"s": student_id}
    )
    await db.commit()


async def test_foreign_teacher_cannot_set_requirement(graph, client):  # noqa: F811
    """Преподаватель без связки с учеником → 403, запись не создаётся."""
    ids = graph["ids"]
    resp = await client.put(
        _base(ids) + "/requirement",
        json={"target_kind": "task", "target_id": ids["task_root_a"], "level": "recommended"},
        headers={"Authorization": f"Bearer {graph['tokens']['other']}"},
    )
    assert resp.status_code == 403, resp.text
    count = (
        await graph["db"].execute(
            text("SELECT count(*) FROM student_requirement_override WHERE student_id = :s"),
            {"s": ids["student"]},
        )
    ).scalar_one()
    assert count == 0


async def test_teacher_full_cycle_on_subcourse(graph, client, monkeypatch):  # noqa: F811
    """Предпросмотр → снять обязательность с подкурса → видно в карточке → снять решение."""
    ids, db = graph["ids"], graph["db"]
    headers = {"Authorization": f"Bearer {graph['tokens']['teacher']}"}
    try:
        preview = await client.get(
            _base(ids) + f"/requirement/preview?target_kind=course&target_id={ids['child']}",
            headers=headers,
        )
        assert preview.status_code == 200, preview.text
        assert (preview.json()["tasks_affected"], preview.json()["materials_affected"]) == (1, 1)

        put = await client.put(
            _base(ids) + "/requirement",
            json={
                "target_kind": "course", "target_id": ids["child"],
                "level": "recommended", "comment": "тема уже пройдена на занятии",
            },
            headers=headers,
        )
        assert put.status_code == 200, put.text
        assert put.json()["override_id"]

        monkeypatch.setenv("REQUIREMENT_OVERRIDE_READ_ENABLED", "true")
        tree = await client.get(f"{_base(ids)}?course_id={ids['root']}", headers=headers)
        assert tree.status_code == 200, tree.text
        item = next(
            i for i in tree.json()["items"]
            if i["item_type"] == "task" and i["item_id"] == ids["task_child"]
        )
        assert item["requirement_level"] == "recommended"
        assert item["course_requirement_level"] == "required"
        assert item["requirement_reason"] == {
            "code": "teacher", "text": "тема уже пройдена на занятии", "source": "human",
        }
        untouched = next(
            i for i in tree.json()["items"]
            if i["item_type"] == "task" and i["item_id"] == ids["task_root_a"]
        )
        assert untouched["requirement_level"] == "required"
        assert untouched["requirement_reason"] is None

        delete = await client.delete(
            _base(ids) + f"/requirement?target_kind=course&target_id={ids['child']}",
            headers=headers,
        )
        assert delete.status_code == 200, delete.text
        assert delete.json()["revoked"] is True
        again = await client.delete(
            _base(ids) + f"/requirement?target_kind=course&target_id={ids['child']}",
            headers=headers,
        )
        assert again.json()["revoked"] is False, "Снятие идемпотентно"
    finally:
        await _cleanup_overrides(db, ids["student"])


async def test_promoting_course_optional_item_is_rejected(graph, client):  # noqa: F811
    """Сделать обязательным необязательное по курсу — 422, а не тихо бесполезная запись."""
    ids, db = graph["ids"], graph["db"]
    await db.execute(
        text("UPDATE tasks SET requirement_level = 'recommended' WHERE id = :t"),
        {"t": ids["task_root_b"]},
    )
    await db.commit()
    resp = await client.put(
        _base(ids) + "/requirement",
        json={"target_kind": "task", "target_id": ids["task_root_b"], "level": "required"},
        headers={"Authorization": f"Bearer {graph['tokens']['teacher']}"},
    )
    assert resp.status_code == 422, resp.text
    useless = await client.put(
        _base(ids) + "/requirement",
        json={"target_kind": "task", "target_id": ids["task_root_b"], "level": "recommended"},
        headers={"Authorization": f"Bearer {graph['tokens']['teacher']}"},
    )
    assert useless.status_code == 422, "Понижать необязательное по курсу некуда"


async def test_student_sees_personal_level_and_reason_only_when_in_effect(
    graph, client, monkeypatch  # noqa: F811
):
    """Ученик: причина — только когда уровень действительно отличается от курса."""
    ids, db = graph["ids"], graph["db"]
    student_id, student_token = await _new_user(db, "student", "tsk1361-stud")
    await db.execute(
        text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
        {"u": student_id, "c": ids["root"]},
    )
    await db.execute(
        text("INSERT INTO student_teacher_links (student_id, teacher_id) VALUES (:s, :t)"),
        {"s": student_id, "t": ids["teacher"]},
    )
    await db.commit()
    headers = {"Authorization": f"Bearer {student_token}"}
    try:
        put = await client.put(
            f"/api/v1/teacher/students/{student_id}/progress/requirement",
            json={"target_kind": "task", "target_id": ids["task_root_a"], "level": "recommended"},
            headers={"Authorization": f"Bearer {graph['tokens']['teacher']}"},
        )
        assert put.status_code == 200, put.text

        monkeypatch.setenv("REQUIREMENT_OVERRIDE_READ_ENABLED", "false")
        off = await client.get(f"/api/v1/me/requirement?task_id={ids['task_root_a']}", headers=headers)
        assert off.status_code == 200, off.text
        assert off.json()["requirement_level"] == "required"
        assert off.json()["requirement_reason"] is None, "Решение без эффекта ученику не показываем"

        monkeypatch.setenv("REQUIREMENT_OVERRIDE_READ_ENABLED", "true")
        on = await client.get(f"/api/v1/me/requirement?task_id={ids['task_root_a']}", headers=headers)
        assert on.json()["requirement_level"] == "recommended"
        assert on.json()["requirement_reason"]["text"] == "Так решил преподаватель"

        bad = await client.get("/api/v1/me/requirement", headers=headers)
        assert bad.status_code == 422
    finally:
        invalidate_grace_cache(db)
        for sql in (
            "DELETE FROM student_requirement_override WHERE student_id = :u",
            "DELETE FROM student_teacher_links WHERE student_id = :u",
            "DELETE FROM user_courses WHERE user_id = :u",
            "DELETE FROM user_session WHERE user_id = :u",
            "DELETE FROM identity_link WHERE user_id = :u",
            "DELETE FROM user_roles WHERE user_id = :u",
        ):
            await db.execute(text(sql), {"u": student_id})
        await db.commit()
