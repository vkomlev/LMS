"""
Вложение живёт на пару «попытка + задание», а не на попытку целиком (tsk-575).

Дефект: загрузка нового файла удаляла ВСЕ прежние файлы попытки. Попытка
охватывает много заданий, поэтому ученик, сдав задание 1 с `task1.py` и взявшись
за задание 2, стирал файл первого — и ссылка в уже сданной работе вела в никуда.
На проде так утрачено 180 файлов из 205 (201 работа у 13 учеников, 2026-08-07).

Покрываем:
- (а) загрузка по ДРУГОМУ заданию той же попытки не трогает первый файл;
- (б) перезаливка по ТОМУ ЖЕ заданию заменяет прежний;
- (в) обратная совместимость: загрузка без `task_id` (старый клиент) не сносит
      файлы, помеченные заданием, и наоборот;
- (г) скачивание файла, которого нет на диске, отдаёт 410 с внятным текстом;
- (е) гейт `requires_attachment` смотрит файл ЭТОГО задания, а не любой файл
      попытки, но продолжает засчитывать файлы без метки (старый клиент).
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.services.attempt_attachments import (
    build_attachment_id,
    parse_attachment_id,
)

pytestmark = pytest.mark.asyncio

_settings = Settings()


# ── helpers ─────────────────────────────────────────────────────────────────


def _headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


async def _make_student(db) -> int:
    r = await db.execute(
        text("INSERT INTO users (email, full_name) VALUES (:e, 'tsk575 student') RETURNING id"),
        {"e": f"tsk575_{uuid.uuid4().hex[:8]}@example.com"},
    )
    sid = int(r.scalar())
    await db.commit()
    return sid


async def _make_course(db) -> int:
    r = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"),
        {"t": f"tsk575 {uuid.uuid4().hex[:8]}"},
    )
    cid = int(r.scalar())
    await db.commit()
    return cid


async def _make_sa_task(db, course_id: int, *, requires_attachment: bool) -> int:
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = '{"type":"SA","stem":"Приложи файл. В ответ напиши: готово"}'
    ra = "true" if requires_attachment else "false"
    sr = (
        '{"max_score":10,"requires_attachment":' + ra + ','
        '"short_answer":{"normalization":["trim","lower"],'
        '"accepted_answers":[{"value":"готово","score":10}]}}'
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


async def _upload(client, attempt_id: int, name: str, *, task_id: int | None = None):
    data = {"task_id": str(task_id)} if task_id is not None else None
    return await client.post(
        f"/api/v1/attempts/{attempt_id}/attachments",
        files={"file": (name, f"print('{name}')".encode(), "text/x-python")},
        data=data,
        headers=_headers(),
    )


def _cleanup_attachments(attempt_id: int) -> None:
    """Убрать файлы попытки.

    tsk-593: тесты идут БЕЗ настроенного S3, то есть хранилище работает в
    режиме диска — поэтому уборка ходит прямо в каталог, а не через
    асинхронный слой хранилища (его нельзя ждать в синхронном `finally`).
    """
    for path in _settings.attempt_attachments_upload_dir.glob(f"{attempt_id}_*"):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


async def _cleanup(db, *, course_id: int, student_id: int) -> None:
    await db.execute(text("DELETE FROM courses WHERE id = :cid"), {"cid": course_id})
    await db.execute(text("DELETE FROM users WHERE id = :sid"), {"sid": student_id})
    await db.commit()


# ── (а) файл соседнего задания не стирается ─────────────────────────────────


async def test_upload_for_other_task_keeps_previous_file(client, db):
    """Главный сценарий дефекта: сдал задание 1, взялся за 2 — файл первого на месте."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_one = await _make_sa_task(db, course_id, requires_attachment=False)
    task_two = await _make_sa_task(db, course_id, requires_attachment=False)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        first = await _upload(client, attempt_id, "task1.py", task_id=task_one)
        assert first.status_code == 201, first.text
        second = await _upload(client, attempt_id, "task2.py", task_id=task_two)
        assert second.status_code == 201, second.text

        first_id = first.json()["attachment_id"]
        second_id = second.json()["attachment_id"]
        assert first_id != second_id
        assert parse_attachment_id(first_id) == (attempt_id, task_one)
        assert parse_attachment_id(second_id) == (attempt_id, task_two)

        # Оба файла на диске и оба скачиваются — до правки первый исчезал.
        for attachment_id in (first_id, second_id):
            download = await client.get(
                f"/api/v1/attempts/{attempt_id}/attachments/{attachment_id}",
                headers=_headers(),
            )
            assert download.status_code == 200, attachment_id
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (б) перезаливка того же задания заменяет ─────────────────────────────────


async def test_reupload_same_task_replaces_file(client, db):
    """Инвариант «одно актуальное вложение на пару» сохраняется."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_task(db, course_id, requires_attachment=False)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        first = await _upload(client, attempt_id, "draft.py", task_id=task_id)
        second = await _upload(client, attempt_id, "final.py", task_id=task_id)
        assert first.status_code == 201 and second.status_code == 201

        first_id = first.json()["attachment_id"]
        second_id = second.json()["attachment_id"]
        upload_dir = _settings.attempt_attachments_upload_dir
        assert not (upload_dir / first_id).exists()
        assert (upload_dir / second_id).exists()
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (в) обратная совместимость со старым клиентом ────────────────────────────


async def test_legacy_upload_without_task_id_keeps_tagged_files(client, db):
    """Загрузка без `task_id` (клиент ещё не обновлён) не сносит файлы заданий."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_task(db, course_id, requires_attachment=False)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        tagged = await _upload(client, attempt_id, "tagged.py", task_id=task_id)
        legacy_first = await _upload(client, attempt_id, "legacy1.py")
        legacy_second = await _upload(client, attempt_id, "legacy2.py")
        assert tagged.status_code == 201
        assert legacy_first.status_code == 201 and legacy_second.status_code == 201

        upload_dir = _settings.attempt_attachments_upload_dir
        # Файл с меткой задания цел, хотя после него дважды грузили без метки.
        assert (upload_dir / tagged.json()["attachment_id"]).exists()
        # А между собой файлы без метки по-прежнему вытесняют друг друга.
        assert not (upload_dir / legacy_first.json()["attachment_id"]).exists()
        assert (upload_dir / legacy_second.json()["attachment_id"]).exists()
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_tagged_upload_keeps_legacy_file(client, db):
    """Обратная сторона: загрузка с меткой не трогает старый файл без метки."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_task(db, course_id, requires_attachment=False)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        legacy = await _upload(client, attempt_id, "legacy.py")
        tagged = await _upload(client, attempt_id, "tagged.py", task_id=task_id)
        assert legacy.status_code == 201 and tagged.status_code == 201

        upload_dir = _settings.attempt_attachments_upload_dir
        assert (upload_dir / legacy.json()["attachment_id"]).exists()
        assert (upload_dir / tagged.json()["attachment_id"]).exists()
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_legacy_attachment_id_still_downloadable(client, db):
    """Файлы старого формата `{attempt}_{uuid}_{имя}` лежат на проде — они должны читаться."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    legacy_id = build_attachment_id(attempt_id, None, "old_format.py")
    path = _settings.attempt_attachments_upload_dir / legacy_id
    path.write_bytes(b"print('legacy')")
    try:
        assert parse_attachment_id(legacy_id) == (attempt_id, None)
        download = await client.get(
            f"/api/v1/attempts/{attempt_id}/attachments/{legacy_id}", headers=_headers()
        )
        assert download.status_code == 200, download.text
        assert download.content == b"print('legacy')"
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (г) утраченный файл: 410, а не 404 ───────────────────────────────────────


async def test_missing_file_download_returns_410(client, db):
    """Имя разобрано, попытка та — файла нет: 410 «утрачен», а не «не найдено»."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_task(db, course_id, requires_attachment=False)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        lost_id = build_attachment_id(attempt_id, task_id, "lost.py")
        download = await client.get(
            f"/api/v1/attempts/{attempt_id}/attachments/{lost_id}", headers=_headers()
        )
        assert download.status_code == 410, download.text
        assert "утрачен" in download.json()["detail"].lower()
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_invalid_attachment_id_still_404(client, db):
    """Мусорный id — по-прежнему 404: там нечего было терять."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        download = await client.get(
            f"/api/v1/attempts/{attempt_id}/attachments/{attempt_id}_not-a-uuid_x.py",
            headers=_headers(),
        )
        assert download.status_code == 404
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (е) гейт requires_attachment сузился до задания ──────────────────────────


async def test_requires_attachment_not_satisfied_by_other_task_file(client, db):
    """Файл, приложенный к заданию 1, больше не открывает зачёт заданию 2."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_one = await _make_sa_task(db, course_id, requires_attachment=False)
    task_two = await _make_sa_task(db, course_id, requires_attachment=True)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        up = await _upload(client, attempt_id, "proof_for_task1.py", task_id=task_one)
        assert up.status_code == 201, up.text

        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_two, "answer": {
                "type": "SA", "response": {"value": "готово"}}}]},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]["check_result"]
        assert result["is_correct"] is False
        assert result["score"] == 0
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_requires_attachment_satisfied_by_own_task_file(client, db):
    """Файл этого задания зачёт открывает."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_task(db, course_id, requires_attachment=True)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        up = await _upload(client, attempt_id, "proof.py", task_id=task_id)
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


async def test_requires_attachment_satisfied_by_untagged_file_legacy_client(client, db):
    """Страховка: старый клиент грузит без метки — приём ответов не ломается."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sa_task(db, course_id, requires_attachment=True)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        up = await _upload(client, attempt_id, "proof.py")
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


async def test_upload_rejects_non_positive_task_id(client, db):
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    attempt_id = await _create_attempt(client, student_id=student_id, course_id=course_id)
    try:
        resp = await _upload(client, attempt_id, "x.py", task_id=0)
        assert resp.status_code == 422, resp.text
    finally:
        _cleanup_attachments(attempt_id)
        await _cleanup(db, course_id=course_id, student_id=student_id)
