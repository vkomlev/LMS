# app/api/v1/messages_extra.py

from __future__ import annotations

from datetime import datetime
from uuid import uuid4
from typing import Any, List, Optional
import os
import mimetypes
from starlette.responses import FileResponse

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    UploadFile,
    status,
    HTTPException,
    Query,
    #Path,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.messages import (
    MessageRead, 
    MessageCreate,
    MarkReadRequest,
    MarkReadBySenderRequest,
    MarkReadResponse,
    )
from app.services.messages_service import MessagesService
from app.services.student_teacher_links_service import (
    StudentTeacherLinksService,
)
from app.utils.pagination import Page, build_page
from app.schemas.messages import InboxResponse, InboxItem, MessageRead
from app.core.config import Settings

router = APIRouter(tags=["messages"])
service = MessagesService()
student_teacher_service = StudentTeacherLinksService()

settings = Settings()


# ------------ Базовые запросы (send / reply / forward) ------------

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


# ------------ Массовые рассылки ------------

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

    # Дополнительно поддерживаем вложения/цепочки:
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


# ------------ НОВОЕ: выборка сообщений по периоду/направлению ------------

@router.get(
    "/messages/by-user",
    response_model=Page[MessageRead],
    summary="Сообщения пользователя с фильтрами по направлению и периоду",
)
async def get_messages_by_user_endpoint(
    user_id: int,
    direction: str = "both",  # sent | received | both
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    is_read: Optional[bool] = None,
    unread_only: bool = False,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> Page[MessageRead]:
    """
    Получить сообщения пользователя с возможностью указать направление и период.

    direction:
        - sent     — только отправленные пользователем;
        - received — только полученные;
        - both     — по умолчанию, всё.
    is_read / unread_only применяются ТОЛЬКО к входящим сообщениям (recipient_id == user_id). 
    При direction=both исходящие сообщения не фильтруются по is_read.
    """
    items, total = await service.get_messages_for_user(
        db,
        user_id=user_id,
        direction=direction,
        from_dt=from_dt,
        to_dt=to_dt,
        is_read=is_read,
        unread_only=unread_only,
        limit=limit,
        offset=skip,
    )
    return build_page(items, total=total, limit=limit, offset=skip)


# ------------ НОВОЕ: список отправителей ------------

class SenderStats(BaseModel):
    sender_id: int
    messages_count: int


@router.get(
    "/messages/senders",
    response_model=List[SenderStats],
    summary="Список отправителей пользователя за период",
)
async def get_senders_for_user_endpoint(
    user_id: int,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
) -> List[SenderStats]:
    """
    Получить список отправителей пользователя и количество сообщений от каждого.

    Системные сообщения (sender_id = NULL) не попадают в список.
    """
    raw = await service.get_senders_for_user(
        db,
        user_id=user_id,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    return [SenderStats(sender_id=sid, messages_count=cnt) for sid, cnt in raw]


# ------------ НОВОЕ: прикрепление файла к сообщению ------------


@router.post(
    "/messages/{message_id}/attachment",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Прикрепить файл к сообщению",
)
async def attach_file_to_message_endpoint(
    message_id: int,
    file: UploadFile = File(..., description="Файл для прикрепления к сообщению"),
    db: AsyncSession = Depends(get_db),
) -> MessageRead:
    """
    Загружает файл и привязывает к сообщению.

    - Лимит размера: Settings.max_attachment_size_bytes
    - Файл пишем в Settings.messages_upload_dir
    - attachment_url сохраняем как ОТНОСИТЕЛЬНЫЙ путь до download endpoint:
      /api/v1/messages/{message_id}/attachment
    """
    # Папка гарантированно есть (Settings её создаёт), но на всякий случай:
    settings.messages_upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = f"{message_id}_{uuid4().hex}_{file.filename}"
    file_path = settings.messages_upload_dir / safe_name

    total = 0
    try:
        with open(file_path, "wb") as f:
            while True:
                chunk = await file.read(settings.attachment_chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.max_attachment_size_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Attachment too large. Max {settings.max_attachment_size_bytes} bytes",
                    )
                f.write(chunk)
    except HTTPException:
        # удаляем частично записанный файл
        try:
            if file_path.exists():
                file_path.unlink()
        except Exception:
            pass
        raise

    # ✅ ВАЖНО: сохраняем относительный URL (без api_key)
    attachment_url = f"/api/v1/messages/{message_id}/attachment"
    attachment_id = safe_name

    msg = await service.attach_file(
        db,
        message_id=message_id,
        attachment_url=attachment_url,
        attachment_id=attachment_id,
    )
    return msg

@router.get(
    "/messages/{message_id}/attachment",
    summary="Скачать вложение сообщения",
)
async def download_message_attachment(
    message_id: int,
    user_id: int = Query(..., description="Кто скачивает (для проверки прав)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Стриминговая отдача вложения.
    Доступ: только sender_id или recipient_id сообщения.
    """
    msg = await service.get_by_id(db, message_id)  # BaseService method
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")

    if not msg.attachment_id:
        raise HTTPException(status_code=404, detail="Attachment not found")

    # ✅ Проверка прав
    if user_id not in {msg.sender_id, msg.recipient_id}:
        raise HTTPException(status_code=403, detail="No access to this attachment")

    file_path = settings.messages_upload_dir / msg.attachment_id
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Attachment file missing on server")

    media_type = mimetypes.guess_type(msg.attachment_id)[0] or "application/octet-stream"

    # filename: можно отдать исходное имя, но у нас оно в конце safe_name (с префиксом)
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=os.path.basename(msg.attachment_id),
    )

class UnreadCountResponse(BaseModel):
    user_id: int
    unread_count: int

@router.get(
    "/messages/unread/count",
    response_model=UnreadCountResponse,
    summary="Количество непрочитанных сообщений у пользователя",
)
async def get_unread_count_endpoint(
    user_id: int,
    db: AsyncSession = Depends(get_db),
) -> UnreadCountResponse:
    unread = await service.count_unread(db, user_id=user_id)
    return UnreadCountResponse(user_id=user_id, unread_count=unread)


class UnreadBySenderItem(BaseModel):
    sender_id: int
    unread_count: int

@router.get(
    "/messages/unread/by-sender",
    response_model=List[UnreadBySenderItem],
    summary="Непрочитанные сообщения по отправителям",
)
async def get_unread_by_sender_endpoint(
    user_id: int,
    db: AsyncSession = Depends(get_db),
) -> List[UnreadBySenderItem]:
    rows = await service.count_unread_by_sender(db, user_id=user_id)
    return [UnreadBySenderItem(sender_id=sid, unread_count=cnt) for sid, cnt in rows]

@router.post(
    "/messages/mark-read",
    response_model=MarkReadResponse,
    summary="Массово отметить сообщения как прочитанные",
)
async def mark_read_endpoint(
    payload: MarkReadRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> MarkReadResponse:
    updated = await service.mark_read(
        db,
        user_id=payload.user_id,
        message_ids=payload.message_ids,
    )
    return MarkReadResponse(updated_count=updated)

@router.post(
    "/messages/mark-read/by-sender",
    response_model=MarkReadResponse,
    summary="Отметить как прочитанные все сообщения от отправителя",
)
async def mark_read_by_sender_endpoint(
    payload: MarkReadBySenderRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> MarkReadResponse:
    updated = await service.mark_read_by_sender(
        db,
        user_id=payload.user_id,
        sender_id=payload.sender_id,
    )
    return MarkReadResponse(updated_count=updated)

@router.get(
    "/messages/inbox",
    response_model=InboxResponse,
    summary="Список диалогов (peer + last_message + unread_count)",
)
async def get_inbox(
    user_id: int,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> InboxResponse:
    rows = await service.get_inbox(db, user_id=user_id, limit=limit, offset=offset)

    items = [
        InboxItem(
            peer_id=row["peer_id"],
            peer_full_name=row["peer_full_name"],
            unread_count=row["unread_count"],
            last_message=MessageRead.model_validate(row["last_message"], from_attributes=True),
        )
        for row in rows
    ]
    return InboxResponse(items=items)

@router.post(
    "/messages/{message_id}/read",
    summary="Отметить одно сообщение прочитанным (явно)",
)
async def mark_one_as_read(
    message_id: int,  # ← БЕЗ Path(...)
    user_id: int = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    updated = await service.mark_read(
        db,
        user_id=user_id,
        message_ids=[message_id],
    )
    return {"updated": updated}