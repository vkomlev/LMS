"""tsk-996: хвосты ревью фазы D.

* PATCH /tasks/{id} распознаёт отказ триггера-стража якоря по SQLSTATE 23514
  и по признаку стража (метка «tsk-996:» в сообщении или функция
  `tasks_anchor_guard` в контексте), а не по одной подстроке: гонка «проверка
  сервиса прошла → связь сняли → страж отказал» — 422, прочие ошибки базы —
  как раньше.
* «Ушёл вперёд» (content_grace_service) при подкурсе, прозрачном сразу для
  двух уроков (ошибка разметки), учитывает якорь только на материал ПЕРВОГО
  хозяина — как валидация якоря (`TasksService.anchor_course_ids`) и порядок
  узла (`transparent_links`).

Взаимоблокировка «якорь против снятия прозрачности» — в
`tests/test_tsk996_anchor_locks.py` (нужны настоящие параллельные транзакции).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.v1.tasks_extra import _is_anchor_guard_rejection
from app.services.content_grace_service import compute_graced_items
from app.services.tasks_service import TasksService
from tests.test_tsk996_anchor import _anchor, _anchor_row, _user_with_session
from tests.test_tsk996_bank_anchor import shape_bank
from tests.test_tsk996_node_sequence import (
    INSERTED,
    LONG_AGO,
    _course,
    _link,
    _material,
    _reset_caches,
    _result,
    _student,
    _task,
)


# ─── Распознавание отказа стража ────────────────────────────────────────────


async def _db_error(db, sql: str, params: dict | None = None) -> DBAPIError:
    nested = await db.begin_nested()
    with pytest.raises(DBAPIError) as exc:
        await db.execute(text(sql), params or {})
    await nested.rollback()
    return exc.value


@pytest.mark.asyncio
async def test_guard_rejection_recognized_by_sqlstate_and_guard(db):
    """Настоящий отказ стража — да; 23514 без признака стража и метка с другим кодом — нет."""
    s = await shape_bank(db)
    guard = await _db_error(
        db,
        "UPDATE tasks SET anchor_material_id = :m WHERE id = :t",
        {"m": s["pm"], "t": s["b2"]},
    )
    # Как реально устроена ошибка: SQLSTATE у обёртки и у первопричины asyncpg.
    assert guard.orig.sqlstate == "23514"
    assert guard.orig.__cause__.sqlstate == "23514"
    assert "tasks_anchor_guard" in (guard.orig.__cause__.context or "")
    assert _is_anchor_guard_rejection(guard)

    other_check = await _db_error(
        db,
        "DO $$ BEGIN RAISE EXCEPTION 'нарушено другое ограничение' "
        "USING ERRCODE = 'check_violation'; END $$",
    )
    assert other_check.orig.sqlstate == "23514"
    assert not _is_anchor_guard_rejection(other_check)

    marked_other_code = await _db_error(
        db, "DO $$ BEGIN RAISE EXCEPTION 'tsk-996: не страж'; END $$"
    )
    assert marked_other_code.orig.sqlstate == "P0001"
    assert not _is_anchor_guard_rejection(marked_other_code)


@pytest.mark.asyncio
async def test_api_patch_race_unlink_between_check_and_write_is_422(db, client, monkeypatch):
    """Связь банка с уроком сняли между проверкой сервиса и записью — 422, не 500."""
    s = await shape_bank(db)
    token = await _user_with_session(db, "methodist")
    auth = {"Authorization": f"Bearer {token}"}
    await _anchor(db, s["b2"], s["bm"])
    await db.commit()

    original = TasksService.validate_anchor

    async def validate_then_unlink(self, session, task, anchor_material_id):
        await original(self, session, task, anchor_material_id)
        # «Параллельная» правка иерархии: проверка уже прошла.
        await session.execute(
            text(
                "UPDATE course_parents SET is_transparent = false "
                "WHERE course_id = :b AND parent_course_id = :l"
            ),
            {"b": s["bank"], "l": s["lesson"]},
        )

    monkeypatch.setattr(TasksService, "validate_anchor", validate_then_unlink)
    r = await client.patch(
        f"/api/v1/tasks/{s['b2']}", json={"anchor_material_id": s["m3"]}, headers=auth
    )
    assert r.status_code == 422, r.text
    assert "больше не подходит" in r.text
    # Запись откачена целиком: якорь прежний.
    assert await _anchor_row(db, s["b2"]) == (s["bm"], "manual")


@pytest.mark.asyncio
async def test_api_patch_other_db_error_is_not_422(db, client, monkeypatch):
    """Ошибка базы, не связанная со стражем, по-прежнему не маскируется под 422."""
    s = await shape_bank(db)
    token = await _user_with_session(db, "methodist")
    auth = {"Authorization": f"Bearer {token}"}

    async def broken_update(self, session, db_obj, obj_in):
        await session.execute(
            text(
                "DO $$ BEGIN RAISE EXCEPTION 'другое ограничение' "
                "USING ERRCODE = 'check_violation'; END $$"
            )
        )

    monkeypatch.setattr(TasksService, "update", broken_update)
    with pytest.raises(DBAPIError):
        await client.patch(
            f"/api/v1/tasks/{s['b2']}", json={"anchor_material_id": s["m3"]}, headers=auth
        )


# ─── «Ушёл вперёд»: подкурс, прозрачный для двух уроков ─────────────────────


@pytest.mark.asyncio
async def test_grace_two_transparent_hosts_uses_first_host_only(db, monkeypatch):
    """Якорь на материал ВТОРОГО хозяина правило не ставит в голову второго урока.

    Банк прозрачен для урока 1 (первый по `order_number`) и урока 2. Движок и
    валидация берут только первого хозяина: в уроке 2 банк — обычный раздел,
    и задание x стоит в нём после решённого y, за ним только нерешённое z —
    прощать нечего. Якорь x на материал урока 2 (страж такой пропускает)
    раньше переносил x в голову урока 2, и правило прощало его.
    Контроль: якорь на материал ПЕРВОГО хозяина, корень — урок 1, работает.
    """
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    first = await _course(db, "два хозяина урок 1")
    second = await _course(db, "два хозяина урок 2")
    bank = await _course(db, "два хозяина банк")
    await _link(db, first, bank, 1, transparent=True)
    await _link(db, second, bank, 2, transparent=True)
    f1 = await _material(db, first, 1)
    await _material(db, first, 2)
    s1 = await _material(db, second, 1)
    await _material(db, second, 2)
    y = await _task(db, bank, 1, host_position=100)
    student = await _student(db, first, second)
    await _result(db, student, y, second, second, at=LONG_AGO + timedelta(days=3))
    x = await _task(db, bank, 2, host_position=200, created_at=INSERTED)
    await _task(db, bank, 3, host_position=300)
    await db.commit()

    async def graced(root: int) -> set[int]:
        _reset_caches(db)
        return set((await compute_graced_items(db, student, root)).tasks)

    assert x not in await graced(second)
    await _anchor(db, x, s1)
    await db.commit()
    assert x not in await graced(second)

    await _result(db, student, y, first, first, at=LONG_AGO + timedelta(days=3))
    await _anchor(db, x, f1)
    await db.commit()
    assert x in await graced(first)
