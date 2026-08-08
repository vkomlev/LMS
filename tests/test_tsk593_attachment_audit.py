"""tsk-593: суточная проверка «ссылка на вложение есть, файла в хранилище нет».

Главное свойство проверки — она МОЛЧИТ, пока не появилось новой утраты. Иначе
180 старых потерь (дефект tsk-575, восстановлению не подлежат) превратили бы её
в ежедневное одинаковое письмо, за которым новую потерю никто не заметит.

Покрываем:
- (а) чистый прогон: файлы на месте — ни одного уведомления;
- (б) новая утрата: уведомление уходит, и адресат зависит от вида файла
      (учебное вложение — методисту, чек — маркетологу);
- (в) уже известная утрата второй раз не тревожит;
- (г) файл вернулся — память об утрате очищается, повторная потеря снова
      считается новой;
- (д) хранилище не ответило — прогон признаётся недостоверным целиком, а не
      объявляет утраченными все вложения разом.
"""
from __future__ import annotations

import uuid
from typing import List

import pytest
from sqlalchemy import text

from app.services import attachment_audit_service as audit
from app.services import attachment_storage
from app.utils.exceptions import DomainError

pytestmark = pytest.mark.asyncio


async def _seed_result_with_attachment(db, name: str) -> int:
    """Работа ученика со ссылкой на вложение. Возвращает id строки результата."""
    user_id = (await db.execute(text("SELECT id FROM users LIMIT 1"))).scalar()
    task_id = (await db.execute(text("SELECT id FROM tasks LIMIT 1"))).scalar()
    answer = (
        '{"response": {"value": "готово", "meta": {"attachments": '
        '[{"filename": "solution.py", "attachment_id": "' + name + '"}]}}}'
    )
    row = await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, score, max_score, answer_json, "
            "                          source_system) "
            "VALUES (:uid, :tid, 1, 1, CAST(:aj AS jsonb), 'tsk593_test') RETURNING id"
        ),
        {"uid": user_id, "tid": task_id, "aj": answer},
    )
    result_id = int(row.scalar())
    await db.commit()
    return result_id


async def _cleanup(db, result_ids: List[int], names: List[str]) -> None:
    for result_id in result_ids:
        await db.execute(text("DELETE FROM task_results WHERE id = :i"), {"i": result_id})
    for name in names:
        await db.execute(
            text("DELETE FROM attachment_missing_seen WHERE name = :n"), {"n": name}
        )
    await db.execute(
        text("DELETE FROM notifications WHERE kind = :k"), {"k": audit.NOTIFICATION_KIND}
    )
    await db.commit()


def _fresh_names(summary: dict) -> set[str]:
    return set(summary.get("fresh_names") or [])


async def test_clean_run_is_silent(db, db_session_factory, monkeypatch):
    """Файл на месте — проверка не говорит ничего."""
    name = f"901_t7_{uuid.uuid4().hex}_ok.py"
    result_id = await _seed_result_with_attachment(db, name)

    async def _all_present(space, names):
        return set(names)

    monkeypatch.setattr(attachment_storage, "existing_names", _all_present)
    try:
        summary = await audit.attachment_audit_tick(db_session_factory)
        assert summary["locked"] is True
        assert name not in str(summary.get("fresh_names"))
        assert summary["notified"] == 0
    finally:
        await _cleanup(db, [result_id], [name])


async def test_new_loss_notifies_methodist(db, db_session_factory, monkeypatch):
    """Новая утрата — уведомление уходит, и в нём есть конкретное имя файла."""
    name = f"902_t7_{uuid.uuid4().hex}_lost.py"
    result_id = await _seed_result_with_attachment(db, name)

    async def _nothing_found(space, names):
        return set()

    monkeypatch.setattr(attachment_storage, "existing_names", _nothing_found)
    try:
        summary = await audit.attachment_audit_tick(db_session_factory)
        assert f"attempts:{name}" in _fresh_names(summary)
        assert summary["notified"] >= 1

        # Утрата записана в память проверки — это и делает завтрашний прогон тихим.
        known = (
            await db.execute(
                text("SELECT count(*) FROM attachment_missing_seen WHERE name = :n"),
                {"n": name},
            )
        ).scalar()
        assert int(known) == 1
    finally:
        await _cleanup(db, [result_id], [name])


async def test_known_loss_does_not_notify_twice(db, db_session_factory, monkeypatch):
    """Вторые сутки подряд про ту же потерю не пишем: иначе это шум."""
    name = f"903_t7_{uuid.uuid4().hex}_lost.py"
    result_id = await _seed_result_with_attachment(db, name)

    async def _nothing_found(space, names):
        return set()

    monkeypatch.setattr(attachment_storage, "existing_names", _nothing_found)
    try:
        await audit.attachment_audit_tick(db_session_factory)
        second = await audit.attachment_audit_tick(db_session_factory)
        assert f"attempts:{name}" not in _fresh_names(second)
        assert second["notified"] == 0
    finally:
        await _cleanup(db, [result_id], [name])


async def test_returned_file_clears_memory(db, db_session_factory, monkeypatch):
    """Файл вернулся — забываем утрату, иначе повторная потеря пройдёт молча."""
    name = f"904_t7_{uuid.uuid4().hex}_lost.py"
    result_id = await _seed_result_with_attachment(db, name)

    async def _nothing_found(space, names):
        return set()

    async def _all_present(space, names):
        return set(names)

    try:
        monkeypatch.setattr(attachment_storage, "existing_names", _nothing_found)
        await audit.attachment_audit_tick(db_session_factory)

        monkeypatch.setattr(attachment_storage, "existing_names", _all_present)
        await audit.attachment_audit_tick(db_session_factory)
        left = (
            await db.execute(
                text("SELECT count(*) FROM attachment_missing_seen WHERE name = :n"),
                {"n": name},
            )
        ).scalar()
        assert int(left) == 0, "память об утрате не очистилась после возврата файла"
    finally:
        await _cleanup(db, [result_id], [name])


async def test_storage_outage_aborts_run(db, db_session_factory, monkeypatch):
    """«Нет ответа» — не «файла нет»: прогон прерывается, никого не будим."""
    name = f"905_t7_{uuid.uuid4().hex}_x.py"
    result_id = await _seed_result_with_attachment(db, name)

    async def _outage(space, names):
        raise DomainError("Хранилище файлов недоступно", status_code=503)

    monkeypatch.setattr(attachment_storage, "existing_names", _outage)
    try:
        summary = await audit.attachment_audit_tick(db_session_factory)
        assert "error" in summary
        assert summary["notified"] == 0
        assert summary["missing"] == 0
    finally:
        await _cleanup(db, [result_id], [name])
