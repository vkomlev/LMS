# app/services/messages_service.py

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.messages import Messages
from app.repos.messages_repo import MessagesRepository
from app.services.base import BaseService
from app.utils.exceptions import DomainError


class MessagesService(BaseService[Messages]):
    """
    Сервис для сообщений.

    Базовый CRUD уже реализован в BaseService:
    - create, get_by_id, update, delete, paginate и т.п.

    Ниже — доменные операции:
    - отправка per2per,
    - массовая отправка (bulk),
    - ответ на сообщение,
    - пересылка сообщения,
    - выборка сообщений по пользователю/периоду,
    - список отправителей,
    - прикрепление файла.
    """

    def __init__(self, repo: MessagesRepository = MessagesRepository()) -> None:
        super().__init__(repo)

    # ---------- Отправка сообщений ----------

    async def send_message(
        self,
        db: AsyncSession,
        *,
        message_type: str,
        content: Any,
        recipient_id: int,
        sender_id: Optional[int] = None,
        source_system: Optional[str] = None,
        reply_to_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        forwarded_from_id: Optional[int] = None,
        attachment_url: Optional[str] = None,
        attachment_id: Optional[str] = None,
    ) -> Messages:
        """
        Отправить одно сообщение (per2per или системное).

        Логика тредов:
        - если передан reply_to_id — проверяем, что сообщение существует и
          наследуем thread_id от него (или берём его id, если thread_id у него пустой);
        - если reply_to_id не задан и thread_id не передан — после создания
          считаем сообщение корнем треда (thread_id = id).
        """
        # Обработка ответов: подтягиваем исходное сообщение и вычисляем thread_id
        if reply_to_id is not None:
            original = await self.repo.get(db, reply_to_id)
            if original is None:
                raise DomainError(
                    "Исходное сообщение для ответа не найдено",
                    status_code=404,
                    payload={"reply_to_id": reply_to_id},
                )
            if thread_id is None:
                thread_id = original.thread_id or original.id

        data: dict[str, Any] = {
            "message_type": message_type,
            "content": content,
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "source_system": source_system or "system",
            "reply_to_id": reply_to_id,
            "thread_id": thread_id,
            "forwarded_from_id": forwarded_from_id,
            "attachment_url": attachment_url,
            "attachment_id": attachment_id,
        }

        # Удаляем None, чтобы не затирать дефолты БД
        obj_in = {k: v for k, v in data.items() if v is not None}

        # Создаём сообщение через базовый репозиторий
        msg = await self.repo.create(db, obj_in)

        # Если это корневое сообщение треда — thread_id = id
        if msg.reply_to_id is None and msg.thread_id is None:
            msg.thread_id = msg.id
            db.add(msg)
            await db.commit()
            await db.refresh(msg)

        return msg

    async def send_bulk(
        self,
        db: AsyncSession,
        *,
        message_type: str,
        content: Any,
        recipient_ids: Sequence[int],
        sender_id: Optional[int] = None,
        source_system: Optional[str] = None,
        reply_to_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        forwarded_from_id: Optional[int] = None,
        attachment_url: Optional[str] = None,
        attachment_id: Optional[str] = None,
    ) -> List[Messages]:
        """
        Отправить одно и то же сообщение нескольким получателям.
        Возвращает список созданных сообщений.

        Пока без общей транзакции на все N сообщений: каждый send_message
        выполняет свой коммит. Это проще и достаточно надёжно для MVP.
        """
        if not recipient_ids:
            return []

        messages: List[Messages] = []
        for rid in recipient_ids:
            msg = await self.send_message(
                db,
                message_type=message_type,
                content=content,
                recipient_id=rid,
                sender_id=sender_id,
                source_system=source_system,
                reply_to_id=reply_to_id,
                thread_id=thread_id,
                forwarded_from_id=forwarded_from_id,
                attachment_url=attachment_url,
                attachment_id=attachment_id,
            )
            messages.append(msg)
        return messages

    async def reply_to_message(
        self,
        db: AsyncSession,
        *,
        message_id: int,
        sender_id: int,
        message_type: str,
        content: Any,
        source_system: Optional[str] = None,
    ) -> Messages:
        """
        Ответить на существующее сообщение.

        Правила:
        - отправитель ответа должен быть либо sender, либо recipient исходного сообщения;
        - получатель определяется автоматически:
          * если исходное сообщение отправил sender_id → отвечаем получателю;
          * если исходное сообщение получил sender_id → отвечаем отправителю
            (если он не системный / не NULL);
        - thread_id наследуется от исходного сообщения (или его id).
        """
        original = await self.repo.get(db, message_id)
        if original is None:
            raise DomainError(
                "Сообщение для ответа не найдено",
                status_code=404,
                payload={"message_id": message_id},
            )

        # Определяем, кому отвечаем
        if original.sender_id == sender_id:
            recipient_id = original.recipient_id
        elif original.recipient_id == sender_id:
            if original.sender_id is None:
                raise DomainError(
                    "Нельзя ответить на системное сообщение без отправителя",
                    status_code=400,
                    payload={"message_id": message_id},
                )
            recipient_id = original.sender_id
        else:
            raise DomainError(
                "Пользователь не является участником диалога и не может ответить",
                status_code=403,
                payload={"message_id": message_id, "sender_id": sender_id},
            )

        thread_id = original.thread_id or original.id

        return await self.send_message(
            db,
            message_type=message_type,
            content=content,
            recipient_id=recipient_id,
            sender_id=sender_id,
            source_system=source_system or original.source_system,
            reply_to_id=original.id,
            thread_id=thread_id,
        )

    async def forward_message(
        self,
        db: AsyncSession,
        *,
        message_id: int,
        sender_id: int,
        recipient_ids: Sequence[int],
        message_type: Optional[str] = None,
        source_system: Optional[str] = None,
    ) -> List[Messages]:
        """
        Переслать сообщение одному или нескольким получателям.

        В текущей реализации:
        - пересылается тот же content, что у исходного сообщения;
        - message_type по умолчанию наследуется от исходного сообщения,
          но может быть переопределён;
        - forwarded_from_id = id исходного сообщения;
        - thread_id наследуется от исходного сообщения (или его id).
        """
        original = await self.repo.get(db, message_id)
        if original is None:
            raise DomainError(
                "Сообщение для пересылки не найдено",
                status_code=404,
                payload={"message_id": message_id},
            )

        if not recipient_ids:
            return []

        thread_id = original.thread_id or original.id
        final_type = message_type or original.message_type
        final_source = source_system or original.source_system

        messages: List[Messages] = []
        for rid in recipient_ids:
            msg = await self.send_message(
                db,
                message_type=final_type,
                content=original.content,
                recipient_id=rid,
                sender_id=sender_id,
                source_system=final_source,
                reply_to_id=None,
                thread_id=thread_id,
                forwarded_from_id=original.id,
            )
            messages.append(msg)

        return messages

    # ---------- Выборка сообщений ----------

    async def get_messages_for_user(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        direction: str = "both",
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Messages], int]:
        """
        Получить сообщения пользователя с фильтрами по направлению и периоду.

        direction:
            - "sent"     — только отправленные пользователем;
            - "received" — только полученные пользователем;
            - "both"     — и отправленные, и полученные.
        """
        model = self.repo.model  # Messages
        filters = []

        # Направление
        if direction == "sent":
            filters.append(model.sender_id == user_id)
        elif direction == "received":
            filters.append(model.recipient_id == user_id)
        else:  # both
            filters.append(
                or_(
                    model.sender_id == user_id,
                    model.recipient_id == user_id,
                )
            )

        # Период
        if from_dt is not None:
            filters.append(model.sent_at >= from_dt)
        if to_dt is not None:
            filters.append(model.sent_at <= to_dt)

        return await self.paginate(
            db,
            limit=limit,
            offset=offset,
            filters=filters,
            order_by=[model.sent_at.desc()],
        )

    async def get_senders_for_user(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
    ) -> List[Tuple[int, int]]:
        """
        Получить список отправителей пользователя и количество сообщений от каждого.

        Возвращает список кортежей (sender_id, messages_count), отсортированный
        по убыванию количества сообщений. Сообщения от системного отправителя
        (sender_id IS NULL) не включаются.
        """
        model = self.repo.model  # Messages

        stmt = select(
            model.sender_id,
            func.count().label("messages_count"),
        ).where(
            model.recipient_id == user_id,
        )

        if from_dt is not None:
            stmt = stmt.where(model.sent_at >= from_dt)
        if to_dt is not None:
            stmt = stmt.where(model.sent_at <= to_dt)

        stmt = (
            stmt.where(model.sender_id.isnot(None))
            .group_by(model.sender_id)
            .order_by(func.count().desc())
        )

        result = await db.execute(stmt)
        rows = result.all()

        # Преобразуем в список (sender_id, count)
        return [(int(sender_id), int(messages_count)) for sender_id, messages_count in rows]

    # ---------- Прикрепление файла ----------

    async def attach_file(
        self,
        db: AsyncSession,
        *,
        message_id: int,
        attachment_url: str,
        attachment_id: Optional[str] = None,
    ) -> Messages:
        """
        Обновить сообщение, прикрепив к нему файл.

        attachment_url — путь или URL до файла (на диске/в хранилище),
        attachment_id  — идентификатор файла во внешней системе (если есть).
        """
        msg = await self.repo.get(db, message_id)
        if msg is None:
            raise DomainError(
                "Сообщение не найдено",
                status_code=404,
                payload={"message_id": message_id},
            )

        msg.attachment_url = attachment_url
        msg.attachment_id = attachment_id

        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        return msg
