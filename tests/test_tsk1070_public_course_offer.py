"""tsk-1070: публичная цена курса для движка лендингов сайта.

`GET /api/v1/public/courses/{course_id}/offer` — без авторизации, только чтение.
Проверяем на настоящей БД (по образцу test_tsk505_marketer_pricing.py):
- платный курс с двумя тарифами (частота 1/2) — порядок по sort_order;
- неактивный тариф и тарифы неактивной группы не отдаются;
- free / not_for_sale / unset (нет строки course_pricing);
- 404 для несуществующего курса;
- в ответе нет приватных полей (note, updated_by, id тарифов, is_active);
- заголовок Cache-Control.
"""
from __future__ import annotations

import random

import pytest
import pytest_asyncio
from sqlalchemy import text

_TAG = "tsk1070"
_ALLOWED_TOP_KEYS = {"course_id", "sale_status", "group_name", "tariffs"}
_ALLOWED_TARIFF_KEYS = {
    "name",
    "price_minor",
    "currency",
    "period",
    "match_kind",
    "match_value",
    "is_default",
    "sort_order",
}


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _cleanup(db):
    """Убрать тестовые курсы и группы после прогона (тарифы уходят каскадом)."""
    yield
    await db.execute(text("DELETE FROM courses WHERE title LIKE :p"), {"p": f"{_TAG}-%"})
    await db.execute(text("DELETE FROM pricing_group WHERE name LIKE :p"), {"p": f"{_TAG}-%"})
    await db.commit()


async def _new_course(db) -> int:
    course_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES (:t, 'self_guided') RETURNING id"
            ),
            {"t": f"{_TAG}-{random.randint(10**6, 10**7)}"},
        )
    ).scalar()
    await db.commit()
    return course_id


async def _new_group(db, *, is_active: bool = True) -> int:
    group_id = (
        await db.execute(
            text("INSERT INTO pricing_group (name, is_active) VALUES (:n, :a) RETURNING id"),
            {"n": f"{_TAG}-{random.randint(10**6, 10**7)}", "a": is_active},
        )
    ).scalar()
    await db.commit()
    return group_id


async def _add_tariff(
    db,
    *,
    group_id: int,
    name: str,
    price_minor: int,
    match_value: str | None,
    sort_order: int,
    is_active: bool = True,
    is_default: bool = False,
) -> None:
    await db.execute(
        text(
            "INSERT INTO pricing_tariff (group_id, name, price_minor, match_kind, match_value, "
            "sort_order, is_active, is_default) "
            "VALUES (:g, :n, :p, :k, :v, :s, :a, :d)"
        ),
        {
            "g": group_id,
            "n": name,
            "p": price_minor,
            "k": "attendance_frequency" if match_value else None,
            "v": match_value,
            "s": sort_order,
            "a": is_active,
            "d": is_default,
        },
    )
    await db.commit()


async def _set_status(db, *, course_id: int, sale_status: str, group_id: int | None = None) -> None:
    await db.execute(
        text(
            "INSERT INTO course_pricing (course_id, sale_status, group_id, note) "
            "VALUES (:c, :s, :g, 'секретная заметка маркетолога')"
        ),
        {"c": course_id, "s": sale_status, "g": group_id},
    )
    await db.commit()


def _url(course_id: int) -> str:
    return f"/api/v1/public/courses/{course_id}/offer"


@pytest.mark.asyncio
async def test_paid_course_returns_active_tariffs_sorted(db, client):
    """Платный курс: два активных тарифа по частоте, неактивный не отдаётся."""
    course_id = await _new_course(db)
    group_id = await _new_group(db)
    # Вставляем не по порядку — сортировка должна прийти из sort_order.
    await _add_tariff(db, group_id=group_id, name="2 раза в неделю", price_minor=800000,
                      match_value="2", sort_order=2)
    await _add_tariff(db, group_id=group_id, name="1 раз в неделю", price_minor=450000,
                      match_value="1", sort_order=1, is_default=True)
    await _add_tariff(db, group_id=group_id, name="старый тариф", price_minor=100,
                      match_value="3", sort_order=0, is_active=False)
    await _set_status(db, course_id=course_id, sale_status="paid", group_id=group_id)

    resp = await client.get(_url(course_id))
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=300"
    body = resp.json()
    assert body["course_id"] == course_id
    assert body["sale_status"] == "paid"
    assert body["group_name"].startswith(f"{_TAG}-")
    assert [t["name"] for t in body["tariffs"]] == ["1 раз в неделю", "2 раза в неделю"]
    first, second = body["tariffs"]
    assert first == {
        "name": "1 раз в неделю",
        "price_minor": 450000,
        "currency": "RUB",
        "period": "month",
        "match_kind": "attendance_frequency",
        "match_value": "1",
        "is_default": True,
        "sort_order": 1,
    }
    assert second["price_minor"] == 800000
    assert second["match_value"] == "2"


@pytest.mark.asyncio
async def test_no_private_fields_leak(db, client):
    """Ни заметки, ни автора правки, ни id и флагов активности в ответе."""
    course_id = await _new_course(db)
    group_id = await _new_group(db)
    await _add_tariff(db, group_id=group_id, name="единственный", price_minor=500000,
                      match_value=None, sort_order=0)
    await _set_status(db, course_id=course_id, sale_status="paid", group_id=group_id)

    resp = await client.get(_url(course_id))
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _ALLOWED_TOP_KEYS
    for tariff in body["tariffs"]:
        assert set(tariff) == _ALLOWED_TARIFF_KEYS
    assert "секретная" not in resp.text


@pytest.mark.asyncio
async def test_inactive_group_gives_no_tariffs(db, client):
    """Выключенная тарифная группа для витрины равна «тарифов нет»."""
    course_id = await _new_course(db)
    group_id = await _new_group(db, is_active=False)
    await _add_tariff(db, group_id=group_id, name="1 раз в неделю", price_minor=450000,
                      match_value="1", sort_order=0)
    await _set_status(db, course_id=course_id, sale_status="paid", group_id=group_id)

    body = (await client.get(_url(course_id))).json()
    assert body["sale_status"] == "paid"
    assert body["group_name"] is None
    assert body["tariffs"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sale_status", ["free", "not_for_sale"])
async def test_free_and_not_for_sale(db, client, sale_status):
    course_id = await _new_course(db)
    await _set_status(db, course_id=course_id, sale_status=sale_status)

    resp = await client.get(_url(course_id))
    assert resp.status_code == 200
    assert resp.json() == {
        "course_id": course_id,
        "sale_status": sale_status,
        "group_name": None,
        "tariffs": [],
    }


@pytest.mark.asyncio
async def test_unpriced_course_is_unset(db, client):
    """Нет строки course_pricing — явный `unset`, а не null и не «бесплатно»."""
    course_id = await _new_course(db)

    resp = await client.get(_url(course_id))
    assert resp.status_code == 200
    assert resp.json() == {
        "course_id": course_id,
        "sale_status": "unset",
        "group_name": None,
        "tariffs": [],
    }


@pytest.mark.asyncio
async def test_missing_course_is_404(db, client):
    max_id = (await db.execute(text("SELECT COALESCE(max(id), 0) FROM courses"))).scalar()
    resp = await client.get(_url(max_id + 100000))
    assert resp.status_code == 404
