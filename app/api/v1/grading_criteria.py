"""Заполнение и вычитка критериев оценивания (tsk-590).

Поле критериев появилось в tsk-605, и две недели простояло пустым: заполнять
279 заданий по одному через карточку курса физически некому. Здесь собран
путь, который делает работу методиста посильной:

1. **Очередь вычитки** (`GET /tasks/grading-criteria/queue`) — задания подряд,
   с полным условием и текущими критериями. Основная работа методиста — не
   печатать, а читать, и экран построен под это.
2. **Черновик модели** (`POST /tasks/{task_id}/grading-criteria/draft`) —
   заготовка по тексту условия. Всегда `status="draft"`: к оценке ученика
   она не допускается, пока её не прочитал человек.
3. **Подтверждение** (`POST /tasks/{task_id}/grading-criteria`) — отдельное
   осознанное действие с именем подтвердившего.
4. **Пакет** (`POST /tasks/grading-criteria/bulk`) и **выгрузка**
   (`GET /tasks/grading-criteria/export`) — для правки вне кабинета и для
   просмотра списка со стороны.

Роль — methodist/admin: критерии содержат разбор верного ответа, ученику их
видеть нельзя (обнуление `solution_rules` в `_task_read_for`, tsk-460).
"""
from __future__ import annotations

import csv
import io
import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.models.courses import Courses
from app.models.tasks import Tasks
from app.schemas.solution_rules import SolutionRules
from app.services import ai_check_policy, grading_criteria_draft, grading_criteria_service
from app.services.grading_criteria_service import CriteriaUpdate

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tasks"])

_METHODIST_GATE = require_role("methodist", "admin")

#: Сколько заданий отдаёт пакетная загрузка за раз. Ограничение не про
#: производительность, а про отчёт: длиннее сотни строк построчный разбор
#: «принято/отклонено» человек уже не читает.
BULK_MAX_ITEMS = 100


class CriteriaView(BaseModel):
    """Критерии задания в том виде, в каком их правит и читает методист."""

    must: List[str] = Field(default_factory=list)
    accept: List[str] = Field(default_factory=list)
    reject: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    status: Optional[str] = None
    origin: Optional[str] = None
    generated_by_model: Optional[str] = None
    reviewed_by: Optional[int] = None
    reviewed_at: Optional[str] = None
    #: Оговорка о классе задания от кода (файл-приложение, вычисляемый ответ).
    draft_warning: Optional[str] = None


class CriteriaQueueItem(BaseModel):
    """Задание в очереди вычитки — с полным условием, а не превью.

    Превью хватает инвентарю («что заполнять»), но не вычитке: решить, верны
    ли критерии, без текста задания нельзя, а открывать каждое задание в
    соседней вкладке — та самая работа, из-за которой поле стояло пустым.
    """

    task_id: int
    external_uid: Optional[str] = None
    course_id: int
    course_title: Optional[str] = None
    task_type: Optional[str] = None
    order_position: Optional[int] = None
    title: Optional[str] = None
    stem: str
    requires_attachment: bool
    #: `none` — критериев нет, `draft` — заготовка ждёт вычитки.
    criteria_state: str
    criteria: Optional[CriteriaView] = None


class CriteriaQueueResponse(BaseModel):
    """Очередь вычитки критериев."""

    total: int
    drafts_total: int
    empty_total: int
    items: List[CriteriaQueueItem]


class CriteriaWriteRequest(BaseModel):
    """Правка и/или подтверждение критериев одного задания."""

    must: Optional[List[str]] = Field(
        default=None, description="Обязательные требования. `null` — не менять."
    )
    accept: Optional[List[str]] = Field(default=None, description="Что засчитывать наравне.")
    reject: Optional[List[str]] = Field(default=None, description="Что не засчитывать.")
    notes: Optional[str] = Field(default=None, description="Пояснение проверяющему.")
    approve: bool = Field(
        default=False,
        description=(
            "Подтвердить критерии как вычитанные. Только с этим флагом они "
            "начинают работать заменой эталона; правка текста сама по себе "
            "черновик не подтверждает."
        ),
    )


class CriteriaWriteResponse(BaseModel):
    """Итог записи по одному заданию."""

    task_id: int
    criteria_state: str
    machine_gradable: bool
    criteria: CriteriaView


class CriteriaBulkItem(CriteriaWriteRequest):
    """Строка пакетной загрузки."""

    task_id: int = Field(..., description="Задание, которому принадлежат критерии.")


class CriteriaBulkRequest(BaseModel):
    """Пакетная загрузка критериев."""

    items: List[CriteriaBulkItem] = Field(..., min_length=1, max_length=BULK_MAX_ITEMS)
    dry_run: bool = Field(
        default=False,
        description=(
            "Проверить строки и вернуть отчёт, ничего не записывая. Пакет с "
            "чужой правкой лучше отклонить целиком до записи, чем разбирать "
            "половину применённых строк потом."
        ),
    )


class CriteriaBulkResultItem(BaseModel):
    """Что стало с одной строкой пакета."""

    task_id: int
    ok: bool
    criteria_state: Optional[str] = None
    error: Optional[str] = None


class CriteriaBulkResponse(BaseModel):
    """Построчный отчёт пакетной загрузки."""

    applied: int
    rejected: int
    dry_run: bool
    items: List[CriteriaBulkResultItem]


class DraftResponse(BaseModel):
    """Составленный черновик критериев."""

    task_id: int
    criteria_state: str
    criteria: CriteriaView
    model: str
    tokens_in: int
    tokens_out: int


def _view(rules: SolutionRules) -> Optional[CriteriaView]:
    """Критерии задания для показа методисту."""
    criteria = rules.grading_criteria
    if criteria is None or not criteria.must:
        return None
    return CriteriaView(
        must=list(criteria.must),
        accept=list(criteria.accept),
        reject=list(criteria.reject),
        notes=criteria.notes,
        status=criteria.status,
        origin=criteria.origin,
        generated_by_model=criteria.generated_by_model,
        reviewed_by=criteria.reviewed_by,
        reviewed_at=criteria.reviewed_at,
        draft_warning=criteria.draft_warning,
    )


def _rules_or_400(task: Tasks) -> SolutionRules:
    """Правило задания схемой; нечитаемое — 422 с внятной причиной."""
    raw: Any = task.solution_rules
    if isinstance(raw, dict):
        try:
            return SolutionRules.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 — текст уходит методисту
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"правило проверки задания не разбирается: {exc}",
            ) from exc
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="у задания нет правила проверки (solution_rules)",
    )


def _assert_human_reviewer(current_user: CurrentUser, *, approve: bool) -> None:
    """Подтвердить критерии может только человек, не сервисный ключ.

    `require_role` пропускает сервисный ключ без проверки роли — на этом
    держатся ТГ-боты и импорт. Но вся задача стоит на том, что заготовку
    модели читает ЧЕЛОВЕК, и скрипт, ходящий тем же ключом, не должен уметь
    подтвердить то, что сам же и сгенерировал.

    :param current_user: кто пришёл.
    :param approve: просят ли подтверждение.
    :raises HTTPException: 403, если подтверждение просит сервисный ключ.
    """
    if approve and current_user.is_service:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "подтвердить критерии может только человек: сервисный ключ "
                "вычиткой не является"
            ),
        )


async def _task_or_404(db: AsyncSession, task_id: int) -> Tasks:
    task = await db.scalar(select(Tasks).where(Tasks.id == task_id))
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="задание не найдено")
    return task


#: Кандидаты очереди: задания, где эталона нет и критерии либо отсутствуют,
#: либо не подтверждены. Отбор ШИРЕ нужного, окончательное решение выносит
#: `ai_check_policy.evaluate` и `SolutionRules.criteria_state` в Python — по
#: той же причине, что в инвентаре пробелов (tsk-605): две редакции одного
#: правила расходятся при первой правке.
#: `jsonb_typeof` вместо `IS NULL` — потому что правка через API пишет
#: незаполненные блоки явным JSON-null (дефект, найденный живым прогоном
#: tsk-605 §9).
_QUEUE_CANDIDATE_SQL = """
    jsonb_array_length(coalesce(solution_rules->'correct_options','[]'::jsonb)) = 0
    AND coalesce(jsonb_typeof(solution_rules->'turtle_sim'), 'null') <> 'object'
    AND jsonb_array_length(coalesce(solution_rules->'short_answer'->'accepted_answers','[]'::jsonb)) = 0
    AND jsonb_array_length(coalesce(solution_rules->'text_answer'->'rubric','[]'::jsonb)) = 0
    AND coalesce(solution_rules->'grading_criteria'->>'status', 'draft') <> 'approved'
"""


@router.get(
    "/tasks/grading-criteria/queue",
    response_model=CriteriaQueueResponse,
    summary="Очередь вычитки критериев оценивания (tsk-590)",
    responses={
        200: {"description": "Задания, которым нужны критерии или их вычитка"},
        401: {"description": "Не аутентифицирован"},
        403: {"description": "Роль не позволяет (нужна methodist/admin)"},
    },
)
async def criteria_queue(
    course_id: Optional[int] = Query(None, description="Ограничить одним курсом"),
    state: Literal["draft", "none", "all"] = Query(
        "all", description="`draft` — заготовки на вычитку, `none` — пустые, `all` — всё"
    ),
    limit: int = Query(20, ge=1, le=100, description="Сколько заданий вернуть"),
    offset: int = Query(0, ge=0, description="Смещение по очереди"),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_METHODIST_GATE),
) -> CriteriaQueueResponse:
    """Отдать задания, которым нужны критерии, вместе с полным условием.

    Сводка (`total`, `drafts_total`, `empty_total`) считается по всей выборке,
    а не по странице: постфильтр поверх пагинации молча теряет строки, и
    методист видел бы «осталось 20» там, где осталось 279 (tsk-605 §1).

    :param course_id: показать только один курс.
    :param state: что показывать — заготовки, пустые или всё подряд.
    :param limit: размер страницы.
    :param offset: смещение.
    """
    from sqlalchemy import text as sa_text  # noqa: PLC0415 — локально, как в tasks_extra

    query = (
        select(
            Tasks.id,
            Tasks.external_uid,
            Tasks.course_id,
            Tasks.task_content,
            Tasks.solution_rules,
            Tasks.order_position,
            Courses.title.label("course_title"),
        )
        .join(Courses, Courses.id == Tasks.course_id)
        .where(Tasks.is_active.is_(True))
        .where(sa_text(_QUEUE_CANDIDATE_SQL))
    )
    if course_id is not None:
        query = query.where(Tasks.course_id == course_id)

    rows = (await db.execute(query.order_by(Tasks.course_id, Tasks.order_position, Tasks.id))).all()

    items: List[CriteriaQueueItem] = []
    drafts = 0
    empties = 0
    for row in rows:
        content = row.task_content if isinstance(row.task_content, dict) else {}
        task_type = content.get("type")
        verdict = ai_check_policy.evaluate(task_type, row.solution_rules)
        # Задание, у которого машине и так есть с чем сверять (эталон), в
        # очереди не нужно: критерии ему ничего не добавят.
        if verdict.allowed:
            continue
        if verdict.reason == "invalid_rules":
            continue
        try:
            rules = SolutionRules.model_validate(row.solution_rules)
        except Exception:  # noqa: BLE001 — битые правила уже отсеяны выше, это страховка
            continue
        criteria_state = rules.criteria_state()
        if criteria_state == "approved":
            continue
        if criteria_state == "draft":
            drafts += 1
        else:
            empties += 1
        if state != "all" and criteria_state != state:
            continue
        items.append(
            CriteriaQueueItem(
                task_id=row.id,
                external_uid=row.external_uid,
                course_id=row.course_id,
                course_title=row.course_title,
                task_type=task_type,
                order_position=row.order_position,
                title=content.get("title") if isinstance(content.get("title"), str) else None,
                stem=grading_criteria_draft.clean_stem(content.get("stem")),
                requires_attachment=bool(rules.requires_attachment),
                criteria_state=criteria_state,
                criteria=_view(rules),
            )
        )

    return CriteriaQueueResponse(
        total=len(items),
        drafts_total=drafts,
        empty_total=empties,
        items=items[offset : offset + limit],
    )


@router.get(
    "/tasks/grading-criteria/export",
    summary="Выгрузка критериев оценивания таблицей (tsk-590)",
    responses={
        200: {"description": "CSV со списком заданий и текущими критериями"},
        401: {"description": "Не аутентифицирован"},
        403: {"description": "Роль не позволяет (нужна methodist/admin)"},
    },
)
async def criteria_export(
    course_id: Optional[int] = Query(None, description="Ограничить одним курсом"),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_METHODIST_GATE),
) -> StreamingResponse:
    """Выгрузить очередь таблицей — посмотреть список целиком вне кабинета.

    Пункты внутри ячейки разделяются переносом строки: так их читает и
    таблица, и человек, и обратная загрузка (`bulk`).

    :param course_id: ограничить одним курсом.
    """
    queue = await criteria_queue(
        course_id=course_id, state="all", limit=100000, offset=0, db=db, current_user=current_user
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(
        [
            "task_id", "external_uid", "course_id", "course_title", "task_type",
            "criteria_state", "requires_attachment", "title", "stem",
            "must", "accept", "reject", "notes", "draft_warning",
        ]
    )
    for item in queue.items:
        criteria = item.criteria
        writer.writerow(
            [
                item.task_id, item.external_uid or "", item.course_id, item.course_title or "",
                item.task_type or "", item.criteria_state, "да" if item.requires_attachment else "нет",
                item.title or "", item.stem,
                "\n".join(criteria.must) if criteria else "",
                "\n".join(criteria.accept) if criteria else "",
                "\n".join(criteria.reject) if criteria else "",
                (criteria.notes or "") if criteria else "",
                (criteria.draft_warning or "") if criteria else "",
            ]
        )
    buffer.seek(0)
    # BOM — чтобы кириллица открывалась в Excel без плясок с кодировкой.
    payload = "﻿" + buffer.getvalue()
    return StreamingResponse(
        io.BytesIO(payload.encode("utf-8")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="grading-criteria.csv"'},
    )


@router.post(
    "/tasks/grading-criteria/bulk",
    response_model=CriteriaBulkResponse,
    summary="Пакетная загрузка критериев оценивания (tsk-590)",
    responses={
        200: {"description": "Построчный отчёт: что принято, что отклонено и почему"},
        401: {"description": "Не аутентифицирован"},
        403: {"description": "Роль не позволяет (нужна methodist/admin)"},
    },
)
async def criteria_bulk(
    payload: CriteriaBulkRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_METHODIST_GATE),
) -> CriteriaBulkResponse:
    """Записать критерии сразу нескольким заданиям.

    Пакет не прерывается на первой плохой строке: методисту нужен отчёт по
    всем, а не «упало на 7-й». Запись коммитится один раз в конце, поэтому
    отчёт и состояние базы совпадают.

    :param payload: строки и признак пробного прогона.
    """
    _assert_human_reviewer(current_user, approve=any(item.approve for item in payload.items))
    results: List[CriteriaBulkResultItem] = []
    applied = 0
    for item in payload.items:
        outcome = await grading_criteria_service.apply(
            db,
            CriteriaUpdate(
                task_id=item.task_id,
                must=item.must,
                accept=item.accept,
                reject=item.reject,
                notes=item.notes,
                approve=item.approve,
            ),
            reviewer_id=current_user.id,
            origin="import",
            commit=False,
        )
        if outcome.ok:
            applied += 1
        results.append(
            CriteriaBulkResultItem(
                task_id=outcome.task_id,
                ok=outcome.ok,
                criteria_state=outcome.state,
                error=outcome.error,
            )
        )

    if payload.dry_run:
        await db.rollback()
    else:
        await db.commit()

    logger.info(
        "tsk-590: пакет критериев — принято %s из %s, пробный прогон=%s",
        applied,
        len(payload.items),
        payload.dry_run,
    )
    return CriteriaBulkResponse(
        applied=applied,
        rejected=len(payload.items) - applied,
        dry_run=payload.dry_run,
        items=results,
    )


@router.post(
    "/tasks/{task_id}/grading-criteria/draft",
    response_model=DraftResponse,
    summary="Составить черновик критериев по тексту задания (tsk-590)",
    responses={
        200: {"description": "Черновик составлен и записан со статусом «не вычитан»"},
        401: {"description": "Не аутентифицирован"},
        403: {"description": "Роль не позволяет (нужна methodist/admin)"},
        404: {"description": "Задание не найдено"},
        409: {"description": "У задания уже есть подтверждённые критерии"},
        502: {"description": "Модель не ответила или ответила неразборчиво"},
    },
)
async def criteria_draft(
    task_id: int = Path(..., description="Задание, для которого нужна заготовка"),
    model: Optional[str] = Query(
        None, description="Явная модель; по умолчанию — цепочка LLM_JUDGE_MODELS"
    ),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_METHODIST_GATE),
) -> DraftResponse:
    """Составить заготовку критериев и положить её в задание как черновик.

    Заготовка НЕ участвует в оценке ответов: она записывается со
    `status="draft"`, а предикат допуска считает критериями только
    подтверждённые человеком. Это главное свойство всей задачи — незачёт по
    правилам, которых никто не читал, хуже, чем очередь на проверку.

    :param task_id: задание.
    :param model: явная модель вместо цепочки по умолчанию.
    """
    task = await _task_or_404(db, task_id)
    rules = _rules_or_400(task)
    if rules.criteria_state() == "approved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="у задания уже есть подтверждённые критерии — заготовка их не заменяет",
        )

    course_title = await db.scalar(select(Courses.title).where(Courses.id == task.course_id))
    try:
        drafted = await grading_criteria_draft.generate(
            task_content=task.task_content,
            solution_rules=task.solution_rules,
            course_title=course_title,
            model=model,
        )
    except grading_criteria_draft.DraftError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    outcome = await grading_criteria_service.store_draft(
        db, task_id=task_id, criteria=drafted.criteria
    )
    if not outcome.ok:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=outcome.error or "черновик не записан"
        )

    return DraftResponse(
        task_id=task_id,
        criteria_state="draft",
        criteria=CriteriaView(
            must=list(drafted.criteria.must),
            accept=list(drafted.criteria.accept),
            reject=list(drafted.criteria.reject),
            notes=drafted.criteria.notes,
            status=drafted.criteria.status,
            origin=drafted.criteria.origin,
            generated_by_model=drafted.criteria.generated_by_model,
            draft_warning=drafted.criteria.draft_warning,
        ),
        model=drafted.model,
        tokens_in=drafted.tokens_in,
        tokens_out=drafted.tokens_out,
    )


@router.post(
    "/tasks/{task_id}/grading-criteria",
    response_model=CriteriaWriteResponse,
    summary="Сохранить и/или подтвердить критерии оценивания (tsk-590)",
    responses={
        200: {"description": "Критерии записаны"},
        401: {"description": "Не аутентифицирован"},
        403: {"description": "Роль не позволяет (нужна methodist/admin)"},
        404: {"description": "Задание не найдено"},
        422: {"description": "Критерии не прошли проверку"},
    },
)
async def criteria_write(
    task_id: int = Path(..., description="Задание"),
    payload: CriteriaWriteRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_METHODIST_GATE),
) -> CriteriaWriteResponse:
    """Записать критерии задания и, если методист подтвердил, снять с них черновик.

    Правка и подтверждение разведены намеренно: методист, который поправил в
    задании что-то другое, не должен нечаянно допустить непрочитанные
    критерии к оценке ученика (решение оператора 2026-08-20).

    :param task_id: задание.
    :param payload: новые списки и признак подтверждения.
    """
    _assert_human_reviewer(current_user, approve=payload.approve)
    outcome = await grading_criteria_service.apply(
        db,
        CriteriaUpdate(
            task_id=task_id,
            must=payload.must,
            accept=payload.accept,
            reject=payload.reject,
            notes=payload.notes,
            approve=payload.approve,
        ),
        reviewer_id=current_user.id,
        origin="manual",
    )
    if not outcome.ok:
        code = (
            status.HTTP_404_NOT_FOUND
            if outcome.error == "задание не найдено"
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise HTTPException(status_code=code, detail=outcome.error or "критерии не записаны")

    task = await _task_or_404(db, task_id)
    rules = _rules_or_400(task)
    content = task.task_content if isinstance(task.task_content, dict) else {}
    verdict = ai_check_policy.evaluate(content.get("type"), task.solution_rules)
    view = _view(rules)
    return CriteriaWriteResponse(
        task_id=task_id,
        criteria_state=rules.criteria_state(),
        machine_gradable=verdict.allowed,
        criteria=view or CriteriaView(),
    )
