"""API кураторства (tsk-742).

Три группы ручек и три разных читателя:

* **Куратор** — своя доска (`GET /curator/board`) и отметка о просмотре карточки
  ученика. Чужую доску преподаватель не видит.
* **Методист / админ** — раскладка: предпросмотр, применение, ручное
  закрепление, история по ученику, сводка.
* **Владелец школы** — недельный отчёт по активности кураторов.

Видимость НЕ расширяется: доска показывает только тех, за кого куратор
отвечает по `student_curator`. Доступ к ученикам, которых он не ведёт,
остаётся закрытым (граница tsk-757).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.curator import (
    ApplyDerivedRequest,
    ApplyDerivedResponse,
    AssignCuratorRequest,
    CuratorBoardResponse,
    CuratorCoverageResponse,
    CuratorPeriod,
    CuratorWeeklyReportResponse,
    DerivePreviewResponse,
)
from app.services import (
    curator_activity_service,
    curator_board_service,
    curator_service,
    roles_service,
)

logger = logging.getLogger("api.curator")

router = APIRouter(tags=["curator"])

#: Доску смотрит преподаватель; методист и админ — чужую, для разбора.
_BOARD_GATE = require_role("teacher", "methodist", "admin")
#: Раскладку правит тот, кто и так распределяет людей (см. student_teacher_links).
_ASSIGN_GATE = require_role("methodist", "admin")
#: Отчёт — владельцу школы, и только ему.
#:
#: Методист сюда НЕ допущен намеренно (исправлено 02.09 по живым данным): у нас
#: методист — тот же преподаватель, и он получил бы сводку с оценкой работы
#: своих коллег. Куратор про себя узнаёт персональным сигналом, где чужих чисел
#: нет. Тот же список получателей у рассылки — `curator_report_cron_service`.
_REPORT_GATE = require_role("admin")


async def _is_elevated(db: AsyncSession, current_user: CurrentUser) -> bool:
    """Методист, админ или сервисный токен — может смотреть чужую доску."""
    if current_user.is_service:
        return True
    roles = set(await roles_service.get_user_role_names(db, current_user.id))
    return not roles.isdisjoint({"methodist", "admin"})


@router.get(
    "/curator/board",
    response_model=CuratorBoardResponse,
    summary="Доска куратора: мои ученики и что требует действия",
)
async def get_curator_board(
    curator_id: Optional[int] = Query(
        None,
        description=(
            "Чужая доска — только методисту и админу. Преподаватель видит свою"
        ),
    ),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_BOARD_GATE),
) -> CuratorBoardResponse:
    """Ученики куратора, отсортированные по срочности повода.

    Ничего не пишет: пролистать список — не то же самое, что посмотреть
    человека, и засчитывать открытие доски за внимание к каждому ученику
    значило бы обесценить главную цифру недельного отчёта.
    """
    target = curator_id or current_user.id
    if target != current_user.id and not await _is_elevated(db, current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Чужая доска недоступна")
    board = await curator_board_service.get_board(db, curator_id=target)
    return CuratorBoardResponse(**board)


@router.post(
    "/curator/students/{student_id}/view",
    summary="Отметить, что куратор открыл карточку своего ученика",
)
async def record_curator_view(
    student_id: int,
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_BOARD_GATE),
) -> dict:
    """Записать просмотр карточки — подтверждение обязанности «знать, как дела».

    Пишется, ТОЛЬКО если ученик действительно закреплён за вызывающим. Иначе
    отвечаем `recorded: false`, а не ошибкой: карточку ученика открывают и
    методист, и преподаватель, ведущий у него занятие, — для них это обычный
    просмотр, а не кураторская работа, и падать здесь нечему.

    Молчаливо засчитывать такой просмотр за кураторский нельзя: тогда охват в
    отчёте показывал бы работу, которой не было.
    """
    roster = await curator_service.roster_ids(db, current_user.id)
    if student_id not in roster:
        return {"recorded": False, "reason": "ученик не закреплён за вами"}
    await curator_activity_service.record_view(
        db, curator_id=current_user.id, student_id=student_id
    )
    return {"recorded": True}


@router.get(
    "/curator/derive-preview",
    response_model=DerivePreviewResponse,
    summary="Предпросмотр раскладки, выведенной из расписания",
)
async def derive_preview(
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_ASSIGN_GATE),
) -> DerivePreviewResponse:
    """Кого правило закрепит и кого оставит оператору. В базу не пишет."""
    result = await curator_service.derive_from_schedule(db)
    return DerivePreviewResponse(**result)


@router.post(
    "/curator/derive-apply",
    response_model=ApplyDerivedResponse,
    summary="Применить раскладку из расписания",
)
async def derive_apply(
    payload: ApplyDerivedRequest = Body(default_factory=ApplyDerivedRequest),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_ASSIGN_GATE),
) -> ApplyDerivedResponse:
    """Закрепить учеников по правилу.

    По умолчанию сухой прогон: `dry_run=false` нужно указать явно. Запись,
    которую видят живые люди, не должна происходить от нажатия по инерции.
    """
    result = await curator_service.apply_derived(
        db,
        dry_run=payload.dry_run,
        overwrite=payload.overwrite,
        assigned_by=None if current_user.is_service else current_user.id,
    )
    return ApplyDerivedResponse(**result)


@router.get(
    "/curator/coverage",
    response_model=CuratorCoverageResponse,
    summary="Сводка раскладки: у кого сколько учеников и сколько без куратора",
)
async def coverage(
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_ASSIGN_GATE),
) -> CuratorCoverageResponse:
    """Кто сколько ведёт. Ученики без куратора — отдельным числом."""
    return CuratorCoverageResponse(**await curator_service.coverage(db))


@router.put(
    "/curator/students/{student_id}",
    summary="Закрепить ученика за куратором вручную",
)
async def assign_curator(
    student_id: int,
    payload: AssignCuratorRequest,
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_ASSIGN_GATE),
) -> dict:
    """Сменить или назначить куратора с причиной.

    Ученику и преподавателю система об этом НЕ сообщает: смена куратора —
    событие для живых людей, и объявляет о нём человек (устав § 7).
    """
    try:
        result = await curator_service.assign(
            db,
            student_id=student_id,
            curator_id=payload.curator_id,
            source=curator_service.SOURCE_MANUAL,
            reason=payload.reason,
            ended_reason=payload.ended_reason,
            assigned_by=None if current_user.is_service else current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return result


@router.delete(
    "/curator/students/{student_id}",
    summary="Снять куратора с ученика",
)
async def unassign_curator(
    student_id: int,
    reason: Optional[str] = Query(None, max_length=500, description="Почему сняли"),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_ASSIGN_GATE),
) -> dict:
    """Ученик уходит в список «без куратора» — то есть обратно к оператору."""
    ok = await curator_service.unassign(
        db,
        student_id=student_id,
        ended_reason=reason,
        ended_by=None if current_user.is_service else current_user.id,
    )
    return {"changed": ok}


@router.get(
    "/curator/students/{student_id}/history",
    response_model=list[CuratorPeriod],
    summary="История кураторства по ученику",
)
async def curator_history(
    student_id: int,
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_ASSIGN_GATE),
) -> list[CuratorPeriod]:
    """Кто и когда отвечал за этого ученика — ради этого закрепление хранится
    отрезками, а не колонкой."""
    return [CuratorPeriod(**r) for r in await curator_service.history(db, student_id)]


@router.get(
    "/curator/weekly-report",
    response_model=CuratorWeeklyReportResponse,
    summary="Недельный отчёт по активности кураторов",
)
async def weekly_report(
    week_start: Optional[date] = Query(
        None, description="Понедельник отчётной недели; по умолчанию прошлая полная"
    ),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_REPORT_GATE),
) -> CuratorWeeklyReportResponse:
    """Что делали кураторы за неделю — не как учились ученики.

    Главная строка по каждому — сколько учеников осталось без единого касания.
    """
    report = await curator_activity_service.weekly_report(db, week_start=week_start)
    return CuratorWeeklyReportResponse(
        **report, text=curator_activity_service.render_report_text(report)
    )
