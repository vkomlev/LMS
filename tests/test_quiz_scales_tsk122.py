"""
Тесты квиз-вопросов со шкалами (tsk-122, ADR-0003, Stage 1).

Покрывают:
- валидацию схем SC_Qw/MC_Qw (объявление scales, scores с ключами из scales,
  секция quiz в solution_rules, согласованность scales и mode);
- scoring CheckingService: вклад по шкалам, отсутствие pass/fail, ограничение
  SC_Qw на один вариант;
- персистентность task_result.scale_scores через API (POST /attempts/.../answers).

Юнит-тесты (схемы/scoring) не требуют БД. Интеграционный тест создаёт данные в
dev-БД (Learn.public) и подчищает за собой каскадом от курса/ученика.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.schemas.checking import StudentAnswer, StudentResponse
from app.schemas.solution_rules import SolutionRules
from app.schemas.task_content import TaskContent
from app.services.checking_service import CheckingService
from app.utils.exceptions import DomainError

# asyncio_mode=auto (pytest.ini) автодетектит async-тесты; модульный маркер не нужен,
# иначе он навешивается и на синхронные unit-тесты схем/scoring.
_settings = Settings()
_cs = CheckingService()


# --------------------------- Юнит: валидация схем ---------------------------


def test_sc_qw_valid_content_and_rules():
    """Корректный SC_Qw: scales объявлены, scores ссылаются на них, quiz.mode=single."""
    tc = TaskContent.model_validate(
        {
            "type": "SC_Qw",
            "stem": "Что тебе ближе?",
            "scales": ["информатика", "python"],
            "options": [
                {"id": "a", "text": "разгадывать", "scores": {"информатика": 2}},
                {"id": "b", "text": "игры и боты", "scores": {"python": 2}},
                {"id": "c", "text": "и то и то", "scores": {"информатика": 1, "python": 1}},
            ],
        }
    )
    sr = SolutionRules.model_validate(
        {"max_score": 2, "quiz": {"scales": ["информатика", "python"], "mode": "single"}}
    )
    sr.validate_with_task_content(tc)  # не должно бросить


def test_quiz_unknown_scale_rejected():
    """scores с необъявленной шкалой → ошибка валидации task_content."""
    with pytest.raises(ValueError):
        TaskContent.model_validate(
            {
                "type": "SC_Qw",
                "stem": "x",
                "scales": ["информатика"],
                "options": [
                    {"id": "a", "text": "a", "scores": {"python": 1}},
                    {"id": "b", "text": "b"},
                ],
            }
        )


def test_quiz_requires_scales():
    """SC_Qw без объявления scales → ошибка валидации."""
    with pytest.raises(ValueError):
        TaskContent.model_validate(
            {
                "type": "MC_Qw",
                "stem": "x",
                "options": [
                    {"id": "a", "text": "a"},
                    {"id": "b", "text": "b"},
                ],
            }
        )


def test_quiz_rules_scales_mismatch_rejected():
    """quiz.scales должны совпадать с task_content.scales."""
    tc = TaskContent.model_validate(
        {
            "type": "SC_Qw",
            "stem": "x",
            "scales": ["информатика", "python"],
            "options": [
                {"id": "a", "text": "a", "scores": {"информатика": 1}},
                {"id": "b", "text": "b", "scores": {"python": 1}},
            ],
        }
    )
    sr = SolutionRules.model_validate(
        {"max_score": 1, "quiz": {"scales": ["информатика"], "mode": "single"}}
    )
    with pytest.raises(ValueError):
        sr.validate_with_task_content(tc)


def test_quiz_rules_mode_mismatch_rejected():
    """SC_Qw требует quiz.mode=single, MC_Qw → multiple."""
    tc = TaskContent.model_validate(
        {
            "type": "SC_Qw",
            "stem": "x",
            "scales": ["python"],
            "options": [
                {"id": "a", "text": "a", "scores": {"python": 1}},
                {"id": "b", "text": "b", "scores": {"python": 2}},
            ],
        }
    )
    sr = SolutionRules.model_validate(
        {"max_score": 2, "quiz": {"scales": ["python"], "mode": "multiple"}}
    )
    with pytest.raises(ValueError):
        sr.validate_with_task_content(tc)


def test_quiz_rules_missing_quiz_section_rejected():
    """SC_Qw без секции quiz в solution_rules → ошибка."""
    tc = TaskContent.model_validate(
        {
            "type": "SC_Qw",
            "stem": "x",
            "scales": ["python"],
            "options": [
                {"id": "a", "text": "a", "scores": {"python": 1}},
                {"id": "b", "text": "b", "scores": {"python": 2}},
            ],
        }
    )
    sr = SolutionRules.model_validate({"max_score": 2})
    with pytest.raises(ValueError):
        sr.validate_with_task_content(tc)


# --------------------------- Юнит: scoring ---------------------------


def _sc_qw_fixture() -> tuple[TaskContent, SolutionRules]:
    tc = TaskContent.model_validate(
        {
            "type": "SC_Qw",
            "stem": "Что тебе ближе?",
            "scales": ["информатика", "python"],
            "options": [
                {"id": "a", "text": "разгадывать", "scores": {"информатика": 2}},
                {"id": "b", "text": "игры", "scores": {"python": 2}},
                {"id": "c", "text": "оба", "scores": {"информатика": 1, "python": 1}},
            ],
        }
    )
    sr = SolutionRules.model_validate(
        {"max_score": 2, "quiz": {"scales": ["информатика", "python"], "mode": "single"}}
    )
    return tc, sr


def test_sc_qw_scoring_single_choice():
    """SC_Qw: выбор варианта 'a' даёт {информатика:2, python:0}, без is_correct."""
    tc, sr = _sc_qw_fixture()
    res = _cs.check_task(
        tc, sr, StudentAnswer(type="SC_Qw", response=StudentResponse(selected_option_ids=["a"]))
    )
    assert res.scale_scores == {"информатика": 2, "python": 0}
    assert res.is_correct is None
    # Отвеченный квиз = выполненная задача: score=max_score (прогрессия Learning Engine).
    assert res.score == 2
    assert res.max_score == 2


def test_sc_qw_empty_answer_zero_scales():
    """SC_Qw без выбора: все объявленные шкалы по нулям, задача не выполнена (score=0)."""
    tc, sr = _sc_qw_fixture()
    res = _cs.check_task(
        tc, sr, StudentAnswer(type="SC_Qw", response=StudentResponse(selected_option_ids=[]))
    )
    assert res.scale_scores == {"информатика": 0, "python": 0}
    assert res.score == 0


def test_sc_qw_two_selected_rejected():
    """SC_Qw: выбор более одного варианта → DomainError 400."""
    tc, sr = _sc_qw_fixture()
    with pytest.raises(DomainError):
        _cs.check_task(
            tc,
            sr,
            StudentAnswer(type="SC_Qw", response=StudentResponse(selected_option_ids=["a", "b"])),
        )


def test_mc_qw_scoring_sums_selected():
    """MC_Qw: суммирует баллы всех выбранных вариантов по шкалам."""
    tc = TaskContent.model_validate(
        {
            "type": "MC_Qw",
            "stem": "x",
            "scales": ["информатика", "python"],
            "options": [
                {"id": "a", "text": "a", "scores": {"информатика": 1}},
                {"id": "b", "text": "b", "scores": {"python": 1}},
                {"id": "c", "text": "c", "scores": {"информатика": 1, "python": 1}},
            ],
        }
    )
    sr = SolutionRules.model_validate(
        {"max_score": 3, "quiz": {"scales": ["информатика", "python"], "mode": "multiple"}}
    )
    res = _cs.check_task(
        tc,
        sr,
        StudentAnswer(type="MC_Qw", response=StudentResponse(selected_option_ids=["a", "c"])),
    )
    assert res.scale_scores == {"информатика": 2, "python": 1}
    assert res.is_correct is None


# --------------------------- Интеграция: персистентность ---------------------------


async def _make_student(db) -> int:
    email = f"quiz_{uuid.uuid4().hex[:8]}@example.com"
    r = await db.execute(
        text("INSERT INTO users (email, full_name) VALUES (:e, 'tsk122 student') RETURNING id"),
        {"e": email},
    )
    sid = int(r.scalar())
    await db.commit()
    return sid


async def _make_course(db) -> int:
    r = await db.execute(
        text(
            "INSERT INTO courses (title, access_level) "
            "VALUES (:t, 'auto_check') RETURNING id"
        ),
        {"t": f"tsk122 {uuid.uuid4().hex[:8]}"},
    )
    cid = int(r.scalar())
    await db.commit()
    return cid


async def _make_quiz_task(db, course_id: int) -> int:
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = (
        '{"type":"SC_Qw","stem":"Что ближе?","scales":["информатика","python"],'
        '"options":[{"id":"a","text":"разгадывать","scores":{"информатика":2}},'
        '{"id":"b","text":"игры","scores":{"python":2}}]}'
    )
    sr = '{"max_score":2,"quiz":{"scales":["информатика","python"],"mode":"single"}}'
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


async def _cleanup(db, *, course_id: int, student_id: int) -> None:
    await db.execute(text("DELETE FROM courses WHERE id = :cid"), {"cid": course_id})
    await db.execute(text("DELETE FROM users WHERE id = :sid"), {"sid": student_id})
    await db.commit()


async def test_quiz_answer_persists_scale_scores(client, db):
    """POST /attempts/{id}/answers по SC_Qw пишет task_results.scale_scores."""
    api_key = next(iter(_settings.valid_api_keys))
    headers = {"X-API-Key": api_key}
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_quiz_task(db, course_id)
    try:
        resp = await client.post(
            "/api/v1/attempts",
            json={"user_id": student_id, "course_id": course_id, "source_system": "test"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        attempt_id = resp.json()["id"]

        resp2 = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={
                "items": [
                    {
                        "task_id": task_id,
                        "answer": {
                            "type": "SC_Qw",
                            "response": {"selected_option_ids": ["a"]},
                        },
                    }
                ]
            },
            headers=headers,
        )
        assert resp2.status_code == 200, resp2.text

        row = (
            await db.execute(
                text(
                    "SELECT scale_scores, is_correct, score FROM task_results "
                    "WHERE task_id = :tid AND user_id = :uid"
                ),
                {"tid": task_id, "uid": student_id},
            )
        ).first()
        assert row is not None
        assert row[0] == {"информатика": 2, "python": 0}
        assert row[1] is None  # квиз не pass/fail (is_correct)
        assert row[2] == 2     # отвеченный квиз = выполнен: score=max_score
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_quiz_task_completes_course_progression(client, db):
    """Прогрессия: отвеченный квиз → задача PASSED, курс COMPLETED (не зацикливается).

    Регрессия на блокер ревью 2026-06-27: Learning Engine гейтит прохождение по
    score/max_score; при score=0 квиз был бы вечно FAILED и курс не завершался.
    """
    from app.services.learning_engine_service import LearningEngineService

    api_key = next(iter(_settings.valid_api_keys))
    headers = {"X-API-Key": api_key}
    le = LearningEngineService()
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_quiz_task(db, course_id)  # единственная required-задача курса
    try:
        resp = await client.post(
            "/api/v1/attempts",
            json={"user_id": student_id, "course_id": course_id, "source_system": "test"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        attempt_id = resp.json()["id"]

        resp2 = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id,
                             "answer": {"type": "SC_Qw",
                                        "response": {"selected_option_ids": ["a"]}}}]},
            headers=headers,
        )
        assert resp2.status_code == 200, resp2.text

        task_state = await le.compute_task_state(db, student_id, task_id)
        assert task_state.state == "PASSED"

        course_state = await le.compute_course_state(db, student_id, course_id, update_state_table=False)
        assert course_state.state == "COMPLETED"
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)
