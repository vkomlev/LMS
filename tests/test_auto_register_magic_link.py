"""Auto-create user в magic-link/verify (Phase Y-1.5, ADR-0021)."""
import asyncio
import hashlib
import os
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from app.models.audit_event import AuditEvent
from app.models.identity_link import IdentityLink
from app.models.magic_link import MagicLink
from app.models.users import Users


async def _issue_magic_link(db, email: str, ttl_min: int = 15) -> str:
    """Создать magic_link напрямую и вернуть raw hex token."""
    raw = os.urandom(32)
    h = hashlib.sha256(raw).digest()
    link = MagicLink(
        email=email,
        token_hash=h,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_min),
    )
    db.add(link)
    await db.flush()
    await db.commit()
    return raw.hex()


@pytest.mark.asyncio
async def test_first_time_email_creates_user_and_identity(client: AsyncClient, db):
    """First-time email: verify создаёт user + identity_link + audit_event."""
    email = f"newcomer-{os.urandom(4).hex()}@example.com"
    token = await _issue_magic_link(db, email)

    resp = await client.post(
        "/api/v1/auth/magic-link/verify",
        json={"token": token},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "access_token" in data and "refresh_token" in data

    user = (await db.execute(select(Users).where(Users.email == email))).scalar_one_or_none()
    assert user is not None
    assert user.password_hash is None

    link = (await db.execute(
        select(IdentityLink).where(IdentityLink.kind == "email", IdentityLink.value == email)
    )).scalar_one_or_none()
    assert link is not None
    assert link.user_id == user.id

    audit = (await db.execute(
        select(AuditEvent).where(AuditEvent.event_type == "user.registered.via_magic_link",
                                 AuditEvent.user_id == user.id)
    )).scalar_one_or_none()
    assert audit is not None


@pytest.mark.asyncio
async def test_existing_email_reuses_user(client: AsyncClient, db):
    """Повторный verify по same email не создаёт нового user."""
    email = f"repeat-{os.urandom(4).hex()}@example.com"
    token1 = await _issue_magic_link(db, email)
    r1 = await client.post("/api/v1/auth/magic-link/verify", json={"token": token1})
    assert r1.status_code == 200
    user_id_1 = (await db.execute(
        select(Users.id).where(Users.email == email)
    )).scalar_one()

    token2 = await _issue_magic_link(db, email)
    r2 = await client.post("/api/v1/auth/magic-link/verify", json={"token": token2})
    assert r2.status_code == 200
    user_id_2 = (await db.execute(
        select(Users.id).where(Users.email == email)
    )).scalar_one()
    assert user_id_1 == user_id_2

    count = (await db.execute(
        select(text("COUNT(*)")).select_from(IdentityLink).where(
            IdentityLink.kind == "email", IdentityLink.value == email
        )
    )).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_email_normalized_to_lowercase(client: AsyncClient, db):
    """Email с UPPERCASE: user сохранён с lowercase + identity.value lowercase."""
    rand = os.urandom(4).hex()
    upper = f"Mixed.Case-{rand}@Example.COM"
    lower = upper.lower()
    token = await _issue_magic_link(db, lower)

    resp = await client.post(
        "/api/v1/auth/magic-link/verify",
        json={"token": token},
    )
    assert resp.status_code == 200

    user = (await db.execute(select(Users).where(Users.email == lower))).scalar_one_or_none()
    assert user is not None
    link = (await db.execute(
        select(IdentityLink).where(IdentityLink.kind == "email", IdentityLink.value == lower)
    )).scalar_one_or_none()
    assert link is not None


@pytest.mark.asyncio
@pytest.mark.no_tx_isolation  # два ОДНОВРЕМЕННЫХ verify обязаны идти разными
                              # соединениями — на одном проверять нечего (tsk-333)
async def test_concurrent_verify_same_email_creates_one_user(client: AsyncClient, db):
    """Race-safety: 2 concurrent verify для same email → exactly 1 user."""
    email = f"race-{os.urandom(4).hex()}@example.com"
    token_a = await _issue_magic_link(db, email)
    token_b = await _issue_magic_link(db, email)

    results = await asyncio.gather(
        client.post("/api/v1/auth/magic-link/verify", json={"token": token_a}),
        client.post("/api/v1/auth/magic-link/verify", json={"token": token_b}),
        return_exceptions=True,
    )
    statuses = [r.status_code for r in results if hasattr(r, "status_code")]
    assert 200 in statuses, results

    users = (await db.execute(select(Users).where(Users.email == email))).scalars().all()
    assert len(users) == 1


@pytest.mark.asyncio
async def test_orphan_email_returns_409(client: AsyncClient, db):
    """S2 regression (handoff 2026-04-28 §2): users.email exists без identity_link
    kind='email' → magic-link verify возвращает 409 identity_conflict, не 500.

    Сценарий: после manual DELETE FROM identity_link WHERE id=K (или race
    с orphan email-в-users) — auto-create раньше падал с UniqueViolationError
    в savepoint, не имея identity_link для recovery.
    """
    rand = os.urandom(4).hex()
    email = f"orphan-{rand}@example.com"

    # Подготовка orphan: users.email присутствует, identity_link отсутствует.
    orphan_user = Users(email=email, password_hash=None, full_name="Orphan")
    db.add(orphan_user)
    await db.flush()
    await db.commit()

    token = await _issue_magic_link(db, email)
    resp = await client.post(
        "/api/v1/auth/magic-link/verify",
        json={"token": token},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["detail"]["error"] == "identity_conflict"
    assert body["detail"]["conflict_kind"] == "email_already_linked_to_orphan_user"


@pytest.mark.asyncio
async def test_b1_regression_consumed_at_persists_after_savepoint_rollback(client: AsyncClient, db):
    """B1 regression: race на partial unique index не откатывает magic_link.consumed_at.

    Сценарий до фикса:
    1. Verify A → consume token_A + create user → commit OK
    2. Verify B → consume token_B (consumed_at=now() в tx) + INSERT users(email) →
       IntegrityError на partial unique → rollback ВСЕЙ транзакции →
       token_B.consumed_at снова NULL → token_B re-usable (security regression)

    После B1 fix (savepoint pattern): только INSERT users откатывается через
    nested-savepoint, magic_link.consumed_at остаётся в основной tx.
    """
    email = f"b1-{os.urandom(4).hex()}@example.com"
    token_a = await _issue_magic_link(db, email)
    token_b = await _issue_magic_link(db, email)

    r_a = await client.post("/api/v1/auth/magic-link/verify", json={"token": token_a})
    assert r_a.status_code == 200, r_a.text

    r_b = await client.post("/api/v1/auth/magic-link/verify", json={"token": token_b})
    assert r_b.status_code == 200, r_b.text

    # Critical invariant: token_b consumed_at должен быть persisted после второго verify
    raw_b = bytes.fromhex(token_b)
    h_b = hashlib.sha256(raw_b).digest()
    link_b = (await db.execute(
        select(MagicLink).where(MagicLink.token_hash == h_b)
    )).scalar_one()
    assert link_b.consumed_at is not None, (
        "B1 regression: magic_link.consumed_at для token_b откатился — "
        "token re-usable after race"
    )

    # Третий verify тем же token_b должен вернуть 401 (single-use enforced)
    r_replay = await client.post("/api/v1/auth/magic-link/verify", json={"token": token_b})
    assert r_replay.status_code == 401, (
        f"B1 regression: повторный verify token_b должен 401, получен {r_replay.status_code}"
    )
