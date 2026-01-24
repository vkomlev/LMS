# app/services/courses_sheets_parser_service.py

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urlparse, parse_qs

from app.schemas.courses import AccessLevel
from app.core.config import Settings
from app.utils.exceptions import DomainError

logger = logging.getLogger("services.courses_sheets_parser")


class CoursesSheetsParserService:
    """
    Сервис для парсинга данных из Google Sheets в структуры курсов.
    
    Преобразует строки таблицы в данные для создания/обновления курсов.
    Обрабатывает иерархию (parent_course_uid) и зависимости (required_courses_uid).
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

    def parse_course_row(
        self,
        row: Dict[str, str],
        column_mapping: Optional[Dict[str, str]] = None,
    ) -> Tuple[Dict[str, Any], List[str]]:
        """
        Парсит строку таблицы в данные курса.
        
        Args:
            row: Словарь с данными строки (ключи - названия колонок или индексы).
            column_mapping: Маппинг колонок на поля (если None, используется стандартный).
        
        Returns:
            Кортеж (course_data, required_courses_uid_list).
            course_data содержит: course_uid, title, description, access_level, 
            parent_course_uid, is_required.
            required_courses_uid_list - список course_uid зависимостей (может быть пустым).
        
        Raises:
            DomainError: при ошибках парсинга.
        """
        if column_mapping is None:
            column_mapping = self._get_default_column_mapping()
        
        # Извлекаем обязательные поля
        course_uid = self._get_field(row, column_mapping, "course_uid", required=True)
        title = self._get_field(row, column_mapping, "title", required=True)
        access_level_str = self._get_field(row, column_mapping, "access_level", required=True)
        
        # Валидируем access_level
        try:
            access_level = AccessLevel(access_level_str.lower().strip())
        except ValueError:
            raise DomainError(
                detail=f"Неподдерживаемый уровень доступа: {access_level_str}. "
                       f"Допустимые значения: {', '.join([e.value for e in AccessLevel])}",
                status_code=400,
            )
        
        # Опциональные поля
        description = self._get_field(row, column_mapping, "description", required=False)
        parent_course_uid = self._get_field(row, column_mapping, "parent_course_uid", required=False)
        order_number_str = self._get_field(row, column_mapping, "order_number", required=False)
        is_required_str = self._get_field(row, column_mapping, "is_required", required=False)
        
        # Парсим is_required (по умолчанию False)
        is_required = False
        if is_required_str:
            is_required_str_lower = is_required_str.lower().strip()
            if is_required_str_lower in ("true", "1", "yes", "да", "истина"):
                is_required = True
            elif is_required_str_lower in ("false", "0", "no", "нет", "ложь"):
                is_required = False
            else:
                # Если значение не распознано, используем False
                logger.warning(
                    "Не удалось распознать значение is_required '%s' для курса %s, используется False",
                    is_required_str,
                    course_uid,
                )
        
        # Парсим order_number (опционально, только если указан parent_course_uid)
        order_number: Optional[int] = None
        if order_number_str and parent_course_uid:
            try:
                order_number = int(order_number_str.strip())
                if order_number < 1:
                    logger.warning(
                        "order_number должен быть положительным числом, получено: %s для курса %s. Используется None",
                        order_number_str,
                        course_uid,
                    )
                    order_number = None
            except (ValueError, AttributeError):
                logger.warning(
                    "Не удалось распознать order_number '%s' для курса %s. Используется None",
                    order_number_str,
                    course_uid,
                )
                order_number = None
        
        # Парсим required_courses_uid (список через запятую)
        required_courses_uid_str = self._get_field(row, column_mapping, "required_courses_uid", required=False)
        required_courses_uid_list: List[str] = []
        if required_courses_uid_str:
            # Разделяем по запятой и очищаем от пробелов
            parts = [part.strip() for part in required_courses_uid_str.split(",") if part.strip()]
            required_courses_uid_list = parts
        
        # Формируем данные курса
        course_data: Dict[str, Any] = {
            "course_uid": course_uid,
            "title": title,
            "access_level": access_level.value,
            "description": description,
            "parent_course_uid": parent_course_uid if parent_course_uid else None,
            "order_number": order_number,
            "is_required": is_required,
        }
        
        return course_data, required_courses_uid_list

    def _get_default_column_mapping(self) -> Dict[str, str]:
        """
        Возвращает стандартный маппинг колонок.
        
        Returns:
            Словарь: название колонки -> поле курса.
        """
        return {
            "course_uid": "course_uid",
            "title": "title",
            "description": "description",
            "access_level": "access_level",
            "parent_course_uid": "parent_course_uid",
            "order_number": "order_number",
            "required_courses_uid": "required_courses_uid",
            "is_required": "is_required",
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
