# app/api/v1/messages_extra.py

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Body, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.messages import MessageRead, MessageCreate
from app.services.messages_service import MessagesService
from app.services.student_teacher_links_service import (
    StudentTeacherLinksService,
)

router = APIRouter(tags=["messages"])
service = MessagesService()
student_teacher_service = StudentTeacherLinksService()


# ------------ Базовые запросы, которые уже были ------------

class MessageReplyRequest(BaseModel):
    """
    Запрос на ответ на сообщение.
    sender_id передаём явно (автор ответа), пока без auth-слоя.
    """
    sender_id: int
    message_type: str
    content: Any
    source_system: Optional[str] = None


class MessageForwardRequest(BaseModel):
    """
    Запрос на пересылку сообщения.
    sender_id — кто инициирует пересылку.
    recipient_ids — список получателей.
    """
    sender_id: int
    recipient_ids: List[int]
    message_type: Optional[str] = None
    source_system: Optional[str] = None


@router.post(
    "/messages/send",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Отправить per2per или системное сообщение",
)
async def send_message_endpoint(
    payload: MessageCreate = Body(..., description="Данные сообщения для отправки"),
    db: AsyncSession = Depends(get_db),
) -> MessageRead:
    """
    Высокоуровневый эндпойнт отправки сообщения.

    Это обёртка над MessagesService.send_message и замена голого CRUD в случаях,
    когда нужна логика тредов и reply/forward.
    """
    msg = await service.send_message(
        db,
        message_type=payload.message_type,
        content=payload.content,
        recipient_id=payload.recipient_id,
        sender_id=payload.sender_id,
        source_system=payload.source_system,
        reply_to_id=payload.reply_to_id,
        thread_id=payload.thread_id,
        forwarded_from_id=payload.forwarded_from_id,
        attachment_url=payload.attachment_url,
        attachment_id=payload.attachment_id,
    )
    return msg


@router.post(
    "/messages/{message_id}/reply",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Ответить на сообщение",
)
async def reply_to_message_endpoint(
    message_id: int,
    payload: MessageReplyRequest = Body(..., description="Данные ответа"),
    db: AsyncSession = Depends(get_db),
) -> MessageRead:
    """
    Ответить на существующее сообщение.

    Получатель определяется автоматически по исходному сообщению.
    """
    msg = await service.reply_to_message(
        db,
        message_id=message_id,
        sender_id=payload.sender_id,
        message_type=payload.message_type,
        content=payload.content,
        source_system=payload.source_system,
    )
    return msg


@router.post(
    "/messages/{message_id}/forward",
    response_model=List[MessageRead],
    status_code=status.HTTP_201_CREATED,
    summary="Переслать сообщение одному или нескольким пользователям",
)
async def forward_message_endpoint(
    message_id: int,
    payload: MessageForwardRequest = Body(..., description="Параметры пересылки"),
    db: AsyncSession = Depends(get_db),
) -> List[MessageRead]:
    """
    Переслать сообщение одному или нескольким получателям.

    На этом уровне:
    - content пересылается как есть;
    - тип сообщения по умолчанию наследуется от исходного.
    """
    messages = await service.forward_message(
        db,
        message_id=message_id,
        sender_id=payload.sender_id,
        recipient_ids=payload.recipient_ids,
        message_type=payload.message_type,
        source_system=payload.source_system,
    )
    return messages


# ------------ НОВОЕ: массовые рассылки ------------

class MessageToGroupBase(BaseModel):
    """
    Общая часть запроса для рассылок:
    - тип сообщения,
    - контент,
    - отправитель (может быть None для системных сообщений),
    - источник.
    При необходимости можно будет добавить сюда reply/forward/attachments.
    """
    message_type: str
    content: Any
    sender_id: Optional[int] = None
    source_system: Optional[str] = None

    # При желании можно сразу поддержать вложения/цепочки:
    reply_to_id: Optional[int] = None
    thread_id: Optional[int] = None
    forwarded_from_id: Optional[int] = None
    attachment_url: Optional[str] = None
    attachment_id: Optional[str] = None


class MessageBulkRequest(MessageToGroupBase):
    """
    Запрос на массовую отправку по произвольному списку получателей.
    """
    recipient_ids: List[int]


@router.post(
    "/messages/send/bulk",
    response_model=List[MessageRead],
    status_code=status.HTTP_201_CREATED,
    summary="Массовая отправка сообщения нескольким получателям",
)
async def send_bulk_messages_endpoint(
    payload: MessageBulkRequest = Body(..., description="Параметры массовой отправки"),
    db: AsyncSession = Depends(get_db),
) -> List[MessageRead]:
    """
    Отправить одно и то же сообщение сразу нескольким пользователям.

    Если список получателей пуст, вернётся пустой список без ошибок.
    """
    messages = await service.send_bulk(
        db,
        message_type=payload.message_type,
        content=payload.content,
        recipient_ids=payload.recipient_ids,
        sender_id=payload.sender_id,
        source_system=payload.source_system,
        reply_to_id=payload.reply_to_id,
        thread_id=payload.thread_id,
        forwarded_from_id=payload.forwarded_from_id,
        attachment_url=payload.attachment_url,
        attachment_id=payload.attachment_id,
    )
    return messages


@router.post(
    "/messages/send/to-students/{teacher_id}",
    response_model=List[MessageRead],
    status_code=status.HTTP_201_CREATED,
    summary="Отправить сообщение всем студентам преподавателя",
)
async def send_to_students_endpoint(
    teacher_id: int,
    payload: MessageToGroupBase = Body(..., description="Текст и параметры сообщения"),
    db: AsyncSession = Depends(get_db),
) -> List[MessageRead]:
    """
    Отправить сообщение всем студентам, привязанным к преподавателю `teacher_id`.

    Получатели берутся из связей student_teacher_links.
    Если у преподавателя нет студентов, вернётся пустой список.
    """
    students = await student_teacher_service.list_students(db, teacher_id)
    recipient_ids = [u.id for u in students]

    messages = await service.send_bulk(
        db,
        message_type=payload.message_type,
        content=payload.content,
        recipient_ids=recipient_ids,
        sender_id=payload.sender_id,
        source_system=payload.source_system,
        reply_to_id=payload.reply_to_id,
        thread_id=payload.thread_id,
        forwarded_from_id=payload.forwarded_from_id,
        attachment_url=payload.attachment_url,
        attachment_id=payload.attachment_id,
    )
    return messages


@router.post(
    "/messages/send/to-teachers/{student_id}",
    response_model=List[MessageRead],
    status_code=status.HTTP_201_CREATED,
    summary="Отправить сообщение всем преподавателям студента",
)
async def send_to_teachers_endpoint(
    student_id: int,
    payload: MessageToGroupBase = Body(..., description="Текст и параметры сообщения"),
    db: AsyncSession = Depends(get_db),
) -> List[MessageRead]:
    """
    Отправить сообщение всем преподавателям, привязанным к студенту `student_id`.

    Получатели берутся из связей student_teacher_links.
    Если у студента нет преподавателей, вернётся пустой список.
    """
    teachers = await student_teacher_service.list_teachers(db, student_id)
    recipient_ids = [u.id for u in teachers]

    messages = await service.send_bulk(
        db,
        message_type=payload.message_type,
        content=payload.content,
        recipient_ids=recipient_ids,
        sender_id=payload.sender_id,
        source_system=payload.source_system,
        reply_to_id=payload.reply_to_id,
        thread_id=payload.thread_id,
        forwarded_from_id=payload.forwarded_from_id,
        attachment_url=payload.attachment_url,
        attachment_id=payload.attachment_id,
    )
    return messages
