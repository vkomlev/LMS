"""tsk-1255: импорт урока из файлов автора — разбор и публикация.

Два шага, черновик между ними живёт у клиента (поставка 1, без таблицы):
1. `POST /courses/{course_id}/lesson-imports/parse` — файлы → черновик, в БД
   ничего не пишется;
2. `POST /courses/{course_id}/lesson-imports/publish` — поправленный черновик →
   узлы урока, материалы, задания. Повтор обновляет тот же урок.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.models.courses import Courses
from app.schemas.lesson_import import LessonImportDraft, LessonImportPublishResult
from app.services.lesson_import.draft import build_draft
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
