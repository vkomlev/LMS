"""Флаги формы ответа по правилам задания (tsk-227, tsk-396, tsk-547, tsk-953).

Единая точка расчёта: ими пользуются состояние задания ученика
(`GET /learning/tasks/{id}/state`) и предпросмотр преподавателя (tsk-1146),
чтобы форма в обоих режимах выглядела одинаково.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.schemas.solution_rules import SolutionRules
from app.schemas.task_content import SHORT_ANSWER_TASK_TYPES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskFormFlags:
    """UX-сигналы формы ответа; форс правил — на сервере при сдаче."""

    requires_attachment: bool = False
    partial_auto_check: bool = False
    has_reference_answer: bool = True
    has_io_tests: bool = False
    diagnostic: bool = False


def compute_task_form_flags(solution_rules: Any, task_content: Any) -> TaskFormFlags:
    """
    Посчитать флаги формы по сырым `solution_rules` и `task_content` задания.

    «Есть эталон» считается типо-зависимо: у SC/MC/TA/квизов блок
    `short_answer` не заполняется в принципе, для них флаг всегда true.
    Некорректные правила не ломают выдачу — возвращаются значения по умолчанию.
    """
    task_type = task_content.get("type") if isinstance(task_content, dict) else None
    try:
        rules = SolutionRules.model_validate(solution_rules or {})
        return TaskFormFlags(
            requires_attachment=bool(rules.requires_attachment),
            partial_auto_check=bool(rules.partial_auto_check),
            has_reference_answer=(
                rules.has_reference_answer() if task_type in SHORT_ANSWER_TASK_TYPES else True
            ),
            has_io_tests=rules.io_tests is not None,
            diagnostic=bool(rules.diagnostic),
        )
    except Exception:
        # Как и до выноса: битые правила не должны ронять выдачу состояния задания.
        logger.warning(
            "compute_task_form_flags: некорректные solution_rules, флаги по умолчанию",
            exc_info=True,
        )
        return TaskFormFlags()
