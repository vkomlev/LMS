# app/repos/messages_repo.py

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select, case, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.messages import Messages
from app.models.users import Users
from app.repos.base import BaseRepository


class MessagesRepository(BaseRepository[Messages]):
    """
    Репозиторий для сообщений.
    """
    model = Messages

    def __init__(self) -> None:
        super().__init__(Messages)


    async def get_inbox(
        self,
        db: AsyncSession,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Возвращает список диалогов (peer + last_message + unread_count) для user_id.
        """

        peer_id = case(
            (Messages.sender_id == user_id, Messages.recipient_id),
            else_=Messages.sender_id,
        ).label("peer_id")

        peer_user = aliased(Users)

        # unread_count: сколько непрочитанных сообщений от peer -> user
        unread_count_sq = (
            select(func.count(Messages.id))
            .where(
                and_(
                    Messages.recipient_id == user_id,
                    Messages.sender_id == peer_id,
                    Messages.is_read.is_(False),
                )
            )
            .correlate(Messages)
            .scalar_subquery()
        ).label("unread_count")

        # subquery: берем последнее сообщение на peer (distinct on peer_id)
        last_msg_stmt = (
            select(
                Messages,
                peer_id,
                unread_count_sq,
                peer_user.full_name.label("peer_full_name"),
            )
            .join(peer_user, peer_user.id == peer_id)
            .where(
                and_(
                    # диалоговые сообщения, где user участвует
                    (Messages.sender_id == user_id) | (Messages.recipient_id == user_id),
                    # исключим "диалоги" без собеседника (sender_id null)
                    peer_id.is_not(None),
                )
            )
            .order_by(peer_id, Messages.sent_at.desc(), Messages.id.desc())
            .distinct(peer_id)
            .limit(limit)
            .offset(offset)
        )

        rows = (await db.execute(last_msg_stmt)).all()

        # rows: [(Messages, peer_id, unread_count, peer_full_name), ...]
        return [
            {
                "peer_id": r.peer_id,
                "peer_full_name": r.peer_full_name,
                "unread_count": int(r.unread_count or 0),
                "last_message": r.Messages,
            }
            for r in rows
        ]