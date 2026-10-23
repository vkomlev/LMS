"""tsk-1255: публикация черновика урока — узлы, материалы, задания.

Внутри те же сервисы, что у пакетного импорта (bulk-upsert материалов и
заданий), поэтому повторная публикация идемпотентна по external_uid: правка
в предпросмотре и повторное «Опубликовать» обновляют урок, а не плодят копию.
Лимит попыток пре-теста ставится здесь штатно — ручной скрипт писал его
прямо в БД, потому что в схеме пакетного импорта поля нет.

Записи идут несколькими транзакциями (сервисы коммитят внутри). Чтобы
недопубликованный урок был редкостью, сначала проверяется всё присланное,
и только потом пишется первая строка; сбой посередине лечится повтором.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courses import Courses
from app.models.difficulty_levels import DifficultyLevels
from app.models.tasks import Tasks
from app.schemas.lesson_import import LessonImportDraft, LessonImportPublishResult
from app.services.courses_service import CoursesService
from app.services.materials_service import MaterialsService
from app.services.tasks_service import TasksService
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

courses_service = CoursesService()
materials_service = MaterialsService()
tasks_service = TasksService()


def lesson_uid(parent: Courses, lesson_num: int) -> str:
    """Ключ урока от курса-родителя: тот же вид, что у ручного импорта (`HIM-DIPLOMAT-U02`)."""
    prefix = parent.course_uid or f"C{parent.id}"
    return f"{prefix}-U{lesson_num:02d}"


async def _difficulties(db: AsyncSession, uids: set[str]) -> dict[str, int]:
    """uid сложности → id; неизвестная сложность — 422 до записи."""
    rows = (await db.execute(
        select(DifficultyLevels.uid, DifficultyLevels.id).where(DifficultyLevels.uid.in_(uids))
    )).all()
    found = {uid: id_ for uid, id_ in rows}
    missing = sorted(uids - set(found))
    if missing:
        raise DomainError(detail=f"В справочнике сложностей нет: {', '.join(missing)}", status_code=422)
    return found


async def _validate(db: AsyncSession, draft: LessonImportDraft, diff: dict[str, int]) -> None:
    """Проверить связность черновика и каждое задание теми же правилами, что импорт."""
    nodes = {n.suffix for n in draft.nodes}
    errors: list[str] = []
    for item in [*draft.materials, *draft.tasks]:
        if item.node not in nodes:
            errors.append(f"{item.uid}: узла {item.node} нет в черновике")
    for kind, items in (("материал", draft.materials), ("задание", draft.tasks)):
        uids = [i.uid for i in items]
        dups = sorted({u for u in uids if uids.count(u) > 1})
        if dups:
            errors.append(f"повторяется {kind}: {', '.join(dups)}")
    for t in draft.tasks:
        ok, errs = await tasks_service.validate_task_import(
            db, task_content=t.task_content, solution_rules=t.solution_rules,
            difficulty_code=None, difficulty_id=diff[t.difficulty_uid],
            course_code=None, external_uid=t.uid,
        )
        # Код курса проверка требует для импорта из таблиц; узел урока здесь
        # ещё не создан и задаётся сервером, поэтому эту строку не считаем.
        errs = [e for e in errs if not e.startswith("course_code")]
        if not ok and errs:
            errors += [f"{t.uid}: {e}" for e in errs]
    if errors:
        raise DomainError(detail="Черновик не прошёл проверку: " + "; ".join(errors[:20]),
                          status_code=422)


async def _upsert_course(
    db: AsyncSession, uid: str, data: dict[str, Any], parent_id: int, order_number: int | None,
) -> int:
    """Узел по course_uid: нет — создать под родителем, есть — обновить поля, связи не трогать."""
    existing = await courses_service.repo.get_by_keys(db, {"course_uid": uid})
    if existing is not None:
        await courses_service.update(db, existing, dict(data))
        return existing.id
    link: dict[str, Any] = {"parent_course_id": parent_id}
    if order_number is not None:
        link["order_number"] = order_number
    created = await courses_service.create(db, {**data, "course_uid": uid, "parent_courses": [link]})
    return created.id


async def publish_draft(
    db: AsyncSession, parent_course_id: int, draft: LessonImportDraft,
    lesson_order: int | None = None,
) -> LessonImportPublishResult:
    """Опубликовать урок под курсом `parent_course_id`; повтор обновляет тот же урок."""
    parent = await db.get(Courses, parent_course_id)
    if parent is None:
        raise DomainError(detail="Курс-родитель не найден", status_code=404)
    if not draft.nodes:
        raise DomainError(detail="В черновике нет ни одного узла — публиковать нечего", status_code=422)
    diff = await _difficulties(db, {t.difficulty_uid for t in draft.tasks}) if draft.tasks else {}
    await _validate(db, draft, diff)

    base = lesson_uid(parent, draft.lesson_num)
    lesson_id = await _upsert_course(
        db, base, {"title": draft.lesson_title, "access_level": "manual_check"},
        parent.id, lesson_order,
    )
    node_ids: dict[str, int] = {}
    for n in sorted(draft.nodes, key=lambda x: x.order_number):
        node_ids[n.suffix] = await _upsert_course(
            db, f"{base}-{n.suffix}",
            {"title": n.title, "description": n.description, "access_level": n.access_level},
            lesson_id, n.order_number,
        )

    mats = await _publish_materials(db, base, draft, node_ids)
    created, updated = await _publish_tasks(db, base, draft, node_ids, diff)
    logger.info("lesson_import publish parent=%s lesson=%s nodes=%s materials=%s tasks=%s/%s",
                parent.id, base, len(node_ids), mats, created, updated)
    return LessonImportPublishResult(
        lesson_course_id=lesson_id, lesson_course_uid=base, node_ids=node_ids,
        materials_created=mats[0], materials_updated=mats[1],
        tasks_created=created, tasks_updated=updated, warnings=draft.warnings,
    )


async def _publish_materials(
    db: AsyncSession, base: str, draft: LessonImportDraft, node_ids: dict[str, int],
) -> tuple[int, int]:
    """Материалы через пакетный upsert; ошибка строки — 422 с текстом сервиса."""
    if not draft.materials:
        return 0, 0
    items = [
        {"course_id": node_ids[m.node], "external_uid": f"{base}-{m.uid}", "title": m.title,
         "type": m.type, "content": m.content,
         **({"order_position": m.order_position} if m.order_position else {})}
        for m in draft.materials
    ]
    res = await materials_service.bulk_upsert(db, items)
    bad = [f"{i.external_uid}: {i.error}" for i in res.items if i.error]
    if bad or res.errors:
        raise DomainError(detail="Материалы не записаны: " + "; ".join([*bad, *res.errors]),
                          status_code=422)
    return res.created, res.updated + res.unchanged


async def _publish_tasks(
    db: AsyncSession, base: str, draft: LessonImportDraft, node_ids: dict[str, int],
    diff: dict[str, int],
) -> tuple[int, int]:
    """Задания пакетным upsert, порядок как в черновике, лимит попыток штатно."""
    if not draft.tasks:
        return 0, 0
    items = []
    for t in draft.tasks:
        item: dict[str, Any] = {
            "external_uid": f"{base}-{t.uid}", "course_id": node_ids[t.node],
            "difficulty_id": diff[t.difficulty_uid], "task_content": t.task_content,
            "solution_rules": t.solution_rules, "max_score": t.max_score, "is_active": True,
        }
        if t.requirement_level:
            item["requirement_level"] = t.requirement_level
        items.append(item)
    results = await tasks_service.bulk_upsert(db, items=items, warnings=[])
    id_by_uid = {uid: tid for uid, _, tid in results}
    by_node: dict[int, list[int]] = {}
    for t in draft.tasks:
        by_node.setdefault(node_ids[t.node], []).append(id_by_uid[f"{base}-{t.uid}"])
    for course_id, ids in by_node.items():
        await tasks_service.reorder_tasks(
            db, course_id, [{"task_id": tid, "order_position": i + 1} for i, tid in enumerate(ids)]
        )
    for t in (x for x in draft.tasks if x.max_attempts is not None):
        await db.execute(
            update(Tasks).where(Tasks.id == id_by_uid[f"{base}-{t.uid}"])
            .values(max_attempts=t.max_attempts)
        )
    await db.commit()
    created = sum(1 for _, action, _ in results if action == "created")
    return created, len(results) - created
