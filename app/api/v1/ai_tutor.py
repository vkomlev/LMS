"""ИИ-наставник ученика: эндпоинты (tsk-572 этап 2).

Стриминг обязателен по UX: без него ученик смотрит в пустой экран весь ответ
целиком, а p99 первого байта равен p99 полного ответа.

Деградация вместо ошибки: если модель недоступна, ученик получает не «500», а
понятную строку и предложение позвать преподавателя. Заявку при этом НЕ создаём
автоматически — решение оператора: непрошеная заявка от каждого сбоя завалила бы
преподавателя.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.services.ai_tutor import session_service
from app.services.llm import Budget, LLMError, stream

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai-tutor", tags=["ai_tutor"])
settings = Settings()

_DEGRADED_TEXT = (
    "Наставник сейчас недоступен. Это не из-за тебя — сбой на нашей стороне. "
    "Позови преподавателя: он разберёт задание вместе с тобой."
)
_LIMIT_TEXT = (
    "Мы уже долго ходим по кругу. Дальше быстрее будет с преподавателем — "
    "напиши ему, он посмотрит задание вместе с тобой."
)


class TutorSessionRead(BaseModel):
    """Состояние разговора. Плашка о видимости — обязательное поле, не опция."""

    session_id: int
    task_id: int
    mode: str
    turns: int
    status: str
    soft_limit_reached: bool
    visibility_notice: str = Field(
        default=(
            "Этот разговор видят твой преподаватель и методист — "
            "они помогают, а не проверяют."
        ),
        description="Показывается ДО первой реплики: ученик должен знать, кто это прочитает",
    )
    messages: list[dict] = Field(default_factory=list)


class TutorAskRequest(BaseModel):
    message: Optional[str] = Field(
        None, max_length=4000,
        description="Реплика ученика. Пусто на первом обращении — наставник начинает сам.",
    )


def _enabled() -> bool:
    """Рубильник: выключенный наставник не ломает ничего остального."""
    return getattr(settings, "ai_tutor_enabled", True)


@router.get(
    "/tasks/{task_id}",
    response_model=TutorSessionRead,
    summary="Открыть разговор с наставником по заданию",
)
async def get_session(
    task_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> TutorSessionRead:
    """Состояние разговора текущего ученика по заданию (создаёт при первом входе).

    Сессия жёстко привязана к `current_user`: чужой разговор по чужому заданию
    недостижим — идентификатор сессии в запрос вообще не передаётся.
    """
    if not _enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Наставник выключен")
    try:
        session, _ = await session_service.get_or_create(
            db, student_id=current_user.id, task_id=task_id
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    rows = await session_service.history(db, session.id)
    return TutorSessionRead(
        session_id=session.id, task_id=session.task_id, mode=session.mode,
        turns=session.turns, status=session.status,
        soft_limit_reached=session.soft_limit_reached,
        # Системный промпт ученику не показываем: это инструкция наставнику,
        # а не часть разговора.
        messages=[
            {"role": r["role"], "content": r["content"], "truncated": r["truncated"]}
            for r in rows if r["role"] != "system"
        ],
    )


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post(
    "/tasks/{task_id}/ask",
    summary="Спросить наставника (поток server-sent events)",
)
async def ask(
    task_id: int,
    body: TutorAskRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """Отправить реплику и получить ответ потоком.

    Ошибка ДО первого куска превращается в понятную деградацию, а не в 5xx:
    ученику незачем видеть код ошибки, ему нужен следующий шаг. Ошибка ПОСЛЕ
    первого куска приходит как `truncated` — написанное не стирается.
    """
    if not _enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Наставник выключен")

    try:
        session, _ = await session_service.get_or_create(
            db, student_id=current_user.id, task_id=task_id
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    if session.hard_limit_reached:
        await session_service.close(db, session.id)
        await db.commit()

        async def _limited() -> AsyncIterator[str]:
            yield _sse("delta", {"text": _LIMIT_TEXT})
            yield _sse("done", {"offer_teacher": True, "limit_reached": True})

        return StreamingResponse(_limited(), media_type="text/event-stream")

    student_text = (body.message or "").strip() or None
    if student_text:
        await session_service.add_message(db, session.id, "student", student_text)
    messages = await session_service.build_llm_messages(db, session, student_text)
    await session_service.bump_turn(db, session.id)
    await db.commit()

    async def _generate() -> AsyncIterator[str]:
        collected: list[str] = []
        model_used = ""
        truncated = False
        try:
            async for chunk in stream(
                messages, purpose="tutor", student_id=current_user.id,
                budget=Budget.INTERACTIVE, max_tokens=900,
            ):
                if chunk.done:
                    model_used = chunk.model
                    truncated = chunk.truncated
                    break
                collected.append(chunk.delta)
                yield _sse("delta", {"text": chunk.delta})
        except LLMError as exc:
            # Деградация: наставник молчит, но ученик не в тупике.
            logger.warning(
                "ai_tutor: сбой наставника session=%s student=%s: %s",
                session.id, current_user.id, exc,
            )
            yield _sse("delta", {"text": _DEGRADED_TEXT})
            yield _sse("done", {"offer_teacher": True, "degraded": True})
            return

        text_out = "".join(collected).strip()
        if text_out:
            await session_service.add_message(
                db, session.id, "tutor", text_out, model=model_used, truncated=truncated
            )
            await db.commit()
        yield _sse("done", {
            "truncated": truncated,
            "offer_teacher": session.soft_limit_reached,
            "turns": session.turns + 1,
        })

    return StreamingResponse(_generate(), media_type="text/event-stream", headers={
        # Без этого прокси буферизует поток и стриминг превращается в обычный
        # ответ целиком — ровно то, ради отказа от чего он и делался.
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@router.post(
    "/tasks/{task_id}/close",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Закрыть разговор",
)
async def close_session(
    task_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    session, created = await session_service.get_or_create(
        db, student_id=current_user.id, task_id=task_id
    )
    await session_service.close(db, session.id)
    await db.commit()
