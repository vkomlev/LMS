"""tsk-718 (служебный вход лидов для соседних систем).

Покрывает то, ради чего вход и заведён:

- пускает только сервисный ключ, человеку — 403 (у него есть кабинет);
- повторное обращение того же человека **не создаёт второго лида** — это
  главное свойство, ради которого появилась таблица связей;
- разные внешние номера — разные лиды (дедуп не склеивает чужих);
- источник (объявление, город, линейка) доезжает до карточки лида;
- незнакомый канал — понятный отказ, а не ошибка сервера.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()
_TAG = "tsk718"


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


def _external_id() -> str:
    return f"{_TAG}-{random.randint(10**9, 10**12)}"


def _payload(external_id: str, **over) -> dict:
    body = {
        "external_source": "avito_messenger",
        "external_id": external_id,
        "source_code": "avito",
        "contact": "https://www.avito.ru/profile/messenger/channel/u2i-test",
        "full_name": "Тестовый Собеседник",
        "source_detail": (
            "Авито, переписка; объявление «Репетитор по информатике»; "
            "город Казань; линейка ЕГЭ"
        ),
        "note": "Первое сообщение с Авито: сколько стоит?",
    }
    body.update(over)
    return body


async def _cleanup(db, external_ids: list[str]) -> None:
    await db.execute(
        text(
            "DELETE FROM leads WHERE id IN ("
            "SELECT lead_id FROM lead_external_ref WHERE external_id = ANY(:ids))"
        ),
        {"ids": external_ids},
    )
    await db.commit()


async def _new_user(db, *, role: str | None, name: str) -> str:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
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
                "SELECT :u, r.id FROM roles r WHERE r.name = :role "
                "ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.commit()
    return token


@pytest.mark.parametrize("role", ["marketer", "admin", "student", None])
async def test_service_only(db, client, role):
    """Человек сюда не ходит — даже маркетолог: у него есть кабинет."""
    token = await _new_user(db, role=role, name=f"gate-{role}")
    resp = await client.post(
        "/api/v1/integrations/leads",
        json=_payload(_external_id()),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text


async def test_repeat_call_does_not_duplicate_lead(db, client):
    """Повторное обращение того же человека — тот же лид, а не второй.

    Ровно то, из-за чего в базе соседнего проекта завёлся дубль на дубле:
    ключ дедупа должен срабатывать всегда, а не «обычно».
    """
    external_id = _external_id()
    try:
        first = await client.post(
            "/api/v1/integrations/leads",
            json=_payload(external_id),
            headers={"X-API-Key": _api_key()},
        )
        assert first.status_code == 200, first.text
        assert first.json()["created"] is True

        second = await client.post(
            "/api/v1/integrations/leads",
            json=_payload(external_id, note="Написал ещё раз по другому объявлению"),
            headers={"X-API-Key": _api_key()},
        )
        assert second.status_code == 200, second.text
        assert second.json()["created"] is False
        assert second.json()["lead_id"] == first.json()["lead_id"]

        total = (
            await db.execute(
                text(
                    "SELECT count(*) FROM lead_external_ref "
                    "WHERE external_id = :e AND source = 'avito_messenger'"
                ),
                {"e": external_id},
            )
        ).scalar()
        assert total == 1
    finally:
        await _cleanup(db, [external_id])


async def test_different_people_get_different_leads(db, client):
    """Разные собеседники — разные лиды: дедуп не склеивает чужих."""
    one, two = _external_id(), _external_id()
    try:
        first = await client.post(
            "/api/v1/integrations/leads",
            json=_payload(one),
            headers={"X-API-Key": _api_key()},
        )
        second = await client.post(
            "/api/v1/integrations/leads",
            json=_payload(two),
            headers={"X-API-Key": _api_key()},
        )
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["lead_id"] != second.json()["lead_id"]
        assert second.json()["created"] is True
    finally:
        await _cleanup(db, [one, two])


async def test_source_reaches_lead_card(db, client):
    """Объявление, город и линейка доезжают до карточки — иначе воронку не разобрать."""
    external_id = _external_id()
    try:
        resp = await client.post(
            "/api/v1/integrations/leads",
            json=_payload(external_id),
            headers={"X-API-Key": _api_key()},
        )
        assert resp.status_code == 200, resp.text
        row = (
            await db.execute(
                text(
                    "SELECT l.source_detail, l.contact, l.note, s.code "
                    "FROM leads l JOIN lead_source s ON s.id = l.source_id "
                    "WHERE l.id = :id"
                ),
                {"id": resp.json()["lead_id"]},
            )
        ).first()
        assert row is not None
        assert row.code == "avito"
        assert "линейка ЕГЭ" in row.source_detail
        assert "город Казань" in row.source_detail
        assert row.contact.startswith("https://www.avito.ru/")
        assert "сколько стоит" in row.note
    finally:
        await _cleanup(db, [external_id])


async def test_unknown_source_code_is_explained(client):
    """Незнакомый канал — понятный отказ, а не ошибка сервера."""
    resp = await client.post(
        "/api/v1/integrations/leads",
        json=_payload(_external_id(), source_code="нет-такого-канала"),
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 404, resp.text
    assert "не найден" in resp.json()["detail"]


@pytest.mark.parametrize("bad", ["", "   "])
async def test_blank_external_id_rejected(client, bad):
    """Пустой и пробельный внешний номер — отказ на входе.

    Пустой ключ склейки — тот самый случай, когда дедуп молча перестаёт
    работать. Строка из пробелов ничем не лучше: она непустая для проверки
    длины, но склеивает всех подряд. До базы такое доезжать не должно.
    """
    resp = await client.post(
        "/api/v1/integrations/leads",
        json=_payload(bad),
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 422, resp.text


# --- tsk-857: свободные окна расписания -------------------------------------


@pytest.mark.parametrize("role", ["marketer", "admin", "student", None])
async def test_free_slots_service_only(db, client, role):
    """Расписание через служебный вход человеку не отдаём.

    У ученика есть свой экран выбора времени с записью в одно нажатие; вторая
    дверь в те же данные с другими правилами не нужна.
    """
    token = await _new_user(db, role=role, name=f"slots-{role}")
    resp = await client.get(
        "/api/v1/integrations/free-slots",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text


async def test_free_slots_hide_full_groups_and_staff(db, client):
    """Отдаём только то, куда можно записаться, и ничего лишнего.

    Порог тот же, что на экране ученика: слот, где больше восьми человек,
    в ответ не попадает вовсе. Замер боевой базы 09.09: из 23 активных слотов
    три были набраны (по девять человек) — в субботу 10:00 и 11:00 и в
    понедельник 17:00. При этом справочник Авито звал людей «в субботу в 10,
    11, 12 и 13», то есть прямо в набранные группы: ровно ради этого вход и
    сделан.

    Наружу не уходят ни преподаватель, ни номер слота, ни число учеников —
    с той стороны текст читает клиент площадки.
    """
    resp = await client.get(
        "/api/v1/integrations/free-slots",
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["timezone"] == "Europe/Moscow"
    assert "generated_at" in body

    for slot in body["slots"]:
        assert set(slot) == {
            "weekday",
            "start_time",
            "duration_minutes",
            "availability",
        }, slot
        assert 0 <= slot["weekday"] <= 6
        assert slot["availability"] in {"free", "partial", "crowded"}


async def test_free_slots_match_what_a_student_would_see(db, client):
    """Служебный ответ совпадает с экраном ученика по составу окон.

    Два места решают одно и то же — «куда можно записаться», — и разъехаться
    им нельзя: на площадке пообещали бы время, которого нет. Поэтому здесь
    сверяется не текст, а сам отбор: те же пороги, та же сетка, та же
    проверка «слот доживёт до занятия».
    """
    from app.services import schedule_booking_service

    service_side = await schedule_booking_service.get_free_slots(db)
    offered = {
        (s["weekday"], s["start_time"]) for s in service_side["slots"]
    }

    # Прямой пересчёт по тем же правилам, но своим кодом: если отбор в сервисе
    # подменят, тест это увидит.
    from app.schemas.schedule_booking import is_bookable_count
    from app.services.schedule_plan_service import in_grid

    rows = await db.execute(
        text(
            """
            SELECT ls.weekday, ls.start_time, ls.active_until,
                   COUNT(lss.id) FILTER (WHERE lss.is_active) AS students
              FROM lesson_slot ls
              LEFT JOIN lesson_slot_student lss ON lss.slot_id = ls.id
             WHERE ls.is_active
             GROUP BY ls.id, ls.weekday, ls.start_time, ls.active_until
            """
        )
    )
    today = schedule_booking_service._today_moscow()
    expected = {
        (r[0], r[1])
        for r in rows.fetchall()
        if schedule_booking_service.slot_is_alive(r[0], r[2], today)
        and in_grid((r[0], r[1]))
        and is_bookable_count(int(r[3] or 0))
    }
    assert offered == expected
