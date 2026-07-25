"""tsk-378: переиздание через bulk-upsert не реактивирует и не двигает материал.

Тот же класс дефекта, что tsk-377 закрыл для `requirement_level` («дефолт схемы
затирает состояние при UPDATE через bulk-upsert»), теперь для двух соседних
полей:

1. `is_active` (задания и материалы) — ни `TaskPayload`, ни `MaterialPayload`
   в `D:\\Work\\ContentBackbone\\monolith\\lms_client\\contracts.py` поле не
   имеют/не шлют его, если явно не задано (`material_payload_to_dict` кладёт
   ключ в JSON только при `p.is_active is not None`). Сервис раньше
   материализовал дефолт схемы `True` на UPDATE — переиздание молча
   возвращало элемент, погашенный методистом (tsk-112).
2. `order_position` материалов — `MaterialsService.bulk_upsert` раньше клал
   в UPDATE `None`, если клиент его не прислал; колонка nullable, поэтому
   `trg_set_material_order_position` (`docs/database-triggers-contract.md`
   §7) трактовал `NULL` на UPDATE как «поставить следующий номер» — материал
   уезжал в конец курса. У заданий тот же класс закрыт в tsk-345 (там
   `order_position` в UPDATE-словарь просто не кладётся, если не передан).

Payload переиздания — ровно те ключи, которые кладут `task_payload_to_dict` /
`material_payload_to_dict`, а не синтетический полный (дефект жил в
материализации дефолта схемой, вызов сервиса напрямую его не воспроизводит).
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.core.config import Settings

_settings = Settings()

TASKS_URL = "/api/v1/tasks/bulk-upsert"
MATERIALS_URL = "/api/v1/materials/bulk-upsert"

EASY = 2  # не HARD: маршрутизация tsk-347 в блок сложных здесь не участвует


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


async def _new_course(db) -> int:
    row = (
        await db.execute(
            text(
                "INSERT INTO courses (title, description, access_level, is_required, course_uid) "
                "VALUES (:t, 'tsk-378', 'self_guided', false, :uid) RETURNING id"
            ),
            {"t": "test_tsk378_is_active_order_position", "uid": f"lms:test:tsk378:{uuid.uuid4().hex[:12]}"},
        )
    ).first()
    await db.flush()
    return int(row.id)


def _cb_task_payload(external_uid: str, course_id: int) -> dict[str, Any]:
    """Ровно то, что кладёт ContentBackbone `task_payload_to_dict`: is_active в ключах нет."""
    return {
        "external_uid": external_uid,
        "course_id": course_id,
        "task_content": {"type": "SA_COM", "stem": "tsk-378", "accepted_answers": ["1"]},
        "solution_rules": {"type": "SC", "correct_options": ["a"], "max_score": 1},
        "difficulty_id": EASY,
        "max_score": 1,
    }


def _cb_material_payload(
    external_uid: str, course_id: int, *, order_position: int | None = None
) -> dict[str, Any]:
    """Ровно то, что кладёт `material_payload_to_dict`: ключ есть, только если значение не None."""
    d: dict[str, Any] = {
        "course_id": course_id,
        "external_uid": external_uid,
        "title": "tsk-378 материал",
        "type": "link",
        "content": {"url": "https://example.com/tsk378"},
    }
    if order_position is not None:
        d["order_position"] = order_position
    return d


async def _post_tasks(client, items: list[dict[str, Any]]):
    return await client.post(TASKS_URL, params={"api_key": _api_key()}, json={"items": items})


async def _post_materials(client, items: list[dict[str, Any]]):
    return await client.post(MATERIALS_URL, params={"api_key": _api_key()}, json={"items": items})


async def _task_row(db, external_uid: str) -> tuple[bool, int]:
    row = (
        await db.execute(
            text("SELECT is_active, order_position FROM tasks WHERE external_uid = :uid"),
            {"uid": external_uid},
        )
    ).first()
    assert row is not None, f"задание {external_uid} не найдено"
    return bool(row.is_active), row.order_position


async def _material_row(db, course_id: int, external_uid: str) -> tuple[bool, int]:
    row = (
        await db.execute(
            text(
                "SELECT is_active, order_position FROM materials "
                "WHERE course_id = :cid AND external_uid = :uid"
            ),
            {"cid": course_id, "uid": external_uid},
        )
    ).first()
    assert row is not None, f"материал {external_uid} не найден"
    return bool(row.is_active), row.order_position


# --------------------------------------------------------------------------
# Задания — is_active
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_explicit_is_active_still_applied(client, db):
    """Явно переданный is_active по-прежнему перезаписывает существующий (оба направления)."""
    course_id = await _new_course(db)
    uid = f"tsk378-task-{uuid.uuid4().hex[:8]}"

    await _post_tasks(client, [_cb_task_payload(uid, course_id)])
    assert (await _task_row(db, uid))[0] is True

    resp = await _post_tasks(client, [{**_cb_task_payload(uid, course_id), "is_active": False}])
    assert resp.status_code == 200, resp.text
    assert (await _task_row(db, uid))[0] is False

    resp = await _post_tasks(client, [{**_cb_task_payload(uid, course_id), "is_active": True}])
    assert resp.status_code == 200, resp.text
    assert (await _task_row(db, uid))[0] is True


@pytest.mark.asyncio
async def test_task_create_defaults_active(client, db):
    """CREATE без is_active — прежний дефолт `True` (регресс)."""
    course_id = await _new_course(db)
    uid = f"tsk378-task-{uuid.uuid4().hex[:8]}"

    resp = await _post_tasks(client, [_cb_task_payload(uid, course_id)])
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["action"] == "created"
    assert (await _task_row(db, uid))[0] is True


# --------------------------------------------------------------------------
# Материалы — is_active
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_material_explicit_is_active_still_applied(client, db):
    """Явно переданный is_active материала по-прежнему применяется (оба направления)."""
    course_id = await _new_course(db)
    uid = f"tsk378-mat-{uuid.uuid4().hex[:8]}"

    await _post_materials(client, [_cb_material_payload(uid, course_id)])
    assert (await _material_row(db, course_id, uid))[0] is True

    resp = await _post_materials(client, [{**_cb_material_payload(uid, course_id), "is_active": False}])
    assert resp.status_code == 200, resp.text
    assert (await _material_row(db, course_id, uid))[0] is False

    resp = await _post_materials(client, [{**_cb_material_payload(uid, course_id), "is_active": True}])
    assert resp.status_code == 200, resp.text
    assert (await _material_row(db, course_id, uid))[0] is True


@pytest.mark.asyncio
async def test_material_create_defaults_active(client, db):
    """CREATE материала без is_active — прежний дефолт `True` (регресс)."""
    course_id = await _new_course(db)
    uid = f"tsk378-mat-{uuid.uuid4().hex[:8]}"

    resp = await _post_materials(client, [_cb_material_payload(uid, course_id)])
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["status"] == "created", resp.text
    assert (await _material_row(db, course_id, uid))[0] is True


# --------------------------------------------------------------------------
# Материалы — order_position
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_material_reissue_without_position_does_not_move_to_end(client, db):
    """Главный регресс: переиздание без order_position не утаскивает материал в конец курса.

    Курс из трёх материалов; переиздаём СРЕДНИЙ payload'ом без order_position
    (как шлёт CB, когда позиция неизвестна на этом шаге) — позиция должна
    остаться прежней, а не съехать на MAX+1 (описание бага в tsk-378).
    """
    course_id = await _new_course(db)
    uid_a = f"tsk378-mat-{uuid.uuid4().hex[:8]}"
    uid_b = f"tsk378-mat-{uuid.uuid4().hex[:8]}"
    uid_c = f"tsk378-mat-{uuid.uuid4().hex[:8]}"

    resp = await _post_materials(
        client,
        [
            _cb_material_payload(uid_a, course_id, order_position=1),
            _cb_material_payload(uid_b, course_id, order_position=2),
            _cb_material_payload(uid_c, course_id, order_position=3),
        ],
    )
    assert resp.status_code == 200, resp.text
    assert (await _material_row(db, course_id, uid_b))[1] == 2

    # Round-trip переиздание среднего материала без order_position в payload.
    resp = await _post_materials(client, [_cb_material_payload(uid_b, course_id)])
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["status"] == "unchanged", resp.text

    position, = ((await _material_row(db, course_id, uid_b))[1],)
    assert position == 2, "переиздание без order_position утащило материал в конец курса"


@pytest.mark.asyncio
async def test_material_explicit_position_still_applied(client, db):
    """Явно переданный order_position по-прежнему двигает материал (тот же payload с новым значением)."""
    course_id = await _new_course(db)
    uid_a = f"tsk378-mat-{uuid.uuid4().hex[:8]}"
    uid_b = f"tsk378-mat-{uuid.uuid4().hex[:8]}"

    await _post_materials(
        client,
        [
            _cb_material_payload(uid_a, course_id, order_position=1),
            _cb_material_payload(uid_b, course_id, order_position=2),
        ],
    )
    assert (await _material_row(db, course_id, uid_a))[1] == 1

    resp = await _post_materials(client, [_cb_material_payload(uid_a, course_id, order_position=2)])
    assert resp.status_code == 200, resp.text

    assert (await _material_row(db, course_id, uid_a))[1] == 2
    assert (await _material_row(db, course_id, uid_b))[1] == 1


@pytest.mark.asyncio
async def test_material_create_without_position_defaults_to_trigger(client, db):
    """CREATE без order_position — прежнее поведение: триггер ставит MAX+1 (регресс)."""
    course_id = await _new_course(db)
    uid_a = f"tsk378-mat-{uuid.uuid4().hex[:8]}"
    uid_b = f"tsk378-mat-{uuid.uuid4().hex[:8]}"

    await _post_materials(client, [_cb_material_payload(uid_a, course_id, order_position=1)])
    resp = await _post_materials(client, [_cb_material_payload(uid_b, course_id)])
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["status"] == "created", resp.text

    assert (await _material_row(db, course_id, uid_b))[1] == 2


@pytest.mark.asyncio
async def test_material_reissue_without_is_active_or_position_is_unchanged(client, db):
    """Отсутствие is_active/order_position в payload не считается изменением: статус `unchanged`.

    Иначе сервис сообщал бы `updated` там, где ничего не менял (тот же риск,
    что tsk-377 закрыл для requirement_level).
    """
    course_id = await _new_course(db)
    uid = f"tsk378-mat-{uuid.uuid4().hex[:8]}"

    await _post_materials(client, [_cb_material_payload(uid, course_id, order_position=1)])
    resp = await _post_materials(client, [_cb_material_payload(uid, course_id)])
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["status"] == "unchanged", resp.text
