"""tsk-998 (П9): контрольная точка внутри материала.

Проверяется:
- формат `content.checkpoints` принимается и хранится (bulk-upsert его не срезает),
  кривой — отклоняется; content без точек не меняется;
- ученику материал отдаётся БЕЗ `answer`/`explanation` в обоих путях выдачи
  содержимого (`GET /materials/{id}`, `GET /courses/{id}/materials`), а при
  выключенном рубильнике — без `checkpoints` вообще; сервисный ключ видит всё;
- `POST /learning/materials/{id}/checkpoint/{cp_id}`: вердикт, пояснение, событие
  `material_checkpoint` с `first_attempt`, повтор разрешён, гейты (зачисление,
  выключенный материал, оплата), рубильник, длина ответа;
- ответ не трогает прохождение материала, попытки и `task_results`.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.schemas.material_content import validate_material_content
from app.services import material_checkpoints_service, payment_access_service
from app.utils.exceptions import DomainError
from test_help_ladder_endpoints_tsk303 import _bearer, _headers, _user

pytestmark = pytest.mark.asyncio

CP_SINGLE = {
    "id": "cp-index",
    "after_block": 1,
    "question": "<p>Какой индекс у первого элемента?</p>",
    "kind": "single",
    "options": [{"id": "a", "text": "1"}, {"id": "b", "text": "0"}],
    "answer": "b",
    "explanation": "Счёт индексов идёт с нуля.",
}
CP_SHORT = {
    "id": "cp-len",
    "after_block": 2,
    "question": "Что вернёт len([1, 2])?",
    "kind": "short",
    "answer": ["2", "два"],
}
HTML = (
    "<p>Индексы.</p>\n<div data-cb-checkpoint=\"cp-index\"></div>\n"
    "<p>Длина.</p>\n<div data-cb-checkpoint=\"cp-len\"></div>"
)


@pytest.fixture
def checkpoints_on(monkeypatch):
    """Включить рубильник контрольных точек на время теста."""
    monkeypatch.setattr(
        material_checkpoints_service._settings, "material_checkpoints_enabled", True
    )


async def _course(db) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES (:t, 'self_guided') RETURNING id"
            ),
            {"t": f"t998 курс {uuid.uuid4().hex[:8]}"},
        )
    ).scalar_one()


async def _material(db, course_id: int, *, content: dict | None = None, is_active: bool = True) -> int:
    body = content if content is not None else {
        "text": HTML, "format": "html", "checkpoints": [CP_SINGLE, CP_SHORT],
    }
    return (
        await db.execute(
            text(
                "INSERT INTO materials (title, type, content, course_id, is_active, external_uid) "
                "VALUES (:t, 'text', CAST(:c AS jsonb), :cid, :act, :e) RETURNING id"
            ),
            {
                "t": "t998 материал",
                "c": json.dumps(body, ensure_ascii=False),
                "cid": course_id,
                "act": is_active,
                "e": f"t998:{uuid.uuid4().hex}#m1",
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


async def _setup(db, *, enrolled: bool = True, is_active: bool = True, content: dict | None = None):
    sid, token = await _user(db, "t998 ученик", with_session=True)
    cid = await _course(db)
    mid = await _material(db, cid, content=content, is_active=is_active)
    if enrolled:
        await _enroll(db, sid, cid)
    await db.commit()
    return sid, token, cid, mid


async def _answer(client, token: str, mid: int, cp_id: str, answer: str):
    return await client.post(
        f"/api/v1/learning/materials/{mid}/checkpoint/{cp_id}",
        json={"answer": answer},
        headers=_bearer(token),
    )


async def _events(db, sid: int) -> list[dict]:
    rows = (
        await db.execute(
            text(
                "SELECT payload FROM learning_events "
                "WHERE student_id = :s AND event_type = 'material_checkpoint' ORDER BY id"
            ),
            {"s": sid},
        )
    ).fetchall()
    return [r[0] for r in rows]


# ── формат content ────────────────────────────────────────────────────────────


async def test_switch_is_off_by_default():
    """Выкат кода не меняет поведение, пока оператор не включит."""
    assert Settings().material_checkpoints_enabled is False


async def test_content_without_checkpoints_unchanged():
    """Материал без точек валидируется в прежний вид — ключа checkpoints нет."""
    assert validate_material_content("text", {"text": "<p>x</p>", "format": "html"}) == {
        "text": "<p>x</p>", "format": "html",
    }


async def test_content_with_checkpoints_kept():
    out = validate_material_content(
        "text", {"text": HTML, "format": "html", "checkpoints": [CP_SINGLE, CP_SHORT]}
    )
    assert out["checkpoints"][0] == CP_SINGLE
    assert out["checkpoints"][1] == CP_SHORT


@pytest.mark.parametrize(
    "bad",
    [
        {**CP_SINGLE, "answer": "z"},
        {**CP_SINGLE, "options": [{"id": "a", "text": "1"}]},
        {**CP_SHORT, "answer": []},
        {**CP_SHORT, "options": [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}]},
        {**CP_SHORT, "id": "с пробелом"},
        {**CP_SHORT, "kind": "multi"},
    ],
)
async def test_bad_checkpoint_rejected(bad):
    with pytest.raises(ValueError):
        validate_material_content("text", {"text": HTML, "format": "html", "checkpoints": [bad]})


async def test_duplicate_checkpoint_ids_rejected():
    with pytest.raises(ValueError):
        validate_material_content(
            "text", {"text": HTML, "format": "html", "checkpoints": [CP_SHORT, CP_SHORT]}
        )


async def test_bulk_upsert_keeps_checkpoints(db, client):
    """Публикатор CB пишет через bulk-upsert — точки не должны срезаться схемой."""
    cid = await _course(db)
    await db.commit()
    ext = f"t998:{uuid.uuid4().hex}#m1"
    resp = await client.post(
        "/api/v1/materials/bulk-upsert",
        json={"items": [{
            "course_id": cid, "external_uid": ext, "title": "t998 урок", "type": "text",
            "content": {"text": HTML, "format": "html", "checkpoints": [CP_SINGLE, CP_SHORT]},
        }]},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    stored = (
        await db.execute(text("SELECT content FROM materials WHERE external_uid = :e"), {"e": ext})
    ).scalar_one()
    assert stored["checkpoints"] == [CP_SINGLE, CP_SHORT]


# ── выдача ученику без ответа ────────────────────────────────────────────────


def _assert_student_view(content: dict) -> None:
    assert content["text"] == HTML
    cps = content["checkpoints"]
    assert [c["id"] for c in cps] == ["cp-index", "cp-len"]
    for cp in cps:
        assert "answer" not in cp
        assert "explanation" not in cp
    assert cps[0]["options"] == CP_SINGLE["options"]
    assert cps[0]["question"] == CP_SINGLE["question"]
    assert cps[1]["kind"] == "short"


async def test_student_get_material_hides_answers(db, client, checkpoints_on):
    _, token, _, mid = await _setup(db)
    resp = await client.get(f"/api/v1/materials/{mid}", headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    _assert_student_view(resp.json()["content"])
    assert "Счёт индексов" not in resp.text


async def test_student_course_list_hides_answers(db, client, checkpoints_on):
    _, token, cid, _ = await _setup(db)
    resp = await client.get(f"/api/v1/courses/{cid}/materials", headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    _assert_student_view(items[0]["content"])
    assert "Счёт индексов" not in resp.text


async def test_switch_off_student_gets_no_checkpoints(db, client):
    """Рубильник выключен: ученик получает материал как раньше — без checkpoints."""
    _, token, cid, mid = await _setup(db)
    one = await client.get(f"/api/v1/materials/{mid}", headers=_bearer(token))
    many = await client.get(f"/api/v1/courses/{cid}/materials", headers=_bearer(token))
    for content in (one.json()["content"], many.json()["items"][0]["content"]):
        assert content == {"text": HTML, "format": "html"}


async def test_service_key_sees_full_content(db, client):
    """Сервисный ключ (переиздание CB, боты, кабинет методиста) получает content целиком."""
    _, _, cid, mid = await _setup(db)
    one = await client.get(f"/api/v1/materials/{mid}", headers=_headers())
    many = await client.get(f"/api/v1/courses/{cid}/materials", headers=_headers())
    assert one.json()["content"]["checkpoints"] == [CP_SINGLE, CP_SHORT]
    assert many.json()["items"][0]["content"]["checkpoints"] == [CP_SINGLE, CP_SHORT]


async def test_student_view_does_not_mutate_stored_content(db, client, checkpoints_on):
    _, token, _, mid = await _setup(db)
    await client.get(f"/api/v1/materials/{mid}", headers=_bearer(token))
    stored = (await db.execute(text("SELECT content FROM materials WHERE id = :m"), {"m": mid})).scalar_one()
    assert stored["checkpoints"] == [CP_SINGLE, CP_SHORT]


async def test_material_without_checkpoints_unchanged_for_student(db, client, checkpoints_on):
    _, token, _, mid = await _setup(db, content={"text": "<p>Теория</p>", "format": "html"})
    resp = await client.get(f"/api/v1/materials/{mid}", headers=_bearer(token))
    assert resp.json()["content"] == {"text": "<p>Теория</p>", "format": "html"}


# ── ответ на точку ───────────────────────────────────────────────────────────


async def test_answer_rejected_when_switch_off(db, client):
    sid, token, _, mid = await _setup(db)
    resp = await _answer(client, token, mid, "cp-index", "b")
    assert resp.status_code == 409, resp.text
    assert resp.json()["payload"]["code"] == "checkpoints_disabled"
    assert await _events(db, sid) == []


async def test_single_answer_verdict_and_event(db, client, checkpoints_on):
    sid, token, _, mid = await _setup(db)
    wrong = await _answer(client, token, mid, "cp-index", "a")
    assert wrong.status_code == 200, wrong.text
    assert wrong.json() == {
        "material_id": mid, "checkpoint_id": "cp-index", "correct": False,
        "explanation": None, "first_attempt": True,
    }
    right = await _answer(client, token, mid, "cp-index", "b")
    assert right.json()["correct"] is True
    assert right.json()["explanation"] == "Счёт индексов идёт с нуля."
    assert right.json()["first_attempt"] is False

    events = await _events(db, sid)
    assert events == [
        {"material_id": mid, "checkpoint_id": "cp-index", "correct": False, "answer": "a", "first_attempt": True},
        {"material_id": mid, "checkpoint_id": "cp-index", "correct": True, "answer": "b", "first_attempt": False},
    ]


async def test_explanation_hidden_on_first_mistake_only(db, client, checkpoints_on):
    """tsk-1373: первая ошибка — без пояснения; вторая ошибка и верный ответ — с ним."""
    expl = CP_SINGLE["explanation"]
    _, token, _, mid = await _setup(db)
    first = (await _answer(client, token, mid, "cp-index", "a")).json()
    assert (first["correct"], first["explanation"]) == (False, None)
    second = (await _answer(client, token, mid, "cp-index", "a")).json()
    assert (second["correct"], second["explanation"]) == (False, expl)
    right = (await _answer(client, token, mid, "cp-index", "b")).json()
    assert (right["correct"], right["explanation"]) == (True, expl)
    # Верно с первой попытки — пояснение сразу.
    _, token2, _, mid2 = await _setup(db)
    ok = (await _answer(client, token2, mid2, "cp-index", "b")).json()
    assert (ok["correct"], ok["explanation"]) == (True, expl)


async def test_short_answer_normalized(db, client, checkpoints_on):
    sid, token, _, mid = await _setup(db)
    resp = await _answer(client, token, mid, "cp-len", "  Два. ")
    assert resp.status_code == 200, resp.text
    assert resp.json()["correct"] is True
    assert resp.json()["explanation"] is None
    assert resp.json()["first_attempt"] is True
    # Первая попытка считается по каждой точке отдельно.
    other = await _answer(client, token, mid, "cp-index", "b")
    assert other.json()["first_attempt"] is True
    assert (await _answer(client, token, mid, "cp-len", "3")).json()["correct"] is False


async def test_answer_too_long_rejected(db, client, checkpoints_on):
    sid, token, _, mid = await _setup(db)
    assert (await _answer(client, token, mid, "cp-len", "x" * 201)).status_code == 422
    assert (await _answer(client, token, mid, "cp-len", "")).status_code == 422
    assert (await _answer(client, token, mid, "cp-len", "   ")).status_code == 422
    assert (await _answer(client, token, mid, "cp-len", "x" * 200)).status_code == 200
    assert len((await _events(db, sid))[0]["answer"]) == 200


async def test_unknown_checkpoint_404(db, client, checkpoints_on):
    sid, token, _, mid = await _setup(db)
    resp = await _answer(client, token, mid, "cp-nope", "b")
    assert resp.status_code == 404
    assert resp.json()["payload"]["code"] == "checkpoint_not_found"
    assert await _events(db, sid) == []


async def test_not_enrolled_forbidden(db, client, checkpoints_on):
    sid, token, _, mid = await _setup(db, enrolled=False)
    assert (await _answer(client, token, mid, "cp-index", "b")).status_code == 403
    assert await _events(db, sid) == []


async def test_inactive_material_404(db, client, checkpoints_on):
    sid, token, _, mid = await _setup(db, is_active=False)
    assert (await _answer(client, token, mid, "cp-index", "b")).status_code == 404
    assert await _events(db, sid) == []


async def test_missing_material_404(db, client, checkpoints_on):
    _, token, _, _ = await _setup(db)
    assert (await _answer(client, token, 10**9, "cp-index", "b")).status_code == 404


async def test_payment_gate(db, client, checkpoints_on, monkeypatch):
    """Просроченная оплата закрывает ответ так же, как чтение материала."""
    sid, token, _, mid = await _setup(db)

    async def _blocked(_db, student_id):
        raise DomainError(detail="оплата", status_code=403, payload={"code": "payment_overdue"})

    monkeypatch.setattr(payment_access_service, "assert_content_allowed", _blocked)
    resp = await _answer(client, token, mid, "cp-index", "b")
    assert resp.status_code == 403
    assert resp.json()["payload"]["code"] == "payment_overdue"
    assert (await client.get(f"/api/v1/materials/{mid}", headers=_bearer(token))).status_code == 403
    assert await _events(db, sid) == []


async def test_service_key_cannot_answer(db, client, checkpoints_on):
    _, _, _, mid = await _setup(db)
    resp = await client.post(
        f"/api/v1/learning/materials/{mid}/checkpoint/cp-index",
        json={"answer": "b"}, headers=_headers(),
    )
    assert resp.status_code == 403


async def test_answer_does_not_touch_progress_or_results(db, client, checkpoints_on):
    """Ответ — диагностика: ни прохождения материала, ни попыток, ни task_results."""
    sid, token, _, mid = await _setup(db)
    assert (await _answer(client, token, mid, "cp-index", "b")).json()["correct"] is True
    assert (await _answer(client, token, mid, "cp-len", "5")).json()["correct"] is False

    async def _count(sql: str) -> int:
        return (await db.execute(text(sql), {"s": sid})).scalar_one()

    assert await _count("SELECT count(*) FROM student_material_progress WHERE student_id = :s") == 0
    assert await _count("SELECT count(*) FROM task_results WHERE user_id = :s") == 0
    assert await _count("SELECT count(*) FROM attempts WHERE user_id = :s") == 0
    assert await _count(
        "SELECT count(*) FROM learning_events WHERE student_id = :s AND event_type <> 'material_checkpoint'"
    ) == 0

    # Прохождение материала работает как раньше и от ответа не зависит.
    resp = await client.post(
        f"/api/v1/learning/materials/{mid}/complete",
        json={"student_id": sid}, headers=_bearer(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"


async def test_learning_gaps_does_not_read_learning_events():
    """Аналитика пробелов строится по task_results; событий точек она не читает."""
    from pathlib import Path

    from app.services import learning_gaps_service

    src = Path(learning_gaps_service.__file__).read_text(encoding="utf-8")
    assert "learning_events" not in src.replace("learning_events_service", "")
    assert "material_checkpoint" not in src


async def test_unseen_constructs_ignores_checkpoint_answers():
    """Скрытый ответ точки не считается «встречалось в теории» (tsk-965)."""
    from app.services.unseen_constructs_service import codes_in_material

    probe = {"text": "<p>Просто теория.</p>", "format": "html", "checkpoints": [
        {**CP_SHORT, "answer": ["'{}'.format(x)"], "explanation": "Это .format(x)"},
    ]}
    assert codes_in_material(json.dumps(probe), wanted={"format_method"}) == set()
    plain = {"text": "<p>Метод .format(x) подставляет значения.</p>", "format": "html"}
    assert codes_in_material(json.dumps(plain), wanted={"format_method"}) == {"format_method"}
