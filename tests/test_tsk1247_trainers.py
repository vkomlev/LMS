"""tsk-1247: интерактивные тренажёры — бэкенд (шаг 3 плана).

Проверяется:
- схема `trainer-scenario/1`: оба пилотных сценария из
  `docs/design/trainers/scenarios/` проходят, битые — отказ (в ручках — 422 с
  понятной ошибкой); тренажёр всегда пишется уровнем `recommended`;
- выдача ученику при рубильнике `TRAINERS_ENABLED` вкл/выкл: материал,
  список курса, программа курса, развилка `checkpoint` у теории;
- порядок узла: при выключенном рубильнике выдача та же, что без тренажёра,
  тренажёр не блокирует next-item ни при каком положении рубильника, а «Да»
  на развилке (next-item после тренажёра) ведёт к следующему элементу;
- `GET /trainers/{slug}`: 404 / ETag / 304;
- `POST /trainers/{slug}/events`: вошедший, гость, встройка, отказ неизвестного
  кода ошибки, лимит пакета, длина ввода, лимит частоты;
- `trainer_suggestion` после неверного ответа — в ответе проверки и в next-item.
"""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.schemas.material_content import validate_material_content
from app.schemas.trainer_scenario import validate_scenario
from app.services import trainers_service
from test_help_ladder_endpoints_tsk303 import _bearer, _headers, _user

pytestmark = pytest.mark.asyncio

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "docs" / "design" / "trainers" / "scenarios"


def _scenario(name: str = "interpreter-input") -> dict:
    return json.loads((SCENARIOS_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def trainers_on(monkeypatch):
    monkeypatch.setenv("TRAINERS_ENABLED", "true")


@pytest.fixture
def trainers_off(monkeypatch):
    monkeypatch.setenv("TRAINERS_ENABLED", "false")


# ── данные ──────────────────────────────────────────────────────────────────


async def _course(db) -> int:
    return (
        await db.execute(
            text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
            {"t": f"t1247 курс {uuid.uuid4().hex[:8]}"},
        )
    ).scalar_one()


async def _material(
    db, course_id: int, *, mtype: str = "text", content: dict | None = None,
    position: int | None = None, level: str = "required", active: bool = True,
) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO materials (title, type, content, course_id, is_active, "
                "order_position, requirement_level, external_uid) "
                "VALUES (:t, CAST(:ty AS content_type), CAST(:c AS jsonb), :cid, :a, :p, :lvl, :e) "
                "RETURNING id"
            ),
            {
                "t": f"t1247 {mtype}", "ty": mtype,
                "c": json.dumps(content or {"text": "<p>Теория</p>", "format": "html"}, ensure_ascii=False),
                "cid": course_id, "a": active, "p": position, "lvl": level,
                "e": f"t1247:{uuid.uuid4().hex}",
            },
        )
    ).scalar_one()


async def _trainer(db, course_id: int, *, slug: str | None = None, position: int | None = None,
                   active: bool = True) -> tuple[int, str]:
    scenario = _scenario()
    slug = slug or f"t1247-{uuid.uuid4().hex[:10]}"
    scenario["slug"] = slug
    content = validate_material_content("trainer", {"slug": slug, "scenario": scenario})
    mid = await _material(
        db, course_id, mtype="trainer", content=content, position=position,
        level="recommended", active=active,
    )
    return mid, slug


async def _task(db, course_id: int, position: int | None = None) -> int:
    did = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar_one()
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, difficulty_id, "
                "external_uid, max_attempts, is_active, order_position) VALUES "
                "(CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 10, true, :p) RETURNING id"
            ),
            {
                "tc": '{"type":"SC","stem":"2+2?","options":[{"id":"a","text":"3"},{"id":"b","text":"4"}]}',
                "sr": '{"max_score":1,"correct_options":["b"]}',
                "cid": course_id, "did": did, "uid": f"t1247-task-{uuid.uuid4().hex[:12]}", "p": position,
            },
        )
    ).scalar_one()


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active, order_number) "
            "VALUES (:u, :c, true, 1) ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "c": course_id},
    )


async def _node(db, *, with_trainer: bool = True):
    """Узел «теория → тренажёр → задание», ученик зачислен.

    Тренажёр стоит позицией между теорией и заданием (в голове узла), у теории
    развилка на него.
    """
    sid, token = await _user(db, "t1247 ученик", with_session=True)
    cid = await _course(db)
    theory = await _material(db, cid, position=1)
    trainer_id, slug = (None, None)
    if with_trainer:
        trainer_id, slug = await _trainer(db, cid, position=2)
        await db.execute(
            text("UPDATE materials SET content = content || CAST(:cp AS jsonb) WHERE id = :m"),
            {"cp": json.dumps({"checkpoint": {"question": "Всё понятно?", "trainer_material_id": trainer_id}}),
             "m": theory},
        )
    task = await _task(db, cid, position=1)
    await _enroll(db, sid, cid)
    await db.commit()
    return {"sid": sid, "token": token, "cid": cid, "theory": theory,
            "trainer": trainer_id, "slug": slug, "task": task}


# ── 1. схема сценария ───────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["interpreter-input", "loop-machine"])
async def test_pilot_scenarios_pass_schema(name):
    scenario = _scenario(name)
    parsed = validate_scenario(scenario)
    assert parsed.slug == name
    stored = validate_material_content("trainer", {"slug": name, "scenario": scenario})
    # Сценарий хранится как прислан — без дописанных значений по умолчанию.
    assert stored == {"slug": name, "scenario": scenario}


def _broken(mutate):
    s = _scenario()
    mutate(s)
    return s


BROKEN = {
    "wrong_schema": lambda s: s.update(schema="trainer-scenario/2"),
    "unknown_misconception": lambda s: s["levels"][1]["steps"][0]["options"][0].update(misconception="nope"),
    "unused_misconception": lambda s: s["misconceptions"].update(
        spare={"label": "x", "level": 1, "tutor_hint": "x"}
    ),
    "wrong_option_without_code": lambda s: s["levels"][1]["steps"][0]["options"][0].pop("misconception"),
    "console_too_long": lambda s: s["levels"][1]["steps"][2].update(max_len=65),
    "typo_in_step_key": lambda s: s["levels"][1]["steps"][0].update(optoins=[]),
    "duplicate_step_id": lambda s: s["levels"][1]["steps"][1].update(id=s["levels"][1]["steps"][0]["id"]),
    "html_in_text": lambda s: s.update(intro="<script>alert(1)</script>"),
    "html_event_handler": lambda s: s.update(intro="<details open ontoggle=alert(1)>"),
    "js_link_with_newline": lambda s: s.update(subtitle="[x](java\nscript:alert(1))"),
    "bad_regex": lambda s: s["levels"][1]["steps"][2].update(accept="(["),
    "no_levels": lambda s: s.update(levels=[]),
    "unknown_level_kind": lambda s: s["levels"][0].update(kind="drag_drop"),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
async def test_broken_scenario_rejected(case):
    with pytest.raises(ValueError):
        validate_scenario(_broken(BROKEN[case]))


async def test_code_comparisons_are_not_html():
    """`a < b` в листинге и `i<n` в `коде` вопроса — не теги."""
    s = _scenario("loop-machine")
    s["levels"][0]["code"].append("while a < b:")
    s["levels"][0]["steps"][0]["q"] = "Что будет, если `i<n` неверно?"
    validate_scenario(s)


async def test_slug_must_match_scenario():
    with pytest.raises(ValueError, match="не совпадает"):
        validate_material_content("trainer", {"slug": "other", "scenario": _scenario()})


async def test_create_broken_trainer_returns_422(db, client):
    cid = await _course(db)
    await db.commit()
    bad = _broken(BROKEN["unknown_misconception"])
    resp = await client.post(
        "/api/v1/materials/",
        json={"course_id": cid, "title": "Тренажёр", "type": "trainer",
              "content": {"slug": "interpreter-input", "scenario": bad}},
        headers=_headers(),
    )
    assert resp.status_code == 422, resp.text
    assert "неизвестный код ошибки «nope»" in resp.text


async def test_create_trainer_forces_recommended(db, client):
    """Тренажёр не блокирует продвижение: уровень всегда recommended."""
    cid = await _course(db)
    await db.commit()
    resp = await client.post(
        "/api/v1/materials/",
        json={"course_id": cid, "title": "Тренажёр", "type": "trainer", "requirement_level": "skippable",
              "content": {"slug": "interpreter-input", "scenario": _scenario()}},
        headers=_headers(),
    )
    assert resp.status_code in (200, 201), resp.text
    assert resp.json()["requirement_level"] == "recommended"


async def test_bulk_upsert_validates_trainer(db, client):
    cid = await _course(db)
    await db.commit()
    good = {"course_id": cid, "external_uid": f"t1247:{uuid.uuid4().hex}", "title": "Цикл-машина",
            "type": "trainer", "content": {"slug": "loop-machine", "scenario": _scenario("loop-machine")}}
    bad = {**good, "external_uid": f"t1247:{uuid.uuid4().hex}",
           "content": {"slug": "loop-machine", "scenario": {**_scenario("loop-machine"), "levels": []}}}
    resp = await client.post("/api/v1/materials/bulk-upsert", json={"items": [good, bad]}, headers=_headers())
    # Ошибка валидации одного элемента — per-item, остальные пишутся.
    assert resp.status_code == 200, resp.text
    errors = [r for r in resp.json()["items"] if r.get("error_type") == "validation"]
    assert len(errors) == 1 and "levels" in errors[0]["error"], resp.text
    stored = (
        await db.execute(
            text("SELECT requirement_level, content->>'slug' FROM materials WHERE external_uid = :e"),
            {"e": good["external_uid"]},
        )
    ).first()
    bad_stored = (
        await db.execute(text("SELECT 1 FROM materials WHERE external_uid = :e"), {"e": bad["external_uid"]})
    ).first()
    assert bad_stored is None
    assert stored == ("recommended", "loop-machine")


async def test_put_content_without_type_validated(db, client):
    """Generic PUT без `type` у тренажёра всё равно проверяет сценарий."""
    cid = await _course(db)
    mid, _ = await _trainer(db, cid)
    await db.commit()
    resp = await client.put(
        f"/api/v1/materials/{mid}",
        json={"content": {"slug": "x", "scenario": {"schema": "trainer-scenario/1"}}, "requirement_level": "required"},
        headers=_headers(),
    )
    assert resp.status_code == 422, resp.text


async def test_text_checkpoint_kept_and_validated():
    out = validate_material_content(
        "text", {"text": "<p>x</p>", "format": "html", "checkpoint": {"trainer_material_id": 7}}
    )
    assert out["checkpoint"] == {"question": "Всё понятно?", "trainer_material_id": 7}
    # Без развилки content прежний — bulk-upsert не считает материал изменённым.
    assert validate_material_content("text", {"text": "<p>x</p>", "format": "html"}) == {
        "text": "<p>x</p>", "format": "html",
    }
    with pytest.raises(ValueError):
        validate_material_content("text", {"text": "x", "checkpoint": {"trainer_material_id": 0}})


# ── 2. выдача ученику и рубильник ───────────────────────────────────────────


async def test_switch_is_off_by_default(monkeypatch):
    monkeypatch.delenv("TRAINERS_ENABLED", raising=False)
    assert trainers_service.trainers_enabled() is False


async def test_switch_off_hides_trainer_and_checkpoint(db, client, trainers_off):
    n = await _node(db)
    h = _bearer(n["token"])
    assert (await client.get(f"/api/v1/materials/{n['trainer']}", headers=h)).status_code == 404
    items = (await client.get(f"/api/v1/courses/{n['cid']}/materials", headers=h)).json()["items"]
    assert [i["id"] for i in items] == [n["theory"]]
    assert "checkpoint" not in items[0]["content"]
    one = (await client.get(f"/api/v1/materials/{n['theory']}", headers=h)).json()
    assert "checkpoint" not in one["content"]
    # Методист/сервисный ключ видят всё как есть.
    svc = (await client.get(f"/api/v1/courses/{n['cid']}/materials", headers=_headers())).json()["items"]
    assert {i["id"] for i in svc} == {n["theory"], n["trainer"]}


async def test_switch_on_shows_trainer_and_checkpoint(db, client, trainers_on):
    n = await _node(db)
    h = _bearer(n["token"])
    tr = await client.get(f"/api/v1/materials/{n['trainer']}", headers=h)
    assert tr.status_code == 200, tr.text
    assert tr.json()["type"] == "trainer"
    assert tr.json()["requirement_level"] == "recommended"
    items = (await client.get(f"/api/v1/courses/{n['cid']}/materials", headers=h)).json()["items"]
    assert [i["id"] for i in items] == [n["theory"], n["trainer"]]
    assert items[0]["content"]["checkpoint"] == {"question": "Всё понятно?", "trainer_material_id": n["trainer"]}


async def test_checkpoint_to_inactive_trainer_dropped(db, client, trainers_on):
    n = await _node(db)
    await db.execute(text("UPDATE materials SET is_active = false WHERE id = :m"), {"m": n["trainer"]})
    await db.commit()
    one = (await client.get(f"/api/v1/materials/{n['theory']}", headers=_bearer(n["token"]))).json()
    assert "checkpoint" not in one["content"]


async def _syllabus(client, n) -> list:
    resp = await client.get(f"/api/v1/me/courses/{n['cid']}/syllabus-states", headers=_bearer(n["token"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return [
        (i["kind"], i.get("material_id") or i.get("task_id"), i.get("requirement_level"))
        for i in body["items"]
    ]


async def test_syllabus_order_with_switch(db, client, monkeypatch):
    n = await _node(db)
    monkeypatch.setenv("TRAINERS_ENABLED", "false")
    off = await _syllabus(client, n)
    assert off == [("material", n["theory"], "required"), ("task", n["task"], "required")]
    monkeypatch.setenv("TRAINERS_ENABLED", "true")
    on = await _syllabus(client, n)
    # Место тренажёру даёт node_sequence: между теорией и заданием.
    assert on == [
        ("material", n["theory"], "required"),
        ("material", n["trainer"], "recommended"),
        ("task", n["task"], "required"),
    ]


async def _next(client, n, **after) -> dict:
    params = {"student_id": n["sid"], "root_course_id": n["cid"], **after}
    resp = await client.get("/api/v1/learning/next-item", params=params, headers=_bearer(n["token"]))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _complete(client, n, mid: int) -> None:
    resp = await client.post(
        f"/api/v1/learning/materials/{mid}/complete", json={"student_id": n["sid"]}, headers=_bearer(n["token"]),
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("switch", ["true", "false"])
async def test_trainer_does_not_block_next_item(db, client, monkeypatch, switch):
    """next-item: тренажёр не выдаётся следующим шагом и не держит путь."""
    monkeypatch.setenv("TRAINERS_ENABLED", switch)
    n = await _node(db)
    first = await _next(client, n)
    assert (first["type"], first["material_id"]) == ("material", n["theory"])
    after_theory = await _next(client, n, after_material_id=n["theory"])
    assert (after_theory["type"], after_theory["task_id"]) == ("task", n["task"])
    # «Да» на развилке — следующий элемент после тренажёра.
    after_trainer = await _next(client, n, after_material_id=n["trainer"])
    assert (after_trainer["type"], after_trainer["task_id"]) == ("task", n["task"])
    await _complete(client, n, n["theory"])
    resumed = await _next(client, n)
    assert (resumed["type"], resumed["task_id"]) == ("task", n["task"])
    assert resumed["trainer_suggestion"] is None


async def test_order_unchanged_when_switch_off(db, client, trainers_off):
    """При выключенном рубильнике выдача узла та же, что у узла без тренажёра."""
    with_t = await _node(db)
    without = await _node(db, with_trainer=False)

    async def shape(n):
        chain = [await _next(client, n), await _next(client, n, after_material_id=n["theory"])]
        return [(c["type"], c["material_id"] == n["theory"], c["task_id"] == n["task"]) for c in chain], [
            (k, lvl) for k, _id, lvl in await _syllabus(client, n)
        ]

    assert await shape(with_t) == await shape(without)


# ── 3. GET /trainers/{slug} ─────────────────────────────────────────────────


async def test_get_trainer_404_when_switch_off(db, client, trainers_off):
    cid = await _course(db)
    _, slug = await _trainer(db, cid)
    await db.commit()
    assert (await client.get(f"/api/v1/trainers/{slug}")).status_code == 404


async def test_get_trainer_public_with_etag(db, client, trainers_on):
    cid = await _course(db)
    _, slug = await _trainer(db, cid)
    await db.commit()
    resp = await client.get(f"/api/v1/trainers/{slug}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slug"] == slug
    assert body["title"] == "Ты — интерпретатор"
    assert body["scenario"]["schema"] == "trainer-scenario/1"
    assert resp.headers["etag"] == f'"{body["version"]}"'
    assert "max-age" in resp.headers["cache-control"]
    cached = await client.get(f"/api/v1/trainers/{slug}", headers={"If-None-Match": resp.headers["etag"]})
    assert cached.status_code == 304
    assert cached.headers["etag"] == resp.headers["etag"]


async def test_get_trainer_unknown_or_inactive_404(db, client, trainers_on):
    cid = await _course(db)
    _, slug = await _trainer(db, cid, active=False)
    await db.commit()
    assert (await client.get(f"/api/v1/trainers/{slug}")).status_code == 404
    assert (await client.get("/api/v1/trainers/no-such-trainer")).status_code == 404
    assert (await client.get("/api/v1/trainers/Bad_Slug")).status_code == 422


# ── 4. POST /trainers/{slug}/events ─────────────────────────────────────────


STEP = {"type": "trainer_step", "level": "L2", "step": "l1_predict", "ok": False,
        "misconception": "input_prompt", "ms": 4200, "answer": "Как тебя зовут?"}


async def _post(client, slug, body, **kw):
    return await client.post(f"/api/v1/trainers/{slug}/events", json=body, **kw)


async def test_events_404_when_switch_off(db, client, trainers_off):
    cid = await _course(db)
    _, slug = await _trainer(db, cid)
    await db.commit()
    resp = await _post(client, slug, {"context": "embed", "events": [{"type": "embed_view"}]})
    assert resp.status_code == 404


async def test_student_events_to_learning_events(db, client, trainers_on):
    n = await _node(db)
    body = {"context": "student", "events": [
        {"type": "trainer_start", "level": "L1"}, STEP,
        {"type": "trainer_step", "level": "L3", "step": "cond", "iter": 2, "ok": True, "ms": 900},
        {"type": "trainer_done", "stars": 7},
        {"type": "checkpoint_answer", "answer": "no"},
    ]}
    resp = await _post(client, n["slug"], body, headers=_bearer(n["token"]))
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"accepted": 5}
    rows = (await db.execute(
        text("SELECT event_type, payload FROM learning_events WHERE student_id = :s ORDER BY id"),
        {"s": n["sid"]},
    )).fetchall()
    assert [r[0] for r in rows] == ["trainer_start", "trainer_step", "trainer_step", "trainer_done", "checkpoint_answer"]
    step = rows[1][1]
    assert step == {"slug": n["slug"], "trainer_material_id": n["trainer"], "context": "student",
                    "level": "L2", "step": "l1_predict", "ok": False, "misconception": "input_prompt",
                    "ms": 4200, "answer": "Как тебя зовут?"}
    assert rows[2][1]["iter"] == 2
    assert "task_id" not in step


async def test_student_events_require_login(db, client, trainers_on):
    n = await _node(db)
    resp = await _post(client, n["slug"], {"context": "student", "events": [STEP]})
    assert resp.status_code == 401
    # Сервисный ключ — не ученик.
    resp = await _post(client, n["slug"], {"context": "student", "events": [STEP]}, headers=_headers())
    assert resp.status_code == 401


async def _guest_session(client) -> str:
    resp = await client.post("/api/v1/learning/guest/session", json={})
    assert resp.status_code == 201, resp.text
    return resp.json()["guest_session_id"]


async def test_guest_events_to_guest_attempt(db, client, trainers_on):
    cid = await _course(db)
    _, slug = await _trainer(db, cid)
    await db.commit()
    gs = await _guest_session(client)
    resp = await _post(client, slug, {"context": "guest", "events": [{"type": "trainer_start"}, STEP]},
                       cookies={"guest_session": gs})
    assert resp.status_code == 202, resp.text
    rows = (await db.execute(
        text("SELECT task_id, answer_json, is_correct, attributed_user_id FROM guest_attempt "
             "WHERE guest_session_id = :g ORDER BY id"),
        {"g": gs},
    )).fetchall()
    assert len(rows) == 2
    assert all(r[0] is None and r[3] is None for r in rows)
    assert rows[1][1]["kind"] == "trainer_event"
    assert rows[1][1]["event_type"] == "trainer_step"
    assert rows[1][1]["misconception"] == "input_prompt"
    assert rows[1][2] is False


async def test_guest_events_need_session(db, client, trainers_on):
    cid = await _course(db)
    _, slug = await _trainer(db, cid)
    await db.commit()
    body = {"context": "guest", "events": [STEP]}
    assert (await _post(client, slug, body)).status_code == 400
    assert (await _post(client, slug, body, cookies={"guest_session": "nope"})).status_code == 400
    assert (await _post(client, slug, body, cookies={"guest_session": str(uuid.uuid4())})).status_code == 400


async def test_embed_writes_only_anonymous_counters(db, client, trainers_on):
    cid = await _course(db)
    _, slug = await _trainer(db, cid)
    await db.commit()
    gs = await _guest_session(client)
    body = {"context": "embed", "events": [
        {"type": "embed_view", "parent_domain": "School-42.example.org"},
        {"type": "trainer_start", "parent_domain": "school-42.example.org"},
        {**STEP, "parent_domain": "school-42.example.org"},
        {"type": "trainer_done", "stars": 9, "answer": "секрет", "parent_domain": "school-42.example.org"},
    ]}
    # Cookie гостя у встройки не читается: ничего не должно лечь в guest_attempt.
    resp = await _post(client, slug, body, cookies={"guest_session": gs})
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"accepted": 3}
    rows = (await db.execute(
        text("SELECT user_id, event_type, properties FROM product_event "
             "WHERE properties->>'slug' = :s ORDER BY id"),
        {"s": slug},
    )).fetchall()
    assert [r[1] for r in rows] == ["embed_view", "trainer_start", "trainer_done"]
    for user_id, _type, props in rows:
        assert user_id is None
        assert props == {"slug": slug, "context": "embed", "parent_domain": "school-42.example.org"}
    assert (await db.execute(
        text("SELECT count(*) FROM guest_attempt WHERE guest_session_id = :g"), {"g": gs}
    )).scalar_one() == 0


async def test_unknown_misconception_rejected(db, client, trainers_on):
    n = await _node(db)
    resp = await _post(client, n["slug"], {"context": "student", "events": [{**STEP, "misconception": "loop_body"}]},
                       headers=_bearer(n["token"]))
    assert resp.status_code == 422, resp.text
    assert resp.json()["payload"]["misconceptions"] == ["loop_body"]
    count = (await db.execute(
        text("SELECT count(*) FROM learning_events WHERE student_id = :s"), {"s": n["sid"]}
    )).scalar_one()
    assert count == 0


@pytest.mark.parametrize(
    "body",
    [
        {"context": "embed", "events": [{"type": "embed_view"}] * 201},
        {"context": "embed", "events": []},
        {"context": "student", "events": [{**STEP, "answer": "x" * 65}]},
        {"context": "student", "events": [{"type": "trainer_start", "parent_domain": "a.ru"}]},
        {"context": "embed", "events": [{"type": "embed_view", "parent_domain": "https://a.ru/x"}]},
        {"context": "robot", "events": [{"type": "embed_view"}]},
        {"context": "embed", "events": [{"type": "hack"}]},
    ],
    ids=["batch_201", "empty", "answer_65", "domain_not_embed", "domain_url", "bad_context", "bad_type"],
)
async def test_events_validation(db, client, trainers_on, body):
    cid = await _course(db)
    _, slug = await _trainer(db, cid)
    await db.commit()
    assert (await _post(client, slug, body)).status_code == 422


async def test_batch_of_200_accepted(db, client, trainers_on):
    cid = await _course(db)
    _, slug = await _trainer(db, cid)
    await db.commit()
    resp = await _post(client, slug, {"context": "embed", "events": [{"type": "embed_view"}] * 200})
    assert resp.status_code == 202, resp.text


async def test_events_rate_limited(db, client, trainers_on, monkeypatch):
    cid = await _course(db)
    _, slug = await _trainer(db, cid)
    await db.commit()

    async def limited(*_a, **_kw):
        return True

    monkeypatch.setattr("app.api.v1.trainers.is_rate_limited", limited)
    resp = await _post(client, slug, {"context": "embed", "events": [{"type": "embed_view"}]})
    assert resp.status_code == 429


# ── 5. trainer_suggestion ───────────────────────────────────────────────────


async def _answer(client, n, option: str) -> dict:
    h = _bearer(n["token"])
    start = await client.post(
        f"/api/v1/learning/tasks/{n['task']}/start-or-get-attempt",
        json={"student_id": n["sid"], "root_course_id": n["cid"]}, headers=h,
    )
    assert start.status_code == 200, start.text
    resp = await client.post(
        f"/api/v1/attempts/{start.json()['attempt_id']}/answers",
        json={"items": [{"task_id": n["task"], "answer": {"type": "SC", "response": {"selected_option_ids": [option]}}}]},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_suggestion_after_wrong_answer(db, client, trainers_on):
    n = await _node(db)
    await _complete(client, n, n["theory"])
    wrong = await _answer(client, n, "a")
    title = "Ты — интерпретатор"
    assert wrong["results"][0]["trainer_suggestion"] == {"slug": n["slug"], "title": title}
    nxt = await _next(client, n)
    assert (nxt["type"], nxt["task_id"]) == ("task", n["task"])
    assert nxt["trainer_suggestion"] == {"slug": n["slug"], "title": title}
    right = await _answer(client, n, "b")
    assert right["results"][0]["trainer_suggestion"] is None


async def test_no_suggestion_when_switch_off(db, client, trainers_off):
    n = await _node(db)
    await _complete(client, n, n["theory"])
    wrong = await _answer(client, n, "a")
    assert wrong["results"][0]["trainer_suggestion"] is None
    assert (await _next(client, n))["trainer_suggestion"] is None


async def test_no_suggestion_without_trainer_in_node(db, client, trainers_on):
    n = await _node(db, with_trainer=False)
    await _complete(client, n, n["theory"])
    wrong = await _answer(client, n, "a")
    assert wrong["results"][0]["trainer_suggestion"] is None
    assert (await _next(client, n))["trainer_suggestion"] is None


# ── 6. находки ревью ────────────────────────────────────────────────────────


async def test_slug_taken_by_other_scenario_conflicts(db, client):
    """Второй тренажёр с тем же slug и другим сценарием подменил бы встройки — 409."""
    cid = await _course(db)
    _, slug = await _trainer(db, cid)
    await db.commit()
    other = _scenario("loop-machine")
    other["slug"] = slug
    resp = await client.post(
        "/api/v1/materials/",
        json={"course_id": cid, "title": "Другой", "type": "trainer", "content": {"slug": slug, "scenario": other}},
        headers=_headers(),
    )
    assert resp.status_code == 409, resp.text
    # Копия того же сценария в другом курсе разрешена.
    same = _scenario()
    same["slug"] = slug
    cid2 = await _course(db)
    await db.commit()
    resp = await client.post(
        "/api/v1/materials/",
        json={"course_id": cid2, "title": "Копия", "type": "trainer", "content": {"slug": slug, "scenario": same}},
        headers=_headers(),
    )
    assert resp.status_code in (200, 201), resp.text


async def test_put_type_to_trainer_requires_content(db, client):
    cid = await _course(db)
    mid = await _material(db, cid)
    await db.commit()
    resp = await client.put(f"/api/v1/materials/{mid}", json={"type": "trainer"}, headers=_headers())
    assert resp.status_code == 422, resp.text


async def test_switch_off_list_pagination_consistent(db, client, trainers_off):
    n = await _node(db)
    await _trainer(db, n["cid"], position=3)
    await db.commit()
    h = _bearer(n["token"])
    pages = [
        (await client.get(f"/api/v1/courses/{n['cid']}/materials", params={"skip": k, "limit": 1}, headers=h)).json()
        for k in range(2)
    ]
    assert [p["total"] for p in pages] == [1, 1]
    assert [i["id"] for p in pages for i in p["items"]] == [n["theory"]]


async def test_events_limited_per_writer(db, client, trainers_on, monkeypatch):
    n = await _node(db)
    seen = []

    async def limited(_redis, key, **_kw):
        seen.append(key)
        return key.startswith("trainer_events_user:")

    monkeypatch.setattr("app.api.v1.trainers.is_rate_limited", limited)
    resp = await _post(client, n["slug"], {"context": "student", "events": [STEP]}, headers=_bearer(n["token"]))
    assert resp.status_code == 429
    assert f"trainer_events_user:{n['sid']}" in seen
