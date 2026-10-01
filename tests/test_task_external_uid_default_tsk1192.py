"""
tsk-1192: задание без `external_uid` получает код `lms:task-{id}` от триггера БД.

Пункты домашней работы ученика строят ссылку из `external_uid`; задания, заведённые
скриптами прямым `INSERT` без кода, рисовались текстом без ссылки. Сторожим все
пути разом — вставку без кода, с пустой строкой и обнуление кода правкой; заданный
код триггер не трогает.
"""
from __future__ import annotations

import json

from sqlalchemy import text as sqltext


async def _insert_task(db, ext: str | None) -> int:
    """Завести курс и задание с данным кодом; вернуть id задания."""
    course_id = (await db.execute(sqltext(
        "INSERT INTO courses (title, access_level) VALUES ('tsk1192', 'auto_check') RETURNING id"
    ))).scalar_one()
    return (await db.execute(sqltext(
        "INSERT INTO tasks (external_uid, max_score, task_content, course_id, difficulty_id) "
        "VALUES (:ext, 1, CAST(:c AS jsonb), :cid, 1) RETURNING id"
    ), {
        "ext": ext,
        "c": json.dumps({"type": "SA", "stem": "tsk1192"}),
        "cid": course_id,
    })).scalar_one()


async def _uid(db, task_id: int) -> str | None:
    """Текущий код задания."""
    return (await db.execute(
        sqltext("SELECT external_uid FROM tasks WHERE id = :i"), {"i": task_id}
    )).scalar_one()


async def test_insert_without_uid_gets_default(db) -> None:
    """Вставка без кода (путь скриптов tsk-412/tsk-524) — код из номера."""
    task_id = await _insert_task(db, None)
    assert await _uid(db, task_id) == f"lms:task-{task_id}"


async def test_blank_uid_gets_default(db) -> None:
    """Пустая строка — тоже «кода нет»."""
    task_id = await _insert_task(db, "  ")
    assert await _uid(db, task_id) == f"lms:task-{task_id}"


async def test_explicit_uid_kept_and_reset_restored(db) -> None:
    """Заданный код остаётся; обнулённый правкой — восстанавливается."""
    task_id = await _insert_task(db, f"tsk1192-own-{id(db)}")
    assert await _uid(db, task_id) == f"tsk1192-own-{id(db)}"
    await db.execute(
        sqltext("UPDATE tasks SET external_uid = NULL WHERE id = :i"), {"i": task_id}
    )
    assert await _uid(db, task_id) == f"lms:task-{task_id}"
