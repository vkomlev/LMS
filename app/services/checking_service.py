# app/services/checking_service.py

from __future__ import annotations

from typing import List, Optional, Set, Dict

from app.schemas.task_content import TaskContent, TaskType
from app.schemas.solution_rules import SolutionRules, ShortAnswerRules
from app.schemas.checking import (
    StudentAnswer,
    CheckResult,
    CheckResultDetails,
    CheckFeedback,
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
        # Проверяем наличие ответа перед валидацией
        selected = answer.response.selected_option_ids or []
        missing_answer = len(selected) == 0
        
        # Для SC считаем, что должен быть ровно 1 выбранный вариант.
        if len(selected) != 1:
            if missing_answer:
                # Если ответ отсутствует, применяем штраф и возвращаем результат
                penalty = solution_rules.penalties.missing_answer if solution_rules.penalties else 0
                final_score = max(0, 0 - penalty)
                
                details = CheckResultDetails(
                    correct_options=list(solution_rules.correct_options or []) or None,
                    user_options=[],
                )
                
                feedback = self._generate_feedback_sc(
                    task_content=task_content,
                    is_correct=False,
                    user_set=set(),
                    correct_set=set(solution_rules.correct_options or []),
                )
                
                return CheckResult(
                    is_correct=False,
                    score=final_score,
                    max_score=solution_rules.max_score,
                    details=details,
                    feedback=feedback,
                )
            else:
                raise DomainError(
                    detail="Для задач типа SC должен быть выбран ровно один вариант.",
                    status_code=400,
                    payload={"selected_option_ids": selected},
                )

        correct_set: Set[str] = set(solution_rules.correct_options or [])
        user_set: Set[str] = set(selected)

        # Обработка различных режимов оценивания
        if solution_rules.scoring_mode == "custom":
            base_score, is_correct = self._apply_custom_scoring(
                solution_rules,
                user_set,
                correct_set,
                missing_answer,
            )
        else:
            # all_or_nothing (по умолчанию для SC)
            is_correct = user_set == correct_set and len(correct_set) == 1
            base_score = solution_rules.max_score if is_correct else 0
        
        # Применение штрафов
        penalty = 0
        if not is_correct and solution_rules.penalties:
            penalty += solution_rules.penalties.wrong_answer
        
        final_score = max(0, base_score - penalty)

        details = CheckResultDetails(
            correct_options=list(correct_set) or None,
            user_options=list(user_set),
        )

        # Генерация обратной связи
        feedback = self._generate_feedback_sc(
            task_content=task_content,
            is_correct=is_correct,
            user_set=user_set,
            correct_set=correct_set,
        )

        return CheckResult(
            is_correct=is_correct,
            score=final_score,
            max_score=solution_rules.max_score,
            details=details,
            feedback=feedback,
        )

    # ---------- Проверка MC ----------

    def _check_multiple_choice(
        self,
        task_content: TaskContent,
        solution_rules: SolutionRules,
        answer: StudentAnswer,
    ) -> CheckResult:
        # Проверяем наличие ответа
        selected = answer.response.selected_option_ids or []
        missing_answer = len(selected) == 0
        
        correct_set: Set[str] = set(solution_rules.correct_options or [])
        user_set: Set[str] = set(selected)

        # all_or_nothing: либо все и только правильные варианты → полный балл
        if solution_rules.scoring_mode == "all_or_nothing":
            is_correct = user_set == correct_set and bool(correct_set)
            base_score = solution_rules.max_score if is_correct else 0

        # partial: сначала пытаемся применить явные partial_rules,
        # если нет совпадения — даём простой пропорциональный балл.
        elif solution_rules.scoring_mode == "partial":
            base_score = self._apply_partial_rules(solution_rules, user_set)

            if base_score is None:
                # Пропорциональный вариант: только за пересечение с правильными.
                if not correct_set:
                    base_score = 0
                else:
                    num_correct = len(correct_set & user_set)
                    base_score = int(
                        solution_rules.max_score * num_correct / len(correct_set)
                    )
            is_correct = base_score == solution_rules.max_score

        # custom: используем custom_scoring_config для расширенной логики
        else:  # "custom"
            base_score, is_correct = self._apply_custom_scoring(
                solution_rules,
                user_set,
                correct_set,
                missing_answer,
            )

        # Применение штрафов
        penalty = 0
        if solution_rules.penalties:
            if missing_answer:
                penalty += solution_rules.penalties.missing_answer
            elif not is_correct:
                penalty += solution_rules.penalties.wrong_answer
                
                # Штраф за лишние неверные варианты в MC
                wrong_selected = user_set - correct_set
                if wrong_selected:
                    penalty += solution_rules.penalties.extra_wrong_mc * len(wrong_selected)
        
        # Не даём уйти в отрицательные или сверх max_score
        final_score = max(0, min(base_score - penalty, solution_rules.max_score))

        details = CheckResultDetails(
            correct_options=list(correct_set) or None,
            user_options=list(user_set),
        )

        # Генерация обратной связи
        feedback = self._generate_feedback_mc(
            task_content=task_content,
            is_correct=is_correct,
            user_set=user_set,
            correct_set=correct_set,
        )

        return CheckResult(
            is_correct=is_correct,
            score=final_score,
            max_score=solution_rules.max_score,
            details=details,
            feedback=feedback,
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

    def _apply_custom_scoring(
        self,
        solution_rules: SolutionRules,
        user_set: Set[str],
        correct_set: Set[str],
        missing_answer: bool,
    ) -> tuple[int, bool]:
        """
        Применяет кастомную логику оценивания на основе custom_scoring_config.
        
        Поддерживаемые форматы конфигурации:
        1. Правила на основе условий:
           {"rules": [{"condition": "all_correct", "score": 10}, ...]}
        2. Формула:
           {"formula": "score = correct_count * 2", "min_score": 0, "max_score": 20}
        3. Пропорциональное оценивание с коэффициентом:
           {"coefficient": 2.0, "min_score": 0}
        
        Если конфигурация не задана или не распознана, используется all_or_nothing.
        
        Returns:
            tuple[int, bool]: (base_score, is_correct)
        """
        config = solution_rules.custom_scoring_config
        
        # Если конфигурация не задана, используем all_or_nothing
        if not config:
            is_correct = user_set == correct_set and bool(correct_set)
            base_score = solution_rules.max_score if is_correct else 0
            return base_score, is_correct
        
        # Убеждаемся, что config - это словарь
        if not isinstance(config, dict):
            # Если config не словарь, используем all_or_nothing
            is_correct = user_set == correct_set and bool(correct_set)
            base_score = solution_rules.max_score if is_correct else 0
            return base_score, is_correct
        
        # Обработка отсутствующего ответа
        if missing_answer:
            return 0, False
        
        # Формат 1: Правила на основе условий
        if "rules" in config and isinstance(config.get("rules"), list):
            for rule in config["rules"]:
                condition = rule.get("condition")
                score = rule.get("score", 0)
                
                if condition == "all_correct":
                    if user_set == correct_set and bool(correct_set):
                        return min(score, solution_rules.max_score), score == solution_rules.max_score
                elif condition == "partial":
                    if user_set & correct_set:  # Есть хотя бы один правильный
                        return min(score, solution_rules.max_score), False
                elif condition == "no_wrong":
                    wrong_selected = user_set - correct_set
                    if not wrong_selected and user_set & correct_set:
                        return min(score, solution_rules.max_score), False
        
        # Формат 2: Формула (упрощенная версия)
        if "formula" in config:
            # Поддерживаем простые формулы вида "score = correct_count * multiplier"
            formula = config["formula"]
            correct_count = len(correct_set & user_set)
            
            if "correct_count" in formula:
                # Извлекаем множитель из формулы (упрощенно)
                try:
                    # Ищем паттерн "correct_count * N" или "N * correct_count"
                    import re
                    match = re.search(r'correct_count\s*\*\s*(\d+(?:\.\d+)?)', formula)
                    if match:
                        multiplier = float(match.group(1))
                        base_score = int(correct_count * multiplier)
                    else:
                        # По умолчанию: correct_count * (max_score / total_correct)
                        if correct_set:
                            base_score = int(solution_rules.max_score * correct_count / len(correct_set))
                        else:
                            base_score = 0
                except Exception:
                    base_score = 0
            else:
                base_score = 0
            
            min_score = config.get("min_score", 0)
            max_score = config.get("max_score", solution_rules.max_score)
            base_score = max(min_score, min(base_score, max_score))
            is_correct = base_score == solution_rules.max_score
            return base_score, is_correct
        
        # Формат 3: Пропорциональное оценивание с коэффициентом
        if "coefficient" in config:
            coefficient = float(config.get("coefficient", 1.0))
            correct_count = len(correct_set & user_set)
            if correct_set:
                base_score = int(solution_rules.max_score * correct_count / len(correct_set) * coefficient)
            else:
                base_score = 0
            
            min_score = config.get("min_score", 0)
            base_score = max(min_score, min(base_score, solution_rules.max_score))
            is_correct = base_score == solution_rules.max_score
            return base_score, is_correct
        
        # Если формат не распознан, используем all_or_nothing
        is_correct = user_set == correct_set and bool(correct_set)
        base_score = solution_rules.max_score if is_correct else 0
        return base_score, is_correct

    # ---------- Проверка SA / SA_COM ----------

    def _check_short_answer(
        self,
        task_content: TaskContent,
        solution_rules: SolutionRules,
        answer: StudentAnswer,
    ) -> CheckResult:
        # Проверяем наличие ответа
        value_raw = answer.response.value or ""
        missing_answer = not value_raw or value_raw.strip() == ""
        
        rules: Optional[ShortAnswerRules] = solution_rules.short_answer

        if not rules:
            # Нечем проверять — считаем, что нужна ручная проверка.
            penalty = solution_rules.penalties.missing_answer if missing_answer else 0
            final_score = max(0, 0 - penalty)
            
            return CheckResult(
                is_correct=None,
                score=final_score,
                max_score=solution_rules.max_score,
                details=None,
                feedback=None,
            )

        if missing_answer:
            # Если ответ отсутствует, применяем штраф и возвращаем результат
            penalty = solution_rules.penalties.missing_answer
            final_score = max(0, 0 - penalty)
            
            details = CheckResultDetails(
                correct_options=None,
                user_options=None,
                matched_short_answer=None,
            )
            
            feedback = self._generate_feedback_sa(
                is_correct=False,
                base_score=0,
                max_score=solution_rules.max_score,
            )
            
            return CheckResult(
                is_correct=False,
                score=final_score,
                max_score=solution_rules.max_score,
                details=details,
                feedback=feedback,
            )

        value_norm = self._normalize_text(value_raw, rules.normalization)

        matched_value: Optional[str] = None
        base_score = 0

        # Если включён regex — пробуем сперва его
        if rules.use_regex and rules.regex:
            import re

            try:
                pattern = re.compile(rules.regex)
                if pattern.fullmatch(value_norm):
                    base_score = solution_rules.max_score
                    matched_value = value_raw
            except re.error:
                # Невалидное регулярное выражение — игнорируем regex,
                # оставляем только accepted_answers.
                pass

        # Если regex не дал полного балла — проверяем accepted_answers
        if base_score < solution_rules.max_score:
            for accepted in rules.accepted_answers:
                accepted_norm = self._normalize_text(
                    accepted.value,
                    rules.normalization,
                )
                if value_norm == accepted_norm:
                    # Берём максимальный из найденных вариантов (на случай нескольких правил)
                    if accepted.score > base_score:
                        base_score = accepted.score
                        matched_value = accepted.value

        is_correct = base_score == solution_rules.max_score if base_score > 0 else False
        
        # Применение штрафов
        penalty = 0
        if solution_rules.penalties:
            if not is_correct and base_score == 0:
                penalty += solution_rules.penalties.wrong_answer
        
        final_score = max(0, base_score - penalty)

        details = CheckResultDetails(
            correct_options=None,
            user_options=None,
            matched_short_answer=matched_value,
        )

        # Генерация обратной связи
        feedback = self._generate_feedback_sa(
            is_correct=is_correct,
            base_score=base_score,
            max_score=solution_rules.max_score,
        )

        return CheckResult(
            is_correct=is_correct,
            score=final_score,
            max_score=solution_rules.max_score,
            details=details,
            feedback=feedback,
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

    # ---------- Генерация обратной связи ----------

    def _generate_feedback_sc(
        self,
        task_content: TaskContent,
        is_correct: bool,
        user_set: Set[str],
        correct_set: Set[str],
    ) -> Optional[CheckFeedback]:
        """
        Генерирует обратную связь для задач типа SC.
        """
        if not task_content.options:
            return None

        by_option: Dict[str, str] = {}
        general: Optional[str] = None

        # Обратная связь по выбранным вариантам
        for option in task_content.options:
            if option.id in user_set:
                if option.id in correct_set:
                    # Правильный вариант
                    if option.explanation:
                        by_option[option.id] = option.explanation
                    else:
                        by_option[option.id] = "Правильный вариант!"
                else:
                    # Неправильный вариант
                    if option.explanation:
                        by_option[option.id] = option.explanation
                    else:
                        by_option[option.id] = "Этот вариант неверен."

        # Общая обратная связь
        if is_correct:
            general = "Отлично! Вы выбрали правильный ответ."
        else:
            general = "Ответ неверен. Обратите внимание на объяснения к вариантам."

        return CheckFeedback(
            general=general if by_option or general else None,
            by_option=by_option if by_option else None,
        )

    def _generate_feedback_mc(
        self,
        task_content: TaskContent,
        is_correct: bool,
        user_set: Set[str],
        correct_set: Set[str],
    ) -> Optional[CheckFeedback]:
        """
        Генерирует обратную связь для задач типа MC.
        """
        if not task_content.options:
            return None

        by_option: Dict[str, str] = {}
        general: Optional[str] = None

        # Обратная связь по всем вариантам, которые выбрал пользователь
        for option in task_content.options:
            if option.id in user_set:
                if option.id in correct_set:
                    # Правильный вариант
                    if option.explanation:
                        by_option[option.id] = option.explanation
                    else:
                        by_option[option.id] = "Правильный вариант!"
                else:
                    # Неправильный вариант
                    if option.explanation:
                        by_option[option.id] = option.explanation
                    else:
                        by_option[option.id] = "Этот вариант неверен."

        # Общая обратная связь
        if is_correct:
            general = "Отлично! Вы выбрали все правильные варианты."
        else:
            correct_count = len(correct_set & user_set)
            total_correct = len(correct_set)
            if correct_count > 0:
                general = f"Вы выбрали {correct_count} из {total_correct} правильных вариантов."
            else:
                general = "Ответ неверен. Обратите внимание на объяснения к вариантам."

        return CheckFeedback(
            general=general if by_option or general else None,
            by_option=by_option if by_option else None,
        )

    def _generate_feedback_sa(
        self,
        is_correct: bool,
        base_score: int,
        max_score: int,
    ) -> Optional[CheckFeedback]:
        """
        Генерирует обратную связь для задач типа SA/SA_COM.
        """
        if is_correct:
            general = "Отлично! Ваш ответ правильный."
        elif base_score > 0:
            general = f"Ваш ответ частично правильный. Набрано {base_score} из {max_score} баллов."
        else:
            general = "Ответ неверен. Попробуйте еще раз."

        return CheckFeedback(
            general=general,
            by_option=None,
        )

    def _generate_feedback_ta(
        self,
        task_content: TaskContent,
        solution_rules: SolutionRules,
    ) -> Optional[CheckFeedback]:
        """
        Генерирует обратную связь для задач типа TA.
        """
        general = "Ваш ответ будет проверен вручную."
        
        # Добавляем информацию о рубриках, если они есть
        if solution_rules.text_answer and solution_rules.text_answer.rubric:
            rubric_info = ", ".join([r.title for r in solution_rules.text_answer.rubric])
            general += f" Критерии оценивания: {rubric_info}."

        return CheckFeedback(
            general=general,
            by_option=None,
        )

    # ---------- Проверка TA ----------

    def _check_text_answer(
        self,
        task_content: TaskContent,
        solution_rules: SolutionRules,
        answer: StudentAnswer,
    ) -> CheckResult:
        # Проверяем наличие ответа
        text = answer.response.text or ""
        missing_answer = not text or text.strip() == ""

        # Сейчас автопроверку сочинений/эссе не делаем.
        # Возвращаем шаблон, который фронт/методист смогут дооценить вручную.
        
        # Применение штрафов за отсутствие ответа
        penalty = 0
        if solution_rules.penalties and missing_answer:
            penalty = solution_rules.penalties.missing_answer
        
        base_score = 0
        final_score = max(0, base_score - penalty)
        
        details = CheckResultDetails(
            rubric_scores=None,
        )

        # Генерация обратной связи
        feedback = self._generate_feedback_ta(
            task_content=task_content,
            solution_rules=solution_rules,
        )

        return CheckResult(
            is_correct=None,
            score=final_score,
            max_score=solution_rules.max_score,
            details=details,
            feedback=feedback,
        )
