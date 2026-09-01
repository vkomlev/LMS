"""tsk-629: вход через ВК поверх живого сеанса привязывается, а не двоит аккаунт.

Найдено на живом проде по обращению ученика: у него оказалось два аккаунта —
рабочий (вход по почте, два курса) и заведённый входом через ВК (пустой). ВК
почту не отдал, поэтому защита от совпадения почты (ADR-0021) не срабатывала, и
создавался новый пользователь. По логам вход через ВК случился через 21 секунду
после входа по почте — то есть прямо поверх живого сеанса.

Живой сеанс — доказательство владения аккаунтом, поэтому привязка к нему
безопасна. Слияние по совпадающей почте так и остаётся запрещённым: почту от ВК
провайдер не заверяет.
"""
import os
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models.identity_link import IdentityLink
from app.models.users import Users
from app.services.auth.exceptions import IdentityConflictError
from app.services.auth.vk_oauth_service import get_or_create_user_by_vk


def _new_vk_id() -> str:
    return str(random.SystemRandom().randint(10**8, 10**10))


async def _make_user(db, *, email: str | None = None, full_name: str = "Ученик") -> Users:
    user = Users(email=email, password_hash=None, full_name=full_name, tg_id=None)
    db.add(user)
    await db.flush()
    return user


@pytest.mark.asyncio
async def test_vk_over_live_session_links_instead_of_new_account(db):
    """Человек уже в кабинете → ВК привязывается к нему, второй аккаунт не заводится."""
    settings = Settings()
    vk_id = _new_vk_id()
    email = f"student-{os.urandom(4).hex()}@example.com"
    existing = await _make_user(db, email=email, full_name="Астафьев Данил")
    users_before = len((await db.execute(select(Users.id))).scalars().all())

    user, created = await get_or_create_user_by_vk(
        db, vk_user_id=vk_id,
        # Ровно случай с прода: ВК почту не вернул.
        email=None, full_name="Данил Астафьев",
        access_token="acc", refresh_token="ref",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=3600),
        settings=settings, ip="127.0.0.1", user_agent="test",
        current_user_id=existing.id,
    )
    await db.commit()

    assert created is False
    assert user.id == existing.id
    users_after = len((await db.execute(select(Users.id))).scalars().all())
    assert users_after == users_before, "новый пользователь заводиться не должен"

    link = (await db.execute(
        select(IdentityLink).where(IdentityLink.kind == "vk", IdentityLink.value == vk_id)
    )).scalar_one()
    assert link.user_id == existing.id


@pytest.mark.asyncio
async def test_vk_over_live_session_keeps_existing_profile(db):
    """Привязка не перетирает данные аккаунта именем из ВК."""
    settings = Settings()
    existing = await _make_user(db, email=f"keep-{os.urandom(4).hex()}@example.com",
                                full_name="Астафьев Данил Алексеевич")

    user, _ = await get_or_create_user_by_vk(
        db, vk_user_id=_new_vk_id(), email=None, full_name="Данил А.",
        access_token="acc", refresh_token=None, expires_at=None,
        settings=settings, ip=None, user_agent=None,
        current_user_id=existing.id,
    )
    await db.commit()

    assert user.full_name == "Астафьев Данил Алексеевич"


@pytest.mark.asyncio
async def test_known_vk_wins_over_live_session(db):
    """Чужой незакрытый сеанс не уводит вход: этот ВК уже за своим аккаунтом.

    Общий компьютер: первый ученик не вышел, второй жмёт «войти через ВК». Он
    обязан попасть в СВОЙ аккаунт, а не привязать свой ВК к чужому профилю.
    """
    settings = Settings()
    vk_id = _new_vk_id()
    vk_owner = await _make_user(db, full_name="Хозяин ВК")
    someone_else = await _make_user(db, email=f"other-{os.urandom(4).hex()}@example.com")

    # Первый вход владельца — ВК закрепляется за ним.
    await get_or_create_user_by_vk(
        db, vk_user_id=vk_id, email=None, full_name=None,
        access_token="acc", refresh_token=None, expires_at=None,
        settings=settings, ip=None, user_agent=None,
        current_user_id=vk_owner.id,
    )
    await db.commit()

    # Теперь тот же ВК приходит поверх чужого живого сеанса.
    user, created = await get_or_create_user_by_vk(
        db, vk_user_id=vk_id, email=None, full_name=None,
        access_token="acc2", refresh_token=None, expires_at=None,
        settings=settings, ip=None, user_agent=None,
        current_user_id=someone_else.id,
    )
    await db.commit()

    assert created is False
    assert user.id == vk_owner.id, "вход должен вести в аккаунт владельца ВК"
    links = (await db.execute(
        select(IdentityLink).where(IdentityLink.kind == "vk", IdentityLink.value == vk_id)
    )).scalars().all()
    assert len(links) == 1 and links[0].user_id == vk_owner.id


@pytest.mark.asyncio
async def test_second_vk_of_same_person_conflicts_with_other_profile(db):
    """ВК, закреплённый за другим профилем, к текущему не привязывается."""
    settings = Settings()
    vk_id = _new_vk_id()
    owner = await _make_user(db, full_name="Владелец")
    await get_or_create_user_by_vk(
        db, vk_user_id=vk_id, email=None, full_name=None,
        access_token="acc", refresh_token=None, expires_at=None,
        settings=settings, ip=None, user_agent=None, current_user_id=owner.id,
    )
    await db.commit()

    # Прямая привязка того же ВК к другому пользователю (минуя ветку «вход по
    # известному ВК») обязана упереться в защиту от захвата личности.
    other = await _make_user(db, full_name="Другой")
    from app.services.auth import identity_link_service
    with pytest.raises(IdentityConflictError) as err:
        await identity_link_service.link_existing_user(db, other.id, "vk", vk_id)
    assert err.value.conflict_kind == "vk_already_linked"


@pytest.mark.asyncio
async def test_no_live_session_behaves_as_before(db):
    """Без живого сеанса поведение прежнее: заводится новый аккаунт."""
    settings = Settings()
    users_before = len((await db.execute(select(Users.id))).scalars().all())

    user, created = await get_or_create_user_by_vk(
        db, vk_user_id=_new_vk_id(), email=None, full_name="Новый Ученик",
        access_token="acc", refresh_token=None, expires_at=None,
        settings=settings, ip=None, user_agent=None,
        current_user_id=None,
    )
    await db.commit()

    assert created is True
    users_after = len((await db.execute(select(Users.id))).scalars().all())
    assert users_after == users_before + 1
    assert user.full_name == "Новый Ученик"


@pytest.mark.asyncio
async def test_email_overlap_now_links_without_session(db):
    """tsk-755 отменил здесь запрет: совпадение почты — привязка, а не 409.

    Правило tsk-629 (живой сеанс = доказательство владения) осталось; к нему
    добавилось второе основание — подтверждённая ВКонтакте почта (ADR-0054).
    Живого сеанса тут нет, и вход всё равно ведёт в существующий аккаунт.
    """
    settings = Settings()
    email = f"overlap-{os.urandom(4).hex()}@example.com"
    owner = await _make_user(db, email=email)
    from app.services.auth import identity_link_service
    await identity_link_service.link_existing_user(db, owner.id, "email", email)
    await db.commit()

    user, created = await get_or_create_user_by_vk(
        db, vk_user_id=_new_vk_id(), email=email, full_name=None,
        access_token="acc", refresh_token=None, expires_at=None,
        settings=settings, ip=None, user_agent=None,
        current_user_id=None,
    )
    await db.commit()

    assert created is False
    assert user.id == owner.id
