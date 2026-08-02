"""tsk-506 (мини-CRM лидов в кабинете маркетолога).

Покрывает:
- гейт `marketer|admin` на все пути лидов;
- канал «Другое» без приписки отклоняется (иначе источник теряется);
- фильтр «привязан / не привязан» и переход лида между ними;
- идемпотентность повторной привязки, снятие привязки;
- узкий поиск учеников отдаёт только номер и имя и не пускает не-учеников.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_TAG = "tsk506"


async def _new_user(db, *, role: str | None, name: str) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}-{random.randint(10**6, 10**7)}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.commit()
    return u.id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _source_id(db, code: str) -> int:
    return (
        await db.execute(text("SELECT id FROM lead_source WHERE code = :c"), {"c": code})
    ).scalar()


async def _create_lead(client, token: str, **payload) -> dict:
    resp = await client.post("/api/v1/marketer/leads", json=payload, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.parametrize("role", ["teacher", "student", "methodist", None])
async def test_leads_gate_rejects_other_roles(db, client, role):
    _, token = await _new_user(db, role=role, name=f"gate-{role}")
    resp = await client.post(
        "/api/v1/marketer/leads",
        json={"source_id": 1, "contact": "@somebody"},
        headers=_auth(token),
    )
    assert resp.status_code == 403


async def test_lead_sources_seeded(db, client):
    _, token = await _new_user(db, role="marketer", name="src")
    resp = await client.get("/api/v1/marketer/lead-sources", headers=_auth(token))
    assert resp.status_code == 200
    codes = {s["code"] for s in resp.json()}
    assert {"avito", "telegram", "vk", "other"} <= codes


async def test_other_channel_requires_detail(db, client):
    """Канал «Другое» без приписки — это потерянный источник, а не лид без деталей."""
    _, token = await _new_user(db, role="marketer", name="other")
    other_id = await _source_id(db, "other")

    rejected = await client.post(
        "/api/v1/marketer/leads",
        json={"source_id": other_id, "contact": "@ghost", "source_detail": "   "},
        headers=_auth(token),
    )
    assert rejected.status_code == 422

    accepted = await client.post(
        "/api/v1/marketer/leads",
        json={"source_id": other_id, "contact": "@ghost", "source_detail": "форум школьников"},
        headers=_auth(token),
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["source_detail"] == "форум школьников"


async def test_lead_lifecycle_and_linked_filter(db, client):
    _, token = await _new_user(db, role="marketer", name="life")
    student_id, _ = await _new_user(db, role="student", name="target")
    avito_id = await _source_id(db, "avito")

    lead = await _create_lead(
        client, token, source_id=avito_id, contact="+7 999 000-00-00", full_name="Мама Пети"
    )
    lead_id = lead["id"]
    assert lead["linked_student_id"] is None
    assert lead["source_code"] == "avito"

    unlinked = await client.get("/api/v1/marketer/leads?linked=false", headers=_auth(token))
    assert lead_id in {item["id"] for item in unlinked.json()}

    linked = await client.post(
        f"/api/v1/marketer/leads/{lead_id}/link",
        json={"student_id": student_id},
        headers=_auth(token),
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["linked_student_id"] == student_id
    assert linked.json()["linked_student_name"] is not None

    # Повторная привязка того же ученика — не ошибка.
    again = await client.post(
        f"/api/v1/marketer/leads/{lead_id}/link",
        json={"student_id": student_id},
        headers=_auth(token),
    )
    assert again.status_code == 200

    still_unlinked = await client.get(
        "/api/v1/marketer/leads?linked=false", headers=_auth(token)
    )
    assert lead_id not in {item["id"] for item in still_unlinked.json()}

    now_linked = await client.get("/api/v1/marketer/leads?linked=true", headers=_auth(token))
    assert lead_id in {item["id"] for item in now_linked.json()}

    unlink = await client.delete(
        f"/api/v1/marketer/leads/{lead_id}/link", headers=_auth(token)
    )
    assert unlink.status_code == 200
    assert unlink.json()["linked_student_id"] is None

    removed = await client.delete(f"/api/v1/marketer/leads/{lead_id}", headers=_auth(token))
    assert removed.status_code == 204
    assert (
        await client.get(f"/api/v1/marketer/leads/{lead_id}", headers=_auth(token))
    ).status_code == 404


async def test_link_to_missing_student_is_404(db, client):
    _, token = await _new_user(db, role="marketer", name="ghost-link")
    avito_id = await _source_id(db, "avito")
    lead = await _create_lead(client, token, source_id=avito_id, contact="@nobody")

    resp = await client.post(
        f"/api/v1/marketer/leads/{lead['id']}/link",
        json={"student_id": 99_000_000},
        headers=_auth(token),
    )
    assert resp.status_code == 404


@pytest.mark.parametrize("role", ["teacher", "methodist", "admin", None])
async def test_link_only_accepts_students(db, client, role):
    """Привязка не должна становиться обходным чтением ФИО кого угодно.

    Раньше `link` принимал любой `users.id`, проходящий по внешнему ключу, а
    карточка лида показывала `linked_student_name` — перебором номеров маркетолог
    прочитал бы ФИО преподавателей, родителей и администраторов. Ровно тот
    доступ, ради закрытия которого ему не открывали общий поиск людей.
    """
    _, token = await _new_user(db, role="marketer", name=f"link-{role}")
    victim_id, _ = await _new_user(db, role=role, name=f"victim-{role}")
    avito_id = await _source_id(db, "avito")
    lead = await _create_lead(client, token, source_id=avito_id, contact="@probe")

    resp = await client.post(
        f"/api/v1/marketer/leads/{lead['id']}/link",
        json={"student_id": victim_id},
        headers=_auth(token),
    )
    assert resp.status_code == 404, f"роль {role} не должна привязываться как ученик"

    card = await client.get(f"/api/v1/marketer/leads/{lead['id']}", headers=_auth(token))
    assert card.json()["linked_student_id"] is None
    assert card.json()["linked_student_name"] is None


async def test_note_can_be_cleared(db, client):
    """Стереть примечание должно быть можно.

    Раньше `None` отбрасывался при сборке UPDATE: сервер отвечал 200 со старым
    текстом, и поле в интерфейсе само возвращало прежнее значение — со стороны
    это «кнопка не работает».
    """
    _, token = await _new_user(db, role="marketer", name="clear-note")
    avito_id = await _source_id(db, "avito")
    lead = await _create_lead(
        client, token, source_id=avito_id, contact="@erase", note="созвон в четверг"
    )

    resp = await client.patch(
        f"/api/v1/marketer/leads/{lead['id']}",
        json={"note": None},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["note"] is None

    card = await client.get(f"/api/v1/marketer/leads/{lead['id']}", headers=_auth(token))
    assert card.json()["note"] is None


async def test_detail_requirement_holds_on_detail_only_patch(db, client):
    """Приписку у канала «Другое» нельзя стереть правкой одной приписки.

    Проверка запускалась только при смене канала — правка одного `source_detail`
    её обходила, и лид оставался на канале «Другое» с пустым источником.
    """
    _, token = await _new_user(db, role="marketer", name="detail-patch")
    other_id = await _source_id(db, "other")
    lead = await _create_lead(
        client,
        token,
        source_id=other_id,
        contact="@forum",
        source_detail="форум школьников",
    )

    for bad in (None, "   "):
        resp = await client.patch(
            f"/api/v1/marketer/leads/{lead['id']}",
            json={"source_detail": bad},
            headers=_auth(token),
        )
        assert resp.status_code == 422, f"приписка {bad!r} не должна проходить"

    card = await client.get(f"/api/v1/marketer/leads/{lead['id']}", headers=_auth(token))
    assert card.json()["source_detail"] == "форум школьников"


async def test_patch_to_other_channel_keeps_detail_requirement(db, client):
    """Смена канала на «Другое» не должна пройти с пустым источником."""
    _, token = await _new_user(db, role="marketer", name="patch")
    avito_id = await _source_id(db, "avito")
    other_id = await _source_id(db, "other")
    lead = await _create_lead(client, token, source_id=avito_id, contact="@switch")

    resp = await client.patch(
        f"/api/v1/marketer/leads/{lead['id']}",
        json={"source_id": other_id},
        headers=_auth(token),
    )
    assert resp.status_code == 422


async def test_student_search_returns_only_students_and_narrow_fields(db, client):
    _, token = await _new_user(db, role="marketer", name="search")
    student_id, _ = await _new_user(db, role="student", name="findme")
    teacher_id, _ = await _new_user(db, role="teacher", name="findme")

    resp = await client.get(
        f"/api/v1/marketer/students/search?q={_TAG}-findme", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    found = {item["id"] for item in resp.json()}
    assert student_id in found
    assert teacher_id not in found, "поиск для привязки лида отдаёт только учеников"
    for item in resp.json():
        assert set(item.keys()) == {"id", "full_name"}


async def test_lead_all_fields_editable(db, client):
    """Полная правка лида (tsk-518): ошиблись каналом — исправляем на месте.

    Сценарий оператора: лид заведён с неверным каналом привлечения. Раньше через
    интерфейс правились только примечание и привязка, и такую ошибку было нечем
    исправить.
    """
    _, token = await _new_user(db, role="marketer", name="full-edit")
    avito_id = await _source_id(db, "avito")
    tg_id = await _source_id(db, "telegram")
    lead = await _create_lead(
        client,
        token,
        source_id=avito_id,
        contact="@wrong",
        full_name="Было имя",
        note="было примечание",
    )

    resp = await client.patch(
        f"/api/v1/marketer/leads/{lead['id']}",
        json={
            "source_id": tg_id,
            "full_name": "Стало имя",
            "contact": "@right",
            "note": "стало примечание",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_id"] == tg_id
    assert body["full_name"] == "Стало имя"
    assert body["contact"] == "@right"
    assert body["note"] == "стало примечание"

    card = await client.get(f"/api/v1/marketer/leads/{lead['id']}", headers=_auth(token))
    assert card.json()["source_id"] == tg_id, "канал должен смениться насовсем"


async def test_lead_contact_cannot_be_emptied(db, client):
    """Связь — единственное, чем лид опознаётся: стереть её нельзя."""
    _, token = await _new_user(db, role="marketer", name="empty-contact")
    avito_id = await _source_id(db, "avito")
    lead = await _create_lead(client, token, source_id=avito_id, contact="@keeps")

    for bad in (None, "   "):
        resp = await client.patch(
            f"/api/v1/marketer/leads/{lead['id']}",
            json={"contact": bad},
            headers=_auth(token),
        )
        assert resp.status_code == 422, f"пустая связь {bad!r} не должна проходить"

    card = await client.get(f"/api/v1/marketer/leads/{lead['id']}", headers=_auth(token))
    assert card.json()["contact"] == "@keeps"
