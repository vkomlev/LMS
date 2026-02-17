# app/core/config.py

import os
from typing import List
from pathlib import Path


class Settings:
    def __init__(self):
        try:
            self.database_url: str = os.environ["DATABASE_URL"]
            raw_keys = os.environ["VALID_API_KEYS"]
        except KeyError as e:
            raise RuntimeError(f"Missing required environment variable: {e}")

        self.log_level: str = os.environ.get("LOG_LEVEL", "INFO")

        self.valid_api_keys: List[str] = [
            key.strip() for key in raw_keys.split(",") if key.strip()
        ]
        if not self.valid_api_keys:
            raise RuntimeError("VALID_API_KEYS must contain at least one key")

        # ✅ Messages attachments settings (из env + дефолты)
        self.messages_upload_dir: Path = Path(
            os.getenv("MESSAGES_UPLOAD_DIR", "uploads/messages")
        )
        self.messages_upload_dir.mkdir(parents=True, exist_ok=True)

        # ✅ Materials / files upload (для контента материалов: PDF, документы и т.д.)
        self.materials_upload_dir: Path = Path(
            os.getenv("MATERIALS_UPLOAD_DIR", "uploads/materials")
        )
        self.materials_upload_dir.mkdir(parents=True, exist_ok=True)

        self.max_attachment_size_bytes: int = int(
            os.getenv("MAX_ATTACHMENT_SIZE_BYTES", str(10 * 1024 * 1024))  # 10 MB
        )

        self.attachment_chunk_size: int = int(
            os.getenv("ATTACHMENT_CHUNK_SIZE", str(1024 * 1024))  # 1 MB
        )

        # ✅ Google Sheets settings (из QSMImport)
        self.gsheets_spreadsheet_id: str = os.getenv("GSHEETS_SPREADSHEET_ID", "")
        self.gsheets_worksheet_name: str = os.getenv("GSHEETS_WORKSHEET_NAME", "Tasks")
        self.gsheets_service_account_json: str = os.getenv("GSHEETS_SERVICE_ACCOUNT_JSON", "")
        
        # ✅ Default settings для импорта
        self.default_points_short_answer: int = int(os.getenv("DEFAULT_POINTS_SHORT_ANSWER", "10"))
        self.prepend_input_link: bool = os.getenv("PREPEND_INPUT_LINK", "true").lower() == "true"
        self.input_link_label: str = os.getenv("INPUT_LINK_LABEL", "Входные данные")