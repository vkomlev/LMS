"""Черновики текстовых подсказок и их вычитка (tsk-1220).

Путь черновика:
1. `GET /tasks/hint-drafts/candidates` — задания с новыми ответами преподавателей;
2. `POST /tasks/{task_id}/hint-drafts/generate` — черновик моделью, в базу не пишет;
3. линтер ContentBackbone (`hint-leaks-answer`) — на стороне пакетного скрипта;
4. `POST /tasks/{task_id}/hint-drafts` — запись в очередь (или пропуск/блок);
5. `GET /tasks/hint-drafts/queue` + `POST /tasks/hint-drafts/{draft_id}/review` —
   вычитка человеком; подтверждённое дописывается в `hints_text`.

Роль — methodist/admin: в карточке очереди лежит эталонный ответ и переписка
преподавателя с учеником. Подтвердить и отклонить может только человек,
сервисный ключ — нет (как в tsk-590).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.services import hint_drafts_service as svc

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tasks"])

_METHODIST_GATE = require_role("methodist", "admin")


class HintCandidate(BaseModel):
    """Задание с ответами преподавателей, ещё не вошедшими в черновики."""

    task_id: int
    new_reply_ids: List[int]
    replies_total: int


class HintCandidatesResponse(BaseModel):
    """Кандидаты на генерацию."""

    items: List[HintCandidate]


class GeneratedHintResponse(BaseModel):
    """Черновик модели до записи — для линтера и решения скрипта."""

    task_id: int
    source_reply_ids: List[int]
    skip: bool
    text: Optional[str] = None
    reason: Optional[str] = None
    model: str
    lint_flags: List[str] = Field(default_factory=list)
    accepted_answers: List[str] = Field(default_factory=list)


class HintDraftStoreRequest(BaseModel):
    """Итог генерации по заданию."""

    status: Literal["draft", "skipped", "blocked"]
    text: Optional[str] = None
    source_reply_ids: List[int] = Field(..., min_length=1)
    model: Optional[str] = None
    note: Optional[str] = Field(default=None, description="Причина пропуска или блокировки.")
    lint_flags: List[str] = Field(default_factory=list)


class HintDraftView(BaseModel):
    """Строка черновика."""

    id: int
    task_id: int
    status: str
    text: Optional[str] = None
    model: Optional[str] = None
    note: Optional[str] = None
    lint_flags: List[str] = Field(default_factory=list)
    source_reply_ids: List[int] = Field(default_factory=list)
    reviewed_by: Optional[int] = None
    reviewed_at: Optional[datetime] = None


class HintSource(BaseModel):
    """Исходный ответ преподавателя."""

    reply_id: int
    question: Optional[str] = None
    body: str


class HintQueueItem(BaseModel):
    """Карточка вычитки: черновик рядом со всем, что нужно для решения."""

    id: int
    task_id: int
    course_id: Optional[int] = None
    course_title: Optional[str] = None
    task_type: Optional[str] = None
    title: Optional[str] = None
    stem: str
    existing_hints: List[str]
    accepted_answers: List[str]
    text: Optional[str] = None
    status: str
    model: Optional[str] = None
    note: Optional[str] = None
    created_at: datetime
    sources: List[HintSource]


class HintQueueResponse(BaseModel):
    """Очередь вычитки."""

    total: int
    items: List[HintQueueItem]


class HintReviewRequest(BaseModel):
    """Решение методиста по черновику."""

    action: Literal["save", "approve", "reject"]
    text: Optional[str] = Field(default=None, description="Исправленный текст; `null` — не менять.")


class HintReviewResponse(BaseModel):
    """Итог вычитки и текущие подсказки задания."""

    draft: HintDraftView
    hints_text: List[str]


def _view(row) -> HintDraftView:
    return HintDraftView(
        id=row.id,
        task_id=row.task_id,
        status=row.status,
        text=row.text,
        model=row.model,
        note=row.note,
        lint_flags=list(row.lint_flags or []),
        source_reply_ids=list(row.source_reply_ids or []),
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
    )


@router.get(
    "/tasks/hint-drafts/candidates",
    response_model=HintCandidatesResponse,
    summary="Задания с новыми ответами преподавателей для подсказок (tsk-1220)",
)
async def hint_candidates(
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_async_db),
    _: CurrentUser = Depends(_METHODIST_GATE),
) -> HintCandidatesResponse:
    """Задания, где есть текстовые ответы, не вошедшие ни в один черновик."""
    return HintCandidatesResponse(
        items=[HintCandidate(**c) for c in await svc.candidates(db, limit=limit)]
    )


@router.get(
    "/tasks/hint-drafts/queue",
    response_model=HintQueueResponse,
    summary="Очередь вычитки черновиков подсказок (tsk-1220)",
)
async def hint_queue(
    state: Literal["draft", "approved", "rejected", "skipped", "blocked"] = Query("draft"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_async_db),
    _: CurrentUser = Depends(_METHODIST_GATE),
) -> HintQueueResponse:
    """Черновики по статусу, старые первыми."""
    total, items = await svc.queue(db, status=state, limit=limit, offset=offset)
    return HintQueueResponse(total=total, items=[HintQueueItem(**i) for i in items])


@router.post(
    "/tasks/hint-drafts/{draft_id}/review",
    response_model=HintReviewResponse,
    summary="Правка, подтверждение или отклонение черновика (tsk-1220)",
    responses={403: {"description": "Сервисный ключ не вычитывает"}, 409: {"description": "Уже обработан"}},
)
async def hint_review(
    draft_id: int = Path(..., ge=1),
    payload: HintReviewRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_METHODIST_GATE),
) -> HintReviewResponse:
    """Только человек: черновик модели подтверждает тот, кто его прочитал."""
    if current_user.is_service:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="вычитать подсказку может только человек: сервисный ключ вычиткой не является",
        )
    try:
        draft, hints = await svc.review(
            db, draft_id=draft_id, reviewer_id=current_user.id,
            action=payload.action, text_=payload.text,
        )
    except svc.HintDraftError as exc:
        code = status.HTTP_404_NOT_FOUND if "не найден" in str(exc) else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return HintReviewResponse(draft=_view(draft), hints_text=hints)


@router.post(
    "/tasks/{task_id}/hint-drafts/generate",
    response_model=GeneratedHintResponse,
    summary="Черновик подсказки моделью, без записи (tsk-1220)",
    responses={422: {"description": "Нет подходящих ответов"}, 502: {"description": "Сбой модели"}},
)
async def hint_generate(
    task_id: int = Path(..., ge=1),
    model: Optional[str] = Query(None, description="Явная модель вместо цепочки LLM_JUDGE_MODELS."),
    db: AsyncSession = Depends(get_async_db),
    _: CurrentUser = Depends(_METHODIST_GATE),
) -> GeneratedHintResponse:
    """Составить черновик. Ничего не пишет — решение о записи за линтером."""
    try:
        result = await svc.generate(db, task_id, model=model)
    except svc.HintDraftError as exc:
        msg = str(exc)
        code = (
            status.HTTP_502_BAD_GATEWAY
            if "модел" in msg
            else status.HTTP_404_NOT_FOUND if "не найдено" in msg
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise HTTPException(status_code=code, detail=msg) from exc
    return GeneratedHintResponse(**result.__dict__)


@router.post(
    "/tasks/{task_id}/hint-drafts",
    response_model=HintDraftView,
    status_code=status.HTTP_201_CREATED,
    summary="Записать итог генерации: в очередь, пропуск или блок (tsk-1220)",
)
async def hint_store(
    task_id: int = Path(..., ge=1),
    payload: HintDraftStoreRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    _: CurrentUser = Depends(_METHODIST_GATE),
) -> HintDraftView:
    """Записать строку черновика. Черновик с метками линтера не принимается."""
    try:
        row = await svc.store(
            db, task_id=task_id, status=payload.status, text_=payload.text,
            source_reply_ids=payload.source_reply_ids, model=payload.model,
            note=payload.note, lint_flags=payload.lint_flags,
        )
    except svc.HintDraftError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _view(row)
