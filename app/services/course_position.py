"""Где ученик в курсе: процент, фронт и хвосты (tsk-918).

Одно место для расчёта, который раньше жил в двух копиях — сводке занятия
(`teacher_lesson_summary_service`) и дашборде (`student_dashboard_service`).

Расхождение, из-за которого модуль появился (оператор, 12.09, карточка
Нуженко): «Последнее: задание (Задание 5)» и тут же «Сейчас: Задание 3 —
Доля товара-лидера». Обе строки правдивы. «Сейчас» брало ПЕРВЫЙ незакрытый
элемент в порядке курса, а ученик одно задание в Задании 3 пропустил (0
попыток, задание с файлом Excel) и ушёл на два задания вперёд. Преподаватель
читал это как противоречие.

Поэтому «сейчас» — ФРОНТ: первый незакрытый элемент после ЯКОРЯ — элемента
этого курса, который ученик закрыл ПОСЛЕДНИМ ПО ВРЕМЕНИ. Всё незакрытое
позади якоря — хвосты, отдельным числом и первым названием: преподаватель
видит и где человек, и что он перепрыгнул. Движок в кабинете ученика
по-прежнему зовёт его к первому незакрытому — хвост так и остаётся его
следующим шагом, и сводка об этом говорит, а не прячет.

Якорь именно по ВРЕМЕНИ, а не «последний закрытый по порядку курса»: замер
12.09 по 177 парам «ученик × курс» показал, что одно задание, сделанное
далеко впереди (разобрали на уроке), уносит порядковый фронт в конец курса —
у Литовкина «позади» выходило 330 элементов, у Нуженко 287.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.learning_gaps_service import (
    real_student_material_filter,
    real_student_results_filter,
)

#: Статусы, при которых элемент закрыт. Те же, что у сводки и дашборда.
DONE_STATUSES = ("PASSED", "COMPLETED", "SKIPPED")


@dataclass(frozen=True)
class CoursePosition:
    percent_complete: int
    current_section_title: Optional[str]
    current_item_title: Optional[str]
    #: Незакрытых элементов ПОЗАДИ фронта — перепрыгнутых.
    behind_count: int
    behind_section_title: Optional[str]
    behind_item_title: Optional[str]


async def anchor_for(
    db: AsyncSession, *, student_id: int, items: Iterable[dict[str, Any]]
) -> Optional[tuple[str, int]]:
    """(`task`|`material`, id) — элемент из `items`, закрытый учеником последним
    по времени; None — в курсе он ничего сам не закрывал.

    Только настоящая работа: ручной зачёт преподавателя якорем быть не может —
    иначе «сейчас» уезжало бы туда, где преподаватель проставил зачёты пачкой.
    """
    task_ids = [int(i["item_id"]) for i in items if i["item_type"] == "task"]
    material_ids = [int(i["item_id"]) for i in items if i["item_type"] == "material"]
    row = (
        await db.execute(
            text(
                f"""
                SELECT kind, id FROM (
                    SELECT 'task' AS kind, tr.task_id AS id, max(tr.submitted_at) AS at
                      FROM task_results tr
                     WHERE tr.user_id = :sid AND tr.is_correct = true
                       AND tr.task_id = ANY(CAST(:task_ids AS int[]))
                       AND {real_student_results_filter('tr')}
                     GROUP BY tr.task_id
                    UNION ALL
                    SELECT 'material', smp.material_id, smp.completed_at
                      FROM student_material_progress smp
                     WHERE smp.student_id = :sid AND smp.status = 'completed'
                       AND smp.completed_at IS NOT NULL
                       AND smp.material_id = ANY(CAST(:material_ids AS int[]))
                       AND {real_student_material_filter('smp')}
                ) x
                ORDER BY at DESC NULLS LAST
                LIMIT 1
                """
            ),
            {"sid": student_id, "task_ids": task_ids, "material_ids": material_ids},
        )
    ).first()
    return (str(row[0]), int(row[1])) if row else None


def position(
    items: Iterable[dict[str, Any]],
    *,
    course_id: int,
    anchor: Optional[tuple[str, int]] = None,
) -> CoursePosition:
    """Положение ученика по списку элементов курса в учебном порядке.

    :param items: строки `manual_progress_service.get_student_progress`
        (`item_type`, `item_id`, `title`, `status`, `parent_course_id`) —
        уже без прощённых (правило tsk-692 применяет вызывающий).
    :param course_id: корень курса; раздел показывается только когда элемент
        лежит не прямо в корне.
    :param anchor: элемент, закрытый последним по времени (`anchor_for`);
        None — фронт с начала курса, хвостов нет.
    """
    items = list(items)
    section_titles = {
        i["item_id"]: i["title"] for i in items if i["item_type"] == "course"
    }
    countable = [i for i in items if i["item_type"] != "course"]
    done_flags = [i["status"] in DONE_STATUSES for i in countable]
    done = sum(done_flags)
    total = len(countable)
    percent = round(done / total * 100) if total else 0

    anchor_idx = -1
    if anchor is not None:
        kind, item_id = anchor
        for idx, i in enumerate(countable):
            if i["item_type"] == kind and int(i["item_id"]) == item_id:
                anchor_idx = idx
                break
    undone = [idx for idx, ok in enumerate(done_flags) if not ok]
    ahead = [idx for idx in undone if idx > anchor_idx]
    behind = [idx for idx in undone if idx < anchor_idx]

    # Впереди пусто — следующий шаг и есть первый из хвостов.
    if ahead:
        current_idx: Optional[int] = ahead[0]
    elif behind:
        current_idx = behind.pop(0)
    else:
        current_idx = None

    def _section(idx: int) -> Optional[str]:
        parent_id = countable[idx].get("parent_course_id")
        if parent_id is not None and parent_id != course_id:
            return section_titles.get(parent_id)
        return None

    return CoursePosition(
        percent_complete=percent,
        current_section_title=_section(current_idx) if current_idx is not None else None,
        current_item_title=countable[current_idx]["title"] if current_idx is not None else None,
        behind_count=len(behind),
        behind_section_title=_section(behind[0]) if behind else None,
        behind_item_title=countable[behind[0]]["title"] if behind else None,
    )
