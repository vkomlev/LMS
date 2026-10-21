"""tsk-1334: следующий элемент курса в предпросмотре «глазами ученика».

Дерево: корень R (материал mR, задание tR, рекомендуемый материал mRec) и
подраздел C (материал mC, задание tC). Порядок tsk-1250: материалы узла,
подразделы, задания узла → mR, mC, tC, tR. Рекомендуемый материал в обходе
не выдаётся, но с него идти можно. Прогресс не учитывается, ничего не пишется.

Дерево живёт в общей откатываемой транзакции фикстуры `db` (tsk-333): ручка
только читает и ходит через `get_async_db`, параллельные соединения ей не нужны,
поэтому свой движок с реальными коммитами и ручной уборкой здесь лишний.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.api.main import app
from app.api.deps import get_current_user
from app.auth.current_user import CurrentUser

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(scope="function")
async def tree(db):
    """Дерево R → C с материалами и заданиями, преподаватель и ученик."""
    ids: dict[str, int] = {}

    async def course(title: str) -> int:
        return (
            await db.execute(
                text(
                    "INSERT INTO courses (title, access_level) "
                    "VALUES (:t, 'self_guided') RETURNING id"
                ),
                {"t": f"tsk1334 {title}"},
            )
        ).scalar()

    async def material(course_id: int, level: str, pos: int) -> int:
        return (
            await db.execute(
                text(
                    "INSERT INTO materials (course_id, title, type, content, order_position, "
                    "requirement_level, is_active) VALUES (:c, 'tsk1334', 'text', "
                    "CAST(:body AS jsonb), :p, :lvl, true) RETURNING id"
                ),
                {"c": course_id, "body": json.dumps({"body": "x"}), "p": pos, "lvl": level},
            )
        ).scalar()

    async def task(course_id: int) -> int:
        return (
            await db.execute(
                text(
                    "INSERT INTO tasks (task_content, solution_rules, course_id, "
                    "difficulty_id, external_uid, max_attempts, is_active) VALUES "
                    "(CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, "
                    "(SELECT id FROM difficulties ORDER BY id LIMIT 1), :uid, 3, true) "
                    "RETURNING id"
                ),
                {
                    "tc": '{"type":"SC","stem":"?","options":[{"id":"a","text":"1"}]}',
                    "sr": '{"max_score":1,"correct_options":["a"]}',
                    "cid": course_id,
                    "uid": f"tsk1334-{uuid.uuid4().hex[:12]}",
                },
            )
        ).scalar()

    ids["R"] = await course("корень")
    ids["C"] = await course("раздел")
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
            "VALUES (:c, :p, 1)"
        ),
        {"c": ids["C"], "p": ids["R"]},
    )
    ids["mR"] = await material(ids["R"], "required", 1)
    ids["mRec"] = await material(ids["R"], "recommended", 2)
    ids["mC"] = await material(ids["C"], "required", 1)
    ids["tC"] = await task(ids["C"])
    ids["tR"] = await task(ids["R"])
    for key, role in (("teacher", "teacher"), ("student", "student")):
        ids[key] = (
            await db.execute(
                text("INSERT INTO users (full_name) VALUES (:n) RETURNING id"),
                {"n": f"tsk1334 {key}"},
            )
        ).scalar()
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, id FROM roles WHERE name = :r"
            ),
            {"u": ids[key], "r": role},
        )
    yield ids


async def _next(client, user_id: int, kind: str, item_id: int, root: int | None = None):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=user_id, is_service=False)
    try:
        params = {"kind": kind, "item_id": item_id}
        if root is not None:
            params["root_course_id"] = root
        return await client.get("/api/v1/check/preview/next-item", params=params)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_walks_tree_in_tsk1250_order(client, tree):
    """mR → mC → tC → tR → конец; рекомендуемый пропускается, но с него идут дальше."""
    chain = [("material", tree["mR"]), ("material", tree["mC"]), ("task", tree["tC"]),
             ("task", tree["tR"])]
    for (kind, item), (nkind, nitem) in zip(chain, chain[1:]):
        resp = await _next(client, tree["teacher"], kind, item, tree["R"])
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"root_course_id": tree["R"], "next_kind": nkind, "next_id": nitem}

    end = (await _next(client, tree["teacher"], "task", tree["tR"], tree["R"])).json()
    assert end["next_kind"] is None and end["next_id"] is None

    rec = (await _next(client, tree["teacher"], "material", tree["mRec"], tree["R"])).json()
    assert (rec["next_kind"], rec["next_id"]) == ("material", tree["mC"])


async def test_root_defaults_to_top_of_tree(client, tree):
    """Без корня (или с чужим) — корень над курсом элемента."""
    for root in (None, tree["C"] + 10_000_000):
        body = (await _next(client, tree["teacher"], "material", tree["mC"], root)).json()
        assert body == {"root_course_id": tree["R"], "next_kind": "task", "next_id": tree["tC"]}


async def test_subtree_root_ends_at_its_border(client, tree):
    """Открыт из раздела C — после tC курс закончился, в R не выходим."""
    body = (await _next(client, tree["teacher"], "task", tree["tC"], tree["C"])).json()
    assert body == {"root_course_id": tree["C"], "next_kind": None, "next_id": None}


async def test_student_forbidden_and_unknown_item_404(client, tree):
    assert (await _next(client, tree["student"], "material", tree["mR"])).status_code == 403
    assert (await _next(client, tree["teacher"], "task", 2_000_000_000)).status_code == 404
