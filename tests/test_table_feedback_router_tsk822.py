"""
tsk-822 на слое РОУТЕРА: доходит ли «совпало N из M» до ученика через API.

Зачем отдельно от юнит-тестов. Реестр ошибок LMS (`docs/ai/ERRORS.md`, запись
2026-08-08 по tsk-396) прямо требует: юнит-тест на `CheckingService` НЕ ловит
переопределение результата на слое роутера. Здесь это не теория — TBL_COM входит
в `COMMENT_TASK_TYPES`, и гейт 2.3f (tsk-419) ЗАТИРАЕТ `check_result` целиком,
включая обратную связь, если ученик сдал без комментария и без файла.

Поэтому фиксируем обе стороны развилки:
- с комментарием — ученик видит, сколько рядов совпало;
- без комментария и файла — видит требование гейта, а не подсчёт (так и надо:
  такой ответ вообще не оценивается, и точная причина важнее).

Тесты работают с dev-БД (Learn.public) и подчищают за собой.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.core.config import Settings

pytestmark = pytest.mark.asyncio

_settings = Settings()

#: Эталон из четырёх строк — форма заданий ЕГЭ 19-21 (курс 147).
REFERENCE = "20\n34\n38\n33"
#: Три строки из четырёх верны — случай, ради которого правка и делалась.
ALMOST = "20\n34\n38\n99"


def _headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


async def _make_student(db) -> int:
    r = await db.execute(
        text("INSERT INTO users (email, full_name) VALUES (:e, 'tsk822 student') RETURNING id"),
        {"e": f"tsk822_{uuid.uuid4().hex[:8]}@example.com"},
    )
    sid = int(r.scalar())
    await db.commit()
    return sid


async def _make_course(db) -> int:
    r = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"),
        {"t": f"tsk822 {uuid.uuid4().hex[:8]}"},
    )
    cid = int(r.scalar())
    await db.commit()
    return cid


async def _make_task(db, course_id: int) -> int:
    """TBL_COM в том же виде, что 306 активных заданий прода: all_or_nothing, max_score=1."""
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = '{"type":"TBL_COM","stem":"Задания 19-21","table":{"columns":1}}'
    sr = (
        '{"max_score":1,"scoring_mode":"all_or_nothing",'
        '"short_answer":{"normalization":["trim","lower"],'
        '"accepted_answers":[{"value":"' + REFERENCE.replace("\n", "\\n") + '","score":1}]}}'
    )
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


async def _submit(client, attempt_id: int, task_id: int, value: str, comment: str | None):
    response: dict = {"value": value}
    if comment is not None:
        response["comment"] = comment
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": task_id, "answer": {
            "type": "TBL_COM",
            "response": response,
        }}]},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]["check_result"]


async def _cleanup(db, *, course_id: int, student_id: int) -> None:
    await db.execute(text("DELETE FROM courses WHERE id = :cid"), {"cid": course_id})
    await db.execute(text("DELETE FROM users WHERE id = :sid"), {"sid": student_id})
    await db.commit()


async def test_ученик_видит_число_совпавших_через_api(client, db):
    """Главное: подсчёт доживает до ответа API, а не теряется в роутере."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    try:
        task_id = await _make_task(db, course_id)
        attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)

        result = await _submit(client, attempt_id, task_id, ALMOST, comment="перебрал позиции игры")

        assert result["is_correct"] is False
        assert result["score"] == 0  # оценивание не изменилось
        assert "Совпало 3 значения из 4" in (result["feedback"]["general"] or "")
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_без_комментария_гейт_важнее_подсчёта(client, db):
    """TBL_COM ∈ COMMENT_TASK_TYPES: гейт tsk-419 затирает результат целиком.

    Это не дефект правки, а порядок приоритетов: ответ без комментария и файла
    не оценивается вообще, и ученику важнее узнать именно это.
    """
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    try:
        task_id = await _make_task(db, course_id)
        attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)

        result = await _submit(client, attempt_id, task_id, ALMOST, comment=None)

        general = result["feedback"]["general"] or ""
        assert "комментарий" in general.lower()
        assert "Совпало" not in general
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_полный_ответ_засчитывается_как_прежде(client, db):
    """Инвариант: зачёт по-прежнему только за полный ответ."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    try:
        task_id = await _make_task(db, course_id)
        attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)

        result = await _submit(client, attempt_id, task_id, REFERENCE, comment="решил перебором")

        assert result["is_correct"] is True
        assert result["score"] == 1
        assert "Совпало" not in (result["feedback"]["general"] or "")
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)
