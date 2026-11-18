"""Версионированные экзаменационные варианты и initial flow (tsk-1524)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.checking import CheckResult, StudentAnswer
from app.schemas.exam_progress import ExamVariantPublish
from app.schemas.task_content import TaskContent
from app.auth.current_user import CurrentUser
from app.services import retrieval_service
from app.services.courses_acl_service import assert_course_access
from app.services.checking_service import CheckingService
from app.utils.exceptions import DomainError

HEARTBEAT_CAP_SECONDS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _request_fingerprint(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _without_fingerprint(response: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in response.items() if key != "_request_fingerprint"}


def _error(message: str, status: int, code: str) -> DomainError:
    return DomainError(message, status_code=status, payload={"code": code})


async def publish_variant(db: AsyncSession, body: ExamVariantPublish) -> dict[str, Any]:
    """Идемпотентно опубликовать неизменяемую версию варианта."""
    course_id = (await db.execute(text("SELECT id FROM courses WHERE course_uid=:uid"), {"uid": body.root_course_uid})).scalar_one_or_none()
    if course_id is None:
        raise _error("Корневой курс не найден.", 404, "exam_root_not_found")
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:uid))"), {"uid": body.external_uid})
    existing = (await db.execute(text("SELECT id,status,published_version,exam_kind,root_course_id,title,sequence,scoring_scale FROM exam_variant WHERE external_uid=:uid FOR UPDATE"), {"uid": body.external_uid})).mappings().first()
    if existing:
        if existing["status"] != "published":
            raise _error("Нельзя повторно опубликовать неактивный вариант.", 409, "exam_variant_immutable")
        if int(existing["published_version"]) == body.published_version:
            stored_items = (await db.execute(text("SELECT i.position,i.exam_number,i.primary_score,i.task_id,t.external_uid FROM exam_variant_item i JOIN tasks t ON t.id=i.task_id WHERE i.variant_id=:id ORDER BY i.position"), {"id": existing["id"]})).mappings().all()
            expected_items = [{"position": item.position, "exam_number": item.exam_number, "primary_score": item.primary_score, "external_uid": item.task_external_uid} for item in body.items]
            actual_items = [{"position": int(item["position"]), "exam_number": int(item["exam_number"]), "primary_score": int(item["primary_score"]), "external_uid": item["external_uid"]} for item in stored_items]
            current_task_ids = {item["position"]: int(item["task_id"]) for item in stored_items}
            for item in body.items:
                current_task_ids_result = (await db.execute(text("WITH RECURSIVE descendants(id) AS (SELECT CAST(:root AS integer) UNION SELECT cp.course_id FROM course_parents cp JOIN descendants d ON cp.parent_course_id=d.id) SELECT t.id FROM tasks t JOIN descendants d ON d.id=t.course_id WHERE t.external_uid=:uid AND t.is_active=true"), {"uid": item.task_external_uid, "root": course_id})).scalars().all()
                if len(current_task_ids_result) != 1 or int(current_task_ids_result[0]) != current_task_ids.get(item.position):
                    raise _error("Task ID в повторной публикации отличается от опубликованной версии.", 409, "exam_variant_immutable")
            if (existing["exam_kind"] != body.exam_kind or int(existing["root_course_id"]) != course_id
                    or existing["title"] != body.title or int(existing["sequence"]) != body.sequence
                    or existing["scoring_scale"] != body.scale.model_dump() or actual_items != expected_items):
                raise _error("Повтор публикации отличается содержимым; используйте новую версию.", 409, "exam_variant_immutable")
            return {"variant_id": int(existing["id"]), "published_version": int(existing["published_version"]), "created": False}
        raise _error("Опубликованная версия неизменяема; используйте новый external_uid.", 409, "exam_variant_immutable")
    checking = CheckingService()
    resolved: list[dict[str, Any]] = []
    for item in body.items:
        tasks = (await db.execute(text("WITH RECURSIVE descendants(id) AS (SELECT CAST(:root AS integer) UNION SELECT cp.course_id FROM course_parents cp JOIN descendants d ON cp.parent_course_id=d.id) SELECT t.id,t.task_content,t.solution_rules,t.max_score FROM tasks t JOIN descendants d ON d.id=t.course_id WHERE t.external_uid=:uid AND t.is_active=true"), {"uid": item.task_external_uid, "root": course_id})).mappings().all()
        if len(tasks) != 1:
            raise _error(f"Задание {item.task_external_uid} не найдено или не уникально в дереве курса.", 422, "exam_task_not_found")
        task = tasks[0]
        content = TaskContent.model_validate(task["task_content"])
        rules = checking.build_solution_rules(task["solution_rules"], task["max_score"])
        if not retrieval_service.task_supports_review(content, rules):
            raise _error("Вариант допускает только полную автопроверку.", 422, "exam_task_not_auto_checkable")
        expected_score = 2 if item.exam_number in (26, 27) else 1
        if item.primary_score != expected_score or int(task["max_score"]) != expected_score:
            raise _error("Балл задания не соответствует весу номера КИМ.", 422, "exam_task_score_mismatch")
        resolved.append({"position": item.position, "exam_number": item.exam_number, "task_id": int(task["id"]), "primary_score": item.primary_score})
    variant_id = int((await db.execute(text("INSERT INTO exam_variant(external_uid,exam_kind,root_course_id,title,sequence,published_version,scoring_scale) VALUES(:uid,:kind,:root,:title,:sequence,:version,CAST(:scale AS JSONB)) RETURNING id"), {"uid": body.external_uid, "kind": body.exam_kind, "root": course_id, "title": body.title, "sequence": body.sequence, "version": body.published_version, "scale": _json(body.scale.model_dump())})).scalar_one())
    for item_data in resolved:
        await db.execute(text("INSERT INTO exam_variant_item(variant_id,position,exam_number,task_id,primary_score) VALUES(:variant,:position,:number,:task,:score)"), {"variant": variant_id, "position": item_data["position"], "number": item_data["exam_number"], "task": item_data["task_id"], "score": item_data["primary_score"]})
    await db.commit()
    return {"variant_id": variant_id, "published_version": body.published_version, "created": True}


async def _run(db: AsyncSession, run_id: int, student_id: int, *, lock: bool = False) -> dict[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (await db.execute(text(f"SELECT r.*,v.root_course_id FROM exam_run r JOIN exam_variant v ON v.id=r.variant_id WHERE r.id=:id AND r.student_id=:student{suffix}"), {"id": run_id, "student": student_id})).mappings().first()
    if row is None:
        raise _error("Прохождение не найдено.", 404, "exam_run_not_found")
    await assert_course_access(db, current_user=CurrentUser(id=student_id), course_id=int(row["root_course_id"]))
    return row


async def _close_session(db: AsyncSession, run_id: int, reason: str, now: datetime) -> None:
    session = (await db.execute(text("SELECT * FROM exam_run_session WHERE run_id=:id AND ended_at IS NULL FOR UPDATE"), {"id": run_id})).mappings().first()
    if session is None:
        return
    last = session["last_heartbeat_at"]
    elapsed = max(0, min(int((now - last).total_seconds()), HEARTBEAT_CAP_SECONDS))
    seconds = int(session["seconds"]) + elapsed
    await db.execute(text("UPDATE exam_run_session SET ended_at=:now,last_heartbeat_at=:now,end_reason=:reason,seconds=:seconds WHERE id=:id"), {"now": now, "reason": reason, "seconds": seconds, "id": session["id"]})
    await db.execute(text("UPDATE exam_run SET active_seconds=active_seconds+:seconds,updated_at=:now WHERE id=:id"), {"seconds": elapsed, "now": now, "id": run_id})


async def _settle_stale(db: AsyncSession, run: dict[str, Any]) -> bool:
    now = _now()
    session = (await db.execute(text("SELECT last_heartbeat_at FROM exam_run_session WHERE run_id=:id AND ended_at IS NULL"), {"id": run["id"]})).mappings().first()
    if session and (now - session["last_heartbeat_at"]).total_seconds() > HEARTBEAT_CAP_SECONDS:
        await _close_session(db, int(run["id"]), "stale", now)
        await db.execute(text("UPDATE exam_run SET status='paused',updated_at=:now WHERE id=:id AND status='in_progress'"), {"now": now, "id": run["id"]})
        return True
    return False


async def start_run(db: AsyncSession, *, student_id: int, variant_id: int) -> dict[str, Any]:
    """Создать run с публичным и закрытым checker snapshot либо вернуть текущий."""
    await db.execute(text("SELECT pg_advisory_xact_lock(:student,:variant)"), {"student": student_id, "variant": variant_id})
    variant = (await db.execute(text("SELECT * FROM exam_variant WHERE id=:id AND status='published'"), {"id": variant_id})).mappings().first()
    if variant is None:
        raise _error("Вариант не найден.", 404, "exam_variant_not_found")
    await assert_course_access(db, current_user=CurrentUser(id=student_id), course_id=int(variant["root_course_id"]))
    existing = (await db.execute(text("SELECT id FROM exam_run WHERE student_id=:student AND variant_id=:variant"), {"student": student_id, "variant": variant_id})).scalar_one_or_none()
    if existing:
        return await get_run(db, run_id=int(existing), student_id=student_id)
    previous = (await db.execute(text("SELECT r.id,r.status FROM exam_variant v LEFT JOIN exam_run r ON r.variant_id=v.id AND r.student_id=:student WHERE v.root_course_id=:root AND v.exam_kind=:kind AND v.sequence<:sequence AND (r.id IS NULL OR r.status IN ('in_progress','paused','review') OR EXISTS(SELECT 1 FROM exam_run_item i WHERE i.run_id=r.id AND i.initial_outcome IN ('wrong','blank') AND i.review_attempt_count=0)) ORDER BY v.sequence LIMIT 1"), {"student": student_id, "root": variant["root_course_id"], "kind": variant["exam_kind"], "sequence": variant["sequence"]})).mappings().first()
    if previous:
        unfinished = (await db.execute(text("SELECT count(*) FROM exam_run_item WHERE run_id=:id AND initial_outcome IN ('wrong','blank') AND review_attempt_count=0"), {"id": previous["id"]})).scalar_one()
        if previous["id"] is None or previous["status"] in ("in_progress", "paused", "review") or unfinished:
            raise _error("Сначала завершите вариант и попробуйте исправить каждую ошибку.", 409, "exam_previous_review_required")
    now = _now()
    run_id = int((await db.execute(text("INSERT INTO exam_run(student_id,variant_id,variant_version,scale_snapshot) VALUES(:student,:variant,:version,CAST(:scale AS JSONB)) RETURNING id"), {"student": student_id, "variant": variant_id, "version": variant["published_version"], "scale": _json(variant["scoring_scale"])})).scalar_one())
    items = (await db.execute(text("SELECT i.*,t.task_content,t.solution_rules,t.max_score FROM exam_variant_item i JOIN tasks t ON t.id=i.task_id WHERE i.variant_id=:id ORDER BY i.position"), {"id": variant_id})).mappings().all()
    for item in items:
        checker = {"task_content": item["task_content"], "solution_rules": item["solution_rules"], "max_score": item["max_score"], "checker_version": "1"}
        await db.execute(text("INSERT INTO exam_run_item(run_id,position,exam_number,task_id,primary_score,public_content,checker_snapshot) VALUES(:run,:position,:number,:task,:score,CAST(:public AS JSONB),CAST(:checker AS JSONB))"), {"run": run_id, "position": item["position"], "number": item["exam_number"], "task": item["task_id"], "score": item["primary_score"], "public": _json(item["task_content"]), "checker": _json(checker)})
    await db.execute(text("INSERT INTO exam_run_session(run_id,started_at,last_heartbeat_at) VALUES(:id,:now,:now)"), {"id": run_id, "now": now})
    await db.commit()
    return await get_run(db, run_id=run_id, student_id=student_id)


async def transition(db: AsyncSession, *, run_id: int, student_id: int, action: str) -> dict[str, Any]:
    """Пауза, возобновление или heartbeat по серверному lifecycle."""
    run = await _run(db, run_id, student_id, lock=True)
    now = _now()
    stale = await _settle_stale(db, run)
    if stale:
        run = await _run(db, run_id, student_id, lock=True)
        if action == "heartbeat":
            await db.commit()
            raise _error("Таймер автоматически остановлен. Возобновите вариант.", 409, "exam_timer_not_active")
    if action == "heartbeat":
        if run["status"] != "in_progress":
            raise _error("Таймер не активен.", 409, "exam_timer_not_active")
        session = (await db.execute(text("SELECT * FROM exam_run_session WHERE run_id=:id AND ended_at IS NULL FOR UPDATE"), {"id": run_id})).mappings().first()
        if session is None:
            raise _error("Сначала возобновите вариант.", 409, "exam_timer_not_active")
        elapsed = max(0, min(int((now - session["last_heartbeat_at"]).total_seconds()), HEARTBEAT_CAP_SECONDS))
        await db.execute(text("UPDATE exam_run_session SET last_heartbeat_at=:now,seconds=seconds+:elapsed WHERE id=:id"), {"now": now, "elapsed": elapsed, "id": session["id"]})
        await db.execute(text("UPDATE exam_run SET active_seconds=active_seconds+:elapsed,updated_at=:now WHERE id=:id"), {"elapsed": elapsed, "now": now, "id": run_id})
    elif action == "pause":
        if run["status"] != "in_progress":
            raise _error("Вариант нельзя поставить на паузу.", 409, "exam_invalid_transition")
        await _close_session(db, run_id, "pause", now)
        await db.execute(text("UPDATE exam_run SET status='paused',updated_at=:now WHERE id=:id"), {"now": now, "id": run_id})
    elif action == "resume":
        if run["status"] != "paused":
            raise _error("Вариант не на паузе.", 409, "exam_invalid_transition")
        await db.execute(text("UPDATE exam_run SET status='in_progress',updated_at=:now WHERE id=:id"), {"now": now, "id": run_id})
        await db.execute(text("INSERT INTO exam_run_session(run_id,started_at,last_heartbeat_at) VALUES(:id,:now,:now)"), {"id": run_id, "now": now})
    else:
        raise ValueError(action)
    await db.commit()
    return await get_run(db, run_id=run_id, student_id=student_id)


async def answer_initial(db: AsyncSession, *, run_id: int, item_id: int, student_id: int, request_id: str, answer: StudentAnswer, phase: str) -> dict[str, Any]:
    """Проверить initial по snapshot, не раскрывая вердикт и не создавая task_result."""
    if phase != "initial":
        raise _error("Фаза ответа не совпадает с состоянием варианта.", 409, "exam_phase_mismatch")
    run = await _run(db, run_id, student_id, lock=True)
    item = (await db.execute(text("SELECT * FROM exam_run_item WHERE id=:item AND run_id=:run FOR UPDATE"), {"item": item_id, "run": run_id})).mappings().first()
    if item is None:
        raise _error("Задание не найдено.", 404, "exam_item_not_found")
    fingerprint = _request_fingerprint({"answer": answer.model_dump(mode="json"), "phase": phase})
    cached = (await db.execute(text("SELECT response_json FROM exam_answer_request WHERE run_id=:run AND client_request_id=:request"), {"run": run_id, "request": request_id})).scalar_one_or_none()
    if cached is not None:
        if cached.get("operation") != f"initial:{item_id}" or cached.get("_request_fingerprint") != fingerprint:
            raise _error("Этот request id уже использован для другой операции или ответа.", 409, "exam_request_id_conflict")
        return _without_fingerprint(cached)
    if await _settle_stale(db, run):
        await db.commit()
        raise _error("Таймер автоматически остановлен. Возобновите вариант.", 409, "exam_timer_not_active")
    if run["status"] != "in_progress":
        raise _error("Первичное прохождение уже завершено или приостановлено.", 409, "exam_initial_finished")
    active_session = (await db.execute(text("SELECT 1 FROM exam_run_session WHERE run_id=:id AND ended_at IS NULL"), {"id": run_id})).scalar_one_or_none()
    if active_session is None:
        raise _error("Сначала возобновите вариант.", 409, "exam_timer_not_active")
    if item["initial_answer"] is not None:
        raise _error("Первичный ответ уже сохранён.", 409, "exam_initial_already_answered")
    snapshot = item["checker_snapshot"]
    content = TaskContent.model_validate(snapshot["task_content"])
    if answer.type != content.type:
        raise _error("Тип ответа не совпадает с заданием.", 400, "exam_answer_type_mismatch")
    checking = CheckingService()
    rules = checking.build_solution_rules(snapshot["solution_rules"], snapshot["max_score"])
    result = await asyncio.to_thread(checking.check_task, task_content=content, solution_rules=rules, answer=answer)
    partial_allowed = int(item["exam_number"]) in (26, 27)
    valid_scores = range(0, int(result.max_score) + 1) if partial_allowed and result.max_score is not None else (0, result.max_score)
    if result.is_correct is None or result.max_score != item["primary_score"] or result.score not in valid_scores or result.score > item["primary_score"]:
        raise _error("Форма балла не соответствует правилам номера.", 422, "exam_non_binary_result")
    private = {"answer": answer.model_dump(mode="json"), "score": result.score, "max_score": result.max_score, "is_correct": result.is_correct}
    response = {"accepted": True, "run_id": run_id, "item_id": item_id, "operation": f"initial:{item_id}", "_request_fingerprint": fingerprint}
    await db.execute(text("UPDATE exam_run_item SET initial_answer=CAST(:private AS JSONB),updated_at=now() WHERE id=:id"), {"private": _json(private), "id": item_id})
    await db.execute(text("INSERT INTO exam_answer_request(run_id,client_request_id,response_json) VALUES(:run,:request,CAST(:response AS JSONB))"), {"run": run_id, "request": request_id, "response": _json(response)})
    await db.commit()
    return _without_fingerprint(response)


async def skip_initial(db: AsyncSession, *, run_id: int, item_id: int, student_id: int, request_id: str) -> dict[str, Any]:
    """Зафиксировать «не знаю, как решить» как blank без раскрытия ответа."""
    run = await _run(db, run_id, student_id, lock=True)
    item = (await db.execute(text("SELECT id,initial_answer FROM exam_run_item WHERE id=:item AND run_id=:run FOR UPDATE"), {"item": item_id, "run": run_id})).mappings().first()
    if item is None:
        raise _error("Задание не найдено.", 404, "exam_item_not_found")
    fingerprint = _request_fingerprint({"skipped": True, "item_id": item_id})
    cached = (await db.execute(text("SELECT response_json FROM exam_answer_request WHERE run_id=:run AND client_request_id=:request"), {"run": run_id, "request": request_id})).scalar_one_or_none()
    if cached is not None:
        if cached.get("operation") != f"skip:{item_id}" or cached.get("_request_fingerprint") != fingerprint:
            raise _error("Этот request id уже использован для другой операции или ответа.", 409, "exam_request_id_conflict")
        return _without_fingerprint(cached)
    if await _settle_stale(db, run):
        await db.commit()
        raise _error("Таймер автоматически остановлен. Возобновите вариант.", 409, "exam_timer_not_active")
    if run["status"] != "in_progress":
        raise _error("Первичное прохождение уже завершено или приостановлено.", 409, "exam_initial_finished")
    active_session = (await db.execute(text("SELECT 1 FROM exam_run_session WHERE run_id=:id AND ended_at IS NULL"), {"id": run_id})).scalar_one_or_none()
    if active_session is None:
        raise _error("Сначала возобновите вариант.", 409, "exam_timer_not_active")
    if item["initial_answer"] is not None:
        raise _error("Первичный ответ уже сохранён.", 409, "exam_initial_already_answered")
    response = {"accepted": True, "run_id": run_id, "item_id": item_id, "operation": f"skip:{item_id}", "_request_fingerprint": fingerprint}
    await db.execute(text("UPDATE exam_run_item SET initial_answer=CAST(:private AS JSONB),updated_at=now() WHERE id=:id"), {"private": _json({"skipped": True}), "id": item_id})
    await db.execute(text("INSERT INTO exam_answer_request(run_id,client_request_id,response_json) VALUES(:run,:request,CAST(:response AS JSONB))"), {"run": run_id, "request": request_id, "response": _json(response)})
    await db.commit()
    return _without_fingerprint(response)


async def finish_initial(db: AsyncSession, *, run_id: int, student_id: int) -> dict[str, Any]:
    """Материализовать скрытые initial-результаты и открыть РНО одной транзакцией."""
    run = await _run(db, run_id, student_id, lock=True)
    if run["status"] not in ("in_progress", "paused"):
        return await get_run(db, run_id=run_id, student_id=student_id)
    now = _now()
    await _close_session(db, run_id, "initial_finish", now)
    items = (await db.execute(text("SELECT * FROM exam_run_item WHERE run_id=:id ORDER BY position FOR UPDATE"), {"id": run_id})).mappings().all()
    from app.models.attempts import Attempts
    from app.services.task_results_service import TaskResultsService
    for item in items:
        private = item["initial_answer"]
        if private is None or private.get("skipped") is True:
            outcome = "blank"
            initial_score = 0
        else:
            answer = StudentAnswer.model_validate(private["answer"])
            attempt = Attempts(user_id=student_id, course_id=None, root_course_id=None, source_system="exam_progress", finished_at=now)
            db.add(attempt)
            await db.flush()
            task_result = await TaskResultsService().create_from_check_result(db=db, attempt_id=attempt.id, task_id=int(item["task_id"]), user_id=student_id, answer=answer, check_result=CheckResult(score=private["score"], max_score=private["max_score"], is_correct=private["is_correct"]), source_system="exam_initial", commit=False)
            outcome = "correct" if private["is_correct"] and private["score"] == item["primary_score"] else "wrong"
            initial_score = int(private["score"])
            await db.execute(text("UPDATE exam_run_item SET initial_result_id=:result,resolution=:resolution WHERE id=:id"), {"result": task_result.id, "resolution": "initial" if outcome == "correct" else None, "id": item["id"]})
        await db.execute(text("UPDATE exam_run_item SET initial_outcome=:outcome,initial_score=:initial_score,updated_at=:now WHERE id=:id"), {"outcome": outcome, "initial_score": initial_score, "now": now, "id": item["id"]})
    await db.execute(text("UPDATE exam_run SET status='review',initial_finished_at=:now,updated_at=:now WHERE id=:id"), {"now": now, "id": run_id})
    await db.commit()
    return await get_run(db, run_id=run_id, student_id=student_id)


async def answer_review(
    db: AsyncSession, *, run_id: int, item_id: int, student_id: int,
    request_id: str, answer: StudentAnswer, phase: str,
    reflection_reason: str | None, reflection_comment: str | None,
) -> dict[str, Any]:
    """Принять неограниченную попытку РНО/late; верный ответ требует рефлексию."""
    expected_status = "review" if phase == "review" else "completed" if phase == "late" else ""
    if not expected_status:
        raise _error("Фаза ответа не совпадает с состоянием варианта.", 409, "exam_phase_mismatch")
    run = await _run(db, run_id, student_id, lock=True)
    item = (await db.execute(text("SELECT * FROM exam_run_item WHERE id=:item AND run_id=:run FOR UPDATE"), {"item": item_id, "run": run_id})).mappings().first()
    if item is None:
        raise _error("Задание не найдено.", 404, "exam_item_not_found")
    fingerprint = _request_fingerprint({"answer": answer.model_dump(mode="json"), "phase": phase, "reflection_reason": reflection_reason, "reflection_comment": reflection_comment})
    cached = (await db.execute(text("SELECT response_json FROM exam_answer_request WHERE run_id=:run AND client_request_id=:request"), {"run": run_id, "request": request_id})).scalar_one_or_none()
    if cached is not None:
        if cached.get("operation") != f"{phase}:{item_id}" or cached.get("_request_fingerprint") != fingerprint:
            raise _error("Этот request id уже использован для другой операции или ответа.", 409, "exam_request_id_conflict")
        return _without_fingerprint(cached)
    if run["status"] != expected_status:
        raise _error("Фаза ответа не совпадает с состоянием варианта.", 409, "exam_phase_mismatch")
    if item["initial_outcome"] not in ("wrong", "blank") or item["resolution"] is not None:
        raise _error("Это задание не требует отработки.", 409, "exam_item_not_reviewable")
    snapshot = item["checker_snapshot"]
    content = TaskContent.model_validate(snapshot["task_content"])
    if answer.type != content.type:
        raise _error("Тип ответа не совпадает с заданием.", 400, "exam_answer_type_mismatch")
    checking = CheckingService()
    rules = checking.build_solution_rules(snapshot["solution_rules"], snapshot["max_score"])
    result = await asyncio.to_thread(checking.check_task, task_content=content, solution_rules=rules, answer=answer)
    correct = result.is_correct is True and result.score == result.max_score == item["primary_score"]
    partial_allowed = int(item["exam_number"]) in (26, 27)
    valid_scores = range(0, int(result.max_score) + 1) if partial_allowed and result.max_score is not None else (0, result.max_score)
    if result.is_correct is None or result.max_score != item["primary_score"] or result.score not in valid_scores or result.score > item["primary_score"]:
        raise _error("Форма балла не соответствует правилам номера.", 422, "exam_non_binary_result")
    if correct and reflection_reason is None:
        raise _error("Выберите причину исходной ошибки.", 422, "exam_reflection_required")
    now = _now()
    from app.models.attempts import Attempts
    from app.services.task_results_service import TaskResultsService
    attempt = Attempts(user_id=student_id, course_id=None, root_course_id=None, source_system="exam_progress", finished_at=now)
    db.add(attempt)
    await db.flush()
    task_result = await TaskResultsService().create_from_check_result(
        db=db, attempt_id=attempt.id, task_id=int(item["task_id"]), user_id=student_id,
        answer=answer, check_result=result, source_system="exam_review", commit=False,
    )
    resolution = ("rno" if phase == "review" else "late") if correct else None
    await db.execute(text("UPDATE exam_run_item SET review_result_id=:result,review_attempt_count=review_attempt_count+1,resolution=COALESCE(:resolution,resolution),reflection_reason=CASE WHEN :correct THEN :reason ELSE reflection_reason END,reflection_comment=CASE WHEN :correct THEN :comment ELSE reflection_comment END,reflected_at=CASE WHEN :correct THEN :now ELSE reflected_at END,updated_at=:now WHERE id=:id"), {"result": task_result.id, "resolution": resolution, "correct": correct, "reason": reflection_reason, "comment": reflection_comment, "now": now, "id": item_id})
    response = {"accepted": True, "run_id": run_id, "item_id": item_id, "correct": correct, "resolution": resolution, "operation": f"{phase}:{item_id}", "_request_fingerprint": fingerprint}
    await db.execute(text("INSERT INTO exam_answer_request(run_id,client_request_id,response_json) VALUES(:run,:request,CAST(:response AS JSONB))"), {"run": run_id, "request": request_id, "response": _json(response)})
    await db.commit()
    return _without_fingerprint(response)


async def finish_review(db: AsyncSession, *, run_id: int, student_id: int) -> dict[str, Any]:
    """Завершить основную РНО после попытки по каждой initial-ошибке."""
    run = await _run(db, run_id, student_id, lock=True)
    if run["status"] == "completed":
        return await get_run(db, run_id=run_id, student_id=student_id)
    if run["status"] != "review":
        raise _error("Сначала завершите первичное прохождение.", 409, "exam_invalid_transition")
    remaining = (await db.execute(text("SELECT count(*) FROM exam_run_item WHERE run_id=:id AND initial_outcome IN ('wrong','blank') AND review_attempt_count=0"), {"id": run_id})).scalar_one()
    if remaining:
        raise _error("Попробуйте исправить каждую ошибку перед завершением РНО.", 409, "exam_review_incomplete")
    now = _now()
    await db.execute(text("UPDATE exam_run SET status='completed',review_finished_at=:now,completed_at=:now,updated_at=:now WHERE id=:id"), {"now": now, "id": run_id})
    await db.commit()
    return await get_run(db, run_id=run_id, student_id=student_id)


async def teacher_review_queue(db: AsyncSession, *, user_id: int, elevated: bool) -> list[dict[str, Any]]:
    """Живой список unresolved red после РНО без видео, уже отфильтрованный по ACL."""
    scope = "" if elevated else "AND EXISTS (SELECT 1 FROM student_teacher_links l WHERE l.student_id=r.student_id AND l.teacher_id=:teacher)"
    rows = (await db.execute(text(f"SELECT i.task_id,v.exam_kind,i.exam_number,i.public_content,r.student_id,r.id run_id,u.full_name FROM exam_run_item i JOIN exam_run r ON r.id=i.run_id JOIN exam_variant v ON v.id=r.variant_id JOIN tasks t ON t.id=i.task_id JOIN users u ON u.id=r.student_id WHERE r.status='completed' AND i.initial_outcome IN ('wrong','blank') AND i.resolution IS NULL AND COALESCE(t.task_content->'hints_video','[]'::jsonb)='[]'::jsonb {scope} ORDER BY v.exam_kind,i.exam_number,i.task_id,u.full_name"), {"teacher": user_id})).mappings().all()
    grouped: dict[tuple[int, str, int], dict[str, Any]] = {}
    for row in rows:
        key = (int(row["task_id"]), row["exam_kind"], int(row["exam_number"]))
        item = grouped.setdefault(key, {"task_id": key[0], "exam_kind": key[1], "exam_number": key[2], "title": row["public_content"].get("title") or f"Задание {key[2]}", "student_count": 0, "red_count": 0, "students": [], "_seen": set()})
        item["red_count"] += 1
        if int(row["student_id"]) in item["_seen"]:
            continue
        item["_seen"].add(int(row["student_id"]))
        item["students"].append({"student_id": int(row["student_id"]), "full_name": row["full_name"], "run_id": int(row["run_id"])})
        item["student_count"] += 1
    for item in grouped.values():
        item.pop("_seen")
    return list(grouped.values())


async def list_variants(db: AsyncSession, *, student_id: int, root_course_id: int, exam_kind: str) -> list[dict[str, Any]]:
    """Вернуть каталог вариантов и доступность по завершённости предыдущей РНО."""
    await assert_course_access(db, current_user=CurrentUser(id=student_id), course_id=root_course_id)
    rows = (await db.execute(text("SELECT v.id,v.title,v.sequence,r.id run_id,r.status run_status,(SELECT count(*) FROM exam_run_item i WHERE i.run_id=r.id AND i.initial_outcome IN ('wrong','blank') AND i.review_attempt_count=0) unfinished FROM exam_variant v LEFT JOIN exam_run r ON r.variant_id=v.id AND r.student_id=:student WHERE v.root_course_id=:root AND v.exam_kind=:kind AND v.status='published' ORDER BY v.sequence"), {"student": student_id, "root": root_course_id, "kind": exam_kind})).mappings().all()
    previous_open = False
    result = []
    for row in rows:
        can_start = row["run_id"] is not None or not previous_open
        result.append({"variant_id": int(row["id"]), "title": row["title"], "sequence": int(row["sequence"]), "run_id": int(row["run_id"]) if row["run_id"] else None, "can_start": can_start})
        previous_open = row["run_id"] is None or row["run_status"] in ("in_progress", "paused", "review") or row["unfinished"] > 0
    return result


def initial_group_score(items: Sequence[Mapping[str, Any]]) -> int:
    """Одиночный номер хранит 0..weight; составной — all-or-nothing."""
    weight = int(items[0]["primary_score"])
    if len(items) == 1:
        return int(items[0]["initial_score"] or 0)
    return weight if all(item["initial_outcome"] == "correct" for item in items) else 0


async def get_map(db: AsyncSession, *, student_id: int, root_course_id: int, exam_kind: str) -> dict[str, Any]:
    """Собрать одну ячейку на номер и неизменяемый initial score по шкале."""
    await assert_course_access(db, current_user=CurrentUser(id=student_id), course_id=root_course_id)
    rows = (await db.execute(text("SELECT r.id run_id,r.started_at,r.active_seconds,r.scale_snapshot,v.id variant_id,v.title,v.sequence,i.exam_number,i.primary_score,i.initial_outcome,i.initial_score,i.resolution,i.review_attempt_count FROM exam_run r JOIN exam_variant v ON v.id=r.variant_id JOIN exam_run_item i ON i.run_id=r.id WHERE r.student_id=:student AND v.root_course_id=:root AND v.exam_kind=:kind ORDER BY v.sequence,i.position"), {"student": student_id, "root": root_course_id, "kind": exam_kind})).mappings().all()
    numbers = sorted({int(row["exam_number"]) for row in rows})
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        run_id = int(row["run_id"])
        column = grouped.setdefault(run_id, {"run_id": run_id, "variant_id": int(row["variant_id"]), "title": row["title"], "sequence": int(row["sequence"]), "started_at": row["started_at"], "active_seconds": int(row["active_seconds"]), "primary_score": 0, "result_label": "", "cells": [], "_scale": row["scale_snapshot"], "_groups": {}})
        column["_groups"].setdefault(int(row["exam_number"]), []).append(row)
    for column in grouped.values():
        for number, items in column["_groups"].items():
            score = initial_group_score(items)
            column["primary_score"] += score
            resolutions = {item["resolution"] for item in items}
            if None in resolutions:
                color = "red"
            elif "late" in resolutions:
                color = "orange"
            elif "rno" in resolutions:
                color = "yellow"
            else:
                color = "green"
            if None in resolutions:
                review_state = "in_progress" if any(item["review_attempt_count"] for item in items) else "not_started"
            elif "late" in resolutions:
                review_state = "late_resolved"
            else:
                review_state = "resolved"
            column["cells"].append({"run_id": column["run_id"], "exam_number": number, "color": color, "review_state": review_state})
        column.pop("_groups")
        column["cells"].sort(key=lambda cell: cell["exam_number"])
        scale = column.pop("_scale")
        result = scale["primary_to_result"].get(str(column["primary_score"]))
        if result is None:
            raise _error("В snapshot шкалы нет первичного балла.", 500, "exam_scale_incomplete")
        column["result_label"] = f"Оценка {result}" if exam_kind == "oge" else f"{result} баллов"
    return {"exam_kind": exam_kind, "exam_numbers": numbers, "columns": list(grouped.values())}


async def get_run(db: AsyncSession, *, run_id: int, student_id: int) -> dict[str, Any]:
    """Вернуть только публичную проекцию run, без checker snapshot и скрытых вердиктов."""
    run = await _run(db, run_id, student_id)
    variant = (await db.execute(text("SELECT root_course_id,exam_kind FROM exam_variant WHERE id=:id"), {"id": run["variant_id"]})).mappings().one()
    items = (await db.execute(text("SELECT id,exam_number,task_id,initial_outcome,initial_score,review_attempt_count,resolution,public_content,initial_answer FROM exam_run_item WHERE run_id=:id ORDER BY position"), {"id": run_id})).mappings().all()
    visible = run["status"] in ("review", "completed")
    return {
        "run_id": run_id, "variant_id": int(run["variant_id"]), "root_course_id": int(variant["root_course_id"]), "exam_kind": variant["exam_kind"], "status": run["status"], "started_at": run["started_at"],
        "active_seconds": int(run["active_seconds"]), "initial_finished_at": run["initial_finished_at"], "review_finished_at": run["review_finished_at"],
        "items": [{"item_id": int(i["id"]), "exam_number": int(i["exam_number"]), "task_id": int(i["task_id"]), "initial_outcome": i["initial_outcome"] if visible else None, "initial_score": i["initial_score"] if visible else None, "review_attempt_count": int(i["review_attempt_count"]), "color": ({"initial": "green", "rno": "yellow", "late": "orange"}.get(i["resolution"], "red") if visible and i["initial_outcome"] else None), "review_state": (("late_resolved" if i["resolution"] == "late" else "resolved") if i["resolution"] else "in_progress" if i["review_attempt_count"] else "not_started") if visible else None, "has_video_review": bool(i["public_content"].get("hints_video")), "available_phase": "initial" if run["status"] in ("in_progress", "paused") and i["initial_answer"] is None else "review" if run["status"] == "review" and i["initial_outcome"] in ("wrong", "blank") and i["resolution"] is None else "late" if run["status"] == "completed" and i["initial_outcome"] in ("wrong", "blank") and i["resolution"] is None else None, "is_skipped": bool((i["initial_answer"] or {}).get("skipped")), "content": i["public_content"]} for i in items],
    }
