"""tsk-1361: персональная обязательность элемента — запись и чтение.

# Зачем

`requirement_level` — одна отметка на всех. Отступления для конкретного
ученика раньше вычислялись молча в каждом потребителе: прощение нового
(`content_grace_service`, tsk-692/1261/1294) и сокращение программы
(`program_scope_service`, tsk-798). Причину не видели ни ученик, ни учитель,
а потребители применяли правила вразнобой (инвентарь — в
`docs/specs/2026-10-15-plan-tsk1361-requirement-overrides.md`).

Теперь правило ЗАПИСЫВАЕТ отступление в `student_requirement_override` с
кодом и текстом причины. Расчёт остаётся тем же кодом, меняется только то,
что результат хранится и его можно проверить.

# Инварианты

* Человек главнее правила: правило не создаёт, не меняет и не отменяет
  строку `source='human'` и не пишет поверх неё.
* Отмена мягкая: `revoked_at` ставится, строка остаётся — история причин.
* Активна не больше одной строки на (ученик, вид, цель) — частичный
  уникальный индекс; вставка правила идёт `ON CONFLICT DO NOTHING`, чтобы
  гонка с учителем не роняла фоновую сверку.

# Фаза А (тень)

Строки пишет фоновая сверка (`requirement_override_sync_cron_service`),
потребители их ещё не читают. Переключение — фаза Б, после сверки.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.content_grace_service import (
    REASON_AHEAD,
    REASON_NEW_CONTENT,
    compute_grace_reasons,
)
from app.services.program_scope_service import excluded_courses_for

logger = logging.getLogger(__name__)

# Уровни, которые идут в счёт обязательного. Единая точка для новых
# потребителей (фаза Б сводит сюда копии литерала из других модулей).
REQUIRED_LEVELS: Tuple[str, ...] = ("required", "skippable")
LEVELS: Tuple[str, ...] = ("required", "recommended", "skippable")
TARGET_KINDS: Tuple[str, ...] = ("task", "material", "course")

# Растёт при изменении любого правила — по нему видно, какие строки
# посчитаны старой версией.
RULE_VERSION = 1

REASON_PROGRAM_TRIM = "program_trim"
REASON_TEACHER = "teacher"

REASON_TEXTS: Dict[str, str] = {
    REASON_NEW_CONTENT: "Добавлено после того, как вы прошли эту тему",
    REASON_AHEAD: "Добавлено, когда вы уже прошли дальше по теме",
    REASON_PROGRAM_TRIM: "Не входит в вашу программу до экзамена: не хватает времени до срока",
    REASON_TEACHER: "Так решил преподаватель",
}

Target = Tuple[str, int]


@dataclass(frozen=True)
class OverrideView:
    """Активное переопределение глазами потребителя."""

    level: str
    reason_code: str
    reason_text: str
    source: str


@dataclass
class SyncResult:
    """Итог сверки правил одного ученика."""

    inserted: int = 0
    revoked: int = 0
    kept: int = 0
    skipped_human: int = 0


async def desired_rule_overrides(db: AsyncSession, student_id: int) -> Dict[Target, str]:
    """Что правила считают нужным сейчас: цель → код причины.

    Прощение считается от каждого действующего корня зачисления (ровно как
    `graced_for_roots`), сокращение программы — по сохранённому плану
    объёма (`student_program_scope`).
    """
    root_ids = (
        await db.execute(
            text("SELECT course_id FROM user_courses WHERE user_id = :sid AND is_active = true"),
            {"sid": student_id},
        )
    ).scalars().all()

    desired: Dict[Target, str] = {}
    for root_id in sorted({int(r) for r in root_ids}):
        for target, code in (await compute_grace_reasons(db, student_id, root_id)).items():
            # Один элемент виден из двух корней — правило узла объяснимее.
            if desired.get(target) != REASON_NEW_CONTENT:
                desired[target] = code
    for course_id in await excluded_courses_for(db, student_id=student_id):
        desired[("course", int(course_id))] = REASON_PROGRAM_TRIM
    return desired


async def sync_rule_overrides(db: AsyncSession, student_id: int) -> SyncResult:
    """Привести строки правил ученика к `desired_rule_overrides`. Без коммита.

    Строки человека не трогаются. Строка правила, чья причина сменилась,
    отменяется и пишется заново — история показывает, когда сменилась причина.
    """
    desired = await desired_rule_overrides(db, student_id)
    rows = (
        await db.execute(
            text(
                "SELECT id, target_kind, target_id, source, reason_code "
                "  FROM student_requirement_override "
                " WHERE student_id = :sid AND revoked_at IS NULL"
            ),
            {"sid": student_id},
        )
    ).fetchall()

    result = SyncResult()
    human: set[Target] = set()
    current_rule: Dict[Target, Tuple[int, str]] = {}
    for row_id, kind, target_id, source, code in rows:
        target = (str(kind), int(target_id))
        if source == "human":
            human.add(target)
        else:
            current_rule[target] = (int(row_id), str(code))

    to_revoke = [
        row_id
        for target, (row_id, code) in current_rule.items()
        if desired.get(target) != code
    ]
    if to_revoke:
        await db.execute(
            text(
                "UPDATE student_requirement_override SET revoked_at = now() "
                " WHERE id = ANY(:ids) AND revoked_at IS NULL"
            ),
            {"ids": to_revoke},
        )
        result.revoked = len(to_revoke)

    for target, code in sorted(desired.items()):
        if target in human:
            result.skipped_human += 1
            continue
        existing = current_rule.get(target)
        if existing is not None and existing[1] == code:
            result.kept += 1
            continue
        inserted = await db.execute(
            text(
                "INSERT INTO student_requirement_override "
                "  (student_id, target_kind, target_id, level, reason_code, "
                "   reason_text, source, rule_version) "
                "VALUES (:sid, :kind, :tid, 'recommended', :code, :txt, 'rule', :ver) "
                "ON CONFLICT (student_id, target_kind, target_id) "
                "  WHERE revoked_at IS NULL DO NOTHING"
            ),
            {
                "sid": student_id,
                "kind": target[0],
                "tid": target[1],
                "code": code,
                "txt": REASON_TEXTS[code],
                "ver": RULE_VERSION,
            },
        )
        result.inserted += int(inserted.rowcount or 0)

    if result.inserted or result.revoked:
        logger.info(
            "tsk-1361: ученик %s — правила: добавлено %s, отменено %s, без изменений %s, "
            "под решением человека %s",
            student_id, result.inserted, result.revoked, result.kept, result.skipped_human,
        )
    return result


async def set_human_override(
    db: AsyncSession,
    *,
    student_id: int,
    target_kind: str,
    target_id: int,
    level: str,
    by_user_id: int,
    reason_text: Optional[str] = None,
) -> int:
    """Решение учителя: задать уровень элемента/узла ученику. Без коммита.

    Активная строка на эту цель (правила или прежнее решение человека)
    отменяется от имени учителя. Возвращает id новой строки.
    """
    if target_kind not in TARGET_KINDS:
        raise ValueError(f"неизвестный вид цели: {target_kind!r}")
    if level not in LEVELS:
        raise ValueError(f"неизвестный уровень: {level!r}")
    note = (reason_text or "").strip() or REASON_TEXTS[REASON_TEACHER]

    await db.execute(
        text(
            "UPDATE student_requirement_override "
            "   SET revoked_at = now(), revoked_by = :by "
            " WHERE student_id = :sid AND target_kind = :kind AND target_id = :tid "
            "   AND revoked_at IS NULL"
        ),
        {"by": by_user_id, "sid": student_id, "kind": target_kind, "tid": target_id},
    )
    new_id = (
        await db.execute(
            text(
                "INSERT INTO student_requirement_override "
                "  (student_id, target_kind, target_id, level, reason_code, "
                "   reason_text, source, created_by) "
                "VALUES (:sid, :kind, :tid, :lvl, :code, :txt, 'human', :by) RETURNING id"
            ),
            {
                "sid": student_id, "kind": target_kind, "tid": target_id, "lvl": level,
                "code": REASON_TEACHER, "txt": note, "by": by_user_id,
            },
        )
    ).scalar_one()
    logger.info(
        "tsk-1361: учитель %s задал ученику %s уровень %s для %s %s",
        by_user_id, student_id, level, target_kind, target_id,
    )
    return int(new_id)


async def revoke_human_override(
    db: AsyncSession, *, student_id: int, target_kind: str, target_id: int, by_user_id: int
) -> bool:
    """Снять решение учителя — элемент вернётся к курсу и правилам. Без коммита.

    Правило восстановит своё переопределение на следующей сверке.
    """
    res = await db.execute(
        text(
            "UPDATE student_requirement_override "
            "   SET revoked_at = now(), revoked_by = :by "
            " WHERE student_id = :sid AND target_kind = :kind AND target_id = :tid "
            "   AND source = 'human' AND revoked_at IS NULL"
        ),
        {"by": by_user_id, "sid": student_id, "kind": target_kind, "tid": target_id},
    )
    return bool(res.rowcount)


async def active_overrides(db: AsyncSession, student_id: int) -> Dict[Target, OverrideView]:
    """Все активные переопределения ученика одним запросом по индексу."""
    rows = (
        await db.execute(
            text(
                "SELECT target_kind, target_id, level, reason_code, reason_text, source "
                "  FROM student_requirement_override "
                " WHERE student_id = :sid AND revoked_at IS NULL"
            ),
            {"sid": student_id},
        )
    ).fetchall()
    return {
        (str(k), int(t)): OverrideView(level=str(lvl), reason_code=str(c), reason_text=str(txt), source=str(s))
        for k, t, lvl, c, txt, s in rows
    }
