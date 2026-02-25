"""
Learning Engine V1, этап 2: сервисный слой.

Маршрутизация (next item), расчёт effective limit попыток,
вычисление состояния задания по последней завершённой попытке.
Без публичных REST-эндпоинтов (этап 3).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attempts import Attempts
from app.models.materials import Materials
from app.models.tasks import Tasks
from app.models.user_courses import UserCourses
from app.models.association_tables import t_course_dependencies, t_course_parents
from app.schemas.learning_engine import (
    NextItemResult,
    NextItemType,
    TaskStateResult,
    TaskStateType,
    CourseState,
    CourseStateType,
)
from app.repos.user_courses_repo import UserCoursesRepository
from app.repos.courses_repo import CoursesRepository
from app.repos.course_dependencies_repository import CourseDependenciesRepository
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
PASS_THRESHOLD_RATIO = 0.5


class LearningEngineService:
    """
    Сервис маршрутизации и состояний Learning Engine V1.
    """

    def __init__(self) -> None:
        self._user_courses_repo = UserCoursesRepository()
        self._courses_repo = CoursesRepository()
        self._deps_repo = CourseDependenciesRepository()

    async def get_effective_attempt_limit(
        self,
        db: AsyncSession,
        student_id: int,
        task_id: int,
    ) -> int:
        """
        Лимит попыток по приоритету: override -> task.max_attempts -> 3.

        Args:
            db: Сессия БД.
            student_id: ID студента.
            task_id: ID задания.

        Returns:
            Эффективный лимит попыток (>= 1).
        """
        # 1) Override
        r = await db.execute(
            text("""
                SELECT max_attempts_override FROM student_task_limit_override
                WHERE student_id = :student_id AND task_id = :task_id
            """),
            {"student_id": student_id, "task_id": task_id},
        )
        row = r.fetchone()
        if row is not None:
            return int(row[0])

        # 2) tasks.max_attempts
        r = await db.execute(
            select(Tasks.max_attempts).where(Tasks.id == task_id)
        )
        row = r.fetchone()
        if row is not None and row[0] is not None:
            return int(row[0])

        return DEFAULT_MAX_ATTEMPTS

    async def compute_task_state(
        self,
        db: AsyncSession,
        student_id: int,
        task_id: int,
    ) -> TaskStateResult:
        """
        Состояние задания по последней завершённой попытке.

        attempts_used = число завершённых попыток по задаче (с записью в task_results).
        state: OPEN/IN_PROGRESS если нет завершённой попытки;
        PASSED если last_score/last_max_score >= 0.5;
        FAILED если последняя завершённая не PASSED;
        BLOCKED_LIMIT если attempts_used >= limit и нет PASSED.
        """
        limit = await self.get_effective_attempt_limit(db, student_id, task_id)

        # Количество завершённых попыток по этой задаче (есть task_result по task_id)
        count_stmt = text("""
            SELECT COUNT(DISTINCT a.id)
            FROM attempts a
            INNER JOIN task_results tr ON tr.attempt_id = a.id AND tr.task_id = :task_id
            WHERE a.user_id = :student_id AND a.finished_at IS NOT NULL
        """)
        r = await db.execute(count_stmt, {"student_id": student_id, "task_id": task_id})
        attempts_used = r.scalar() or 0

        # Последняя завершённая попытка по задаче (по finished_at)
        last_stmt = text("""
            SELECT a.id, a.finished_at, tr.score, tr.max_score
            FROM attempts a
            INNER JOIN task_results tr ON tr.attempt_id = a.id AND tr.task_id = :task_id
            WHERE a.user_id = :student_id AND a.finished_at IS NOT NULL
            ORDER BY a.finished_at DESC
            LIMIT 1
        """)
        r = await db.execute(last_stmt, {"student_id": student_id, "task_id": task_id})
        row = r.fetchone()

        if row is None:
            return TaskStateResult(
                state="OPEN" if attempts_used == 0 else "IN_PROGRESS",
                last_attempt_id=None,
                last_score=None,
                last_max_score=None,
                last_finished_at=None,
                attempts_used=attempts_used,
                attempts_limit_effective=limit,
            )

        last_attempt_id, last_finished_at, last_score, last_max_score = (
            int(row[0]), row[1], int(row[2]) if row[2] is not None else 0,
            int(row[3]) if row[3] is not None else 0,
        )

        if last_max_score and last_max_score > 0:
            ratio = last_score / last_max_score
            if ratio >= PASS_THRESHOLD_RATIO:
                return TaskStateResult(
                    state="PASSED",
                    last_attempt_id=last_attempt_id,
                    last_score=last_score,
                    last_max_score=last_max_score,
                    last_finished_at=last_finished_at,
                    attempts_used=attempts_used,
                    attempts_limit_effective=limit,
                )

        if attempts_used >= limit:
            return TaskStateResult(
                state="BLOCKED_LIMIT",
                last_attempt_id=last_attempt_id,
                last_score=last_score,
                last_max_score=last_max_score,
                last_finished_at=last_finished_at,
                attempts_used=attempts_used,
                attempts_limit_effective=limit,
            )

        return TaskStateResult(
            state="FAILED",
            last_attempt_id=last_attempt_id,
            last_score=last_score,
            last_max_score=last_max_score,
            last_finished_at=last_finished_at,
            attempts_used=attempts_used,
            attempts_limit_effective=limit,
        )

    async def compute_course_state(
        self,
        db: AsyncSession,
        student_id: int,
        course_id: int,
        *,
        update_state_table: bool = True,
    ) -> CourseState:
        """
        Состояние студента по курсу: NOT_STARTED | IN_PROGRESS | COMPLETED.

        Учитывается дерево курса (course_id + все потомки): total_tasks и
        tasks_with_result считаются по всем заданиям в дереве. Так dependency-gate
        в resolve_next_item даёт корректный COMPLETED только при завершении всего курса.

        При update_state_table=True выполняет upsert в student_course_state.
        """
        tree_ids = await self._collect_courses_in_order(db, course_id)
        if not tree_ids:
            tree_ids = [course_id]

        # Число заданий в дереве курса
        tasks_count_stmt = select(func.count(Tasks.id)).where(Tasks.course_id.in_(tree_ids))
        r = await db.execute(tasks_count_stmt)
        total_tasks = r.scalar() or 0

        # Число заданий в дереве, по которым есть результат в завершённой попытке
        with_result_stmt = text("""
            SELECT COUNT(DISTINCT tr.task_id)
            FROM task_results tr
            INNER JOIN attempts a ON a.id = tr.attempt_id AND a.user_id = :student_id AND a.finished_at IS NOT NULL
            INNER JOIN tasks t ON t.id = tr.task_id AND t.course_id = ANY(:course_ids)
        """)
        r = await db.execute(with_result_stmt, {"student_id": student_id, "course_ids": tree_ids})
        tasks_with_result = r.scalar() or 0

        if total_tasks == 0:
            state: CourseStateType = "NOT_STARTED"
        elif tasks_with_result == 0:
            state = "NOT_STARTED"
        elif tasks_with_result >= total_tasks:
            state = "COMPLETED"
        else:
            state = "IN_PROGRESS"

        if update_state_table:
            await db.execute(
                text("""
                    INSERT INTO student_course_state (student_id, course_id, state, updated_at)
                    VALUES (:student_id, :course_id, :state, now())
                    ON CONFLICT (student_id, course_id)
                    DO UPDATE SET state = EXCLUDED.state, updated_at = now()
                """),
                {"student_id": student_id, "course_id": course_id, "state": state},
            )

        return CourseState(state=state, course_id=course_id)

    async def resolve_next_item(
        self,
        db: AsyncSession,
        student_id: int,
    ) -> NextItemResult:
        """
        Следующий шаг для студента: material | task | none | blocked_dependency | blocked_limit.

        Правила: активные user_courses (is_active=true) по order_number;
        проверка зависимостей (required курс должен быть COMPLETED);
        обход дерева курса: материалы (order_position), затем задания (id);
        приоритет material над task; блокировка по лимиту попыток.
        """
        # Активные курсы пользователя по порядку
        user_courses = await self._user_courses_repo.get_user_courses(db, student_id, order_by_order=True)
        active = [uc for uc in user_courses if uc.is_active]
        if not active:
            logger.info("resolve_next_item: student_id=%s нет активных курсов", student_id)
            return NextItemResult(type="none", reason="Нет активных курсов в плане")

        for uc in active:
            root_course_id = uc.course_id

            # Зависимости: все required должны быть COMPLETED
            deps = await self._deps_repo.list_dependencies(db, root_course_id)
            for req_course in deps:
                course_state = await self.compute_course_state(
                    db, student_id, req_course.id, update_state_table=True
                )
                if course_state.state != "COMPLETED":
                    logger.info(
                        "resolve_next_item: student_id=%s root=%s blocked_dependency required=%s",
                        student_id, root_course_id, req_course.id,
                    )
                    return NextItemResult(
                        type="blocked_dependency",
                        course_id=root_course_id,
                        reason="Требуется завершить курс",
                        dependency_course_id=req_course.id,
                    )

            # Обход дерева: root + дети по order_number
            flat_courses = await self._collect_courses_in_order(db, root_course_id)
            for cid in flat_courses:
                # Первый незавершённый материал
                mat = await self._first_incomplete_material(db, student_id, cid)
                if mat is not None:
                    logger.info("resolve_next_item: student_id=%s next=material course_id=%s material_id=%s", student_id, cid, mat)
                    return NextItemResult(type="material", course_id=cid, material_id=mat, reason="Следующий материал")
                # Первое задание не PASSED и не BLOCKED_LIMIT
                task_id, blocked = await self._first_incomplete_task(db, student_id, cid)
                if blocked is not None:
                    return NextItemResult(
                        type="blocked_limit",
                        course_id=cid,
                        task_id=blocked,
                        reason="Исчерпан лимит попыток",
                    )
                if task_id is not None:
                    logger.info("resolve_next_item: student_id=%s next=task course_id=%s task_id=%s", student_id, cid, task_id)
                    return NextItemResult(type="task", course_id=cid, task_id=task_id, reason="Следующее задание")

        return NextItemResult(type="none", reason="Все элементы пройдены или заблокированы")

    async def _collect_courses_in_order(self, db: AsyncSession, root_id: int) -> List[int]:
        """Курсы для обхода: root и потомки в порядке course_parents.order_number (рекурсивно)."""
        result: List[int] = []

        async def walk(course_id: int) -> None:
            result.append(course_id)
            children = await self._courses_repo.get_children(db, course_id)
            # order_number ASC NULLS LAST, затем id
            for _c, _ord in sorted(children, key=lambda x: (0 if x[1] is not None else 1, x[1] or 0, x[0].id)):
                await walk(_c.id)

        await walk(root_id)
        return result

    async def _first_incomplete_material(self, db: AsyncSession, student_id: int, course_id: int) -> Optional[int]:
        """ID первого материала курса, не отмеченного как completed для студента."""
        materials_stmt = (
            select(Materials.id)
            .where(Materials.course_id == course_id, Materials.is_active.is_(True))
            .order_by(Materials.order_position.asc().nulls_last(), Materials.id.asc())
        )
        r = await db.execute(materials_stmt)
        material_ids = [row[0] for row in r.fetchall()]

        if not material_ids:
            return None

        completed_stmt = text("""
            SELECT material_id FROM student_material_progress
            WHERE student_id = :student_id AND material_id = ANY(:ids) AND status = 'completed'
        """)
        r = await db.execute(completed_stmt, {"student_id": student_id, "ids": material_ids})
        completed_ids = {row[0] for row in r.fetchall()}

        for mid in material_ids:
            if mid not in completed_ids:
                return mid
        return None

    async def _first_incomplete_task(
        self,
        db: AsyncSession,
        student_id: int,
        course_id: int,
    ) -> Tuple[Optional[int], Optional[int]]:
        """
        (task_id для следующего задания, task_id с blocked_limit или None).
        Если есть задание с BLOCKED_LIMIT — возвращаем (None, that_task_id).
        """
        tasks_stmt = select(Tasks.id).where(Tasks.course_id == course_id).order_by(Tasks.id.asc())
        r = await db.execute(tasks_stmt)
        task_ids = [row[0] for row in r.fetchall()]

        for tid in task_ids:
            state_result = await self.compute_task_state(db, student_id, tid)
            if state_result.state == "BLOCKED_LIMIT":
                return (None, tid)
            if state_result.state in ("OPEN", "IN_PROGRESS", "FAILED"):
                return (tid, None)
        return (None, None)
