"""tsk-990: черновик развивающего отзыва в карточке проверки.

Покрывает:
- черновик без модели из пунктов рубрики: что получилось / что улучшить / шаг;
- `reject`-пункты и критерии коротких ответов в текст ученику не попадают;
- готовый черновик модели главнее, черновик с ошибкой — нет;
- страж: код и кусок эталона выбрасывают черновик модели;
- grade сохраняет структуру отзыва в `metrics.feedback`.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.services import feedback_draft_service as fds
from tests.test_grade_endpoint_y4 import (
    _cleanup_grade,
    _create_pending_task_result,
    _pick_or_create_task,
    _setup_student_with_email,
    _setup_teacher_with_session,
)


def _rubric(*items, source="text_rubric", **extra):
    return {"source": source, "items": list(items), **extra}


def _item(id_, title, met, kind="must"):
    return {"id": id_, "title": title, "met": met, "kind": kind, "max_score": 1, "evidence": ""}


def test_rubric_draft_splits_met_and_missed() -> None:
    draft = fds.build_rubric_draft(_rubric(
        _item("c1", "Названы 2 команды", "yes"),
        _item("c2", "Объяснено, почему прибор исполнитель", "no"),
        _item("c3", "Приведён пример", "unclear"),
    ))
    assert draft["source"] == "rubric"
    assert "Названы 2 команды" in draft["strengths"]
    assert "Объяснено, почему прибор исполнитель" in draft["improve"]
    assert "Приведён пример" in draft["improve"]
    assert "Названы 2 команды" not in draft["improve"]
    assert "Объяснено, почему прибор исполнитель" in draft["next_step"]


def test_rubric_draft_all_met_has_no_improve() -> None:
    draft = fds.build_rubric_draft(_rubric(_item("c1", "Названы 2 команды", "yes")))
    assert draft["improve"] == ""
    assert "следующему заданию" in draft["next_step"]


def test_reject_items_never_reach_student_text() -> None:
    draft = fds.build_rubric_draft(_rubric(
        _item("c1", "Названы 2 команды", "yes"),
        _item("r1", "Перепутаны ввод и вывод", "yes", kind="reject"),
    ))
    assert "Перепутаны" not in json.dumps(draft, ensure_ascii=False)


@pytest.mark.parametrize("rubric", [
    None,
    {},
    _rubric(),
    _rubric(_item("c1", "Ответ — 42", "yes"), source="grading_criteria"),
    _rubric(_item("c1", "x", "yes"), error="Timeout"),
])
def test_no_draft_when_nothing_safe_to_build_from(rubric) -> None:
    assert fds.build_rubric_draft(rubric) is None


def test_llm_draft_preferred_and_broken_one_ignored() -> None:
    rubric = _rubric(_item("c1", "Названы 2 команды", "yes"))
    ready = fds.draft_for_review({
        "rubric_review": rubric,
        "feedback_draft": {"strengths": "Ты верно назвал", "improve": "", "next_step": "Дальше"},
    })
    assert ready["source"] == "llm" and ready["strengths"] == "Ты верно назвал"
    fallback = fds.draft_for_review({"rubric_review": rubric, "feedback_draft": {"error": "leak_guard"}})
    assert fallback["source"] == "rubric"
    assert fds.draft_for_review(None) is None


REFERENCE = "Микроволновка исполняет команды разогреть и разморозить, но не умеет жарить"


def test_leak_guard_catches_code_and_reference() -> None:
    assert fds._leaks_solution("Попробуй так:\n```\nprint(1)\n```", [])
    assert fds._leaks_solution("for i in range(3):\n    pass", [])
    assert fds._leaks_solution(
        "Напиши: микроволновка исполняет команды разогреть и разморозить", [REFERENCE]
    )
    assert not fds._leaks_solution("В следующий раз добавь пример прибора-исполнителя.", [REFERENCE])


async def test_llm_draft_dropped_on_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_complete(messages, **kwargs):
        assert kwargs["purpose"] == fds.FEEDBACK_PURPOSE
        payload = json.dumps({
            "strengths": "Молодец",
            "improve": "Надо было написать: " + REFERENCE,
            "next_step": "Дальше",
        }, ensure_ascii=False)
        return type("R", (), {"text": payload, "model": "test-model"})()

    monkeypatch.setattr(fds, "complete", _fake_complete)
    out = await fds.write_llm_draft(
        answer_text="ответ ученика", rubric=_rubric(_item("c1", "Названы 2 команды", "no")),
        task_stem="Условие", solution_rules={"reference_answer": REFERENCE}, student_id=None,
    )
    assert out["feedback_draft"]["error"] == "leak_guard"


async def test_llm_draft_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_complete(messages, **kwargs):
        payload = json.dumps({"strengths": "Команды названы", "improve": "Добавь пример", "next_step": "Дальше"},
                             ensure_ascii=False)
        return type("R", (), {"text": payload, "model": "test-model"})()

    monkeypatch.setattr(fds, "complete", _fake_complete)
    out = await fds.write_llm_draft(
        answer_text="ответ", rubric=_rubric(_item("c1", "Названы 2 команды", "yes")),
        task_stem=None, solution_rules={}, student_id=None,
    )
    assert out["feedback_draft"] == {
        "strengths": "Команды названы", "improve": "Добавь пример", "next_step": "Дальше", "model": "test-model",
    }


@pytest.mark.asyncio
async def test_grade_stores_feedback_structure(db, client):
    task_id, course_id = await _pick_or_create_task(db)
    teacher_id, token = await _setup_teacher_with_session(db, course_id=course_id)
    student_id, _ = await _setup_student_with_email(db)
    rid, lock_token, _ = await _create_pending_task_result(
        db, student_id=student_id, task_id=task_id, teacher_id=teacher_id, max_score=10,
    )
    feedback = {
        "strengths": "Названы команды", "improve": "Добавь пример", "next_step": "Дальше",
        "draft_source": "rubric", "draft_edited": True,
    }
    try:
        resp = await client.post(
            f"/api/v1/teacher/reviews/{rid}/grade",
            json={"teacher_id": teacher_id, "lock_token": lock_token, "score": 8,
                  "comment": "Названы команды\n\nДобавь пример", "feedback": feedback},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        metrics = (await db.execute(
            text("SELECT metrics FROM task_results WHERE id=:r"), {"r": rid},
        )).scalar()
        assert metrics["feedback"] == feedback
        assert metrics["comment"].startswith("Названы команды")
    finally:
        await _cleanup_grade(db, result_id=rid, student_id=student_id, teacher_id=teacher_id)
