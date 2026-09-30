"""tsk-1171: GET /api/v1/admin/new-users — лента новых регистраций для бота админа.

Лента строится из самой таблицы `users`, а не из отдельной очереди событий:
учётку создают четыре пути (почта, ВК, Telegram, ручное создание админом), и
любой будущий путь попадёт в ленту сам, без правки в каждом месте создания.
Способ регистрации берётся из журнала аудита — его пишет каждый из путей.

Идемпотентность — курсором: клиент передаёт `after_id` (последний id, который
он уже обработал), сервер отвечает новыми учётками и курсором, до которого
можно сдвинуться. Курсор сдвигается и через пропущенные учётки (слитые,
тестовые), иначе они бы возвращались на каждом цикле.

Свежие учётки (моложе `min_age_sec`) в ленту не попадают и курсор не
двигают: в первую минуту человек заполняет ФИО и класс, а тариф и слияние
могут прийти следом. Так уведомление уходит уже с данными.

ACL: роль admin (сервисный ключ — как у остальных ботов).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser

router = APIRouter(prefix="/admin", tags=["admin_new_users"])
logger = logging.getLogger("api.admin_new_users")

#: Тарифы служебных учёток: о них админу сообщать незачем.
SERVICE_PLAN_CODES = ("test",)

#: Событие аудита → способ регистрации (машинный код для клиента).
REGISTRATION_EVENTS = {
    "user.registered.via_magic_link": "email",
    "user.registered.via_vk": "vk",
    "user.registered.via_tg_init": "telegram",
    "admin.student.created_manually": "admin",
}


class NewUserItem(BaseModel):
    """Новая учётка для уведомления админа."""
    id: int
    created_at: datetime
    full_name: Optional[str] = None
    channel: Optional[str] = Field(
        None, description="email | vk | telegram | admin; None — путь не записан в аудите",
    )
    category: Optional[str] = None
    school_grade: Optional[int] = None
    plan_code: Optional[str] = None
    attribution: Optional[dict] = Field(
        None, description="Метки первого касания (utm_*, квиз) из гостевой сессии или лида",
    )


class NewUsersResponse(BaseModel):
    """Ответ ленты: новые учётки и курсор для следующего запроса."""
    items: list[NewUserItem]
    cursor: int = Field(..., description="Передать как after_id в следующем запросе")


_EVENTS_SQL = ", ".join(f"'{e}'" for e in REGISTRATION_EVENTS)  # nosec B608 — константы модуля


@router.get(
    "/new-users",
    response_model=NewUsersResponse,
    status_code=status.HTTP_200_OK,
    summary="Новые регистрации после курсора (tsk-1171)",
)
async def list_new_users(
    after_id: Optional[int] = Query(
        None, ge=0,
        description="Последний обработанный id. Не задан — вернуть только курсор (первый запуск)",
    ),
    min_age_sec: int = Query(120, ge=0, le=3600),
    limit: int = Query(50, ge=1, le=200),
    current_user: CurrentUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_async_db),
) -> NewUsersResponse:
    """Возвращает учётки с id > after_id старше min_age_sec, без слитых и тестовых."""
    age = {"age": int(min_age_sec)}
    if after_id is None:
        # Первый запуск: точка отсчёта без уведомлений — иначе бот
        # вывалил бы админу всю историю регистраций разом.
        row = (await db.execute(
            text(
                "SELECT COALESCE(MAX(id), 0) FROM users "
                "WHERE created_at <= now() - make_interval(secs => :age)"
            ),
            age,
        )).scalar_one()
        return NewUsersResponse(items=[], cursor=int(row))

    rows = (await db.execute(
        text(
            "SELECT u.id, u.created_at, u.full_name, u.category, u.school_grade, "
            "  u.is_active, u.merged_into_user_id, "
            "  (SELECT a.event_type FROM audit_event a WHERE a.user_id = u.id "
            f"     AND a.event_type IN ({_EVENTS_SQL}) ORDER BY a.id LIMIT 1) AS reg_event, "
            "  (SELECT p.code FROM student_subscription s "
            "     JOIN subscription_plan p ON p.id = s.plan_id "
            "     WHERE s.student_id = u.id ORDER BY s.id DESC LIMIT 1) AS plan_code, "
            "  COALESCE( "
            "    (SELECT g.attribution FROM guest_session g "
            "       WHERE g.attributed_user_id = u.id AND g.attribution IS NOT NULL "
            "       ORDER BY g.created_at LIMIT 1), "
            "    (SELECT l.attribution FROM leads l "
            "       WHERE l.linked_student_id = u.id AND l.attribution IS NOT NULL "
            "       ORDER BY l.id LIMIT 1) "
            "  ) AS attribution "
            "FROM users u "
            "WHERE u.id > :after AND u.created_at <= now() - make_interval(secs => :age) "
            "ORDER BY u.id LIMIT :limit"
        ),
        {"after": int(after_id), "age": int(min_age_sec), "limit": int(limit)},
    )).mappings().all()

    cursor = int(after_id)
    items: list[NewUserItem] = []
    for r in rows:
        cursor = max(cursor, int(r["id"]))
        if r["merged_into_user_id"] is not None or not r["is_active"]:
            continue  # слита в другую учётку — это не новый человек
        if r["plan_code"] in SERVICE_PLAN_CODES:
            continue
        items.append(NewUserItem(
            id=int(r["id"]),
            created_at=r["created_at"],
            full_name=r["full_name"],
            channel=REGISTRATION_EVENTS.get(r["reg_event"] or ""),
            category=r["category"],
            school_grade=r["school_grade"],
            plan_code=r["plan_code"],
            attribution=dict(r["attribution"]) if r["attribution"] else None,
        ))
    logger.debug(
        "new-users after=%s → items=%s cursor=%s", after_id, len(items), cursor,
    )
    return NewUsersResponse(items=items, cursor=cursor)
