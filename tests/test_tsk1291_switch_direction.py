"""tsk-1291: смена направления ученика одним действием.

Мочалов (4518) летом учил «Python для ЕГЭ», осенью его перевели на
«Информатику 8–9» и ОГЭ, а старый курс остался активным и влиял на выдачу ДЗ.
Отчисление стирало запись целиком, выключения в кабинете не было вовсе, а
список курсов не показывал, какой курс выключен.

Проверяется:
- старые выключаются (не удаляются), новые зачисляются — одним вызовом;
- выключенный курс включается обратно той же ручкой;
- отказ по одному курсу (вложенный) не меняет НИЧЕГО;
- в списке курсов есть `is_active`;
- ученик и преподаватель ручку не зовут.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.test_tsk433_people_write_gates import _auth, _child_course, _root_course, _user

_PATH = "/api/v1/users/{}/courses/switch-direction"


async def _enrolled(db, student_id: int) -> dict[int, bool]:
    rows = await db.execute(
        text("SELECT course_id, is_active FROM user_courses WHERE user_id = :u"),
        {"u": student_id},
    )
    return dict(rows.all())


@pytest.mark.asyncio
async def test_switch_turns_old_off_and_new_on(db, client):
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    old = await _root_course(db)
    new_a, new_b = await _root_course(db), await _root_course(db)
    await client.post(
        f"/api/v1/users/{student_id}/courses/bulk",
        json={"course_ids": [old]}, headers=_auth(token),
    )

    resp = await client.post(
        _PATH.format(student_id),
        json={"enroll_course_ids": [new_a, new_b], "deactivate_course_ids": [old]},
        headers=_auth(token),
    )

    assert resp.status_code == 200, resp.text
    state = {c["course_id"]: c["is_active"] for c in resp.json()["courses"]}
    assert state == {old: False, new_a: True, new_b: True}
    db.expire_all()
    assert await _enrolled(db, student_id) == state, "старый курс удалили вместо выключения"


@pytest.mark.asyncio
async def test_switched_off_course_comes_back(db, client):
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    course = await _root_course(db)
    await client.post(
        f"/api/v1/users/{student_id}/courses/bulk",
        json={"course_ids": [course]}, headers=_auth(token),
    )
    await client.post(
        _PATH.format(student_id), json={"deactivate_course_ids": [course]},
        headers=_auth(token),
    )

    back = await client.post(
        _PATH.format(student_id), json={"enroll_course_ids": [course]},
        headers=_auth(token),
    )

    assert back.status_code == 200, back.text
    db.expire_all()
    assert await _enrolled(db, student_id) == {course: True}


@pytest.mark.asyncio
async def test_refusal_changes_nothing(db, client):
    """Новый курс вложенный — старый курс НЕ должен остаться выключенным."""
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    old = await _root_course(db)
    child = await _child_course(db, await _root_course(db))
    await client.post(
        f"/api/v1/users/{student_id}/courses/bulk",
        json={"course_ids": [old]}, headers=_auth(token),
    )

    resp = await client.post(
        _PATH.format(student_id),
        json={"enroll_course_ids": [child], "deactivate_course_ids": [old]},
        headers=_auth(token),
    )

    assert resp.status_code == 409, resp.text
    db.expire_all()
    assert await _enrolled(db, student_id) == {old: True}, "ученик остался без курсов"


@pytest.mark.asyncio
async def test_bad_requests(db, client):
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    course = await _root_course(db)

    empty = await client.post(_PATH.format(student_id), json={}, headers=_auth(token))
    assert empty.status_code == 400, empty.text
    both = await client.post(
        _PATH.format(student_id),
        json={"enroll_course_ids": [course], "deactivate_course_ids": [course]},
        headers=_auth(token),
    )
    assert both.status_code == 400, both.text
    not_enrolled = await client.post(
        _PATH.format(student_id), json={"deactivate_course_ids": [course]},
        headers=_auth(token),
    )
    assert not_enrolled.status_code == 404, not_enrolled.text


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["student", "teacher"])
async def test_only_methodist_or_admin(db, client, role):
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, role)
    course = await _root_course(db)

    resp = await client.post(
        _PATH.format(student_id), json={"enroll_course_ids": [course]},
        headers=_auth(token),
    )

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_course_list_shows_is_active(db, client):
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    course = await _root_course(db)
    await client.post(
        f"/api/v1/users/{student_id}/courses/bulk",
        json={"course_ids": [course]}, headers=_auth(token),
    )
    await db.execute(
        text("UPDATE user_courses SET is_active = false WHERE user_id = :u"),
        {"u": student_id},
    )
    await db.commit()

    listed = await client.get(
        f"/api/v1/users/{student_id}/courses?role=student", headers=_auth(token)
    )

    assert [c["is_active"] for c in listed.json()["courses"]] == [False]
