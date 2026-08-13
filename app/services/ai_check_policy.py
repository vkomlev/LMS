"""Допуск задания к машинной проверке ответа (tsk-605).

**Один вопрос — одно место.** «Можно ли доверить проверку этого задания
машине» спрашивают минимум трое: судья ответов (tsk-590), автономный
подписной трек без преподавателя (tsk-301) и экран методиста, который
показывает, что осталось заполнить. Если каждый ответит сам, они разойдутся —
как уже расходились «сверять нечем» на проверке и «поле ответа бессмысленно»
в форме до tsk-547, и как разъехалось правило ИИ-расхода по трём клиентам
в tsk-572. Поэтому предикат здесь один и его ВЫЗЫВАЮТ, а не переписывают.

**Почему правило именно такое.** Калибровка на 180 живых сдачах
(`reviews/2026-08-08-tsk590-kalibrovka.md`): с эталоном собственные ошибки
лучшей модели 1.2 %, без эталона — 7.6 % у сильной и 19.0 % у дешёвой.
Разделяющий признак — не тип задания, а наличие того, с чем сверять. Без
преподавателя ошибочный зачёт перехватить некому, поэтому «сверять нечем»
обязано означать «машине не отдаём», а не «ну как-нибудь».

Модуль намеренно чистый: без БД и без сети. Права ученика (тариф, лимиты) —
не здесь, а в `entitlements_service`; здесь только свойство САМОГО задания.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Optional

from pydantic import ValidationError

from app.schemas.solution_rules import SolutionRules
from app.schemas.task_content import QUIZ_TASK_TYPES, SHORT_ANSWER_TASK_TYPES

logger = logging.getLogger(__name__)

#: Почему задание не допущено. Отдельные коды, а не общее «нельзя»: у причин
#: разные продуктовые выходы — критерии дописывает методист, а задание с
#: файлом-приложением упирается в то, что модель файла не видит вовсе, и его
#: судьба (вне трека либо платная эскалация к человеку) решается в tsk-301.
BlockReason = Literal[
    "no_reference_no_criteria",
    "attachment_not_readable",
    "invalid_rules",
]

#: Типы, у которых эталон живёт в `correct_options`, а не в `short_answer`.
_OPTION_TASK_TYPES: tuple[str, ...] = ("SC", "MC")

_REASON_TEXT: dict[BlockReason, str] = {
    "no_reference_no_criteria": (
        "у задания нет ни эталона ответа, ни критериев оценивания — "
        "машине не с чем сверять"
    ),
    "attachment_not_readable": (
        "ответ подтверждается файлом-приложением, которого проверяющая модель "
        "не видит"
    ),
    "invalid_rules": "правило проверки задания не разбирается",
}


@dataclass(frozen=True)
class GradabilityVerdict:
    """Ответ двери. `allowed` и `reason` возвращаются вместе и оба.

    Форма повторяет `entitlements_service.Decision` намеренно: «не допущено»
    и «почему» — разные сведения, и сведение их к булеву значению лишает
    вызывающего возможности объяснить это ученику и методисту.
    """

    allowed: bool
    reason: Optional[BlockReason] = None
    #: Есть ли эталон — для инвентаря методиста и телеметрии.
    has_reference: bool = False
    #: Есть ли критерии (новое поле либо рубрика TA).
    has_criteria: bool = False

    @property
    def human_reason(self) -> Optional[str]:
        """Причина отказа простыми словами — для экрана и лога."""
        return _REASON_TEXT[self.reason] if self.reason else None


_ALLOWED = GradabilityVerdict(allowed=True, has_reference=True)


def evaluate(task_type: Optional[str], solution_rules: Any) -> GradabilityVerdict:
    """Можно ли отдать проверку ответа на это задание машине.

    :param task_type: `task_content.type` задания (`SA_COM`, `TA`, `SC`, …).
        `None` и незнакомый тип трактуются как отказ: неизвестное правило
        проверки не должно молча означать «разрешено».
    :param solution_rules: правила задания — `SolutionRules`, словарь из
        `tasks.solution_rules` либо `None`.
    :returns: вердикт с причиной отказа.
    """
    rules = _as_rules(solution_rules)
    if rules is None:
        return GradabilityVerdict(allowed=False, reason="invalid_rules")

    has_criteria = rules.has_grading_criteria()

    # Квизы со шкалами вердикта «верно/неверно» не выносят вовсе — судить
    # нечего, и отказывать не в чем.
    if task_type in QUIZ_TASK_TYPES:
        return _ALLOWED

    if task_type in _OPTION_TASK_TYPES:
        has_reference = bool(rules.correct_options)
    elif task_type in SHORT_ANSWER_TASK_TYPES:
        has_reference = rules.has_reference_answer()
    elif task_type == "TA":
        # Развёрнутый ответ формализуемого эталона не имеет по определению:
        # единственная опора — критерии.
        has_reference = False
    else:
        return GradabilityVerdict(allowed=False, reason="invalid_rules")

    if not has_reference and not has_criteria:
        return GradabilityVerdict(
            allowed=False,
            reason="no_reference_no_criteria",
            has_reference=False,
            has_criteria=False,
        )

    # Файл-приложение обязателен → доказательство лежит там, куда модель не
    # смотрит. В прогоне разведки модели по таким работам честно отказывались
    # выносить вердикт («не хватает данных»), и это правильное поведение:
    # засчитать ответ, не увидев подтверждения, — тот же ложный зачёт.
    # Гейт стоит ПОСЛЕ проверки эталона, чтобы в инвентаре методиста было
    # видно: критерии тут не помогут, вопрос продуктовый (tsk-301).
    if rules.requires_attachment:
        return GradabilityVerdict(
            allowed=False,
            reason="attachment_not_readable",
            has_reference=has_reference,
            has_criteria=has_criteria,
        )

    return GradabilityVerdict(
        allowed=True, has_reference=has_reference, has_criteria=has_criteria
    )


def is_machine_gradable(task_type: Optional[str], solution_rules: Any) -> bool:
    """Короткая форма `evaluate` для условий в коде."""
    return evaluate(task_type, solution_rules).allowed


def _as_rules(solution_rules: Any) -> Optional[SolutionRules]:
    """Привести правило к схеме, не роняя вызывающего на битых данных.

    Правки `solution_rules` прямо в БД мимо API валидатор схемы обходят
    (прецедент tsk-396), поэтому нечитаемое правило здесь — рабочий случай,
    а не исключительный. Ответ на него — отказ в допуске, а не 500.
    """
    if isinstance(solution_rules, SolutionRules):
        return solution_rules
    if not isinstance(solution_rules, dict):
        return None
    try:
        return SolutionRules.model_validate(solution_rules)
    except ValidationError:
        logger.warning(
            "tsk-605: solution_rules не разбирается — задание к машинной "
            "проверке не допущено",
            exc_info=True,
        )
        return None
