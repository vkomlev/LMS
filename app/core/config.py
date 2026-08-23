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
        # tsk-593: с настроенным S3 это уже не «где лежит файл», а запасной путь
        # для файлов, загруженных до переезда, и режим разработки без S3.
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

        # Реквизиты для перевода — основной способ оплаты (решение оператора
        # 2026-08-02: привычнее людям и без комиссии). Показываются в кабинете
        # над кнопкой; пусто — блок не выводится. Не секрет: те же реквизиты
        # опубликованы на сайте.
        self.payment_transfer_details: str = os.getenv("PAYMENT_TRANSFER_DETAILS", "")

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

        # tsk-644: сколько ждать ответа хранилища. Раньше клиент создавался без
        # настройки таймаутов, то есть на умолчаниях botocore: 60 c на соединение,
        # 60 c на чтение и режим повторов `legacy` (до пяти попыток). Замер на
        # стенде: одно чтение вложения молчащего хранилища держало приём ответа
        # ученика 3 с половиной минуты — обработчик зовёт это чтение синхронно,
        # в потоке, и ждёт его результата.
        #
        # Читать эти значения как «хранилище не отвечает», а не «файл большой»:
        # `read_timeout` у botocore — пауза между байтами на сокете, а не время
        # передачи целиком, поэтому загрузке вложения на 10 МБ двадцати секунд
        # хватает с запасом. Отдельно от них приём ответа ученика ограничен
        # своим, куда более коротким сроком (`CODE_PICK_TIMEOUT_SEC`): там ждёт
        # живой человек, и ждать он не должен вовсе.
        self.s3_connect_timeout_sec: float = float(
            os.getenv("S3_CONNECT_TIMEOUT_SEC", "3")
        )
        self.s3_read_timeout_sec: float = float(os.getenv("S3_READ_TIMEOUT_SEC", "20"))
        # Повторов СВЕРХ первой попытки — именно так botocore читает
        # `retries.max_attempts` (внутри он превращает N в `total_max_attempts`
        # = N + 1). Проверено замером: при N=2 молчащее хранилище держало
        # вызов 60 c, то есть три попытки по двадцать, а не две.
        self.s3_retries: int = int(os.getenv("S3_RETRIES", "1"))

        # tsk-644: потолок ожидания на приёме ответа, когда снимок кода берётся
        # из вложения. Ученик ждёт ответа на «Ответить» — и ждёт он ради оценки,
        # которую сам никогда не увидит (её читает преподаватель). Не успели за
        # этот срок — ставим работу в очередь БЕЗ снимка: фоновый тик прочитает
        # файл сам. Терять тут нечего, кроме редкого случая, когда ученик успеет
        # перезалить файл до тика (снимок и заводился ради него).
        self.code_pick_timeout_sec: float = float(
            os.getenv("CODE_PICK_TIMEOUT_SEC", "3")
        )

        # tsk-644: потолок ожидания хранилища на гейте вложений при приёме
        # ответа. В отличие от снимка кода, обойтись без этого ответа нельзя —
        # от него зависит зачёт, — поэтому не успели значит честный отказ с
        # `Retry-After`, а не молчаливый пропуск защиты.
        self.attachment_gate_timeout_sec: float = float(
            os.getenv("ATTACHMENT_GATE_TIMEOUT_SEC", "5")
        )

        # Префикс ключей файлов материалов в бакете. Отделяет их от CAS-пространства
        # заданий (`<sha[:2]>/<sha>.<ext>`), которое наполняет ContentBackbone и
        # которое публично читается через /api/v1/media.
        self.material_files_s3_prefix: str = os.getenv(
            "MATERIAL_FILES_S3_PREFIX", "materials"
        ).strip("/")

        # ✅ tsk-593: пространства ключей для файлов, которые до сих пор жили
        # только на диске приложения. Диск переезд машины не переживает — так уже
        # потеряли ВСЕ файлы материалов (tsk-519: 0 файлов после переноса).
        #
        # Пространства РАЗНЫЕ намеренно, а не один общий каталог: у чека об
        # оплате другой круг читателей (ученик/родитель и маркетолог) и другой
        # срок хранения, чем у учебного вложения. Разделение по префиксу — то,
        # на что опирается и правило доступа в бакете, и разбор при переносе.
        #
        # Ни одно из трёх пространств НЕ публично: наружу файл уходит только
        # потоком через эндпоинт с проверкой прав. Публичное чтение в бакете
        # оставлено ровно на CAS-пространстве заданий (`<2 hex>/…`), потому что
        # `/api/v1/media` отдаёт на него переадресацию в браузер ученика.
        self.attempt_attachments_s3_prefix: str = os.getenv(
            "ATTEMPT_ATTACHMENTS_S3_PREFIX", "attempts"
        ).strip("/")
        self.message_attachments_s3_prefix: str = os.getenv(
            "MESSAGE_ATTACHMENTS_S3_PREFIX", "messages"
        ).strip("/")
        self.payment_receipts_s3_prefix: str = os.getenv(
            "PAYMENT_RECEIPTS_S3_PREFIX", "receipts"
        ).strip("/")

        # ✅ Проверка целостности ссылок на файлы (tsk-521). Связи «материал → файл»
        # в базе нет, поэтому битую ссылку не видно, пока на неё не наткнётся
        # человек: в tsk-519 такая провисела полгода.
        self.link_audit_enabled: bool = os.getenv(
            "LINK_AUDIT_ENABLED", "true"
        ).lower() in ("true", "1", "yes")
        self.link_audit_interval_hours: int = int(
            os.getenv("LINK_AUDIT_INTERVAL_HOURS", "24")
        )
        # Проверяем только своё — то, что сами и чиним. Чужие сайты массово
        # отвечают 418/429 на автоматические запросы (защита от роботов), и это
        # не признак битой ссылки.
        # removeprefix, а не lstrip: lstrip срезает ЛЮБЫЕ символы из набора
        # {w, .}, и домен вроде `wiki.ru` молча превратился бы в `iki.ru`.
        self.link_audit_own_hosts: list[str] = [
            h.strip().lower().removeprefix("www.")
            for h in os.getenv("LINK_AUDIT_OWN_HOSTS", "victor-komlev.ru").split(",")
            if h.strip()
        ]
        self.link_audit_concurrency: int = int(os.getenv("LINK_AUDIT_CONCURRENCY", "8"))
        self.link_audit_http_timeout_sec: float = float(
            os.getenv("LINK_AUDIT_HTTP_TIMEOUT_SEC", "20")
        )
        # Молчание при чистом прогоне; при находках — не чаще раза в сутки,
        # иначе ежедневный тик превратит одну незамеченную ссылку в поток.
        self.link_audit_notify_cooldown_hours: int = int(
            os.getenv("LINK_AUDIT_NOTIFY_COOLDOWN_HOURS", "24")
        )
        self.link_audit_max_examples: int = int(
            os.getenv("LINK_AUDIT_MAX_EXAMPLES", "10")
        )

        # ✅ tsk-593: суточная проверка вложений — «ссылка на файл есть, файла в
        # хранилище нет». Брат-близнец проверки ссылок в контенте (tsk-521), но
        # источник другой: сами работы учеников, переписка и чеки, а не тела
        # материалов и заданий.
        #
        # Уведомление уходит ТОЛЬКО о новых потерях: те, что случились до
        # переезда (утраченные дефектом tsk-575), записаны в
        # `attachment_missing_seen` как исходный уровень и больше не тревожат —
        # иначе ежедневный тик превратился бы в постоянный шум, на который
        # перестают смотреть. Чистый прогон молчит.
        self.attachment_audit_enabled: bool = os.getenv(
            "ATTACHMENT_AUDIT_ENABLED", "true"
        ).lower() in ("true", "1", "yes")
        self.attachment_audit_interval_hours: int = int(
            os.getenv("ATTACHMENT_AUDIT_INTERVAL_HOURS", "24")
        )
        self.attachment_audit_notify_cooldown_hours: int = int(
            os.getenv("ATTACHMENT_AUDIT_NOTIFY_COOLDOWN_HOURS", "24")
        )
        self.attachment_audit_max_examples: int = int(
            os.getenv("ATTACHMENT_AUDIT_MAX_EXAMPLES", "10")
        )
        # Сколько проверок наличия файла держим в полёте разом. Проверка одного
        # файла — сетевой запрос к хранилищу; без предела тик на тысяче вложений
        # открыл бы тысячу соединений.
        self.attachment_storage_concurrency: int = int(
            os.getenv("ATTACHMENT_STORAGE_CONCURRENCY", "8")
        )

        # tsk-541: фоновый пересчёт student_course_state для целей
        # course_dependencies (см. course_dependency_state_cron_service.py).
        # Без него подкурс-цель зависимости, записанной в обход API (прямой
        # SQL — как в tsk-523), никогда не получает свежий кеш прогресса.
        self.course_dependency_state_cron_enabled: bool = os.getenv(
            "COURSE_DEPENDENCY_STATE_CRON_ENABLED", "true"
        ).lower() in ("true", "1", "yes")
        self.course_dependency_state_cron_interval_min: int = int(
            os.getenv("COURSE_DEPENDENCY_STATE_CRON_INTERVAL_MIN", "15")
        )

        # tsk-302 этап 3: фоновая оценка кода ученика (чистота + признак
        # ИИ-авторства). Синхронно её делать нельзя — это внешний вызов модели
        # в пользовательском пути приёма ответа; ученик ждать не должен, а к
        # моменту, когда работу откроет преподаватель, оценка уже готова.
        # Выключатель нужен на случай проблем с провайдером: приём ответов
        # продолжает работать, просто отчёты копятся в статусе pending.
        self.code_review_cron_enabled: bool = os.getenv(
            "CODE_REVIEW_CRON_ENABLED", "true"
        ).lower() in ("true", "1", "yes")
        self.code_review_cron_interval_min: int = int(
            os.getenv("CODE_REVIEW_CRON_INTERVAL_MIN", "2")
        )
        # За тик берём немного: оценка одной работы — сетевой вызов на секунды,
        # а очередь всё равно разгребётся следующими тиками. Заодно это потолок
        # расхода на провайдера при внезапном наплыве сдач.
        self.code_review_batch_size: int = int(
            os.getenv("CODE_REVIEW_BATCH_SIZE", "10")
        )
        # Сколько раз пробуем повторно при временной ошибке (сеть, таймаут,
        # остывание провайдера). Постоянные ошибки (неверный ключ, битый ответ)
        # не ретраятся вовсе — см. `retryable` в таксономии клиента.
        self.code_review_max_attempts: int = int(
            os.getenv("CODE_REVIEW_MAX_ATTEMPTS", "3")
        )
        # tsk-644: через сколько минут работа, взятая тиком, снова считается
        # свободной. Нужно с тех пор, как проход перестал держать замок на всё
        # своё время: пометка захвата не даёт двум worker'ам заплатить
        # провайдеру дважды за один и тот же ответ, а срок возвращает работу в
        # очередь, если процесс умер посередине.
        #
        # Пятнадцать минут — с запасом больше самого долгого прохода: одна
        # работа при молчащем провайдере стоит ~2 минуты (60 c бюджета плюс
        # один повтор), пачка по умолчанию — десять работ.
        self.code_review_claim_ttl_min: int = int(
            os.getenv("CODE_REVIEW_CLAIM_TTL_MIN", "15")
        )

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

        # tsk-572: рубильник ИИ-наставника. Выключение убирает кнопку у ученика и
        # закрывает эндпоинты, всё остальное продолжает работать как сегодня —
        # откат без развёртывания, если наставник поведёт себя не так.
        self.ai_tutor_enabled: bool = os.getenv("AI_TUTOR_ENABLED", "true").lower() in ("true", "1", "yes")

        # tsk-301: режим подписного гейта. Четыре значения, а не булев рубильник —
        # включение идёт ступенями, и `guests` это самостоятельный шаг выката,
        # а не оттенок «включено»:
        #   off    — проверка не вызывается вовсе, система как до задачи;
        #   shadow — считает и ЛОГИРУЕТ, кого бы отрезала, ничего не запрещая;
        #   guests — применяются только отказы «ученик не опознан / подписки нет»
        #            (у гостя прав и так нет, риск нулевой, а кран токенов на
        #            464 демо-курсах закрывается сразу);
        #   on     — применяются все отказы.
        # Откат — возврат в `off`, без развёртывания.
        _gate_mode = os.getenv("SUBSCRIPTION_GATE_MODE", "off").strip().lower()
        if _gate_mode not in ("off", "shadow", "guests", "on"):
            # Опечатка в настройке не должна молча означать «включено»: неверное
            # значение падает на старте, а не открывает или закрывает доступ наугад.
            raise RuntimeError(
                f"SUBSCRIPTION_GATE_MODE={_gate_mode!r}: допустимо off|shadow|guests|on"
            )
        self.subscription_gate_mode: str = _gate_mode

        # tsk-301: докупаемый пакет обращений к ИИ-наставнику. Цена и объём —
        # настройка, а не константа в коде: закупочная стоимость пакета около
        # 0,7 ₽ (замер брифа), то есть цена здесь продуктовая и будет меняться
        # быстрее кода.
        self.ai_package_price_minor: int = int(
            os.getenv("AI_PACKAGE_PRICE_MINOR", "50000")  # 500 ₽
        )
        self.ai_package_units: int = int(os.getenv("AI_PACKAGE_UNITS", "40"))

        # tsk-301: до какого числа включительно первая покупка оплачивает ТЕКУЩИЙ
        # месяц. Позже — первое начисление ставится за следующий, остаток текущего
        # даётся бесплатно: человек не должен платить полную цену за три дня ровно
        # в тот момент, когда впервые расстаётся с деньгами (решение оператора).
        self.first_month_charge_cutoff_day: int = int(
            os.getenv("FIRST_MONTH_CHARGE_CUTOFF_DAY", "20")
        )

        # tsk-572: суточный проход датчика учебных пробелов. Влияет на то, что
        # видят преподаватель и методист, поэтому выключается без развёртывания.
        self.learning_gaps_cron_enabled: bool = os.getenv("LEARNING_GAPS_CRON_ENABLED", "true").lower() in ("true", "1", "yes")
        self.learning_gaps_cron_interval_hours: int = int(os.getenv("LEARNING_GAPS_CRON_INTERVAL_HOURS", "24"))

        # tsk-596: суточный пересчёт текущего месяца начислений + страж
        # «ходит, но не выставлен». Без него строка месяца появлялась только
        # при правке расписания или по кнопке в кабинете маркетолога — то есть
        # первого числа не появлялась вовсе.
        self.charge_cron_enabled: bool = os.getenv(
            "CHARGE_CRON_ENABLED", "true"
        ).lower() in ("true", "1", "yes")
        self.charge_cron_interval_hours: int = int(
            os.getenv("CHARGE_CRON_INTERVAL_HOURS", "24")
        )
        # Молчание при чистом прогоне; при находках — не чаще раза в сутки,
        # иначе ежедневный тик превратит одного невыставленного ученика в поток.
        self.charge_anomaly_notify_cooldown_hours: int = int(
            os.getenv("CHARGE_ANOMALY_NOTIFY_COOLDOWN_HOURS", "24")
        )
        self.charge_anomaly_max_examples: int = int(
            os.getenv("CHARGE_ANOMALY_MAX_EXAMPLES", "10")
        )

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
        # tsk-504 (цветовая подсветка метрик дашборда родителя): минимальный
        # размер когорты ДРУГИХ активных учеников курса, при котором позиция
        # ученика вообще классифицируется (решение оператора 2026-08-06).
        self.student_dashboard_cohort_min_size: int = int(
            os.getenv("STUDENT_DASHBOARD_COHORT_MIN_SIZE", "5")
        )
        # tsk-032 (удержание между занятиями): фоновая фиксация выполненных
        # вех в user_achievements. Сама серия НЕ хранится и от тика не зависит
        # — она считается на лету при чтении; тик только проставляет earned_at
        # (см. app/services/retention_achievements_cron_service.py).
        self.retention_achievements_cron_enabled: bool = os.getenv(
            "RETENTION_ACHIEVEMENTS_CRON_ENABLED", "true"
        ).lower() in ("true", "1", "yes")
        self.retention_achievements_cron_interval_min: int = int(
            os.getenv("RETENTION_ACHIEVEMENTS_CRON_INTERVAL_MIN", "15")
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
        # tsk-591: простой ученика во время занятия — сигнал преподавателю.
        self.lesson_idle_cron_enabled: bool = os.getenv(
            "LESSON_IDLE_CRON_ENABLED", "true"
        ).lower() in ("true", "1", "yes")
        # Тик чаще порога: с шагом 3 минуты простой обнаруживается на 10–13-й
        # минуте, а не на 20-й.
        self.lesson_idle_cron_interval_min: int = int(
            os.getenv("LESSON_IDLE_CRON_INTERVAL_MIN", "3")
        )
        # Порог тишины. 10 минут — решение оператора 2026-08-09.
        self.lesson_idle_threshold_minutes: int = int(
            os.getenv("LESSON_IDLE_THRESHOLD_MINUTES", "10")
        )
        # Как часто кабинет шлёт пульс присутствия. Отдаётся клиенту в ответе
        # на пульс — частоту можно менять без выката SPW.
        self.presence_ping_seconds: int = int(
            os.getenv("PRESENCE_PING_SECONDS", "120")
        )
        # Сколько пульс считается свежим. Заведомо больше интервала пульса:
        # один потерянный сигнал (обрыв сети, спящий таймер вкладки) не должен
        # превращать ученика в «ушёл».
        self.presence_stale_seconds: int = int(
            os.getenv("PRESENCE_STALE_SECONDS", "420")
        )
