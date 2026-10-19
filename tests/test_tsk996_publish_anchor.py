"""tsk-996 (фаза C): якоря заданий от публикатора ContentBackbone через bulk-upsert.

ADR-0008 «Владение якорем», постановка `docs/briefs/2026-10-19-p7-anchor-implementation.md`,
шаги 12–14. Публикатор шлёт у задания `anchor_external_uid` — external_uid
материала H2-раздела, под которым задание стоит в уроке (`{base}#m{N}`).
Здесь проверяется приём на стороне LMS:

* якорь резолвится только среди материалов курса задания; чужой узел или
  несуществующий материал — предупреждение в ответе, задание записано, пакет
  не падает;
* `publish` и пустой якорь переиздание перезаписывает, `manual` — нет (в том
  числе «в конце раздела» — `manual` при пустом якоре);
* поле не пришло — якорь не меняется; явный null снимает только якорь публикатора;
* пересортировка по сложности (`_reorder_tasks_by_difficulty`) меняет порядок
  внутри группы, но не разбивает группы: каждое задание остаётся за своим
  материалом, цепочка next-item проходит каждый элемент ровно один раз;
* материал, появившийся позже задания, подхватывается следующим переизданием;
* скрипт снятия якорей публикатора: план ничего не пишет, `--apply` снимает
  только `publish`.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import Settings
from scripts.tsk996_clear_publish_anchors import clear_publish_anchors
from tests.test_tsk996_anchor import M, T, _anchor, _anchor_row, _assert_once, _chain_items
from tests.test_tsk996_node_sequence import _course, _student

_settings = Settings()

EASY, NORMAL, HARD = 2, 3, 4


# ─── Помощники: тот же поток, что у публикатора CB ──────────────────────────


def _api_key() -> dict[str, str]:
    return {"api_key": next(iter(_settings.valid_api_keys))}


async def _publish_materials(client, course_id: int, base: str, n: int) -> list[str]:
    """bulk-upsert материалов `{base}#m1..#mN` (публикатор шлёт их раньше заданий)."""
    uids = [f"{base}#m{i}" for i in range(1, n + 1)]
    items = [
        {
            "course_id": course_id,
            "external_uid": uid,
            "title": f"Раздел {i}",
            "type": "text",
            "content": {"text": f"<p>Теория раздела {i} урока про якоря</p>", "format": "html"},
            "order_position": i,
        }
        for i, uid in enumerate(uids, start=1)
    ]
    r = await client.post("/api/v1/materials/bulk-upsert", params=_api_key(), json={"items": items})
    assert r.status_code == 200, r.text
    return uids


def _task_item(uid: str, course_id: int, difficulty_id: int = NORMAL, **extra: Any) -> dict:
    item = {
        "external_uid": uid,
        "course_id": course_id,
        "difficulty_id": difficulty_id,
        "task_content": {
            "type": "SC", "stem": f"Вопрос {uid}",
            "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
        },
        "solution_rules": {"type": "SC", "correct_options": ["a"], "max_score": 1},
        "max_score": 1,
    }
    item.update(extra)
    return item


async def _publish_tasks(client, items: list[dict]) -> dict:
    r = await client.post("/api/v1/tasks/bulk-upsert", params=_api_key(), json={"items": items})
    assert r.status_code == 200, r.text
    return r.json()


async def _material_id(db, course_id: int, uid: str) -> int:
    return int(
        (
            await db.execute(
                text("SELECT id FROM materials WHERE course_id = :c AND external_uid = :u"),
                {"c": course_id, "u": uid},
            )
        ).scalar_one()
    )


async def _task_id(db, uid: str) -> int:
    return int(
        (await db.execute(text("SELECT id FROM tasks WHERE external_uid = :u"), {"u": uid}))
        .scalar_one()
    )


async def _lesson(db, client) -> dict[str, Any]:
    """Урок «теория — задания — теория — задания», опубликованный как CB.

    Раздел 1: q1 (HARD), q2 (EASY); раздел 2: q3 (EASY), q4 (NORMAL). В пакете
    задания идут в порядке урока; пересортировка по сложности ставит позиции
    q2 q3 q4 q1 — группы разделов по позициям перемешаны.
    """
    root = await _course(db, "публикация якорей")
    base = f"wp:tsk996c:{uuid4().hex[:8]}"
    m_uids = await _publish_materials(client, root, base, 2)
    q = [f"{base}#q{i}" for i in range(1, 5)]
    body = await _publish_tasks(client, [
        _task_item(q[0], root, HARD, anchor_external_uid=m_uids[0]),
        _task_item(q[1], root, EASY, anchor_external_uid=m_uids[0]),
        _task_item(q[2], root, EASY, anchor_external_uid=m_uids[1]),
        _task_item(q[3], root, NORMAL, anchor_external_uid=m_uids[1]),
    ])
    assert body["warnings"] == []
    s: dict[str, Any] = {"root": root, "base": base, "m_uids": m_uids, "q_uids": q}
    s["m1"] = await _material_id(db, root, m_uids[0])
    s["m2"] = await _material_id(db, root, m_uids[1])
    for i, uid in enumerate(q, start=1):
        s[f"t{i}"] = await _task_id(db, uid)
    return s


# ─── Приём и резолв ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_sets_anchor_to_own_section(db, client):
    """Каждое задание получает якорь на материал своего раздела, источник publish."""
    s = await _lesson(db, client)
    assert await _anchor_row(db, s["t1"]) == (s["m1"], "publish")
    assert await _anchor_row(db, s["t2"]) == (s["m1"], "publish")
    assert await _anchor_row(db, s["t3"]) == (s["m2"], "publish")
    assert await _anchor_row(db, s["t4"]) == (s["m2"], "publish")


@pytest.mark.asyncio
async def test_reorder_by_difficulty_keeps_groups_and_chain_once(db, client, monkeypatch):
    """Пересортировка по сложности не разбивает группы; next-item — каждый элемент раз.

    Позиции после пересортировки: q2 q3 q4 q1 (EASY, EASY, NORMAL, HARD) —
    задания разделов по позициям перемешаны. Порядок узла: материал 1 → его
    задания по позиции (q2, q1) → материал 2 → его задания (q3, q4).
    """
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await _lesson(db, client)
    positions = dict(
        (
            await db.execute(
                text("SELECT id, order_position FROM tasks WHERE course_id = :c"),
                {"c": s["root"]},
            )
        ).fetchall()
    )
    assert sorted(positions, key=positions.get) == [s["t2"], s["t3"], s["t4"], s["t1"]], (
        "пересортировка по сложности не сработала — тест не проверяет то, что заявлено"
    )
    student = await _student(db, s["root"])
    await db.commit()
    chain = await _chain_items(db, student, s["root"])
    _assert_once(chain, [M(s["m1"]), T(s["t2"]), T(s["t1"]), M(s["m2"]), T(s["t3"]), T(s["t4"])])

    # Новое задание раздела 1 (EASY) — снова пересортировка; группы держатся.
    q5 = f"{s['base']}#q5"
    await _publish_tasks(client, [_task_item(q5, s["root"], EASY, anchor_external_uid=s["m_uids"][0])])
    t5 = await _task_id(db, q5)
    await db.commit()
    chain = await _chain_items(db, student, s["root"])
    group1 = chain[1:chain.index(M(s["m2"]))]
    assert set(group1) == {T(s["t1"]), T(s["t2"]), T(t5)}, chain
    _assert_once(chain, [M(s["m1"]), *group1, M(s["m2"]), T(s["t3"]), T(s["t4"])])


@pytest.mark.asyncio
async def test_without_anchors_tasks_after_all_materials(db, client, monkeypatch):
    """Контроль: тот же урок без поля — прежний порядок «все материалы, затем задания»."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    root = await _course(db, "без якорей")
    base = f"wp:tsk996c:{uuid4().hex[:8]}"
    m_uids = await _publish_materials(client, root, base, 2)
    q = [f"{base}#q{i}" for i in (1, 2)]
    body = await _publish_tasks(client, [_task_item(q[0], root, EASY), _task_item(q[1], root, EASY)])
    assert body["warnings"] == []
    t1, t2 = await _task_id(db, q[0]), await _task_id(db, q[1])
    assert await _anchor_row(db, t1) == (None, None)
    student = await _student(db, root)
    await db.commit()
    chain = await _chain_items(db, student, root)
    m1, m2 = await _material_id(db, root, m_uids[0]), await _material_id(db, root, m_uids[1])
    _assert_once(chain, [M(m1), M(m2), T(t1), T(t2)])


@pytest.mark.asyncio
async def test_foreign_or_missing_material_warns_without_failing(db, client):
    """Чужой узел и несуществующий материал — предупреждение; задания записаны без якоря."""
    s = await _lesson(db, client)
    other = await _course(db, "чужой узел")
    other_base = f"wp:tsk996c:{uuid4().hex[:8]}"
    other_m = (await _publish_materials(client, other, other_base, 1))[0]
    q_foreign, q_missing, q_ok = (f"{s['base']}#q{i}" for i in (11, 12, 13))
    body = await _publish_tasks(client, [
        _task_item(q_foreign, s["root"], anchor_external_uid=other_m),
        _task_item(q_missing, s["root"], anchor_external_uid=f"{s['base']}#m99"),
        _task_item(q_ok, s["root"], anchor_external_uid=s["m_uids"][1]),
    ])
    assert [r["action"] for r in body["results"]] == ["created"] * 3
    assert len(body["warnings"]) == 2, body["warnings"]
    assert q_foreign in body["warnings"][0] and "другого раздела" in body["warnings"][0]
    assert q_missing in body["warnings"][1] and "нет в разделе" in body["warnings"][1]
    assert await _anchor_row(db, await _task_id(db, q_foreign)) == (None, None)
    assert await _anchor_row(db, await _task_id(db, q_missing)) == (None, None)
    assert await _anchor_row(db, await _task_id(db, q_ok)) == (s["m2"], "publish")

    # Существующий якорь publish при ненайденном материале не обнуляется.
    body = await _publish_tasks(client, [
        _task_item(s["q_uids"][0], s["root"], HARD, anchor_external_uid=f"{s['base']}#m99"),
    ])
    assert len(body["warnings"]) == 1
    assert await _anchor_row(db, s["t1"]) == (s["m1"], "publish")


@pytest.mark.asyncio
async def test_hard_routed_task_gets_no_anchor_and_no_warning(db, client):
    """HARD уходит в подкурс сложных (tsk-347): материала раздела там нет — якоря нет,
    пакет не падает и предупреждением не шумит; прежний якорь снимает триггер переезда."""
    s = await _lesson(db, client)
    twin = await _course(db, "сложные")
    await db.execute(
        text("UPDATE courses SET course_uid = :u WHERE id = :c"),
        {"u": f"lms:tsk347:hard:{s['root']}", "c": twin},
    )
    await db.commit()
    body = await _publish_tasks(client, [
        _task_item(s["q_uids"][0], s["root"], HARD, anchor_external_uid=s["m_uids"][0]),
    ])
    assert body["warnings"] == []
    row = (
        await db.execute(text("SELECT course_id FROM tasks WHERE id = :t"), {"t": s["t1"]})
    ).scalar_one()
    assert row == twin
    assert await _anchor_row(db, s["t1"]) == (None, None)


# ─── Владение якорем ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_republish_overwrites_publish_anchor(db, client):
    """Якорь publish перезаписывается переизданием (задание переехало в другой раздел)."""
    s = await _lesson(db, client)
    await _publish_tasks(client, [
        _task_item(s["q_uids"][0], s["root"], HARD, anchor_external_uid=s["m_uids"][1]),
    ])
    assert await _anchor_row(db, s["t1"]) == (s["m2"], "publish")


@pytest.mark.asyncio
async def test_republish_sets_anchor_on_task_without_anchor(db, client):
    """Задание, опубликованное до фазы C (якоря нет), получает якорь при переиздании."""
    root = await _course(db, "до фазы C")
    base = f"wp:tsk996c:{uuid4().hex[:8]}"
    m_uids = await _publish_materials(client, root, base, 2)
    uid = f"{base}#q1"
    await _publish_tasks(client, [_task_item(uid, root)])
    tid = await _task_id(db, uid)
    assert await _anchor_row(db, tid) == (None, None)
    await _publish_tasks(client, [_task_item(uid, root, anchor_external_uid=m_uids[1])])
    assert await _anchor_row(db, tid) == (await _material_id(db, root, m_uids[1]), "publish")


@pytest.mark.asyncio
async def test_manual_anchor_survives_republish(db, client, monkeypatch):
    """Ручной якорь методиста и его «в конце раздела» переиздание не трогает."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await _lesson(db, client)
    # Методист перенёс q3 к материалу 1, а q4 — «в конце раздела».
    await _anchor(db, s["t3"], s["m1"], source="manual")
    await _anchor(db, s["t4"], None, source="manual")
    await db.commit()
    student = await _student(db, s["root"])
    await db.commit()
    before = await _chain_items(db, student, s["root"])

    body = await _publish_tasks(client, [
        _task_item(uid, s["root"], d, anchor_external_uid=s["m_uids"][sect])
        for uid, d, sect in zip(s["q_uids"], (HARD, EASY, EASY, NORMAL), (0, 0, 1, 1))
    ])
    assert body["warnings"] == []
    assert await _anchor_row(db, s["t3"]) == (s["m1"], "manual")
    assert await _anchor_row(db, s["t4"]) == (None, "manual")
    # Явный null публикатора ручной якорь тоже не снимает.
    await _publish_tasks(client, [_task_item(s["q_uids"][2], s["root"], EASY, anchor_external_uid=None)])
    assert await _anchor_row(db, s["t3"]) == (s["m1"], "manual")
    await db.commit()
    assert await _chain_items(db, student, s["root"]) == before, "переиздание сдвинуло ручной порядок"


@pytest.mark.asyncio
async def test_absent_field_keeps_anchor_and_null_clears_publish(db, client):
    """Поле не пришло (старый публикатор, --no-anchors) — якорь прежний; null — снять publish."""
    s = await _lesson(db, client)
    await _publish_tasks(client, [_task_item(s["q_uids"][0], s["root"], HARD)])
    assert await _anchor_row(db, s["t1"]) == (s["m1"], "publish")

    await _publish_tasks(client, [
        _task_item(s["q_uids"][0], s["root"], HARD, anchor_external_uid=None),
    ])
    assert await _anchor_row(db, s["t1"]) == (None, None)


@pytest.mark.asyncio
async def test_material_published_later_is_picked_up_on_republish(db, client):
    """Материала раздела ещё нет — предупреждение; следующее переиздание ставит якорь."""
    root = await _course(db, "материал позже")
    base = f"wp:tsk996c:{uuid4().hex[:8]}"
    uid = f"{base}#q1"
    body = await _publish_tasks(client, [_task_item(uid, root, anchor_external_uid=f"{base}#m1")])
    assert len(body["warnings"]) == 1
    tid = await _task_id(db, uid)
    assert await _anchor_row(db, tid) == (None, None)
    m_uids = await _publish_materials(client, root, base, 1)
    body = await _publish_tasks(client, [_task_item(uid, root, anchor_external_uid=m_uids[0])])
    assert body["warnings"] == []
    assert await _anchor_row(db, tid) == (await _material_id(db, root, m_uids[0]), "publish")


@pytest.mark.asyncio
async def test_anchor_does_not_bump_updated_at(db, client):
    """Смена якоря не двигает отметку правки (tsk-760): переиздание не сочтёт задание правленным."""
    s = await _lesson(db, client)
    row = "SELECT updated_at FROM tasks WHERE id = :t"
    before = (await db.execute(text(row), {"t": s["t1"]})).scalar()
    await _publish_tasks(client, [
        _task_item(s["q_uids"][0], s["root"], HARD, anchor_external_uid=s["m_uids"][1]),
    ])
    assert await _anchor_row(db, s["t1"]) == (s["m2"], "publish")
    assert (await db.execute(text(row), {"t": s["t1"]})).scalar() == before


# ─── Скрипт снятия якорей публикатора ──────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_script_dry_run_and_apply(db, client):
    """План ничего не пишет; --apply снимает только publish выбранных курсов."""
    s = await _lesson(db, client)
    other = await _lesson(db, client)
    await _anchor(db, s["t3"], s["m1"], source="manual")

    plan = await clear_publish_anchors(db, [s["root"]], apply=False)
    assert plan["publish_anchors"] == 3 and plan["cleared"] == 0
    assert plan["manual_anchors_untouched"] == 1
    assert await _anchor_row(db, s["t1"]) == (s["m1"], "publish")

    done = await clear_publish_anchors(db, [s["root"]], apply=True)
    assert done["cleared"] == 3
    for key in ("t1", "t2", "t4"):
        assert await _anchor_row(db, s[key]) == (None, None)
    assert await _anchor_row(db, s["t3"]) == (s["m1"], "manual")
    assert await _anchor_row(db, other["t1"]) == (other["m1"], "publish"), "чужой курс тронут"

    again = await clear_publish_anchors(db, [s["root"]], apply=True)
    assert again["publish_anchors"] == 0 and again["cleared"] == 0


@pytest.mark.asyncio
async def test_clear_script_refuses_empty_course_list(db):
    with pytest.raises(ValueError):
        await clear_publish_anchors(db, [], apply=True)


def test_clear_script_cli_requires_target():
    from scripts.tsk996_clear_publish_anchors import main

    with pytest.raises(SystemExit):
        main([])
