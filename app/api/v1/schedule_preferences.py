"""API пожеланий по расписанию (tsk-674, фаза 1).

Ученик:
- ``GET  /me/schedule-preference``          — что выбрано + сама сетка часов
- ``PUT  /me/schedule-preference``          — сохранить (перезапись целиком)
- ``GET  /me/schedule-preference/history``  — история собственных правок

Методист/админ:
- ``GET  /methodist/schedule-preferences/summary``            — охват опроса
- ``GET  /methodist/schedule-preferences/{student_id}``       — пожелание ученика
- ``GET  /methodist/schedule-preferences/{student_id}/history`` — его история

Гейт сводки — тот же, что у расписания (`methodist`/`admin`): вёрстку делает
методист, и охват опроса нужен ему же. Преподаватель сюда не входит по той же
причине, что и в `lesson_calendar_admin`: слот — распорядительное решение.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_async_db,
    get_current_user,
    require_authenticated,
    require_role,
)
from app.auth.current_user import CurrentUser
from app.schemas.schedule_preference import (
    SchedulePreferenceRead,
    SchedulePreferenceReminderItem,
    SchedulePreferenceReminderPending,
    SchedulePreferenceReminderRun,
    SchedulePreferenceRevisionRead,
    SchedulePreferenceSummary,
    SchedulePreferenceWrite,
)
from app.services import schedule_preference_reminder_service, schedule_preference_service
from app.services.schedule_preference_service import SchedulePreferenceError

router = APIRouter(tags=["schedule_preferences"])

_SUMMARY_GATE = require_role("methodist", "admin")


@router.get("/me/schedule-preference", response_model=SchedulePreferenceRead)
async def get_my_schedule_preference(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> SchedulePreferenceRead:
    """Пожелания текущего ученика вместе с сеткой допустимых часов.

    Ответ отдаётся и тому, кто в аудиторию опроса не входит (выпускник, демо):
    поле `is_audience=false` — это ответ «опрос не для вас», а не отказ. Отказ
    пришлось бы объяснять на экране, а объяснять тут нечего.
    """
    data = await schedule_preference_service.get_preference(db, current_user.id)
    return SchedulePreferenceRead(**data)


@router.put("/me/schedule-preference", response_model=SchedulePreferenceRead)
async def save_my_schedule_preference(
    body: SchedulePreferenceWrite,
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> SchedulePreferenceRead:
    """Сохранить пожелания. Правится в любой момент, каждая версия остаётся в истории."""
    try:
        data = await schedule_preference_service.save_preference(
            db, current_user.id, body, changed_by=current_user.id
        )
    except SchedulePreferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return SchedulePreferenceRead(**data)


@router.get(
    "/me/schedule-preference/history",
    response_model=list[SchedulePreferenceRevisionRead],
)
async def get_my_schedule_preference_history(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> list[SchedulePreferenceRevisionRead]:
    rows = await schedule_preference_service.list_history(db, current_user.id)
    return [SchedulePreferenceRevisionRead(**r) for r in rows]


@router.get(
    "/methodist/schedule-preferences/summary",
    response_model=SchedulePreferenceSummary,
)
async def get_schedule_preferences_summary(
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_SUMMARY_GATE),
) -> SchedulePreferenceSummary:
    """Охват опроса: сколько заполнили, кто молчит, какой час сколько просят."""
    return SchedulePreferenceSummary(**await schedule_preference_service.get_summary(db))


@router.post(
    "/methodist/schedule-preferences/remind",
    response_model=SchedulePreferenceReminderRun,
)
async def remind_silent_students(
    dry_run: bool = Query(
        False, description="Только посчитать адресатов, ничего не отправляя"
    ),
    limit: int | None = Query(
        None, ge=1, le=200, description="Ограничить число адресатов за проход"
    ),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_SUMMARY_GATE),
) -> SchedulePreferenceReminderRun:
    """Напомнить молчащим — прямо сейчас, поверх суточного прохода.

    Нужна 29 августа, когда молчащих осталось пятеро и ждать сутки нельзя.
    Тем, кому напоминали меньше двух суток назад, повторно не пишет: отсрочка
    считается там же, где и у автоматического прохода.
    """
    result = await schedule_preference_reminder_service.enqueue_reminders(
        db, dry_run=dry_run, limit=limit
    )
    return SchedulePreferenceReminderRun(**result, dry_run=dry_run)


@router.get(
    "/students/{student_id}/schedule-preference-reminders/pending",
    response_model=SchedulePreferenceReminderPending,
)
async def list_pending_schedule_preference_reminders(
    student_id: int = Path(..., ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> SchedulePreferenceReminderPending:
    """Напоминания о пожеланиях для одного ученика — для student-бота TG_LMS.

    Отдельный адрес, а не расширение `lesson-reminders/pending`: тот эндпоинт
    жёстко про напоминания о занятиях, у бота для них свой текст, и подмешивать
    туда второй вид означало бы менять уже работающий контракт (tsk-431).

    Гейт тот же: сервисный ключ бота ходит за разных учеников, поэтому
    `is_service` пропускается, а живой человек — только за себя.
    """
    if not current_user.is_service and current_user.id != student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Доступ запрещён")

    rows = (
        await db.execute(
            text(
                "SELECT n.id, n.modified_at, n.kind, n.title, n.content, n.payload, n.read_at "
                "  FROM notifications n "
                " WHERE n.user_id = :uid AND n.kind = :kind "
                " ORDER BY n.modified_at ASC LIMIT :lim"
            ),
            {
                "uid": student_id,
                "kind": schedule_preference_reminder_service.REMINDER_KIND,
                "lim": limit,
            },
        )
    ).fetchall()

    items = [
        SchedulePreferenceReminderItem(
            id=int(r[0]),
            created_at=r[1],
            kind=str(r[2]),
            title=r[3],
            content=r[4],
            payload=dict(r[5]) if r[5] else {},
            read_at=r[6],
        )
        for r in rows
    ]
    return SchedulePreferenceReminderPending(items=items, count=len(items))


@router.get(
    "/methodist/schedule-preferences/{student_id}",
    response_model=SchedulePreferenceRead,
)
async def get_student_schedule_preference(
    student_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_SUMMARY_GATE),
) -> SchedulePreferenceRead:
    data = await schedule_preference_service.get_preference(db, student_id)
    return SchedulePreferenceRead(**data)


@router.get(
    "/methodist/schedule-preferences/{student_id}/history",
    response_model=list[SchedulePreferenceRevisionRead],
)
async def get_student_schedule_preference_history(
    student_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_SUMMARY_GATE),
) -> list[SchedulePreferenceRevisionRead]:
    rows = await schedule_preference_service.list_history(db, student_id)
    return [SchedulePreferenceRevisionRead(**r) for r in rows]
