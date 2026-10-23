"""tsk-1255: импорт урока из файлов автора — разбор и публикация.

Два шага, черновик между ними живёт у клиента (поставка 1, без таблицы):
1. `POST /courses/{course_id}/lesson-imports/parse` — файлы → черновик, в БД
   ничего не пишется;
2. `POST /courses/{course_id}/lesson-imports/publish` — поправленный черновик →
   узлы урока, материалы, задания. Повтор обновляет тот же урок.

Презентация (поставка 2) разбирается в фоне — минуты: `POST …/presentation`
ставит задачу, `GET …/presentation/{job_id}` отдаёт состояние и, когда готово,
разметку блока «разбор по шагам» для материала черновика.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, Path, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.models.courses import Courses
from app.schemas.lesson_import import LessonImportDraft, LessonImportJob, LessonImportPublishResult
from app.services.lesson_import.draft import build_draft
from app.services.lesson_import.jobs import get_job, start_job
from app.services.lesson_import.publish import publish_draft
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["lesson-imports"])
settings = Settings()
_GATE = require_role("methodist", "admin")


async def _read_docx(file: Optional[UploadFile], label: str) -> Optional[bytes]:
    """Прочитать присланный docx с лимитом размера; не прислан — None."""
    if file is None or not file.filename:
        return None
    if not file.filename.lower().endswith(".docx"):
        raise DomainError(detail=f"«{label}»: нужен файл Word .docx", status_code=422)
    data = await file.read(settings.lesson_import_max_bytes + 1)
    if len(data) > settings.lesson_import_max_bytes:
        raise DomainError(
            detail=f"«{label}» больше {settings.lesson_import_max_bytes // (1024 * 1024)} МБ",
            status_code=413,
        )
    return data


@router.post(
    "/courses/{course_id}/lesson-imports/parse",
    response_model=LessonImportDraft,
    summary="Разобрать файлы урока в черновик (без записи в БД)",
)
async def parse_lesson_files(
    course_id: int,
    lesson_num: int = Form(..., ge=1, le=999),
    lesson_title: str = Form(..., min_length=1, max_length=500),
    video_url: Optional[str] = Form(None, max_length=2000),
    konspekt: Optional[UploadFile] = File(None),
    pretest: Optional[UploadFile] = File(None),
    homework: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_GATE),
) -> LessonImportDraft:
    """Конспект, пре-тест и ДЗ (docx) → черновик с предупреждениями для предпросмотра."""
    if await db.get(Courses, course_id) is None:
        raise DomainError(detail="Курс не найден", status_code=404)
    if video_url and not video_url.startswith(("https://", "http://")):
        raise DomainError(detail="Ссылка на видео должна начинаться с https://", status_code=422)
    files = {
        "konspekt": await _read_docx(konspekt, "Конспект"),
        "pretest": await _read_docx(pretest, "Пре-тест"),
        "homework": await _read_docx(homework, "Домашняя работа"),
    }
    if not any(files.values()) and not video_url:
        raise DomainError(detail="Не прислано ни одного файла урока", status_code=422)
    draft = build_draft(lesson_num=lesson_num, lesson_title=lesson_title.strip(),
                        video_url=video_url or None, **files)
    logger.info("lesson_import parse course=%s lesson=%s tasks=%s warnings=%s",
                course_id, lesson_num, len(draft["tasks"]), len(draft["warnings"]))
    return LessonImportDraft.model_validate(draft)


@router.post(
    "/courses/{course_id}/lesson-imports/publish",
    response_model=LessonImportPublishResult,
    summary="Опубликовать черновик урока (идемпотентно по ключам урока)",
)
async def publish_lesson_draft(
    course_id: int,
    draft: LessonImportDraft = Body(...),
    lesson_order: Optional[int] = Query(None, ge=1, le=1000,
                                        description="Место урока в курсе при первом создании"),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_GATE),
) -> LessonImportPublishResult:
    """Создать или обновить урок под курсом `course_id` из черновика."""
    return await publish_draft(db, course_id, draft, lesson_order=lesson_order)


@router.post(
    "/courses/{course_id}/lesson-imports/presentation",
    response_model=LessonImportJob,
    status_code=202,
    summary="Поставить разбор презентации (pptx/pdf) в фон",
)
async def start_presentation_job(
    course_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_GATE),
) -> LessonImportJob:
    """Презентация → кадры по щелчкам анимации. Ответ сразу, результат — опросом."""
    if await db.get(Courses, course_id) is None:
        raise DomainError(detail="Курс не найден", status_code=404)
    name = file.filename or ""
    if not name.lower().endswith((".pptx", ".pdf")):
        raise DomainError(detail="Презентация: нужен файл .pptx или .pdf", status_code=422)
    data = await file.read(settings.lesson_import_max_bytes + 1)
    if len(data) > settings.lesson_import_max_bytes:
        raise DomainError(
            detail=f"Презентация больше {settings.lesson_import_max_bytes // (1024 * 1024)} МБ",
            status_code=413,
        )
    job_id = await start_job(course_id, getattr(current_user, "id", None), data, name)
    logger.info("lesson_import presentation queued course=%s job=%s size=%s", course_id, job_id, len(data))
    return LessonImportJob(id=job_id, status="queued")


@router.get(
    "/courses/{course_id}/lesson-imports/presentation/{job_id}",
    response_model=LessonImportJob,
    summary="Состояние разбора презентации",
)
async def get_presentation_job(
    course_id: int,
    job_id: str = Path(..., pattern=r"^[0-9a-f]{32}$"),
    current_user: CurrentUser = Depends(_GATE),
) -> LessonImportJob:
    """queued → running → ready (с разметкой блока) или failed (с причиной)."""
    state = await get_job(job_id, course_id)
    if state is None:
        raise DomainError(detail="Задача разбора не найдена или устарела", status_code=404)
    result = state.get("result") or {}
    return LessonImportJob(
        id=job_id, status=state["status"], error=state.get("error"),
        html=result.get("html"), states=result.get("states"), screens=result.get("screens"),
    )
