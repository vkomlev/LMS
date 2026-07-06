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

        # Вложения к ответам учеников в stateful attempts.
        self.attempt_attachments_upload_dir: Path = Path(
            os.getenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", "uploads/attempts")
        )
        self.attempt_attachments_upload_dir.mkdir(parents=True, exist_ok=True)

        # ✅ Materials / files upload (для контента материалов: PDF, документы и т.д.)
        self.materials_upload_dir: Path = Path(
            os.getenv("MATERIALS_UPLOAD_DIR", "uploads/materials")
        )
        self.materials_upload_dir.mkdir(parents=True, exist_ok=True)

        # ✅ CAS media root — разделяемый путь с ContentBackbone (ADR-0040, tsk-110).
        # CB скачивает файлы в эту директорию; LMS читает из неё через /api/v1/media/.
        # Структура: <cas_media_root>/<sha256[:2]>/<sha256hex>.<ext>
        # Dev-fallback: используется только если S3_MEDIA_BUCKET_URL не задан (см. ниже).
        self.cas_media_root: Path = Path(
            os.getenv("CAS_MEDIA_ROOT", "data/media_store")
        )
        # Директория создаётся при старте; CB пишет туда, LMS только читает.
        self.cas_media_root.mkdir(parents=True, exist_ok=True)

        # ✅ S3-хранилище медиафайлов (ADR-0047, tsk-160) — заменяет общий диск CB/LMS
        # после переезда LMS на VPS. Публичный базовый URL bucket'а (без секретов —
        # LMS только строит редирект, не обращается к S3 API напрямую).
        # Пример: https://s3.twcstorage.ru/lms-media-cas
        # Если не задан — endpoint /api/v1/media/ работает в старом dev-режиме
        # (FileResponse из cas_media_root).
        self.s3_media_bucket_url: str | None = os.getenv("S3_MEDIA_BUCKET_URL") or None

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

        # Domain для сессионной cookie. Пусто в dev (host-only, localhost) —
        # в prod LMS (api.learn.<domain>) и SPW (learn.<domain>) на разных
        # поддоменах, cookie без domain видит только тот поддомен, который её
        # выставил (браузер не шлёт её на другой поддомен даже с credentials:
        # include). "victor-komlev.ru" расшаривает cookie на все поддомены.
        self.cookie_domain: str | None = os.getenv("COOKIE_DOMAIN") or None

        # Phase Y-5: JWT-секрет для подписи embed URL-token (HS256).
        # Single-use enforce через Redis jti marker (TTL = embed_jwt_ttl_sec).
        # Backup в password-manager (как FERNET_MASTER_KEY).
        self.embed_jwt_secret: str = os.getenv("CB_EMBED_JWT_SECRET", "")
        self.embed_jwt_ttl_sec: int = int(os.getenv("CB_EMBED_JWT_TTL_SEC", "300"))

        # Phase Y-6: review-loop constants.
        # REVIEW_PASS_THRESHOLD_RATIO — для derived `is_correct` в teacher
        # grade/regrade: is_correct = (score / max_score >= ratio).
        # Отдельная константа от auto-check PASS_THRESHOLD_RATIO=0.5 (SC/MC/SA),
        # т.к. семантика разная: rubric-pass у teacher мягче, 20% уже даёт
        # «попытка засчитана».
        self.review_pass_threshold_ratio: float = float(
            os.getenv("REVIEW_PASS_THRESHOLD_RATIO", "0.2")
        )
        # ESCALATION_TIMEOUT_HOURS — pending review старше N часов
        # → push методисту (Stage 4 cron).
        self.escalation_timeout_hours: int = int(
            os.getenv("ESCALATION_TIMEOUT_HOURS", "48")
        )
        # ESCALATION_CRON_INTERVAL_MIN — интервал тика APScheduler.
        self.escalation_cron_interval_min: int = int(
            os.getenv("ESCALATION_CRON_INTERVAL_MIN", "5")
        )
        # METHODIST_RATE_LIMIT_PER_DAY_PER_COURSE — verhinder spam:
        # не более N escalation push'ей по одному курсу в сутки.
        self.methodist_rate_limit_per_day_per_course: int = int(
            os.getenv("METHODIST_RATE_LIMIT_PER_DAY_PER_COURSE", "1")
        )
