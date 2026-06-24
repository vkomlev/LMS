"""API upsert правил назначения курсов (tsk-120, ADR-0042).

Для режима публикатора LMS: публикатор ContentBackbone создаёт правила
назначения вместе с курсом/задачами. Идемпотентно по ``code`` (повтор не
плодит дубли). Авторизация — глобальная api-key (как у bulk-upsert материалов
и задач), отдельной teacher-роли не требует: это служебный пайплайн.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.assignment_rules import (
    AssignmentRulesBulkUpsertRequest,
    AssignmentRulesBulkUpsertResponse,
    AssignmentRuleUpsertResult,
)
from app.services.assignment_rules_service import bulk_upsert_rules

router = APIRouter(tags=["assignment_rules"])


@router.post(
    "/assignment-rules/bulk-upsert",
    response_model=AssignmentRulesBulkUpsertResponse,
    status_code=status.HTTP_200_OK,
    summary="Идемпотентный upsert правил назначения курсов (ключ — code)",
)
async def assignment_rules_bulk_upsert(
    body: AssignmentRulesBulkUpsertRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> AssignmentRulesBulkUpsertResponse:
    """Создать/обновить правила назначения курсов пакетно.

    - Ключ идемпотентности — ``code``.
    - ``task_external_uid`` резолвится в ``task_id``, ``course_uid`` — в ``course_id``.
    - Устойчиво по элементам: ошибка одного правила не валит весь batch
      (``results[].action = error`` с описанием).
    """
    results = await bulk_upsert_rules(db, body.items)
    return AssignmentRulesBulkUpsertResponse(
        results=[AssignmentRuleUpsertResult(**r) for r in results]
    )
