"""tsk-803: журнал правок оценок `task_result_audit` (триггер на task_results).

Проверяет то, ради чего заведён журнал: правка оценки ЛЮБЫМ путём — в том
числе прямым SQL, который обходит `audit_event`, — оставляет след со старым и
новым значением; поток обычных UPDATE (захват проверки, метрики) в журнал не
попадает; стереть след нельзя.

Стратегия — как в test_tsk114_task_audit.py: временный курс, задание,
пользователь и результат создаются в транзакции фикстуры `db` (rollback после
теста), `flush()` вместо `commit()` — триггеры срабатывают всё равно.
"""
from __future__ import annotations

import random
import secrets

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

_TASK_CONTENT = '{"type": "SC", "stem": "x", "options": [{"id": "a", "label": "1"}]}'
_SOLUTION_RULES = '{"type": "SC", "correct_options": ["a"], "max_score": 10}'


async def _new_course(db) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO courses (title, description, access_level, is_required)
                VALUES ('test_tsk803_audit', 'test', 'self_guided', false)
                RETURNING id
                """
            )
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _new_task(db, course_id: int) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO tasks (task_content, course_id, difficulty_id, solution_rules,
                                   max_score, is_active)
                VALUES (CAST(:tc AS jsonb), :cid, 1, CAST(:sr AS jsonb), 10, true)
                RETURNING id
                """
            ),
            {"tc": _TASK_CONTENT, "cid": course_id, "sr": _SOLUTION_RULES},
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _new_user(db) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO users (email, full_name)
                VALUES (:email, 'tsk803-student')
                RETURNING id
                """
            ),
            {"email": f"tsk803-{random.randint(10**8, 10**10)}@example.com"},
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _new_result(db, *, user_id: int, task_id: int, score: int, is_correct: bool) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO task_results (user_id, task_id, score, max_score, is_correct,
                                          submitted_at, received_at, count_retry, source_system)
                VALUES (:u, :t, :sc, 10, :ok, now(), now(), 0, 'test')
                RETURNING id
                """
            ),
            {"u": user_id, "t": task_id, "sc": score, "ok": is_correct},
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _audit_rows(db, result_id: int):
    rows = (
        await db.execute(
            text(
                """
                SELECT action, task_id, user_id,
                       old_score, new_score, old_is_correct, new_is_correct,
                       changed_by, db_role
                FROM task_result_audit
                WHERE result_id = :rid
                ORDER BY id
                """
            ),
            {"rid": result_id},
        )
    ).all()
    return rows


@pytest.fixture
async def result_ctx(db):
    """Курс + задание + ученик + результат с баллом 3 и вердиктом «не зачёт»."""
    course_id = await _new_course(db)
    task_id = await _new_task(db, course_id)
    user_id = await _new_user(db)
    result_id = await _new_result(
        db, user_id=user_id, task_id=task_id, score=3, is_correct=False
    )
    return {
        "course_id": course_id,
        "task_id": task_id,
        "user_id": user_id,
        "result_id": result_id,
    }


# ---------- Что попадает в журнал ----------


@pytest.mark.asyncio
async def test_direct_sql_score_change_is_logged(db, result_ctx):
    """Прямой UPDATE балла (путь, который обходит audit_event) пишет в журнал.

    Ровно этим способом чинились дефекты августа-сентября (tsk-760, tsk-796,
    tsk-801) — и ни одна правка не попала в `audit_event`.
    """
    rid = result_ctx["result_id"]

    await db.execute(
        text("UPDATE task_results SET score = 9, is_correct = true WHERE id = :rid"),
        {"rid": rid},
    )
    await db.flush()

    rows = await _audit_rows(db, rid)
    assert len(rows) == 1, f"ожидалась одна запись журнала, получено: {rows}"
    row = rows[0]
    assert row.action == "UPDATE"
    assert (row.old_score, row.new_score) == (3, 9)
    assert (row.old_is_correct, row.new_is_correct) == (False, True)
    assert row.task_id == result_ctx["task_id"]
    assert row.user_id == result_ctx["user_id"]
    # db_role заполняется всегда — след, не зависящий от кооперации кода.
    assert row.db_role


@pytest.mark.asyncio
async def test_verdict_change_without_score_change_is_logged(db, result_ctx):
    """Смена только вердикта (`is_correct`) — тоже правка оценки.

    Случай tsk-801: балл оставался прежним, а ложный незачёт менялся на зачёт.
    """
    rid = result_ctx["result_id"]

    await db.execute(
        text("UPDATE task_results SET is_correct = true WHERE id = :rid"), {"rid": rid}
    )
    await db.flush()

    rows = await _audit_rows(db, rid)
    assert len(rows) == 1
    assert (rows[0].old_score, rows[0].new_score) == (3, 3)
    assert (rows[0].old_is_correct, rows[0].new_is_correct) == (False, True)


@pytest.mark.asyncio
async def test_audit_allows_rollback_of_a_batch(db, result_ctx):
    """Журнал позволяет вернуть прежние значения — то, чего не хватило в tsk-801.

    Правка отката делается с выключенным триггером, иначе она сама попадёт в
    журнал; выключение — тем же session-var, что и у соседних триггеров.
    """
    rid = result_ctx["result_id"]

    await db.execute(
        text("UPDATE task_results SET score = 10, is_correct = true WHERE id = :rid"),
        {"rid": rid},
    )
    await db.flush()

    row = (await _audit_rows(db, rid))[0]
    await db.execute(
        text("SELECT set_config('app.skip_task_result_audit_trigger', 'true', true)")
    )
    await db.execute(
        text(
            "UPDATE task_results SET score = :sc, is_correct = :ok WHERE id = :rid"
        ),
        {"sc": row.old_score, "ok": row.old_is_correct, "rid": rid},
    )
    await db.execute(
        text("SELECT set_config('app.skip_task_result_audit_trigger', 'false', true)")
    )
    await db.flush()

    restored = (
        await db.execute(
            text("SELECT score, is_correct FROM task_results WHERE id = :rid"),
            {"rid": rid},
        )
    ).first()
    assert (restored.score, restored.is_correct) == (3, False)
    # Откат с выключенным триггером новой записи не добавил.
    assert len(await _audit_rows(db, rid)) == 1


# ---------- Что в журнал НЕ попадает ----------


@pytest.mark.asyncio
async def test_insert_is_not_logged(db, result_ctx):
    """INSERT не аудируется: поток вставок на порядок больше правок."""
    assert await _audit_rows(db, result_ctx["result_id"]) == []


@pytest.mark.asyncio
async def test_unrelated_update_is_not_logged(db, result_ctx):
    """UPDATE соседних полей (захват проверки, метрики, checked_at) — не правка оценки.

    Именно это условие удерживает журнал от роста на обычном трафике очереди
    преподавателя.
    """
    rid = result_ctx["result_id"]

    await db.execute(
        text(
            "UPDATE task_results SET review_claimed_by = NULL, "
            "  metrics = CAST('{\"comment\": \"тест\"}' AS jsonb), "
            "  checked_at = now() "
            "WHERE id = :rid"
        ),
        {"rid": rid},
    )
    await db.flush()

    assert await _audit_rows(db, rid) == []


@pytest.mark.asyncio
async def test_update_to_same_values_is_not_logged(db, result_ctx):
    """Повторная запись тех же значений строк не плодит (IS DISTINCT FROM)."""
    rid = result_ctx["result_id"]

    await db.execute(
        text("UPDATE task_results SET score = 3, is_correct = false WHERE id = :rid"),
        {"rid": rid},
    )
    await db.flush()

    assert await _audit_rows(db, rid) == []


# ---------- Источник правки ----------


@pytest.mark.asyncio
async def test_changed_by_from_audit_actor(db, result_ctx):
    """`changed_by` берётся из `app.audit_actor` — той же метки, что у task_audit."""
    rid = result_ctx["result_id"]

    await db.execute(
        text("SELECT set_config('app.audit_actor', 'script:tsk803_test.py', true)")
    )
    await db.execute(
        text("UPDATE task_results SET score = 7 WHERE id = :rid"), {"rid": rid}
    )
    await db.flush()

    rows = await _audit_rows(db, rid)
    assert len(rows) == 1
    assert rows[0].changed_by == "script:tsk803_test.py"


@pytest.mark.asyncio
async def test_changed_by_is_null_when_source_stays_silent(db, result_ctx):
    """Без метки `changed_by` остаётся NULL — честный сигнал «источник не назвался».

    Это не дефект: `db_role` заполнен всегда и даёт минимальный след.
    """
    rid = result_ctx["result_id"]

    await db.execute(
        text("UPDATE task_results SET score = 5 WHERE id = :rid"), {"rid": rid}
    )
    await db.flush()

    rows = await _audit_rows(db, rid)
    assert rows[0].changed_by is None
    assert rows[0].db_role


# ---------- Append-only ----------


@pytest.mark.asyncio
async def test_audit_rows_cannot_be_updated(db, result_ctx):
    """UPDATE строки журнала запрещён: кто правит оценки, не должен стирать след."""
    rid = result_ctx["result_id"]
    await db.execute(
        text("UPDATE task_results SET score = 8 WHERE id = :rid"), {"rid": rid}
    )
    await db.flush()

    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(
            text("UPDATE task_result_audit SET new_score = 0 WHERE result_id = :rid"),
            {"rid": rid},
        )
    await db.rollback()


@pytest.mark.asyncio
async def test_audit_rows_cannot_be_deleted(db, result_ctx):
    """DELETE строки журнала запрещён — зеркало task_audit / audit_event."""
    rid = result_ctx["result_id"]
    await db.execute(
        text("UPDATE task_results SET score = 8 WHERE id = :rid"), {"rid": rid}
    )
    await db.flush()

    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(
            text("DELETE FROM task_result_audit WHERE result_id = :rid"), {"rid": rid}
        )
    await db.rollback()


# ---------- Штатный путь: оценка через кабинет преподавателя ----------


@pytest.mark.asyncio
@pytest.mark.no_tx_isolation
async def test_grade_endpoint_names_the_teacher_and_keeps_previous_score(db, client):
    """Оценка через API: журнал знает преподавателя, уведомление — прежний балл.

    Две правки tsk-803 в одном проходе:
    * `changed_by = 'user:<id>'` — иначе штатная оценка была бы в журнале
      неотличима от прямого SQL (эндпоинт идёт через `get_bare_db`, где метка
      источника не проставлялась);
    * `previous_score` в payload уведомления и в `audit_event` — раньше туда
      уходил жёсткий `None`.
    """
    from tests.test_y6_review_loop import (  # локальный импорт: тяжёлые фикстуры Y-6
        _cleanup,
        _create_pending_tr,
        _create_user,
        _pick_root_task,
        _setup_teacher_with_course,
    )

    task_id, course_id, _t = await _pick_root_task(db)
    teacher_id, token = await _setup_teacher_with_course(db, course_id=course_id)
    student_id = await _create_user(db, prefix="tsk803-stud")
    lock_token = secrets.token_hex(32)
    rid, _, _ = await _create_pending_tr(
        db,
        student_id=student_id,
        task_id=task_id,
        teacher_id=teacher_id,
        lock_token=lock_token,
        is_correct=True,  # optimistic-PASSED на submit
        score=10,
        max_score=10,
    )
    try:
        resp = await client.post(
            f"/api/v1/teacher/reviews/{rid}/grade",
            json={"teacher_id": teacher_id, "lock_token": lock_token, "score": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text

        row = (
            await db.execute(
                text(
                    "SELECT old_score, new_score, old_is_correct, new_is_correct, "
                    "       changed_by, db_role "
                    "FROM task_result_audit WHERE result_id = :rid ORDER BY id DESC LIMIT 1"
                ),
                {"rid": rid},
            )
        ).first()
        assert row is not None, "оценка через кабинет не попала в журнал"
        assert (row.old_score, row.new_score) == (10, 1)
        assert (row.old_is_correct, row.new_is_correct) == (True, False)
        assert row.changed_by == f"user:{teacher_id}"

        payload = (
            await db.execute(
                text(
                    "SELECT payload FROM notifications WHERE user_id = :s "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"s": student_id},
            )
        ).scalar()
        assert payload is not None
        assert payload.get("previous_score") == 10, payload
        assert payload.get("previous_is_correct") is True, payload

        details = (
            await db.execute(
                text(
                    "SELECT details FROM audit_event "
                    "WHERE event_type = 'teacher.review.graded' AND user_id = :t "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"t": teacher_id},
            )
        ).scalar()
        assert details is not None
        assert details.get("previous_score") == 10, details
        assert details.get("previous_is_correct") is True, details
    finally:
        await _cleanup(db, user_ids=[teacher_id, student_id], result_ids=[rid])
