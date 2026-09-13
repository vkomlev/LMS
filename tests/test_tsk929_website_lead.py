"""Публичная форма захвата заявки на лендингах сайта (tsk-929).

Форма встаёт перед переходом в Telegram (решение оператора 13.09), без cookie
гостевой сессии — вызывается JS-фетчем с чужого домена (WordPress).
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.requires_redis

_PAGE_PREFIX = "pytest:tsk929"


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _reset_and_cleanup(db):
    """Сбросить лимиты перед прогоном и убрать тестовые лиды после."""
    import os

    import redis.asyncio as aioredis

    redis = aioredis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/2"), decode_responses=True
    )
    try:
        async for key in redis.scan_iter(match="website_lead:*", count=200):
            await redis.delete(key)
        yield
    finally:
        await redis.aclose()
        await db.execute(
            text("DELETE FROM leads WHERE source_detail LIKE :p"),
            {"p": f"{_PAGE_PREFIX}%"},
        )
        await db.commit()


async def _post_lead(client, *, page: str, ip: str, **overrides):
    body = {
        "full_name": "Тест Тестович",
        "contact": "+7 900 000-00-00",
        "page": page,
        **overrides,
    }
    return await client.post(
        "/api/v1/public/leads",
        json=body,
        headers={"x-forwarded-for": ip},
    )


@pytest.mark.asyncio
async def test_website_lead_creates_lead_with_website_source(client, db):
    """Заявка пишется с каналом «website» и приписью — слагом лендинга."""
    resp = await _post_lead(client, page=f"{_PAGE_PREFIX}-happy", ip="203.0.113.1")
    assert resp.status_code == 201
    lead_id = resp.json()["lead_id"]
    assert lead_id > 0

    row = (
        await db.execute(
            text(
                "SELECT l.full_name, l.contact, l.source_detail, l.created_by, s.code "
                "FROM leads l JOIN lead_source s ON s.id = l.source_id WHERE l.id = :id"
            ),
            {"id": lead_id},
        )
    ).first()
    assert row is not None
    full_name, contact, source_detail, created_by, source_code = row
    assert full_name == "Тест Тестович"
    assert contact == "+7 900 000-00-00"
    assert source_detail == f"{_PAGE_PREFIX}-happy"
    assert source_code == "website"
    # Анонимная заявка — не заведена сотрудником.
    assert created_by is None


@pytest.mark.asyncio
async def test_website_lead_requires_all_fields(client):
    """Пустое имя/контакт/страница — 422, а не тихая заявка с дырой."""
    resp = await _post_lead(client, page="", ip="203.0.113.2")
    assert resp.status_code == 422

    resp = await _post_lead(client, page=f"{_PAGE_PREFIX}-empty-contact", ip="203.0.113.2", contact="  ")
    assert resp.status_code == 422

    resp = await _post_lead(client, page=f"{_PAGE_PREFIX}-empty-name", ip="203.0.113.2", full_name="   ")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_website_lead_honeypot_is_silently_dropped(client, db):
    """Заполненный honeypot — ответ как при успехе, но лида не заводим.

    Явная ошибка подсказала бы боту, какое поле снять с формы.
    """
    page = f"{_PAGE_PREFIX}-honeypot"
    resp = await _post_lead(client, page=page, ip="203.0.113.3", hp="я бот")
    assert resp.status_code == 201
    assert resp.json()["lead_id"] == 0

    count = (
        await db.execute(
            text("SELECT count(*) FROM leads WHERE source_detail = :p"), {"p": page}
        )
    ).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_website_lead_rate_limited_per_ip(client):
    """11-я заявка с одного адреса за час — 429, а не бесконечный приём."""
    ip = "203.0.113.9"
    for i in range(10):
        resp = await _post_lead(client, page=f"{_PAGE_PREFIX}-rl-{i}", ip=ip)
        assert resp.status_code == 201, f"заявка {i} должна пройти, получили {resp.status_code}"

    resp = await _post_lead(client, page=f"{_PAGE_PREFIX}-rl-11", ip=ip)
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_website_lead_different_ips_have_independent_limits(client):
    """Лимит считается по адресу — сосед с другого IP не упирается в чужой лимит."""
    for i in range(10):
        resp = await _post_lead(client, page=f"{_PAGE_PREFIX}-a-{i}", ip="203.0.113.10")
        assert resp.status_code == 201

    resp = await _post_lead(client, page=f"{_PAGE_PREFIX}-b-0", ip="203.0.113.11")
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_widget_renders_form_with_page_embedded(client):
    """Виджет для iframe отдаёт форму и подставляет слаг лендинга в JS.

    Пилот tsk-929 упёрся в то, что WordPress вырезает `<form>/<input>/<script>`
    из HTML-виджета Elementor при записи через REST API — даже у администратора.
    `<iframe>` эту фильтрацию переживает, поэтому форма переехала на LMS и
    встраивается через iframe, а не публикуется как контент WordPress.
    """
    resp = await client.get(
        "/api/v1/public/leads/widget", params={"page": f"{_PAGE_PREFIX}-widget"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "<form" in body
    assert "<script" in body
    assert f'"{_PAGE_PREFIX}-widget"' in body


@pytest.mark.asyncio
async def test_widget_escapes_page_against_script_injection(client):
    """`page` не может закрыть `<script>` раньше времени и внедрить свой код."""
    payload = "</script><script>alert(1)</script>"
    resp = await client.get("/api/v1/public/leads/widget", params={"page": payload})
    assert resp.status_code == 200
    assert "<script>alert(1)</script>" not in resp.text
    assert "<\\/script>" in resp.text


@pytest.mark.asyncio
async def test_widget_requires_page_param(client):
    """Без `page` виджет не знает, какой лендинг прислал заявку — 422."""
    resp = await client.get("/api/v1/public/leads/widget")
    assert resp.status_code == 422
