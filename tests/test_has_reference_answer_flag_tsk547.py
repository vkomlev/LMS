"""
Флаг `has_reference_answer` в состоянии задания (tsk-547).

Зачем. У 271 активного задания `SA_COM` на проде эталона для сверки
`response.value` нет вовсе (после tsk-546: ОГЭ-13 сдаётся файлом, ОГЭ-15/16 —
программой, авторские проекты — скриншотом или объяснением). Поле «Ответ» в
форме SPW для них бессмысленно, но клиент не может это определить сам:
`solution_rules` ученику не отдаются — видимость полей это отдельный слой
(tsk-460). Значит нужен производный флаг с сервера, как `requires_attachment`
(tsk-227).

Покрывает:
- (а) юнит `SolutionRules.has_reference_answer()` — все три формы «эталона нет»
      (плейбук ЕГЭ §6.1: SQL NULL / JSON-null / объект-но-пустой) и обе формы
      «эталон есть» (accepted_answers, regex);
- (б) `GET /learning/tasks/{id}/state` отдаёт флаг для SA_COM в обе стороны;
- (в) типо-зависимость: у SC/TA флаг true, хотя `short_answer` у них пуст всегда
      (иначе клиент принял бы «эталона нет» за «поле ответа не нужно»);
- (г) сдача с ПУСТЫМ `value` и комментарием на таком задании принимается и даёт
      оптимистичный зачёт — именно это будет слать SPW, когда поле скрыто.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.schemas.solution_rules import SolutionRules

pytestmark = pytest.mark.asyncio

_settings = Settings()


def _headers() -> dict[str, str]:
    api_key = next(iter(_settings.valid_api_keys))
    return {"X-API-Key": api_key}


async def _make_student(db) -> int:
    email = f"tsk547_{uuid.uuid4().hex[:8]}@example.com"
    r = await db.execute(
        text("INSERT INTO users (email, full_name) VALUES (:e, 'tsk547 student') RETURNING id"),
        {"e": email},
    )
    sid = int(r.scalar())
    await db.commit()
    return sid


async def _make_course(db) -> int:
    r = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"),
        {"t": f"tsk547 {uuid.uuid4().hex[:8]}"},
    )
    cid = int(r.scalar())
    await db.commit()
    return cid


async def _make_task(db, course_id: int, *, task_type: str, rules_json: str) -> int:
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = '{"type":"' + task_type + '","stem":"Тестовое задание tsk-547."}'
    r = await db.execute(
        text(
            "INSERT INTO tasks (course_id, difficulty_id, task_content, solution_rules) "
            "VALUES (:cid, :did, CAST(:tc AS jsonb), CAST(:sr AS jsonb)) RETURNING id"
        ),
        {"cid": course_id, "did": diff, "tc": tc, "sr": rules_json},
    )
    tid = int(r.scalar())
    await db.commit()
    return tid


async def _cleanup(db, *, course_id: int, student_id: int) -> None:
    await db.execute(text("DELETE FROM courses WHERE id = :cid"), {"cid": course_id})
    await db.execute(text("DELETE FROM users WHERE id = :sid"), {"sid": student_id})
    await db.commit()


async def _state(client, task_id: int, student_id: int) -> dict:
    resp = await client.get(
        f"/api/v1/learning/tasks/{task_id}/state?student_id={student_id}",
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── (а) юнит предиката ──────────────────────────────────────────────────────


async def test_has_reference_answer_unit_all_empty_forms():
    """Три формы «эталона нет» + две формы «эталон есть».

    Форма «объект-но-пустой» — та, которую наивная проверка `short_answer is
    None` пропускает; ровно она дважды давала ложное «всё чисто» (плейбук §6.1).
    """
    # блока правил нет вовсе
    assert SolutionRules.model_validate({"max_score": 10}).has_reference_answer() is False
    # блок есть, но пустой — accepted_answers=[] и regex не задан
    assert (
        SolutionRules.model_validate(
            {"max_score": 10, "short_answer": {"accepted_answers": []}}
        ).has_reference_answer()
        is False
    )
    # regex задан, но выключен флагом — сверять всё равно нечем
    assert (
        SolutionRules.model_validate(
            {
                "max_score": 10,
                "short_answer": {"accepted_answers": [], "use_regex": False, "regex": "^\\d+$"},
            }
        ).has_reference_answer()
        is False
    )
    # use_regex включён, но самого выражения нет
    assert (
        SolutionRules.model_validate(
            {"max_score": 10, "short_answer": {"accepted_answers": [], "use_regex": True}}
        ).has_reference_answer()
        is False
    )
    # эталон списком
    assert (
        SolutionRules.model_validate(
            {"max_score": 10, "short_answer": {"accepted_answers": [{"value": "42", "score": 10}]}}
        ).has_reference_answer()
        is True
    )
    # эталон регуляркой
    assert (
        SolutionRules.model_validate(
            {
                "max_score": 10,
                "short_answer": {"accepted_answers": [], "use_regex": True, "regex": "^\\d+$"},
            }
        ).has_reference_answer()
        is True
    )


# ── (б) endpoint отдаёт флаг ────────────────────────────────────────────────


async def test_task_state_has_reference_answer_false_without_reference(client, db):
    """SA_COM без эталона (форма прода после tsk-546) → флаг false."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(
        db, course_id, task_type="SA_COM",
        rules_json='{"max_score":10,"manual_review_required":true,"short_answer":null}',
    )
    try:
        assert (await _state(client, task_id, student_id))["has_reference_answer"] is False
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_task_state_has_reference_answer_false_when_rules_object_empty(client, db):
    """Блок правил есть, но пустой — флаг всё равно false (форма 3 из §6.1)."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(
        db, course_id, task_type="SA_COM",
        rules_json='{"max_score":10,"short_answer":{"accepted_answers":[]}}',
    )
    try:
        assert (await _state(client, task_id, student_id))["has_reference_answer"] is False
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_task_state_has_reference_answer_true_with_reference(client, db):
    """SA_COM с эталоном (форма ОГЭ-14 после tsk-395) → флаг true, поле нужно."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(
        db, course_id, task_type="SA_COM",
        rules_json=(
            '{"max_score":10,"manual_review_required":true,'
            '"short_answer":{"accepted_answers":[{"value":"32 546,82","score":10}]}}'
        ),
    )
    try:
        assert (await _state(client, task_id, student_id))["has_reference_answer"] is True
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (в) типо-зависимость ────────────────────────────────────────────────────


@pytest.mark.parametrize("task_type", ["SC", "TA"])
async def test_task_state_has_reference_answer_true_for_non_short_answer_types(
    client, db, task_type
):
    """У SC/TA блок `short_answer` пуст всегда — но это НЕ «поле ответа не нужно».

    Без типо-зависимого гейта флаг был бы false у всех вариантов выбора и эссе,
    и клиент, доверившись ему буквально, спрятал бы у них ввод ответа.
    """
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    rules = (
        '{"max_score":10,"correct_options":["A"]}'
        if task_type == "SC"
        else '{"max_score":10}'
    )
    task_id = await _make_task(db, course_id, task_type=task_type, rules_json=rules)
    try:
        assert (await _state(client, task_id, student_id))["has_reference_answer"] is True
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (г) сдача с пустым value — то, что будет слать SPW ───────────────────────


async def test_submit_empty_value_with_comment_accepted_on_no_reference_task(client, db):
    """Поле «Ответ» скрыто → `value` пустой, ответ несёт комментарий.

    Это ровно тело запроса, которое SPW отправит при поднятом флаге. Должно
    приниматься и давать оптимистичный зачёт (задание уходит преподавателю),
    а не падать валидацией и не проваливаться в «неверно».
    """
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(
        db, course_id, task_type="SA_COM",
        rules_json='{"max_score":10,"manual_review_required":true,"short_answer":null}',
    )
    resp = await client.post(
        "/api/v1/attempts",
        json={"user_id": student_id, "course_id": course_id, "source_system": "test"},
        headers=_headers(),
    )
    assert resp.status_code == 201, resp.text
    attempt_id = int(resp.json()["id"])
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SA_COM",
                "response": {"value": "", "comment": "нц пока справа свободно\nвправо\nкц"},
            }}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is True, "пустой value не должен мешать зачёту"
        assert result["score"] == 10

        row = (await db.execute(
            text("SELECT checked_at FROM task_results WHERE user_id = :u AND task_id = :t"),
            {"u": student_id, "t": task_id},
        )).fetchone()
        assert row is not None and row[0] is None, "работа обязана остаться в очереди"
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)
