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
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.content_grace_service import (
    REASON_AHEAD,
    REASON_NEW_CONTENT,
    compute_grace_reasons,
    invalidate_grace_cache,
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

# Пространство ключей advisory-блокировки «ученик» (двухаргументная форма:
# (пространство, student_id)) — сверка и решения учителя по одному ученику
# идут строго по очереди, иначе запрос учителя мог упасть на уникальности.
_LOCK_NAMESPACE = 1361


async def _lock_student(db: AsyncSession, student_id: int) -> None:
    """Транзакционная блокировка изменений переопределений одного ученика."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :sid)"),
        {"ns": _LOCK_NAMESPACE, "sid": student_id},
    )


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
    await _lock_student(db, student_id)
    # Отметка свежести — момент НАЧАЛА транзакции (`now()`), а не конца:
    # сдача, пришедшая во время расчёта, окажется новее отметки и попадёт в
    # следующий быстрый тик, а не потеряется.
    await db.execute(
        text(
            "INSERT INTO student_requirement_sync (student_id, synced_at) "
            "VALUES (:sid, now()) "
            "ON CONFLICT (student_id) DO UPDATE SET synced_at = EXCLUDED.synced_at"
        ),
        {"sid": student_id},
    )
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

    await _lock_student(db, student_id)
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
    invalidate_grace_cache(db)
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
    await _lock_student(db, student_id)
    res = await db.execute(
        text(
            "UPDATE student_requirement_override "
            "   SET revoked_at = now(), revoked_by = :by "
            " WHERE student_id = :sid AND target_kind = :kind AND target_id = :tid "
            "   AND source = 'human' AND revoked_at IS NULL"
        ),
        {"by": by_user_id, "sid": student_id, "kind": target_kind, "tid": target_id},
    )
    invalidate_grace_cache(db)
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


# Итоговый уровень элементов ученика, у которых есть хоть одно применимое
# переопределение. Узел (`course`) раскрывается на поддерево по
# `course_parents` (`path` — защита от цикла, как в соседних обходах).
# Порядок (решение оператора 15.10): решение человека главнее правила на
# любом уровне; внутри одного источника — ближайшее к элементу (сам элемент —
# глубина 0, его курс — 1, предки — дальше).
_RESOLVE_SQL = """
/* tsk1361-resolve */
WITH RECURSIVE ov AS (
    SELECT id, target_kind, target_id, level, source, reason_code, reason_text, created_at
      FROM student_requirement_override
     WHERE student_id = :sid AND revoked_at IS NULL
),
node AS (
    SELECT o.target_id AS course_id, o.level, o.source, o.reason_code,
           o.reason_text, o.created_at, o.id AS override_id, 1 AS depth,
           ARRAY[o.target_id] AS path
      FROM ov o WHERE o.target_kind = 'course'
    UNION ALL
    SELECT cp.course_id, n.level, n.source, n.reason_code, n.reason_text,
           n.created_at, n.override_id, n.depth + 1, n.path || cp.course_id
      FROM node n
      JOIN course_parents cp ON cp.parent_course_id = n.course_id
     WHERE NOT cp.course_id = ANY(n.path)
),
cand AS (
    SELECT 'task' AS kind, t.id AS item_id, n.level, n.source, n.reason_code,
           n.reason_text, n.created_at, n.override_id, n.depth
      FROM node n JOIN tasks t ON t.course_id = n.course_id AND t.is_active
    UNION ALL
    SELECT 'material', m.id, n.level, n.source, n.reason_code, n.reason_text,
           n.created_at, n.override_id, n.depth
      FROM node n JOIN materials m ON m.course_id = n.course_id AND m.is_active
    UNION ALL
    SELECT o.target_kind, o.target_id, o.level, o.source, o.reason_code,
           o.reason_text, o.created_at, o.id, 0
      FROM ov o WHERE o.target_kind IN ('task', 'material')
),
best AS (
    SELECT DISTINCT ON (kind, item_id)
           kind, item_id, level, source, reason_code, reason_text
      FROM cand
     -- Ничья (узел на одной глубине по двум путям — подкурс у нескольких
     -- родителей): побеждает более позднее решение, чтобы выбор не был случайным.
     -- tsk-996: строки правил одной сверки пишутся одной транзакцией — равный
     -- created_at; тогда побеждает строка с большим id (выбор может пересмотреть
     -- оператор).
     ORDER BY kind, item_id, (source = 'human') DESC, depth ASC, created_at DESC,
              override_id DESC
)
SELECT b.kind, b.item_id, b.level, b.source, b.reason_code, b.reason_text,
       COALESCE(t.requirement_level, m.requirement_level) AS base_level
  FROM best b
  LEFT JOIN tasks t ON b.kind = 'task' AND t.id = b.item_id AND t.is_active
  LEFT JOIN materials m ON b.kind = 'material' AND m.id = b.item_id AND m.is_active
 WHERE COALESCE(t.id, m.id) IS NOT NULL
"""


@dataclass(frozen=True)
class ResolvedLevels:
    """Итог по ученику: что понижено, что повышено и почему."""

    # Элементы, обязательные по курсу, но не обязательные ученику.
    demoted: frozenset[Target] = frozenset()
    # Элементы, необязательные по курсу, но обязательные ученику (решение учителя).
    promoted: frozenset[Target] = frozenset()
    # Причина для каждого элемента, у которого итоговый уровень отличается от курса.
    reasons: Dict[Target, OverrideView] = field(default_factory=dict)


async def resolve_levels(db: AsyncSession, student_id: int) -> ResolvedLevels:
    """Единый источник персонального уровня (tsk-1361). Один запрос по индексу.

    Возвращает только элементы, у которых итоговый уровень отличается от
    уровня в курсе; для остальных действует `requirement_level` как раньше.
    """
    rows = (await db.execute(text(_RESOLVE_SQL), {"sid": student_id})).fetchall()
    demoted: set[Target] = set()
    promoted: set[Target] = set()
    reasons: Dict[Target, OverrideView] = {}
    for kind, item_id, level, source, code, reason, base in rows:
        target = (str(kind), int(item_id))
        was_required = base in REQUIRED_LEVELS
        now_required = level in REQUIRED_LEVELS
        if was_required == now_required:
            continue
        (demoted if was_required else promoted).add(target)
        reasons[target] = OverrideView(
            level=str(level), reason_code=str(code), reason_text=str(reason), source=str(source)
        )
    return ResolvedLevels(
        demoted=frozenset(demoted), promoted=frozenset(promoted), reasons=reasons
    )


# Ученики с действующим зачислением, чьи строки могли устареть: отметки нет
# вовсе, либо после неё появилось что-то, от чего зависят правила, — сдача или
# её проверка, пропуск, зачёт материала, новый план объёма, новое зачисление.
#
# Запас `:margin` назад от отметки: время сдачи ставит приложение ДО коммита,
# и сдача, закоммиченная уже после чтения сверки, иначе оказалась бы «старше
# отметки» и ждала бы часового прохода. Цена — лишний пересчёт раз-другой.
_STALE_BY_STUDENT_SQL = """
/* tsk1361-stale */
WITH s AS (
    SELECT DISTINCT uc.user_id AS sid FROM user_courses uc WHERE uc.is_active
),
m AS (
    SELECT s.sid, rs.synced_at - CAST(:margin AS interval) AS since
      FROM s LEFT JOIN student_requirement_sync rs ON rs.student_id = s.sid
)
SELECT m.sid
  FROM m
 WHERE m.since IS NULL
    OR EXISTS (SELECT 1 FROM task_results tr
                WHERE tr.user_id = m.sid
                  AND GREATEST(tr.submitted_at, tr.checked_at) > m.since)
    OR EXISTS (SELECT 1 FROM student_task_progress stp
                WHERE stp.student_id = m.sid AND stp.updated_at > m.since)
    OR EXISTS (SELECT 1 FROM student_material_progress smp
                WHERE smp.student_id = m.sid
                  AND GREATEST(smp.completed_at, smp.skipped_at) > m.since)
    OR EXISTS (SELECT 1 FROM student_program_scope sps
                WHERE sps.student_id = m.sid AND sps.computed_at > m.since)
    OR EXISTS (SELECT 1 FROM user_courses uc2
                WHERE uc2.user_id = m.sid AND uc2.added_at > m.since)
"""

# Ученики, у которых после ИХ отметки правили содержимое их деревьев. Курс с
# правкой поднимается вверх до корней вместе с моментом правки; сравнение — с
# отметкой каждого ученика, а не с самой старой (иначе любая правка гоняла бы
# всё дерево каждую минуту до часового прохода).
_STALE_BY_CONTENT_SQL = """
/* tsk1361-stale-content */
WITH RECURSIVE changed AS (
    SELECT course_id, max(GREATEST(created_at, updated_at)) AS changed_at
      FROM (
        SELECT course_id, created_at, updated_at FROM tasks
         WHERE GREATEST(created_at, updated_at) > :since
        UNION ALL
        SELECT course_id, created_at, updated_at FROM materials
         WHERE GREATEST(created_at, updated_at) > :since
      ) x
     GROUP BY course_id
),
up AS (
    SELECT c.course_id AS node_id, c.changed_at, ARRAY[c.course_id] AS path FROM changed c
    UNION ALL
    SELECT cp.parent_course_id, u.changed_at, u.path || cp.parent_course_id
      FROM up u JOIN course_parents cp ON cp.course_id = u.node_id
     WHERE NOT cp.parent_course_id = ANY(u.path)
)
SELECT DISTINCT uc.user_id
  FROM user_courses uc
  JOIN up ON up.node_id = uc.course_id
  JOIN student_requirement_sync rs ON rs.student_id = uc.user_id
 WHERE uc.is_active
   AND up.changed_at > rs.synced_at - CAST(:margin AS interval)
"""

_STALE_MARGIN = timedelta(minutes=2)


async def stale_students(db: AsyncSession) -> list[int]:
    """Ученики, чьи переопределения надо пересчитать в быстром тике.

    Правка связей подкурсов (`course_parents`) отметки времени не имеет и
    здесь не видна — её ловит часовая полная сверка.
    """
    ids = {
        int(r)
        for r in (
            await db.execute(text(_STALE_BY_STUDENT_SQL), {"margin": _STALE_MARGIN})
        ).scalars().all()
    }
    since = (
        # Только действующие ученики: отметка отчисленного стоит на месте, и
        # по ней каждый тик превращался бы в полную сверку.
        await db.execute(
            text(
                "SELECT min(rs.synced_at) FROM student_requirement_sync rs "
                " WHERE EXISTS (SELECT 1 FROM user_courses uc "
                "                WHERE uc.user_id = rs.student_id AND uc.is_active)"
            )
        )
    ).scalar()
    if since is not None:
        ids |= {
            int(r)
            for r in (
                await db.execute(
                    text(_STALE_BY_CONTENT_SQL),
                    {"since": since - _STALE_MARGIN, "margin": _STALE_MARGIN},
                )
            ).scalars().all()
        }
    return sorted(ids)


# ─── Экраны ученика и учителя (фаза Б3) ────────────────────────────────────


def reason_payload(view: Optional[OverrideView]) -> Optional[dict]:
    """Причина в форме `RequirementReason` для ответа API."""
    if view is None:
        return None
    return {"code": view.reason_code, "text": view.reason_text, "source": view.source}


@dataclass(frozen=True)
class ItemRequirement:
    """Уровень элемента глазами ученика: как его видят экраны прямо сейчас."""

    level: str  # итоговый для ученика
    course_level: str  # уровень в курсе
    reason: Optional[OverrideView]  # почему отличается; решение учителя — всегда


async def requirement_view(
    db: AsyncSession, student_id: int, targets: list[Target]
) -> Dict[Target, ItemRequirement]:
    """Уровень и причина для набора заданий/материалов ученика.

    Итоговый уровень берётся ровно тем же путём, что у движка
    (`graced_for_roots` — с рубильником или без), поэтому экран не может
    разойтись со «следующим шагом». Причина показывается, только если уровень
    действительно отличается от курса, — кроме решения учителя: его учитель
    видит всегда, даже когда оно пока ничего не меняет.
    """
    # Отложенный импорт ради симметрии с content_grace_service (кольцо).
    from app.services.content_grace_service import graced_for_roots

    task_ids = sorted({i for k, i in targets if k == "task"})
    material_ids = sorted({i for k, i in targets if k == "material"})
    if not task_ids and not material_ids:
        return {}
    base_rows = (
        await db.execute(
            text(
                "SELECT 'task', id, requirement_level FROM tasks WHERE id = ANY(CAST(:t AS int[])) "
                "UNION ALL "
                "SELECT 'material', id, requirement_level FROM materials "
                " WHERE id = ANY(CAST(:m AS int[]))"
            ),
            {"t": task_ids, "m": material_ids},
        )
    ).fetchall()
    graced = await graced_for_roots(db, student_id)
    reasons = (await resolve_levels(db, student_id)).reasons
    human = await active_overrides(db, student_id)

    result: Dict[Target, ItemRequirement] = {}
    for kind, item_id, base in base_rows:
        target = (str(kind), int(item_id))
        demoted = int(item_id) in (graced.tasks if kind == "task" else graced.materials)
        level = "recommended" if demoted and base in REQUIRED_LEVELS else str(base)
        reason = reasons.get(target) if level != base else None
        own = human.get(target)
        if reason is None and own is not None and own.source == "human":
            reason = own
        result[target] = ItemRequirement(level=level, course_level=str(base), reason=reason)
    return result


_AFFECTED_SQL = """
/* tsk1361-affected */
WITH RECURSIVE sub AS (
    SELECT CAST(:cid AS integer) AS cid, ARRAY[CAST(:cid AS integer)] AS path
    UNION ALL
    SELECT cp.course_id, s.path || cp.course_id
      FROM sub s JOIN course_parents cp ON cp.parent_course_id = s.cid
     WHERE NOT cp.course_id = ANY(s.path)
)
SELECT
    (SELECT count(*) FROM tasks
      WHERE course_id IN (SELECT cid FROM sub) AND is_active
        AND requirement_level = ANY(:lv)),
    (SELECT count(*) FROM materials
      WHERE course_id IN (SELECT cid FROM sub) AND is_active
        AND requirement_level = ANY(:lv))
"""


async def affected_counts(db: AsyncSession, target_kind: str, target_id: int) -> Tuple[int, int]:
    """Сколько обязательных по курсу заданий и материалов задевает решение.

    Для подкурса — всё его поддерево (учитель видит масштаб до сохранения).
    """
    if target_kind == "task":
        return 1, 0
    if target_kind == "material":
        return 0, 1
    row = (
        await db.execute(text(_AFFECTED_SQL), {"cid": target_id, "lv": list(REQUIRED_LEVELS)})
    ).one()
    return int(row[0] or 0), int(row[1] or 0)
