"""
Тесты фундамента триггеров назначения курсов (tsk-031).

Покрывают:
- идемпотентное ядро assign_course_to_student (по course_id и course_uid);
- движок evaluate_rules_for_answer (answer_value / task_failed);
- движок evaluate_rules_for_attempt (course_failed);
- ручной эндпоинт учителя (idempotent, teacher-only через сервисный токен).

Тесты создают свои данные в dev-БД (Learn.public) и подчищают за собой
каскадом от курса/ученика. Пользователи с email '@example.*' дополнительно
удаляются session-scoped sweep'ом из conftest.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.schemas.checking import CheckResult, StudentAnswer, StudentResponse
from app.services import assignment_rules_service as ars

pytestmark = pytest.mark.asyncio

_settings = Settings()


async def _make_student(db) -> int:
    email = f"stud_{uuid.uuid4().hex[:8]}@example.com"
    r = await db.execute(
        text("INSERT INTO users (email, full_name) VALUES (:e, 'tsk031 student') RETURNING id"),
        {"e": email},
    )
    sid = int(r.scalar())
    await db.commit()
    return sid


async def _make_course(db, uid: str | None) -> int:
    r = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, course_uid) "
            "VALUES (:t, 'auto_check', :uid) RETURNING id"
        ),
        {"t": f"tsk031 {uid or 'nouid'}", "uid": uid},
    )
    cid = int(r.scalar())
    await db.commit()
    return cid


async def _make_task(db, course_id: int) -> int:
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    r = await db.execute(
        text(
            "INSERT INTO tasks (course_id, difficulty_id, task_content) "
            "VALUES (:cid, :did, CAST(:tc AS jsonb)) RETURNING id"
        ),
        {"cid": course_id, "did": diff, "tc": '{"type": "SC", "stem": "t"}'},
    )
    tid = int(r.scalar())
    await db.commit()
    return tid


async def _cleanup(db, *, course_ids: list[int], student_id: int) -> None:
    for cid in course_ids:
        await db.execute(text("DELETE FROM courses WHERE id = :cid"), {"cid": cid})
    await db.execute(text("DELETE FROM users WHERE id = :sid"), {"sid": student_id})
    await db.commit()


async def test_assign_idempotent_by_id(db):
    """Назначение по course_id: первый раз enroll, второй — already_enrolled, без дубля."""
    student_id = await _make_student(db)
    course_id = await _make_course(db, uid=None)
    try:
        r1 = await ars.assign_course_to_student(
            db, student_id=student_id, course_id=course_id, source="manual_teacher",
        )
        assert r1.already_enrolled is False
        assert r1.course_id == course_id

        r2 = await ars.assign_course_to_student(
            db, student_id=student_id, course_id=course_id, source="manual_teacher",
        )
        assert r2.already_enrolled is True

        cnt = (
            await db.execute(
                text("SELECT COUNT(*) FROM user_courses WHERE user_id = :u AND course_id = :c"),
                {"u": student_id, "c": course_id},
            )
        ).scalar()
        assert cnt == 1
    finally:
        await _cleanup(db, course_ids=[course_id], student_id=student_id)


async def test_assign_by_course_uid(db):
    """Назначение по course_uid резолвится в id и зачисляет ученика."""
    student_id = await _make_student(db)
    uid = f"wp:tsk031-{uuid.uuid4().hex[:8]}"
    course_id = await _make_course(db, uid=uid)
    try:
        res = await ars.assign_course_to_student(
            db, student_id=student_id, course_uid=uid, source="manual_teacher",
        )
        assert res.course_id == course_id
        assert res.already_enrolled is False
        assert res.event_id is not None
    finally:
        await _cleanup(db, course_ids=[course_id], student_id=student_id)


async def test_assign_unknown_uid_raises(db):
    """Неизвестный course_uid → DomainError 404."""
    from app.utils.exceptions import DomainError

    student_id = await _make_student(db)
    try:
        with pytest.raises(DomainError):
            await ars.assign_course_to_student(
                db, student_id=student_id, course_uid="wp:does-not-exist-xyz",
                source="manual_teacher",
            )
    finally:
        await _cleanup(db, course_ids=[], student_id=student_id)


async def test_rule_answer_value_assigns_track_course(db):
    """answer_value: выбор варианта 'py' назначает целевой курс (сценарий пробного)."""
    student_id = await _make_student(db)
    trigger_course = await _make_course(db, uid=None)  # курс пробного
    target_uid = f"wp:vvodnyy-python-{uuid.uuid4().hex[:8]}"
    target_course = await _make_course(db, uid=target_uid)
    task_id = await _make_task(db, trigger_course)
    rule_code = f"trial-py-{uuid.uuid4().hex[:8]}"
    await db.execute(
        text(
            "INSERT INTO assignment_rule (code, trigger_event, task_id, condition, target_course_uid) "
            "VALUES (:code, 'answer_value', :tid, CAST(:cond AS jsonb), :uid)"
        ),
        {"code": rule_code, "tid": task_id, "cond": '{"option_id": "py"}', "uid": target_uid},
    )
    await db.commit()
    try:
        answer = StudentAnswer(type="SC", response=StudentResponse(selected_option_ids=["py"]))
        check = CheckResult(score=10, max_score=10, is_correct=True)
        fired = await ars.evaluate_rules_for_answer(
            db, student_id=student_id, task_id=task_id, answer=answer, check_result=check,
        )
        assert fired == 1
        enrolled = (
            await db.execute(
                text("SELECT 1 FROM user_courses WHERE user_id = :u AND course_id = :c"),
                {"u": student_id, "c": target_course},
            )
        ).fetchone()
        assert enrolled is not None

        # Повторный ответ не назначает второй раз (once_per_student).
        fired2 = await ars.evaluate_rules_for_answer(
            db, student_id=student_id, task_id=task_id, answer=answer, check_result=check,
        )
        assert fired2 == 0
    finally:
        await _cleanup(db, course_ids=[trigger_course, target_course], student_id=student_id)


async def test_rule_answer_value_no_match(db):
    """answer_value: другой вариант ответа не назначает курс."""
    student_id = await _make_student(db)
    trigger_course = await _make_course(db, uid=None)
    target_uid = f"wp:vvodnyy-inf-{uuid.uuid4().hex[:8]}"
    target_course = await _make_course(db, uid=target_uid)
    task_id = await _make_task(db, trigger_course)
    await db.execute(
        text(
            "INSERT INTO assignment_rule (code, trigger_event, task_id, condition, target_course_uid) "
            "VALUES (:code, 'answer_value', :tid, CAST(:cond AS jsonb), :uid)"
        ),
        {
            "code": f"trial-inf-{uuid.uuid4().hex[:8]}",
            "tid": task_id, "cond": '{"option_id": "inf"}', "uid": target_uid,
        },
    )
    await db.commit()
    try:
        answer = StudentAnswer(type="SC", response=StudentResponse(selected_option_ids=["py"]))
        check = CheckResult(score=10, max_score=10, is_correct=True)
        fired = await ars.evaluate_rules_for_answer(
            db, student_id=student_id, task_id=task_id, answer=answer, check_result=check,
        )
        assert fired == 0
    finally:
        await _cleanup(db, course_ids=[trigger_course, target_course], student_id=student_id)


async def test_manual_assign_endpoint_idempotent(client, db):
    """Ручной эндпоинт учителя: сервисный токен назначает курс, повтор — already_enrolled."""
    api_key = next(iter(_settings.valid_api_keys))
    student_id = await _make_student(db)
    course_id = await _make_course(db, uid=None)
    headers = {"X-API-Key": api_key}
    try:
        url = f"/api/v1/teacher/students/{student_id}/assignments"
        resp1 = await client.post(url, json={"course_id": course_id}, headers=headers)
        assert resp1.status_code == 200, resp1.text
        body1 = resp1.json()
        assert body1["already_enrolled"] is False
        assert body1["course_id"] == course_id

        resp2 = await client.post(url, json={"course_id": course_id}, headers=headers)
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["already_enrolled"] is True
    finally:
        await _cleanup(db, course_ids=[course_id], student_id=student_id)


async def test_manual_assign_validation_both_refs(client, db):
    """Эндпоинт: нельзя указать одновременно course_id и course_uid (422)."""
    api_key = next(iter(_settings.valid_api_keys))
    student_id = await _make_student(db)
    try:
        resp = await client.post(
            f"/api/v1/teacher/students/{student_id}/assignments",
            json={"course_id": 1, "course_uid": "wp:x"},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 422
    finally:
        await _cleanup(db, course_ids=[], student_id=student_id)


# --- ACL ручного назначения (S3 fix) ---


async def _grant_role(db, user_id: int, role_name: str) -> None:
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, id FROM roles WHERE name = :n "
            "ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "n": role_name},
    )
    await db.commit()


async def _link_student_teacher(db, student_id: int, teacher_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO student_teacher_links (student_id, teacher_id) "
            "VALUES (:s, :t) ON CONFLICT DO NOTHING"
        ),
        {"s": student_id, "t": teacher_id},
    )
    await db.commit()


async def test_acl_teacher_unlinked_denied(db):
    """Преподаватель не может назначить курс чужому (несвязанному) ученику → 403."""
    from fastapi import HTTPException
    from app.api.v1.teacher_assignments import _ensure_can_assign

    teacher_id = await _make_student(db)
    student_id = await _make_student(db)
    await _grant_role(db, teacher_id, "teacher")
    cu = CurrentUser(id=teacher_id, is_service=False)
    try:
        with pytest.raises(HTTPException) as exc:
            await _ensure_can_assign(db, cu, student_id)
        assert exc.value.status_code == 403
    finally:
        await _cleanup(db, course_ids=[], student_id=teacher_id)
        await _cleanup(db, course_ids=[], student_id=student_id)


async def test_acl_teacher_linked_allowed(db):
    """Преподаватель может назначить курс своему (связанному) ученику."""
    from app.api.v1.teacher_assignments import _ensure_can_assign

    teacher_id = await _make_student(db)
    student_id = await _make_student(db)
    await _grant_role(db, teacher_id, "teacher")
    await _link_student_teacher(db, student_id, teacher_id)
    cu = CurrentUser(id=teacher_id, is_service=False)
    try:
        await _ensure_can_assign(db, cu, student_id)  # не должно бросить
    finally:
        await _cleanup(db, course_ids=[], student_id=teacher_id)
        await _cleanup(db, course_ids=[], student_id=student_id)


async def test_acl_methodist_bypass(db):
    """Методист имеет полный доступ — связь с учеником не требуется."""
    from app.api.v1.teacher_assignments import _ensure_can_assign

    methodist_id = await _make_student(db)
    student_id = await _make_student(db)
    await _grant_role(db, methodist_id, "methodist")
    cu = CurrentUser(id=methodist_id, is_service=False)
    try:
        await _ensure_can_assign(db, cu, student_id)  # не должно бросить
    finally:
        await _cleanup(db, course_ids=[], student_id=methodist_id)
        await _cleanup(db, course_ids=[], student_id=student_id)


async def test_acl_service_bypass(db):
    """Сервисный токен имеет полный доступ."""
    from app.api.v1.teacher_assignments import _ensure_can_assign

    student_id = await _make_student(db)
    cu = CurrentUser(id=0, is_service=True)
    try:
        await _ensure_can_assign(db, cu, student_id)  # не должно бросить
    finally:
        await _cleanup(db, course_ids=[], student_id=student_id)


async def test_acl_no_role_denied(db):
    """Пользователь без подходящей роли не может назначать курсы → 403."""
    from fastapi import HTTPException
    from app.api.v1.teacher_assignments import _ensure_can_assign

    user_id = await _make_student(db)
    student_id = await _make_student(db)
    cu = CurrentUser(id=user_id, is_service=False)
    try:
        with pytest.raises(HTTPException) as exc:
            await _ensure_can_assign(db, cu, student_id)
        assert exc.value.status_code == 403
    finally:
        await _cleanup(db, course_ids=[], student_id=user_id)
        await _cleanup(db, course_ids=[], student_id=student_id)


async def test_bulk_upsert_rules_idempotent(db):
    """Upsert правил по code: первый раз created, повтор — updated, без дубля (tsk-120)."""
    from app.schemas.assignment_rules import AssignmentRuleUpsertItem

    course_id = await _make_course(db, uid=None)
    task_id = await _make_task(db, course_id)
    code = f"test-rule-{uuid.uuid4().hex[:8]}"
    item = AssignmentRuleUpsertItem(
        code=code, trigger_event="answer_value", task_id=task_id,
        condition={"option_id": "a"}, target_course_uid="wp:vvodnyy-python",
    )
    try:
        r1 = await ars.bulk_upsert_rules(db, [item])
        assert r1[0]["action"] == "created"
        rid = r1[0]["id"]
        assert isinstance(rid, int)

        r2 = await ars.bulk_upsert_rules(db, [item])
        assert r2[0]["action"] == "updated"
        assert r2[0]["id"] == rid

        cnt = (await db.execute(
            text("SELECT COUNT(*) FROM assignment_rule WHERE code = :c"), {"c": code}
        )).scalar()
        assert cnt == 1

        row = (await db.execute(
            text("SELECT trigger_event, task_id, target_course_uid, condition "
                 "FROM assignment_rule WHERE code = :c"), {"c": code}
        )).first()
        assert row[0] == "answer_value"
        assert row[1] == task_id
        assert row[2] == "wp:vvodnyy-python"
        assert row[3] == {"option_id": "a"}
    finally:
        await db.execute(text("DELETE FROM assignment_rule WHERE code = :c"), {"c": code})
        await db.execute(text("DELETE FROM courses WHERE id = :cid"), {"cid": course_id})
        await db.commit()


async def test_bulk_upsert_rules_unknown_task_uid_soft_error(db):
    """Неизвестный task_external_uid → action=error по элементу, не падение всего batch."""
    from app.schemas.assignment_rules import AssignmentRuleUpsertItem

    item = AssignmentRuleUpsertItem(
        code=f"test-rule-{uuid.uuid4().hex[:8]}", trigger_event="answer_value",
        task_external_uid="нет-такой-задачи", condition={"option_id": "a"},
        target_course_uid="wp:x",
    )
    res = await ars.bulk_upsert_rules(db, [item])
    assert res[0]["action"] == "error"
    assert "не найдена" in (res[0]["error"] or "")
