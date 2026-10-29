"""Интерактивные тренажёры: выдача сценария, события, предложение после ошибки (tsk-1247).

Тренажёр — материал узла типа `trainer` (`content = {slug, scenario}`), уровень
`recommended`: в обход next-item и в счётчики прогресса не входит, поэтому
продвижение не блокирует, а место в узле ему даёт тот же `node_sequence`, что
и остальным материалам (П7). Второй вход в тренажёр — развилка
`content.checkpoint` у теории и предложение после неверного ответа на задание
узла (бриф tsk-1247, §3).

Где пишутся события (бриф Р4, исследование §D.5) — без новых таблиц:
* вошедший ученик — `learning_events` (`event_type` = тип события);
* гость — `guest_attempt` с `task_id = NULL`, как ответы квиза и диагностики:
  после входа строки переносит тот же `attribute-guest`, что и попытки;
* встройка на чужом сайте — только обезличенные счётчики (показ, старт, финиш,
  домен родителя) в `product_event` с `user_id = NULL`; ввод, шаги и коды
  ошибок не пишутся.

Рубильник `TRAINERS_ENABLED` (по умолчанию выключен): тип `trainer` не попадает
в выдачу ученику, `checkpoint` не отдаётся, ручки `/trainers/*` отвечают 404,
`trainer_suggestion` всегда null. Читается из окружения на каждый вызов, как
рубильники обхода (`node_sequence.node_sequence_enabled`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Iterable, Optional, Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.trainers import TrainerEvent, TrainerSuggestion
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

#: Тип материала-тренажёра.
TRAINER_TYPE = "trainer"
#: События встройки, которые идут в обезличенные счётчики (показ/старт/финиш).
EMBED_COUNTED = ("embed_view", "trainer_start", "trainer_done")
#: Поля события, которые пишутся в payload (ученик, гость).
_PAYLOAD_FIELDS = ("level", "step", "iter", "ok", "misconception", "ms", "stars", "answer")


def trainers_enabled() -> bool:
    """Рубильник `TRAINERS_ENABLED` (tsk-1247, по умолчанию выключен)."""
    return os.getenv("TRAINERS_ENABLED", "false").strip().lower() in ("true", "1", "yes")


# ── Сценарий ────────────────────────────────────────────────────────────────


async def find_trainer(db: AsyncSession, slug: str) -> Optional[dict[str, Any]]:
    """Активный материал-тренажёр по slug: `{material_id, course_id, title, content}`.

    Один тренажёр может стоять в нескольких курсах (узел 106 — в четырёх);
    сценарий у них один, берётся последний изданный.
    """
    row = (
        await db.execute(
            text(
                """
                SELECT id, course_id, title, content
                FROM materials
                WHERE type = 'trainer' AND is_active
                  AND content->>'slug' = :slug
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"slug": slug},
        )
    ).mappings().first()
    if row is None:
        return None
    return {
        "material_id": int(row["id"]),
        "course_id": int(row["course_id"]),
        "title": row["title"],
        "content": row["content"],
    }


def trainer_title(content: Any, fallback: str) -> str:
    """Название тренажёра: `scenario.title`, иначе заголовок материала."""
    if isinstance(content, dict):
        scenario = content.get("scenario")
        if isinstance(scenario, dict) and isinstance(scenario.get("title"), str):
            return scenario["title"]
    return fallback


def scenario_version(scenario: Any) -> str:
    """Версия сценария для ETag: хэш канонического JSON (меняется с любой правкой)."""
    canonical = json.dumps(scenario, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def misconception_codes(content: Any) -> set[str]:
    """Коды ошибок сценария (ручка событий принимает только их)."""
    scenario = content.get("scenario") if isinstance(content, dict) else None
    if not isinstance(scenario, dict) or not isinstance(scenario.get("misconceptions"), dict):
        return set()
    return set(scenario["misconceptions"])


async def slug_conflict(
    db: AsyncSession,
    content: Any,
    *,
    exclude_id: Optional[int] = None,
    exclude_key: Optional[tuple[int, str]] = None,
) -> Optional[str]:
    """Текст ошибки, если slug уже занят тренажёром с другим сценарием, иначе None.

    Публичный `GET /trainers/{slug}` и встройки на чужих сайтах смотрят на slug:
    второй материал с тем же slug и другим сценарием молча подменил бы их.
    Копии одного сценария в разных курсах разрешены. `exclude_id` /
    `exclude_key` (course_id, external_uid) — сам записываемый материал.
    """
    if not isinstance(content, dict) or not content.get("slug"):
        return None
    rows = (
        await db.execute(
            text(
                """
                SELECT id, course_id, external_uid, content->'scenario'
                FROM materials
                WHERE type = 'trainer' AND content->>'slug' = :slug
                """
            ),
            {"slug": content["slug"]},
        )
    ).fetchall()
    for mid, cid, ext, scenario in rows:
        if exclude_id is not None and int(mid) == exclude_id:
            continue
        if exclude_key is not None and (int(cid), ext) == exclude_key:
            continue
        if scenario != content.get("scenario"):
            return (
                f"slug «{content['slug']}» уже занят тренажёром {mid} (курс {cid}) с другим "
                "сценарием — обновите тот материал или выберите другой slug"
            )
    return None


# ── События ─────────────────────────────────────────────────────────────────


def check_misconceptions(events: Sequence[TrainerEvent], allowed: set[str]) -> None:
    """Отказ 422, если код ошибки не из словаря сценария (бриф Р3)."""
    unknown = sorted({e.misconception for e in events if e.misconception and e.misconception not in allowed})
    if unknown:
        raise DomainError(
            f"Неизвестные коды ошибок для этого тренажёра: {', '.join(unknown)}",
            status_code=422,
            payload={"code": "unknown_misconception", "misconceptions": unknown},
        )


def _event_payload(event: TrainerEvent, trainer: dict[str, Any], context: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "slug": trainer["content"]["slug"],
        "trainer_material_id": trainer["material_id"],
        "context": context,
    }
    for name in _PAYLOAD_FIELDS:
        value = getattr(event, name)
        if value is not None:
            payload[name] = value
    return payload


async def record_student_events(
    db: AsyncSession, student_id: int, trainer: dict[str, Any], events: Iterable[TrainerEvent]
) -> int:
    """Записать события вошедшего ученика в `learning_events`."""
    rows = [
        {
            "student_id": student_id,
            "event_type": e.type,
            "payload": json.dumps(_event_payload(e, trainer, "student"), ensure_ascii=False),
        }
        for e in events
    ]
    await db.execute(
        text(
            """
            INSERT INTO learning_events (student_id, event_type, payload, created_at)
            VALUES (:student_id, :event_type, CAST(:payload AS jsonb), now())
            """
        ),
        rows,
    )
    return len(rows)


async def record_guest_events(
    db: AsyncSession, guest_session_id: UUID, trainer: dict[str, Any], events: Iterable[TrainerEvent]
) -> int:
    """Записать события гостя в `guest_attempt` (без задания, `task_id = NULL`).

    Сессия должна существовать: иначе 400, как у ответа гостя на демо-задачу.
    `is_correct` — признак `ok` шага; после входа строки атрибутируются вместе
    с попытками сессии (`guest_attribution_service`).
    """
    exists = (
        await db.execute(
            text("UPDATE guest_session SET last_used_at = now() WHERE id = :id RETURNING id"),
            {"id": guest_session_id},
        )
    ).first()
    if exists is None:
        raise DomainError(
            "Гостевая сессия не найдена. Сначала вызовите POST /learning/guest/session.",
            status_code=400,
        )
    rows = []
    for e in events:
        payload = {"kind": "trainer_event", "event_type": e.type, **_event_payload(e, trainer, "guest")}
        rows.append(
            {
                "gs": guest_session_id,
                "answer": json.dumps(payload, ensure_ascii=False),
                "ok": e.ok if e.type == "trainer_step" else None,
            }
        )
    await db.execute(
        text(
            """
            INSERT INTO guest_attempt (guest_session_id, task_id, answer_json, is_correct)
            VALUES (:gs, NULL, CAST(:answer AS jsonb), :ok)
            """
        ),
        rows,
    )
    return len(rows)


async def record_embed_counters(
    db: AsyncSession, trainer: dict[str, Any], events: Iterable[TrainerEvent]
) -> int:
    """Обезличенные счётчики встройки в `product_event` (`user_id = NULL`).

    Пишутся только показ/старт/финиш и домен родителя. Остальные события
    встройки (шаги, ответы, коды ошибок, развилка) отбрасываются: ввод
    посетителя чужого сайта на сервер не попадает.

    Счётчик — не учебные данные: если запись не прошла (например, у
    `product_event` нет партиции на текущий месяц), ответ всё равно 202,
    а сбой виден в журнале.
    """
    rows = [
        {
            "event_type": e.type,
            "props": json.dumps(
                {"slug": trainer["content"]["slug"], "context": "embed", "parent_domain": e.parent_domain},
                ensure_ascii=False,
            ),
        }
        for e in events
        if e.type in EMBED_COUNTED
    ]
    if not rows:
        return 0
    try:
        async with db.begin_nested():
            await db.execute(
                text(
                    """
                    INSERT INTO product_event (user_id, event_type, ts, properties)
                    VALUES (NULL, :event_type, now(), CAST(:props AS jsonb))
                    """
                ),
                rows,
            )
    except Exception:  # noqa: BLE001 — счётчик не роняет ответ встройке
        logger.warning(
            "tsk-1247: счётчики встройки не записаны slug=%s n=%s",
            trainer["content"]["slug"], len(rows), exc_info=True,
        )
        return 0
    return len(rows)


# ── Предложение тренажёра после неверного ответа ────────────────────────────


async def suggestion_for_course(db: AsyncSession, course_id: int) -> Optional[TrainerSuggestion]:
    """Первый активный тренажёр узла (по порядку материалов) или None."""
    if not trainers_enabled():
        return None
    row = (
        await db.execute(
            text(
                """
                SELECT title, content
                FROM materials
                WHERE course_id = :cid AND type = 'trainer' AND is_active
                ORDER BY order_position ASC NULLS LAST, id ASC
                LIMIT 1
                """
            ),
            {"cid": course_id},
        )
    ).mappings().first()
    if row is None or not isinstance(row["content"], dict) or not row["content"].get("slug"):
        return None
    return TrainerSuggestion(
        slug=row["content"]["slug"], title=trainer_title(row["content"], row["title"]),
    )


async def suggestion_after_last_answer(
    db: AsyncSession, student_id: int, task_id: int, course_id: int
) -> Optional[TrainerSuggestion]:
    """Предложение для next-item: последний ответ ученика на задание неверный.

    При выключенном рубильнике запросов нет вовсе.
    """
    if not trainers_enabled():
        return None
    last = (
        await db.execute(
            text(
                """
                SELECT is_correct FROM task_results
                WHERE user_id = :sid AND task_id = :tid
                ORDER BY submitted_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"sid": student_id, "tid": task_id},
        )
    ).first()
    if last is None or last[0] is not False:
        return None
    return await suggestion_for_course(db, course_id)


# ── Выдача материалов ученику ───────────────────────────────────────────────


def _strip_checkpoint(content: Any) -> Any:
    if isinstance(content, dict) and "checkpoint" in content:
        return {k: v for k, v in content.items() if k != "checkpoint"}
    return content


async def student_visible(db: AsyncSession, materials: Sequence[Any]) -> tuple[list[Any], dict[int, Any]]:
    """Что из материалов показывать ученику и с каким `content` (tsk-1247).

    Возвращает (материалы без скрытых тренажёров, {id: новый content}) — во
    втором словаре только материалы, у которых `content` надо подменить:
    * рубильник выключен — тренажёров нет, у теории снята развилка;
    * включён — развилка остаётся, только если её тренажёр — активный
      материал `trainer` того же курса (иначе «Нет» вело бы в 404).

    ORM-объекты не трогаются: правка их `content` ушла бы в базу при flush.
    """
    enabled = trainers_enabled()
    visible = [m for m in materials if enabled or m.type != TRAINER_TYPE]
    with_checkpoint = [
        m for m in visible if isinstance(m.content, dict) and "checkpoint" in m.content
    ]
    if not with_checkpoint:
        return visible, {}
    if not enabled:
        return visible, {m.id: _strip_checkpoint(m.content) for m in with_checkpoint}
    targets = {
        int(m.content["checkpoint"].get("trainer_material_id") or 0)
        for m in with_checkpoint
        if isinstance(m.content.get("checkpoint"), dict)
    }
    live = {
        (int(r[0]), int(r[1]))
        for r in (
            await db.execute(
                text(
                    "SELECT id, course_id FROM materials "
                    "WHERE id = ANY(:ids) AND type = 'trainer' AND is_active"
                ),
                {"ids": sorted(targets)},
            )
        ).fetchall()
    }
    replaced: dict[int, Any] = {}
    for m in with_checkpoint:
        cp = m.content.get("checkpoint")
        target = int(cp.get("trainer_material_id") or 0) if isinstance(cp, dict) else 0
        if (target, int(m.course_id)) not in live:
            replaced[m.id] = _strip_checkpoint(m.content)
    return visible, replaced

