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

        # tsk-010: чеки об оплате, которые загружает ученик или родитель.
        # Отдельная директория от учебных вложений: это платёжные документы,
        # у них другой срок хранения и другой круг читателей.
        self.payment_receipts_upload_dir: Path = Path(
            os.getenv("PAYMENT_RECEIPTS_UPLOAD_DIR", "uploads/receipts")
        )
        self.payment_receipts_upload_dir.mkdir(parents=True, exist_ok=True)

        # Месяц оплачивается ДО СВОЕГО КОНЦА: за август платят до 31 августа.
        # Должником человек становится в сентябре, а не 5-го августа — цикл
        # школы именно такой (уточнено оператором 2026-08-02, до этого здесь
        # стояло 5-е число текущего месяца, и это было неверно).
        #
        # Отсюда две разные даты, которые нельзя путать:
        #   • просрочка (пометка в кабинете и письмо) — со следующего дня после
        #     конца месяца, то есть с 1-го числа следующего;
        #   • блокировка занятий — на PAYMENT_BLOCK_AFTER_DAYS позже, чтобы у
        #     человека были дни на оплату после закрытия месяца.
        self.payment_block_after_days: int = int(
            os.getenv("PAYMENT_BLOCK_AFTER_DAYS", "5")
        )

        # tsk-010: оплата картой через ЮKassa. Пусто — способ выключен, кабинет
        # показывает только загрузку чека.
        self.yookassa_shop_id: str = os.getenv("YOOKASSA_SHOP_ID", "")
        self.yookassa_secret_key: str = os.getenv("YOOKASSA_SECRET_KEY", "")
        # Предохранитель против боевых платежей на этапе разработки: ключ
        # тестового магазина начинается с `test_`. Боевой ключ принимается
        # только при явном YOOKASSA_ALLOW_LIVE=true — то есть случайно
        # настоящие деньги не спишутся, даже если ключ перепутали местами.
        self.yookassa_allow_live: bool = (
            os.getenv("YOOKASSA_ALLOW_LIVE", "false").lower() in ("true", "1", "yes")
        )
        self.yookassa_api_url: str = os.getenv(
            "YOOKASSA_API_URL", "https://api.yookassa.ru/v3"
        )

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
        # rstrip("/") — защита от двойного слэша при неаккуратном заполнении оператором
        # (например, .../lms-media-cas/ вместо .../lms-media-cas), найдено ревью tsk-160.
        _s3_url = (os.getenv("S3_MEDIA_BUCKET_URL") or "").rstrip("/")
        self.s3_media_bucket_url: str | None = _s3_url or None

        # ✅ Реквизиты записи в S3 (tsk-520) — те же, что у ContentBackbone: бакет
        # общий, различаются только пространства ключей. Нужны, чтобы файлы
        # материалов ложились в объектное хранилище, а не на диск приложения,
        # который не переживает переезд машины (tsk-519: 0 файлов после переноса).
        # Пусто — dev-режим: файл пишется в materials_upload_dir.
        self.s3_endpoint_url: str = (os.getenv("S3_ENDPOINT_URL") or "").rstrip("/")
        self.s3_bucket_name: str = os.getenv("S3_BUCKET_NAME", "")
        self.s3_access_key: str = os.getenv("S3_ACCESS_KEY", "")
        self.s3_secret_key: str = os.getenv("S3_SECRET_KEY", "")
        self.s3_region: str = os.getenv("S3_REGION", "ru-1")

        # Префикс ключей файлов материалов в бакете. Отделяет их от CAS-пространства
        # заданий (`<sha[:2]>/<sha>.<ext>`), которое наполняет ContentBackbone и
        # которое публично читается через /api/v1/media.
        self.material_files_s3_prefix: str = os.getenv(
            "MATERIAL_FILES_S3_PREFIX", "materials"
        ).strip("/")

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
        # include).
        #
        # ⚠️ Правильное prod-значение — `learn.victor-komlev.ru` (ADR-0014,
        # ContentBackbone docs/adr/0014-domain-layout.md: «Cookie scope
        # Domain=.victor-komlev.ru нельзя — не должны утекать на WordPress»).
        # Такой домен покрывает и `learn.victor-komlev.ru`, и её поддомен
        # `api.learn.victor-komlev.ru`, но НЕ `www.victor-komlev.ru` (WordPress)
        # и не корневой `victor-komlev.ru`. Значение `victor-komlev.ru` (без
        # `learn.` префикса) — регресс относительно ADR-0014, найденный
        # независимым ревью 2026-07-06 (tsk-159/161): делит cookie-scope с
        # живым WordPress-сайтом на том же родительском домене.
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

        # tsk-428 (Календарь LMS, Фаза 1): скользящий горизонт генерации
        # lesson_occurrence из активных lesson_slot вперёд, в днях.
        self.lesson_occurrence_horizon_days: int = int(
            os.getenv("LESSON_OCCURRENCE_HORIZON_DAYS", "14")
        )
        # Интервал APScheduler-тика генератора occurrence.
        self.lesson_occurrence_cron_interval_min: int = int(
            os.getenv("LESSON_OCCURRENCE_CRON_INTERVAL_MIN", "60")
        )

        # tsk-429 (Календарь LMS, Фаза 2): за сколько минут до occurrence
        # слать напоминание ученику (once per occurrence).
        self.lesson_reminder_lead_minutes: int = int(
            os.getenv("LESSON_REMINDER_LEAD_MINUTES", "30")
        )
        # Порог «не пришёл»: минут после scheduled_at без joined/manual_present.
        self.lesson_no_show_threshold_minutes: int = int(
            os.getenv("LESSON_NO_SHOW_THRESHOLD_MINUTES", "10")
        )
        # tsk-494 (дашборд ученика): сколько последних недель считать
        # "типичным" темпом для прогноза окончания курса (простая эвристика,
        # не ML — план docs/specs/2026-08-01-plan-tsk494-student-dashboard-api.md).
        self.student_forecast_pace_weeks: int = int(
            os.getenv("STUDENT_FORECAST_PACE_WEEKS", "4")
        )
        # Интервал APScheduler-тика reminder+no_show (чаще генератора —
        # десятиминутный порог no_show требует более мелкой гранулярности).
        self.lesson_attendance_cron_interval_min: int = int(
            os.getenv("LESSON_ATTENDANCE_CRON_INTERVAL_MIN", "5")
        )
        # tsk-455: запас до начала occurrence, в пределах которого реальное
        # учебное действие (сдача ответа/завершение материала) всё ещё
        # авто-подтверждает явку — без него ученик, пришедший на несколько
        # секунд/минут раньше scheduled_at, не попадает в auto_joined.
        self.lesson_auto_confirm_early_grace_minutes: int = int(
            os.getenv("LESSON_AUTO_CONFIRM_EARLY_GRACE_MINUTES", "15")
        )
