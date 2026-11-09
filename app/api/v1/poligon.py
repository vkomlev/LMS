"""Polygon-only эндпоинты: регистрация/каталог/промокод/checkout/enroll (tsk-182 + tsk-1468).

Ветка `poligon` ТОЛЬКО. В `app/api/main.py` этой ветки:
    from app.api.v1.poligon import router as poligon_router
    app.include_router(poligon_router, prefix="/api/v1")

Содержит 2 намеренных дефекта (классы 2 и 3 реестра, docs/qa-poligon/
defect-registry.md) — см. комментарии `# DEFECT` на месте. Всё остальное —
обычный, корректный код (не нужно засеивать баг в КАЖДОЙ строке — полигон
должен в целом работать, иначе дефекты не будут выделяться на фоне шума).
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user, CurrentUser
from app.core.config import Settings
from app.models.courses import Courses
from app.models.users import Users
from app.schemas.poligon import (
    CatalogCourseOut,
    CheckoutRequest,
    CheckoutResponse,
    EnrollRequest,
    EnrollResponse,
    PoligonLoginRequest,
    PoligonLoginResponse,
    PoligonRegisterRequest,
    PoligonRegisterResponse,
    PromoApplyRequest,
    PromoApplyResponse,
)
from app.services import audit_service
from app.services.auth import session_service
from app.services.auth.cookie import set_session_cookie
from app.services.auth.role_assign_service import ensure_student_role
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/poligon", tags=["poligon"])
_settings = Settings()

_LOGIN_SESSION_TTL_SECONDS = 3600  # как у issue-session: blast-radius ограничен


def _validate_service_key_constant_time(provided: str | None) -> bool:
    """Constant-time compare X-API-Key против ВСЕХ valid_api_keys."""
    if not provided:
        return False
    found = False
    for valid in _settings.valid_api_keys:
        if secrets.compare_digest(provided, valid):
            found = True
            # NB: не break — выполняем все compare_digest для constant-time
    return found


@router.post(
    "/register",
    response_model=PoligonRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация ученика учебного полигона (только ветка poligon)",
    responses={
        409: {"description": "Email уже зарегистрирован"},
        422: {"description": "Невалидные имя/email"},
    },
)
async def register_student(
    body: PoligonRegisterRequest,
    db: AsyncSession = Depends(get_async_db),
) -> PoligonRegisterResponse:
    """Создать ученика полигона: имя + email (tsk-1468).

    Модель создания — по образцу scripts/poligon_seed.py: INSERT в users,
    student-роль через ensure_student_role, identity_link 'email'.
    Повторный email → 409 с понятным сообщением (двойная защита: явный SELECT
    до INSERT + перехват IntegrityError от partial unique index на users.email
    при гонке). 500 здесь недопустимы — форма регистрации публичная.
    """
    email = body.email.strip().lower()
    full_name = body.full_name.strip()
    if not full_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Имя не может быть пустым")

    existing = (
        await db.execute(select(Users.id).where(Users.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Этот email уже зарегистрирован. Войдите через страницу входа.",
        )

    try:
        user_id = (
            await db.execute(
                text(
                    "INSERT INTO users (email, full_name) VALUES (:email, :name) "
                    "RETURNING id"
                ),
                {"email": email, "name": full_name},
            )
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO identity_link (user_id, kind, value) "
                "VALUES (:uid, 'email', :value)"
            ),
            {"uid": user_id, "value": email},
        )
        await ensure_student_role(
            db, user_id,
            channel="poligon_register", origin="auto_registration",
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.info("poligon register: email conflict (race): %s", email)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Этот email уже зарегистрирован. Войдите через страницу входа.",
        ) from None

    return PoligonRegisterResponse(
        user_id=user_id,
        message="Регистрация прошла успешно. Теперь войдите на странице входа.",
    )


@router.post(
    "/login-by-email",
    response_model=PoligonLoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Вход ученика полигона по email без письма (только ветка poligon)",
    responses={
        200: {"description": "Cookie-сессия выдана"},
        401: {"description": "Invalid X-API-Key"},
        403: {"description": "Аккаунт не является учеником полигона"},
        404: {"description": "Пользователь с таким email не найден"},
    },
)
async def login_by_email(
    body: PoligonLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> PoligonLoginResponse:
    """Вход по email для самозарегистрированных учеников полигона (tsk-1469).

    Песочница без реальной почты: подтверждение владения email не выполняется
    (это осознанный dev-вход уровня «полигон», как выбор персоны). Защита от
    чужих аккаунтов — трёхслойная:
      1. X-API-Key (constant-time) — ключ знает только серверный прокси SPW,
         прямые анонимные вызовы к LMS отсекаются;
      2. пользователь обязан существовать в БД СВОЕГО tier'а (полигон живёт
         на отдельных БД poligon_dev/test/stage, боевых аккаунтов там нет);
      3. у пользователя обязана быть роль student — её получают и посевные
         персоны (scripts/poligon_seed.py), и зарегистрированные через
         /poligon/register (tsk-1468). Боевой аккаунт без student-роли
         отсекается даже при ошибочном подключении к боевой БД.
    Сессия выдаётся по образцу /auth/test/issue-session: TTL=3600,
    HttpOnly-cookie через set_session_cookie, audit без значения cookie.
    """
    if not _validate_service_key_constant_time(x_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid X-API-Key")

    email = body.email.strip().lower()
    user = (
        await db.execute(select(Users).where(Users.email == email))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Пользователь с таким email не найден. Сначала зарегистрируйтесь.",
        )

    student_role = (
        await db.execute(
            text(
                "SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
                "WHERE ur.user_id = :uid AND r.name = 'student'"
            ),
            {"uid": user.id},
        )
    ).first()
    if student_role is None:
        logger.info("poligon login-by-email: user %s без student-роли — отказ", user.id)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Этот аккаунт не является учеником полигона.",
        )

    ip = request.client.host if request.client else None
    access_token, _refresh_token, session = await session_service.create_session(
        db, user_id=user.id, ua_fingerprint=request.headers.get("user-agent"),
    )
    custom_expires = datetime.now(timezone.utc) + timedelta(
        seconds=_LOGIN_SESSION_TTL_SECONDS
    )
    session.expires_at = custom_expires
    await db.flush()

    await audit_service.log_event(
        db,
        audit_service.AUTH_TEST_SESSION_ISSUED,
        user_id=user.id,
        ip=ip,
        details={
            "session_id": str(session.id),
            "ttl_seconds": _LOGIN_SESSION_TTL_SECONDS,
            "channel": "poligon_email_login",
        },
    )
    await db.commit()

    set_session_cookie(
        response, access_token,
        max_age=_LOGIN_SESSION_TTL_SECONDS,
        secure=_settings.cookie_secure,
    )
    logger.info("poligon login-by-email: сессия выдана user_id=%s (ip=%s)", user.id, ip)

    return PoligonLoginResponse(
        user_id=user.id,
        full_name=user.full_name or "",
        message="Вход выполнен.",
    )


@router.get("/catalog", response_model=list[CatalogCourseOut])
async def get_catalog(db: AsyncSession = Depends(get_async_db)) -> list[CatalogCourseOut]:
    """Публичный каталог — только курсы полигона (course_uid LIKE 'poligon-%'),
    аномалия для Г9 (`poligon-sql-anomaly-*`) сознательно НЕ включена в выдачу —
    она находится только прямым SQL-запросом к БД, не через API."""
    result = await db.execute(
        select(Courses).where(
            Courses.course_uid.like("poligon-%"),
            ~Courses.course_uid.like("poligon-sql-anomaly-%"),
        )
    )
    return [
        CatalogCourseOut(
            id=c.id, course_uid=c.course_uid, title=c.title, price=float(c.price or 0)
        )
        for c in result.scalars().all()
    ]


async def _get_promo(db: AsyncSession, code: str) -> tuple[str, int] | None:
    row = (
        await db.execute(
            text(
                "SELECT code, discount_percent FROM poligon_promo_codes WHERE code = :code"
            ),
            {"code": code},
        )
    ).first()
    return (row.code, row.discount_percent) if row else None


@router.post("/promo/apply", response_model=PromoApplyResponse)
async def apply_promo(
    body: PromoApplyRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> PromoApplyResponse:
    promo = await _get_promo(db, body.code)
    if promo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Промокод не найден")
    code, discount_percent = promo

    # DEFECT (класс 8 реестра): здесь ДОЛЖНА быть проверка "уже применялся
    # этим пользователем" (SELECT в poligon_promo_redemptions по (user_id,
    # promo_code) ДО INSERT) — сознательно опущена. INSERT происходит всегда,
    # без уникального ограничения на уровне таблицы (см. migration) —
    # повторное применение снова возвращает promo_applied=true.
    await db.execute(
        text(
            "INSERT INTO poligon_promo_redemptions (user_id, promo_code) VALUES (:u, :c)"
        ),
        {"u": current_user.id, "c": code},
    )
    await db.commit()

    return PromoApplyResponse(promo_applied=True, discount_percent=discount_percent)


@router.post("/checkout", response_model=CheckoutResponse)
async def checkout(
    body: CheckoutRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> CheckoutResponse:
    course = (
        await db.execute(select(Courses).where(Courses.id == body.course_id))
    ).scalar_one_or_none()
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Курс не найден")

    base_price = float(course.price)
    discount_percent = 0
    promo_applied = False
    if body.promo_code:
        promo = await _get_promo(db, body.promo_code)
        if promo is not None:
            _, discount_percent = promo
            promo_applied = True

    # DEFECT (класс 2 реестра, урок 6.4): скидка не применяется в ветке
    # payment_method == "card" — сумма списания равна полной цене, хотя
    # ответ ниже (promo_applied/discount_percent) честно отражает, что
    # промокод был распознан. Ветка "wallet" считает корректно.
    if promo_applied and body.payment_method == "wallet":
        amount_charged = round(base_price * (1 - discount_percent / 100), 2)
    else:
        amount_charged = base_price  # DEFECT: для payment_method="card" это неверно,
        # если promo_applied=True — должно быть amount_charged со скидкой.

    order_id_row = (
        await db.execute(
            text(
                "INSERT INTO poligon_enrollments (user_id, course_id, payment_method, amount_charged) "
                "VALUES (:u, :c, :pm, :amt) "
                "ON CONFLICT (user_id, course_id) DO UPDATE SET amount_charged = EXCLUDED.amount_charged "
                "RETURNING id"
            ),
            {
                "u": current_user.id,
                "c": course.id,
                "pm": body.payment_method,
                "amt": amount_charged,
            },
        )
    ).scalar_one()
    await db.commit()

    return CheckoutResponse(
        order_id=order_id_row,
        promo_applied=promo_applied,
        discount_percent=discount_percent,
        amount_charged=amount_charged,
    )


@router.post("/enroll", response_model=EnrollResponse)
async def enroll(
    body: EnrollRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> EnrollResponse:
    """DEFECT (класс 3 реестра): исключение из БД (например, гонка/конфликт)
    намеренно проглатывается без re-raise и без отдельного commit — ответ
    всегда 200/enrolled=true, даже если запись физически не создалась.
    Это НЕ демонстрация «как правильно» — это воспроизведение реального
    класса ошибок (см. project_lms_service_side_effects в памяти проекта:
    коммит-в-чужой-транзакции — одна из самых частых причин именно такого
    расхождения в проде)."""
    try:
        await db.execute(
            text(
                "INSERT INTO user_courses (user_id, course_id, is_active) "
                "VALUES (:u, :c, TRUE)"
            ),
            {"u": current_user.id, "c": body.course_id},
        )
        # DEFECT: commit намеренно НЕ вызван здесь — транзакция откатится при
        # закрытии сессии (или на следующем db.rollback() где-то выше по стеку
        # запроса), а обработчик уже вернул enrolled=true.
    except Exception:
        logger.warning("poligon enroll: insert failed silently (by design, defect class 3)")

    return EnrollResponse(enrolled=True)
