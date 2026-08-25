"""Сервис управления user_session (создание, валидация, отзыв)."""
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

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

# tsk-621: минимальный интервал между записями отметки последней активности
# сессии. Подробности — в `_last_used_is_stale`.
_LAST_USED_MIN_INTERVAL = timedelta(minutes=1)

# tsk-604: предохранитель обхода цепочки сессий. Цепочка — связный список
# (каждая ротация ставит старой сессии `replaced_by_session_id`), циклов в ней
# по построению не бывает: преемник всегда новее. Ограничение нужно, чтобы
# рекурсивный обход не зациклился, если запись когда-нибудь окажется битой.
_CHAIN_MAX_DEPTH = 1000

# tsk-604: причины отказа продления, которые требуют внимания в проде.
# Остальные (нет токена, протух, отозван логаутом) — штатный ход событий.
_REFRESH_DENY_ALERTING_REASONS = frozenset({"replay", "grace_cache_miss"})


def log_refresh_denied(
    reason: str,
    *,
    user_id: int | None = None,
    session_id: "UUID | None" = None,
    detail: str | None = None,
) -> None:
    """Записать в лог приложения одну строку с причиной отказа продления (tsk-604).

    Формат стабилен и рассчитан на поиск по логам прода::

        auth.refresh denied reason=<код> user_id=<id|-> session_id=<uuid|-> [detail]

    Коды причин:
      * ``no_token`` — refresh-токен не передан (обычный незалогиненный визит);
      * ``malformed`` — токен не является шестнадцатеричной строкой;
      * ``unknown`` — такого токена нет в базе;
      * ``expired`` — срок продления истёк;
      * ``revoked`` — сессия отозвана логаутом или блокировкой, не ротацией;
      * ``grace_cache_miss`` — распознанная гонка вкладок, но кэш окна пуст;
      * ``replay`` — повтор вне окна благодати, подозрение на кражу токена.

    Уровень: WARNING для ``replay`` и ``grace_cache_miss`` (нужен разбор),
    INFO для остальных. Сам токен в лог не попадает никогда — только
    идентификаторы пользователя и сессии.

    До tsk-604 причину отказа не писал никто, и в разборе tsk-594 её
    восстанавливали по размеру тела ответа в логе nginx (49 байт против 73).
    """
    level = logging.WARNING if reason in _REFRESH_DENY_ALERTING_REASONS else logging.INFO
    logger.log(
        level,
        "auth.refresh denied reason=%s user_id=%s session_id=%s%s",
        reason,
        user_id if user_id is not None else "-",
        session_id if session_id is not None else "-",
        f" {detail}" if detail else "",
    )


def _hash_token(raw: bytes) -> bytes:
    return hashlib.sha256(raw).digest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _last_used_is_stale(last_used_at: datetime | None, *, now: datetime) -> bool:
    """Пора ли обновлять отметку последней активности сессии (tsk-621).

    Поле — след активности для разбора инцидентов: наружу оно не отдаётся и
    ни в одной проверке доступа не участвует (`last_used_at` в `/me/identities`
    относится к `identity_link`, а не к сессии). Точность в одну минуту тут
    избыточна с запасом. Редкая запись здесь —
    не оптимизация, а защита от отказа: `UPDATE user_session` держит блокировку
    строки до конца транзакции запроса, и при обновлении на КАЖДОМ запросе все
    параллельные запросы одного пользователя выстраиваются в очередь друг за
    другом. Кабинет открывает десятки запросов разом (дерево курса), очередь
    перерастает таймаут пула — и 500 получают уже все пользователи, а не только
    владелец сессии (прод, 17.08.2026: 14 из 15 подключений ждали эту строку).

    Это ДЕШЁВЫЙ предфильтр, а не защита: он работает по значению, прочитанному
    до записи, без блокировки между чтением и записью. Когда порог открывается,
    его видят все параллельные запросы разом — за дедупликацию и за отсутствие
    очереди отвечает сам оператор записи (`_TOUCH_LAST_USED_SQL`), а эта
    проверка лишь избавляет от похода в базу в те 99% запросов, где писать
    заведомо нечего.

    Naive-значение (без часового пояса) сравнивать с aware-`now` нельзя —
    считаем его устаревшим и обновляем, приводя к корректному типу.
    """
    if last_used_at is None or last_used_at.tzinfo is None:
        return True
    return now - last_used_at >= _LAST_USED_MIN_INTERVAL


#: Запись отметки, которая не выстраивает залп запросов одного ученика в
#: очередь (tsk-675). Две части, и обе обязательны:
#:
#: * `last_used_at < :threshold` — из залпа строку переписывает ровно один
#:   запрос, остальным условие уже ложно;
#: * `FOR UPDATE SKIP LOCKED` — остальные не ЖДУТ, а сразу проходят мимо.
#:
#: Второе без первого не работает, и это не теория: замер 25.08 показал, что
#: один лишь условный `UPDATE ... AND last_used_at < ...` очередь НЕ убирает —
#: залп из шести так и занял 2,95-3,00 с против 2,95-3,10 с у безусловной
#: записи, хотя строку переписал ровно один запрос. Причина в самом
#: PostgreSQL: при READ COMMITTED ждущий `UPDATE` сначала встаёт на замок
#: строки и только потом перепроверяет условие по свежей версии, а взятый
#: замок остаётся до конца его транзакции даже когда обновлять уже нечего.
#: Со `SKIP LOCKED` занятая строка просто не попадает в выборку: залп занял
#: 0,95-1,28 с при базовой линии 0,90-1,01 с — сложения нет вовсе.
#:
#: Цена — отметка может пропустить такт, если строка занята соседним запросом
#: или ротацией токена. Для следа активности это ничего не значит: следующий
#: запрос ученика поставит её заново.
_TOUCH_LAST_USED_SQL = text(
    """
    UPDATE user_session
       SET last_used_at = :now
     WHERE id = (
           SELECT id
             FROM user_session
            WHERE id = CAST(:session_id AS uuid)
              AND last_used_at < :threshold
              FOR UPDATE SKIP LOCKED
           )
    """
)


async def _touch_last_used(
    db: AsyncSession, session: "UserSession", *, now: datetime
) -> None:
    """Отметить активность сессии, не задерживая соседние запросы (tsk-675).

    Запись идёт в транзакции запроса — отдельная короткая транзакция сюда
    просилась, но замер её не оправдал: она снимает удержание замка, а не
    саму запись, поэтому шесть запросов залпа по-прежнему пишут шесть раз и
    берут по лишнему подключению из пула (1,44-1,60 с против 0,95-1,28 с у
    выбранного варианта). Исчерпание пула — та самая авария tsk-621, ради
    которой всё это и затевалось.
    """
    result = await db.execute(
        _TOUCH_LAST_USED_SQL,
        {
            "now": now,
            "session_id": str(session.id),
            "threshold": now - _LAST_USED_MIN_INTERVAL,
        },
    )
    if result.rowcount:
        # Записали мы — синхронизируем объект в памяти как УЖЕ сохранённое
        # значение. Обычным присваиванием нельзя: объект стал бы «изменённым»,
        # и `commit()` роутера выпустил бы ещё один `UPDATE user_session` —
        # ровно ту запись, от которой здесь уходим.
        set_committed_value(session, "last_used_at", now)


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
        now = _now()
        if _last_used_is_stale(session.last_used_at, now=now):
            await _touch_last_used(db, session, now=now)
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
    отзывается цепочка сессий, к которой принадлежит этот токен.

    tsk-604: радиус отзыва сужен с «все сессии пользователя» до «эта цепочка».
    Угон по-прежнему обрубается — вор и владелец сидят в одной цепочке, кто бы
    из них ни прислал повтор. А ученик, у которого повтор случился мирно
    (браузер не сохранил новую пару cookie), теряет доступ только на этом
    устройстве, а не на телефоне и ноутбуке разом. По фактам прода за две
    недели оба срабатывания защиты были именно такими мирными повторами.

    Любой отказ пишется в лог одной строкой через `log_refresh_denied`.

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
        log_refresh_denied("malformed")
        return None
    rh = _hash_token(raw)
    result = await db.execute(
        select(UserSession).where(UserSession.refresh_token_hash == rh).with_for_update()
    )
    old = result.scalar_one_or_none()
    if old is None:
        log_refresh_denied("unknown")
        return None

    if old.revoked_at is None:
        # Штатный путь: токен ещё активен. NULL здесь — не «бессрочный», а
        # «невалиден» (сохраняем семантику исходного SQL-фильтра
        # `refresh_expires_at > now()`, где NULL > now() ложно и строка
        # исключалась; `create_session` всегда проставляет это поле —
        # NULL в проде не встречается, но ветка обязана вести себя так же).
        if old.refresh_expires_at is None or old.refresh_expires_at <= _now():
            log_refresh_denied("expired", user_id=old.user_id, session_id=old.id)
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
        log_refresh_denied("revoked", user_id=old.user_id, session_id=old.id)
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
        log_refresh_denied("grace_cache_miss", user_id=old.user_id, session_id=old.id)
        return None

    # Повторное использование токена ПОСЛЕ окна благодати — настоящий replay
    # (кража токена): отзываем цепочку сессий, которой принадлежит токен.
    # Остальные устройства пользователя живут своими цепочками и не страдают
    # (tsk-604).
    revoked_count = await revoke_session_chain(db, old.id)
    log_refresh_denied(
        "replay",
        user_id=old.user_id,
        session_id=old.id,
        detail=f"revoked_at={old.revoked_at} revoked_sessions={revoked_count}",
    )
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


async def revoke_session_chain(db: AsyncSession, session_id: UUID) -> int:
    """Отозвать цепочку сессий, начиная с указанной и вперёд по ротациям (tsk-604).

    Цепочка — связный список: при каждом продлении старой сессии проставляется
    `replaced_by_session_id` на её преемника. Обход идёт только вперёд —
    предшественники уже отозваны самой ротацией, а сессии других устройств
    растут из своего входа и в эту цепочку не входят.

    Один рекурсивный запрос вместо цикла обращений: отзыв должен быть
    неделимым, иначе между шагами успевает проскочить очередное продление.
    Отзываются только ещё живые строки — у отозванных сохраняется исходная
    отметка времени. Глубина ограничена `_CHAIN_MAX_DEPTH` на случай битой
    записи (циклов в цепочке по построению не бывает).

    Возвращает число реально отозванных сессий.
    """
    result = await db.execute(
        text(
            """
            WITH RECURSIVE chain(id, depth) AS (
                SELECT CAST(:start_id AS uuid), 0
                UNION ALL
                SELECT s.replaced_by_session_id, c.depth + 1
                  FROM user_session AS s
                  JOIN chain AS c ON s.id = c.id
                 WHERE s.replaced_by_session_id IS NOT NULL
                   AND c.depth < :max_depth
            )
            UPDATE user_session AS t
               SET revoked_at = :now
              FROM chain
             WHERE t.id = chain.id
               AND t.revoked_at IS NULL
            RETURNING t.id
            """
        ),
        {"start_id": str(session_id), "max_depth": _CHAIN_MAX_DEPTH, "now": _now()},
    )
    revoked = len(result.fetchall())
    await db.flush()
    return revoked


async def revoke_all_sessions(db: AsyncSession, user_id: int) -> None:
    """Отозвать все активные сессии пользователя."""
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    await db.flush()
