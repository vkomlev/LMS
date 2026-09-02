"""
Панель преподавателя (tsk-430/435, Календарь LMS).

- `GET /teacher/lesson-occurrences?teacher_id=&from=&to=` — список занятий,
  каждое с полным списком участников + живой флаг `is_overdue` на каждого.
- `POST /teacher/lesson-occurrences/{id}/attendance` — ручная отметка
  присутствия/отсутствия ОДНОГО участника (occurrence может быть групповым).
- `POST /teacher/lesson-occurrences/add-student` — добавить ученика на
  занятие вручную (ad-hoc occurrence).
- `POST /teacher/lesson-occurrences/{id}/participants` — добавить ученика к
  УЖЕ существующему occurrence (например, подключить опоздавшего к группе).
- `GET /teacher/lesson-occurrences/{id}/summary` — сводка по каждому
  участнику (tsk-022 «до занятия» / tsk-410 «после занятия», кнопка
  «Подвести итоги» — общий эндпоинт для обеих точек входа фронта).

Гейт — тот же паттерн, что `teacher_workload.py`: явный `teacher_id` +
`get_current_user` + ручная ownership-проверка (`current_user.id ==
teacher_id`, сервисный токен — bypass).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.auth.current_user import CurrentUser
from app.core import settings_store
from app.core.config import Settings
from app.schemas.lesson_calendar import (
    AddParticipantRequest,
    AddStudentRequest,
    LessonOccurrenceRead,
    ParticipantRead,
    TeacherAttendanceActionRequest,
    TeacherLessonOccurrenceRead,
    TeacherLessonOccurrenceSummaryRead,
    TeacherParticipantRead,
)
from app.services import lesson_occurrence_service, teacher_lesson_summary_service

router = APIRouter(prefix="/teacher", tags=["teacher_lesson_occurrences"])


async def _ensure_self_or_service(
    db: AsyncSession, current_user: CurrentUser, teacher_id: int
) -> None:
    """Свои занятия — преподавателю, любые — методисту и админу.

    tsk-437 (2026-07-31): методист ведёт расписание всей школы, и занятия
    чужого преподавателя ему нужны по работе. Роль проверяется здесь, а не
    заменой гейта на `require_role`, потому что identity-ветка «свои занятия»
    обязана остаться: иначе преподаватель без роли методиста потерял бы доступ
    к собственному расписанию, а с ролью — получил бы чужое молча.

    Тот же приём и та же причина, что у ростера учеников в tsk-433 Волне 3.1
    (`/users/{teacher_id}/students`).
    """
    if current_user.is_service or current_user.id == teacher_id:
        return

    # Ленивый импорт: roles_service тянет модели, а роутер грузится рано.
    from app.services import roles_service  # noqa: PLC0415

    roles = set(await roles_service.get_user_role_names(db, current_user.id))
    if roles.isdisjoint({"methodist", "admin"}):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")


@router.get(
    "/lesson-occurrences",
    response_model=list[TeacherLessonOccurrenceRead],
)
async def list_teacher_occurrences(
    teacher_id: int = Query(..., description="ID преподавателя"),
    from_dt: Optional[datetime] = Query(default=None, alias="from"),
    to_dt: Optional[datetime] = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=300),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[TeacherLessonOccurrenceRead]:
    await _ensure_self_or_service(db, current_user, teacher_id)
    threshold_minutes = settings_store.get_int("lesson_no_show_threshold_minutes")
    pairs = await lesson_occurrence_service.list_for_teacher(
        db,
        teacher_id=teacher_id,
        from_dt=from_dt,
        to_dt=to_dt,
        limit=limit,
        no_show_threshold_minutes=threshold_minutes,
    )
    # tsk-741 (дефект 02.09): раньше кнопка превращалась в «Подвести итоги»
    # ровно в час начала занятия, и сводку по ученикам было уже не открыть —
    # хотя именно в начале урока преподаватель и смотрит, кто что сделал дома.
    summary_minutes = settings_store.get_int("lesson_summary_after_start_minutes")
    wrapup_minutes = settings_store.get_int("lesson_wrapup_before_end_minutes")

    result: list[TeacherLessonOccurrenceRead] = []
    for occurrence, participant_pairs in pairs:
        data = LessonOccurrenceRead.model_validate(occurrence).model_dump()
        ends_at = occurrence.scheduled_at + timedelta(
            minutes=int(occurrence.duration_minutes)
        )
        # Нижняя граница держит сводку доступной даже там, где занятие короче
        # суммы двух окон: иначе на 30-минутном уроке итоги предлагались бы
        # с первой минуты — ровно тот дефект, который чиним.
        data["wrapup_from"] = max(
            ends_at - timedelta(minutes=wrapup_minutes),
            occurrence.scheduled_at + timedelta(minutes=summary_minutes),
        )
        data["participants"] = [
            TeacherParticipantRead(
                **ParticipantRead.model_validate(p).model_dump(),
                is_overdue=is_overdue,
                # tsk-757: имя едет вместе с занятием, а не подставляется на
                # фронте из ростера преподавателя.
                full_name=full_name,
            )
            for p, is_overdue, full_name in participant_pairs
        ]
        result.append(TeacherLessonOccurrenceRead(**data))
    return result


@router.get(
    "/lesson-occurrences/{occurrence_id}/summary",
    response_model=TeacherLessonOccurrenceSummaryRead,
)
async def get_teacher_lesson_summary(
    occurrence_id: int,
    teacher_id: int = Query(..., description="ID преподавателя"),
    include_progress: bool = Query(
        True,
        description=(
            "Считать ли прогресс по курсу и заблокированные задания (tsk-665). "
            "false — `course_progress` и `blocked_tasks` придут как null "
            "(null = не считалось, [] = посчитали и пусто). Это самая дорогая "
            "часть сводки: обход дерева курса и состояния всех заданий на "
            "каждого участника."
        ),
    ),
    student_id: Optional[int] = Query(
        None,
        description="Вернуть только этого участника (tsk-665: подробности по клику).",
    ),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> TeacherLessonOccurrenceSummaryRead:
    """Сводка по каждому участнику occurrence (tsk-022 «до занятия» /
    tsk-410 «после занятия» — общий источник, см.
    ``teacher_lesson_summary_service``): последнее выполненное задание/
    материал, метрики ДЗ между занятиями, заблокированные лимитом задания,
    открытые заявки помощи, серия пропусков подряд, % прогресса курса.

    tsk-665: панель преподавателя опрашивает сводку раз в минуту всё занятие.
    Список строк обходится без прогресса (`include_progress=false`), а
    подробности одного ученика запрашиваются по клику (`student_id`)."""
    await _ensure_self_or_service(db, current_user, teacher_id)
    threshold_minutes = settings_store.get_int("lesson_no_show_threshold_minutes")
    data = await teacher_lesson_summary_service.get_occurrence_summary(
        db,
        occurrence_id=occurrence_id,
        teacher_id=teacher_id,
        current_user=current_user,
        no_show_threshold_minutes=threshold_minutes,
        include_progress=include_progress,
        student_id=student_id,
    )
    return TeacherLessonOccurrenceSummaryRead(**data)


@router.post(
    "/lesson-occurrences/{occurrence_id}/attendance",
    response_model=ParticipantRead,
)
async def post_teacher_attendance(
    occurrence_id: int,
    request: Request,
    teacher_id: int = Query(..., description="ID преподавателя"),
    body: TeacherAttendanceActionRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ParticipantRead:
    await _ensure_self_or_service(db, current_user, teacher_id)
    ip = request.client.host if request.client else None
    participant = await lesson_occurrence_service.record_teacher_attendance(
        db,
        occurrence_id=occurrence_id,
        teacher_id=teacher_id,
        student_id=body.student_id,
        action=body.action,
        ip=ip,
    )
    return ParticipantRead.model_validate(participant)


@router.post(
    "/lesson-occurrences/add-student",
    response_model=LessonOccurrenceRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_student_to_schedule(
    body: AddStudentRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> LessonOccurrenceRead:
    await _ensure_self_or_service(db, current_user, body.teacher_id)
    # tsk-587: преподавателю/методисту оставлена свобода назначить время вне
    # сетки расписания — это штатная «отработка вне расписания», и списка
    # готовых вариантов у него нет (в отличие от ученика, где выдача и приём
    # обязаны совпадать). Присоединение к уже существующему занятию на это
    # время работает и здесь — второе занятие в тот же час не появится.
    occurrence, _participant = await lesson_occurrence_service.create_ad_hoc_occurrence(
        db,
        student_id=body.student_id,
        teacher_id=body.teacher_id,
        scheduled_at=body.scheduled_at,
        duration_minutes=body.duration_minutes,
        require_scheduled_slot=False,
    )
    return LessonOccurrenceRead.model_validate(occurrence)


@router.post(
    "/lesson-occurrences/{occurrence_id}/participants",
    response_model=ParticipantRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_participant_to_occurrence(
    occurrence_id: int,
    teacher_id: int = Query(..., description="ID преподавателя"),
    body: AddParticipantRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ParticipantRead:
    await _ensure_self_or_service(db, current_user, teacher_id)
    participant = await lesson_occurrence_service.add_participant_to_occurrence(
        db, occurrence_id=occurrence_id, student_id=body.student_id, teacher_id=teacher_id,
    )
    return ParticipantRead.model_validate(participant)
