# tests/test_code_review_endpoint_tsk302.py
"""
tsk-302: работа встаёт в очередь на оценку через РЕАЛЬНЫЙ приём ответа.

Зачем отдельный файл. Сервисные тесты проверяют, что `pick_code_for_review`
умеет доставать код из вложения и из комментария, — и они были зелёные, когда
боевой вызов в `attempts.py` не передавал `attempt_id` и поэтому не читал
вложения ВООБЩЕ. Ревью 2026-08-07 нашло это глазами; тестами не ловилось,
потому что ни один тест не ходил через эндпоинт.

Здесь проверяется цепочка целиком: сдача → пометка `code_review.pending` со
снимком кода. Вызовы модели не происходят — тик здесь не запускается.
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

from sqlalchemy import text

from app.core.config import Settings

_TAG = "tsk302-endpoint"


def _headers() -> dict:
    return {"X-API-Key": os.getenv("VALID_API_KEYS", "").split(",")[0].strip()}


async def _make_student(db) -> int:
    return (await db.execute(text(
        "INSERT INTO users (email, full_name, is_active) "
        "VALUES (:e, :n, true) RETURNING id"
    ), {"e": f"{_TAG}-{random.randint(10**8, 10**10)}@example.com", "n": f"{_TAG} ученик"})).scalar_one()


async def _make_course_and_task(db) -> tuple[int, int]:
    course_id = (await db.execute(text(
        "INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"
    ), {"t": f"{_TAG} курс"})).scalar_one()
    difficulty_id = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()
    # Задание БЕЗ пометки `code_ast`/`turtle_sim` — как в реальном курсе.
    task_id = (await db.execute(text(
        "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
        "                   course_id, difficulty_id, order_position) "
        "VALUES (:uid, 10, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, :did, 1) RETURNING id"
    ), {
        "uid": f"{_TAG}-{random.randint(10**8, 10**10)}",
        "c": json.dumps({"type": "SA_COM", "stem": "Напишите программу и впишите её вывод"}),
        "r": json.dumps({
            "max_score": 10,
            "short_answer": {
                "accepted_answers": [{"value": "1 22 333", "score": 10}],
                "normalization": ["trim", "lower"],
            },
        }),
        "cid": course_id, "did": difficulty_id,
    })).scalar_one()
    await db.execute(text(
        "INSERT INTO user_courses (user_id, course_id, is_active) "
        "SELECT id, :c, true FROM users WHERE full_name = :n"
    ), {"c": course_id, "n": f"{_TAG} ученик"})
    await db.commit()
    return course_id, task_id


async def _create_attempt(client, *, student_id: int, course_id: int) -> int:
    resp = await client.post(
        "/api/v1/attempts",
        json={"user_id": student_id, "course_id": course_id, "source_system": "test"},
        headers=_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _read_code_review(db, *, user_id: int, task_id: int):
    return (await db.execute(text(
        "SELECT code_review FROM task_results WHERE user_id = :u AND task_id = :t"
    ), {"u": user_id, "t": task_id})).scalar_one_or_none()


def _cleanup_files(attempt_id: int) -> None:
    directory = Path(Settings().attempt_attachments_upload_dir)
    if directory.exists():
        for path in directory.glob(f"{attempt_id}_*"):
            path.unlink(missing_ok=True)


async def _cleanup(db, *, course_id: int, student_id: int) -> None:
    await db.execute(text("DELETE FROM courses WHERE id = :c"), {"c": course_id})
    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": student_id})
    await db.commit()


async def test_submission_with_code_attachment_is_queued(client, db) -> None:
    """
    Формат «приложи файл, впиши вывод» встаёт в очередь — со снимком кода.

    Это главный сценарий на проде (101 работа у 8 учеников) и ровно тот, что
    молча умирал, пока боевой вызов не передавал `attempt_id`.
    """
    student_id = await _make_student(db)
    course_id, task_id = await _make_course_and_task(db)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        program = b"for i in range(1, 4):\n    print(str(i) * i)\n"
        up = await client.post(
            f"/api/v1/attempts/{attempt_id}/attachments",
            files={"file": ("task8.py", program, "text/x-python")},
            data={"task_id": str(task_id)},
            headers=_headers(),
        )
        assert up.status_code == 201, up.text
        meta = up.json()

        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SA_COM",
                # В ответе — ВЫВОД программы, а не код.
                "response": {"value": "1 22 333", "meta": {"attachments": [meta]}},
            }}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text

        review = await _read_code_review(db, user_id=student_id, task_id=task_id)
        assert review is not None, "работа должна встать в очередь на оценку"
        assert review["status"] == "pending"
        assert "range(1, 4)" in review["code"], (
            "снимок кода снимается при сдаче: файл живёт только до следующей загрузки"
        )
        assert "1 22 333" not in review["code"], "оценивать надо код, а не вывод программы"
    finally:
        _cleanup_files(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_submission_with_code_in_comment_is_queued(client, db) -> None:
    """Код в комментарии — второй реальный формат (370 работ на проде)."""
    student_id = await _make_student(db)
    course_id, task_id = await _make_course_and_task(db)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SA_COM",
                "response": {
                    "value": "1 22 333",
                    "comment": "for i in range(1, 4):\n    print(str(i) * i)\n",
                },
            }}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text

        review = await _read_code_review(db, user_id=student_id, task_id=task_id)
        assert review is not None and review["status"] == "pending"
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_submission_without_code_is_not_queued(client, db) -> None:
    """Обычный ответ без программы модель не тревожит — это стоит денег."""
    student_id = await _make_student(db)
    course_id, task_id = await _make_course_and_task(db)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SA_COM",
                "response": {"value": "1 22 333", "comment": "решал долго, но разобрался"},
            }}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        assert await _read_code_review(db, user_id=student_id, task_id=task_id) is None
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_hostile_attachment_meta_does_not_break_submission(client, db) -> None:
    """
    Враждебные метаданные не мешают ученику сдать задание.

    `meta.attachments` приходит из тела запроса и схемой не проверяется. Оценка
    кода — побочная фича; сломать из-за неё приём ответа нельзя.
    """
    student_id = await _make_student(db)
    course_id, task_id = await _make_course_and_task(db)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SA_COM",
                "response": {
                    "value": "1 22 333",
                    "meta": {"attachments": [{"filename": 5}, "строка", None]},
                },
            }}]},
            headers=_headers(),
        )
        # Главное — сдача ПРИНЯТА, а не упала с 500. Засчитана она или нет,
        # решает отдельное правило «нужен комментарий или файл» (tsk-419), к
        # оценке кода отношения не имеющее.
        assert resp.status_code == 200, resp.text
        assert await _read_code_review(db, user_id=student_id, task_id=task_id) is None
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)
