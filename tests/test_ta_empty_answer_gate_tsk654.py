"""
Тесты серверного гейта непустоты для развёрнутого ответа TA (tsk-654).

Находка при разборе боевой базы (попутно в tsk-646): гейт 2.3d приёма ответа
ставит TA полный балл БЕЗУСЛОВНО («сверять нечем, вердикт даст преподаватель»),
поэтому пустой текст получал `score=max_score, is_correct=True`. Единственной
защитой были клиенты (SPW `TaskFormTA.canSubmit`, TG_LMS `has_answer`), серверной
проверки не было ни одной — прямой вызов API проходил на полный балл. Тот же
класс, что tsk-419 закрыл для SA_COM/TBL_COM, и та же форма правила: текст ИЛИ
реально загруженный файл.

Покрывает:
- (а) TA с пустым текстом → не зачёт;
- (б) TA с текстом из одних пробелов → не зачёт (пробельная строка не ответ);
- (в) TA без поля text вовсе → не зачёт;
- (г) TA с непустым текстом → зачёт на полный балл (регресс 2.3d);
- (д) TA без текста, но с реально загруженным файлом → зачёт;
- (е) SA с пустым value не затронут — регресс (у него свой путь проверки);
- (ж) у TA с `requires_attachment` важнее сообщение 2.3e, а не гейта 2.3g.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from app.core.config import Settings

pytestmark = pytest.mark.asyncio

_settings = Settings()


def _headers() -> dict[str, str]:
    api_key = next(iter(_settings.valid_api_keys))
    return {"X-API-Key": api_key}


async def _make_student(db) -> int:
    email = f"tsk654_{uuid.uuid4().hex[:8]}@example.com"
    r = await db.execute(
        text("INSERT INTO users (email, full_name) VALUES (:e, 'tsk654 student') RETURNING id"),
        {"e": email},
    )
    sid = int(r.scalar())
    await db.commit()
    return sid


async def _make_course(db) -> int:
    r = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"),
        {"t": f"tsk654 {uuid.uuid4().hex[:8]}"},
    )
    cid = int(r.scalar())
    await db.commit()
    return cid


async def _make_ta_task(db, course_id: int) -> int:
    """TA-задание в том же виде, в каком они лежат на проде: эталона нет, max_score=6."""
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = '{"type":"TA","stem":"Опишите своими словами, как работает цикл while."}'
    sr = '{"max_score":6,"requires_attachment":false,"penalties":{"missing_answer":0}}'
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


async def _make_sa_task(db, course_id: int, *, accepted: str = "готово") -> int:
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = '{"type":"SA","stem":"Ответ: ' + accepted + '"}'
    sr = (
        '{"max_score":6,"requires_attachment":false,'
        '"short_answer":{"normalization":["trim","lower"],'
        '"accepted_answers":[{"value":"' + accepted + '","score":6}]}}'
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


def _cleanup_attachments(attempt_id: int) -> None:
    """Убрать файлы попытки (в тестах хранилище работает в режиме диска, tsk-593)."""
    for path in Settings().attempt_attachments_upload_dir.glob(f"{attempt_id}_*"):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


async def _cleanup(db, *, course_id: int, student_id: int) -> None:
    await db.execute(text("DELETE FROM courses WHERE id = :cid"), {"cid": course_id})
    await db.execute(text("DELETE FROM users WHERE id = :sid"), {"sid": student_id})
    await db.commit()


async def _answer(client, attempt_id: int, task_id: int, response: dict):
    return await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": task_id, "answer": {"type": "TA", "response": response}}]},
        headers=_headers(),
    )


# ── (а) пустой текст → не зачёт ─────────────────────────────────────────────


async def test_ta_empty_text_not_passed(client, db):
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_ta_task(db, course_id)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await _answer(client, attempt_id, task_id, {"text": ""})
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is False
        assert result["score"] == 0
        assert "пуст" in (result["feedback"]["general"] or "").lower()
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (б) пробельный текст → не зачёт ─────────────────────────────────────────


async def test_ta_blank_text_treated_as_missing(client, db):
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_ta_task(db, course_id)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await _answer(client, attempt_id, task_id, {"text": "   \n\t  "})
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is False
        assert result["score"] == 0
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (в) поля text нет вовсе → не зачёт ──────────────────────────────────────


async def test_ta_missing_text_field_not_passed(client, db):
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_ta_task(db, course_id)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await _answer(client, attempt_id, task_id, {})
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is False
        assert result["score"] == 0
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (г) непустой текст → зачёт (регресс оптимистичного пасса 2.3d) ──────────


async def test_ta_with_text_still_passes(client, db):
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_ta_task(db, course_id)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await _answer(
            client, attempt_id, task_id,
            {"text": "Цикл while повторяет тело, пока условие истинно."},
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is True
        assert result["score"] == 6
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (д) без текста, но с файлом → зачёт ─────────────────────────────────────


async def test_ta_without_text_but_with_attachment_passes(client, db):
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_ta_task(db, course_id)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        up = await client.post(
            f"/api/v1/attempts/{attempt_id}/attachments",
            files={"file": ("work.png", png, "image/png")},
            headers=_headers(),
        )
        assert up.status_code == 201, up.text

        resp = await _answer(client, attempt_id, task_id, {"text": ""})
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is True
        assert result["score"] == 6
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (е) SA не затронут — регресс ────────────────────────────────────────────


async def test_sa_empty_value_not_affected_by_ta_gate(client, db):
    """У SA пустой ответ и так неверен — но сообщением гейта TA его быть не должно."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_task(db, course_id)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SA", "response": {"value": ""}}}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["score"] == 0
        general = ((result.get("feedback") or {}).get("general") or "").lower()
        assert "приложите файл с работой" not in general
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (ж) requires_attachment важнее: сообщение 2.3e, не 2.3g ─────────────────


async def _make_ta_task_requires_attachment(db, course_id: int) -> int:
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = '{"type":"TA","stem":"Приложите скриншот работы и опишите ход решения."}'
    sr = '{"max_score":6,"requires_attachment":true,"penalties":{"missing_answer":0}}'
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


async def test_ta_requires_attachment_message_wins_over_empty_text(client, db):
    """У TA с обязательным вложением и без файла важнее точная причина «нужен файл»."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_ta_task_requires_attachment(db, course_id)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await _answer(client, attempt_id, task_id, {"text": ""})
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is False
        assert result["score"] == 0
        general = (result["feedback"]["general"] or "").lower()
        assert "файл-подтверждение" in general
        assert "пустой ответ не засчитывается" not in general
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)
