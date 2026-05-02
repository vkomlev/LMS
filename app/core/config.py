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

        # Environment marker для fail-secure поведения security-critical сервисов
        # (Phase Y-3.1): "production" | "dev" | "test". При production + Redis-outage
        # link_token_service не делает in-memory fallback (см. ERRORS S2-1).
        self.env: str = os.environ.get("ENV", "dev").lower()

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

        # Learning Engine V1 (этап 1: только БД, без изменения поведения API)
        self.learning_engine_v1: bool = os.getenv("LEARNING_ENGINE_V1", "false").lower() in ("true", "1", "yes")

        # SPW auth — Phase Y-1
        self.resend_api_key: str = os.getenv("RESEND_API_KEY", "")
        self.smtp_from: str = os.getenv("SMTP_FROM", "noreply@victor-komlev.ru")
        # Базовый URL SPW (для встраивания в magic-link письма).
        # Dev: http://localhost:3000; prod: https://learn.victor-komlev.ru
        self.public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:3000")
        self.magic_link_secret: str = os.getenv("MAGIC_LINK_SECRET", "")
        self.session_signing_key: str = os.getenv("SESSION_SIGNING_KEY", "")
        self.fernet_master_key: str = os.getenv("FERNET_MASTER_KEY", "")
        self.tg_bot_token_for_initdata: str = os.getenv("TG_BOT_TOKEN_FOR_INITDATA", "")
        self.vk_id_client_id: str = os.getenv("VK_ID_CLIENT_ID", "")
        self.vk_id_client_secret: str = os.getenv("VK_ID_CLIENT_SECRET", "")
        self.vk_id_redirect_uri: str = os.getenv("VK_ID_REDIRECT_URI", "")
        self.redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/2")
        self.cors_allowed_origins: List[str] = [
            o.strip()
            for o in os.getenv(
                "CORS_ALLOWED_ORIGINS",
                "http://localhost:3000",
            ).split(",")
            if o.strip()
        ]

        # Y-4 pre-S5: тестовые auth-эндпоинты (e.g. /auth/test/issue-session).
        # Двойной gating: env in {"dev","test"} AND test_endpoints_enabled=True
        # → endpoint работает; иначе FastAPI вернёт 404 (path-as-disabled).
        # KEEP FALSE in prod даже если ENV случайно стал dev/test.
        self.test_endpoints_enabled: bool = os.getenv(
            "TEST_ENDPOINTS_ENABLED", "false"
        ).lower() in ("true", "1", "yes")

        # Cookie secure flag — True только в prod, False в dev (HTTP localhost).
        # Используется в Set-Cookie для test-issue-session.
        self.cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() in (
            "true", "1", "yes"
        )

        # Phase Y-5: JWT-секрет для подписи embed URL-token (HS256).
        # Single-use enforce через Redis jti marker (TTL = embed_jwt_ttl_sec).
        # Backup в password-manager (как FERNET_MASTER_KEY).
        self.embed_jwt_secret: str = os.getenv("CB_EMBED_JWT_SECRET", "")
        self.embed_jwt_ttl_sec: int = int(os.getenv("CB_EMBED_JWT_TTL_SEC", "300"))