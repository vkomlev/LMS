"""tsk-032: удержание ученика между занятиями — недельная серия и вехи.

**Цель задачи (решение оператора 2026-08-08).** Возвращать ученика к занятиям
В ПРОМЕЖУТКАХ между уроками с преподавателем, круглый год. Не сезонная
кампания. Измеримое число цели уже считается — «активность между занятиями»
(`between_lessons`) из дашборда родителя (tsk-494/504).

**Почему недельная серия, а не дневной стрик 7 дней** (исходная летняя рамка).
Замер по проду 2026-08-08 (`reviews/2026-08-08-tsk032-retention-baseline.md`):
за 12 недель порога «7 дней подряд» не достиг НИ ОДИН ученик из 49, максимум
за всё время — 5 дней. При этом 19 учеников держали 2 активные недели подряд,
8 — три. Цель, которой не достигает никто, работает против удержания.
Соревновательные механики (лидерборд) отпали отдельно: у большинства курсов
когорта меньше 5 человек (tsk-504), соревнование втроём чаще демотивирует.

**Единое определение события — то же, что у метрики дашборда.** Событие
«между занятиями» здесь описано ТЕМ ЖЕ набором условий, что
`student_dashboard_service._bulk_between_lessons_activity`: верно сданное
задание (попытка не отменена) либо изученный материал, не из ручного
источника, чьё время НЕ попадает ни в одно окно занятия этого ученика. Это не
косметика: рост серии обязан отражаться на том же числе, по которому эффект
задачи будут доказывать (базовый уровень снят ДО внедрения).

**Серия — производная величина, она НЕ хранится.** Считается по событиям при
каждом чтении. Причина не в экономии: сохранённая производная строка обязана
пересчитываться в КАЖДОЙ точке изменения основания, иначе молча замирает со
старым значением (tsk-511, tsk-548). Путей записи результата у нас много
(SA, SA_COM, ручная проверка, импорт) — хранимая серия рассинхронизировалась бы
на первом же неучтённом. В `user_achievements` пишутся только ОДНОКРАТНЫЕ вехи,
у которых основание уже не может исчезнуть.

**Таймзона.** Границы дня и недели — всегда `Europe/Moscow`, явно в SQL. У
боевой сессии БД таймзона другая (UTC+5): опора на неё сдвинула бы границу
недели на сутки. Тот же принцип, что у `me_service.get_streak`.

**Недельная серия и «милость понедельника».** Активная неделя = хотя бы один
день с событием между занятиями. Текущая серия считается назад от ТЕКУЩЕЙ
недели, если она уже активна, иначе — от ПРОШЛОЙ. Без этой оговорки серия
всей школы обнулялась бы визуально каждый понедельник в 00:00, до того как у
ученика вообще была возможность что-то сделать.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.teacher_lesson_summary_service import MANUAL_SOURCE

logger = logging.getLogger("app.retention")

#: Типы условий достижения, которые умеет проверять `_is_earned`. Каталог —
#: данные (строки `achievements`), правила — код. Незнакомый тип не роняет
#: начисление (см. `_is_earned`), иначе новая строка в каталоге ломала бы cron.
CONDITION_WEEKLY_STREAK = "weekly_streak"
CONDITION_ITEMS_TOTAL = "between_lessons_items"

#: События между занятиями (дата в МСК + идентификатор элемента) для набора
#: учеников. Условия — дословно из `_bulk_between_lessons_activity`
#: (tsk-494/504), см. docstring модуля. `UNION` (не `UNION ALL`) даёт
#: дедупликацию повторных верных сдач одного задания в один день — метрика
#: дашборда тоже считает DISTINCT по элементу.
_EVENTS_SQL = """
SELECT tr.user_id AS student_id,
       (tr.submitted_at AT TIME ZONE 'Europe/Moscow')::date AS d,
       'task' AS kind,
       tr.task_id AS item_id
FROM task_results tr
JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
WHERE tr.user_id = ANY(:student_ids)
  AND tr.is_correct = true
  AND tr.source_system IS DISTINCT FROM :manual_source
  AND NOT EXISTS (
      SELECT 1 FROM lesson_occurrence_participant lop
      JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
      WHERE lop.student_id = tr.user_id
        AND tr.submitted_at BETWEEN lo.scheduled_at
            AND lo.scheduled_at + make_interval(mins => lo.duration_minutes)
  )
UNION
SELECT smp.student_id,
       (smp.completed_at AT TIME ZONE 'Europe/Moscow')::date,
       'material',
       smp.material_id
FROM student_material_progress smp
WHERE smp.student_id = ANY(:student_ids)
  AND smp.status = 'completed'
  AND smp.completed_at IS NOT NULL
  AND smp.source IS DISTINCT FROM :manual_source
  AND NOT EXISTS (
      SELECT 1 FROM lesson_occurrence_participant lop
      JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
      WHERE lop.student_id = smp.student_id
        AND smp.completed_at BETWEEN lo.scheduled_at
            AND lo.scheduled_at + make_interval(mins => lo.duration_minutes)
  )
"""


def _week_start(day: date) -> date:
    """Понедельник недели, которой принадлежит ``day`` (границы МСК —
    вызывающий передаёт уже московскую дату)."""
    return day - timedelta(days=day.weekday())


async def _today_msk(db: AsyncSession) -> date:
    """Сегодня в Europe/Moscow по часам БД (не по часам процесса — приложение
    и БД живут в разных таймзонах, см. docstring модуля)."""
    row = (
        await db.execute(text("SELECT (now() AT TIME ZONE 'Europe/Moscow')::date AS today"))
    ).mappings().first()
    return row["today"]


async def load_events(
    db: AsyncSession, *, student_ids: list[int],
) -> dict[int, list[tuple[date, str, int]]]:
    """События «между занятиями» по ученикам: ``{student_id: [(дата, вид, id)]}``.

    Ученик без единого события в словаре ПРИСУТСТВУЕТ с пустым списком —
    иначе вызывающему пришлось бы отличать «нет данных» от «нет активности»,
    а это разные вещи только на входе, не на выходе."""
    result: dict[int, list[tuple[date, str, int]]] = {sid: [] for sid in student_ids}
    if not student_ids:
        return result
    rows = (
        await db.execute(
            text(_EVENTS_SQL),
            {"student_ids": student_ids, "manual_source": MANUAL_SOURCE},
        )
    ).mappings().fetchall()
    for r in rows:
        result[int(r["student_id"])].append((r["d"], str(r["kind"]), int(r["item_id"])))
    return result


def compute_state(
    events: list[tuple[date, str, int]], *, today_msk: date,
) -> dict[str, Any]:
    """Состояние удержания одного ученика по его событиям.

    Возвращает:
    - ``weekly_streak`` — активных недель подряд (с «милостью понедельника»,
      см. docstring модуля);
    - ``best_weekly_streak`` — лучший прогон за всё время;
    - ``current_week_active`` / ``current_week_days`` / ``current_week_items``;
    - ``items_total`` — всего элементов, закрытых между занятиями (для вех);
    - ``last_active_date`` — последний день активности между занятиями.
    """
    if not events:
        return {
            "weekly_streak": 0,
            "best_weekly_streak": 0,
            "current_week_active": False,
            "current_week_days": 0,
            "current_week_items": 0,
            "items_total": 0,
            "last_active_date": None,
        }

    days = {d for d, _kind, _item in events}
    active_weeks = {_week_start(d) for d in days}
    this_week = _week_start(today_msk)
    prev_week = this_week - timedelta(days=7)

    # Точка отсчёта серии: текущая неделя, если она уже активна, иначе
    # прошлая. Если не активна и прошлая — серия прервана.
    if this_week in active_weeks:
        anchor: Optional[date] = this_week
    elif prev_week in active_weeks:
        anchor = prev_week
    else:
        anchor = None

    weekly_streak = 0
    if anchor is not None:
        cursor = anchor
        while cursor in active_weeks:
            weekly_streak += 1
            cursor -= timedelta(days=7)

    # Лучший прогон за всё время — по отсортированному списку активных недель.
    best = 0
    run = 0
    prev: Optional[date] = None
    for week in sorted(active_weeks):
        run = run + 1 if prev is not None and week - prev == timedelta(days=7) else 1
        best = max(best, run)
        prev = week

    current_week_days = sum(1 for d in days if _week_start(d) == this_week)
    # Элементы считаются УНИКАЛЬНЫМИ (вид + id), как и в метрике дашборда
    # (там DISTINCT по task_id/material_id). Иначе задание, верно сданное в
    # два разных дня, дало бы двойку — и объёмные вехи набирались бы
    # повторными сдачами одного и того же.
    current_week_items = len(
        {(kind, item) for d, kind, item in events if _week_start(d) == this_week}
    )
    items_total = len({(kind, item) for _d, kind, item in events})

    return {
        "weekly_streak": weekly_streak,
        "best_weekly_streak": best,
        "current_week_active": this_week in active_weeks,
        "current_week_days": current_week_days,
        "current_week_items": current_week_items,
        "items_total": items_total,
        "last_active_date": max(days),
    }


def _is_earned(condition: Any, state: dict[str, Any]) -> bool:
    """Выполнено ли условие достижения при данном состоянии.

    Незнакомый или битый ``condition`` — это НЕ «достижение получено»: такой
    строке каталога здесь отвечают `False` и пишут предупреждение в лог. Иначе
    опечатка в данных раздала бы веху всей школе."""
    if not isinstance(condition, dict):
        logger.warning("tsk-032: condition достижения не объект: %r", condition)
        return False
    ctype = condition.get("type")
    if ctype == CONDITION_WEEKLY_STREAK:
        target = condition.get("weeks")
        if not isinstance(target, int) or target <= 0:
            logger.warning("tsk-032: битый weeks в condition: %r", condition)
            return False
        # Веха берётся и по ТЕКУЩЕЙ, и по ЛУЧШЕЙ серии: однажды достигнутая
        # веха не отбирается назад при обрыве серии (у неё уже нет основания,
        # которое могло бы исчезнуть — см. docstring модуля).
        return max(state["weekly_streak"], state["best_weekly_streak"]) >= target
    if ctype == CONDITION_ITEMS_TOTAL:
        target = condition.get("count")
        if not isinstance(target, int) or target <= 0:
            logger.warning("tsk-032: битый count в condition: %r", condition)
            return False
        return state["items_total"] >= target
    logger.warning("tsk-032: неизвестный тип условия достижения: %r", ctype)
    return False


def _progress_of(condition: Any, state: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    """(текущее значение, целевое) для условия — для полосы прогресса в UI.
    ``(None, None)`` для условий, которые UI показать не умеет."""
    if not isinstance(condition, dict):
        return None, None
    if condition.get("type") == CONDITION_WEEKLY_STREAK:
        target = condition.get("weeks")
        if isinstance(target, int) and target > 0:
            return max(state["weekly_streak"], state["best_weekly_streak"]), target
    if condition.get("type") == CONDITION_ITEMS_TOTAL:
        target = condition.get("count")
        if isinstance(target, int) and target > 0:
            return state["items_total"], target
    return None, None


async def load_catalog(db: AsyncSession) -> list[dict[str, Any]]:
    """Каталог достижений (строки `achievements`), порядок — стабильный."""
    rows = (
        await db.execute(
            text(
                "SELECT id, name, description, condition, badge_image_url, reward_points "
                "FROM achievements ORDER BY id"
            )
        )
    ).mappings().fetchall()
    return [dict(r) for r in rows]


async def load_earned_at(
    db: AsyncSession, *, student_ids: list[int],
) -> dict[tuple[int, int], Any]:
    """``{(user_id, achievement_id): earned_at}`` — что уже зафиксировано."""
    if not student_ids:
        return {}
    rows = (
        await db.execute(
            text(
                "SELECT user_id, achievement_id, earned_at FROM user_achievements "
                "WHERE user_id = ANY(:student_ids)"
            ),
            {"student_ids": student_ids},
        )
    ).mappings().fetchall()
    return {(int(r["user_id"]), int(r["achievement_id"])): r["earned_at"] for r in rows}


async def get_retention(db: AsyncSession, *, student_id: int) -> dict[str, Any]:
    """Полное состояние удержания ученика для его кабинета.

    Чтение БЕЗ побочных эффектов: фиксацию вех в `user_achievements` делает
    фоновый тик (`retention_achievements_cron_service`), а не этот путь.
    Но список выполненных вех считается ЗДЕСЬ тем же правилом, что и в тике —
    поэтому ученик видит веху сразу, не дожидаясь тика; у только что
    выполненной вехи `earned_at` пока `None` (ещё не зафиксирована).
    """
    today = await _today_msk(db)
    events = (await load_events(db, student_ids=[student_id]))[student_id]
    state = compute_state(events, today_msk=today)
    catalog = await load_catalog(db)
    earned_at = await load_earned_at(db, student_ids=[student_id])

    achievements: list[dict[str, Any]] = []
    next_milestone: Optional[dict[str, Any]] = None
    best_gap: Optional[float] = None

    for item in catalog:
        current, target = _progress_of(item["condition"], state)
        if _is_earned(item["condition"], state):
            achievements.append({
                "id": int(item["id"]),
                "name": item["name"],
                "description": item["description"],
                "badge_image_url": item["badge_image_url"],
                "earned_at": earned_at.get((student_id, int(item["id"]))),
            })
            continue
        # Ближайшая невыполненная веха — с наименьшей ОТНОСИТЕЛЬНОЙ нехваткой,
        # чтобы «ещё 2 недели» не проигрывало «ещё 40 заданий» просто потому,
        # что 2 < 40.
        if current is None or target is None or target <= 0:
            continue
        gap = (target - current) / target
        if best_gap is None or gap < best_gap:
            best_gap = gap
            next_milestone = {
                "id": int(item["id"]),
                "name": item["name"],
                "description": item["description"],
                "current": current,
                "target": target,
            }

    return {
        "weekly_streak": state["weekly_streak"],
        "best_weekly_streak": state["best_weekly_streak"],
        "current_week_active": state["current_week_active"],
        "current_week_days": state["current_week_days"],
        "current_week_items": state["current_week_items"],
        "items_between_lessons_total": state["items_total"],
        "last_active_date": state["last_active_date"],
        "achievements": achievements,
        "next_milestone": next_milestone,
    }


async def get_retention_summary(db: AsyncSession, *, student_id: int) -> dict[str, Any]:
    """Урезанная сводка для дашборда родителя (tsk-504): серия и текущая
    неделя, без списка вех и без прогресса до следующей — родителю нужен
    сигнал «возвращается ли ребёнок между занятиями», а не витрина наград."""
    today = await _today_msk(db)
    events = (await load_events(db, student_ids=[student_id]))[student_id]
    state = compute_state(events, today_msk=today)
    earned = await load_earned_at(db, student_ids=[student_id])
    return {
        "weekly_streak": state["weekly_streak"],
        "best_weekly_streak": state["best_weekly_streak"],
        "current_week_active": state["current_week_active"],
        "achievements_earned": len(earned),
    }


async def award_pending(db: AsyncSession, *, student_ids: list[int]) -> int:
    """Зафиксировать выполненные, но ещё не записанные вехи. Возвращает число
    новых строк `user_achievements`.

    Идемпотентно: `ON CONFLICT DO NOTHING` по PK `(user_id, achievement_id)`.
    Коммит — на вызывающем (тик коммитит один раз за проход)."""
    if not student_ids:
        return 0
    catalog = await load_catalog(db)
    if not catalog:
        return 0
    today = await _today_msk(db)
    events_by_student = await load_events(db, student_ids=student_ids)
    already = await load_earned_at(db, student_ids=student_ids)

    inserted = 0
    for student_id in student_ids:
        state = compute_state(events_by_student.get(student_id, []), today_msk=today)
        for item in catalog:
            achievement_id = int(item["id"])
            if (student_id, achievement_id) in already:
                continue
            if not _is_earned(item["condition"], state):
                continue
            res = await db.execute(
                text(
                    "INSERT INTO user_achievements (user_id, achievement_id, progress) "
                    "VALUES (:user_id, :achievement_id, CAST(:progress AS jsonb)) "
                    "ON CONFLICT (user_id, achievement_id) DO NOTHING"
                ),
                {
                    "user_id": student_id,
                    "achievement_id": achievement_id,
                    "progress": _progress_json(item["condition"], state),
                },
            )
            # Считаем по факту вставки, а не по факту попытки: при гонке двух
            # процессов ON CONFLICT молча пропустит строку, и счётчик в логе
            # завышал бы число выданных вех.
            inserted += int(res.rowcount or 0)
    return inserted


def _progress_json(condition: Any, state: dict[str, Any]) -> str:
    """Снимок состояния на момент выдачи — чтобы потом было видно, ЧЕМ веха
    была закрыта, а не только что она закрыта."""
    current, target = _progress_of(condition, state)
    return json.dumps(
        {
            "source": "tsk-032",
            "weekly_streak": state["weekly_streak"],
            "best_weekly_streak": state["best_weekly_streak"],
            "items_total": state["items_total"],
            "condition_current": current,
            "condition_target": target,
        },
        ensure_ascii=False,
    )


async def list_active_student_ids(db: AsyncSession) -> list[int]:
    """Активные ученики — та же выборка, что у базового замера
    (`user_courses.is_active` И `users.is_active`)."""
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT uc.user_id FROM user_courses uc "
                "JOIN users u ON u.id = uc.user_id AND u.is_active = true "
                "WHERE uc.is_active = true"
            )
        )
    ).scalars().all()
    return [int(r) for r in rows]
