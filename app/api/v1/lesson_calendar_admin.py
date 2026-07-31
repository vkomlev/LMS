"""
Admin API Календаря LMS (tsk-428/435): часы работы школы + групповые слоты
расписания + их участники.

Гейт: роли ``methodist``/``admin`` (или сервисный токен) — расписание создаётся
централизованно, не самим преподавателем/учеником. С tsk-437 (2026-07-31)
расписание ведёт методист из веб-кабинета; раньше гейт был только ``admin``, и
браузеру методиста слоты были недоступны (бот проходил сервисным ключом).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.repos.lesson_calendar_repository import LessonSlotStudentRepository
from app.schemas.lesson_calendar import (
    AddSlotParticipantRequest,
    LessonSlotCreate,
    LessonSlotRead,
    LessonSlotUpdate,
    OperatingHoursCreate,
    OperatingHoursRead,
    OperatingHoursUpdate,
    SlotParticipantRead,
)
from app.services import lesson_calendar_service

router = APIRouter(tags=["lesson_calendar_admin"])

# tsk-437 (2026-07-31): расписание ведёт МЕТОДИСТ, не админ.
#
# Раньше гейт был `require_role("admin")`, и веб-кабинету методиста слоты были
# недоступны: ТГ-бот проходил мимо роли сервисным ключом (`is_service` в
# `require_role` возвращает раньше проверки), а браузер под cookie-сессией —
# нет. Роль admin оставлена: у неё доступ ко всему.
#
# Преподаватель сюда НЕ входит намеренно: слот определяет, кто и когда ведёт
# занятие, — это распорядительное решение методиста, а не самого преподавателя.
_SCHEDULE_GATE = require_role("methodist", "admin")

#: Историческое имя. Оставлено, чтобы не переписывать 13 обработчиков разом
#: и не смешивать смену гейта с правкой их логики.
_ADMIN_GATE = _SCHEDULE_GATE
_slot_student_repo = LessonSlotStudentRepository()


async def _to_slot_read(db: AsyncSession, slot) -> LessonSlotRead:
    participants = await _slot_student_repo.list_for_slot(db, slot.id)
    data = LessonSlotRead.model_validate(slot).model_dump()
    data["student_ids"] = [p.student_id for p in participants]
    return LessonSlotRead(**data)


# ─── Operating Hours ────────────────────────────────────────────────────────


@router.get("/operating-hours", response_model=list[OperatingHoursRead])
async def get_operating_hours(
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> list[OperatingHoursRead]:
    rows = await lesson_calendar_service.list_operating_hours(db)
    return [OperatingHoursRead.model_validate(r) for r in rows]


@router.post(
    "/operating-hours",
    response_model=OperatingHoursRead,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить окно часов работы школы на день недели",
)
async def create_operating_hours(
    body: OperatingHoursCreate = Body(...),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> OperatingHoursRead:
    row = await lesson_calendar_service.create_operating_hours(
        db,
        weekday=body.weekday,
        start_time=body.start_time,
        end_time=body.end_time,
        timezone=body.timezone,
    )
    return OperatingHoursRead.model_validate(row)


@router.patch("/operating-hours/{row_id}", response_model=OperatingHoursRead)
async def update_operating_hours(
    row_id: int,
    body: OperatingHoursUpdate = Body(...),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> OperatingHoursRead:
    row = await lesson_calendar_service.update_operating_hours(
        db,
        row_id,
        start_time=body.start_time,
        end_time=body.end_time,
        timezone=body.timezone,
    )
    return OperatingHoursRead.model_validate(row)


@router.delete("/operating-hours/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_operating_hours(
    row_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> Response:
    await lesson_calendar_service.delete_operating_hours(db, row_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ─── Lesson Slot (групповой) ────────────────────────────────────────────────


@router.post(
    "/lesson-slots",
    response_model=LessonSlotRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_lesson_slot(
    body: LessonSlotCreate = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> LessonSlotRead:
    row = await lesson_calendar_service.create_lesson_slot(
        db,
        teacher_id=body.teacher_id,
        weekday=body.weekday,
        start_time=body.start_time,
        duration_minutes=body.duration_minutes,
        timezone=body.timezone,
        created_by=current_user.id if not current_user.is_service else None,
        student_ids=body.student_ids,
    )
    return await _to_slot_read(db, row)


@router.get("/lesson-slots", response_model=list[LessonSlotRead])
async def list_lesson_slots(
    teacher_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> list[LessonSlotRead]:
    rows = await lesson_calendar_service.list_lesson_slots(db, teacher_id=teacher_id)
    return [await _to_slot_read(db, r) for r in rows]


@router.get("/lesson-slots/{slot_id}", response_model=LessonSlotRead)
async def get_lesson_slot(
    slot_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> LessonSlotRead:
    row = await lesson_calendar_service.get_lesson_slot(db, slot_id)
    return await _to_slot_read(db, row)


@router.patch("/lesson-slots/{slot_id}", response_model=LessonSlotRead)
async def update_lesson_slot(
    slot_id: int,
    body: LessonSlotUpdate = Body(...),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> LessonSlotRead:
    row = await lesson_calendar_service.update_lesson_slot(
        db,
        slot_id,
        weekday=body.weekday,
        start_time=body.start_time,
        duration_minutes=body.duration_minutes,
        timezone=body.timezone,
        is_active=body.is_active,
        teacher_id=body.teacher_id,
    )
    return await _to_slot_read(db, row)


@router.delete("/lesson-slots/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_lesson_slot(
    slot_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> Response:
    """Деактивация (``is_active=false``), не физическое удаление — сохраняет
    историю уже сгенерированных occurrence."""
    await lesson_calendar_service.deactivate_lesson_slot(db, slot_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/lesson-slots/{slot_id}/participants",
    response_model=SlotParticipantRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_slot_participant(
    slot_id: int,
    body: AddSlotParticipantRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> SlotParticipantRead:
    row = await lesson_calendar_service.add_slot_participant(
        db, slot_id, body.student_id,
        added_by=current_user.id if not current_user.is_service else None,
    )
    return SlotParticipantRead.model_validate(row)


@router.get("/lesson-slots/{slot_id}/participants", response_model=list[SlotParticipantRead])
async def list_slot_participants(
    slot_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> list[SlotParticipantRead]:
    rows = await lesson_calendar_service.list_slot_participants(db, slot_id)
    return [SlotParticipantRead.model_validate(r) for r in rows]


@router.delete(
    "/lesson-slots/{slot_id}/participants/{student_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_slot_participant(
    slot_id: int,
    student_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> Response:
    await lesson_calendar_service.remove_slot_participant(db, slot_id, student_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class OccurrenceTeacherRead(BaseModel):
    """Ведущий одного занятия."""

    model_config = ConfigDict(from_attributes=True)

    occurrence_id: int
    teacher_id: int
    is_active: bool
    is_one_off: bool


@router.get(
    "/lesson-occurrences/{occurrence_id}/teachers",
    response_model=list[OccurrenceTeacherRead],
    summary="Кто ведёт это занятие",
    description=(
        "Состав ведущих КОНКРЕТНОГО занятия, с учётом разовых исключений. "
        "Постоянный состав — у слота (`/lesson-slots/{id}/teachers`)."
    ),
)
async def list_occurrence_teachers(
    occurrence_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_SCHEDULE_GATE),
) -> list[OccurrenceTeacherRead]:
    rows = await lesson_calendar_service.list_occurrence_teachers(db, occurrence_id)
    return [OccurrenceTeacherRead.model_validate(r) for r in rows]


@router.post(
    "/lesson-occurrences/{occurrence_id}/teachers/{teacher_id}",
    response_model=OccurrenceTeacherRead,
    status_code=status.HTTP_200_OK,
    summary="Поставить преподавателя на одно занятие",
    description=(
        "РАЗОВОЕ назначение: действует только на это занятие, состав слота не "
        "меняется, следующие занятия идут как обычно. Годится и для усиления "
        "(ведут двое), и для подмены — вместе со снятием штатного.\n\n"
        "Постоянное назначение — это другое действие: "
        "`POST /lesson-slots/{id}/teachers/{teacher_id}`."
    ),
    responses={404: {"description": "Занятие не найдено"}},
)
async def add_occurrence_teacher(
    occurrence_id: int,
    teacher_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_SCHEDULE_GATE),
) -> OccurrenceTeacherRead:
    row = await lesson_calendar_service.add_occurrence_teacher(
        db, occurrence_id, teacher_id
    )
    return OccurrenceTeacherRead.model_validate(row)


@router.delete(
    "/lesson-occurrences/{occurrence_id}/teachers/{teacher_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Снять преподавателя с одного занятия",
    description=(
        "РАЗОВОЕ снятие: «на этом занятии не ведёт» (болезнь, отпуск). Состав "
        "слота не меняется — следующие занятия останутся за ним.\n\n"
        "Снятие реализовано ГАШЕНИЕМ, а не удалением строки: генератор занятий "
        "досыпает состав слота каждый тик и удалённую строку вернул бы обратно."
    ),
    responses={
        404: {"description": "Занятие не найдено или преподаватель его не ведёт"},
        409: {"description": "Последнего ведущего снять нельзя"},
    },
)
async def remove_occurrence_teacher(
    occurrence_id: int,
    teacher_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_SCHEDULE_GATE),
) -> Response:
    await lesson_calendar_service.remove_occurrence_teacher(db, occurrence_id, teacher_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class TransferSlotParticipantRequest(BaseModel):
    """Куда переводим ученика."""

    target_slot_id: int = Field(..., description="Слот, в который ученик переезжает")


@router.post(
    "/lesson-slots/{slot_id}/participants/{student_id}/transfer",
    response_model=SlotParticipantRead,
    status_code=status.HTTP_200_OK,
    summary="Перевести ученика в другой слот",
    description=(
        "Перевод НАСОВСЕМ: ученик снимается с исходного слота и ставится на "
        "целевой одной транзакцией — он либо переехал целиком, либо остался "
        "там, где был.\n\n"
        "Будущие занятия исходного слота, где ученик ещё ничего не решил сам, "
        "он покидает; целевого — получает сразу, не дожидаясь генератора. "
        "Прошедшие занятия и уже отмеченная явка не трогаются.\n\n"
        "Разовый перенос одного занятия — это другое действие "
        "(`POST /lesson-occurrences/{id}/reschedule` в кабинете ученика)."
    ),
    responses={
        404: {"description": "Слот не найден или ученик не числится в исходном слоте"},
        409: {"description": "Целевой слот выключен либо время занято другим слотом ученика"},
        422: {"description": "Исходный и целевой слоты совпадают"},
    },
)
async def transfer_slot_participant(
    slot_id: int,
    student_id: int,
    body: TransferSlotParticipantRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> SlotParticipantRead:
    row = await lesson_calendar_service.transfer_slot_participant(
        db,
        source_slot_id=slot_id,
        target_slot_id=body.target_slot_id,
        student_id=student_id,
        added_by=current_user.id if not current_user.is_service else None,
    )
    return SlotParticipantRead.model_validate(row)


# ---------------------------------------------------------------------------
# tsk-437: кто ведёт слот (состав преподавателей)
# ---------------------------------------------------------------------------


class SlotTeacherRead(BaseModel):
    """Преподаватель, закреплённый за слотом."""

    slot_id: int
    teacher_id: int
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


@router.get(
    "/lesson-slots/{slot_id}/teachers",
    response_model=list[SlotTeacherRead],
    summary="Кто ведёт слот",
    description=(
        "Состав преподавателей слота. Именно он определяет, у кого занятие "
        "появится в кабинете; `lesson_slot.teacher_id` — основной/создатель.\n\n"
        "Если состав пуст, генератор занятий подставляет основного."
    ),
)
async def list_slot_teachers_endpoint(
    slot_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_SCHEDULE_GATE),
) -> list[SlotTeacherRead]:
    rows = await lesson_calendar_service.list_slot_teachers(db, slot_id)
    return [SlotTeacherRead.model_validate(r) for r in rows]


@router.post(
    "/lesson-slots/{slot_id}/teachers/{teacher_id}",
    response_model=SlotTeacherRead,
    status_code=status.HTTP_201_CREATED,
    summary="Поставить преподавателя на слот",
    description=(
        "Идемпотентно: повторный вызов вернёт существующую запись, снятую — "
        "поднимет обратно.\n\n"
        "Отражается на уже созданные БУДУЩИЕ занятия слота — иначе "
        "преподаватель не увидел бы их до следующего прогона генератора."
    ),
    responses={404: {"description": "Слот или преподаватель не найден"}},
)
async def add_slot_teacher_endpoint(
    slot_id: int,
    teacher_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_SCHEDULE_GATE),
) -> SlotTeacherRead:
    row = await lesson_calendar_service.add_slot_teacher(
        db, slot_id, teacher_id,
        added_by=_current_user.id if not _current_user.is_service else None,
    )
    return SlotTeacherRead.model_validate(row)


@router.delete(
    "/lesson-slots/{slot_id}/teachers/{teacher_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Снять преподавателя со слота",
    description=(
        "Мягкое снятие. Прошедшие занятия не трогаются — кто вёл, тот и вёл; "
        "снимается только с будущих.\n\n"
        "**Последнего преподавателя снять нельзя** (409): слот остался бы без "
        "ведущего, а генератор молча вернул бы основного."
    ),
    responses={
        404: {"description": "Слот не найден"},
        409: {"description": "Это последний преподаватель слота"},
    },
)
async def remove_slot_teacher_endpoint(
    slot_id: int,
    teacher_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_SCHEDULE_GATE),
) -> Response:
    await lesson_calendar_service.remove_slot_teacher(db, slot_id, teacher_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
