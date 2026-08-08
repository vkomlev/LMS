"""
Гибридный режим проверки: авто-сверка части ответа при обязательном ручном гейте
(tsk-396, ADR-0007). Заменяет обход tsk-395 (SA_COM + manual_review_required).

Покрывает критерии готовности задачи:
- (а) числа сошлись → ученик СРАЗУ видит авто-итог, но задание НЕ зачтено
      (score=0, state≠PASSED) до оценки преподавателем;
- (б) числа не сошлись → is_correct=False, к преподавателю работа НЕ идёт;
- (в) эталон НЕ раскрывается ученику (details.matched_short_answer пуст);
- (г) работа с верными числами попадает в обязательную очередь с подсказкой
      auto_checked_part_matched=true; с неверными — не попадает;
- (д) паритет SA_COM и TBL_COM (близнецы-типы не должны разъезжаться);
- (е) РЕГРЕСС найденного дефекта: SA_COM с эталоном + manual_review_required
      больше НЕ получает оптимистичный зачёт на заведомо неверный ответ;
- (ж) регресс: SA_COM БЕЗ эталона (миссии флагмана) оптимистичный зачёт сохраняет;
- (з) валидатор режима и проброс флага клиенту.

Тесты работают с dev-БД (Learn.public) и подчищают за собой.
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.core.config import Settings
from app.schemas.solution_rules import SolutionRules

pytestmark = pytest.mark.asyncio

_settings = Settings()

# Эталон ОГЭ-14 курса 1179 (задание 7151) — форма «два числа одной строкой».
REFERENCE = "12 516,30"
WRONG = "999 999"


def _headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


# ── фикстуры данных ──────────────────────────────────────────────────────────


async def _make_student(db) -> int:
    r = await db.execute(
        text("INSERT INTO users (email, full_name) VALUES (:e, 'tsk396 student') RETURNING id"),
        {"e": f"tsk396_{uuid.uuid4().hex[:8]}@example.com"},
    )
    sid = int(r.scalar())
    await db.commit()
    return sid


async def _make_course(db) -> int:
    r = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"),
        {"t": f"tsk396 {uuid.uuid4().hex[:8]}"},
    )
    cid = int(r.scalar())
    await db.commit()
    return cid


async def _make_task(
    db,
    course_id: int,
    *,
    task_type: str = "SA_COM",
    partial_auto_check: bool = True,
    manual_review_required: bool = True,
    with_reference: bool = True,
    requires_attachment: bool = False,
) -> int:
    """Задание в гибридном режиме (либо его вариации для регресс-проверок)."""
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = '{"type":"' + task_type + '","stem":"Два числа и диаграмма"}'
    parts = [
        '"max_score":1',
        '"manual_review_required":' + ("true" if manual_review_required else "false"),
        '"partial_auto_check":' + ("true" if partial_auto_check else "false"),
        '"requires_attachment":' + ("true" if requires_attachment else "false"),
    ]
    if with_reference:
        parts.append(
            '"short_answer":{"normalization":["trim","collapse_spaces"],'
            '"accepted_answers":[{"value":"' + REFERENCE + '","score":1}]}'
        )
    sr = "{" + ",".join(parts) + "}"
    r = await db.execute(
        text(
            "INSERT INTO tasks (course_id, difficulty_id, task_content, solution_rules) "
            "VALUES (:cid, :did, CAST(:tc AS jsonb), CAST(:sr AS jsonb)) RETURNING id"
        ),
        {"cid": course_id, "did": diff, "tc": tc, "sr": sr},
    )
    tid = int(r.scalar())
    await db.commit()
    return tid


async def _create_attempt(client, *, student_id: int, course_id: int) -> int:
    resp = await client.post(
        "/api/v1/attempts",
        json={"user_id": student_id, "course_id": course_id, "source_system": "test"},
        headers=_headers(),
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _submit(client, attempt_id: int, task_id: int, value: str, task_type: str = "SA_COM"):
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": task_id, "answer": {
            "type": task_type,
            "response": {"value": value, "comment": "построил круговую диаграмму"},
        }}]},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]["check_result"]


async def _task_state(client, task_id: int, student_id: int) -> dict:
    resp = await client.get(
        f"/api/v1/learning/tasks/{task_id}/state?student_id={student_id}",
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _cleanup(db, *, course_id: int, student_id: int) -> None:
    await db.execute(text("DELETE FROM courses WHERE id = :cid"), {"cid": course_id})
    await db.execute(text("DELETE FROM users WHERE id = :sid"), {"sid": student_id})
    await db.commit()


# ── (а) числа сошлись: обратная связь есть, зачёта нет ───────────────────────


async def test_matched_numbers_give_feedback_but_no_pass(client, db):
    """Главный критерий: авто-итог виден сразу, задание НЕ зачтено до преподавателя."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        result = await _submit(client, attempt_id, task_id, REFERENCE)

        # Ученик сразу видит, что числа сошлись.
        assert "сошл" in (result["feedback"]["general"] or "").lower()
        # Но зачёта нет: score=0 → PASS-гейт движка (score/max_score >= 0.5) не пройден.
        assert result["score"] == 0
        assert result["is_correct"] is None

        state = await _task_state(client, task_id, student_id)
        assert state["state"] != "PASSED", (
            "задание зачтено до ручной проверки — сломан гейт tsk-396"
        )
        assert state["last_checked_at"] is None
        assert state["partial_auto_check"] is True
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (б) числа не сошлись: авто-вердикт, преподавателя не тревожим ────────────


async def test_wrong_numbers_fail_without_teacher(client, db):
    """Числа не сошлись → is_correct=False (законченный авто-вердикт), не PASSED."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        result = await _submit(client, attempt_id, task_id, WRONG)
        assert result["is_correct"] is False
        assert result["score"] == 0
        assert "не сошл" in (result["feedback"]["general"] or "").lower()

        state = await _task_state(client, task_id, student_id)
        assert state["state"] != "PASSED"
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (в) эталон не утекает ученику ────────────────────────────────────────────


async def test_reference_answer_not_leaked_to_student(client, db):
    """CheckResult эхо-возвращается ученику (tsk-302) — эталона в нём быть не должно."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        result = await _submit(client, attempt_id, task_id, REFERENCE)
        details = result.get("details")
        assert details is None or details.get("matched_short_answer") is None
        # И в тексте обратной связи эталона тоже нет.
        assert REFERENCE not in (result["feedback"]["general"] or "")
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (г) очередь преподавателя ────────────────────────────────────────────────


async def test_queue_shows_matched_and_hides_mismatched(client, db):
    """Верные числа → в обязательной очереди с подсказкой; неверные → не в очереди."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    ok_task = await _make_task(db, course_id)
    bad_task = await _make_task(db, course_id)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        await _submit(client, attempt_id, ok_task, REFERENCE)
        await _submit(client, attempt_id, bad_task, WRONG)

        # Методист видит всю очередь (REVIEW_ACL_SQL: methodist без course-tree ACL).
        r = await db.execute(
            text("INSERT INTO users (email, full_name) VALUES (:e, 'tsk396 methodist') RETURNING id"),
            {"e": f"tsk396_m_{uuid.uuid4().hex[:8]}@example.com"},
        )
        methodist_id = int(r.scalar())
        await db.execute(
            text("INSERT INTO user_roles (user_id, role_id) "
                 "SELECT :u, id FROM roles WHERE name = 'methodist'"),
            {"u": methodist_id},
        )
        await db.commit()

        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={methodist_id}&limit=200",
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        by_task = {it["task_id"]: it for it in resp.json()["items"]}

        assert ok_task in by_task, "работа с верными числами не дошла до преподавателя"
        assert by_task[ok_task]["auto_checked_part_matched"] is True

        assert bad_task not in by_task, (
            "работа с неверными числами попала в очередь — преподаватель тратит время"
        )

        await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": methodist_id})
        await db.commit()
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (д) паритет SA_COM и TBL_COM ─────────────────────────────────────────────


async def test_tbl_com_parity(client, db):
    """TBL_COM ведёт себя так же: близнецы-типы не должны разъезжаться."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id, task_type="TBL_COM")
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        ok = await _submit(client, attempt_id, task_id, REFERENCE, task_type="TBL_COM")
        assert ok["score"] == 0 and ok["is_correct"] is None
        state = await _task_state(client, task_id, student_id)
        assert state["state"] != "PASSED"
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (е) РЕГРЕСС найденного дефекта ───────────────────────────────────────────


async def test_manual_with_reference_no_longer_false_passes(client, db):
    """Дефект, найденный под tsk-396: SA_COM + эталон + manual_review_required
    получал оптимистичный зачёт (score=max, is_correct=True, state=PASSED) на
    ЗАВЕДОМО неверный ответ. Обход tsk-395 держался именно на этой связке.
    Здесь partial_auto_check=false — то есть проверяется прежняя конфигурация."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id, partial_auto_check=False)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        result = await _submit(client, attempt_id, task_id, WRONG)
        assert result["is_correct"] is not True, "неверный ответ снова зачтён"
        assert result["score"] < result["max_score"]

        state = await _task_state(client, task_id, student_id)
        assert state["state"] != "PASSED", (
            "заведомо неверный ответ дал PASSED до ручной проверки (регресс tsk-396)"
        )
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (ж) регресс: миссии без эталона сохраняют оптимистичный зачёт ────────────


async def test_no_reference_mission_keeps_optimistic_pass(client, db):
    """SA_COM БЕЗ эталона (миссии флагмана, 230 заданий прода) — сверять нечем,
    оптимистичный зачёт tsk-210 обязан сохраниться: иначе ученики встали бы
    в ожидание преподавателя."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(
        db, course_id, partial_auto_check=False, with_reference=False
    )
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        result = await _submit(client, attempt_id, task_id, "сделал")
        assert result["is_correct"] is True
        assert result["score"] == result["max_score"]
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (з) валидатор режима + проброс флага ─────────────────────────────────────


async def test_validator_requires_manual_review_required():
    """partial_auto_check без ручного гейта обнулял бы сам себя → 422 на импорте."""
    with pytest.raises(ValidationError, match="manual_review_required"):
        SolutionRules.model_validate({
            "max_score": 1,
            "partial_auto_check": True,
            "manual_review_required": False,
            "short_answer": {"accepted_answers": [{"value": REFERENCE, "score": 1}]},
        })


async def test_validator_requires_reference_answer():
    """partial_auto_check без эталона обещал бы обратную связь, которой нет."""
    with pytest.raises(ValidationError, match="эталон"):
        SolutionRules.model_validate({
            "max_score": 1,
            "partial_auto_check": True,
            "manual_review_required": True,
        })


async def test_flag_defaults_to_false():
    """Задания без флага ведут себя как раньше — режим строго opt-in."""
    rules = SolutionRules.model_validate({"max_score": 1})
    assert rules.partial_auto_check is False


async def test_pending_hybrid_work_is_escalated(client, db, db_session_factory):
    """Ждущая работа гибридного режима обязана попадать в эскалацию.

    Ловушка класса tsk-438: эскалация ищет SA_COM/TBL_COM с `is_correct IS TRUE`
    (оптимистичный зачёт), а гибридный режим даёт ЖДУЩЕЙ работе `is_correct=NULL`.
    Без отдельной ветки залежавшаяся работа не эскалировалась бы методисту
    никогда — тихо, без единой ошибки в логах.
    """
    from app.services.escalation_service import escalation_cron_tick

    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        await _submit(client, attempt_id, task_id, REFERENCE)

        # Сдвигаем сдачу за таймаут — иначе кандидат не наберётся. `metrics`
        # НЕ трогаем: работа идёт через реальный submit, и туда ложится
        # JSON-null (Pydantic metrics=None → json null). Прежняя версия теста
        # выставляла здесь `metrics = '{}'`, обходя дефект отбора крона
        # (tsk-582): условие требовало SQL NULL либо объект, а JSON-null не
        # проходил ни одну ветку. Обход снят вместе с починкой — теперь этот
        # тест заодно стережёт и форму metrics.
        await db.execute(
            text("UPDATE task_results SET submitted_at = now() - interval '30 days' "
                 "WHERE task_id = :t"),
            {"t": task_id},
        )
        await db.commit()

        row = (await db.execute(
            text("SELECT is_correct, checked_at, jsonb_typeof(metrics) "
                 "FROM task_results WHERE task_id = :t"),
            {"t": task_id},
        )).fetchone()
        assert row[0] is None and row[1] is None, "предпосылка теста нарушена"
        assert row[2] == "null", (
            "предпосылка теста нарушена: на сдаче в metrics должен ложиться "
            f"JSON-null, а не {row[2]} — иначе тест перестаёт стеречь tsk-582"
        )

        summary = await escalation_cron_tick(session_factory=db_session_factory)
        assert summary["candidates"] >= 1, (
            "ждущая работа гибридного режима не попала в кандидаты эскалации: "
            "is_correct=NULL у SA_COM ловится только веткой partial_auto_check"
        )
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_task_state_exposes_flag_false_by_default(client, db):
    """Задание вне гибридного режима отдаёт partial_auto_check=false."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id, partial_auto_check=False)
    try:
        state = await _task_state(client, task_id, student_id)
        assert state["partial_auto_check"] is False
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)
