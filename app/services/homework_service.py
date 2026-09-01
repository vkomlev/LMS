"""Домашняя работа: выдача, состав, срок, выполнение (tsk-741, фаза 3).

Что здесь есть и чего намеренно нет:

- **Выдача** (`issue`) собирает состав из СЛЕДУЮЩИХ незавершённых элементов
  программы ученика — в том же учебном порядке, в каком их отдаёт дерево курса.
  Отсюда бесплатно выполняется требование «теорию учить дома»: материалы в
  дереве стоят перед заданиями своего узла, и в выдачу они попадают первыми.
  Своего порядка здесь не заводится — иначе домашняя работа однажды разошлась
  бы с тем, что показывает ученику кабинет.
- **Выполнение НЕ хранится.** Считается у источника: задание — есть верный
  результат, материал — есть отметка прохождения. Ученик работает обычным
  путём из кабинета, а не «внутри ДЗ»; своя колонка была бы вторым источником
  правды и разъехалась бы с фактом в первый же день.
- **Ручной зачёт преподавателя пункт ДЗ закрывает.** Здесь — намеренно, в
  отличие от расчёта темпа (`homework_volume_service`), где ручные зачёты
  отсечены. Это два разных вопроса: «сделано ли задание» решает преподаватель
  (зачёл — значит закрыто, иначе он видел бы красную отметку, которую сам же
  и снял), а «с какой скоростью работает человек» — вопрос про его
  собственные сдачи, и ручные зачёты там задрали бы норму до недостижимой.
- **Отменённые выдачи остаются.** `cancelled_at` вместо удаления: преподаватель
  должен видеть, что задавал и почему передумал, а счётчики за прошлые недели
  не должны меняться задним числом.

Одна действующая выдача на ученика: новая гасит предыдущую (`cancelled_at`).
Иначе «текущее ДЗ» перестаёт быть определённым — а именно этот вопрос задаёт и
ученик в кабинете, и преподаватель перед занятием.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import homework_volume_service

logger = logging.getLogger(__name__)

#: Статусы дерева, означающие «с элементом покончено» — брать в ДЗ нечего.
_DONE_STATUSES = ("PASSED", "COMPLETED", "SKIPPED")

#: Задание, упершееся в лимит попыток, в домашнюю работу не кладём: ученик
#: физически не сможет его сдать, а в сводке оно будет висеть невыполненным.
_UNASSIGNABLE_TASK_STATUSES = ("BLOCKED_LIMIT",)


async def _next_items(
    db: AsyncSession, *, student_id: int, limit: int
) -> list[dict[str, Any]]:
    """Следующие `limit` незавершённых элементов программы ученика.

    Идёт по корневым курсам в порядке `user_courses.order_number` и внутри
    каждого — по учебному порядку дерева (`manual_progress_service.
    get_student_progress`). Дорого (обход дерева), но выдача бывает раз в
    занятие на ученика; чтение готового ДЗ этот путь не трогает вовсе.
    """
    # Локальный импорт: `manual_progress_service` тянет движок и репозитории,
    # а этот модуль зовут из сводки преподавателя — цикла быть не должно.
    from app.services import manual_progress_service

    roots = (
        await db.execute(
            text(
                "SELECT uc.course_id FROM user_courses uc "
                " WHERE uc.user_id = :sid AND uc.is_active = true "
                " ORDER BY uc.order_number ASC NULLS LAST, uc.course_id"
            ),
            {"sid": student_id},
        )
    ).scalars().all()

    picked: list[dict[str, Any]] = []
    for course_id in roots:
        if len(picked) >= limit:
            break
        progress = await manual_progress_service.get_student_progress(
            db, student_id=student_id, course_id=int(course_id)
        )
        for item in progress.get("items", []):
            if len(picked) >= limit:
                break
            if item["item_type"] not in ("task", "material"):
                continue
            if item["status"] in _DONE_STATUSES:
                continue
            if (
                item["item_type"] == "task"
                and item["status"] in _UNASSIGNABLE_TASK_STATUSES
            ):
                continue
            picked.append(
                {
                    "kind": item["item_type"],
                    "item_id": int(item["item_id"]),
                    "title": item.get("title"),
                }
            )
    return picked


async def issue(
    db: AsyncSession,
    *,
    student_id: int,
    due_at: datetime,
    source: str,
    issued_by: Optional[int] = None,
    occurrence_id: Optional[int] = None,
    volume_override: Optional[int] = None,
    note: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Выдать домашнюю работу. Гасит предыдущую действующую выдачу.

    Args:
        db: async session (коммит — на вызывающем).
        student_id: кому.
        due_at: срок — обычно начало следующего занятия.
        source: `auto` (по формуле) или `teacher` (рука преподавателя).
        issued_by: кто выдал; None у автоматической выдачи.
        occurrence_id: занятие, после которого выдано.
        volume_override: сколько элементов задать вместо расчёта формулы —
            преподаватель вправе задать своё число, норма при этом всё равно
            считается и уходит в `volume_details` (иначе потом не понять, от
            чего он отступил и на сколько).
        note: комментарий преподавателя.
        now: момент выдачи (для тестов).

    Returns:
        Словарь выдачи — тот же, что отдаёт `get_current`.

    Raises:
        ValueError: срок в прошлом либо в программе не осталось элементов.
    """
    moment = now or datetime.now(timezone.utc)
    if due_at <= moment:
        raise ValueError("Срок домашней работы должен быть в будущем.")

    plan = await homework_volume_service.compute(db, student_id=student_id, now=moment)
    days = max((due_at - moment).days, 1)
    volume = (
        int(volume_override)
        if volume_override is not None
        else homework_volume_service.volume_for_window(plan, days=days)
    )
    if volume <= 0:
        raise ValueError(
            "Программа пройдена: ученик идёт с опережением, и задавать больше "
            "нечего. Добавьте ему курс — тогда домашняя работа появится снова."
        )

    items = await _next_items(db, student_id=student_id, limit=volume)
    if not items:
        raise ValueError(
            "Программа пройдена: ученик идёт с опережением, и задавать больше "
            "нечего. Добавьте ему курс — тогда домашняя работа появится снова."
        )

    await db.execute(
        text(
            "UPDATE homework_assignment SET cancelled_at = :now "
            " WHERE student_id = :sid AND cancelled_at IS NULL"
        ),
        {"sid": student_id, "now": moment},
    )

    details = plan.as_details()
    details["requested_volume"] = volume
    details["window_days"] = days
    if volume_override is not None:
        details["volume_override"] = int(volume_override)

    homework_id = (
        await db.execute(
            text(
                "INSERT INTO homework_assignment "
                "  (student_id, issued_at, due_at, source, issued_by, occurrence_id, "
                "   planned_volume, volume_details, note) "
                "VALUES (:sid, :now, :due, :source, :by, :occ, :vol, "
                "        CAST(:details AS jsonb), :note) "
                "RETURNING id"
            ),
            {
                "sid": student_id,
                "now": moment,
                "due": due_at,
                "source": source,
                "by": issued_by,
                "occ": occurrence_id,
                "vol": volume,
                "details": json.dumps(details, ensure_ascii=False),
                "note": note,
            },
        )
    ).scalar_one()

    for position, item in enumerate(items):
        await db.execute(
            text(
                "INSERT INTO homework_item (homework_id, kind, task_id, material_id, position) "
                "VALUES (:hid, :kind, :task_id, :material_id, :pos)"
            ),
            {
                "hid": homework_id,
                "kind": item["kind"],
                "task_id": item["item_id"] if item["kind"] == "task" else None,
                "material_id": item["item_id"] if item["kind"] == "material" else None,
                "pos": position,
            },
        )

    logger.info(
        "tsk-741: выдано ДЗ id=%s ученику %s — %s элементов, срок %s, источник %s",
        homework_id, student_id, len(items), due_at, source,
    )
    result = await get_current(db, student_id=student_id)
    assert result is not None  # только что вставили
    return result


#: Сколько дней даём на работу, если следующего занятия в расписании нет.
#: Неделя — шаг сетки школы: у большинства занятия раз в неделю, и «до
#: следующего» для них ровно столько.
_FALLBACK_DUE_DAYS = 7


async def next_due_for(
    db: AsyncSession,
    *,
    student_id: int,
    after: datetime,
    now: Optional[datetime] = None,
) -> datetime:
    """Срок домашней работы: начало СЛЕДУЮЩЕГО занятия ученика.

    Занятия в расписании нет — неделя от «сейчас». Общая функция для ручной
    выдачи и автоматической: два разных ответа на вопрос «до когда» означали
    бы, что преподаватель и система задают ДЗ на разные сроки.
    """
    moment = now or datetime.now(timezone.utc)
    next_at = (
        await db.execute(
            text(
                "SELECT lo.scheduled_at "
                "  FROM lesson_occurrence lo "
                "  JOIN lesson_occurrence_participant lop "
                "    ON lop.occurrence_id = lo.id AND lop.student_id = :sid "
                " WHERE lo.scheduled_at > :after AND lop.status <> 'rescheduled' "
                " ORDER BY lo.scheduled_at ASC LIMIT 1"
            ),
            {"sid": student_id, "after": after},
        )
    ).scalar()
    if next_at is not None and next_at > moment:
        return next_at
    return moment + timedelta(days=_FALLBACK_DUE_DAYS)


async def auto_issue_after_lesson(
    db: AsyncSession,
    *,
    student_id: int,
    occurrence_id: int,
    occurrence_at: datetime,
    now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """Задать домашнюю работу сразу после занятия — по темпу и классу.

    Зовётся, когда преподаватель отметил, что ученик БЫЛ на занятии. Молчит и
    возвращает `None`, если:

    - выключен переключатель `homework_auto_issue_enabled` (по умолчанию —
      выключен, включение без выката);
    - по этому занятию уже выдавали (преподаватель поправляет статус задним
      числом — а каждая новая выдача гасит прежнюю, и ученик потерял бы то,
      что уже начал делать);
    - задавать нечего (программа пройдена) или срок не собрался.

    Срок — начало СЛЕДУЮЩЕГО занятия ученика; нет такого в расписании —
    неделя. Ошибки не поднимает: отметка явки не должна падать из-за
    домашней работы.
    """
    from app.core import settings_store

    if not settings_store.get_bool("homework_auto_issue_enabled"):
        return None

    moment = now or datetime.now(timezone.utc)
    already = (
        await db.execute(
            text(
                "SELECT 1 FROM homework_assignment "
                " WHERE student_id = :sid AND occurrence_id = :oid LIMIT 1"
            ),
            {"sid": student_id, "oid": occurrence_id},
        )
    ).first()
    if already is not None:
        return None

    due_at = await next_due_for(
        db, student_id=student_id, after=occurrence_at, now=moment
    )

    try:
        return await issue(
            db,
            student_id=student_id,
            due_at=due_at,
            source="auto",
            occurrence_id=occurrence_id,
            now=moment,
        )
    except ValueError as exc:
        # Программа пройдена целиком — это не ошибка занятия.
        logger.info(
            "tsk-741: автовыдача ДЗ ученику %s пропущена: %s", student_id, exc
        )
        return None


#: Состав действующей выдачи с отметкой выполнения, посчитанной у источника.
#: Один запрос: экран ученика и сводка преподавателя зовут его часто.
_ITEMS_SQL = """
SELECT hi.id, hi.kind, hi.task_id, hi.material_id, hi.position,
       CASE hi.kind
            WHEN 'task' THEN EXISTS (
                SELECT 1 FROM task_results tr
                  JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
                 WHERE tr.user_id = :sid AND tr.task_id = hi.task_id
                   AND tr.is_correct = true
            )
            ELSE EXISTS (
                SELECT 1 FROM student_material_progress smp
                 WHERE smp.student_id = :sid AND smp.material_id = hi.material_id
                   AND smp.status IN ('completed', 'skipped')
            )
       END AS done,
       t.course_id AS task_course_id,
       m.title AS material_title,
       m.course_id AS material_course_id,
       -- Название задания живёт в jsonb `task_content`, отдельной колонки нет
       -- (`project_lms_task_title_lives_in_task_content`); стем и внешний код
       -- нужны `humanize_task_title` как запасные имена.
       t.task_content->>'title' AS task_title,
       t.task_content->>'stem'  AS task_stem,
       t.external_uid           AS task_external_uid
  FROM homework_item hi
  LEFT JOIN tasks t ON t.id = hi.task_id
  LEFT JOIN materials m ON m.id = hi.material_id
 WHERE hi.homework_id = :hid
 ORDER BY hi.position
"""


async def _load_items(
    db: AsyncSession, *, homework_id: int, student_id: int
) -> list[dict[str, Any]]:
    """Состав выдачи с отметками выполнения."""
    from app.utils.task_title import humanize_task_title

    rows = (
        await db.execute(text(_ITEMS_SQL), {"hid": homework_id, "sid": student_id})
    ).mappings().fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        if row["kind"] == "task":
            title = humanize_task_title(
                int(row["task_id"]),
                row["task_title"],
                row["task_stem"],
                row["task_external_uid"],
            )
            course_id = row["task_course_id"]
        else:
            title = row["material_title"]
            course_id = row["material_course_id"]
        items.append(
            {
                "kind": row["kind"],
                "item_id": int(row["task_id"] or row["material_id"]),
                "course_id": int(course_id) if course_id is not None else None,
                "title": title,
                "done": bool(row["done"]),
                "position": int(row["position"]),
            }
        )
    return items


async def get_current(
    db: AsyncSession, *, student_id: int, now: Optional[datetime] = None
) -> Optional[dict[str, Any]]:
    """Действующая домашняя работа ученика или None, если её нет.

    «Действующая» — неотменённая и самая свежая. Просроченную не прячем:
    невыполненное ДЗ с прошедшим сроком — ровно то, что должен увидеть и
    ученик, и преподаватель перед занятием.
    """
    moment = now or datetime.now(timezone.utc)
    row = (
        await db.execute(
            text(
                "SELECT id, student_id, issued_at, due_at, source, issued_by, "
                "       occurrence_id, planned_volume, volume_details, note "
                "  FROM homework_assignment "
                " WHERE student_id = :sid AND cancelled_at IS NULL "
                " ORDER BY issued_at DESC, id DESC LIMIT 1"
            ),
            {"sid": student_id},
        )
    ).mappings().fetchone()
    if row is None:
        return None

    items = await _load_items(db, homework_id=int(row["id"]), student_id=student_id)
    done = sum(1 for i in items if i["done"])
    return {
        "id": int(row["id"]),
        "student_id": int(row["student_id"]),
        "issued_at": row["issued_at"],
        "due_at": row["due_at"],
        "source": row["source"],
        "issued_by": row["issued_by"],
        "occurrence_id": row["occurrence_id"],
        "planned_volume": int(row["planned_volume"]),
        "volume_details": row["volume_details"],
        "note": row["note"],
        "items": items,
        "total": len(items),
        "done": done,
        "is_overdue": bool(row["due_at"] <= moment and done < len(items)),
    }


#: Свёртка «сколько задано / сколько сделано / просрочено» сразу на группу.
#: Отдельный запрос от `_ITEMS_SQL`: сводке преподавателя нужны числа по
#: каждому участнику, а не состав — тянуть состав на группу из 8 человек
#: значило бы вернуть сотни строк ради трёх чисел.
_SUMMARY_SQL = """
WITH current AS (
    SELECT DISTINCT ON (ha.student_id)
           ha.id, ha.student_id, ha.due_at, ha.planned_volume, ha.issued_at
      FROM homework_assignment ha
     WHERE ha.student_id = ANY(:student_ids) AND ha.cancelled_at IS NULL
     ORDER BY ha.student_id, ha.issued_at DESC, ha.id DESC
),
counted AS (
    SELECT c.student_id, c.id, c.due_at, c.issued_at,
           count(hi.id) AS total,
           count(*) FILTER (
               WHERE (hi.kind = 'task' AND EXISTS (
                        SELECT 1 FROM task_results tr
                          JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
                         WHERE tr.user_id = c.student_id AND tr.task_id = hi.task_id
                           AND tr.is_correct = true))
                  OR (hi.kind = 'material' AND EXISTS (
                        SELECT 1 FROM student_material_progress smp
                         WHERE smp.student_id = c.student_id
                           AND smp.material_id = hi.material_id
                           AND smp.status IN ('completed', 'skipped')))
           ) AS done
      FROM current c
      LEFT JOIN homework_item hi ON hi.homework_id = c.id
     GROUP BY c.student_id, c.id, c.due_at, c.issued_at
)
SELECT * FROM counted
"""


async def status_for_students(
    db: AsyncSession, *, student_ids: list[int], now: Optional[datetime] = None
) -> dict[int, dict[str, Any]]:
    """Состояние действующего ДЗ для каждого ученика группы.

    Ученик без выдачи в ответе отсутствует — это не то же самое, что «выдача
    пустая»: первое значит «ещё не задавали», второе невозможно (пустую выдачу
    `issue` не создаёт).
    """
    if not student_ids:
        return {}
    moment = now or datetime.now(timezone.utc)
    rows = (
        await db.execute(text(_SUMMARY_SQL), {"student_ids": student_ids})
    ).mappings().fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        total = int(row["total"] or 0)
        done = int(row["done"] or 0)
        result[int(row["student_id"])] = {
            "homework_id": int(row["id"]),
            "issued_at": row["issued_at"],
            "due_at": row["due_at"],
            "assigned_total": total,
            "assigned_done": done,
            "is_overdue": bool(row["due_at"] <= moment and done < total),
        }
    return result


#: Доля выполненного из выданного за период — на группу одним запросом.
#: Берутся выдачи, СОЗДАННЫЕ в периоде, включая отменённые: отменённая выдача
#: всё равно была работой, которую человеку давали, и вычёркивать её задним
#: числом значило бы менять прошлые показатели.
_RATIO_SQL = """
WITH scoped AS (
    SELECT ha.id, ha.student_id
      FROM homework_assignment ha
     WHERE ha.student_id = ANY(:student_ids)
       AND ha.issued_at >= :period_from AND ha.issued_at <= :period_to
)
SELECT s.student_id,
       count(hi.id) AS total,
       count(*) FILTER (
           WHERE (hi.kind = 'task' AND EXISTS (
                    SELECT 1 FROM task_results tr
                      JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
                     WHERE tr.user_id = s.student_id AND tr.task_id = hi.task_id
                       AND tr.is_correct = true))
              OR (hi.kind = 'material' AND EXISTS (
                    SELECT 1 FROM student_material_progress smp
                     WHERE smp.student_id = s.student_id
                       AND smp.material_id = hi.material_id
                       AND smp.status IN ('completed', 'skipped')))
       ) AS done
  FROM scoped s
  JOIN homework_item hi ON hi.homework_id = s.id
 GROUP BY s.student_id
"""


async def completion_ratio_for_students(
    db: AsyncSession,
    *,
    student_ids: list[int],
    period_from: datetime,
    period_to: datetime,
) -> dict[int, float]:
    """Доля выполненного из выданного за период, по каждому ученику (0..1).

    Ученик, которому за период ничего не выдавали, в ответе отсутствует — у
    него нет доли, и подставлять ему ноль нельзя: это превратило бы «ему не
    задавали» в «он не сделал» и утянуло бы его вниз в сравнении с группой.
    """
    if not student_ids:
        return {}
    rows = (
        await db.execute(
            text(_RATIO_SQL),
            {
                "student_ids": student_ids,
                "period_from": period_from,
                "period_to": period_to,
            },
        )
    ).mappings().fetchall()
    result: dict[int, float] = {}
    for row in rows:
        total = int(row["total"] or 0)
        if total == 0:
            continue
        result[int(row["student_id"])] = int(row["done"] or 0) / total
    return result


async def cancel(
    db: AsyncSession, *, homework_id: int, now: Optional[datetime] = None
) -> bool:
    """Отменить выдачу. Идемпотентно: уже отменённая остаётся как была.

    Returns:
        True — выдача была действующей и стала отменённой.
    """
    moment = now or datetime.now(timezone.utc)
    result = await db.execute(
        text(
            "UPDATE homework_assignment SET cancelled_at = :now "
            " WHERE id = :hid AND cancelled_at IS NULL"
        ),
        {"hid": homework_id, "now": moment},
    )
    return bool(result.rowcount)
