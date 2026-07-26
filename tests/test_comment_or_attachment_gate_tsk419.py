"""
Тесты серверного форса "комментарий или файл" для SA_COM/TBL_COM (tsk-419).

Решение оператора (2026-07-26, по итогам приёмки QA tsk-414): часть SA_COM-заданий
решается устно/подбором без доказательства хода решения (пример «курсор→танцор»,
id-149) — ответ подбирается без единой строчки кода. Для ВСЕХ SA_COM и TBL_COM
(не per-task флаг, как requires_attachment в tsk-227, а универсальное правило по
типу задания) обязательно заполнить комментарий ИЛИ приложить файл — иначе ответ
не засчитывается, с явным сообщением.

Покрывает:
- (а) SA_COM с верным value, БЕЗ comment и БЕЗ файла → не зачёт;
- (б) SA_COM с верным value + comment (без файла) → зачёт;
- (в) SA_COM с верным value + файл (без comment) → зачёт;
- (г) TBL_COM — то же поведение, что SA_COM;
- (д) SA (без комментария в принципе) не затронут — регресс;
- (е) requires_attachment важнее (сообщение 2.3e, не 2.3f), если оба условия не выполнены.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.api.v1.attempts import _attempt_attachment_files

pytestmark = pytest.mark.asyncio

_settings = Settings()


def _headers() -> dict[str, str]:
    api_key = next(iter(_settings.valid_api_keys))
    return {"X-API-Key": api_key}


async def _make_student(db) -> int:
    email = f"tsk419_{uuid.uuid4().hex[:8]}@example.com"
    r = await db.execute(
        text("INSERT INTO users (email, full_name) VALUES (:e, 'tsk419 student') RETURNING id"),
        {"e": email},
    )
    sid = int(r.scalar())
    await db.commit()
    return sid


async def _make_course(db) -> int:
    r = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"),
        {"t": f"tsk419 {uuid.uuid4().hex[:8]}"},
    )
    cid = int(r.scalar())
    await db.commit()
    return cid


async def _make_task(db, course_id: int, *, task_type: str, accepted: str = "готово") -> int:
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = '{"type":"' + task_type + '","stem":"Тестовое задание. Ответ: ' + accepted + '"}'
    sr = (
        '{"max_score":10,"requires_attachment":false,'
        '"short_answer":{"normalization":["trim","lower"],'
        '"accepted_answers":[{"value":"' + accepted + '","score":10}]}}'
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


async def _make_requires_attachment_task(db, course_id: int, *, accepted: str = "готово") -> int:
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = '{"type":"SA_COM","stem":"Тестовое задание. Ответ: ' + accepted + '"}'
    sr = (
        '{"max_score":10,"requires_attachment":true,'
        '"short_answer":{"normalization":["trim","lower"],'
        '"accepted_answers":[{"value":"' + accepted + '","score":10}]}}'
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
    for path in _attempt_attachment_files(attempt_id):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


async def _cleanup(db, *, course_id: int, student_id: int) -> None:
    await db.execute(text("DELETE FROM courses WHERE id = :cid"), {"cid": course_id})
    await db.execute(text("DELETE FROM users WHERE id = :sid"), {"sid": student_id})
    await db.commit()


# ── (а) без comment и без файла → не зачёт ──────────────────────────────────


async def test_sa_com_correct_value_without_comment_or_attachment_not_passed(client, db):
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id, task_type="SA_COM")
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SA_COM", "response": {"value": "готово"}}}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is False
        assert result["score"] == 0
        assert "коммент" in (result["feedback"]["general"] or "").lower()
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (б) с комментарием (без файла) → зачёт ──────────────────────────────────


async def test_sa_com_correct_value_with_comment_passes(client, db):
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id, task_type="SA_COM")
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SA_COM",
                "response": {"value": "готово", "comment": "мой код решения"},
            }}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is True
        assert result["score"] == 10
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_sa_com_blank_comment_treated_as_missing(client, db):
    """Комментарий из одних пробелов не должен обходить гейт."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id, task_type="SA_COM")
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SA_COM",
                "response": {"value": "готово", "comment": "   "},
            }}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is False
        assert result["score"] == 0
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (в) с файлом (без comment) → зачёт ──────────────────────────────────────


async def test_sa_com_correct_value_with_attachment_passes(client, db):
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id, task_type="SA_COM")
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        up = await client.post(
            f"/api/v1/attempts/{attempt_id}/attachments",
            files={"file": ("proof.png", png, "image/png")},
            headers=_headers(),
        )
        assert up.status_code == 201, up.text

        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SA_COM", "response": {"value": "готово"}}}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is True
        assert result["score"] == 10
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (г) TBL_COM — то же поведение ───────────────────────────────────────────


async def test_tbl_com_without_comment_or_attachment_not_passed(client, db):
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id, task_type="TBL_COM", accepted="1 2")
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "TBL_COM", "response": {"value": "1 2"}}}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is False
        assert result["score"] == 0
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_tbl_com_with_comment_passes(client, db):
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id, task_type="TBL_COM", accepted="1 2")
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "TBL_COM",
                "response": {"value": "1 2", "comment": "код решения"},
            }}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is True
        assert result["score"] == 10
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (д) SA не затронут — регресс ────────────────────────────────────────────


async def test_sa_type_not_affected_by_gate(client, db):
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id, task_type="SA")
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SA", "response": {"value": "готово"}}}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is True
        assert result["score"] == 10
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (е) requires_attachment важнее (не задвоенное сообщение) ────────────────


async def test_requires_attachment_message_takes_priority_over_comment_gate(client, db):
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_requires_attachment_task(db, course_id)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SA_COM",
                "response": {"value": "готово", "comment": "код решения"},
            }}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is False
        assert result["score"] == 0
        # Сообщение про вложение (tsk-227), не про комментарий — обязательное
        # вложение важнее и присутствует его собственное сообщение.
        assert "влож" in (result["feedback"]["general"] or "").lower()
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)
