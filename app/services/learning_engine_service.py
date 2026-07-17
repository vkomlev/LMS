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
from app.schemas.task_content import QUIZ_TASK_TYPES
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
# Квиз-вопросы (SC_Qw/MC_Qw, tsk-124): ровно одна попытка — измеряют шкалы,
# у них нет «верно/неверно», повтор бессмысленен и задваивает scale_scores.
QUIZ_MAX_ATTEMPTS = 1
PASS_THRESHOLD_RATIO = 0.5

# tsk-264: дерево курса вниз по course_parents — содержит ли корень данный узел.
_ROOT_CONTAINS_NODE_SQL = """
WITH RECURSIVE subtree AS (
    SELECT CAST(:root_course_id AS INTEGER) AS course_id
    UNION ALL
    SELECT cp.course_id
    FROM subtree s
    JOIN course_parents cp ON cp.parent_course_id = s.course_id
)
SELECT EXISTS (SELECT 1 FROM subtree WHERE course_id = :course_id)
"""

# tsk-264: активные корневые курсы ученика, в чьё дерево входит данный узел.
_ACTIVE_ROOTS_OF_NODE_SQL = """
WITH RECURSIVE ct AS (
    SELECT uc.course_id AS root_course_id, uc.course_id AS member_course_id
    FROM user_courses uc
    WHERE uc.user_id = :student_id AND uc.is_active = true
    UNION ALL
    SELECT ct.root_course_id, cp.course_id
    FROM ct
    JOIN course_parents cp ON cp.parent_course_id = ct.member_course_id
)
SELECT DISTINCT root_course_id
FROM ct
WHERE member_course_id = :course_id
"""


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
        Лимит попыток по приоритету: квиз -> override -> task.max_attempts -> 3.

        Квиз-вопросы (SC_Qw/MC_Qw, tsk-124) всегда ограничены одной попыткой и
        перебивают override/max_attempts: повтор задвоил бы баллы по шкалам.

        Args:
            db: Сессия БД.
            student_id: ID студента.
            task_id: ID задания.

        Returns:
            Эффективный лимит попыток (>= 1).
        """
        # 0) Квиз-вопросы — всегда ровно одна попытка (выше override и max_attempts).
        r = await db.execute(
            text("SELECT task_content->>'type' FROM tasks WHERE id = :task_id"),
            {"task_id": task_id},
        )
        type_row = r.fetchone()
        if type_row is not None and type_row[0] in QUIZ_TASK_TYPES:
            return QUIZ_MAX_ATTEMPTS

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

    async def root_contains_course(
        self,
        db: AsyncSession,
        root_course_id: int,
        course_id: int,
    ) -> bool:
        """Входит ли курс `course_id` в дерево корня `root_course_id` (tsk-264).

        Args:
            db: async session.
            root_course_id: корневой курс.
            course_id: проверяемый узел (сам корень тоже входит в своё дерево).

        Returns:
            True, если узел лежит в дереве корня.
        """
        return bool(
            (
                await db.execute(text(_ROOT_CONTAINS_NODE_SQL), {
                    "root_course_id": root_course_id,
                    "course_id": course_id,
                })
            ).scalar()
        )

    async def resolve_attempt_root(
        self,
        db: AsyncSession,
        student_id: int,
        course_id: int,
        requested_root_course_id: Optional[int] = None,
    ) -> Optional[int]:
        """Корень дерева, которым ученик пришёл к узлу `course_id` (tsk-264).

        Args:
            db: async session.
            student_id: ID студента.
            course_id: курс узла (курс самого задания).
            requested_root_course_id: корень, заявленный клиентом (SPW знает его
                из URL/дерева). Принимается только если дерево этого корня
                действительно содержит узел.

        Returns:
            ID корневого курса либо None, если путь определить нечем (узел под
            несколькими активными деревьями и клиент корень не передал). None —
            «путь неизвестен»: такая попытка не расходует лимит ни в одном корне.

        Raises:
            DomainError: заявленный корень не содержит узел. Проверка не
                косметическая: `root_course_id` — ключ счёта попыток, и без неё
                клиент обходил бы лимит, присылая каждый раз новый корень.
        """
        if requested_root_course_id is not None:
            if not await self.root_contains_course(
                db, requested_root_course_id, course_id
            ):
                raise DomainError(
                    f"Курс {requested_root_course_id} не содержит узел {course_id}"
                )
            return requested_root_course_id

        # Корень не заявлен — восстанавливаем по активным деревьям ученика.
        # Однозначен ровно один кандидат; несколько (переиспользуемый узел) —
        # None, гадать нельзя: ошибка съела бы попытку не в том курсе.
        rows = (
            await db.execute(text(_ACTIVE_ROOTS_OF_NODE_SQL), {
                "student_id": student_id,
                "course_id": course_id,
            })
        ).fetchall()
        if len(rows) == 1:
            return int(rows[0][0])
        if len(rows) > 1:
            logger.info(
                "resolve_attempt_root: узел под несколькими корнями без контекста — "
                "student_id=%s course_id=%s roots=%s",
                student_id, course_id, [int(r[0]) for r in rows],
            )
        return None

    async def compute_task_state(
        self,
        db: AsyncSession,
        student_id: int,
        task_id: int,
        root_course_id: Optional[int] = None,
    ) -> TaskStateResult:
        """
        Состояние задания по последнему task_result.

        Архитектура: attempts — course-level (один открытый attempt на (user, course),
        накапливает task_results по многим задачам, см. start-or-get-attempt).
        Поэтому фильтруем НЕ по a.finished_at, а по a.cancelled_at: учитываем
        task_results как из активного, так и из завершённого course-level attempt.

        attempts_used = число поданных решений по задаче (= COUNT task_results).
        state:
          - OPEN если нет ни одного task_result;
          - PASSED если last_score/last_max_score >= 0.5 (по последнему submitted_at);
          - FAILED если последний task_result не PASSED, attempts_used < limit;
          - BLOCKED_LIMIT если attempts_used >= limit и нет PASSED.

        tsk-264: `root_course_id` — корень дерева, которым ученик пришёл к заданию.
        Узел графа переиспользуется несколькими корнями, и раньше исчерпанные в
        курсе X попытки убивали задание в курсе Y. Разделены ДВА эффекта:
          - ПРОГРЕСС (последний результат, PASSED) остаётся ОБЩИМ для всех корней:
            что ученик знает — то знает, перерешивать не нужно;
          - СЧЁТ ПОПЫТОК считается в границах корня: новый курс — свежие попытки.
        Попытки с root_course_id IS NULL (путь неизвестен: старые записи, где корень
        восстановить нечем, либо вызов без контекста) не расходуют лимит ни в одном
        корне. `root_course_id=None` у вызова — прежнее поведение: счёт по всем
        попыткам задания независимо от пути.
        """
        limit = await self.get_effective_attempt_limit(db, student_id, task_id)

        # tsk-264: у квиза (SC_Qw/MC_Qw) ответ ОДИН НАВСЕГДА — повтор задваивает
        # scale_scores, и submit отклоняет его глобально, без учёта курса
        # (attempts.py, QUIZ_TASK_TYPES → 409). Значит и счёт у квиза общий, как
        # прогресс: иначе в соседнем курсе показали бы «попытка есть», ученик
        # нажал бы «ответить» и получил отказ сервера.
        if root_course_id is not None:
            type_row = (
                await db.execute(
                    text("SELECT task_content->>'type' FROM tasks WHERE id = :task_id"),
                    {"task_id": task_id},
                )
            ).fetchone()
            if type_row is not None and type_row[0] in QUIZ_TASK_TYPES:
                root_course_id = None

        # Число поданных ответов по задаче (учитывая активный course-level attempt).
        # tsk-264: при заданном корне — только попытки этого корня (см. docstring).
        count_stmt = text("""
            SELECT COUNT(*)
            FROM task_results tr
            INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
            WHERE tr.user_id = :student_id AND tr.task_id = :task_id
              AND (
                    CAST(:root_course_id AS INTEGER) IS NULL
                    OR a.root_course_id = CAST(:root_course_id AS INTEGER)
              )
        """)
        r = await db.execute(
            count_stmt,
            {"student_id": student_id, "task_id": task_id, "root_course_id": root_course_id},
        )
        attempts_used = r.scalar() or 0

        # Последний task_result по задаче (по submitted_at task_results).
        # tsk-222: дополнительно тянем answer_json/is_correct/checked_at — тот же
        # ряд, что уже используется для last_score, без новых JOIN'ов. answer_json —
        # это ответ ученика (StudentAnswer), эталон в него не входит.
        last_stmt = text("""
            SELECT a.id, tr.submitted_at, tr.score, tr.max_score,
                   tr.answer_json, tr.is_correct, tr.checked_at
            FROM task_results tr
            INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
            WHERE tr.user_id = :student_id AND tr.task_id = :task_id
            ORDER BY tr.submitted_at DESC, tr.id DESC
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
        # answer_json (JSONB) драйвер отдаёт уже как dict; is_correct/checked_at — как есть.
        last_answer_json = row[4] if isinstance(row[4], dict) else None
        last_is_correct = row[5]
        last_checked_at = row[6]

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
                    last_answer_json=last_answer_json,
                    last_is_correct=last_is_correct,
                    last_checked_at=last_checked_at,
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
                last_answer_json=last_answer_json,
                last_is_correct=last_is_correct,
                last_checked_at=last_checked_at,
            )

        return TaskStateResult(
            state="FAILED",
            last_attempt_id=last_attempt_id,
            last_score=last_score,
            last_max_score=last_max_score,
            last_finished_at=last_finished_at,
            attempts_used=attempts_used,
            attempts_limit_effective=limit,
            last_answer_json=last_answer_json,
            last_is_correct=last_is_correct,
            last_checked_at=last_checked_at,
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

        # Число заданий в дереве курса (Y-6: TA снова учитываем —
        # SPW рендерит TaskFormTA, optimistic-PASSED продвигает state).
        tasks_count_stmt = select(func.count(Tasks.id)).where(
            Tasks.course_id.in_(tree_ids),
            Tasks.is_active.is_(True),
            Tasks.requirement_level.in_(("required", "skippable")),
        )
        r = await db.execute(tasks_count_stmt)
        total_tasks = r.scalar() or 0

        materials_count_stmt = select(func.count(Materials.id)).where(
            Materials.course_id.in_(tree_ids),
            Materials.is_active.is_(True),
            Materials.requirement_level.in_(("required", "skippable")),
        )
        r = await db.execute(materials_count_stmt)
        total_materials = r.scalar() or 0

        # Число заданий в дереве, по которым последний task_result — PASS.
        # Парность compute_task_state: учитываем все task_results из не-cancelled attempts
        # (включая активный course-level attempt), порядок — по submitted_at task_result.
        tasks_with_last_pass_stmt = text("""
            WITH last_per_task AS (
                SELECT DISTINCT ON (tr.task_id)
                    tr.task_id, tr.score AS last_score, tr.max_score AS last_max
                FROM task_results tr
                INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
                INNER JOIN tasks t
                    ON t.id = tr.task_id
                   AND t.course_id = ANY(:course_ids)
                   AND t.is_active = true
                   AND t.requirement_level IN ('required', 'skippable')
                WHERE tr.user_id = :student_id
                ORDER BY tr.task_id, tr.submitted_at DESC, tr.id DESC
            )
            SELECT COUNT(*) FROM (
                SELECT task_id FROM last_per_task
                WHERE last_max > 0 AND (last_score::float / last_max) >= :pass_threshold
                UNION
                SELECT stp.task_id
                FROM student_task_progress stp
                INNER JOIN tasks t
                    ON t.id = stp.task_id
                   AND t.course_id = ANY(:course_ids)
                   AND t.is_active = true
                   AND t.requirement_level IN ('required', 'skippable')
                WHERE stp.student_id = :student_id
                  AND stp.status = 'skipped'
            ) done_tasks
        """)
        r = await db.execute(
            tasks_with_last_pass_stmt,
            {"student_id": student_id, "course_ids": tree_ids, "pass_threshold": PASS_THRESHOLD_RATIO},
        )
        tasks_with_last_pass = r.scalar() or 0

        materials_done_stmt = text("""
            SELECT COUNT(*)
            FROM student_material_progress smp
            INNER JOIN materials m
                ON m.id = smp.material_id
               AND m.course_id = ANY(:course_ids)
               AND m.is_active = true
               AND m.requirement_level IN ('required', 'skippable')
            WHERE smp.student_id = :student_id
              AND smp.status IN ('completed', 'skipped')
        """)
        r = await db.execute(
            materials_done_stmt,
            {"student_id": student_id, "course_ids": tree_ids},
        )
        materials_done = r.scalar() or 0

        total_items = total_tasks + total_materials
        done_items = tasks_with_last_pass + materials_done

        if total_items == 0:
            state: CourseStateType = "COMPLETED"
        elif done_items == 0:
            state = "NOT_STARTED"
        elif done_items >= total_items:
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

        # Y-6 Stage 4.3: course-completion event-driven escalation.
        # Если курс достиг COMPLETED, но есть pending TA/SA_COM (`checked_at IS NULL`)
        # → notify методиста (idempotent через `task_results.metrics.completion_escalated_at`).
        if state == "COMPLETED":
            try:
                pending_res = await db.execute(
                    text(
                        """
                        SELECT tr.id FROM task_results tr
                        JOIN tasks t ON t.id = tr.task_id
                        WHERE tr.user_id = :sid
                          AND t.course_id = ANY(:cids)
                          AND tr.checked_at IS NULL
                          AND t.task_content->>'type' IN ('SA_COM','TA')
                        """
                    ),
                    {"sid": student_id, "cids": tree_ids},
                )
                pending_ids = [int(r[0]) for r in pending_res.fetchall()]
                if pending_ids:
                    from app.core.config import Settings as _SettingsCls
                    from app.services import methodist_notify_service as _mn
                    _settings = _SettingsCls()
                    await _mn.escalate_course_completion(
                        db,
                        student_id=int(student_id),
                        course_id=int(course_id),
                        pending_result_ids=pending_ids,
                        rate_limit_per_day=int(
                            _settings.methodist_rate_limit_per_day_per_course
                        ),
                    )
            except Exception:
                # Эскалация не должна валить compute_course_state. Если что-то
                # пошло не так — просто залогируем. Студент видит свой COMPLETED,
                # cron-tick подберёт по timeout позже.
                import logging as _logging
                _logging.getLogger(__name__).exception(
                    "Y-6 course_completion escalation failed (student=%s course=%s)",
                    student_id, course_id,
                )

        return CourseState(state=state, course_id=course_id)

    async def _locate_item_course(
        self,
        db: AsyncSession,
        *,
        after_material_id: Optional[int],
        after_task_id: Optional[int],
    ) -> Optional[Tuple[int, str, int, Optional[int]]]:
        """Курс и порядковый ключ элемента текущей позиции.

        Фильтры `is_active`/`requirement_level` здесь НЕ применяются намеренно:
        ученик может стоять на `recommended`-элементе (на проде таких 994 задачи
        и 44 материала), которого нет в списке обхода. Позиция всё равно должна
        работать — обход режется по `order_position`, а не по вхождению в список.

        Returns:
            (course_id, kind, item_id, order_position) либо None, если позиция не
            задана / элемент не найден.
        """
        if after_material_id is not None:
            r = await db.execute(
                select(Materials.course_id, Materials.order_position).where(
                    Materials.id == after_material_id
                )
            )
            row = r.fetchone()
            if row is not None:
                return (int(row[0]), "material", after_material_id, row[1])
        if after_task_id is not None:
            r = await db.execute(
                select(Tasks.course_id, Tasks.order_position).where(Tasks.id == after_task_id)
            )
            row = r.fetchone()
            if row is not None:
                return (int(row[0]), "task", after_task_id, row[1])
        return None

    async def resolve_next_item(
        self,
        db: AsyncSession,
        student_id: int,
        root_course_id: Optional[int] = None,
        after_material_id: Optional[int] = None,
        after_task_id: Optional[int] = None,
    ) -> NextItemResult:
        """
        Следующий шаг для студента: material | task | none | blocked_dependency | blocked_limit.

        Правила: активные user_courses (is_active=true) по order_number;
        проверка зависимостей (required курс должен быть COMPLETED);
        обход дерева курса: материалы (order_position), затем задания (id);
        приоритет material над task; блокировка по лимиту попыток.

        tsk-261 (A4/A5). Раньше метод не знал, ГДЕ находится ученик, и всегда
        отдавал ПЕРВЫЙ незавершённый элемент по всему дереву. Поэтому, отметив
        материал в середине курса, ученик улетал назад — к любому пропуску раньше
        по обходу (жалоба QA: «редирект не на следующий блок, а на предыдущее
        невыполненное задание»), а собственные задания узла-контейнера
        откладывались до конца и выглядели пропущенными («Задание 1 пропускается»).
        tsk-127 менял порядок обхода (pre-order → post-order), но класс дефекта был
        не в порядке, а в том, что «следующий» означало «первый недоделанный».

        Теперь при заданной позиции обход идёт ВПЕРЁД от неё; дошли до конца курса
        и впереди ничего нет → `type="none"`, и SPW возвращает ученика в список
        разделов. Пропуски позади ученик добирает сам из списка — это осознанный
        размен (решение оператора), иначе автопереход снова тащил бы назад.

        Args:
            db: Сессия БД.
            student_id: ID студента.
            root_course_id: если задан — обход ограничен деревом этого корня
                (active фильтруется по uc.course_id); если None — прежнее
                поведение (обход всех активных курсов по order_number,
                обратная совместимость, tsk-127).
            after_material_id: текущая позиция — материал; искать строго ПОСЛЕ него.
            after_task_id: текущая позиция — задание; искать строго ПОСЛЕ него.
                Если позиция не задана или её элемент не найден в дереве — прежнее
                поведение (первый незавершённый с начала обхода).

        Returns:
            NextItemResult с листовым course_id и корневым root_course_id
            дерева, в котором найден элемент.
        """
        # Активные курсы пользователя по порядку
        user_courses = await self._user_courses_repo.get_user_courses(db, student_id, order_by_order=True)
        active = [uc for uc in user_courses if uc.is_active]
        # tsk-127: ограничить обход деревом одного корня, если задан фильтр.
        if root_course_id is not None:
            active = [uc for uc in active if uc.course_id == root_course_id]
        if not active:
            logger.info(
                "resolve_next_item: student_id=%s нет активных курсов (root_course_id=%s)",
                student_id, root_course_id,
            )
            return NextItemResult(type="none", reason="Нет активных курсов в плане")

        # tsk-261: позиция не зависит от корня — резолвим один раз до цикла.
        located = await self._locate_item_course(
            db, after_material_id=after_material_id, after_task_id=after_task_id
        )

        for uc in active:
            current_root_id = uc.course_id

            # Зависимости: все required должны быть COMPLETED
            deps = await self._deps_repo.list_dependencies(db, current_root_id)
            for req_course in deps:
                course_state = await self.compute_course_state(
                    db, student_id, req_course.id, update_state_table=True
                )
                if course_state.state != "COMPLETED":
                    logger.info(
                        "resolve_next_item: student_id=%s root=%s blocked_dependency required=%s",
                        student_id, current_root_id, req_course.id,
                    )
                    return NextItemResult(
                        type="blocked_dependency",
                        course_id=current_root_id,
                        root_course_id=current_root_id,
                        reason="Требуется завершить курс",
                        dependency_course_id=req_course.id,
                    )

            # Обход дерева: root + дети по order_number
            flat_courses = await self._collect_courses_in_order(db, current_root_id)

            # tsk-261: начать обход с курса текущей позиции, а не с начала дерева.
            # Позиция резолвится одним запросом (material/task → course_id), поэтому
            # курсы ДО неё не опрашиваются вовсе — ленивость обхода сохраняется.
            # flat_courses дедуплицирован, поэтому index() однозначен.
            start_index = 0
            position = located
            if position is not None and position[0] in flat_courses:
                start_index = flat_courses.index(position[0])
            else:
                # Позиция в другом дереве (или элемент удалён) — прежнее поведение:
                # первый незавершённый с начала этого корня.
                position = None

            for offset, cid in enumerate(flat_courses[start_index:]):
                material_ids: Optional[List[int]] = None
                task_ids: Optional[List[int]] = None

                # Сужаем списки только в курсе самой позиции; дальше по обходу —
                # курсы целиком. Режем по порядковому ключу элемента, а НЕ по его
                # индексу в списке: позиция может быть на `recommended`-элементе,
                # которого в списке обхода нет вовсе — тогда index() не нашёл бы
                # его и молча вернул к началу курса, то есть назад.
                if position is not None and offset == 0:
                    _, kind, item_id, item_order = position
                    pos_key = self._order_key(item_order, item_id)
                    if kind == "material":
                        material_ids = [
                            i
                            for i, op in await self._ordered_material_rows(db, cid)
                            if self._order_key(op, i) > pos_key
                        ]
                    else:
                        # Задание идёт после всех материалов своего курса — значит
                        # материалы этого курса уже позади позиции.
                        material_ids = []
                        task_ids = [
                            i
                            for i, op in await self._ordered_task_rows(db, cid)
                            if self._order_key(op, i) > pos_key
                        ]

                # Первый незавершённый материал
                mat = await self._first_incomplete_material(
                    db, student_id, cid, material_ids=material_ids
                )
                if mat is not None:
                    logger.info("resolve_next_item: student_id=%s next=material course_id=%s material_id=%s", student_id, cid, mat)
                    return NextItemResult(type="material", course_id=cid, root_course_id=current_root_id, material_id=mat, reason="Следующий материал")
                # Первое задание не PASSED и не BLOCKED_LIMIT.
                # tsk-264: лимит считаем в границах корня, которым идёт обход —
                # иначе исчерпанные в другом курсе попытки блокировали бы
                # переиспользуемый узел и здесь.
                task_id, blocked = await self._first_incomplete_task(
                    db, student_id, cid, task_ids=task_ids,
                    root_course_id=current_root_id,
                )
                if blocked is not None:
                    return NextItemResult(
                        type="blocked_limit",
                        course_id=cid,
                        root_course_id=current_root_id,
                        task_id=blocked,
                        reason="Исчерпан лимит попыток",
                    )
                if task_id is not None:
                    logger.info("resolve_next_item: student_id=%s next=task course_id=%s task_id=%s", student_id, cid, task_id)
                    return NextItemResult(type="task", course_id=cid, root_course_id=current_root_id, task_id=task_id, reason="Следующее задание")

        return NextItemResult(type="none", reason="Все элементы пройдены или заблокированы")

    async def _collect_courses_in_order(self, db: AsyncSession, root_id: int) -> List[int]:
        """
        Курсы для обхода: потомки (рекурсивно, по course_parents.order_number),
        затем сам курс — POST-ORDER.

        tsk-127 (первопричина, 2026-07-08): раньше обход был PRE-ORDER (сначала
        сам курс, потом дети). Из-за этого материалы, привязанные НАПРЯМУЮ к
        корневому/родительскому курсу, выдавались раньше, чем контент его
        подкурсов — и студент, идущий по дереву глав, «выкидывался» на материал
        с корня (например дубль-импорт `authored:*` на корне 825). Правильная
        модель (решение оператора): у каждого подкурса своя очередность —
        сперва спускаемся в подкурсы и берём материалы оттуда, а материалы
        самого курса-контейнера отдаём в ПОСЛЕДНЮЮ очередь.

        Порядок между детьми — course_parents.order_number ASC NULLS LAST, id.
        Используется resolve_next_item (порядок важен) и compute_course_state
        (там дерево берётся как множество — порядок безразличен).

        tsk-261: результат ДЕДУПЛИЦИРОВАН (остаётся первое вхождение).
        `course_parents` — many-to-many, узел может висеть под несколькими
        родителями одного дерева и попадал в список несколько раз (на проде:
        839/843/1020/1054 — по 2 раза в дереве ОГЭ, 1247 — 5 раз в дереве 871).
        Дубли ломали позиционный обход: `flat_courses.index(курс_позиции)` брал
        ПЕРВОЕ вхождение, и ученика со второго вхождения отбрасывало назад —
        ровно тот дефект, который позиция и чинит. Заодно снимается многократный
        опрос материалов/заданий одного и того же узла.
        """
        result: List[int] = []
        seen: set[int] = set()

        async def walk(course_id: int) -> None:
            if course_id in seen:
                return
            seen.add(course_id)
            children = await self._courses_repo.get_children(db, course_id)
            # order_number ASC NULLS LAST, затем id
            for _c, _ord in sorted(children, key=lambda x: (0 if x[1] is not None else 1, x[1] or 0, x[0].id)):
                await walk(_c.id)
            # Материалы/задания самого курса — после всех его подкурсов (post-order).
            result.append(course_id)

        await walk(root_id)
        return result

    @staticmethod
    def _order_key(order_position: Optional[int], item_id: int) -> Tuple[int, int, int]:
        """Ключ сортировки элемента: паритет с SQL `order_position ASC NULLS LAST, id ASC`."""
        return (0 if order_position is not None else 1, order_position or 0, item_id)

    async def _ordered_material_rows(
        self, db: AsyncSession, course_id: int
    ) -> List[Tuple[int, Optional[int]]]:
        """(id, order_position) материалов курса в порядке обхода."""
        materials_stmt = (
            select(Materials.id, Materials.order_position)
            .where(
                Materials.course_id == course_id,
                Materials.is_active.is_(True),
                Materials.requirement_level.in_(("required", "skippable")),
            )
            .order_by(Materials.order_position.asc().nulls_last(), Materials.id.asc())
        )
        r = await db.execute(materials_stmt)
        return [(row[0], row[1]) for row in r.fetchall()]

    async def _ordered_task_rows(
        self, db: AsyncSession, course_id: int
    ) -> List[Tuple[int, Optional[int]]]:
        """(id, order_position) заданий курса в порядке обхода."""
        tasks_stmt = (
            select(Tasks.id, Tasks.order_position)
            .where(
                Tasks.course_id == course_id,
                Tasks.is_active.is_(True),
                Tasks.requirement_level.in_(("required", "skippable")),
            )
            .order_by(Tasks.order_position.asc().nulls_last(), Tasks.id.asc())
        )
        r = await db.execute(tasks_stmt)
        return [(row[0], row[1]) for row in r.fetchall()]

    async def _ordered_material_ids(self, db: AsyncSession, course_id: int) -> List[int]:
        """ID материалов курса в порядке обхода (order_position ASC NULLS LAST, id)."""
        return [i for i, _ in await self._ordered_material_rows(db, course_id)]

    async def _ordered_task_ids(self, db: AsyncSession, course_id: int) -> List[int]:
        """ID заданий курса в порядке обхода (order_position ASC NULLS LAST, id)."""
        return [i for i, _ in await self._ordered_task_rows(db, course_id)]

    async def _first_incomplete_material(
        self,
        db: AsyncSession,
        student_id: int,
        course_id: int,
        material_ids: Optional[List[int]] = None,
    ) -> Optional[int]:
        """ID первого материала курса, не отмеченного как completed для студента.

        `material_ids` — необязательный заранее суженный список (tsk-261: обход от
        текущей позиции передаёт сюда только материалы ПОСЛЕ неё).
        """
        if material_ids is None:
            material_ids = await self._ordered_material_ids(db, course_id)

        if not material_ids:
            return None

        completed_stmt = text("""
            SELECT material_id FROM student_material_progress
            WHERE student_id = :student_id
              AND material_id = ANY(:ids)
              AND status IN ('completed', 'skipped')
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
        task_ids: Optional[List[int]] = None,
        root_course_id: Optional[int] = None,
    ) -> Tuple[Optional[int], Optional[int]]:
        """
        (task_id для следующего задания, task_id с blocked_limit или None).
        Если есть задание с BLOCKED_LIMIT — возвращаем (None, that_task_id).

        `root_course_id` (tsk-264) — корень обхода: лимит попыток считается в его
        границах.

        `task_ids` — необязательный заранее суженный список (tsk-261: обход от
        текущей позиции передаёт сюда только задания ПОСЛЕ неё).

        Y-6: TA снова в routing — SPW рендерит TaskFormTA, на submit
        задача получает optimistic-PASSED, learning engine продолжает
        курс. Stop-gap фильтр `type != 'TA'` (commit cf1908c, 2026-05-02)
        снят — иначе course не достигнет COMPLETED для курсов с TA.
        """
        if task_ids is None:
            task_ids = await self._ordered_task_ids(db, course_id)
        if task_ids:
            skipped_rows = await db.execute(
                text("""
                    SELECT task_id
                    FROM student_task_progress
                    WHERE student_id = :student_id
                      AND task_id = ANY(:task_ids)
                      AND status = 'skipped'
                """),
                {"student_id": student_id, "task_ids": task_ids},
            )
            skipped_ids = {int(row[0]) for row in skipped_rows.fetchall()}
        else:
            skipped_ids = set()

        for tid in task_ids:
            if tid in skipped_ids:
                continue
            state_result = await self.compute_task_state(
                db, student_id, tid, root_course_id=root_course_id
            )
            if state_result.state == "BLOCKED_LIMIT":
                return (None, tid)
            if state_result.state in ("OPEN", "IN_PROGRESS", "FAILED"):
                return (tid, None)
        return (None, None)
