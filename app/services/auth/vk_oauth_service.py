"""VK ID 2.0 OAuth flow: обмен code → tokens, извлечение user_id.

Phase Y-1.5: добавлено auto-create user (см. ADR-0021). Race-safety: INSERT в
SAVEPOINT (begin_nested) — IntegrityError на UNIQUE(kind,value) откатывает
только savepoint, основная транзакция (с обменом VK token и атрибуцией
guest_session) продолжается.

tsk-755 (ADR-0054): совпадение почты из ВК с существующим аккаунтом больше не
409, а привязка — ВКонтакте подтверждает почту прежде, чем отдать её приложению,
поэтому она равна доказательству владения адресом, тому же, что даёт письмо со
ссылкой. Запрет из ADR-0021 §2 держался ровно на обратном предположении.
Слияние двух живых аккаунтов автоматом по-прежнему не делается.
"""
import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.core.config import Settings
from app.models.users import Users
from app.services.audit_service import log_event
from app.services.auth import identity_link_service
from app.services.auth.exceptions import IdentityConflictError
from app.services.auth.magic_link_service import mask_email
from app.services.fernet_service import encrypt_token

logger = logging.getLogger(__name__)

_VK_TOKEN_URL = "https://id.vk.com/oauth2/auth"
_VK_USERINFO_URL = "https://id.vk.com/oauth2/user_info"


__all__ = [
    "IdentityConflictError",  # re-export для backward compat импортов
    "exchange_code",
    "fetch_vk_userinfo",
    "get_or_create_user_by_vk",
    "get_vk_user_id",
]


async def exchange_code(
    code: str,
    code_verifier: str,
    device_id: str,
    settings: Settings,
) -> dict:
    """
    Обменять authorization_code (PKCE) на access+refresh токены VK ID 2.0.
    Возвращает dict с access_token, refresh_token, expires_in, user_id.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _VK_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "client_id": settings.vk_id_client_id,
                "device_id": device_id,
                "redirect_uri": settings.vk_id_redirect_uri,
            },
        )
    if resp.status_code != 200:
        logger.error("VK token exchange error %s: %s", resp.status_code, resp.text)
        raise ValueError("VK token exchange failed")

    data = resp.json()
    logger.info("VK exchange response keys=%s", list(data.keys()))
    if "error" in data:
        logger.error("VK exchange returned 200 OK with error body: %s", data)
        raise ValueError(f"VK error: {data.get('error')} desc={data.get('error_description')}")

    return data


async def get_vk_user_id(access_token: str) -> str:
    """Получить VK user_id через /user_info."""
    info = await fetch_vk_userinfo(access_token)
    return info["user_id"]


async def fetch_vk_userinfo(access_token: str) -> dict:
    """Получить VK userinfo: user_id, email (опц.), full_name (опц.).

    Возвращает dict с ключами user_id (str, обязательно), email (str | None),
    full_name (str | None). Email присутствует только если scope включил email.
    """
    logger.info("VK userinfo: requesting (token len=%d)", len(access_token) if access_token else 0)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _VK_USERINFO_URL,
            data={"access_token": access_token, "client_id": Settings().vk_id_client_id},
        )
    logger.info("VK userinfo: status=%d body_keys=%s", resp.status_code, list(resp.json().keys()) if resp.status_code == 200 else "<non-200>")
    if resp.status_code != 200:
        logger.error("VK userinfo error %s: %s", resp.status_code, resp.text)
        raise ValueError("VK userinfo failed")
    data = resp.json()
    if "error" in data:
        logger.error("VK userinfo returned 200 OK with error body: %s", data)
        raise ValueError(f"VK userinfo error: {data.get('error')}")
    user = data.get("user", {})
    uid = user.get("user_id") or user.get("id")
    if not uid:
        logger.error("VK userinfo no user_id, response: %s", data)
        raise ValueError("VK user_id not found in userinfo response")

    # tsk-363: VK может вернуть email пустой строкой. Приводим её к None —
    # иначе '' уходит в users.email, роняет ответ /api/v1/users/ (EmailStr)
    # и занимает слот в users_email_unique_partial, блокируя следующую
    # VK-регистрацию без почты.
    email = (user.get("email") or "").strip().lower() or None

    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    full_name = (first + " " + last).strip() or None

    return {"user_id": str(uid), "email": email, "full_name": full_name}


async def _link_vk_to_email_owner(
    db: AsyncSession,
    owner: Users,
    vk_user_id: str,
    email: str,
    *,
    enc_access: bytes,
    enc_refresh: bytes | None,
    expires_at: datetime | None,
    ip: str | None,
    user_agent: str | None,
    match_source: str,
) -> tuple[Users, bool]:
    """Привязать ВК к аккаунту, у которого та же подтверждённая почта (tsk-755).

    Вызывается только когда этот ВК ещё ни за кем не закреплён, а почта пришла
    от ВК непустой. Возвращает (владелец, False) — аккаунт существующий, новый
    не заводится.

    Каждая такая привязка записывается в журнал (`auth.vk.auto_linked_by_email`):
    кто, к какому аккаунту, по какой почте и по какому совпадению. Решение
    отменяет часть ADR-0021 §2, и разбирать его когда-нибудь придётся по
    фактическим строкам, а не по памяти.

    `match_source`:
      * ``identity_link`` — почта была полноценной identity аккаунта;
      * ``users_email_orphan`` — почта стояла только в карточке (`users.email`),
        входа по ней не было; недостающая email-identity достраивается здесь же,
        иначе следующий вход по письму завёл бы человеку второй аккаунт.
    """
    await identity_link_service.link_existing_user(
        db, owner.id, "vk", vk_user_id,
        vk_access_token_enc=enc_access,
        vk_refresh_token_enc=enc_refresh,
        vk_token_expires_at=expires_at,
    )
    if match_source == "users_email_orphan":
        await identity_link_service.upsert_identity(db, owner.id, "email", email)

    await log_event(
        db,
        "auth.vk.auto_linked_by_email",
        user_id=owner.id,
        ip=ip,
        user_agent=user_agent,
        details={
            "vk_user_id": vk_user_id,
            "email_masked": mask_email(email),
            "match_source": match_source,
        },
    )
    logger.info(
        "auth.vk.auto_linked_by_email user_id=%d vk_user_id=%s email=%s match=%s",
        owner.id, vk_user_id, mask_email(email), match_source,
    )
    return owner, False


async def _note_merge_candidate(
    db: AsyncSession,
    *,
    vk_owner: Users,
    email: str,
    vk_user_id: str,
    ip: str | None,
    user_agent: str | None,
) -> None:
    """Отметить для оператора два живых аккаунта одного человека (tsk-755).

    Случай: ВК ведёт в аккаунт А, а почта из того же ВК принадлежит аккаунту Б.
    Автоматически такое не сливается — на обоих аккаунтах могут быть работы и
    оценки, и выбор «что чьё» остаётся за человеком. Пишем запись в журнал,
    вход при этом идёт обычным ходом, в аккаунт А.

    Тишина здесь ничего не стоит и ничего не даёт: без записи пара всплывёт
    только жалобой ученика «мои курсы пропали».
    """
    email_owner = await identity_link_service.get_user_by_identity(db, "email", email)
    if email_owner is None or email_owner.id == vk_owner.id:
        return
    await log_event(
        db,
        "auth.vk.merge_candidate",
        user_id=vk_owner.id,
        ip=ip,
        user_agent=user_agent,
        details={
            "vk_user_id": vk_user_id,
            "email_masked": mask_email(email),
            "vk_account_id": vk_owner.id,
            "email_account_id": email_owner.id,
        },
    )
    logger.warning(
        "auth.vk.merge_candidate vk_account_id=%d email_account_id=%d email=%s",
        vk_owner.id, email_owner.id, mask_email(email),
    )


async def get_or_create_user_by_vk(
    db: AsyncSession,
    vk_user_id: str,
    email: str | None,
    full_name: str | None,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime | None,
    settings: Settings,
    ip: str | None,
    user_agent: str | None,
    current_user_id: int | None = None,
) -> tuple[Users, bool]:
    """Найти пользователя по vk-identity или создать нового атомарно.

    Если найден — обновляет VK token поля (ротация при каждом login).
    Если не найден, но вызывающий уже вошёл (`current_user_id`) — привязывает
    ВК к его аккаунту вместо заведения нового (tsk-629).
    Если не найден и email указан — привязывает ВК к аккаунту с этой же почтой
    (tsk-755, ADR-0054: почту от ВК провайдер подтверждает, значит она равна
    доказательству владения адресом). Новый аккаунт заводится только когда почты
    нет вовсе или такой почты ни у кого нет.
    Возвращает (user, created_flag).

    :param current_user_id: чей сеанс жив в момент входа через ВК, если он есть.
    """
    enc_access = encrypt_token(access_token, settings)
    enc_refresh = encrypt_token(refresh_token, settings) if refresh_token else None

    user = await identity_link_service.get_user_by_identity(db, "vk", vk_user_id)
    if user is not None:
        await identity_link_service.upsert_identity(
            db, user.id, "vk", vk_user_id,
            vk_access_token_enc=enc_access,
            vk_refresh_token_enc=enc_refresh,
            vk_token_expires_at=expires_at,
        )
        # tsk-755: ВК уже за этим аккаунтом, но почта из ВК может принадлежать
        # другому — это два живых аккаунта одного человека. Слить их автоматом
        # нельзя (там чужие работы и оценки), поэтому просто отмечаем пару для
        # оператора и пускаем человека туда, куда ведёт его ВК.
        if email:
            await _note_merge_candidate(
                db, vk_owner=user, email=email, vk_user_id=vk_user_id, ip=ip,
                user_agent=user_agent,
            )
        return user, False

    # tsk-629: этот ВК ещё ни за кем не закреплён, а человек уже внутри кабинета —
    # значит перед нами не новый ученик, а свой же, добавляющий второй способ входа.
    #
    # Раньше здесь заводился новый пустой аккаунт: ученик оказывался в кабинете без
    # единого курса и решал, что «всё пропало». Найдено на живом проде — у двух
    # учеников по два аккаунта; у одного вход через ВК случился через 21 секунду
    # после входа по почте, то есть прямо поверх живого сеанса.
    #
    # Привязка безопасна: владение аккаунтом уже доказано входом в него. Слияние по
    # совпадающей почте по-прежнему запрещено (ADR-0021): почта от ВК не заверена
    # провайдером, а живой сеанс — заверен нами.
    #
    # Порядок веток важен: если ВК УЖЕ закреплён за другим аккаунтом, мы сюда не
    # доходим — выше отработал вход в тот аккаунт. Иначе на общем компьютере второй
    # ученик, забыв про чужой незакрытый сеанс, привязал бы свой ВК к чужому профилю.
    if current_user_id is not None:
        await identity_link_service.link_existing_user(
            db, current_user_id, "vk", vk_user_id,
            vk_access_token_enc=enc_access,
            vk_refresh_token_enc=enc_refresh,
            vk_token_expires_at=expires_at,
        )
        linked_user = (await db.execute(
            select(Users).where(Users.id == current_user_id)
        )).scalar_one()
        logger.info(
            "vk.linked_to_current_session user_id=%d vk_user_id=%s", current_user_id, vk_user_id
        )
        return linked_user, False

    if email:
        # tsk-755: почта, пришедшая от ВК, — доказательство владения адресом:
        # ВКонтакте подтверждает почту прежде, чем отдать её приложению
        # (решение оператора 01.09.2026, ADR-0054). Поэтому совпадение почты
        # больше не тупик, а привязка: человек входит в СВОЙ аккаунт, а не
        # упирается в «сначала войдите по почте, потом привяжите ВК».
        #
        # Что здесь уже известно к этому месту и почему привязка безопасна:
        #   * этот ВК ещё ни за кем не закреплён (ветка выше вернула бы вход);
        #   * почта непустая, то есть человек дал согласие на неё в ВК
        #     (без согласия `fetch_vk_userinfo` отдаёт None, см. tsk-363);
        #   * значит перед нами владелец адреса, у которого на платформе уже
        #     есть аккаунт с этим же адресом.
        #
        # Слияние двух ЖИВЫХ аккаунтов остаётся запрещённым и сюда не попадает:
        # оно означало бы «ВК уже на аккаунте А, почта на аккаунте Б», а такой
        # вход обработан выше — человек просто заходит в А. Кандидат на слияние
        # при этом записывается для оператора (`_note_merge_candidate`).
        existing_email_user = await identity_link_service.get_user_by_identity(
            db, "email", email
        )
        if existing_email_user is not None:
            return await _link_vk_to_email_owner(
                db, existing_email_user, vk_user_id, email,
                enc_access=enc_access, enc_refresh=enc_refresh,
                expires_at=expires_at, ip=ip, user_agent=user_agent,
                match_source="identity_link",
            )

        # Осиротевшая почта: `users.email` заполнен (карточка ученика заведена
        # импортом), а identity_link kind='email' нет. Источник правды по
        # уникальности — partial index на users.email, поэтому INSERT нового
        # пользователя с этим адресом всё равно упал бы. Раньше здесь стоял 409;
        # теперь по тому же основанию привязываем ВК к этой карточке и заодно
        # достраиваем недостающую email-identity — иначе человек завёл бы себе
        # второй, пустой аккаунт рядом со своим настоящим.
        orphan = (await db.execute(
            select(Users).where(func.lower(Users.email) == email.lower())
        )).scalar_one_or_none()
        if orphan is not None:
            return await _link_vk_to_email_owner(
                db, orphan, vk_user_id, email,
                enc_access=enc_access, enc_refresh=enc_refresh,
                expires_at=expires_at, ip=ip, user_agent=user_agent,
                match_source="users_email_orphan",
            )

    new_user = Users(
        email=email, password_hash=None, full_name=full_name, tg_id=None,
    )
    try:
        async with db.begin_nested():
            db.add(new_user)
            await db.flush()
            await identity_link_service.upsert_identity(
                db, new_user.id, "vk", vk_user_id,
                vk_access_token_enc=enc_access,
                vk_refresh_token_enc=enc_refresh,
                vk_token_expires_at=expires_at,
            )
            if email:
                await identity_link_service.upsert_identity(db, new_user.id, "email", email)
            # Y-4 pre-S5: auto-assign student role в той же savepoint-транзакции.
            from app.services.auth.role_assign_service import ensure_student_role
            await ensure_student_role(
                db, new_user.id, channel="vk_callback", origin="auto_registration"
            )
    except IntegrityError:
        existing = await identity_link_service.get_user_by_identity(db, "vk", vk_user_id)
        if existing is None:
            raise
        await identity_link_service.upsert_identity(
            db, existing.id, "vk", vk_user_id,
            vk_access_token_enc=enc_access,
            vk_refresh_token_enc=enc_refresh,
            vk_token_expires_at=expires_at,
        )
        logger.info("vk_callback: race resolved, reusing existing user_id=%d", existing.id)
        return existing, False

    await log_event(
        db,
        "user.registered.via_vk",
        user_id=new_user.id,
        ip=ip,
        user_agent=user_agent,
        details={
            "identity_kind": "vk",
            "value_masked": vk_user_id,
            "email_provided": bool(email),
        },
    )
    logger.info("user.registered.via_vk user_id=%d email_provided=%s", new_user.id, bool(email))
    return new_user, True
