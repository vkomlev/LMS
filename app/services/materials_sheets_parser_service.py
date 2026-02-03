# app/services/materials_sheets_parser_service.py
"""
Парсер данных из Google Sheets в структуры материалов.
Поддерживает многокурсовой импорт: курс задаётся полем course_uid в каждой строке.

Интеграция с GoogleSheetsService: вызывающий код (API) использует
GoogleSheetsService.read_sheet(spreadsheet_id, range_name) для чтения листа,
затем передаёт заголовки и строки в build_column_mapping_from_headers и parse_material_row.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

from app.core.config import Settings
from app.utils.exceptions import DomainError

logger = logging.getLogger("services.materials_sheets_parser")

# Типы материалов, допустимые при импорте из таблицы (в т.ч. link для URL)
ALLOWED_IMPORT_TYPES = frozenset({"text", "video", "audio", "image", "link", "pdf", "office_document", "script", "document"})


class MaterialsSheetsParserService:
    """
    Парсинг строк Google Sheets в данные материалов.
    Разрешение course_uid → course_id и upsert выполняет вызывающий код (API/сервис).
    """

    def __init__(self, settings: Optional[Settings] = None):
        if settings is None:
            settings = Settings()
        self.settings = settings

    def extract_spreadsheet_id(self, spreadsheet_url: str) -> str:
        """Извлекает spreadsheet_id из URL или возвращает строку как ID, если это уже ID."""
        s = (spreadsheet_url or "").strip()
        if "/" not in s and "." not in s:
            return s
        try:
            parsed = urlparse(s)
            path_parts = parsed.path.split("/")
            if "d" in path_parts:
                idx = path_parts.index("d")
                if idx + 1 < len(path_parts):
                    return path_parts[idx + 1]
            query_params = parse_qs(parsed.query)
            if "id" in query_params:
                return query_params["id"][0]
            raise DomainError(
                detail=f"Не удалось извлечь spreadsheet_id из URL: {spreadsheet_url}",
                status_code=400,
            )
        except DomainError:
            raise
        except Exception as e:
            logger.exception("Ошибка при извлечении spreadsheet_id: %s", e)
            raise DomainError(
                detail=f"Ошибка при извлечении spreadsheet_id: {str(e)}",
                status_code=400,
            ) from e

    def build_column_mapping_from_headers(
        self,
        headers: List[str],
        user_mapping: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """
        Строит маппинг: поле материала -> название колонки в таблице.
        user_mapping: опциональный маппинг от пользователя { "название колонки": "поле" }.
        Возвращает { "поле": "название колонки" }.
        """
        if user_mapping:
            # Пользователь передал "колонка" -> "поле"; нужен "поле" -> "колонка"
            inverted: Dict[str, str] = {}
            for col_name, field_name in user_mapping.items():
                if field_name and col_name:
                    inverted[field_name.strip()] = col_name.strip()
            return inverted

        column_mapping: Dict[str, str] = {}
        for header in headers:
            if not header:
                continue
            h = header.lower().strip()
            if h in ("course_uid", "course code", "код курса", "курс", "course"):
                column_mapping["course_uid"] = header
            elif h in ("external_uid", "material_uid", "код материала", "id материала", "uid"):
                column_mapping["external_uid"] = header
            elif h in ("title", "название", "name", "заголовок"):
                column_mapping["title"] = header
            elif h in ("type", "тип", "тип материала"):
                column_mapping["type"] = header
            elif h in ("url", "ссылка", "link"):
                column_mapping["url"] = header
            elif h in ("description", "описание", "desc"):
                column_mapping["description"] = header
            elif h in ("caption", "подпись", "caption"):
                column_mapping["caption"] = header
            elif h in ("order_position", "order", "порядок", "позиция"):
                column_mapping["order_position"] = header
            elif h in ("is_active", "active", "активен"):
                column_mapping["is_active"] = header
        return column_mapping

    def parse_material_row(
        self,
        row: Dict[str, str],
        column_mapping: Dict[str, str],
    ) -> Dict[str, Any]:
        """
        Парсит одну строку таблицы в данные материала.

        Returns:
            Словарь: course_uid, external_uid, title, type, content (dict), description, caption,
            order_position, is_active. content для type=link: {url, title?, description?, preview_image?}.

        Raises:
            DomainError: при отсутствии обязательных полей или невалидных данных.
        """
        course_uid = self._get_field(row, column_mapping, "course_uid", required=True)
        external_uid = self._get_field(row, column_mapping, "external_uid", required=True)
        title = self._get_field(row, column_mapping, "title", required=True)
        type_str = self._get_field(row, column_mapping, "type", required=True)
        type_str = type_str.lower().strip() if type_str else ""

        if type_str not in ALLOWED_IMPORT_TYPES:
            raise DomainError(
                detail=f"Недопустимый тип материала: '{type_str}'. Допустимы: {', '.join(sorted(ALLOWED_IMPORT_TYPES))}",
                status_code=400,
            )

        url_val = self._get_field(row, column_mapping, "url", required=False)
        if type_str == "link":
            if not url_val or not url_val.strip():
                raise DomainError(
                    detail="Для типа 'link' обязательна колонка url (ссылка)",
                    status_code=400,
                )
            if not self._is_valid_url(url_val.strip()):
                raise DomainError(
                    detail=f"Некорректный URL: {url_val[:80]}",
                    status_code=400,
                )

        description = self._get_field(row, column_mapping, "description", required=False)
        caption = self._get_field(row, column_mapping, "caption", required=False)
        order_position_str = self._get_field(row, column_mapping, "order_position", required=False)
        is_active_str = self._get_field(row, column_mapping, "is_active", required=False)

        order_position: Optional[int] = None
        if order_position_str:
            try:
                order_position = int(order_position_str.strip())
                if order_position < 1:
                    order_position = None
            except (ValueError, AttributeError):
                order_position = None

        is_active = True
        if is_active_str:
            low = is_active_str.lower().strip()
            if low in ("false", "0", "no", "нет", "ложь"):
                is_active = False
            elif low in ("true", "1", "yes", "да", "истина"):
                is_active = True

        # Формируем content в зависимости от типа
        if type_str == "link":
            content = {
                "url": url_val.strip(),
                "title": title,
                "description": description,
                "preview_image": None,
            }
        elif type_str in ("video", "audio", "image", "pdf", "office_document", "script", "document") and url_val and url_val.strip():
            content = {
                "sources": [{"type": "url", "url": url_val.strip()}],
                "default_source": 0,
            }
        elif type_str == "text":
            content = {
                "text": url_val or title or "",
                "format": "plain",
            }
        else:
            content = {"url": url_val or "", "title": title, "description": description, "preview_image": None}
            if type_str != "link":
                content = {"sources": [{"type": "url", "url": url_val or ""}], "default_source": 0}

        return {
            "course_uid": course_uid.strip(),
            "external_uid": external_uid.strip(),
            "title": title.strip(),
            "type": type_str,
            "content": content,
            "description": description.strip() if description else None,
            "caption": caption.strip() if caption else None,
            "order_position": order_position,
            "is_active": is_active,
        }

    def _get_field(
        self,
        row: Dict[str, str],
        column_mapping: Dict[str, str],
        field_name: str,
        required: bool = False,
    ) -> Optional[str]:
        """Извлекает значение поля из строки по маппингу колонок."""
        column_name = column_mapping.get(field_name)
        if not column_name:
            if required:
                raise DomainError(
                    detail=f"Колонка для поля '{field_name}' не указана в маппинге",
                    status_code=400,
                )
            return None
        value = row.get(column_name, "")
        if value is None:
            value = ""
        value = str(value).strip()
        if required and not value:
            raise DomainError(
                detail=f"Обязательное поле '{field_name}' (колонка '{column_name}') пустое",
                status_code=400,
            )
        return value if value else None

    def _is_valid_url(self, url: str) -> bool:
        """Проверка URL (http/https)."""
        if not url:
            return False
        try:
            parsed = urlparse(url)
            return parsed.scheme in ("http", "https") and bool(parsed.netloc)
        except Exception:
            return False
