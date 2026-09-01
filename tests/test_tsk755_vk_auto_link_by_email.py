"""tsk-755: вход через ВК находит аккаунт с той же почтой и привязывается к нему.

Запрет из ADR-0021 §2 («auto-merge запрещён») держался ровно на одном
предположении: почту от ВК никто не заверяет. Оператор установил обратное —
ВКонтакте подтверждает почту, прежде чем отдать её приложению, — и запрет в этой
части снят (ADR-0054). Ученик больше не упирается в «сначала войдите по почте,
потом привяжите ВК».

Границы решения, каждая проверяется тестом ниже:
  * почты от ВК нет → поведение прежнее, доказательства владения нет;
  * ВК свободен + почта совпала → привязка, автоматически;
  * ВК уже на аккаунте А, почта на аккаунте Б → это два живых аккаунта, слияние
    автоматом не делается: вход идёт в А, пара отмечается для оператора;
  * каждая привязка попадает в журнал.
"""
import os
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models.audit_event import AuditEvent
from app.models.identity_link import IdentityLink
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.vk_oauth_service import get_or_create_user_by_vk


def _new_vk_id() -> str:
    return str(random.SystemRandom().randint(10**8, 10**10))


def _email(prefix: str) -> str:
    return f"{prefix}-{os.urandom(4).hex()}@example.com"


async def _make_user(db, *, email: str | None = None, full_name: str = "Ученик") -> Users:
    user = Users(email=email, password_hash=None, full_name=full_name, tg_id=None)
    db.add(user)
    await db.flush()
    return user


async def _login_via_vk(db, vk_id: str, email: str | None, **kw):
    return await get_or_create_user_by_vk(
        db, vk_user_id=vk_id, email=email,
        full_name=kw.pop("full_name", "Имя Из ВК"),
        access_token=kw.pop("access_token", "acc"),
        refresh_token=None,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=3600),
        settings=Settings(), ip="127.0.0.1", user_agent="test", **kw,
    )


async def _events(db, user_id: int, event_type: str) -> list[AuditEvent]:
    return list((await db.execute(
        select(AuditEvent).where(
            AuditEvent.user_id == user_id, AuditEvent.event_type == event_type
        )
    )).scalars().all())


@pytest.mark.asyncio
async def test_vk_links_to_account_with_same_email(db):
    """Главный случай: ученик входит через ВК и попадает в свой аккаунт."""
    email = _email("student")
    owner = await _make_user(db, email=email, full_name="Редько Артём")
    await identity_link_service.link_existing_user(db, owner.id, "email", email)
    await db.commit()
    users_before = len((await db.execute(select(Users.id))).scalars().all())

    user, created = await _login_via_vk(db, _new_vk_id(), email)
    await db.commit()

    assert created is False
    assert user.id == owner.id
    users_after = len((await db.execute(select(Users.id))).scalars().all())
    assert users_after == users_before, "второй аккаунт заводиться не должен"


@pytest.mark.asyncio
async def test_auto_link_is_written_to_journal(db):
    """Каждая автопривязка записана: кто, к какому аккаунту, по какой почте."""
    email = _email("journal")
    owner = await _make_user(db, email=email)
    await identity_link_service.link_existing_user(db, owner.id, "email", email)
    await db.commit()
    vk_id = _new_vk_id()

    await _login_via_vk(db, vk_id, email)
    await db.commit()

    events = await _events(db, owner.id, "auth.vk.auto_linked_by_email")
    assert len(events) == 1
    details = events[0].details
    assert details["vk_user_id"] == vk_id
    assert details["match_source"] == "identity_link"
    # почта в журнале замаскирована — целиком её там быть не должно
    assert details["email_masked"].endswith("@example.com")
    assert email not in details["email_masked"]


@pytest.mark.asyncio
async def test_no_email_from_vk_keeps_old_behaviour(db):
    """Согласия на почту в ВК не дали — доказательства нет, заводится новый аккаунт.

    Ровно этот случай (`email=None`) и был живым инцидентом tsk-629: без почты
    привязать не к чему, кроме живого сеанса.
    """
    email = _email("silent")
    stranger = await _make_user(db, email=email)
    await identity_link_service.link_existing_user(db, stranger.id, "email", email)
    await db.commit()

    user, created = await _login_via_vk(db, _new_vk_id(), None)
    await db.commit()

    assert created is True
    assert user.id != stranger.id


@pytest.mark.asyncio
async def test_two_live_accounts_are_not_merged(db):
    """ВК на аккаунте А, почта на Б — вход в А, слияния автоматом нет."""
    email = _email("both")
    account_b = await _make_user(db, email=email, full_name="Аккаунт Б")
    await identity_link_service.link_existing_user(db, account_b.id, "email", email)
    account_a = await _make_user(db, full_name="Аккаунт А")
    vk_id = _new_vk_id()
    await identity_link_service.link_existing_user(db, account_a.id, "vk", vk_id)
    await db.commit()

    user, created = await _login_via_vk(db, vk_id, email)
    await db.commit()

    assert created is False
    assert user.id == account_a.id, "вход ведёт туда, куда ведёт ВК"
    # почта осталась за Б: перевешивать identity живого аккаунта нельзя
    email_owner = await identity_link_service.get_user_by_identity(db, "email", email)
    assert email_owner is not None and email_owner.id == account_b.id


@pytest.mark.asyncio
async def test_two_live_accounts_are_noted_for_operator(db):
    """Пара «два аккаунта одного человека» отмечена — иначе её видно только жалобой."""
    email = _email("candidate")
    account_b = await _make_user(db, email=email)
    await identity_link_service.link_existing_user(db, account_b.id, "email", email)
    account_a = await _make_user(db)
    vk_id = _new_vk_id()
    await identity_link_service.link_existing_user(db, account_a.id, "vk", vk_id)
    await db.commit()

    await _login_via_vk(db, vk_id, email)
    await db.commit()

    events = await _events(db, account_a.id, "auth.vk.merge_candidate")
    assert len(events) == 1
    assert events[0].details["email_account_id"] == account_b.id
    assert events[0].details["vk_account_id"] == account_a.id


@pytest.mark.asyncio
async def test_own_email_on_own_account_is_not_a_merge_candidate(db):
    """Свою же почту за кандидата на слияние не считаем (иначе журнал зашумится)."""
    email = _email("self")
    owner = await _make_user(db, email=email)
    await identity_link_service.link_existing_user(db, owner.id, "email", email)
    vk_id = _new_vk_id()
    await identity_link_service.link_existing_user(db, owner.id, "vk", vk_id)
    await db.commit()

    await _login_via_vk(db, vk_id, email)
    await db.commit()

    assert await _events(db, owner.id, "auth.vk.merge_candidate") == []


@pytest.mark.asyncio
async def test_unknown_email_still_creates_account(db):
    """Почта, которой ни у кого нет, — обычная регистрация, как и раньше."""
    email = _email("brand-new")

    user, created = await _login_via_vk(db, _new_vk_id(), email)
    await db.commit()

    assert created is True
    assert user.email == email
    kinds = {row[0] for row in (await db.execute(
        select(IdentityLink.kind).where(IdentityLink.user_id == user.id)
    )).all()}
    assert kinds == {"vk", "email"}


@pytest.mark.asyncio
async def test_repeat_login_is_idempotent(db):
    """Второй вход через тот же ВК не двоит привязку и не пишет журнал заново."""
    email = _email("repeat")
    owner = await _make_user(db, email=email)
    await identity_link_service.link_existing_user(db, owner.id, "email", email)
    await db.commit()
    vk_id = _new_vk_id()

    await _login_via_vk(db, vk_id, email)
    await db.commit()
    user, created = await _login_via_vk(db, vk_id, email, access_token="acc2")
    await db.commit()

    assert created is False and user.id == owner.id
    links = (await db.execute(
        select(IdentityLink).where(IdentityLink.kind == "vk", IdentityLink.value == vk_id)
    )).scalars().all()
    assert len(links) == 1
    assert len(await _events(db, owner.id, "auth.vk.auto_linked_by_email")) == 1
