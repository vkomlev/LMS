"""
Тесты серверного форса вложения `requires_attachment` (tsk-227).

Проектные задания с `solution_rules.requires_attachment=true` не должны
засчитываться без реального вложения в попытке — сервер источник истины.

Покрывают критерии готовности спека tech-spec-tsk227:
- (а) requires_attachment + нет файла → НЕ зачёт (is_correct≠true, score<max);
- (б) requires_attachment + есть файл (реальная загрузка / meta.attachments) → штатный зачёт по слову;
- (в) флаг false/отсутствует → поведение как раньше (регресс);
- (г) SA_COM-миссия (оптимистичный авто-пасс) без вложения НЕ проходит; с вложением — проходит;
- (д) GET /learning/tasks/{id}/state отдаёт requires_attachment клиенту.

Тесты работают с dev-БД (Learn.public) и подчищают за собой.
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


# ── helpers ─────────────────────────────────────────────────────────────────


def _headers() -> dict[str, str]:
    api_key = next(iter(_settings.valid_api_keys))
    return {"X-API-Key": api_key}


async def _make_student(db) -> int:
    email = f"tsk227_{uuid.uuid4().hex[:8]}@example.com"
    r = await db.execute(
        text("INSERT INTO users (email, full_name) VALUES (:e, 'tsk227 student') RETURNING id"),
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
        {"t": f"tsk227 {uuid.uuid4().hex[:8]}"},
    )
    cid = int(r.scalar())
    await db.commit()
    return cid


async def _make_sa_task(
    db, course_id: int, *, requires_attachment: bool, accepted: str = "готово"
) -> int:
    """SA-задача с эталоном `accepted` и флагом requires_attachment."""
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = '{"type":"SA","stem":"Собери проект и приложи скриншот. В ответ напиши: готово"}'
    ra = "true" if requires_attachment else "false"
    sr = (
        '{"max_score":10,"requires_attachment":' + ra + ','
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


async def _make_sa_com_task(db, course_id: int, *, requires_attachment: bool) -> int:
    """SA_COM без short_answer → checking_service возвращает is_correct=None,
    submit включает оптимистичный авто-пасс (is_correct=True). Имитирует
    миссии флагмана (проходятся вводом слова)."""
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = '{"type":"SA_COM","stem":"Собери бота, приложи скриншот. В ответ напиши: сделал"}'
    ra = "true" if requires_attachment else "false"
    sr = '{"max_score":10,"requires_attachment":' + ra + '}'
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


# ── (а) нет вложения → не зачёт ──────────────────────────────────────────────


async def test_requires_attachment_without_file_not_passed(client, db):
    """SA с requires_attachment=true и верным словом, но БЕЗ вложения → не зачёт."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_task(db, course_id, requires_attachment=True)
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
        assert result["is_correct"] is False
        assert result["score"] == 0
        assert result["max_score"] == 10
        # Внятный feedback про вложение
        assert "влож" in (result["feedback"]["general"] or "").lower()
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (б) есть вложение → штатный зачёт ────────────────────────────────────────


async def test_requires_attachment_with_uploaded_file_passes(client, db):
    """SA с requires_attachment=true + реально загруженный файл → зачёт по слову."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_task(db, course_id, requires_attachment=True)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        # Реальная загрузка вложения (детект по файлу {attempt_id}_*)
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


async def test_requires_attachment_forged_meta_without_file_not_passed(client, db):
    """SECURITY (P0): подделанный meta.attachments БЕЗ реально загруженного файла
    НЕ обходит форс — сервер доверяет только файлу на диске ({attempt_id}_*),
    не клиентским данным из тела запроса. Иначе ученик прошёл бы миссию форжем JSON."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_task(db, course_id, requires_attachment=True)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SA",
                "response": {
                    "value": "готово",
                    # Подделка: метаданные вложения без единого файла в upload-dir.
                    "meta": {"attachments": [{"attachment_id": "x", "filename": "p.png"}]},
                },
            }}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        # Форж meta НЕ даёт зачёт — нужен реальный файл.
        assert result["is_correct"] is False
        assert result["score"] == 0
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (в) флаг false → регресс ─────────────────────────────────────────────────


async def test_no_flag_passes_without_attachment_regression(client, db):
    """SA с requires_attachment=false и верным словом без вложения → зачёт как раньше."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_task(db, course_id, requires_attachment=False)
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


# ── (г) оптимистичный SA_COM-пасс перекрыт гейтом ────────────────────────────


async def test_sa_com_optimistic_pass_blocked_without_attachment(client, db):
    """SA_COM-миссия (оптимистичный авто-пасс) с requires_attachment без файла → не зачёт."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_com_task(db, course_id, requires_attachment=True)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SA_COM", "response": {"value": "сделал"}}}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        # Без гейта было бы is_correct=True (оптимистичный пасс). Гейт перекрывает.
        assert result["is_correct"] is False
        assert result["score"] == 0
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_sa_com_optimistic_pass_with_attachment(client, db):
    """SA_COM-миссия с requires_attachment + вложение → оптимистичный авто-пасс сохраняется."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_com_task(db, course_id, requires_attachment=True)
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
                "type": "SA_COM", "response": {"value": "сделал"}}}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is True
        assert result["score"] == 10
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (д) task-state отдаёт флаг клиенту ───────────────────────────────────────


async def test_task_state_returns_requires_attachment_true(client, db):
    """GET /learning/tasks/{id}/state отдаёт requires_attachment=true для флагового задания."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_task(db, course_id, requires_attachment=True)
    try:
        resp = await client.get(
            f"/api/v1/learning/tasks/{task_id}/state?student_id={student_id}",
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["requires_attachment"] is True
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_task_state_requires_attachment_false_by_default(client, db):
    """GET /learning/tasks/{id}/state: задание без флага → requires_attachment=false."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_task(db, course_id, requires_attachment=False)
    try:
        resp = await client.get(
            f"/api/v1/learning/tasks/{task_id}/state?student_id={student_id}",
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["requires_attachment"] is False
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)
