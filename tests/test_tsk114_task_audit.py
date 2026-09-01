"""tsk-114: интеграционные тесты audit-триггера tasks.course_id/is_active.

Покрывает: UPDATE course_id/is_active пишет task_audit (старое/новое +
источник), UPDATE прочих полей — не пишет (WHEN-условие), DELETE
аудируется, append-only enforcement (UPDATE/DELETE task_audit запрещены),
и TasksService.bulk_upsert проставляет changed_by='bulk_upsert'.

Стратегия — как в test_tasks_order_position.py: временный курс + задачи в
транзакции фикстуры `db` (rollback после теста, в БД ничего не остаётся).
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.services.tasks_service import TasksService

_TASK_CONTENT = '{"type": "SC", "stem": "x", "options": [{"id": "a", "label": "1"}]}'
_SOLUTION_RULES = '{"type": "SC", "correct_options": ["a"], "max_score": 1}'


async def _new_course(db, title: str = "test_task_audit") -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO courses (title, description, access_level, is_required)
                VALUES (:title, 'test', 'self_guided', false)
                RETURNING id
                """
            ),
            {"title": title},
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _insert_task(
    db, course_id: int, *, is_active: bool = True, external_uid: str | None = None
) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO tasks (task_content, course_id, difficulty_id, solution_rules,
                                   max_score, is_active, external_uid)
                VALUES (CAST(:tc AS jsonb), :cid, 1, CAST(:sr AS jsonb), 1, :active, :uid)
                RETURNING id
                """
            ),
            {
                "tc": _TASK_CONTENT,
                "cid": course_id,
                "sr": _SOLUTION_RULES,
                "active": is_active,
                "uid": external_uid,
            },
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _audit_rows(db, task_id: int):
    rows = (
        await db.execute(
            text(
                """
                SELECT action, old_course_id, new_course_id,
                       old_is_active, new_is_active, changed_by, db_role
                FROM task_audit
                WHERE task_id = :tid
                ORDER BY id
                """
            ),
            {"tid": task_id},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@pytest.mark.asyncio
async def test_update_course_id_is_audited(db):
    """A1. UPDATE course_id пишет строку с старым/новым значением."""
    course_a = await _new_course(db, "task_audit_a1_a")
    course_b = await _new_course(db, "task_audit_a1_b")
    task_id = await _insert_task(db, course_a)

    await db.execute(text("SELECT set_config('app.audit_actor', 'test_actor', true)"))
    await db.execute(
        text("UPDATE tasks SET course_id = :cid WHERE id = :tid"),
        {"cid": course_b, "tid": task_id},
    )
    await db.flush()

    rows = await _audit_rows(db, task_id)
    assert len(rows) == 1
    assert rows[0]["action"] == "UPDATE"
    assert rows[0]["old_course_id"] == course_a
    assert rows[0]["new_course_id"] == course_b
    # Строка — полный снимок обоих полей на момент изменения (не только
    # того, что сработало условием WHEN), is_active тут не менялся.
    assert rows[0]["old_is_active"] is True
    assert rows[0]["new_is_active"] is True
    assert rows[0]["changed_by"] == "test_actor"
    assert rows[0]["db_role"]


@pytest.mark.asyncio
async def test_update_is_active_is_audited(db):
    """A2. UPDATE is_active пишет строку независимо от course_id."""
    course_a = await _new_course(db, "task_audit_a2")
    task_id = await _insert_task(db, course_a, is_active=True)

    await db.execute(
        text("UPDATE tasks SET is_active = false WHERE id = :tid"), {"tid": task_id}
    )
    await db.flush()

    rows = await _audit_rows(db, task_id)
    assert len(rows) == 1
    assert rows[0]["old_is_active"] is True
    assert rows[0]["new_is_active"] is False
    # course_id не менялся — снимок остаётся тем же значением до/после.
    assert rows[0]["old_course_id"] == course_a
    assert rows[0]["new_course_id"] == course_a


@pytest.mark.asyncio
async def test_update_other_field_not_audited(db):
    """A3. UPDATE order_position — НЕ пишет строку (WHEN).

    tsk-760: правка `task_content` теперь аудируется (условие задания — ровно
    то, что стирало переиздание курса, и без следа его было не отличить от
    импорта). Порядок показа под аудит по-прежнему не подпадает: его массово
    двигают триггеры порядка, а к содержимому задания он отношения не имеет.
    """
    course_a = await _new_course(db, "task_audit_a3")
    task_id = await _insert_task(db, course_a)

    await db.execute(
        text("UPDATE tasks SET order_position = COALESCE(order_position, 0) + 1 WHERE id = :tid"),
        {"tid": task_id},
    )
    await db.flush()

    rows = await _audit_rows(db, task_id)
    assert rows == []


@pytest.mark.asyncio
async def test_update_same_value_not_audited(db):
    """A4. UPDATE course_id на то же самое значение — не считается изменением."""
    course_a = await _new_course(db, "task_audit_a4")
    task_id = await _insert_task(db, course_a)

    await db.execute(
        text("UPDATE tasks SET course_id = :cid WHERE id = :tid"),
        {"cid": course_a, "tid": task_id},
    )
    await db.flush()

    rows = await _audit_rows(db, task_id)
    assert rows == []


@pytest.mark.asyncio
async def test_delete_is_audited(db):
    """A5. DELETE пишет строку action='DELETE' со старыми значениями."""
    course_a = await _new_course(db, "task_audit_a5")
    task_id = await _insert_task(db, course_a, is_active=True, external_uid="a5-uid")

    await db.execute(text("DELETE FROM tasks WHERE id = :tid"), {"tid": task_id})
    await db.flush()

    rows = await _audit_rows(db, task_id)
    assert len(rows) == 1
    assert rows[0]["action"] == "DELETE"
    assert rows[0]["old_course_id"] == course_a
    assert rows[0]["old_is_active"] is True
    assert rows[0]["new_course_id"] is None
    assert rows[0]["new_is_active"] is None


@pytest.mark.asyncio
async def test_no_actor_set_gives_null_changed_by_but_db_role_present(db):
    """A6. Без app.audit_actor — changed_by=NULL, но db_role всё равно есть
    (независимый от кооперации приложения след — как у ad-hoc SQL-скрипта)."""
    course_a = await _new_course(db, "task_audit_a6")
    task_id = await _insert_task(db, course_a)

    # Явно НЕ ставим app.audit_actor.
    await db.execute(
        text("UPDATE tasks SET is_active = false WHERE id = :tid"), {"tid": task_id}
    )
    await db.flush()

    rows = await _audit_rows(db, task_id)
    assert len(rows) == 1
    assert rows[0]["changed_by"] is None
    assert rows[0]["db_role"]


@pytest.mark.asyncio
async def test_append_only_blocks_update(db):
    """A7. UPDATE строки task_audit запрещён (append-only)."""
    course_a = await _new_course(db, "task_audit_a7")
    task_id = await _insert_task(db, course_a)
    await db.execute(
        text("UPDATE tasks SET is_active = false WHERE id = :tid"), {"tid": task_id}
    )
    await db.flush()

    audit_id = (
        await db.execute(
            text("SELECT id FROM task_audit WHERE task_id = :tid"), {"tid": task_id}
        )
    ).scalar_one()

    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(
            text("UPDATE task_audit SET changed_by = 'hacked' WHERE id = :aid"),
            {"aid": audit_id},
        )
        await db.flush()


@pytest.mark.asyncio
async def test_append_only_blocks_delete(db):
    """A8. DELETE строки task_audit запрещён (append-only)."""
    course_a = await _new_course(db, "task_audit_a8")
    task_id = await _insert_task(db, course_a)
    await db.execute(
        text("UPDATE tasks SET is_active = false WHERE id = :tid"), {"tid": task_id}
    )
    await db.flush()

    audit_id = (
        await db.execute(
            text("SELECT id FROM task_audit WHERE task_id = :tid"), {"tid": task_id}
        )
    ).scalar_one()

    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(
            text("DELETE FROM task_audit WHERE id = :aid"), {"aid": audit_id}
        )
        await db.flush()


@pytest.mark.asyncio
async def test_skip_flag_suppresses_audit(db):
    """A9. app.skip_task_audit_trigger='true' — safety-valve, ничего не пишет."""
    course_a = await _new_course(db, "task_audit_a9")
    task_id = await _insert_task(db, course_a)

    await db.execute(
        text("SELECT set_config('app.skip_task_audit_trigger', 'true', true)")
    )
    await db.execute(
        text("UPDATE tasks SET is_active = false WHERE id = :tid"), {"tid": task_id}
    )
    await db.flush()

    rows = await _audit_rows(db, task_id)
    assert rows == []


@pytest.mark.asyncio
async def test_bulk_upsert_labels_actor(db):
    """A10. TasksService.bulk_upsert проставляет changed_by='bulk_upsert' при
    смене course_id существующего задания через импорт."""
    course_a = await _new_course(db, "task_audit_a10_a")
    course_b = await _new_course(db, "task_audit_a10_b")
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar_one()

    service = TasksService()
    external_uid = "tsk114-bulk-a10"
    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": external_uid,
                "course_id": course_a,
                "difficulty_id": int(difficulty_id),
                "task_content": {
                    "type": "SC",
                    "stem": "bulk a10",
                    "options": [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}],
                },
                "solution_rules": {"type": "SC", "correct_options": ["a"], "max_score": 1},
            }
        ],
    )
    task_id = (
        await db.execute(
            text("SELECT id FROM tasks WHERE external_uid = :uid"), {"uid": external_uid}
        )
    ).scalar_one()

    # Реиздание с другим course_id — та самая «незаметная перевозка» tsk-113.
    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": external_uid,
                "course_id": course_b,
                "difficulty_id": int(difficulty_id),
                "task_content": {
                    "type": "SC",
                    "stem": "bulk a10",
                    "options": [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}],
                },
                "solution_rules": {"type": "SC", "correct_options": ["a"], "max_score": 1},
            }
        ],
    )

    rows = await _audit_rows(db, task_id)
    assert len(rows) == 1
    assert rows[0]["old_course_id"] == course_a
    assert rows[0]["new_course_id"] == course_b
    assert rows[0]["changed_by"] == "bulk_upsert"


# ---------- tsk-760: отметка «когда трогали» и отпечаток условия ----------


@pytest.mark.asyncio
async def test_отметка_правки_ставится_при_смене_условия(db):
    """Правка условия проставляет tasks.updated_at (tsk-760)."""
    course = await _new_course(db, "tsk760_touch")
    task_id = await _insert_task(db, course)

    before = (
        await db.execute(text("SELECT updated_at FROM tasks WHERE id = :tid"), {"tid": task_id})
    ).scalar()
    assert before is None, "у только что заведённого задания отметки правки быть не должно"

    await db.execute(
        text("UPDATE tasks SET task_content = CAST(:tc AS jsonb) WHERE id = :tid"),
        {"tc": '{"type": "SA_COM", "stem": "правленное условие"}', "tid": task_id},
    )
    await db.flush()

    after = (
        await db.execute(text("SELECT updated_at FROM tasks WHERE id = :tid"), {"tid": task_id})
    ).scalar()
    assert after is not None


@pytest.mark.asyncio
async def test_перестановка_порядка_отметкой_правки_не_считается(db):
    """order_position двигают триггеры порядка — это не правка содержимого."""
    course = await _new_course(db, "tsk760_order")
    task_id = await _insert_task(db, course)

    await db.execute(
        text("UPDATE tasks SET order_position = COALESCE(order_position, 0) + 5 WHERE id = :tid"),
        {"tid": task_id},
    )
    await db.flush()

    after = (
        await db.execute(text("SELECT updated_at FROM tasks WHERE id = :tid"), {"tid": task_id})
    ).scalar()
    assert after is None


@pytest.mark.asyncio
async def test_повторная_запись_того_же_условия_ничего_не_двигает(db):
    """Импорт с тем же содержимым не выглядит правкой: ни отметки, ни строки в журнале."""
    course = await _new_course(db, "tsk760_idempotent")
    task_id = await _insert_task(db, course)

    same = (
        await db.execute(text("SELECT task_content FROM tasks WHERE id = :tid"), {"tid": task_id})
    ).scalar()
    await db.execute(
        text("UPDATE tasks SET task_content = CAST(:tc AS jsonb) WHERE id = :tid"),
        {"tc": json.dumps(same, ensure_ascii=False), "tid": task_id},
    )
    await db.flush()

    after = (
        await db.execute(text("SELECT updated_at FROM tasks WHERE id = :tid"), {"tid": task_id})
    ).scalar()
    assert after is None
    assert await _audit_rows(db, task_id) == []


@pytest.mark.asyncio
async def test_повторный_импорт_прежнего_содержимого_не_считается_правкой(db):
    """tsk-760: нормализация схемой не двигает отметку правки и не пишет в журнал.

    В базе может лежать значение, записанное до того, как схема завела новые
    необязательные поля. Импорт всегда шлёт провалидированное значение — с
    дописанными `null` (`scales`, `table`, `quiz`, `turtle_sim`, …), — и
    буквальное сравнение jsonb называло это изменением. Из-за этого повторное
    переиздание помечало задание как правленное, хотя по смыслу не менялось
    ничего.
    """
    course = await _new_course(db, "tsk760_norm")
    uid = f"tsk760-norm-{course}"
    task_id = await _insert_task(db, course, external_uid=uid)

    # Приводим строку к «исторической» форме: без полей, добавленных схемой позже.
    # Форма, которая проходит нынешнюю схему, но записана без полей, заведённых
    # позже (`scales`, `table`, `multiline_answer` в условии и т.п.).
    historic_content = {"type": "SA_COM", "stem": "условие из источника"}
    historic_rules = {
        "max_score": 1,
        "scoring_mode": "all_or_nothing",
        "short_answer": {"accepted_answers": [{"value": "42", "score": 1}]},
    }
    await db.execute(
        text(
            "UPDATE tasks SET task_content = CAST(:tc AS jsonb), "
            "solution_rules = CAST(:sr AS jsonb) WHERE id = :tid"
        ),
        {
            "tc": json.dumps(historic_content, ensure_ascii=False),
            "sr": json.dumps(historic_rules, ensure_ascii=False),
            "tid": task_id,
        },
    )
    # Сброс отметки — ОТДЕЛЬНЫМ запросом: в одном с правкой содержимого её тут же
    # переставил бы триггер, и подготовка теста молча не сработала бы.
    await db.execute(text("UPDATE tasks SET updated_at = NULL WHERE id = :tid"), {"tid": task_id})
    await db.flush()
    # Журнал append-only: подготовительная правка уже оставила в нём строку —
    # считаем от текущего количества, а не от нуля.
    rows_before = len(await _audit_rows(db, task_id))

    service = TasksService()
    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": uid,
                "course_id": course,
                "difficulty_id": 1,
                "task_content": historic_content,
                "solution_rules": historic_rules,
                "max_score": 1,
            }
        ],
    )
    await db.flush()

    touched = (
        await db.execute(text("SELECT updated_at FROM tasks WHERE id = :tid"), {"tid": task_id})
    ).scalar()
    assert touched is None, "повторный импорт того же содержимого пометил задание как правленное"
    assert len(await _audit_rows(db, task_id)) == rows_before, (
        "в журнал ушла строка о правке, которой не было"
    )


@pytest.mark.asyncio
async def test_настоящая_правка_из_источника_по_прежнему_видна(db):
    """Обратная сторона: реальное изменение условия обязано двигать отметку."""
    course = await _new_course(db, "tsk760_real")
    uid = f"tsk760-real-{course}"
    task_id = await _insert_task(db, course, external_uid=uid)
    await db.execute(
        text(
            "UPDATE tasks SET task_content = CAST(:tc AS jsonb), "
            "solution_rules = CAST(:sr AS jsonb) WHERE id = :tid"
        ),
        {
            "tc": json.dumps({"type": "SA_COM", "stem": "условие из источника"}, ensure_ascii=False),
            "sr": json.dumps(
                {
                    "max_score": 1,
                    "scoring_mode": "all_or_nothing",
                    "short_answer": {"accepted_answers": [{"value": "42", "score": 1}]},
                },
                ensure_ascii=False,
            ),
            "tid": task_id,
        },
    )
    await db.execute(text("UPDATE tasks SET updated_at = NULL WHERE id = :tid"), {"tid": task_id})
    await db.flush()

    service = TasksService()
    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": uid,
                "course_id": course,
                "difficulty_id": 1,
                "task_content": {"type": "SA_COM", "stem": "условие переписано в источнике"},
                "solution_rules": {
                    "max_score": 1,
                    "scoring_mode": "all_or_nothing",
                    "short_answer": {"accepted_answers": [{"value": "42", "score": 1}]},
                },
                "max_score": 1,
            }
        ],
    )
    await db.flush()

    touched = (
        await db.execute(text("SELECT updated_at FROM tasks WHERE id = :tid"), {"tid": task_id})
    ).scalar()
    assert touched is not None
