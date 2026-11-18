"""API карты прогресса: публикация и initial flow (tsk-1524)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, get_current_user, require_role
from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.schemas.checking import StudentAnswer
from app.schemas.exam_progress import (
    ExamAnswerAccepted, ExamAnswerRequest, ExamRunRead, ExamVariantPublish,
    ExamVariantPublishResponse, ExamMapRead, ExamVariantRead, ExamReviewQueueItem,
)
from app.services import exam_progress_service

router = APIRouter(prefix="/exam-progress", tags=["exam-progress"])
settings = Settings()


class StartRunRequest(BaseModel):
    variant_id: int


class SkipItemRequest(BaseModel):
    client_request_id: str = Field(min_length=8, max_length=100)


def _enabled() -> None:
    if not settings.exam_progress_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


def _student(user: CurrentUser) -> int:
    if user.is_service:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Требуется ученическая сессия")
    if settings.exam_progress_student_ids and user.id not in settings.exam_progress_student_ids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Вариант доступен только участникам пилота")
    return user.id


@router.post("/variants/publish", response_model=ExamVariantPublishResponse)
async def publish_variant(body: ExamVariantPublish, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_bare_db)):
    _enabled()
    if not user.is_service:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Публикация доступна только сервисному клиенту")
    return await exam_progress_service.publish_variant(db, body)


@router.post("/me/runs/start", response_model=ExamRunRead)
async def start_run(body: StartRunRequest, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_bare_db)):
    _enabled()
    return await exam_progress_service.start_run(db, student_id=_student(user), variant_id=body.variant_id)


@router.get("/me/runs/{run_id}", response_model=ExamRunRead)
async def get_run(run_id: int, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_bare_db)):
    _enabled()
    return await exam_progress_service.get_run(db, run_id=run_id, student_id=_student(user))


@router.post("/me/runs/{run_id}/pause", response_model=ExamRunRead)
async def pause(run_id: int, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_bare_db)):
    _enabled()
    return await exam_progress_service.transition(db, run_id=run_id, student_id=_student(user), action="pause")


@router.post("/me/runs/{run_id}/resume", response_model=ExamRunRead)
async def resume(run_id: int, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_bare_db)):
    _enabled()
    return await exam_progress_service.transition(db, run_id=run_id, student_id=_student(user), action="resume")


@router.post("/me/runs/{run_id}/heartbeat", response_model=ExamRunRead)
async def heartbeat(run_id: int, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_bare_db)):
    _enabled()
    return await exam_progress_service.transition(db, run_id=run_id, student_id=_student(user), action="heartbeat")


@router.post("/me/runs/{run_id}/items/{item_id}/answer", response_model=ExamAnswerAccepted, response_model_exclude_none=True)
async def answer(run_id: int, item_id: int, body: ExamAnswerRequest, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_bare_db)):
    _enabled()
    student_answer = StudentAnswer.model_validate(body.answer)
    if body.phase in ("review", "late"):
        return await exam_progress_service.answer_review(db, run_id=run_id, item_id=item_id, student_id=_student(user), request_id=body.client_request_id, answer=student_answer, phase=body.phase, reflection_reason=body.reflection_reason, reflection_comment=body.reflection_comment)
    return await exam_progress_service.answer_initial(db, run_id=run_id, item_id=item_id, student_id=_student(user), request_id=body.client_request_id, answer=student_answer, phase=body.phase)


@router.post("/me/runs/{run_id}/items/{item_id}/skip", response_model=ExamAnswerAccepted, response_model_exclude_none=True)
async def skip(run_id: int, item_id: int, body: SkipItemRequest, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_bare_db)):
    _enabled()
    return await exam_progress_service.skip_initial(db, run_id=run_id, item_id=item_id, student_id=_student(user), request_id=body.client_request_id)


@router.post("/me/runs/{run_id}/finish-initial", response_model=ExamRunRead)
async def finish_initial(run_id: int, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_bare_db)):
    _enabled()
    return await exam_progress_service.finish_initial(db, run_id=run_id, student_id=_student(user))


@router.post("/me/runs/{run_id}/finish-review", response_model=ExamRunRead)
async def finish_review(run_id: int, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_bare_db)):
    _enabled()
    return await exam_progress_service.finish_review(db, run_id=run_id, student_id=_student(user))


@router.get("/me/map", response_model=ExamMapRead)
async def get_map(root_course_id: int, exam_kind: str, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_bare_db)):
    _enabled()
    if exam_kind not in ("ege", "oge"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неизвестный экзамен")
    return await exam_progress_service.get_map(db, student_id=_student(user), root_course_id=root_course_id, exam_kind=exam_kind)


@router.get("/me/variants", response_model=list[ExamVariantRead])
async def list_variants(root_course_id: int, exam_kind: str, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_bare_db)):
    _enabled()
    if exam_kind not in ("ege", "oge"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неизвестный экзамен")
    return await exam_progress_service.list_variants(db, student_id=_student(user), root_course_id=root_course_id, exam_kind=exam_kind)


@router.get("/teacher/review-queue", response_model=list[ExamReviewQueueItem])
async def review_queue(user: CurrentUser = Depends(require_role("teacher", "methodist", "admin")), db: AsyncSession = Depends(get_bare_db)):
    _enabled()
    if user.is_service:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Очередь доступна только сотруднику")
    from app.services import roles_service
    roles = set(await roles_service.get_user_role_names(db, user.id))
    return await exam_progress_service.teacher_review_queue(db, user_id=user.id, elevated=bool(roles & {"methodist", "admin"}))
