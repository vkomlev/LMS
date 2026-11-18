"""Initial flow карты прогресса tsk-1524: snapshot, скрытие и lifecycle."""
import json
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.schemas.checking import StudentAnswer
from app.schemas.exam_progress import ExamVariantPublish
from app.services import exam_progress_service as service
from app.services.auth.session_service import create_session
from app.utils.exceptions import DomainError


def _uid() -> str:
    return f"tsk1524-{random.randint(10**8, 10**10)}"


async def _fixture(db):
    suffix = _uid()
    user = Users(email=f"{suffix}@example.com", full_name="Тест tsk1524")
    db.add(user)
    await db.flush()
    course_id = (await db.execute(text("INSERT INTO courses(title,access_level,course_uid) VALUES(:title,'self_guided',:uid) RETURNING id"), {"title": suffix, "uid": suffix})).scalar_one()
    difficulty = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar_one()
    task_id = (await db.execute(text("INSERT INTO tasks(external_uid,max_score,task_content,solution_rules,course_id,difficulty_id,is_active) VALUES(:uid,1,CAST(:content AS JSONB),CAST(:rules AS JSONB),:course,:difficulty,true) RETURNING id"), {"uid": suffix, "content": json.dumps({"type":"SC","title":"2+2","stem":"2+2?","options":[{"id":"a","text":"3"},{"id":"b","text":"4"}]}), "rules": json.dumps({"max_score":1,"correct_options":["b"]}), "course": course_id, "difficulty": difficulty})).scalar_one()
    body = ExamVariantPublish.model_validate({"external_uid":suffix,"exam_kind":"ege","root_course_uid":suffix,"title":"Вариант","sequence":1,"published_version":1,"scale":{"version":"test","source_url":"https://example.com/scale","primary_max":1,"primary_to_result":{"0":0,"1":100}},"items":[{"position":1,"exam_number":1,"task_external_uid":suffix,"primary_score":1}]})
    published = await service.publish_variant(db, body)
    from app.models.user_courses import UserCourses
    root_course_id = (await db.execute(text("SELECT id FROM courses WHERE course_uid=:uid"), {"uid": suffix})).scalar_one()
    db.add(UserCourses(user_id=user.id, course_id=root_course_id, is_active=True))
    await db.flush()
    return user.id, task_id, published["variant_id"]


async def test_initial_hidden_snapshot_idempotency_and_finish(db):
    student, task_id, variant_id = await _fixture(db)
    run = await service.start_run(db, student_id=student, variant_id=variant_id)
    item_id = run["items"][0]["item_id"]
    assert run["items"][0]["initial_outcome"] is None
    assert "checker_snapshot" not in json.dumps(run, default=str)
    await db.execute(text("UPDATE tasks SET solution_rules=CAST(:rules AS JSONB) WHERE id=:id"), {"rules": json.dumps({"max_score":1,"correct_options":["a"]}), "id": task_id})
    answer = StudentAnswer.model_validate({"type":"SC","response":{"selected_option_ids":["b"]}})
    first = await service.answer_initial(db, run_id=run["run_id"], item_id=item_id, student_id=student, request_id="request-0001", answer=answer, phase="initial")
    retry = await service.answer_initial(db, run_id=run["run_id"], item_id=item_id, student_id=student, request_id="request-0001", answer=answer, phase="initial")
    assert first == retry
    assert (await db.execute(text("SELECT count(*) FROM task_results WHERE user_id=:id AND source_system='exam_initial'"), {"id": student})).scalar_one() == 0
    assert (await service.get_run(db, run_id=run["run_id"], student_id=student))["items"][0]["initial_outcome"] is None
    finished = await service.finish_initial(db, run_id=run["run_id"], student_id=student)
    assert finished["status"] == "review"
    assert finished["items"][0]["initial_outcome"] == "correct"
    assert (await db.execute(text("SELECT count(*) FROM task_results WHERE user_id=:id AND source_system='exam_initial'"), {"id": student})).scalar_one() == 1
    root = (await db.execute(text("SELECT root_course_id FROM exam_variant WHERE id=:id"), {"id": variant_id})).scalar_one()
    progress = await service.get_map(db, student_id=student, root_course_id=root, exam_kind="ege")
    assert progress["columns"][0]["primary_score"] == 1
    assert progress["columns"][0]["result_label"] == "100 баллов"
    assert progress["columns"][0]["cells"][0]["color"] == "green"


async def test_pause_resume_acl_phase_and_blank(db):
    student, _, variant_id = await _fixture(db)
    other = Users(email=f"{_uid()}@example.com", full_name="Другой ученик")
    db.add(other)
    await db.flush()
    run = await service.start_run(db, student_id=student, variant_id=variant_id)
    run_id = run["run_id"]
    paused = await service.transition(db, run_id=run_id, student_id=student, action="pause")
    assert paused["status"] == "paused"
    with pytest.raises(DomainError) as error:
        await service.transition(db, run_id=run_id, student_id=student, action="heartbeat")
    assert error.value.status_code == 409
    resumed = await service.transition(db, run_id=run_id, student_id=student, action="resume")
    assert resumed["status"] == "in_progress"
    with pytest.raises(DomainError) as error:
        await service.get_run(db, run_id=run_id, student_id=other.id)
    assert error.value.status_code == 404
    with pytest.raises(DomainError) as error:
        await service.answer_initial(db, run_id=run_id, item_id=run["items"][0]["item_id"], student_id=student, request_id="request-0002", answer=StudentAnswer.model_validate({"type":"SC","response":{"selected_option_ids":["a"]}}), phase="review")
    assert error.value.status_code == 409
    finished = await service.finish_initial(db, run_id=run_id, student_id=student)
    assert finished["items"][0]["initial_outcome"] == "blank"
    assert (await db.execute(text("SELECT count(*) FROM exam_run_session WHERE run_id=:id AND ended_at IS NULL"), {"id": run_id})).scalar_one() == 0


async def test_api_flag_and_initial_roundtrip(client, db, monkeypatch):
    from app.api.v1 import exam_progress as api

    monkeypatch.setattr(api.settings, "exam_progress_enabled", False)
    student, _, variant_id = await _fixture(db)
    token, _, _ = await create_session(db, user_id=student)
    await db.commit()
    headers = {"Authorization": f"Bearer {token}"}
    denied = await client.post("/api/v1/exam-progress/me/runs/start", headers=headers, json={"variant_id": variant_id})
    assert denied.status_code == 404
    monkeypatch.setattr(api.settings, "exam_progress_enabled", True)
    started = await client.post("/api/v1/exam-progress/me/runs/start", headers=headers, json={"variant_id": variant_id})
    assert started.status_code == 200, started.text
    payload = started.json()
    assert "checker_snapshot" not in started.text
    answered = await client.post(f"/api/v1/exam-progress/me/runs/{payload['run_id']}/items/{payload['items'][0]['item_id']}/answer", headers=headers, json={"phase":"initial","client_request_id":"request-api-1","answer":{"type":"SC","response":{"selected_option_ids":["a"]}}})
    assert answered.status_code == 200, answered.text
    assert "correct" not in answered.text
    finished = await client.post(f"/api/v1/exam-progress/me/runs/{payload['run_id']}/finish-initial", headers=headers)
    assert finished.status_code == 200, finished.text
    assert finished.json()["items"][0]["initial_outcome"] == "wrong"


async def test_stale_session_auto_pauses_and_can_resume(db):
    student, _, variant_id = await _fixture(db)
    run = await service.start_run(db, student_id=student, variant_id=variant_id)
    await db.execute(text("UPDATE exam_run_session SET last_heartbeat_at=now()-interval '3 minutes' WHERE run_id=:id"), {"id": run["run_id"]})
    with pytest.raises(DomainError) as error:
        await service.transition(db, run_id=run["run_id"], student_id=student, action="heartbeat")
    assert error.value.status_code == 409
    assert (await service.get_run(db, run_id=run["run_id"], student_id=student))["status"] == "paused"
    resumed = await service.transition(db, run_id=run["run_id"], student_id=student, action="resume")
    assert resumed["status"] == "in_progress"
    assert (await db.execute(text("SELECT count(*) FROM exam_run_session WHERE run_id=:id AND ended_at IS NULL"), {"id": run["run_id"]})).scalar_one() == 1
    answer = StudentAnswer.model_validate({"type": "SC", "response": {"selected_option_ids": ["b"]}})
    for operation in ("answer", "skip"):
        await db.execute(text("UPDATE exam_run_session SET last_heartbeat_at=now()-interval '3 minutes' WHERE run_id=:id"), {"id": run["run_id"]})
        with pytest.raises(DomainError) as error:
            if operation == "answer":
                await service.answer_initial(db, run_id=run["run_id"], item_id=run["items"][0]["item_id"], student_id=student, request_id="stale-answer", answer=answer, phase="initial")
            else:
                await service.skip_initial(db, run_id=run["run_id"], item_id=run["items"][0]["item_id"], student_id=student, request_id="stale-skip")
        assert error.value.status_code == 409
        assert (await service.get_run(db, run_id=run["run_id"], student_id=student))["status"] == "paused"
        await service.transition(db, run_id=run["run_id"], student_id=student, action="resume")
    await db.execute(text("UPDATE exam_run_session SET ended_at=now() WHERE run_id=:id AND ended_at IS NULL"), {"id": run["run_id"]})
    with pytest.raises(DomainError) as error:
        await service.answer_initial(db, run_id=run["run_id"], item_id=run["items"][0]["item_id"], student_id=student, request_id="closed-session", answer=answer, phase="initial")
    assert error.value.status_code == 409
    assert (await db.execute(text("SELECT initial_answer FROM exam_run_item WHERE id=:id"), {"id": run["items"][0]["item_id"]})).scalar_one() is None


async def test_review_reflection_yellow_and_immutable_score(db):
    student, _, variant_id = await _fixture(db)
    run = await service.start_run(db, student_id=student, variant_id=variant_id)
    item_id = run["items"][0]["item_id"]
    wrong = StudentAnswer.model_validate({"type":"SC","response":{"selected_option_ids":["a"]}})
    right = StudentAnswer.model_validate({"type":"SC","response":{"selected_option_ids":["b"]}})
    await service.answer_initial(db, run_id=run["run_id"], item_id=item_id, student_id=student, request_id="initial-yellow", answer=wrong, phase="initial")
    await service.finish_initial(db, run_id=run["run_id"], student_id=student)
    initial_result = (await db.execute(text("SELECT initial_result_id FROM exam_run_item WHERE id=:id"), {"id": item_id})).scalar_one()
    with pytest.raises(DomainError):
        await service.finish_review(db, run_id=run["run_id"], student_id=student)
    missed = await service.answer_review(db, run_id=run["run_id"], item_id=item_id, student_id=student, request_id="review-miss-1", answer=wrong, phase="review", reflection_reason=None, reflection_comment=None)
    assert missed["correct"] is False
    with pytest.raises(DomainError) as error:
        await service.answer_review(db, run_id=run["run_id"], item_id=item_id, student_id=student, request_id="review-no-reflection", answer=right, phase="review", reflection_reason=None, reflection_comment=None)
    assert error.value.status_code == 422
    solved = await service.answer_review(db, run_id=run["run_id"], item_id=item_id, student_id=student, request_id="review-solved-1", answer=right, phase="review", reflection_reason="calculation_error", reflection_comment="Посчитал заново")
    assert solved["resolution"] == "rno"
    current = await service.get_run(db, run_id=run["run_id"], student_id=student)
    assert current["items"][0]["color"] == "yellow"
    assert (await db.execute(text("SELECT initial_result_id,initial_outcome FROM exam_run_item WHERE id=:id"), {"id": item_id})).mappings().one()["initial_result_id"] == initial_result
    await service.finish_review(db, run_id=run["run_id"], student_id=student)
    root = (await db.execute(text("SELECT root_course_id FROM exam_variant WHERE id=:id"), {"id": variant_id})).scalar_one()
    progress = await service.get_map(db, student_id=student, root_course_id=root, exam_kind="ege")
    assert progress["columns"][0]["primary_score"] == 0
    assert progress["columns"][0]["cells"][0]["color"] == "yellow"


async def test_late_red_to_orange_keeps_initial_zero(db):
    student, _, variant_id = await _fixture(db)
    run = await service.start_run(db, student_id=student, variant_id=variant_id)
    item_id = run["items"][0]["item_id"]
    await service.finish_initial(db, run_id=run["run_id"], student_id=student)
    wrong = StudentAnswer.model_validate({"type":"SC","response":{"selected_option_ids":["a"]}})
    await service.answer_review(db, run_id=run["run_id"], item_id=item_id, student_id=student, request_id="review-blank-attempt", answer=wrong, phase="review", reflection_reason=None, reflection_comment=None)
    await service.finish_review(db, run_id=run["run_id"], student_id=student)
    right = StudentAnswer.model_validate({"type":"SC","response":{"selected_option_ids":["b"]}})
    solved = await service.answer_review(db, run_id=run["run_id"], item_id=item_id, student_id=student, request_id="late-solved-1", answer=right, phase="late", reflection_reason="missing_rule", reflection_comment=None)
    assert solved["resolution"] == "late"
    current = await service.get_run(db, run_id=run["run_id"], student_id=student)
    assert current["items"][0]["color"] == "orange"
    root = (await db.execute(text("SELECT root_course_id FROM exam_variant WHERE id=:id"), {"id": variant_id})).scalar_one()
    progress = await service.get_map(db, student_id=student, root_course_id=root, exam_kind="ege")
    assert progress["columns"][0]["primary_score"] == 0
    assert progress["columns"][0]["cells"][0]["color"] == "orange"


async def test_skip_initial_becomes_blank_and_requires_review(db):
    student, _, variant_id = await _fixture(db)
    run = await service.start_run(db, student_id=student, variant_id=variant_id)
    item_id = run["items"][0]["item_id"]
    skipped = await service.skip_initial(db, run_id=run["run_id"], item_id=item_id, student_id=student, request_id="skip-request-1")
    retry = await service.skip_initial(db, run_id=run["run_id"], item_id=item_id, student_id=student, request_id="skip-request-1")
    assert skipped == retry
    current = await service.get_run(db, run_id=run["run_id"], student_id=student)
    assert current["items"][0]["is_skipped"] is True
    assert current["items"][0]["available_phase"] is None
    assert current["items"][0]["initial_outcome"] is None
    finished = await service.finish_initial(db, run_id=run["run_id"], student_id=student)
    assert finished["items"][0]["initial_outcome"] == "blank"
    assert finished["items"][0]["available_phase"] == "review"
    assert (await db.execute(text("SELECT count(*) FROM task_results WHERE user_id=:id AND source_system='exam_initial'"), {"id": student})).scalar_one() == 0


async def test_skip_after_finish_initial_is_rejected(db):
    student, _, variant_id = await _fixture(db)
    run = await service.start_run(db, student_id=student, variant_id=variant_id)
    item_id = run["items"][0]["item_id"]
    await service.finish_initial(db, run_id=run["run_id"], student_id=student)
    with pytest.raises(DomainError) as error:
        await service.skip_initial(db, run_id=run["run_id"], item_id=item_id, student_id=student, request_id="skip-after-finish")
    assert error.value.status_code == 409


async def test_teacher_queue_acl_completed_red_and_live_video(db):
    student, task_id, variant_id = await _fixture(db)
    run = await service.start_run(db, student_id=student, variant_id=variant_id)
    item_id = run["items"][0]["item_id"]
    await service.finish_initial(db, run_id=run["run_id"], student_id=student)
    wrong = StudentAnswer.model_validate({"type":"SC","response":{"selected_option_ids":["a"]}})
    await service.answer_review(db, run_id=run["run_id"], item_id=item_id, student_id=student, request_id="queue-review-attempt", answer=wrong, phase="review", reflection_reason=None, reflection_comment=None)
    assert await service.teacher_review_queue(db, user_id=999999, elevated=False) == []
    await service.finish_review(db, run_id=run["run_id"], student_id=student)
    teacher = Users(email=f"{_uid()}@example.com", full_name="Преподаватель")
    db.add(teacher)
    await db.flush()
    assert await service.teacher_review_queue(db, user_id=teacher.id, elevated=False) == []
    await db.execute(text("INSERT INTO student_teacher_links(student_id,teacher_id) VALUES(:student,:teacher)"), {"student": student, "teacher": teacher.id})
    queue = await service.teacher_review_queue(db, user_id=teacher.id, elevated=False)
    assert queue[0]["student_count"] == 1
    assert queue[0]["students"][0]["student_id"] == student
    school = await service.teacher_review_queue(db, user_id=999999, elevated=True)
    assert any(row["task_id"] == task_id for row in school)
    await db.execute(text("UPDATE tasks SET task_content=jsonb_set(task_content,'{hints_video}',CAST(:video AS JSONB)) WHERE id=:id"), {"video": json.dumps(["https://example.com/review"]), "id": task_id})
    assert await service.teacher_review_queue(db, user_id=teacher.id, elevated=False) == []


def test_group_score_composite_all_or_nothing_and_partial_single():
    composite = [
        {"primary_score": 1, "initial_score": 1, "initial_outcome": "correct"},
        {"primary_score": 1, "initial_score": 1, "initial_outcome": "correct"},
        {"primary_score": 1, "initial_score": 1, "initial_outcome": "correct"},
    ]
    assert service.initial_group_score(composite) == 1
    composite[1]["initial_outcome"] = "wrong"
    composite[1]["initial_score"] = 0
    assert service.initial_group_score(composite) == 0
    assert service.initial_group_score([{"primary_score": 2, "initial_score": 1, "initial_outcome": "wrong"}]) == 1


async def test_republish_same_version_requires_identical_payload(db):
    from app.utils.exceptions import DomainError

    student, task_id, variant_id = await _fixture(db)
    row = (await db.execute(text("SELECT v.*,c.course_uid FROM exam_variant v JOIN courses c ON c.id=v.root_course_id WHERE v.id=:id"), {"id": variant_id})).mappings().one()
    task_uid = (await db.execute(text("SELECT external_uid FROM tasks WHERE id=:id"), {"id": task_id})).scalar_one()
    body = ExamVariantPublish.model_validate({
        "external_uid": row["external_uid"], "exam_kind": row["exam_kind"], "root_course_uid": row["course_uid"],
        "title": row["title"], "sequence": row["sequence"], "published_version": row["published_version"],
        "scale": row["scoring_scale"],
        "items": [{"position": 1, "exam_number": 1, "task_external_uid": task_uid, "primary_score": 1}],
    })
    repeated = await service.publish_variant(db, body)
    assert repeated["created"] is False
    changed = body.model_copy(update={"title": "Изменённый заголовок"})
    with pytest.raises(DomainError) as error:
        await service.publish_variant(db, changed)
    assert error.value.status_code == 409


async def test_existing_run_and_map_recheck_course_access_after_revoke(db):
    from fastapi import HTTPException

    student, _, variant_id = await _fixture(db)
    run = await service.start_run(db, student_id=student, variant_id=variant_id)
    await db.execute(text("DELETE FROM user_courses WHERE user_id=:id"), {"id": student})
    with pytest.raises(HTTPException) as run_denied:
        await service.get_run(db, run_id=run["run_id"], student_id=student)
    assert run_denied.value.status_code == 403
    with pytest.raises(HTTPException) as start_denied:
        await service.start_run(db, student_id=student, variant_id=variant_id)
    assert start_denied.value.status_code == 403
    with pytest.raises(HTTPException) as map_denied:
        await service.get_map(db, student_id=student, root_course_id=112, exam_kind="ege")
    assert map_denied.value.status_code == 403


def test_partial_26_27_scores_zero_one_two_from_checking_service():
    from app.schemas.checking import StudentAnswer
    from app.schemas.task_content import TaskContent
    from app.schemas.solution_rules import SolutionRules
    from app.services.checking_service import CheckingService

    content = TaskContent.model_validate({"type": "TBL_COM", "stem": "Два ответа", "table": {"columns": 1}})
    rules = SolutionRules.model_validate({
        "max_score": 2, "scoring_mode": "partial", "auto_check": True, "manual_review_required": False,
        "short_answer": {"normalization": ["trim", "lower"], "accepted_answers": [{"value": "7040\n52204", "score": 2}]},
    })
    checking = CheckingService()
    for value, expected_score in (("0", 0), ("7040", 1), ("7040\n52204", 2)):
        answer = StudentAnswer.model_validate({"type": "TBL_COM", "response": {"value": value, "comment": "Решение"}})
        result = checking._check_table_answer(content, rules, answer)
        assert result.score == expected_score


async def test_catalog_keeps_previous_run_block_across_unstarted_variants(db):
    student, task_id, variant_id = await _fixture(db)
    row = (await db.execute(text("SELECT v.*,c.course_uid FROM exam_variant v JOIN courses c ON c.id=v.root_course_id WHERE v.id=:id"), {"id": variant_id})).mappings().one()
    task_uid = (await db.execute(text("SELECT external_uid FROM tasks WHERE id=:id"), {"id": task_id})).scalar_one()
    for sequence in (2, 4):
        body = ExamVariantPublish.model_validate({
            "external_uid": f"{row['external_uid']}-{sequence}", "exam_kind": row["exam_kind"], "root_course_uid": row["course_uid"],
            "title": row["title"], "sequence": sequence, "published_version": row["published_version"],
            "scale": row["scoring_scale"], "items": [{"position": 1, "exam_number": 1, "task_external_uid": task_uid, "primary_score": 1}],
        })
        await service.publish_variant(db, body)
    await service.start_run(db, student_id=student, variant_id=variant_id)
    catalog = await service.list_variants(db, student_id=student, root_course_id=row["root_course_id"], exam_kind="ege")
    assert [item["can_start"] for item in catalog] == [True, False, False]
    first_run_id = catalog[0]["run_id"]
    await db.execute(text("UPDATE exam_run SET status='completed' WHERE id=:id"), {"id": first_run_id})
    second_run = await service.start_run(db, student_id=student, variant_id=catalog[1]["variant_id"])
    await db.execute(text("UPDATE exam_run SET status='review' WHERE id=:first"), {"first": first_run_id})
    await db.execute(text("UPDATE exam_run SET status='completed' WHERE id=:id"), {"id": second_run["run_id"]})
    with pytest.raises(DomainError) as error:
        await service.start_run(db, student_id=student, variant_id=catalog[2]["variant_id"])
    assert error.value.payload["code"] == "exam_previous_review_required"


def test_skip_request_id_has_same_length_contract_as_answer():
    from pydantic import ValidationError
    from app.api.v1.exam_progress import SkipItemRequest

    SkipItemRequest(client_request_id="12345678")
    with pytest.raises(ValidationError):
        SkipItemRequest(client_request_id="short")
    with pytest.raises(ValidationError):
        SkipItemRequest(client_request_id="x" * 101)


async def test_answer_and_skip_are_rejected_while_paused(db):
    student, _, variant_id = await _fixture(db)
    run = await service.start_run(db, student_id=student, variant_id=variant_id)
    item_id = run["items"][0]["item_id"]
    await service.transition(db, run_id=run["run_id"], student_id=student, action="pause")
    answer = StudentAnswer.model_validate({"type": "SC", "response": {"selected_option_ids": ["b"]}})
    with pytest.raises(DomainError) as answer_error:
        await service.answer_initial(db, run_id=run["run_id"], item_id=item_id, student_id=student, request_id="paused-answer", answer=answer, phase="initial")
    assert answer_error.value.status_code == 409
    with pytest.raises(DomainError) as skip_error:
        await service.skip_initial(db, run_id=run["run_id"], item_id=item_id, student_id=student, request_id="paused-skip")
    assert skip_error.value.status_code == 409
