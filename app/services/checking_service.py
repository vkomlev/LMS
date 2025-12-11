# app/services/checking_service.py

from __future__ import annotations

from typing import List, Optional, Set

from app.schemas.task_content import TaskContent, TaskType
from app.schemas.solution_rules import SolutionRules, ShortAnswerRules
from app.schemas.checking import (
    StudentAnswer,
    CheckResult,
    CheckResultDetails,
)
from app.utils.exceptions import DomainError


class CheckingService:
    """
    Сервис статeless-проверки ответов.

    Задача сервиса — по JSON-описанию задачи (TaskContent),
    правилам проверки (SolutionRules) и ответу ученика (StudentAnswer)
    вернуть CheckResult без обращения к БД и FastAPI.
    """

    def check_task(
        self,
        task_content: TaskContent,
        solution_rules: SolutionRules,
        answer: StudentAnswer,
    ) -> CheckResult:
        """
        Проверяет один ответ ученика на одну задачу.

        Args:
            task_content: JSON-описание задания (как в tasks.task_content).
            solution_rules: JSON-правила проверки (tasks.solution_rules).
            answer: Ответ ученика.

        Returns:
            CheckResult с баллом, максимумом и деталями.

        Raises:
            DomainError: при некорректной структуре ответа
                         или несовпадении типов задачи и ответа.
        """
        if task_content.type != answer.type:
            raise DomainError(
                detail=(
                    f"Тип ответа ({answer.type}) не совпадает с типом задачи "
                    f"({task_content.type})."
                ),
                status_code=400,
                payload={"task_type": task_content.type, "answer_type": answer.type},
            )

        task_type: TaskType = task_content.type

        if task_type == "SC":
            return self._check_single_choice(task_content, solution_rules, answer)
        if task_type == "MC":
            return self._check_multiple_choice(task_content, solution_rules, answer)
        if task_type in ("SA", "SA_COM"):
            return self._check_short_answer(task_content, solution_rules, answer)
        if task_type == "TA":
            return self._check_text_answer(task_content, solution_rules, answer)

        # На случай будущих расширений типов:
        raise DomainError(
            detail=f"Неподдерживаемый тип задачи: {task_type}",
            status_code=400,
            payload={"task_type": task_type},
        )

    # ---------- Вспомогательные методы ----------

    @staticmethod
    def _ensure_selected_options(answer: StudentAnswer) -> List[str]:
        selected = answer.response.selected_option_ids or []
        if not selected:
            raise DomainError(
                detail="Для задач с выбором нужно передать selected_option_ids.",
                status_code=400,
            )
        return selected

    @staticmethod
    def _ensure_value(answer: StudentAnswer) -> str:
        value = answer.response.value
        if value is None or value == "":
            raise DomainError(
                detail="Для задач с коротким ответом нужно передать поле 'value'.",
                status_code=400,
            )
        return value

    @staticmethod
    def _ensure_text(answer: StudentAnswer) -> str:
        text = answer.response.text
        if text is None or text == "":
            raise DomainError(
                detail="Для задач с развёрнутым ответом нужно передать поле 'text'.",
                status_code=400,
            )
        return text

    # ---------- Проверка SC ----------

    def _check_single_choice(
        self,
        task_content: TaskContent,
        solution_rules: SolutionRules,
        answer: StudentAnswer,
    ) -> CheckResult:
        selected = self._ensure_selected_options(answer)
        # Для SC считаем, что должен быть ровно 1 выбранный вариант.
        if len(selected) != 1:
            raise DomainError(
                detail="Для задач типа SC должен быть выбран ровно один вариант.",
                status_code=400,
                payload={"selected_option_ids": selected},
            )

        correct_set: Set[str] = set(solution_rules.correct_options or [])
        user_set: Set[str] = set(selected)

        is_correct = user_set == correct_set and len(correct_set) == 1
        score = solution_rules.max_score if is_correct else 0

        details = CheckResultDetails(
            correct_options=list(correct_set) or None,
            user_options=list(user_set),
        )

        return CheckResult(
            is_correct=is_correct,
            score=score,
            max_score=solution_rules.max_score,
            details=details,
            feedback=None,
        )

    # ---------- Проверка MC ----------

    def _check_multiple_choice(
        self,
        task_content: TaskContent,
        solution_rules: SolutionRules,
        answer: StudentAnswer,
    ) -> CheckResult:
        selected = self._ensure_selected_options(answer)
        correct_set: Set[str] = set(solution_rules.correct_options or [])
        user_set: Set[str] = set(selected)

        # all_or_nothing: либо все и только правильные варианты → полный балл
        if solution_rules.scoring_mode == "all_or_nothing":
            is_correct = user_set == correct_set and bool(correct_set)
            score = solution_rules.max_score if is_correct else 0

        # partial: сначала пытаемся применить явные partial_rules,
        # если нет совпадения — даём простой пропорциональный балл.
        elif solution_rules.scoring_mode == "partial":
            score = self._apply_partial_rules(solution_rules, user_set)

            if score is None:
                # Пропорциональный вариант: только за пересечение с правильными.
                if not correct_set:
                    score = 0
                else:
                    num_correct = len(correct_set & user_set)
                    score = int(
                        solution_rules.max_score * num_correct / len(correct_set)
                    )
            is_correct = score == solution_rules.max_score

        # custom: здесь можно будет подключать внешний движок;
        # сейчас считаем как all_or_nothing, чтобы не ломать API.
        else:  # "custom"
            is_correct = user_set == correct_set and bool(correct_set)
            score = solution_rules.max_score if is_correct else 0

        # Не даём уйти в отрицательные или сверх max_score
        score = max(0, min(score, solution_rules.max_score))

        details = CheckResultDetails(
            correct_options=list(correct_set) or None,
            user_options=list(user_set),
        )

        return CheckResult(
            is_correct=is_correct,
            score=score,
            max_score=solution_rules.max_score,
            details=details,
            feedback=None,
        )

    @staticmethod
    def _apply_partial_rules(
        solution_rules: SolutionRules,
        user_set: Set[str],
    ) -> Optional[int]:
        """
        Пытается применить одно из явно заданных partial_rules.
        Возвращает score или None, если ни одно правило не подошло.
        """
        for rule in solution_rules.partial_rules or []:
            if set(rule.selected) == user_set:
                return rule.score
        return None

    # ---------- Проверка SA / SA_COM ----------

    def _check_short_answer(
        self,
        task_content: TaskContent,
        solution_rules: SolutionRules,
        answer: StudentAnswer,
    ) -> CheckResult:
        value_raw = self._ensure_value(answer)
        rules: Optional[ShortAnswerRules] = solution_rules.short_answer

        if not rules:
            # Нечем проверять — считаем, что нужна ручная проверка.
            return CheckResult(
                is_correct=None,
                score=0,
                max_score=solution_rules.max_score,
                details=None,
                feedback=None,
            )

        value_norm = self._normalize_text(value_raw, rules.normalization)

        matched_value: Optional[str] = None
        score = 0

        # Если включён regex — пробуем сперва его
        if rules.use_regex and rules.regex:
            import re

            try:
                pattern = re.compile(rules.regex)
                if pattern.fullmatch(value_norm):
                    score = solution_rules.max_score
                    matched_value = value_raw
            except re.error:
                # Невалидное регулярное выражение — игнорируем regex,
                # оставляем только accepted_answers.
                pass

        # Если regex не дал полного балла — проверяем accepted_answers
        if score < solution_rules.max_score:
            for accepted in rules.accepted_answers:
                accepted_norm = self._normalize_text(
                    accepted.value,
                    rules.normalization,
                )
                if value_norm == accepted_norm:
                    # Берём максимальный из найденных вариантов (на случай нескольких правил)
                    if accepted.score > score:
                        score = accepted.score
                        matched_value = accepted.value

        is_correct = score == solution_rules.max_score if score > 0 else False

        details = CheckResultDetails(
            correct_options=None,
            user_options=None,
            matched_short_answer=matched_value,
        )

        return CheckResult(
            is_correct=is_correct,
            score=score,
            max_score=solution_rules.max_score,
            details=details,
            feedback=None,
        )

    @staticmethod
    def _normalize_text(value: str, steps: List[str]) -> str:
        """
        Простейшая нормализация строки по списку шагов из ShortAnswerRules.normalization.

        Поддерживаем базовые шаги:
        - 'trim'            → обрезка пробелов по краям;
        - 'lower'           → приведение к нижнему регистру;
        - 'collapse_spaces' → схлопывание подряд идущих пробелов в один.
        """
        result = value
        if "trim" in steps:
            result = result.strip()
        if "lower" in steps:
            result = result.lower()
        if "collapse_spaces" in steps:
            result = " ".join(result.split())
        return result

    # ---------- Проверка TA ----------

    def _check_text_answer(
        self,
        task_content: TaskContent,
        solution_rules: SolutionRules,
        answer: StudentAnswer,
    ) -> CheckResult:
        _ = self._ensure_text(answer)

        # Сейчас автопроверку сочинений/эссе не делаем.
        # Возвращаем шаблон, который фронт/методист смогут дооценить вручную.
        details = CheckResultDetails(
            rubric_scores=None,
        )

        return CheckResult(
            is_correct=None,
            score=0,
            max_score=solution_rules.max_score,
            details=details,
            feedback=None,
        )
