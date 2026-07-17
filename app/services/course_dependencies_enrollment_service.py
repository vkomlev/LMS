"""tsk-261 (A2): автоназначение курсов-зависимостей при назначении курса.

Зачем. Курс может требовать прохождения другого курса (`course_dependencies`).
Замок снимается ТОЛЬКО когда required-курс перешёл в `COMPLETED`
(`me_service._BLOCKED_COURSES_SQL`, `learning_engine_service.resolve_next_item`).
Но пройти курс, который ученику не назначен, физически нельзя — он не попадает
ни в `user_courses`, ни в учебный движок. Итог: назначили зависимый курс, не
назначив зависимость — и замок висит вечно, без выхода.

Живой случай приёмки QA 2026-07-16 («заблоченный курс так и висит»): курс
«Python для подростков (11-14)» зависит от «Вводный Python», который QA не был
назначен вовсе. На проде этот курс был назначен ровно одному ученику.

Решение оператора: при назначении курса зависимости доназначаются автоматически.

Границы:
- зависимости собираются ТРАНЗИТИВНО (A → B → C), с защитой от циклов: БД
  запрещает только самоссылку (`check_no_self_dependency`), взаимный цикл
  A → B → A ничем не запрещён;
- курсы, у которых есть родитель, пропускаются: триггер БД
  `trg_check_user_course_no_parents` запрещает привязывать ученика к некорневому
  курсу, и такой INSERT уронил бы всю транзакцию вызывающего;
- `access_level` намеренно не проверяется: это уровень сопровождения
  (self_guided / auto_check / …), а не признак платности, и ни один путь
  назначения его не читает.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Транзитивный обход зависимостей с защитой от циклов через накопленный путь.
# NOT ... = ANY(path) отсекает повторный вход в уже пройденный курс.
_REQUIRED_COURSES_SQL = """
WITH RECURSIVE deps AS (
    SELECT cd.required_course_id AS course_id,
           ARRAY[cd.course_id, cd.required_course_id] AS path
    FROM course_dependencies cd
    WHERE cd.course_id = ANY(:course_ids)
    UNION ALL
    SELECT cd.required_course_id,
           d.path || cd.required_course_id
    FROM deps d
    JOIN course_dependencies cd ON cd.course_id = d.course_id
    WHERE NOT (cd.required_course_id = ANY(d.path))
)
SELECT DISTINCT course_id FROM deps
"""

_REQUIRED_COURSES_STMT = text(_REQUIRED_COURSES_SQL)


async def collect_required_course_ids(
    db: AsyncSession, course_ids: list[int]
) -> list[int]:
    """Транзитивно собрать курсы, от которых зависят указанные курсы.

    Сами `course_ids` в результат не входят. Циклы в графе зависимостей
    безопасны — путь обхода не заходит в уже посещённый курс.

    Args:
        db: async-сессия.
        course_ids: курсы, для которых ищем зависимости.

    Returns:
        Список id курсов-зависимостей (без дублей, порядок не гарантирован).
    """
    if not course_ids:
        return []
    res = await db.execute(_REQUIRED_COURSES_STMT, {"course_ids": course_ids})
    required = {int(row[0]) for row in res.fetchall()}
    required.difference_update(course_ids)
    return sorted(required)


async def ensure_dependencies_assigned(
    db: AsyncSession, *, student_id: int, course_ids: list[int]
) -> list[int]:
    """Доназначить ученику курсы-зависимости указанных курсов.

    Идемпотентно: уже назначенные курсы пропускаются (`ON CONFLICT DO NOTHING`).
    Commit НЕ делает — остаётся в транзакции вызывающего, чтобы назначение курса
    и его зависимостей было атомарным.

    Курсы с родителями пропускаются: триггер БД запрещает привязывать ученика к
    некорневому курсу. Такая зависимость — ошибка данных курса, о ней пишем в лог,
    но назначение основного курса не роняем.

    Args:
        db: async-сессия.
        student_id: ID ученика.
        course_ids: только что назначенные курсы.

    Returns:
        Список id реально доназначенных курсов (пустой, если зависимостей нет).
    """
    required = await collect_required_course_ids(db, course_ids)
    if not required:
        return []

    # Триггер trg_check_user_course_no_parents уронит INSERT для некорневого курса.
    res = await db.execute(
        text(
            "SELECT c.id, EXISTS (SELECT 1 FROM course_parents cp WHERE cp.course_id = c.id) "
            "FROM courses c WHERE c.id = ANY(:ids)"
        ),
        {"ids": required},
    )
    assignable: list[int] = []
    for course_id, has_parents in res.fetchall():
        if has_parents:
            logger.warning(
                "tsk-261: курс-зависимость id=%s не назначен ученику id=%s — "
                "у него есть родитель, ученика можно привязать только к корневому "
                "курсу. Это ошибка данных курса: зависимость должна быть корневой.",
                course_id,
                student_id,
            )
            continue
        assignable.append(int(course_id))

    if not assignable:
        return []

    # order_number проставит триггер trg_set_user_course_order_number.
    res = await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id) "
            "SELECT :uid, cid FROM unnest(CAST(:ids AS int[])) AS cid "
            "ON CONFLICT (user_id, course_id) DO NOTHING "
            "RETURNING course_id"
        ),
        {"uid": student_id, "ids": assignable},
    )
    assigned = [int(row[0]) for row in res.fetchall()]
    if assigned:
        logger.info(
            "tsk-261: ученику id=%s доназначены курсы-зависимости %s (для курсов %s)",
            student_id,
            assigned,
            course_ids,
        )
    return assigned
