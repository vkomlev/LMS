"""tsk-381: происхождение оценки сложности переживает переиздание, но не смену уровня.

По `tasks.difficulty_id` нельзя было сказать, откуда взялось значение: канон,
дефолт импорта или ручная правка. Из-за этого понадобилась сверка задним числом
по всей партии Крылова (tsk-355, tsk-381). Колонка `difficulty_provenance`
хранит обоснование рядом со значением.

Ключевое свойство, которое тут сторожится: обоснование НЕ должно пережить смену
самой оценки. Иначе поле будет уверенно описывать уже не то значение — это хуже,
чем отсутствие обоснования, потому что выглядит достоверно.

Проверка идёт ЧЕРЕЗ ЭНДПОИНТ: как и в tsk-377, поведение зависит от
`exclude_unset=True` при сериализации payload, вызов сервиса напрямую его не
воспроизводит.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional

import pytest
from sqlalchemy import text

from app.core.config import Settings

_settings = Settings()

TASKS_URL = "/api/v1/tasks/bulk-upsert"

EASY = 2
NORMAL = 3  # не HARD: маршрутизация tsk-347 в блок сложных здесь не участвует

PROVENANCE = {
    "canon": 1,
    "source": "tg:cyberguru_ege",
    "evidence": "посты 775: простой",
    "decided_at": "2026-07-23",
    "task": "tsk-381",
}


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


async def _new_course(db) -> int:
    """Курс без подкурса сложных — чистая ветка bulk-upsert."""
    row = (
        await db.execute(
            text(
                "INSERT INTO courses (title, description, access_level, is_required, course_uid) "
                "VALUES (:t, 'tsk-381', 'self_guided', false, :uid) RETURNING id"
            ),
            {"t": "test_tsk381_provenance", "uid": f"lms:test:tsk381:{uuid.uuid4().hex[:12]}"},
        )
    ).first()
    await db.flush()
    return int(row.id)


def _cb_task_payload(external_uid: str, course_id: int, difficulty_id: int = EASY) -> dict[str, Any]:
    """Ровно то, что кладёт ContentBackbone: обоснования в ключах нет."""
    return {
        "external_uid": external_uid,
        "course_id": course_id,
        "task_content": {"type": "SA_COM", "stem": "tsk-381", "accepted_answers": ["1"]},
        "solution_rules": {"type": "SC", "correct_options": ["a"], "max_score": 1},
        "difficulty_id": difficulty_id,
        "max_score": 1,
    }


async def _post(client, items: list[dict[str, Any]]):
    return await client.post(TASKS_URL, params={"api_key": _api_key()}, json={"items": items})


async def _provenance(db, external_uid: str) -> Optional[dict[str, Any]]:
    row = (
        await db.execute(
            text("SELECT difficulty_provenance FROM tasks WHERE external_uid = :uid"),
            {"uid": external_uid},
        )
    ).first()
    assert row is not None, f"задание {external_uid} не найдено"
    value = row.difficulty_provenance
    return json.loads(value) if isinstance(value, str) else value


@pytest.mark.asyncio
async def test_provenance_survives_reissue(client, db):
    """Переиздание тем же уровнем не теряет обоснование (урок tsk-377)."""
    course_id = await _new_course(db)
    uid = f"tsk381-keep-{uuid.uuid4().hex[:8]}"

    resp = await _post(client, [{**_cb_task_payload(uid, course_id), "difficulty_provenance": PROVENANCE}])
    assert resp.status_code == 200, resp.text
    assert (await _provenance(db, uid)) == PROVENANCE

    # Доливка конвейера: обоснования в payload нет, уровень тот же.
    resp = await _post(client, [_cb_task_payload(uid, course_id)])
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["action"] == "updated"

    assert (await _provenance(db, uid)) == PROVENANCE, (
        "переиздание стёрло обоснование, хотя уровень не менялся"
    )


@pytest.mark.asyncio
async def test_provenance_dropped_when_difficulty_changes(client, db):
    """Молчаливая смена уровня импортом обнуляет обоснование."""
    course_id = await _new_course(db)
    uid = f"tsk381-drop-{uuid.uuid4().hex[:8]}"

    await _post(client, [{**_cb_task_payload(uid, course_id, EASY), "difficulty_provenance": PROVENANCE}])
    assert (await _provenance(db, uid)) == PROVENANCE

    # Конвейер переклассифицировал задание и обоснования не прислал.
    resp = await _post(client, [_cb_task_payload(uid, course_id, NORMAL)])
    assert resp.status_code == 200, resp.text

    assert (await _provenance(db, uid)) is None, (
        "обоснование пережило смену уровня и теперь описывает не то значение"
    )


@pytest.mark.asyncio
async def test_explicit_provenance_wins_on_difficulty_change(client, db):
    """Явно переданное обоснование применяется вместе с новым уровнем."""
    course_id = await _new_course(db)
    uid = f"tsk381-explicit-{uuid.uuid4().hex[:8]}"

    await _post(client, [{**_cb_task_payload(uid, course_id, EASY), "difficulty_provenance": PROVENANCE}])

    new_provenance = {**PROVENANCE, "canon": 3, "source": "kompege", "evidence": "difficulty=1 (средняя)"}
    resp = await _post(
        client,
        [{**_cb_task_payload(uid, course_id, NORMAL), "difficulty_provenance": new_provenance}],
    )
    assert resp.status_code == 200, resp.text

    assert (await _provenance(db, uid)) == new_provenance


@pytest.mark.asyncio
async def test_create_without_provenance_leaves_null(client, db):
    """Новое задание без обоснования остаётся неподтверждённым, а не выдуманным."""
    course_id = await _new_course(db)
    uid = f"tsk381-new-{uuid.uuid4().hex[:8]}"

    resp = await _post(client, [_cb_task_payload(uid, course_id)])
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["action"] == "created"

    assert (await _provenance(db, uid)) is None
