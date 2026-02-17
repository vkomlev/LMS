# app/services/sheets_parser_service.py

from __future__ import annotations

import re
import logging
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urlparse, parse_qs

from app.schemas.task_content import TaskContent, TaskOption, TaskType
from app.schemas.solution_rules import (
    SolutionRules,
    PenaltiesRules,
    ShortAnswerRules,
    ShortAnswerAccepted,
)
from app.core.config import Settings
from app.utils.exceptions import DomainError

logger = logging.getLogger("services.sheets_parser")


class SheetsParserService:
    """
    Сервис для парсинга данных из Google Sheets в структуры задач.
    
    Мигрировано из проекта QSMImport.
    Преобразует строки таблицы в TaskContent и SolutionRules.
    """

    def __init__(self, settings: Optional[Settings] = None):
        """
        Инициализация парсера.
        
        Args:
            settings: Настройки приложения (если None, создаются новые).
        """
        if settings is None:
            settings = Settings()
        
        self.settings = settings

    def extract_spreadsheet_id(self, spreadsheet_url: str) -> str:
        """
        Извлекает spreadsheet_id из URL Google Sheets.
        
        Args:
            spreadsheet_url: URL таблицы (полный или только ID).
        
        Returns:
            Spreadsheet ID.
        
        Raises:
            DomainError: если не удалось извлечь ID.
        """
        # Если это уже ID (нет слэшей и точек), возвращаем как есть
        if "/" not in spreadsheet_url and "." not in spreadsheet_url:
            return spreadsheet_url
        
        # Пытаемся извлечь из URL
        try:
            parsed = urlparse(spreadsheet_url)
            # Формат: https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit
            path_parts = parsed.path.split("/")
            if "d" in path_parts:
                idx = path_parts.index("d")
                if idx + 1 < len(path_parts):
                    return path_parts[idx + 1]
            
            # Альтернативный формат: ?id={SPREADSHEET_ID}
            query_params = parse_qs(parsed.query)
            if "id" in query_params:
                return query_params["id"][0]
            
            raise DomainError(
                detail=f"Не удалось извлечь spreadsheet_id из URL: {spreadsheet_url}",
                status_code=400,
            )
        except Exception as e:
            logger.exception("Ошибка при извлечении spreadsheet_id: %s", e)
            raise DomainError(
                detail=f"Ошибка при извлечении spreadsheet_id: {str(e)}",
                status_code=400,
            ) from e

    def parse_task_row(
        self,
        row: Dict[str, str],
        column_mapping: Optional[Dict[str, str]] = None,
        course_id: Optional[int] = None,
        difficulty_id: Optional[int] = None,
    ) -> Tuple[TaskContent, SolutionRules, Dict[str, Any]]:
        """
        Парсит строку таблицы в задачу.
        
        Args:
            row: Словарь с данными строки (ключи - названия колонок или индексы).
            column_mapping: Маппинг колонок на поля (если None, используется стандартный).
            course_id: ID курса (если указан).
            difficulty_id: ID сложности (если указан).
        
        Returns:
            Кортеж (TaskContent, SolutionRules, metadata).
            metadata содержит: external_uid, course_id, difficulty_id, max_score.
        
        Raises:
            DomainError: при ошибках парсинга.
        """
        if column_mapping is None:
            column_mapping = self._get_default_column_mapping()
        
        # Извлекаем основные поля
        task_type = self._get_field(row, column_mapping, "type", required=True)
        stem = self._get_field(row, column_mapping, "stem", required=True)
        external_uid = self._get_field(row, column_mapping, "external_uid", required=True)
        
        # Парсим тип задачи
        task_type_upper = task_type.upper().strip()
        if task_type_upper not in ("SC", "MC", "SA", "SA_COM", "TA"):
            raise DomainError(
                detail=f"Неподдерживаемый тип задачи: {task_type}",
                status_code=400,
            )
        
        task_type_parsed: TaskType = task_type_upper  # type: ignore
        
        # Создаем TaskContent
        task_content_data: Dict[str, Any] = {
            "type": task_type_parsed,
            "stem": stem,
        }
        
        # Опциональные поля
        if "course_uid" in column_mapping:
            course_uid = self._get_field(row, column_mapping, "course_uid", required=False)
            if course_uid:
                task_content_data["course_uid"] = course_uid

        if "code" in column_mapping:
            code = self._get_field(row, column_mapping, "code", required=False)
            if code:
                task_content_data["code"] = code
        
        if "title" in column_mapping:
            title = self._get_field(row, column_mapping, "title", required=False)
            if title:
                task_content_data["title"] = title
        
        if "prompt" in column_mapping:
            prompt = self._get_field(row, column_mapping, "prompt", required=False)
            if prompt:
                task_content_data["prompt"] = prompt
        
        # Парсим варианты ответа для SC/MC
        if task_type_parsed in ("SC", "MC"):
            options = self._parse_options(row, column_mapping)
            if not options:
                raise DomainError(
                    detail=f"Для задач типа {task_type_parsed} необходимо указать варианты ответа",
                    status_code=400,
                )
            task_content_data["options"] = options
        
        # Парсим входные данные (если есть)
        if self.settings.prepend_input_link:
            input_link = self._get_field(row, column_mapping, "input_link", required=False)
            if input_link:
                input_label = self.settings.input_link_label
                # Добавляем ссылку в начало stem
                task_content_data["stem"] = f"[{input_label}: {input_link}]\n\n{stem}"
        
        task_content = TaskContent.model_validate(task_content_data)
        
        # Создаем SolutionRules
        max_score = self._parse_max_score(row, column_mapping, task_type_parsed)
        correct_options = self._parse_correct_options(row, column_mapping, task_type_parsed)
        
        solution_rules_data: Dict[str, Any] = {
            "max_score": max_score,
            "scoring_mode": "all_or_nothing",
            "auto_check": True,
            "correct_options": correct_options,
            "penalties": PenaltiesRules(),  # Дефолтные значения
        }
        
        # Для SA/SA_COM парсим accepted_answers
        if task_type_parsed in ("SA", "SA_COM"):
            accepted_answers = self._parse_accepted_answers(row, column_mapping, max_score)
            if accepted_answers:
                solution_rules_data["short_answer"] = ShortAnswerRules(
                    accepted_answers=accepted_answers,
                    normalization=["trim", "lower"],
                )
        
        solution_rules = SolutionRules.model_validate(solution_rules_data)
        
        # Metadata
        metadata = {
            "external_uid": external_uid,
            "course_id": course_id,
            "difficulty_id": difficulty_id,
            "max_score": max_score,
        }
        
        return task_content, solution_rules, metadata

    def _get_default_column_mapping(self) -> Dict[str, str]:
        """
        Возвращает стандартный маппинг колонок.
        
        Returns:
            Словарь: название колонки -> поле задачи.
        """
        return {
            "external_uid": "external_uid",
            "type": "type",
            "stem": "stem",
            "course_uid": "course_uid",
            "code": "code",
            "title": "title",
            "prompt": "prompt",
            "options": "options",
            "correct_answer": "correct_answer",
            "max_score": "max_score",
            "input_link": "input_link",
            "accepted_answers": "accepted_answers",
        }

    def _get_field(
        self,
        row: Dict[str, str],
        column_mapping: Dict[str, str],
        field_name: str,
        required: bool = False,
    ) -> Optional[str]:
        """
        Извлекает значение поля из строки.
        
        Args:
            row: Словарь с данными строки.
            column_mapping: Маппинг колонок.
            field_name: Название поля.
            required: Обязательное ли поле.
        
        Returns:
            Значение поля или None.
        
        Raises:
            DomainError: если поле обязательное и отсутствует.
        """
        column_name = column_mapping.get(field_name)
        if not column_name:
            if required:
                raise DomainError(
                    detail=f"Колонка для поля '{field_name}' не указана в маппинге",
                    status_code=400,
                )
            return None
        
        value = row.get(column_name, "").strip()
        if required and not value:
            raise DomainError(
                detail=f"Обязательное поле '{field_name}' (колонка '{column_name}') пустое",
                status_code=400,
            )
        
        return value if value else None

    def _parse_options(
        self,
        row: Dict[str, str],
        column_mapping: Dict[str, str],
    ) -> List[TaskOption]:
        """
        Парсит варианты ответа из строки.
        
        Формат в колонке: "A: Вариант 1 | B: Вариант 2 | C: Вариант 3"
        или несколько колонок: options_A, options_B, options_C
        
        Args:
            row: Словарь с данными строки.
            column_mapping: Маппинг колонок.
        
        Returns:
            Список TaskOption.
        """
        options_column = column_mapping.get("options")
        if not options_column:
            return []
        
        options_text = row.get(options_column, "").strip()
        if not options_text:
            return []
        
        options: List[TaskOption] = []
        
        # Формат: "A: Текст варианта | B: Текст варианта 2"
        parts = options_text.split("|")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            # Ищем паттерн "ID: Текст"
            match = re.match(r"^([A-Z0-9]+):\s*(.+)$", part)
            if match:
                option_id = match.group(1).strip()
                option_text = match.group(2).strip()
                
                # Проверяем, есть ли объяснение (формат: "ID: Текст [Объяснение]")
                explanation_match = re.search(r"\[(.+)\]$", option_text)
                explanation = None
                if explanation_match:
                    explanation = explanation_match.group(1).strip()
                    option_text = option_text[:explanation_match.start()].strip()
                
                options.append(TaskOption(
                    id=option_id,
                    text=option_text,
                    explanation=explanation,
                    is_active=True,
                ))
        
        return options

    def _parse_correct_options(
        self,
        row: Dict[str, str],
        column_mapping: Dict[str, str],
        task_type: TaskType,
    ) -> List[str]:
        """
        Парсит правильные варианты ответа.
        
        Args:
            row: Словарь с данными строки.
            column_mapping: Маппинг колонок.
            task_type: Тип задачи.
        
        Returns:
            Список ID правильных вариантов.
        """
        if task_type not in ("SC", "MC"):
            return []
        
        correct_answer_column = column_mapping.get("correct_answer")
        if not correct_answer_column:
            return []
        
        correct_text = row.get(correct_answer_column, "").strip()
        if not correct_text:
            return []
        
        # Формат: "A" или "A,B" или "A, B, C"
        correct_ids = [id.strip() for id in correct_text.split(",") if id.strip()]
        return correct_ids

    def _parse_max_score(
        self,
        row: Dict[str, str],
        column_mapping: Dict[str, str],
        task_type: TaskType,
    ) -> int:
        """
        Парсит максимальный балл.
        
        Args:
            row: Словарь с данными строки.
            column_mapping: Маппинг колонок.
            task_type: Тип задачи.
        
        Returns:
            Максимальный балл.
        """
        max_score_column = column_mapping.get("max_score")
        if max_score_column:
            max_score_text = row.get(max_score_column, "").strip()
            if max_score_text:
                try:
                    return int(max_score_text)
                except ValueError:
                    pass
        
        # Дефолтные значения по типу задачи
        if task_type in ("SA", "SA_COM"):
            return self.settings.default_points_short_answer
        return 10  # Дефолт для SC/MC/TA

    def _parse_accepted_answers(
        self,
        row: Dict[str, str],
        column_mapping: Dict[str, str],
        max_score: int,
    ) -> List[ShortAnswerAccepted]:
        """
        Парсит допустимые варианты короткого ответа.
        
        Args:
            row: Словарь с данными строки.
            column_mapping: Маппинг колонок.
            max_score: Максимальный балл.
        
        Returns:
            Список ShortAnswerAccepted.
        """
        accepted_column = column_mapping.get("accepted_answers")
        if not accepted_column:
            # Если колонки нет, используем correct_answer как единственный вариант
            correct_column = column_mapping.get("correct_answer")
            if correct_column:
                correct_text = row.get(correct_column, "").strip()
                if correct_text:
                    return [ShortAnswerAccepted(value=correct_text, score=max_score)]
            return []
        
        accepted_text = row.get(accepted_column, "").strip()
        if not accepted_text:
            return []
        
        # Формат: "вариант1 | вариант2 | вариант3"
        # или "вариант1:10 | вариант2:5" (с баллами)
        accepted: List[ShortAnswerAccepted] = []
        parts = accepted_text.split("|")
        
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            # Проверяем формат с баллами: "вариант:балл"
            if ":" in part:
                value, score_str = part.split(":", 1)
                value = value.strip()
                try:
                    score = int(score_str.strip())
                except ValueError:
                    score = max_score
            else:
                value = part.strip()
                score = max_score
            
            if value:
                accepted.append(ShortAnswerAccepted(value=value, score=score))
        
        return accepted
