"""
Ссылки доступа родителя к дашборду ученика без регистрации (tsk-498).

Оператор выдаёт ссылку лично (мессенджер, СМС, голосом) — родитель открывает
её и сразу видит дашборд ребёнка, без почты, писем и паролей. Это ВТОРОЙ путь
к тому же экрану: вход по magic-link с ролью `parent` (tsk-478) остаётся.

Границы безопасности (осознанный размен, решение оператора 2026-08-01):
- Токен = пропуск: кто открыл ссылку, тот видит дашборд. Дополнительных
  проверок при открытии нет — цена за отсутствие регистрации.
- Смягчено конструкцией: токен 32 случайных байта (подбор невозможен), даёт
  доступ РОВНО к одному read-only эндпоинту дашборда конкретного ученика и
  НЕ является сессией — под учёткой в LMS по нему войти нельзя.
- В ответе дашборда по контракту tsk-494 нет ни `solution_rules`, ни текста
  переписки заявок помощи — только агрегаты.
- В БД хранится sha256-хеш, сырой токен возвращается один раз при создании
  (тот же приём, что `magic_link`/`user_session`).

Срока годности нет: ссылка живёт, пока её не отозвали вручную. У одного
ученика может быть несколько активных ссылок (маме и папе отдельно) — они
отзываются независимо.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.parent_access_link import ParentAccessLink

_TOKEN_BYTES = 32


def _hash_token(raw: bytes) -> bytes:
    return hashlib.sha256(raw).digest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_link(
    db: AsyncSession,
    *,
    student_id: int,
    label: Optional[str],
    created_by_user_id: Optional[int],
) -> tuple[ParentAccessLink, str]:
    """Создать ссылку; вернуть (строка БД, СЫРОЙ токен).

    Сырой токен показывается вызывающему один раз — в базе только хеш,
    восстановить его позже нельзя (можно только выпустить новый).
    """
    raw = os.urandom(_TOKEN_BYTES)
    link = ParentAccessLink(
        token_hash=_hash_token(raw),
        student_id=student_id,
        label=label,
        created_by_user_id=created_by_user_id,
    )
    db.add(link)
    await db.flush()
    await db.commit()
    await db.refresh(link)
    return link, raw.hex()


async def list_links(db: AsyncSession, *, student_id: int) -> list[ParentAccessLink]:
    """Все ссылки ученика, включая отозванные — оператор видит историю выдач."""
    rows = (
        await db.execute(
            select(ParentAccessLink)
            .where(ParentAccessLink.student_id == student_id)
            .order_by(ParentAccessLink.created_at.desc())
        )
    ).scalars().all()
    return list(rows)


async def revoke_link(db: AsyncSession, *, link_id: int) -> Optional[ParentAccessLink]:
    """Погасить ссылку. Повторный отзыв — не ошибка (идемпотентно, время
    первого отзыва сохраняется). ``None``, если ссылки с таким id нет."""
    link = await db.get(ParentAccessLink, link_id)
    if link is None:
        return None
    if link.revoked_at is None:
        link.revoked_at = _now()
        await db.commit()
        await db.refresh(link)
    return link


async def resolve_token(db: AsyncSession, raw_token: str) -> Optional[ParentAccessLink]:
    """Действующая ссылка по сырому токену, иначе ``None``.

    Отозванная ссылка возвращает ``None`` наравне с несуществующей: вызывающий
    отвечает 404 в обоих случаях и не подтверждает, что токен когда-либо был.
    Кривой hex (не токен вовсе) тоже даёт ``None``, а не 500.
    """
    try:
        raw = bytes.fromhex(raw_token)
    except ValueError:
        return None

    link = (
        await db.execute(
            select(ParentAccessLink).where(
                ParentAccessLink.token_hash == _hash_token(raw),
                ParentAccessLink.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    return link


async def touch_last_used(db: AsyncSession, link: ParentAccessLink) -> None:
    """Отметить факт использования ссылки — оператор видит, дошла ли она.

    Soft-fail: диагностическая отметка не должна ронять выдачу дашборда, ради
    которой родитель и открыл ссылку.
    """
    try:
        link.last_used_at = _now()
        await db.commit()
    except Exception:  # noqa: BLE001 — намеренно широкий: отметка не критична
        await db.rollback()


__all__ = [
    "create_link",
    "list_links",
    "revoke_link",
    "resolve_token",
    "touch_last_used",
]
