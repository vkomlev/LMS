"""Сервис управления user_session (создание, валидация, отзыв)."""
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_session import UserSession

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_TOKEN_BYTES = 32
# Y-5.2: продлеваем session TTL с 1ч до 24ч — иначе ученик за 1 урок (~30-60 мин)
# теряет сессию и должен снова логиниться. UX-блокер. 24 часа — удобный баланс
# (студент возвращается на следующий день; refresh — 30 дней).
_ACCESS_TTL_HOURS = 24
_REFRESH_TTL_DAYS = 30

# tsk-235: окно благодати на ротацию refresh-токена. Две вкладки SPW делят одну
# refresh-cookie; без окна конкурентный refresh из второй вкладки ловит 401
# ("Не удалось сохранить"), хотя первая вкладка уже успешно обновилась.
# 20 сек — с запасом покрывает сетевую задержку двух почти одновременных
# запросов, но не ослабляет детект кражи токена (replay спустя минуты/часы
# по-прежнему считается подозрительным).
_REFRESH_GRACE_WINDOW_SECONDS = 20
# Небольшой запас над окном благодати: кэш не должен истечь раньше, чем
# DB-проверка window перестанет считать повтор легитимным — иначе валидная
# гонка на границе окна ложно деградирует в "cache miss" вместо возврата пары.
_REFRESH_GRACE_CACHE_TTL_SECONDS = _REFRESH_GRACE_WINDOW_SECONDS + 5


def _hash_token(raw: bytes) -> bytes:
    return hashlib.sha256(raw).digest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_session(
    db: AsyncSession,
    user_id: int,
    ua_fingerprint: str | None = None,
) -> tuple[str, str, "UserSession"]:
    """
    Создать новую сессию.
    Возвращает (access_token, refresh_token, UserSession).
    """
    access_raw = os.urandom(_TOKEN_BYTES)
    refresh_raw = os.urandom(_TOKEN_BYTES)

    now = _now()
    session = UserSession(
        user_id=user_id,
        token_hash=_hash_token(access_raw),
        refresh_token_hash=_hash_token(refresh_raw),
        ua_fingerprint=ua_fingerprint,
        expires_at=now + timedelta(hours=_ACCESS_TTL_HOURS),
        refresh_expires_at=now + timedelta(days=_REFRESH_TTL_DAYS),
    )
    db.add(session)
    await db.flush()

    access_token = access_raw.hex()
    refresh_token = refresh_raw.hex()
    return access_token, refresh_token, session


async def validate_session(
    db: AsyncSession,
    access_token: str,
) -> "UserSession | None":
    """Проверить access_token; вернуть сессию если валидна и не истекла."""
    try:
        raw = bytes.fromhex(access_token)
    except ValueError:
        return None
    token_hash = _hash_token(raw)
    result = await db.execute(
        select(UserSession).where(
            UserSession.token_hash == token_hash,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > _now(),
        )
    )
    session = result.scalar_one_or_none()
    if session:
        session.last_used_at = _now()
        await db.flush()
    return session


def _grace_key(old_refresh_hash: bytes) -> str:
    return f"session_refresh_grace:{old_refresh_hash.hex()}"


async def _cache_grace_pair(
    redis: "aioredis.Redis | None",
    old_refresh_hash: bytes,
    access_token: str,
    refresh_token: str,
    session_id: UUID,
) -> None:
    """Закешировать пару токенов преемника на окно благодати (best-effort, fail-open).

    Raw-токены хранятся в БД только как hash — без кэша повторный (конкурентный)
    refresh тем же старым токеном не может получить ту же пару обратно. TTL
    Redis-недоступность не должна ронять основную ротацию — только отключает
    идемпотентность повтора (деградация до pre-fix поведения, не отказ).
    """
    if redis is None:
        return
    payload = json.dumps(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "session_id": str(session_id),
        }
    )
    try:
        await redis.set(
            _grace_key(old_refresh_hash), payload, ex=_REFRESH_GRACE_CACHE_TTL_SECONDS
        )
    except Exception:
        logger.warning(
            "refresh_session: Redis недоступен при записи grace-кэша (fail-open)"
        )


async def _get_grace_pair(
    redis: "aioredis.Redis | None", old_refresh_hash: bytes
) -> "dict[str, Any] | None":
    if redis is None:
        return None
    try:
        payload = await redis.get(_grace_key(old_refresh_hash))
    except Exception:
        logger.warning(
            "refresh_session: Redis недоступен при чтении grace-кэша (fail-open)"
        )
        return None
    if payload is None:
        return None
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        logger.error("refresh_session: повреждён payload grace-кэша")
        return None


async def refresh_session(
    db: AsyncSession,
    refresh_token: str,
    redis: "aioredis.Redis | None" = None,
) -> "tuple[str, str, UserSession] | None":
    """
    Выдать новую пару токенов по refresh_token. Старая сессия отзывается.

    tsk-235: окно благодати на ротацию — конкурентный refresh тем же (уже
    отозванным) токеном в течение `_REFRESH_GRACE_WINDOW_SECONDS` после ротации
    получает ТУ ЖЕ пару токенов преемника (идемпотентно, без создания ещё одной
    сессии), а не 401. Повтор ПОСЛЕ окна — подозрение на кражу/replay токена:
    отзывается вся цепочка сессий пользователя.

    `.with_for_update()` — без него два ПОДЛИННО одновременных запроса (не
    просто «второй чуть позже первого») читают `revoked_at IS NULL` ДО того,
    как любой из них закоммитится, и оба уходят в штатную ветку ротации: два
    новых session вместо одного, цепочка размножается — именно то, чего окно
    благодати обязано избегать. Блокировка строки сериализует их: второй
    запрос ждёт commit первого и повторно видит уже актуальный
    revoked_at + replaced_by_session_id.
    """
    try:
        raw = bytes.fromhex(refresh_token)
    except ValueError:
        return None
    rh = _hash_token(raw)
    result = await db.execute(
        select(UserSession).where(UserSession.refresh_token_hash == rh).with_for_update()
    )
    old = result.scalar_one_or_none()
    if old is None:
        return None

    if old.revoked_at is None:
        # Штатный путь: токен ещё активен. NULL здесь — не «бессрочный», а
        # «невалиден» (сохраняем семантику исходного SQL-фильтра
        # `refresh_expires_at > now()`, где NULL > now() ложно и строка
        # исключалась; `create_session` всегда проставляет это поле —
        # NULL в проде не встречается, но ветка обязана вести себя так же).
        if old.refresh_expires_at is None or old.refresh_expires_at <= _now():
            return None
        new_access, new_refresh, new_session = await create_session(
            db, old.user_id, old.ua_fingerprint
        )
        old.revoked_at = _now()
        old.replaced_by_session_id = new_session.id
        await db.flush()
        await _cache_grace_pair(redis, rh, new_access, new_refresh, new_session.id)
        return new_access, new_refresh, new_session

    if old.replaced_by_session_id is None:
        # Токен отозван не через ротацию (logout/revoke_all) — обычный протухший
        # токен, поведение не меняется, признака кражи здесь нет.
        return None

    if _now() - old.revoked_at <= timedelta(seconds=_REFRESH_GRACE_WINDOW_SECONDS):
        # Гонка ротации между вкладками: этот же refresh_token только что был
        # заменён другим конкурентным запросом. Возвращаем ЕГО пару токенов.
        cached = await _get_grace_pair(redis, rh)
        if cached is not None:
            successor = await db.execute(
                select(UserSession).where(UserSession.id == UUID(cached["session_id"]))
            )
            new_session = successor.scalar_one_or_none()
            if new_session is not None:
                return cached["access_token"], cached["refresh_token"], new_session
        # Кэш недоступен/протух (Redis-сбой, граница TTL) — деградация без
        # сигнала о краже: это распознанная гонка, а не подозрительный replay.
        logger.warning(
            "refresh_session: grace-window cache miss user_id=%s session_id=%s",
            old.user_id, old.id,
        )
        return None

    # Повторное использование токена ПОСЛЕ окна благодати — настоящий replay
    # (кража токена): отзываем всю цепочку сессий пользователя.
    logger.warning(
        "refresh_session: replay refresh-токена вне окна благодати user_id=%s "
        "session_id=%s revoked_at=%s — отзыв всей цепочки сессий",
        old.user_id, old.id, old.revoked_at,
    )
    await revoke_all_sessions(db, old.user_id)
    # Роутер коммитит транзакцию только на успешном пути (result is not None);
    # при result=None он сразу raise HTTPException(401) без commit — без явного
    # commit здесь отзыв цепочки откатится вместе с транзакцией при закрытии
    # сессии, и security-фикс молча не сработает.
    await db.commit()
    return None


async def revoke_session(db: AsyncSession, session_id: UUID) -> None:
    """Отозвать конкретную сессию."""
    await db.execute(
        update(UserSession)
        .where(UserSession.id == session_id)
        .values(revoked_at=_now())
    )
    await db.flush()


async def revoke_all_sessions(db: AsyncSession, user_id: int) -> None:
    """Отозвать все активные сессии пользователя."""
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    await db.flush()
