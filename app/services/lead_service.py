"""Лиды кабинета маркетолога (tsk-506)."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import LEAD_SOURCE_OTHER
from app.schemas.lead import LeadRead, LeadSourceRead, StudentBrief

#: Колонки, которые правка лида имеет право трогать. Белый список, а не
#: «что пришло, то и пишем»: имена полей доезжают до SET-части запроса, и
#: единственное, что сегодня не пускает туда произвольный ключ, — умолчание
#: pydantic `extra="ignore"`. Одна строка `ConfigDict(extra="allow")` в схеме
#: превратила бы это в живую инъекцию без единого признака в диффе.
_LEAD_PATCH_COLUMNS = frozenset(
    {"source_id", "source_detail", "full_name", "contact", "note"}
)

__all__ = [
    "list_sources",
    "get_source_code",
    "is_linkable_student",
    "list_leads",
    "get_lead",
    "create_lead",
    "update_lead",
    "delete_lead",
    "link_student",
    "unlink_student",
    "search_students",
]

_LEAD_SELECT = """
    SELECT l.id,
           l.source_id,
           s.code            AS source_code,
           s.name            AS source_name,
           l.source_detail,
           l.full_name,
           l.contact,
           l.note,
           l.linked_student_id,
           u.full_name       AS linked_student_name,
           l.created_at,
           l.updated_at
      FROM leads l
      JOIN lead_source s ON s.id = l.source_id
      LEFT JOIN users u ON u.id = l.linked_student_id
"""


async def list_sources(db: AsyncSession) -> list[LeadSourceRead]:
    rows = (
        await db.execute(
            text(
                "SELECT id, code, name, sort_order FROM lead_source "
                "WHERE is_active ORDER BY sort_order, name"
            )
        )
    ).all()
    return [LeadSourceRead.model_validate(r) for r in rows]


async def get_source_code(db: AsyncSession, source_id: int) -> Optional[str]:
    return (
        await db.execute(
            text("SELECT code FROM lead_source WHERE id = :id AND is_active"),
            {"id": source_id},
        )
    ).scalar()


def requires_detail(source_code: Optional[str]) -> bool:
    """Канал «другое» без приписки теряет источник — она обязательна."""
    return source_code == LEAD_SOURCE_OTHER


async def list_leads(db: AsyncSession, *, linked: Optional[bool] = None) -> list[LeadRead]:
    sql = _LEAD_SELECT
    if linked is True:
        sql += " WHERE l.linked_student_id IS NOT NULL"
    elif linked is False:
        sql += " WHERE l.linked_student_id IS NULL"
    sql += " ORDER BY l.created_at DESC, l.id DESC"

    rows = (await db.execute(text(sql))).all()
    return [LeadRead.model_validate(r, from_attributes=True) for r in rows]


async def get_lead(db: AsyncSession, lead_id: int) -> Optional[LeadRead]:
    row = (
        await db.execute(text(_LEAD_SELECT + " WHERE l.id = :id"), {"id": lead_id})
    ).first()
    return LeadRead.model_validate(row, from_attributes=True) if row is not None else None


async def create_lead(
    db: AsyncSession,
    *,
    source_id: int,
    source_detail: Optional[str],
    full_name: Optional[str],
    contact: str,
    note: Optional[str],
    created_by: Optional[int],
) -> int:
    lead_id = (
        await db.execute(
            text(
                "INSERT INTO leads "
                "(source_id, source_detail, full_name, contact, note, created_by) "
                "VALUES (:source_id, :source_detail, :full_name, :contact, :note, :created_by) "
                "RETURNING id"
            ),
            {
                "source_id": source_id,
                "source_detail": source_detail,
                "full_name": full_name,
                "contact": contact,
                "note": note,
                "created_by": created_by,
            },
        )
    ).scalar_one()
    await db.commit()
    return int(lead_id)


async def update_lead(db: AsyncSession, *, lead_id: int, patch: dict) -> bool:
    """Правка лида.

    `patch` — результат `model_dump(exclude_unset=True)`: в SET попадает всё, что
    прислали, включая `None`. Фильтровать `None` нельзя — иначе примечание и
    приписку к каналу невозможно СТЕРЕТЬ: сервер отвечал бы 200 со старым
    текстом, а поле в интерфейсе само возвращало бы прежнее значение.
    """
    unknown = set(patch) - _LEAD_PATCH_COLUMNS
    if unknown:
        raise ValueError(f"Недопустимые поля правки лида: {sorted(unknown)}")

    fields = dict(patch)
    if not fields:
        return await _lead_exists(db, lead_id)
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = lead_id
    res = await db.execute(
        text(f"UPDATE leads SET {sets}, updated_at = now() WHERE id = :id"), fields
    )
    await db.commit()
    return res.rowcount > 0


async def delete_lead(db: AsyncSession, *, lead_id: int) -> bool:
    res = await db.execute(text("DELETE FROM leads WHERE id = :id"), {"id": lead_id})
    await db.commit()
    return res.rowcount > 0


async def is_linkable_student(db: AsyncSession, student_id: int) -> bool:
    """Годится ли учётка для привязки лида.

    Проверка не косметическая, а гейт персональных данных. Без неё привязка
    принимала любой `users.id`, проходящий по внешнему ключу, а карточка лида
    затем показывала `linked_student_name` — то есть перебором номеров маркетолог
    читал бы ФИО кого угодно в школе (преподавателей, родителей, администраторов,
    заблокированных и слитых учёток). Ровно тот доступ, ради закрытия которого
    общий `/users/search` ему не открывали.
    """
    row = (
        await db.execute(
            text(
                "SELECT 1 FROM users u "
                "JOIN user_roles ur ON ur.user_id = u.id "
                "JOIN roles r ON r.id = ur.role_id AND r.name = 'student' "
                "WHERE u.id = :id AND u.is_active AND u.blocked_at IS NULL"
            ),
            {"id": student_id},
        )
    ).first()
    return row is not None


async def link_student(db: AsyncSession, *, lead_id: int, student_id: int) -> bool:
    """Привязать лида к учётке ученика. Идемпотентно: повторная привязка — не ошибка.

    Годность учётки проверяет вызывающий через `is_linkable_student`.
    """
    res = await db.execute(
        text(
            "UPDATE leads SET linked_student_id = :student_id, updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": lead_id, "student_id": student_id},
    )
    await db.commit()
    return res.rowcount > 0


async def unlink_student(db: AsyncSession, *, lead_id: int) -> bool:
    res = await db.execute(
        text("UPDATE leads SET linked_student_id = NULL, updated_at = now() WHERE id = :id"),
        {"id": lead_id},
    )
    await db.commit()
    return res.rowcount > 0


async def search_students(db: AsyncSession, *, q: str, limit: int = 20) -> list[StudentBrief]:
    """Узкий поиск учеников для привязки лида.

    Не переиспользует `GET /users/search`: тот отдаёт полную карточку с почтой и
    ролями под гейтом methodist/admin, и расширять его на маркетолога значило бы
    отдать ему персональные данные всех людей школы. Здесь — только id и имя.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT u.id, u.full_name
                  FROM users u
                  JOIN user_roles ur ON ur.user_id = u.id
                  JOIN roles r ON r.id = ur.role_id AND r.name = 'student'
                 WHERE u.is_active
                   AND u.full_name ILIKE :pattern
                 ORDER BY u.full_name
                 LIMIT :limit
                """
            ),
            {"pattern": f"%{_escape_like(q)}%", "limit": limit},
        )
    ).all()
    return [StudentBrief(id=r.id, full_name=r.full_name) for r in rows]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _lead_exists(db: AsyncSession, lead_id: int) -> bool:
    row = (
        await db.execute(text("SELECT 1 FROM leads WHERE id = :id"), {"id": lead_id})
    ).first()
    return row is not None
