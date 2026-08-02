# app/services/link_audit_service.py
"""tsk-521: регулярная проверка целостности ссылок на файлы в контенте.

**Зачем.** Связи «материал → файл» в базе нет: url живёт строкой внутри
`content`, и БД не знает, существует ли файл на самом деле. В tsk-519 такая
ссылка провисела полгода — материал показывал ученику битую картинку, и никто
об этом не узнал, пока не посмотрели руками.

**Что проверяется — только своё**, то есть то, что мы сами и чиним:

- `/api/v1/materials/files/<id>` — файлы материалов (бакет или диск);
- `/api/v1/media/<sha>.<ext>` — CAS-медиа заданий (бакет или диск CB);
- ссылки на файлы с собственных доменов (`LINK_AUDIT_OWN_HOSTS`).

Чужие сайты (kompege, Яндекс.Учебник, Поляков) намеренно не проверяются: они
массово отвечают 418/429 на автоматические запросы, и это защита от роботов, а
не битая ссылка. В прогоне tsk-519 из 935 ответов «не 200» настоящими
находками были три.

**Молчание = всё хорошо.** Уведомление методисту уходит только при находках, не
чаще раза в сутки (`LINK_AUDIT_NOTIFY_COOLDOWN_HOURS`).

Multi-worker safety — как у соседних тиков (`escalation_service`): advisory-lock
уровня транзакции, свой ключ. Один worker за тик делает работу, остальные
мгновенно отступают.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.session import async_session_factory
from app.services import inbox_service, material_files_storage
from app.utils.exceptions import DomainError

logger = logging.getLogger("app.link_audit")

# Ключ advisory-lock. Не должен пересекаться с соседними тиками:
# escalation — 0x59365453 ("Y6TS"), календарь — свои.
_LINK_AUDIT_LOCK_KEY = 0x4C494E4B  # ascii "LINK"

NOTIFICATION_KIND = "broken_media_links"

_MATERIAL_FILE_RE = re.compile(r"/api/v1/materials/files/([A-Za-z0-9._-]+)")
_CAS_MEDIA_RE = re.compile(r"/api/v1/media/([0-9a-f]{64}\.[A-Za-z0-9]{1,8})")
# URL целиком, вместе с query: у части CDN подпись живёт в параметрах, и
# проверка обрезанной ссылки даёт ложную находку (урок tsk-519).
_URL_RE = re.compile(r"https?://[^\s\"'<>\\)]+", re.IGNORECASE)
_FILE_EXT_RE = re.compile(
    r"\.(?:jpg|jpeg|png|gif|webp|svg|bmp|pdf|mp4|webm|mp3|zip|rar|7z|docx|xlsx|pptx|csv|txt)$",
    re.IGNORECASE,
)

_scheduler: Optional[AsyncIOScheduler] = None

# Ссылка → где встретилась. Ключ вида ("material", 187) / ("task", 4033).
Owners = List[Tuple[str, int]]


def _clean_url(raw: str) -> str:
    """Декодирует HTML-энтити и убирает хвостовую пунктуацию разметки."""
    return html.unescape(raw.replace("\\/", "/")).rstrip(".,;)]}'\"")


def _is_own_host(url: str, own_hosts: Sequence[str]) -> bool:
    """True, если ссылка ведёт на наш домен (или его поддомен)."""
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return any(host == h or host.endswith(f".{h}") for h in own_hosts)


def _looks_like_file(url: str) -> bool:
    """True, если ссылка ведёт на файл, а не на страницу."""
    return bool(_FILE_EXT_RE.search(urlsplit(url).path))


def _collect(rows: Sequence[Any], own_hosts: Sequence[str]) -> Dict[str, Owners]:
    """Собирает из тел материалов и заданий ссылки, которые нужно проверить.

    Возвращает отображение «цель проверки → где встретилась». Цель — либо
    `material:<file_id>`, либо `media:<sha_ext>`, либо сам http-URL.
    """
    found: Dict[str, Owners] = {}

    def remember(target: str, owner: Tuple[str, int]) -> None:
        found.setdefault(target, []).append(owner)

    for row in rows:
        body = row.body or ""
        owner = (row.kind, int(row.id))
        for file_id in set(_MATERIAL_FILE_RE.findall(body)):
            remember(f"material:{file_id}", owner)
        for sha_ext in set(_CAS_MEDIA_RE.findall(body)):
            remember(f"media:{sha_ext}", owner)
        for raw in set(_URL_RE.findall(body)):
            url = _clean_url(raw)
            if _is_own_host(url, own_hosts) and _looks_like_file(url):
                remember(url, owner)

    return found


async def _cas_media_exists(sha_ext: str) -> bool:
    """Есть ли медиа задания — в бакете (prod) либо в каталоге CAS (dev)."""
    settings = Settings()
    if material_files_storage.s3_enabled():
        return await material_files_storage.object_exists(f"{sha_ext[:2]}/{sha_ext}")
    return (settings.cas_media_root / sha_ext[:2] / sha_ext).is_file()


async def _check_target(
    target: str,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
) -> Optional[str]:
    """Проверяет одну цель. Возвращает причину поломки или None, если всё цело.

    Недоступность хранилища (`DomainError`) наверх не проглатывается: тик
    прервётся целиком, потому что «нет ответа» — не то же самое, что «файла
    нет», и такой прогон нельзя выдавать за проверку.
    """
    async with sem:
        if target.startswith("material:"):
            ok = await material_files_storage.material_file_exists(target.split(":", 1)[1])
            return None if ok else "файла нет в хранилище"

        if target.startswith("media:"):
            ok = await _cas_media_exists(target.split(":", 1)[1])
            return None if ok else "медиа нет в хранилище"

        try:
            resp = await client.head(target)
            if resp.status_code in (403, 405, 501):
                resp = await client.get(target)
        except httpx.HTTPError as exc:
            return f"нет ответа ({type(exc).__name__})"
        return None if resp.status_code == 200 else f"HTTP {resp.status_code}"


async def _recently_notified(db: AsyncSession, cooldown_hours: int) -> bool:
    """True, если уведомление такого рода уже отправляли за последние N часов."""
    res = await db.execute(
        text(
            "SELECT count(*) FROM notifications "
            "WHERE kind = :kind "
            "  AND modified_at >= now() - CAST(:h AS text)::interval"
        ),
        {"kind": NOTIFICATION_KIND, "h": f"{int(cooldown_hours)} hours"},
    )
    return int(res.scalar() or 0) > 0


async def _notify_methodists(
    db: AsyncSession,
    *,
    broken: Dict[str, str],
    owners: Dict[str, Owners],
    max_examples: int,
) -> int:
    """Кладёт уведомление о находках каждому методисту. Возвращает их число."""
    res = await db.execute(
        text(
            "SELECT ur.user_id FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id WHERE r.name = 'methodist'"
        )
    )
    methodist_ids = [int(row[0]) for row in res.fetchall()]
    if not methodist_ids:
        logger.warning("tsk-521: методистов в базе нет — уведомлять некого")
        return 0

    examples = []
    for target, reason in sorted(broken.items())[:max_examples]:
        where = ", ".join(f"{kind} {oid}" for kind, oid in owners.get(target, [])[:3])
        examples.append({"target": target, "reason": reason, "where": where})

    payload = {
        "broken_count": len(broken),
        "examples": examples,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    lines = [
        f"Проверка нашла {len(broken)} битых ссылок на файлы в активном контенте.",
        "",
    ]
    for ex in examples:
        lines.append(f"• {ex['target']} — {ex['reason']} ({ex['where'] or 'источник не определён'})")
    if len(broken) > len(examples):
        lines.append(f"…и ещё {len(broken) - len(examples)}.")
    lines.append("")
    lines.append("Ученик видит на их месте пустое место или битую картинку.")

    for mid in methodist_ids:
        await inbox_service.create_for_user(
            db,
            user_id=mid,
            kind=NOTIFICATION_KIND,
            title="Битые ссылки на файлы в курсах",
            content="\n".join(lines),
            payload=payload,
            created_by=None,
        )
    return len(methodist_ids)


async def link_audit_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> dict:
    """Один проход проверки. Возвращает сводку для логов и тестов.

    `session_factory` — точка подмены источника сессий: в проде APScheduler
    зовёт тик без аргументов, тесты передают свою фабрику.
    """
    settings = Settings()
    factory = session_factory or async_session_factory
    own_hosts = settings.link_audit_own_hosts
    summary: dict = {"locked": False, "checked": 0, "broken": 0, "notified": 0}

    async with factory() as db:
        got = await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:k) AS locked"),
            {"k": _LINK_AUDIT_LOCK_KEY},
        )
        if not bool(got.scalar()):
            logger.info("tsk-521: тик пропущен — работу делает другой worker")
            return summary
        summary["locked"] = True

        rows = (
            await db.execute(
                text(
                    "SELECT 'material' AS kind, id, content::text AS body "
                    "FROM materials WHERE is_active "
                    "UNION ALL "
                    "SELECT 'task', id, task_content::text FROM tasks WHERE is_active"
                )
            )
        ).fetchall()

        targets = _collect(rows, own_hosts)
        summary["checked"] = len(targets)
        if not targets:
            return summary

        sem = asyncio.Semaphore(settings.link_audit_concurrency)
        async with httpx.AsyncClient(
            timeout=settings.link_audit_http_timeout_sec,
            follow_redirects=True,
            headers={"User-Agent": "LMS link audit (tsk-521)"},
        ) as client:
            # return_exceptions=True намеренно: без него первая же ошибка
            # хранилища пробросилась бы наружу, оставив остальные проверки
            # висеть, а клиент HTTP закрылся бы у них под ногами.
            results = await asyncio.gather(
                *(_check_target(t, client, sem) for t in targets),
                return_exceptions=True,
            )

        outage = next((r for r in results if isinstance(r, DomainError)), None)
        if outage is not None:
            # Хранилище недоступно — прогон недостоверен целиком: «нет ответа»
            # не то же самое, что «файла нет».
            logger.error("tsk-521: проверка прервана: %s", outage.detail)
            summary["error"] = outage.detail
            return summary

        unexpected = next((r for r in results if isinstance(r, BaseException)), None)
        if unexpected is not None:
            # Программная ошибка не должна выглядеть как чистый прогон.
            logger.exception("tsk-521: проверка упала", exc_info=unexpected)
            summary["error"] = f"{type(unexpected).__name__}: {unexpected}"
            return summary

        broken = {t: reason for t, reason in zip(targets, results) if reason is not None}
        summary["broken"] = len(broken)
        # Список находок в сводке — чтобы в логах было видно, что именно
        # сломалось, не поднимая уведомление, и чтобы тесты проверяли конкретную
        # ссылку, а не счётчик по всей базе.
        summary["broken_targets"] = dict(sorted(broken.items())[:50])

        if not broken:
            logger.info("tsk-521: проверено ссылок=%s, битых нет", len(targets))
            return summary

        logger.warning(
            "tsk-521: битых ссылок %s из %s; примеры: %s",
            len(broken), len(targets), list(broken.items())[:5],
        )

        if await _recently_notified(db, settings.link_audit_notify_cooldown_hours):
            logger.info("tsk-521: уведомление уже отправляли недавно — молчим")
            return summary

        summary["notified"] = await _notify_methodists(
            db,
            broken=broken,
            owners=targets,
            max_examples=settings.link_audit_max_examples,
        )
        await db.commit()

    return summary


def start_scheduler() -> None:
    """Поднимает периодический тик проверки (если включён настройкой)."""
    global _scheduler
    settings = Settings()
    if not settings.link_audit_enabled:
        logger.info("tsk-521: проверка ссылок выключена (LINK_AUDIT_ENABLED)")
        return
    if _scheduler is not None:
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        link_audit_tick,
        IntervalTrigger(hours=settings.link_audit_interval_hours),
        id="link_audit_tick",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "tsk-521: проверка ссылок запущена, интервал %s ч",
        settings.link_audit_interval_hours,
    )


def stop_scheduler() -> None:
    """Останавливает тик при остановке приложения."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
