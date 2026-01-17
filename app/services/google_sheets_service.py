# app/services/google_sheets_service.py

from __future__ import annotations

import json
import logging
from typing import List, Optional
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import Settings
from app.utils.exceptions import DomainError

logger = logging.getLogger("services.google_sheets")


class GoogleSheetsService:
    """
    Сервис для работы с Google Sheets API.
    
    Мигрировано из проекта QSMImport.
    Использует Service Account для аутентификации.
    """

    def __init__(self, settings: Optional[Settings] = None):
        """
        Инициализация сервиса.
        
        Args:
            settings: Настройки приложения (если None, создаются новые).
        """
        if settings is None:
            settings = Settings()
        
        self.settings = settings
        self._service: Optional[object] = None

    def _get_service(self):
        """
        Получить или создать клиент Google Sheets API.
        
        Returns:
            Объект сервиса Google Sheets API.
        """
        if self._service is not None:
            return self._service

        if not self.settings.gsheets_service_account_json:
            logger.error("GSHEETS_SERVICE_ACCOUNT_JSON не указан в настройках")
            raise DomainError(
                detail="GSHEETS_SERVICE_ACCOUNT_JSON не указан в настройках. Укажите путь к JSON-файлу сервисного аккаунта в переменной окружения GSHEETS_SERVICE_ACCOUNT_JSON",
                status_code=500,
            )

        # Путь к JSON-файлу с credentials
        credentials_path = Path(self.settings.gsheets_service_account_json)
        if not credentials_path.exists():
            raise DomainError(
                detail=f"Файл с credentials не найден: {credentials_path}",
                status_code=500,
            )

        try:
            # Загружаем credentials из JSON-файла
            credentials = service_account.Credentials.from_service_account_file(
                str(credentials_path),
                scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'],
            )

            # Создаем сервис
            self._service = build('sheets', 'v4', credentials=credentials)
            logger.info("Google Sheets API service initialized")
            return self._service

        except Exception as e:
            logger.exception("Ошибка при инициализации Google Sheets API: %s", e)
            raise DomainError(
                detail=f"Ошибка при инициализации Google Sheets API: {str(e)}",
                status_code=500,
            ) from e

    def read_sheet(
        self,
        spreadsheet_id: Optional[str] = None,
        range_name: Optional[str] = None,
    ) -> List[List[str]]:
        """
        Читает данные из Google Sheets.
        
        Args:
            spreadsheet_id: ID таблицы (если None, используется из настроек).
            range_name: Диапазон для чтения (например, "Лист1!A1:Z100").
                       Если None, используется worksheet_name из настроек.
        
        Returns:
            Список строк, каждая строка - список значений ячеек.
        
        Raises:
            DomainError: при ошибках чтения данных.
        """
        service = self._get_service()
        
        spreadsheet_id = spreadsheet_id or self.settings.gsheets_spreadsheet_id
        if not spreadsheet_id:
            raise DomainError(
                detail="spreadsheet_id не указан",
                status_code=400,
            )

        # Формируем range_name
        if range_name is None:
            worksheet_name = self.settings.gsheets_worksheet_name
            range_name = f"{worksheet_name}!A:Z"  # Читаем все колонки до Z
        
        try:
            logger.info(
                "Reading Google Sheet: spreadsheet_id=%s, range=%s",
                spreadsheet_id,
                range_name,
            )
            
            # Выполняем запрос
            result = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=range_name,
            ).execute()
            
            values = result.get('values', [])
            logger.info("Read %d rows from Google Sheet", len(values))
            
            return values

        except HttpError as e:
            logger.exception("HTTP error при чтении Google Sheet: %s", e)
            raise DomainError(
                detail=f"Ошибка при чтении Google Sheet: {str(e)}",
                status_code=500,
            ) from e
        except Exception as e:
            logger.exception("Неожиданная ошибка при чтении Google Sheet: %s", e)
            raise DomainError(
                detail=f"Неожиданная ошибка при чтении Google Sheet: {str(e)}",
                status_code=500,
            ) from e

    def get_spreadsheet_info(
        self,
        spreadsheet_id: Optional[str] = None,
    ) -> dict:
        """
        Получить информацию о таблице (названия листов и т.д.).
        
        Args:
            spreadsheet_id: ID таблицы (если None, используется из настроек).
        
        Returns:
            Словарь с информацией о таблице.
        """
        service = self._get_service()
        
        spreadsheet_id = spreadsheet_id or self.settings.gsheets_spreadsheet_id
        if not spreadsheet_id:
            raise DomainError(
                detail="spreadsheet_id не указан",
                status_code=400,
            )

        try:
            result = service.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
            ).execute()
            
            sheets = [
                {
                    "title": sheet["properties"]["title"],
                    "sheet_id": sheet["properties"]["sheetId"],
                }
                for sheet in result.get("sheets", [])
            ]
            
            return {
                "spreadsheet_id": spreadsheet_id,
                "title": result.get("properties", {}).get("title", ""),
                "sheets": sheets,
            }

        except HttpError as e:
            logger.exception("HTTP error при получении информации о таблице: %s", e)
            raise DomainError(
                detail=f"Ошибка при получении информации о таблице: {str(e)}",
                status_code=500,
            ) from e
        except Exception as e:
            logger.exception("Неожиданная ошибка: %s", e)
            raise DomainError(
                detail=f"Неожиданная ошибка: {str(e)}",
                status_code=500,
            ) from e
