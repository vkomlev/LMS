# app/services/attachment_audit_service.py
"""tsk-593: суточная проверка «ссылка на вложение есть, файла нет».

**Зачем.** Отдельной таблицы вложений в базе нет: имя файла живёт строкой
внутри `answer_json`, в колонке `messages.attachment_id` и в
`student_payment.receipt_file`. База не знает, существует ли файл на самом
деле — ровно поэтому дефект tsk-575 съедал работы учеников месяцами, а узнали
о нём, только когда преподаватель ткнул в ссылку и получил ошибку. Брат-близнец
проверки ссылок в контенте курсов (tsk-521), но источник другой: работы
учеников, переписка и чеки, а не тела материалов и заданий.

**Молчание = всё хорошо.** Уведомление уходит ТОЛЬКО про НОВЫЕ утраты. Всё, что
потеряно раньше (180 файлов до починки tsk-575), записано в
`attachment_missing_seen` и больше не тревожит: ежедневное одинаковое
уведомление про невосстановимое — это шум, за которым перестанут замечать
настоящую новую потерю.

**Кому уходит.** Учебные вложения и переписка — методистам; чеки об оплате —
маркетологам: платёжный документ методист всё равно не восстановит.

**Отказ хранилища прерывает прогон целиком.** «Нет ответа» — не то же самое,
что «файла нет»: иначе одна сетевая заминка нарисовала бы утраченными все
вложения разом и разбудила бы всех.

Multi-worker safety — как у соседних тиков: advisory-lock уровня транзакции со
своим ключом. Один worker за тик делает работу, остальные мгновенно отступают.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.session import async_session_factory
from app.services import attachment_storage, inbox_service
from app.services.attempt_attachments import collect_attachment_ids
from app.utils.exceptions import DomainError

logger = logging.getLogger("app.attachment_audit")

# Ключ advisory-lock. Не должен пересекаться с соседними тиками:
# проверка ссылок в контенте — 0x4C494E4B ("LINK"), escalation — "Y6TS".
_ATTACHMENT_AUDIT_LOCK_KEY = 0x41545443  # ascii "ATTC"

NOTIFICATION_KIND = "missing_attachments"

#: Кому уходит уведомление о потере в этом пространстве.
_AUDIENCE: Dict[str, str] = {
    attachment_storage.ATTEMPTS: "methodist",
    attachment_storage.MESSAGES: "methodist",
    attachment_storage.RECEIPTS: "marketer",
}

#: Как называть пространство в тексте для человека.
_HUMAN_SPACE: Dict[str, str] = {
    attachment_storage.ATTEMPTS: "вложение к работе ученика",
    attachment_storage.MESSAGES: "файл в переписке",
    attachment_storage.RECEIPTS: "чек об оплате",
}

#: Ссылка на файл: (пространство, имя) → где встретилась.
Reference = Tuple[str, str]
Owner = Tuple[str, int]


async def _collect_references(db: AsyncSession) -> Dict[Reference, Owner]:
    """Собирает все ссылки на файлы вложений из базы.

    Один владелец на ссылку: если одно и то же имя встретилось дважды,
    достаточно любого — запись нужна, чтобы человек нашёл, откуда она.
    """
    found: Dict[Reference, Owner] = {}

    rows = (
        await db.execute(
            text(
                "SELECT id, answer_json FROM task_results "
                "WHERE jsonb_array_length("
                "  COALESCE(answer_json->'response'->'meta'->'attachments', '[]'::jsonb)"
                ") > 0"
            )
        )
    ).fetchall()
    for result_id, answer_json in rows:
        for name in collect_attachment_ids(answer_json):
            found.setdefault((attachment_storage.ATTEMPTS, name), ("task_result", int(result_id)))

    rows = (
        await db.execute(
            text("SELECT id, attachment_id FROM messages WHERE attachment_id IS NOT NULL")
        )
    ).fetchall()
    for message_id, name in rows:
        if name:
            found.setdefault((attachment_storage.MESSAGES, name), ("message", int(message_id)))

    rows = (
        await db.execute(
            text("SELECT id, receipt_file FROM student_payment WHERE receipt_file IS NOT NULL")
        )
    ).fetchall()
    for payment_id, name in rows:
        if name:
            found.setdefault((attachment_storage.RECEIPTS, name), ("payment", int(payment_id)))

    return found


async def _missing(references: Sequence[Reference]) -> List[Reference]:
    """Какие из ссылок не находят файла в хранилище (по пространству, пачкой)."""
    missing: List[Reference] = []
    for space in attachment_storage.SPACES:
        names = [name for sp, name in references if sp == space]
        if not names:
            continue
        present = await attachment_storage.existing_names(space, names)
        missing.extend((space, name) for name in names if name not in present)
    return missing


async def _known_missing(db: AsyncSession) -> set[Reference]:
    """Утраты, про которые уже знают (исходный уровень + прошлые прогоны)."""
    rows = (await db.execute(text("SELECT space, name FROM attachment_missing_seen"))).fetchall()
    return {(str(space), str(name)) for space, name in rows}


async def _remember(db: AsyncSession, items: Sequence[Tuple[Reference, Owner]]) -> None:
    """Запоминает новые утраты, чтобы завтра не сообщать о них снова."""
    for (space, name), (owner_kind, owner_id) in items:
        await db.execute(
            text(
                "INSERT INTO attachment_missing_seen (space, name, owner_kind, owner_id) "
                "VALUES (:space, :name, :kind, :oid) "
                "ON CONFLICT (space, name) DO NOTHING"
            ),
            {"space": space, "name": name, "kind": owner_kind, "oid": owner_id},
        )


async def _forget_healed(db: AsyncSession, still_missing: Sequence[Reference]) -> int:
    """Убирает из памяти то, что перестало быть утратой.

    Файл вернулся (ученик перезалил) или исчезла сама ссылка на него — значит
    повторная потеря того же имени обязана снова считаться новой. Без этой
    уборки она прошла бы молча.
    """
    known = await _known_missing(db)
    healed = known - set(still_missing)
    for space, name in healed:
        await db.execute(
            text("DELETE FROM attachment_missing_seen WHERE space = :space AND name = :name"),
            {"space": space, "name": name},
        )
    return len(healed)


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


async def _recipients(db: AsyncSession, role: str) -> List[int]:
    """Кому слать: пользователи с этой ролью."""
    res = await db.execute(
        text(
            "SELECT ur.user_id FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id WHERE r.name = :role"
        ),
        {"role": role},
    )
    return [int(row[0]) for row in res.fetchall()]


async def _notify(
    db: AsyncSession,
    *,
    fresh: List[Tuple[Reference, Owner]],
    max_examples: int,
) -> int:
    """Кладёт уведомления адресатам по пространствам. Возвращает число уведомлённых."""
    notified = 0
    by_role: Dict[str, List[Tuple[Reference, Owner]]] = {}
    for item in fresh:
        by_role.setdefault(_AUDIENCE[item[0][0]], []).append(item)

    for role, items in sorted(by_role.items()):
        recipients = await _recipients(db, role)
        if not recipients:
            logger.warning(
                "tsk-593: некому сообщить об утратах — нет пользователей с ролью %r", role
            )
            continue

        examples = [
            {
                "space": space,
                "name": name,
                "where": f"{owner_kind} {owner_id}",
            }
            for (space, name), (owner_kind, owner_id) in sorted(items)[:max_examples]
        ]
        lines = [
            f"Пропало файлов: {len(items)}. Ссылка на файл в базе есть, "
            f"самого файла в хранилище нет.",
            "",
        ]
        for ex in examples:
            lines.append(
                f"• {_HUMAN_SPACE.get(ex['space'], ex['space'])}: {ex['name']} ({ex['where']})"
            )
        if len(items) > len(examples):
            lines.append(f"…и ещё {len(items) - len(examples)}.")
        lines.append("")
        lines.append(
            "Восстановить файл нечем — попросите приложить его заново. "
            "Если такое повторяется, это сбой хранилища, а не действия людей."
        )

        payload = {
            "missing_count": len(items),
            "examples": examples,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        for user_id in recipients:
            await inbox_service.create_for_user(
                db,
                user_id=user_id,
                kind=NOTIFICATION_KIND,
                title="Пропали файлы вложений",
                content="\n".join(lines),
                payload=payload,
                created_by=None,
            )
            notified += 1
    return notified


async def attachment_audit_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> dict:
    """Один проход проверки. Возвращает сводку для логов и тестов.

    `session_factory` — точка подмены источника сессий: в проде APScheduler
    зовёт тик без аргументов, тесты передают свою фабрику.
    """
    settings = Settings()
    factory = session_factory or async_session_factory
    summary: dict = {"locked": False, "checked": 0, "missing": 0, "fresh": 0, "notified": 0}

    async with factory() as db:
        got = await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:k) AS locked"),
            {"k": _ATTACHMENT_AUDIT_LOCK_KEY},
        )
        if not bool(got.scalar()):
            logger.info("tsk-593: тик пропущен — работу делает другой worker")
            return summary
        summary["locked"] = True

        references = await _collect_references(db)
        summary["checked"] = len(references)
        if not references:
            return summary

        try:
            missing = await _missing(list(references.keys()))
        except DomainError as exc:
            # Хранилище недоступно — прогон недостоверен целиком.
            logger.error("tsk-593: проверка прервана: %s", exc.detail)
            summary["error"] = exc.detail
            return summary

        summary["missing"] = len(missing)
        summary["healed"] = await _forget_healed(db, missing)

        known = await _known_missing(db)
        fresh = [(ref, references[ref]) for ref in missing if ref not in known]
        summary["fresh"] = len(fresh)
        # Список находок в сводке — чтобы в логах и тестах была конкретная
        # ссылка, а не счётчик по всей базе.
        summary["fresh_names"] = [f"{space}:{name}" for (space, name), _o in sorted(fresh)[:50]]

        if not fresh:
            logger.info(
                "tsk-593: проверено ссылок=%s, утрачено=%s (все известны), новых нет",
                len(references), len(missing),
            )
            await db.commit()
            return summary

        await _remember(db, fresh)
        logger.warning(
            "tsk-593: новых утрат %s из %s ссылок; примеры: %s",
            len(fresh), len(references), summary["fresh_names"][:5],
        )

        if await _recently_notified(db, settings.attachment_audit_notify_cooldown_hours):
            logger.info("tsk-593: уведомление уже отправляли недавно — молчим")
            await db.commit()
            return summary

        summary["notified"] = await _notify(
            db, fresh=fresh, max_examples=settings.attachment_audit_max_examples
        )
        await db.commit()

    return summary


_scheduler: Optional[AsyncIOScheduler] = None


def start_scheduler() -> None:
    """Поднимает суточный тик проверки (если включён настройкой)."""
    global _scheduler
    settings = Settings()
    if not settings.attachment_audit_enabled:
        logger.info("tsk-593: проверка вложений выключена (ATTACHMENT_AUDIT_ENABLED)")
        return
    if _scheduler is not None:
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        attachment_audit_tick,
        IntervalTrigger(hours=settings.attachment_audit_interval_hours),
        id="attachment_audit_tick",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "tsk-593: проверка вложений запущена, интервал %s ч",
        settings.attachment_audit_interval_hours,
    )


def stop_scheduler() -> None:
    """Останавливает тик при остановке приложения."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
