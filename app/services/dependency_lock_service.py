"""tsk-1276: замок зависимости на сервере, а не только в подсказке следующего шага.

Замок «курс A требует пройти курс B» (`course_dependencies`) раньше держал
только `resolve_next_item` (кнопка «Продолжить») и оглавление. Задание
закрытого курса открывалось по прямому адресу и из программы курса, ответ
принимался, а автоматика ДЗ клала задания закрытого курса в домашку. На проде
ученик 4569 при замке «88 требует 2106» сдал 12 заданий курса 88, а по 2106 — ни
одного (07.10).

Правило то же, что у движка (tsk-231 ф.6): корень ученика закрыт, если у него
есть зависимость на ДОСТУПНЫЙ ученику курс, который ещё не COMPLETED. Задание
закрыто, если ВСЕ активные корни ученика, в чьём дереве оно лежит, закрыты:
тема, общая для закрытого корня и курса повторения, через курс повторения
остаётся доступной.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

#: Машинный признак отказа — по нему клиент показывает «сначала пройдите курс».
BLOCKED_DEPENDENCY_CODE = "blocked_dependency"

# Дерево каждого активного корня ученика: (root, node). `path` — защита от цикла.
_TREES_SQL = """
WITH RECURSIVE tree AS (
    SELECT uc.course_id AS root, uc.course_id AS node, ARRAY[uc.course_id] AS path
      FROM user_courses uc
     WHERE uc.user_id = :sid AND uc.is_active
    UNION ALL
    SELECT t.root, cp.course_id, t.path || cp.course_id
      FROM tree t JOIN course_parents cp ON cp.parent_course_id = t.node
     WHERE NOT (cp.course_id = ANY(t.path))
)
SELECT DISTINCT root, node FROM tree
"""


@dataclass(frozen=True)
class Lock:
    """Что закрывает корень: курс, который надо пройти раньше."""

    required_course_id: int
    title: str
    course_uid: str | None


async def _trees(db: AsyncSession, student_id: int) -> dict[int, set[int]]:
    """Корень -> узлы его дерева (сам корень включён)."""
    rows = (await db.execute(text(_TREES_SQL), {"sid": student_id})).all()
    trees: dict[int, set[int]] = {}
    for root, node in rows:
        trees.setdefault(int(root), set()).add(int(node))
    return trees


def _cache(db: AsyncSession, student_id: int) -> dict:
    """Кеш замков ученика на время одной сессии (одного запроса).

    Пачка ответов и подбор ДЗ спрашивают одно и то же много раз: без кеша на
    каждый ответ уходили бы два рекурсивных обхода дерева и пересчёт состояния
    курса. Внутри запроса картина не меняется так, чтобы это было важно.
    """
    return db.info.setdefault("tsk1276_locks", {}).setdefault(student_id, {})


async def _cached_trees(db: AsyncSession, student_id: int) -> dict[int, set[int]]:
    """`_trees` с кешем на время запроса."""
    cache = _cache(db, student_id)
    if "trees" not in cache:
        cache["trees"] = await _trees(db, student_id)
    return cache["trees"]


async def locked_roots(db: AsyncSession, student_id: int) -> dict[int, Lock]:
    """Активные корни ученика, закрытые непройденной доступной зависимостью."""
    cache = _cache(db, student_id)
    if "locks" in cache:
        return cache["locks"]
    from app.services.learning_engine_service import LearningEngineService

    trees = await _cached_trees(db, student_id)
    result: dict[int, Lock] = {}
    if trees:
        reachable = set().union(*trees.values())
        deps = (
            await db.execute(
                text(
                    "SELECT cd.course_id, cd.required_course_id, c.title, c.course_uid "
                    "FROM course_dependencies cd JOIN courses c ON c.id = cd.required_course_id "
                    "WHERE cd.course_id = ANY(:roots) ORDER BY cd.course_id, cd.required_course_id"
                ),
                {"roots": list(trees)},
            )
        ).all()
        engine = LearningEngineService()
        states: dict[int, str] = {}
        for root, required, title, uid in deps:
            root, required = int(root), int(required)
            if root in result or required not in reachable:
                continue
            if required not in states:
                states[required] = (
                    await engine.compute_course_state(
                        db, student_id, required, update_state_table=False
                    )
                ).state
            if states[required] != "COMPLETED":
                result[root] = Lock(required_course_id=required, title=title, course_uid=uid)
    cache["locks"] = result
    return result


def first_open(locks: dict[int, Lock], course_id: int) -> int:
    """Пройти цепочку замков до первого незакрытого курса.

    «112 требует 88», «88 требует 2106»: для 112 работать надо в 2106, а не в 88 —
    88 сам закрыт. Цикл в зависимостях не уводит в бесконечность: путь помнится.
    """
    seen = {course_id}
    current = course_id
    while current in locks:
        nxt = locks[current].required_course_id
        if nxt in seen:
            break
        seen.add(nxt)
        current = nxt
    return current


async def lock_for_course(db: AsyncSession, student_id: int, course_id: int) -> Lock | None:
    """Замок на узел курса: None, если хоть один корень ученика с этим узлом открыт."""
    trees = await _cached_trees(db, student_id)
    roots = [r for r, nodes in trees.items() if course_id in nodes]
    if not roots:
        return None  # вне записей ученика — это забота проверки зачисления
    locks = await locked_roots(db, student_id)
    if any(r not in locks for r in roots):
        return None
    lock = locks[roots[0]]
    target = first_open(locks, roots[0])
    if target != lock.required_course_id:
        # Цепочка: показываем курс, где реально можно работать.
        row = (
            await db.execute(
                text("SELECT title, course_uid FROM courses WHERE id=:c"), {"c": target}
            )
        ).first()
        if row is not None:
            lock = Lock(required_course_id=target, title=row[0], course_uid=row[1])
    return lock


async def _is_staff(db: AsyncSession, user_id: int) -> bool:
    """Преподаватель, методист или админ — превью «как ученик» не закрываем."""
    res = await db.execute(
        text(
            "SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
            "WHERE ur.user_id = :u AND r.name IN ('admin','methodist','teacher') LIMIT 1"
        ),
        {"u": user_id},
    )
    return res.first() is not None


#: Уровни, которые движок считает обязательными (`compute_course_state`).
_REQUIRED_LEVELS = ("required", "skippable")


async def _required_for_student(
    db: AsyncSession, student_id: int, task_id: int, course_id: int
) -> bool:
    """Обязательно ли задание ДЛЯ ЭТОГО ученика.

    Правило оператора 08.10: замок закрывает только обязательное. Рекомендуемое
    задание (`requirement_level = recommended`) и задание, ставшее для ученика
    необязательным по tsk-692 (добавлено в курс после того, как он прошёл тему),
    не закрываются никогда.
    """
    from app.services.content_grace_service import compute_graced_items

    level = (
        await db.execute(text("SELECT requirement_level FROM tasks WHERE id=:t"), {"t": task_id})
    ).scalar()
    if level not in _REQUIRED_LEVELS:
        return False
    trees = await _cached_trees(db, student_id)
    for root, nodes in trees.items():
        if course_id in nodes:
            graced = await compute_graced_items(db, student_id, root)
            if task_id in graced.tasks:
                return False
    return True


async def assert_course_not_locked(
    db: AsyncSession, *, student_id: int, course_id: int | None, task_id: int | None = None
) -> None:
    """403 `blocked_dependency`, если задание курса закрыто замком ученика.

    Проверяется УЧЕНИК (владелец попытки или тот, кто открывает задание), а не
    транспорт: боты ходят сервисным ключом, и обход по ключу дал бы «в кабинете
    нельзя, в Telegram можно» (тот же довод, что tsk-701).
    """
    if course_id is None or await _is_staff(db, student_id):
        return
    lock = await lock_for_course(db, student_id, int(course_id))
    if lock is None:
        return
    if task_id is not None and not await _required_for_student(
        db, student_id, int(task_id), int(course_id)
    ):
        return
    logger.info(
        "tsk-1276: deny student_id=%s course_id=%s — сначала курс %s",
        student_id, course_id, lock.required_course_id,
    )
    raise DomainError(
        detail=f"Сначала пройдите курс «{lock.title}» — до этого задания этого курса закрыты.",
        status_code=status.HTTP_403_FORBIDDEN,
        payload={
            "code": BLOCKED_DEPENDENCY_CODE,
            "required_course_id": lock.required_course_id,
            "required_course_title": lock.title,
            "required_course_uid": lock.course_uid,
        },
    )


# ─── tsk-1322: действующие замки для преподавателя и администратора ─────────

# Кандидаты для общего списка: ученики, у которых закрывающий курс лежит в одном
# из их деревьев. Остальным замок не грозит (правило `locked_roots`: зависимость
# на недоступный ученику курс не закрывает) — их не обсчитываем вовсе.
_CANDIDATES_SQL = """
WITH RECURSIVE cand AS (
    SELECT DISTINCT user_id FROM user_courses
     WHERE is_active AND course_id IN (SELECT course_id FROM course_dependencies)
), tree AS (
    SELECT uc.user_id, uc.course_id AS node, ARRAY[uc.course_id] AS path
      FROM user_courses uc JOIN cand USING (user_id)
     WHERE uc.is_active
    UNION ALL
    SELECT t.user_id, cp.course_id, t.path || cp.course_id
      FROM tree t JOIN course_parents cp ON cp.parent_course_id = t.node
     WHERE NOT (cp.course_id = ANY(t.path))
)
SELECT DISTINCT uc.user_id
  FROM user_courses uc
  JOIN course_dependencies cd ON cd.course_id = uc.course_id
  JOIN tree t ON t.user_id = uc.user_id AND t.node = cd.required_course_id
 WHERE uc.is_active
"""


async def describe_locks(db: AsyncSession, student_id: int) -> list[dict]:
    """Действующие замки ученика: что закрыто, чем и с какой даты.

    Дата — когда замок мог начаться: позднейшая из двух записей ученика — на
    закрытый корень и на корень, через который ему доступен закрывающий курс.
    Своей даты у строки `course_dependencies` нет, поэтому зависимость,
    добавленная позже обеих записей, покажется старше, чем есть. Для вопроса
    «не висит ли замок месяцами» это приемлемо: ошибка только в сторону «дольше».

    Персонал не закрывается (`assert_course_not_locked`) — для него пусто.
    """
    if await _is_staff(db, student_id):
        return []
    locks = await locked_roots(db, student_id)
    if not locks:
        return []
    trees = await _cached_trees(db, student_id)
    rows = (
        await db.execute(
            text(
                "SELECT uc.course_id, uc.added_at, c.title FROM user_courses uc "
                "JOIN courses c ON c.id = uc.course_id "
                "WHERE uc.user_id = :sid AND uc.is_active"
            ),
            {"sid": student_id},
        )
    ).all()
    added = {int(r[0]): r[1] for r in rows}
    titles = {int(r[0]): r[2] for r in rows}
    now = datetime.now(timezone.utc)
    result: list[dict] = []
    for root, lock in sorted(locks.items()):
        via = [
            added[r] for r, nodes in trees.items()
            if lock.required_course_id in nodes and added.get(r) is not None
        ]
        dates = [d for d in (added.get(root), min(via) if via else None) if d is not None]
        since = max(dates) if dates else None
        result.append({
            "student_id": student_id,
            "course_id": root,
            "course_title": titles.get(root) or "",
            "required_course_id": lock.required_course_id,
            "required_course_title": lock.title,
            "since": since,
            "days": (now - since).days if since is not None else None,
        })
    return result


async def list_all_locks(db: AsyncSession) -> list[dict]:
    """Все действующие замки школы — для администратора.

    Все ученики считаются в одной сессии: кеш `_cache` ключуется по ученику и
    не смешивает их. На проде 12.10: 105 учеников с зависимыми корнями — около
    5 секунд, поэтому это отдельная страница админки, а не поле общего списка.
    """
    ids = [int(r[0]) for r in (await db.execute(text(_CANDIDATES_SQL))).all()]
    result: list[dict] = []
    for sid in sorted(ids):
        result.extend(await describe_locks(db, sid))
    if not result:
        return result
    names = dict(
        (
            await db.execute(
                text("SELECT id, full_name FROM users WHERE id = ANY(:ids)"),
                {"ids": sorted({r["student_id"] for r in result})},
            )
        ).all()
    )
    for r in result:
        r["student_name"] = names.get(r["student_id"]) or f"#{r['student_id']}"
    return result
