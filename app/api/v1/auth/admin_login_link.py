"""
Admin-ручки ученика в обход обычной регистрации (tsk-930, tsk-931).

Запрос оператора (tsk-930): у ученика не работают оба обычных пути — нет
ВК-аккаунта и не приходит magic-link на почту. Нужен административный способ
выдать ему ПОЛНОЦЕННУЮ сессию (не read-only дашборд, как у
`parent_access_links` tsk-498) для ручной передачи ссылки любым каналом
(например, в Telegram).

Решения оператора (13.09, tsk-930):
- поверх `magic_link_service`, НЕ прод-аналог `/auth/test/issue-session`;
- TTL=24 ч (не обычные 15 мин письма — они не меняются этим путём);
- выдача по `user_id` напрямую, без требования email-identity;
- выдающая роль — только `admin` (не `methodist`, в отличие от tsk-498);
- обязательный audit-след — кто/кому/когда (когда использовано — смотреть
  `magic_link.consumed_at` по тому же токену, отдельного события нет).

Verify той же ссылки идёт через СУЩЕСТВУЮЩИЙ `/auth/magic-link/verify`
(`app/api/v1/auth/magic_link.py`) — там же ветвление по `MagicLink.user_id`.

Хвост tsk-931 (13.09, живая проверка tsk-930 на реальном клиенте): у части
учеников нет ни ВК, ни почты вообще НИКОГДА — то есть нет и `user_id`, для
которого выдавать ссылку. `POST /admin/students` заводит пустую карточку
(роль `student`, `full_name=NULL` намеренно — см. `admin_student_service`),
дальше в дело идёт уже готовая выдача ссылки выше.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.schemas.auth_admin_login_link import (
    AdminCreateStudentRequest,
    AdminLoginLinkIssuedRead,
    AdminStudentCreatedRead,
)
from app.services import admin_student_service, audit_service
from app.services.auth import magic_link_service
from app.services.audit_service import log_event

router = APIRouter(prefix="/admin/students", tags=["auth-admin"])

_ADMIN_LOGIN_LINK_GATE = require_role("admin")


@router.post(
    "",
    response_model=AdminStudentCreatedRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать ученика вручную (нет ВК, нет почты, никогда не входил)",
    description=(
        "Заводит пустую карточку ученика в обход auto-create при первом входе "
        "— для случая, когда у человека нет вообще никакого способа войти "
        "самому. ФИО не запрашивается: welcome-форма (tsk-223) соберёт его у "
        "самого ученика при первом реальном входе."
    ),
)
async def create_student_manually(
    body: AdminCreateStudentRequest = Body(default=AdminCreateStudentRequest()),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_ADMIN_LOGIN_LINK_GATE),
) -> AdminStudentCreatedRead:
    user = await admin_student_service.create_student_manually(
        db, created_by_user_id=current_user.id, note=body.note,
    )
    return AdminStudentCreatedRead(id=user.id, created_at=user.created_at)


@router.post(
    "/{student_id}/issue-login-link",
    response_model=AdminLoginLinkIssuedRead,
    status_code=status.HTTP_201_CREATED,
    summary="Выдать ученику ссылку входа вручную (нет ВК, не приходит почта)",
    description=(
        "Возвращает готовую ссылку с полноценной сессией ученика — письмо не "
        "отправляется, ссылку передаёт оператор лично. Токен виден в ответе "
        "ЕДИНСТВЕННЫЙ раз (в базе только хеш). TTL 24 часа, одноразовый."
    ),
)
async def issue_admin_login_link(
    student_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_ADMIN_LOGIN_LINK_GATE),
) -> AdminLoginLinkIssuedRead:
    exists = (
        await db.execute(text("SELECT 1 FROM users WHERE id = :id"), {"id": student_id})
    ).first()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")

    link, raw_token = await magic_link_service.create_magic_link_for_user(
        db,
        user_id=student_id,
        issued_by_user_id=current_user.id,
    )

    ip = request.client.host if request.client else None
    await log_event(
        db,
        audit_service.AUTH_ADMIN_LOGIN_LINK_ISSUED,
        user_id=student_id,
        ip=ip,
        details={
            "issued_by_user_id": current_user.id,
            "ttl_hours": magic_link_service.ADMIN_ISSUED_TTL_HOURS,
        },
    )
    await db.commit()

    base = Settings().public_base_url.rstrip("/")
    return AdminLoginLinkIssuedRead(
        student_id=student_id,
        issued_by_user_id=current_user.id,
        url=f"{base}/auth/magic-link/consume?token={raw_token}",
        expires_at=link.expires_at,
    )
