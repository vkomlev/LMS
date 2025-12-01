from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Set

from app.schemas.task_content import TaskContent
from app.schemas.solution_rules import SolutionRules
from app.schemas.checking import (
    StudentAnswer,
    StudentResponse,
    CheckResult,
    CheckResultDetails,
    CheckFeedback,
    TaskWithAnswer,
)


class CheckingService:
    """
    Сервис проверки ответов по JSON-описанию задания и правил.

    Задачи сервиса:
    - принять TaskContent, SolutionRules и ответ ученика;
    - в зависимости от типа задачи (SC/MC/SA/SA_COM/TA) применить корректный алгоритм;
    - вернуть нормализованный результат проверки (CheckResult), который можно
      сохранять в БД, отправлять по API и использовать в аналитике.

    Важно:
    - сервис не знает ни про FastAPI, ни про SQLAlchemy, ни про HTTP-запросы;
    - взаимодействует только через Pydantic-схемы из слоя app.schemas.
    """

    # ---------- Публичный интерфейс ----------

    def check_answer(
        self,
        task_content: TaskContent,
        rules: SolutionRules,
        answer: StudentAnswer,
    ) -> CheckResult:
        """
        Проверить один ответ ученика для конкретного задания.

        :param task_content: JSON-описание задания (как хранится в tasks.task_content).
        :param rules: JSON-правила проверки (tasks.solution_rules).
        :param answer: Ответ ученика в нормализованном виде.
        :return: Результат проверки (баллы, правильность, детали).
        :raises ValueError: при несоответствии типа задачи и типа ответа
                            или при некорректной структуре ответа.
        """
        if answer.type != task_content.type:
            raise ValueError(
                f"Тип ответа '{answer.type}' не совпадает с типом задачи '{task_content.type}'."
            )

        response: StudentResponse = answer.response

        # Диспетчеризация по типу задачи
        if task_content.type == "SC":
            result = self._check_single_choice(task_content, rules, response)
        elif task_content.type == "MC":
            result = self._check_multiple_choice(task_content, rules, response)
        elif task_content.type in ("SA", "SA_COM"):
            result = self._check_short_answer(task_content, rules, response)
        elif task_content.type == "TA":
            result = self._check_text_answer(task_content, rules, response)
        else:
            # На случай, если в будущем появится новый тип и его забудут реализовать.
            raise ValueError(f"Неподдерживаемый тип задачи: {task_content.type}")

        return result
    
    def check_many(self, items: list[TaskWithAnswer]) -> list[CheckResult]:
        """
        Пакетная проверка нескольких задач в stateless-режиме.

        :param items: Список структур TaskWithAnswer (описание задания + правила + ответ).
        :return: Список результатов проверки в том же порядке, что и входные элементы.
        """
        results: list[CheckResult] = []
        for item in items:
            result = self.check_answer(
                task_content=item.task_content,
                rules=item.solution_rules,
                answer=item.answer,
            )
            results.append(result)
        return results

    # ---------- Внутренние методы по типам задач ----------

    def _check_single_choice(
        self,
        task_content: TaskContent,
        rules: SolutionRules,
        response: StudentResponse,
    ) -> CheckResult:
        """
        Проверка задачи с одиночным выбором (SC).

        Логика:
        - считается корректным выбор одного варианта;
        - если выбранный вариант входит в correct_options → полный балл;
        - иначе → 0 баллов.
        """
        max_score = rules.max_score
        selected = (response.selected_option_ids or [])[:1]  # берём максимум один
        user_option = selected[0] if selected else None

        correct_set: Set[str] = set(rules.correct_options)

        is_correct: Optional[bool]
        score: int

        if not user_option:
            is_correct = False
            score = 0
        else:
            if user_option in correct_set:
                is_correct = True
                score = max_score
            else:
                is_correct = False
                score = 0

        details = CheckResultDetails(
            correct_options=list(correct_set) if correct_set else None,
            user_options=[user_option] if user_option else [],
            matched_short_answer=None,
            rubric_scores=None,
        )

        return CheckResult(
            is_correct=is_correct,
            score=score,
            max_score=max_score,
            details=details,
            feedback=None,
        )

    def _check_multiple_choice(
        self,
        task_content: TaskContent,
        rules: SolutionRules,
        response: StudentResponse,
    ) -> CheckResult:
        """
        Проверка задачи с множественным выбором (MC).

        Поддерживаем режимы:
        - all_or_nothing:
            • если множество выбранных вариантов == множеству правильных → полный балл;
            • иначе → 0 (с учётом возможных штрафов).
        - partial:
            • если есть partial_rules → сначала пытаемся найти точное правило;
            • если правило не найдено → считаем долю правильно выбранных вариантов
              и умножаем её на max_score, затем применяем штрафы и округляем;
        - custom:
            • используем только partial_rules (если ни одно не подошло → 0 баллов).

        В обоих случаях учитываем penalties.extra_wrong_mc (штраф за лишние неверные варианты).
        """
        max_score = rules.max_score
        correct_set: Set[str] = set(rules.correct_options)
        selected_set: Set[str] = set(response.selected_option_ids or [])

        # Для SC/MC всегда пытаемся вернуть булеву правильность
        is_correct: Optional[bool] = None
        score: int = 0

        # Шаг 1. Попробовать применить partial_rules (для partial/custom)
        def score_from_partial_rules() -> Optional[int]:
            for rule in rules.partial_rules:
                if set(rule.selected) == selected_set:
                    return rule.score
            return None

        if rules.scoring_mode == "all_or_nothing":
            # Полный балл только при точном совпадении
            if selected_set == correct_set and correct_set:
                score = max_score
                is_correct = True
            else:
                score = 0
                is_correct = False

        elif rules.scoring_mode in ("partial", "custom"):
            # Сначала пробуем явно заданные правила
            matched_score = score_from_partial_rules()
            if matched_score is not None:
                score = matched_score
            elif rules.scoring_mode == "partial":
                # Если partial_rules не заданы, делаем "разумное" частичное оценивание
                if not correct_set:
                    score = 0
                else:
                    correct_selected = len(selected_set & correct_set)
                    wrong_selected = len(selected_set - correct_set)

                    # Базовый балл за долю правильно выбранных вариантов
                    base_score = max_score * (correct_selected / len(correct_set))

                    # Применяем штраф за лишние неверные варианты
                    penalty_total = rules.penalties.extra_wrong_mc * wrong_selected
                    raw_score = base_score - penalty_total

                    score = self._normalize_score(raw_score, max_score)
            else:
                # scoring_mode == "custom" и partial_rules не подошли → 0 баллов
                score = 0

            # Для MC считаем ответ "правильным", если набран полный балл
            is_correct = score >= max_score

        else:
            raise ValueError(f"Неподдерживаемый режим оценивания: {rules.scoring_mode}")

        details = CheckResultDetails(
            correct_options=list(correct_set) if correct_set else None,
            user_options=list(selected_set),
            matched_short_answer=None,
            rubric_scores=None,
        )

        return CheckResult(
            is_correct=is_correct,
            score=score,
            max_score=max_score,
            details=details,
            feedback=None,
        )

    def _check_short_answer(
        self,
        task_content: TaskContent,
        rules: SolutionRules,
        response: StudentResponse,
    ) -> CheckResult:
        """
        Проверка задач с коротким ответом (SA/SA_COM).

        Поддерживаем:
        - список accepted_answers (значение + балл);
        - цепочку нормализации строки;
        - опциональную проверку по регулярному выражению.
        """
        max_score = rules.max_score
        value_raw = response.value or ""

        if not rules.short_answer:
            # Правила не заданы — формально считаем, что автоматически проверить нельзя.
            return CheckResult(
                is_correct=None,
                score=0,
                max_score=max_score,
                details=CheckResultDetails(
                    correct_options=None,
                    user_options=None,
                    matched_short_answer=None,
                    rubric_scores=None,
                ),
                feedback=CheckFeedback(
                    general="Автоматические правила для проверки короткого ответа не заданы.",
                    by_option=None,
                ),
            )

        normalized_value = self._normalize_text(
            value_raw,
            steps=rules.short_answer.normalization,
        )

        matched_value: Optional[str] = None
        score = 0

        # Шаг 1. Проверка по accepted_answers
        for accepted in rules.short_answer.accepted_answers:
            normalized_accepted = self._normalize_text(
                accepted.value,
                steps=rules.short_answer.normalization,
            )
            if normalized_accepted == normalized_value:
                # Берём максимальный возможный балл среди всех совпадений
                if accepted.score > score:
                    score = accepted.score
                    matched_value = accepted.value

        # Шаг 2. Проверка по regex (если включена)
        if score == 0 and rules.short_answer.use_regex and rules.short_answer.regex:
            pattern = re.compile(rules.short_answer.regex)
            if pattern.fullmatch(normalized_value):
                score = max_score
                matched_value = "<regex>"

        # is_correct — если набран максимальный балл
        is_correct: Optional[bool] = score >= max_score if max_score > 0 else None

        details = CheckResultDetails(
            correct_options=None,
            user_options=None,
            matched_short_answer=matched_value,
            rubric_scores=None,
        )

        return CheckResult(
            is_correct=is_correct,
            score=score,
            max_score=max_score,
            details=details,
            feedback=None,
        )

    def _check_text_answer(
        self,
        task_content: TaskContent,
        rules: SolutionRules,
        response: StudentResponse,
    ) -> CheckResult:
        """
        Проверка развёрнутого ответа (TA).

        По умолчанию:
        - если text_answer.auto_check == False → автоматом не проверяем:
            • is_correct = None;
            • score = 0;
        - rubrics (критерии) возвращаем в details.rubric_scores как шаблон
          для ручной проверки (оценки преподавателем).

        Если в будущем появится реальная автопроверка эссе,
        она также будет инкапсулирована здесь.
        """
        max_score = rules.max_score

        rubric_scores = None
        if rules.text_answer and rules.text_answer.rubric:
            # Готовим "пустые" оценки по критериям — чтобы фронт видел структуру.
            rubric_scores = [
                {"id": item.id, "title": item.title, "max_score": item.max_score, "score": None}
                for item in rules.text_answer.rubric
            ]

        # Автопроверка развёрнутых ответов пока не реализована —
        # оцениваем только вручную.
        result = CheckResult(
            is_correct=None,
            score=0,
            max_score=max_score,
            details=CheckResultDetails(
                correct_options=None,
                user_options=None,
                matched_short_answer=None,
                rubric_scores=rubric_scores,
            ),
            feedback=CheckFeedback(
                general="Ответ требует ручной проверки преподавателем.",
                by_option=None,
            ),
        )

        return result

    # ---------- Вспомогательные утилиты ----------

    @staticmethod
    def _normalize_score(raw_score: float, max_score: int) -> int:
        """
        Нормализовать числовой балл:
        - округлить до ближайшего целого;
        - ограничить [0, max_score].

        :param raw_score: исходный (возможно, дробный или отрицательный) балл.
        :param max_score: максимальный возможный балл за задачу.
        :return: целочисленный балл в допустимых пределах.
        """
        score_int = int(round(raw_score))
        if score_int < 0:
            return 0
        if score_int > max_score:
            return max_score
        return score_int

    @staticmethod
    def _normalize_text(value: str, steps: Iterable[str]) -> str:
        """
        Нормализовать строку согласно заданному списку шагов.

        Поддерживаемые шаги:
        - 'trim'            — обрезать пробелы по краям;
        - 'lower'           — привести к нижнему регистру;
        - 'collapse_spaces' — заменить последовательности пробелов на один пробел.

        :param value: исходная строка.
        :param steps: список шагов нормализации.
        :return: нормализованная строка.
        """
        result = value

        for step in steps:
            if step == "trim":
                result = result.strip()
            elif step == "lower":
                result = result.lower()
            elif step == "collapse_spaces":
                # Заменяем все последовательности whitespace одним пробелом
                result = re.sub(r"\s+", " ", result)
            else:
                # Неизвестные шаги тихо игнорируем, чтобы не ломать старые данные.
                continue

        return result
