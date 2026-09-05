"""
Learning Engine V1, этап 2: сервисный слой.

Маршрутизация (next item), расчёт effective limit попыток,
вычисление состояния задания по последней завершённой попытке.
Без публичных REST-эндпоинтов (этап 3).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, List, Optional, Sequence, Tuple

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
from app.repos.courses_repo import CoursesRepository, course_tree_cache
from app.repos.course_dependencies_repository import CourseDependenciesRepository
from app.schemas.task_content import QUIZ_TASK_TYPES
from app.schemas.course_sampling import CourseSamplingConfig
from app.services.attempt_attachments import (
    existing_attachment_ids,
    mark_missing_attachments,
    mark_missing_one,
)
from app.services.task_sampling import sample_task_ids
# tsk-798: персональный объём программы. Порог выборки зависит от срока и темпа
# КОНКРЕТНОГО ученика, и знать это может только его план — общая настройка
# подкурса не различает ноябрьского новичка и того, кто идёт с сентября.
from app.services.program_scope_service import thresholds_for as program_scope_thresholds
# tsk-692: содержимое, добавленное в курс после того, как ученик прошёл тему,
# приходит ему рекомендуемым, а не долгом. Правило живёт отдельным модулем и
# зовётся функцией, а не повторяется условием в каждой точке (иначе копии
# разъезжаются — tsk-598).
from app.services.content_grace_service import EMPTY_GRACE, compute_graced_items
# tsk-598: единый предикат обязательной очереди (tsk-247). Импортируется, а не
# копируется словами: копия здесь уже разъехалась с очередью и дала 823 ложных
# «курса с неоценёнными работами» из 824. Цикла нет — `teacher_queue_service`
# учебный движок не тянет.
from app.services.teacher_queue_service import mandatory_review_sql
from app.utils.exceptions import DomainError
from pydantic import ValidationError

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
# Квиз-вопросы (SC_Qw/MC_Qw, tsk-124): ровно одна попытка — измеряют шкалы,
# у них нет «верно/неверно», повтор бессмысленен и задваивает scale_scores.
QUIZ_MAX_ATTEMPTS = 1
PASS_THRESHOLD_RATIO = 0.5

# tsk-626: пространство ключей advisory-lock для кеша `student_course_state`.
# ascii "SCST" (Student Course STate) — не пересекается с соседними ключами:
# Y-6 (0x59365453), генератор occurrence (0x4C534E43), attendance (0x4C534E41),
# link_audit (0x4C494E4B), тик состояний зависимостей (0x43445354).
COURSE_STATE_LOCK_NS = 0x53435354


async def lock_course_state(db: AsyncSession, student_id: int) -> None:
    """Взять транзакционную блокировку на кеш состояний курсов ученика (tsk-626).

    **Зачем.** Строки `student_course_state` пишутся не пачкой, а по одной,
    вперемешку с расчётами, и набор курсов у каждого писателя свой:
    `resolve_next_item` обновляет узел позиции плюс зависимости корня, фоновый
    тик — все цели `course_dependencies` активных учеников. Порядок захвата
    строк поэтому разный у разных запросов, и два параллельных писателя одного
    ученика встают в цикл ожидания. На проде 17.08.2026 это дало
    `DeadlockDetectedError` на `GET /learning/next-item` (ученик 3, курс 1455):
    PostgreSQL снял одну из транзакций, ученик получил 500.

    Сортировать сами строки недостаточно: набор курсов у писателей разный и
    заранее неизвестен (узел позиции резолвится по ходу). Поэтому порядок
    задаётся не строками, а одним ключом — ученик. Все писатели кеша берут
    ЭТУ блокировку до первой записи, значит по одному ученику они выстраиваются
    в очередь, а цикл ожидания построить не из чего.

    Блокировка транзакционная: снимается сама на commit/rollback, отдельного
    освобождения не требует. Повторный вызов в той же транзакции безвреден.

    :param student_id: ученик, чей кеш будет записан следом.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :student_id)"),
        {"ns": COURSE_STATE_LOCK_NS, "student_id": int(student_id)},
    )


async def upsert_course_state(
    db: AsyncSession, student_id: int, course_id: int, state: str
) -> None:
    """Единственная точка записи в `student_course_state` (tsk-626).

    Блокировка ученика берётся здесь же, а не в вызывающем коде: писателей
    несколько (движок, ручной прогресс, бэкфилл зависимостей, фоновый тик), и
    правило «сначала заблокируй ученика» соблюдается только тогда, когда его
    невозможно забыть. Многоучениковые писатели дополнительно обязаны обходить
    учеников по возрастанию `student_id` — иначе цикл ожидания собирается уже
    из самих блокировок.

    :param student_id: ученик.
    :param course_id: курс, чьё состояние кешируется.
    :param state: NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED_DEPENDENCY.
    """
    await lock_course_state(db, student_id)
    await db.execute(
        text("""
            INSERT INTO student_course_state (student_id, course_id, state, updated_at)
            VALUES (:student_id, :course_id, :state, now())
            ON CONFLICT (student_id, course_id)
            DO UPDATE SET state = EXCLUDED.state, updated_at = now()
        """),
        {"student_id": student_id, "course_id": course_id, "state": state},
    )

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

# tsk-541: зеркало _ACTIVE_ROOTS_OF_NODE_SQL — не «корни ученика», а «ученики
# узла». Нужно, чтобы держать student_course_state свежим для ПОДКУРСОВ,
# выступающих `course_id`/`required_course_id` в course_dependencies: и
# resolve_next_item (только зависимости корня), и manual_progress_service.
# _refresh_course_state (только корень touched-узла) кеш подкурса не пишут.
_ACTIVE_STUDENTS_WITH_NODE_SQL = """
WITH RECURSIVE ct AS (
    SELECT uc.user_id, uc.course_id AS member_course_id
    FROM user_courses uc
    WHERE uc.is_active = true
    UNION ALL
    SELECT ct.user_id, cp.course_id
    FROM ct
    JOIN course_parents cp ON cp.parent_course_id = ct.member_course_id
)
SELECT DISTINCT user_id
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
        roots = await self.list_active_roots_of_node(db, student_id, course_id)
        if len(roots) == 1:
            return roots[0]
        if len(roots) > 1:
            logger.info(
                "resolve_attempt_root: узел под несколькими корнями без контекста — "
                "student_id=%s course_id=%s roots=%s",
                student_id, course_id, roots,
            )
        return None

    async def list_active_roots_of_node(
        self,
        db: AsyncSession,
        student_id: int,
        course_id: int,
    ) -> list[int]:
        """Активные корни ученика, чьи деревья содержат узел `course_id` (tsk-264).

        Кандидаты пути, которым ученик мог прийти к заданию. Один — путь
        однозначен; несколько — неоднозначен (переиспользуемый узел).

        tsk-269: нужен не только для резолва, но и чтобы решить, форсить ли лимит
        при неоднозначном пути — см. `POST /attempts/{id}/answers`, шаг 2.3b.

        Args:
            db: async session.
            student_id: ID студента.
            course_id: курс узла (курс самого задания).

        Returns:
            Список ID корней (может быть пустым).
        """
        rows = (
            await db.execute(text(_ACTIVE_ROOTS_OF_NODE_SQL), {
                "student_id": student_id,
                "course_id": course_id,
            })
        ).fetchall()
        return [int(r[0]) for r in rows]

    async def list_active_students_with_node_in_tree(
        self,
        db: AsyncSession,
        course_id: int,
    ) -> list[int]:
        """Активные студенты, у кого `course_id` входит в дерево активного корня (tsk-541).

        Зеркало `list_active_roots_of_node` в обратную сторону: не «корни
        ученика», а «ученики узла». Используется, чтобы найти, для кого нужно
        держать `student_course_state` этого узла свежим — узел может быть
        подкурсом, выступающим `course_id` в `course_dependencies` (тем самым,
        доступ к которому проверяется через `_BLOCKED_COURSES_SQL`).

        Returns:
            Список ID студентов (может быть пустым).
        """
        rows = (
            await db.execute(text(_ACTIVE_STUDENTS_WITH_NODE_SQL), {"course_id": course_id})
        ).fetchall()
        return [int(r[0]) for r in rows]

    async def backfill_dependency_state(
        self,
        db: AsyncSession,
        course_id: int,
        required_course_id: int,
    ) -> int:
        """Пересчитать `student_course_state[required_course_id]` для активных
        студентов узла `course_id` (tsk-541).

        Закрывает окно молчания `_BLOCKED_COURSES_SQL`: та трактует ЛЮБОЕ
        отсутствие строки `student_course_state` как «не пройдено», а кеш
        подкурса ранее не писал никто (ни `resolve_next_item` — тот считает
        зависимости только КОРНЯ, ни `manual_progress_service.
        _refresh_course_state` — тот считает состояние только корня
        touched-узла). Используется сразу при записи `course_dependencies`
        (см. `CourseDependenciesService`) — синхронный путь для API-записи;
        фоновый тик `course_dependency_state_cron_service` — тот же расчёт для
        путей записи в обход API (прямой SQL, как было в tsk-523).

        Returns:
            Число студентов, для которых пересчитан кеш.
        """
        student_ids = await self.list_active_students_with_node_in_tree(db, course_id)
        # tsk-626: по возрастанию. Каждая запись берёт блокировку ученика и
        # держит её до конца транзакции — значит эта транзакция копит
        # блокировки нескольких учеников сразу. Пока порядок обхода
        # согласован у всех многоучениковых писателей (здесь и в фоновом
        # тике), цикл ожидания из самих блокировок не собирается.
        for student_id in sorted(student_ids):
            await self.compute_course_state(
                db, student_id, required_course_id, update_state_table=True
            )
        return len(student_ids)

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
        # tsk-575: ученик тоже не должен видеть живую ссылку на утраченный файл.
        last_answer_json = await mark_missing_one(row[4]) if isinstance(row[4], dict) else None
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

    async def compute_task_states_batch(
        self,
        db: AsyncSession,
        student_id: int,
        task_ids: List[int],
        *,
        last_results: Optional[dict[int, Any]] = None,
        root_course_id: Optional[int] = None,
        mark_missing: bool = False,
    ) -> dict[int, TaskStateResult]:
        """Пакетная версия `compute_task_state` для дерева заданий (review tsk-297, находка S3-3).

        `get_student_progress` (карточка ученика у преподавателя,
        `manual_progress_service.py`) раньше звал `compute_task_state` в цикле
        по каждому заданию дерева — там ~5 запросов на задание (тип задания,
        override лимита, `tasks.max_attempts`, счёт попыток, последний
        результат). На курсе из 172 заданий это ~860 запросов на одно открытие
        карточки. Здесь та же семантика статусов (см. docstring
        `compute_task_state`), но ДВА запроса на весь `task_ids` — плюс
        переиспользование уже загруженного вызывающим `last_results`.

        tsk-662: граница корня (`root_course_id`) поддержана — раньше здесь
        считалась ТОЛЬКО ветка `root_course_id=None`, и это закрывало дорогу
        главному потребителю. `resolve_next_item` идёт по дереву конкретного
        корня и звал `compute_task_state` на КАЖДОЕ задание: ~6 запросов на
        задание (тип задания — дважды, переопределение лимита, `max_attempts`,
        счёт попыток, последний результат). Замер на боевой базе (tsk-655):
        533 из 627 запросов одного `GET /me/last-position` — ровно это.

        Args:
            db: сессия БД.
            student_id: ID студента.
            task_ids: задания дерева; пустой список -> пустой dict без
                обращения к БД.
            last_results: опционально — уже загруженный вызывающим последний
                результат по каждому заданию (`task_id -> mapping` с колонками
                `attempt_id, submitted_at, score, max_score, answer_json,
                is_correct, checked_at`). Не передан — загружается здесь же
                отдельным запросом (тогда всего запросов не 2, а 3).
            root_course_id: корень дерева, которым ученик пришёл к заданиям
                (tsk-264). Задан — попытки считаются в его границах; `None` —
                по всем корням. Квиз (`QUIZ_TASK_TYPES`) корень игнорирует
                всегда: его ответ один навсегда и submit отклоняет повтор
                глобально — паритет с `compute_task_state`.
            mark_missing: пометить ли в `last_answer_json` вложения, файлов
                которых в хранилище больше нет (tsk-575). По умолчанию НЕТ, и
                это главный рычаг цены вызова: пометка стоит по сетевому
                запросу в объектное хранилище на КАЖДОЕ вложение, а нужна она
                только тому, кто отдаёт `answer_json` наружу — экрану работы.
                Учебному движку (`next-item`, `last-position`) и карточке
                ученика из `manual_progress_service` нужен только статус, и
                поле `last_answer_json` они не читают вовсе.

                tsk-735 (29.08): пока пометка считалась всегда, один
                `GET /me/last-position` у ученика 4515 делал 183 проверки
                файлов и занимал 5,96 с, из них 5,66 с (95%) — ожидание
                хранилища. Проверки идут через общий на процесс пул из шести
                потоков (`asyncio.to_thread`, два ядра), поэтому на границе
                занятия, когда группа разом жмёт «дальше», они выстраивались в
                общую очередь: запросы висели по 14-21 с, а база в это время
                простаивала — активных соединений ноль.

        Returns:
            `task_id -> TaskStateResult`, поэлементно эквивалентно
            `compute_task_state(db, student_id, tid, root_course_id)` для тех
            же `task_ids`. Исключение — `last_answer_json`: без `mark_missing`
            оно пустое (см. описание параметра).
        """
        if not task_ids:
            return {}

        ids = list(task_ids)

        # 1) Тип задания (квиз -> лимит 1, вне очереди) + лимит из override/
        #    tasks.max_attempts — тем же приоритетом, что и get_effective_attempt_limit.
        limit_rows = (
            await db.execute(
                text(
                    "SELECT t.id, t.task_content->>'type' AS ttype, t.max_attempts, "
                    "       o.max_attempts_override "
                    "FROM tasks t "
                    "LEFT JOIN student_task_limit_override o "
                    "       ON o.task_id = t.id AND o.student_id = :student_id "
                    "WHERE t.id = ANY(:ids)"
                ),
                {"student_id": student_id, "ids": ids},
            )
        ).fetchall()

        limits: dict[int, int] = {}
        for tid, ttype, max_attempts, override in limit_rows:
            tid = int(tid)
            if ttype in QUIZ_TASK_TYPES:
                limits[tid] = QUIZ_MAX_ATTEMPTS
            elif override is not None:
                limits[tid] = int(override)
            elif max_attempts is not None:
                limits[tid] = int(max_attempts)
            else:
                limits[tid] = DEFAULT_MAX_ATTEMPTS

        # 2) attempts_used. При заданном корне (tsk-264) считаем попытки только
        #    этого корня — кроме квиза, который корень игнорирует (tsk-662, тот
        #    же порядок ветвлений, что в `compute_task_state`). Попытки с
        #    `root_course_id IS NULL` лимит не расходуют ни в одном корне.
        count_rows = (
            await db.execute(
                text(
                    "SELECT tr.task_id, COUNT(*) "
                    "FROM task_results tr "
                    "INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL "
                    "INNER JOIN tasks t ON t.id = tr.task_id "
                    "WHERE tr.user_id = :student_id AND tr.task_id = ANY(:ids) "
                    "  AND ( "
                    "        CAST(:root_course_id AS INTEGER) IS NULL "
                    "        OR t.task_content->>'type' = ANY(:quiz_types) "
                    "        OR a.root_course_id = CAST(:root_course_id AS INTEGER) "
                    "  ) "
                    "GROUP BY tr.task_id"
                ),
                {
                    "student_id": student_id,
                    "ids": ids,
                    "root_course_id": root_course_id,
                    "quiz_types": list(QUIZ_TASK_TYPES),
                },
            )
        ).fetchall()
        attempts_used: dict[int, int] = {int(tid): int(cnt) for tid, cnt in count_rows}

        # 3) Последний результат по заданию — переиспользуем, если вызывающий
        #    уже его загрузил (get_student_progress грузит это для флага `manual`).
        if last_results is None:
            last_rows = (
                await db.execute(
                    text(
                        "SELECT DISTINCT ON (tr.task_id) "
                        "       tr.task_id, a.id AS attempt_id, tr.submitted_at, tr.score, "
                        "       tr.max_score, tr.answer_json, tr.is_correct, tr.checked_at "
                        "FROM task_results tr "
                        "INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL "
                        "WHERE tr.user_id = :student_id AND tr.task_id = ANY(:ids) "
                        "ORDER BY tr.task_id, tr.submitted_at DESC, tr.id DESC"
                    ),
                    {"student_id": student_id, "ids": ids},
                )
            ).mappings().fetchall()
            last_results = {int(r["task_id"]): r for r in last_rows}

        # tsk-593: наличие файлов вложений спрашиваем у хранилища одной пачкой
        # на весь список заданий — иначе экран курса дал бы по сетевому запросу
        # на каждое задание с вложением. tsk-735: и только если пометка вообще
        # нужна вызывающему — см. `mark_missing` в docstring.
        existing_attachments: set[str] = set()
        if mark_missing:
            existing_attachments = await existing_attachment_ids(
                [r["answer_json"] for r in last_results.values()]
            )

        results: dict[int, TaskStateResult] = {}
        for tid in ids:
            limit = limits.get(tid, DEFAULT_MAX_ATTEMPTS)
            used = attempts_used.get(tid, 0)
            row = last_results.get(tid)

            if row is None:
                results[tid] = TaskStateResult(
                    state="OPEN" if used == 0 else "IN_PROGRESS",
                    attempts_used=used,
                    attempts_limit_effective=limit,
                )
                continue

            last_score = int(row["score"]) if row["score"] is not None else 0
            last_max_score = int(row["max_score"]) if row["max_score"] is not None else 0
            # Без `mark_missing` поле остаётся пустым намеренно. Отдать ответ
            # БЕЗ пометки было бы хуже пустоты: экран показал бы живую ссылку
            # на файл, которого в хранилище уже нет (ровно то, что чинил
            # tsk-575), и молча — а пустое поле вызывающий заметит сразу.
            last_answer_json = (
                mark_missing_attachments(row["answer_json"], existing_attachments)
                if mark_missing and isinstance(row["answer_json"], dict) else None
            )
            common = dict(
                last_attempt_id=int(row["attempt_id"]),
                last_score=last_score,
                last_max_score=last_max_score,
                last_finished_at=row["submitted_at"],
                attempts_used=used,
                attempts_limit_effective=limit,
                last_answer_json=last_answer_json,
                last_is_correct=row["is_correct"],
                last_checked_at=row["checked_at"],
            )
            if last_max_score > 0 and (last_score / last_max_score) >= PASS_THRESHOLD_RATIO:
                results[tid] = TaskStateResult(state="PASSED", **common)
            elif used >= limit:
                results[tid] = TaskStateResult(state="BLOCKED_LIMIT", **common)
            else:
                results[tid] = TaskStateResult(state="FAILED", **common)

        return results

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

        # tsk-314: денаминатор обязан совпадать с тем, что студенту реально
        # показывает resolve_next_item — иначе подкурс с включённой выборкой
        # никогда не дошёл бы до COMPLETED (total_tasks считал бы и задания,
        # которые студенту никогда не предложат), вечно блокируя себя и любую
        # course_dependencies, ссылающуюся на него как на required_course_id.
        # Числитель (tasks_with_last_pass ниже) правки не требует: у
        # вырезанного выборкой задания по определению нет task_result этого
        # студента, оно и так не попадает в счёт пройденных.
        # tsk-692: содержимое, добавленное после того, как ученик прошёл тему,
        # обязательным для него не считается — иначе правка курса откатывала бы
        # его COMPLETED назад в IN_PROGRESS и включала бы, через
        # `course_dependencies`, замок на курсах, которые он уже прошёл.
        # Объединяем с выборкой (tsk-314) МНОЖЕСТВОМ, а не суммой счётчиков:
        # одно и то же задание может быть и вырезано выборкой, и прощено
        # правилом — сумма вычла бы его дважды и занизила знаменатель.
        graced = await compute_graced_items(db, student_id, course_id)

        excluded_tasks: set[int] = set(graced.tasks)
        sampling_map = await self._sampling_enabled_courses(
            db, tree_ids, student_id=student_id
        )
        for sampled_course_id, cfg in sampling_map.items():
            excluded_tasks |= await self._sampled_out_task_ids(
                db, sampled_course_id, student_id, cfg
            )
        total_tasks = max(0, total_tasks - len(excluded_tasks))

        materials_count_stmt = select(func.count(Materials.id)).where(
            Materials.course_id.in_(tree_ids),
            Materials.is_active.is_(True),
            Materials.requirement_level.in_(("required", "skippable")),
        )
        r = await db.execute(materials_count_stmt)
        total_materials = max(0, (r.scalar() or 0) - len(graced.materials))

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
            # tsk-626: запись только через общий helper — он берёт блокировку
            # ученика, без которой два параллельных писателя одного ученика
            # захватывают строки в разном порядке и встают в цикл ожидания.
            await upsert_course_state(db, student_id, course_id, state)

        # Y-6 Stage 4.3: course-completion event-driven escalation.
        # Если курс достиг COMPLETED, но есть pending TA/SA_COM (`checked_at IS NULL`)
        # → notify методиста (idempotent через `task_results.metrics.completion_escalated_at`).
        if state == "COMPLETED":
            try:
                # tsk-598: ТОТ ЖЕ предикат обязательной очереди, что у списка
                # проверки, claim-next и таймаут-эскалации (tsk-247/tsk-597).
                # Здесь была своя ось — «по типу задания», — и она давала ровно
                # тот же дефект: `SA_COM`/`TBL_COM` с
                # `manual_review_required=false` проверяет автомат, `checked_at`
                # у них не проставляется НИКОГДА, и любой завершённый курс
                # выглядел «с неоценёнными работами». Замер на проде
                # 2026-08-08: 824 pending по старой оси, настоящая из них ОДНА.
                #
                # Прежний комментарий здесь ссылался на `escalation_service.py`
                # как на образец — а образец был неверный (tsk-597). Ошибка
                # разошлась копированием, поэтому оба места теперь зовут ОДНУ
                # функцию, а не повторяют условие словами.
                #
                # Суть гейта прежняя и остаётся верной: обычный авто-проверяемый
                # SA (`checked_at` не проставляется в принципе) сюда попасть не
                # должен — просто теперь это выражено через `mrr` для всех
                # четырёх типов сразу, а не только для SA.
                pending_res = await db.execute(
                    text(
                        """
                        SELECT tr.id FROM task_results tr
                        JOIN tasks t ON t.id = tr.task_id
                        WHERE tr.user_id = :sid
                          AND t.course_id = ANY(:cids)
                          AND tr.checked_at IS NULL
                          AND """
                        # nosec B608 — подставляется SQL-фрагмент из
                        # `teacher_queue_service` (два литеральных алиаса),
                        # пользовательского ввода здесь нет.
                        + mandatory_review_sql("t", "tr")
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
        # Отдельная привязка ДО фильтра по корню (ниже `active` переопределяется).
        # Замыкание `_reachable` захватывает имя, а не значение: сошлись бы оно
        # на `active` — при заданном root_course_id доступным считалось бы только
        # текущее дерево, и межкурсовой пререквизит («ЕГЭ после Python для ЕГЭ»,
        # 33 ученика на проде) молча перестал бы блокировать.
        all_active = active
        # tsk-231 фаза 6: множество ДОСТУПНЫХ ученику курсов — объединение
        # деревьев ВСЕХ его активных корней, а не список самих корней.
        #
        # Почему именно дерево, а не `user_courses`. Ученика можно закрепить
        # только на КОРНЕВОМ курсе (триггер `trg_check_user_course_no_parents`),
        # а 79 из 81 прод-зависимости требуют ПОДКУРС («Списки» после «Циклов»
        # внутри «Python для ЕГЭ»). Условие «есть запись в user_courses на
        # required_course_id» для них ложно всегда — и молча отключило бы 214 из
        # 248 действующих блокировок у 38 учеников. Подкурс ученику доступен
        # через свой корень, и именно это делает зависимость выполнимой.
        #
        # Считается ЛЕНИВО и не более одного раза: обход дерева — это запрос на
        # каждый узел, а зависимости есть у меньшинства курсов. Платить за них
        # на каждом вызове next-item (в т.ч. у курсов вообще без зависимостей)
        # незачем. Множество берётся по ВСЕМ активным корням, а не только по
        # текущему: требуемый курс может лежать в дереве другого корня
        # (межкурсовой пререквизит «ЕГЭ после Python для ЕГЭ»).
        reachable_course_ids: Optional[set[int]] = None

        async def _reachable() -> set[int]:
            nonlocal reachable_course_ids
            if reachable_course_ids is None:
                acc: set[int] = set()
                for _uc in all_active:
                    acc.update(await self._collect_courses_in_order(db, _uc.course_id))
                reachable_course_ids = acc
            return reachable_course_ids
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

        # tsk-545: цикл ниже пересчитывает student_course_state только для
        # зависимостей КОРНЯ (course_dependencies.course_id = current_root_id).
        # Если пройденный узел сам выступает required_course_id для другой
        # ПОДКУРСОВОЙ зависимости (обе стороны не корень — тот же класс, что
        # tsk-541), синхронного пересчёта не было вовсе: кеш обновлял только
        # фоновый тик раз в 15 минут. Освежаем кеш узла здесь же, сразу после
        # того, как студент его прошёл.
        if located is not None:
            node_course_id = located[0]
            if await self._deps_repo.is_required_elsewhere(db, node_course_id):
                await self.compute_course_state(
                    db, student_id, node_course_id, update_state_table=True
                )

        for uc in active:
            current_root_id = uc.course_id

            # Зависимости: все required должны быть COMPLETED
            deps = await self._deps_repo.list_dependencies(db, current_root_id)
            for req_course in deps:
                # tsk-231 фаза 6: блокировать может только ДОСТУПНЫЙ ученику курс.
                # compute_course_state не смотрит в user_courses вовсе — у
                # недоступного ученику курса он даёт 0 из N, то есть
                # NOT_STARTED, неотличимо от «доступен, но не начат». Без этого
                # условия точечная зависимость (мини-курс повторения)
                # блокировала бы весь поток, а не адресатов. Заодно закрывается
                # класс «замок без выхода» (tsk-261): курс, до которого ученик
                # не может добраться, пройти физически нельзя, и замок висел бы
                # вечно (на проде таких пар 7 — курсы Excel из чужого дерева).
                if req_course.id not in await _reachable():
                    logger.info(
                        "resolve_next_item: student_id=%s root=%s зависимость required=%s "
                        "пропущена — курс ученику недоступен (tsk-231)",
                        student_id, current_root_id, req_course.id,
                    )
                    continue
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
                        # tsk-231: req_course уже загружен ORM'ом
                        # (list_dependencies), доп. запрос не нужен.
                        dependency_course_title=req_course.title,
                        dependency_course_uid=req_course.course_uid,
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
                            # tsk-692: тот же список, что и в обходе ниже
                            # (_first_incomplete_material) — иначе позиция могла
                            # бы указывать на материал, которому правило уже
                            # сняло обязательность.
                            for i, op in await self._effective_material_rows(
                                db, cid, student_id, root_course_id=current_root_id
                            )
                            if self._order_key(op, i) > pos_key
                        ]
                    else:
                        # Задание идёт после всех материалов своего курса — значит
                        # материалы этого курса уже позади позиции.
                        material_ids = []
                        task_ids = [
                            i
                            # tsk-314: тот же список, что видит студент в обходе
                            # ниже (_first_incomplete_task) — иначе позиция
                            # могла бы указывать на задание, вырезанное выборкой.
                            for i, op in await self._effective_task_rows(
                                db, cid, student_id, root_course_id=current_root_id
                            )
                            if self._order_key(op, i) > pos_key
                        ]

                # Первый незавершённый материал
                mat = await self._first_incomplete_material(
                    db, student_id, cid, material_ids=material_ids,
                    root_course_id=current_root_id,
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

        tsk-662: результат КЕШИРУЕТСЯ на время сессии БД (то есть на один
        запрос; см. `courses_repo.course_tree_cache`). Один и тот же корень
        обходился по нескольку раз за вызов: `resolve_next_item` считает и
        множество доступных курсов (`_reachable`), и обход самого корня, а
        сводка занятия шла по КАЖДОМУ из 7-12 участников группы — у которых
        дерево одно и то же. Замер на боевой базе (tsk-655): сводка на 12
        участников — 1093 запроса, `/me/last-position` одного ученика —
        645-1597. Кеш сбрасывается при правке иерархии в той же сессии
        (`set_parent_courses`, `update_course_parent_order`).
        """
        cache = course_tree_cache(db)
        cached = cache.get(root_id)
        if cached is not None:
            return list(cached)

        result: List[int] = []
        seen: set[int] = set()

        async def walk(course_id: int) -> None:
            if course_id in seen:
                return
            seen.add(course_id)
            # tsk-662: `get_child_rows`, а не `get_children` — обходу нужны
            # только id и порядок, а подгрузка родителей узла добавляла
            # ВТОРОЙ запрос на каждый узел дерева.
            children = await self._courses_repo.get_child_rows(db, course_id)
            # order_number ASC NULLS LAST, затем id — тот же ключ, что у
            # элементов курса (`_order_key`), а не его копия: правило одно,
            # и меняться оно должно в одном месте.
            for _cid, _ord, _title in sorted(children, key=lambda x: self._order_key(x[1], x[0])):
                await walk(_cid)
            # Материалы/задания самого курса — после всех его подкурсов (post-order).
            result.append(course_id)

        await walk(root_id)
        cache[root_id] = list(result)
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

    async def _sampling_enabled_courses(
        self,
        db: AsyncSession,
        course_ids: Sequence[int],
        student_id: Optional[int] = None,
    ) -> dict[int, dict]:
        """Курсы из `course_ids` с включённой выборкой по сложности (tsk-314).

        Два источника порога, и личный сильнее общего:

        1. `courses.sampling_config` — настройка методиста на подкурс, одна на
           всех учеников;
        2. **персональный план объёма** (`student_program_scope`, tsk-798) —
           сколько тренажёра этому ученику помещается до его срока. Программа
           у ноябрьского и у сентябрьского ученика разной длины, и общий порог
           не может выразить это в принципе.

        Личный порог перекрывает общий, потому что общий не знает ни срока
        ученика, ни его темпа. `easy_ratio` при этом берётся из настройки
        курса (методическое решение о составе), а не из плана — план отвечает
        за объём, не за пропорцию.

        Один лёгкий запрос по PK плюс один по ученику; тяжёлый путь (JOIN c
        difficulties, сэмплинг) ниже выполняется только для курсов, реально
        попавших в результат.

        Фильтрация `enabled` — в Python, не в SQL: `sampling_config` пишется
        напрямую (нет PATCH-эндпоинта), и SQL-каст `->>'enabled')::boolean`
        на невалидном значении уронил бы запрос — а с ним весь next-item для
        ВСЕХ студентов, не только владельца битого конфига.
        """
        if not course_ids:
            return {}
        rows = (
            await db.execute(
                text(
                    "SELECT id, sampling_config FROM courses "
                    "WHERE id = ANY(:ids) AND sampling_config IS NOT NULL"
                ),
                {"ids": list(course_ids)},
            )
        ).fetchall()
        result = {
            int(cid): cfg
            for cid, cfg in rows
            if isinstance(cfg, dict) and cfg.get("enabled")
        }

        if student_id is None:
            return result

        personal = await program_scope_thresholds(db, student_id=student_id)
        for cid in course_ids:
            threshold = personal.get(int(cid))
            if threshold is None:
                continue
            base = result.get(int(cid)) or {}
            result[int(cid)] = {
                "enabled": True,
                "threshold": max(int(threshold), 1),
                "easy_ratio": base.get("easy_ratio", 0.5),
            }
        return result

    async def _sampled_out_task_ids(
        self,
        db: AsyncSession,
        course_id: int,
        student_id: int,
        config: dict,
    ) -> set[int]:
        """ID заданий EASY/NORMAL курса, НЕ попавших в выборку студента (tsk-314).

        Пустое множество — выборка не нужна (заданий меньше/равно порога,
        в курсе нет EASY/NORMAL, либо `config` не прошёл валидацию формата:
        битый конфиг деградирует в «выборки нет», а не роняет resolve_next_item).
        """
        try:
            cfg = CourseSamplingConfig.model_validate(config)
        except ValidationError:
            logger.warning(
                "tsk-314: невалидный sampling_config на курсе %s: %r", course_id, config
            )
            return set()
        if not cfg.enabled:
            return set()

        rows = await self._ordered_task_rows(db, course_id)
        if not rows:
            return set()
        task_ids = [i for i, _ in rows]

        diff_rows = (
            await db.execute(
                text(
                    "SELECT t.id, d.code FROM tasks t "
                    "JOIN difficulties d ON d.id = t.difficulty_id "
                    "WHERE t.id = ANY(:ids)"
                ),
                {"ids": task_ids},
            )
        ).fetchall()
        code_by_id = {int(tid): code for tid, code in diff_rows}

        easy_ids = [i for i in task_ids if code_by_id.get(i) == "EASY"]
        normal_ids = [i for i in task_ids if code_by_id.get(i) == "NORMAL"]
        if not easy_ids and not normal_ids:
            return set()

        # tsk-798: уже пройденное выборка не трогает. Раньше это было не нужно
        # (выборка настраивалась на подкурс заранее, до того как кто-то начал),
        # а теперь она включается ученикам, которые давно учатся. Вырезав
        # решённое, мы получили бы числитель прогресса больше знаменателя:
        # `compute_course_state` вычитает вырезанное из общего числа заданий, а
        # пройденные считает как есть — и подкурс мог бы не закрыться никогда.
        solved = set(
            (
                await db.execute(
                    text(
                        "SELECT DISTINCT tr.task_id FROM task_results tr "
                        "  JOIN attempts a ON a.id = tr.attempt_id "
                        "   AND a.cancelled_at IS NULL "
                        " WHERE tr.user_id = :sid AND tr.task_id = ANY(:ids) "
                        "UNION "
                        "SELECT stp.task_id FROM student_task_progress stp "
                        " WHERE stp.student_id = :sid AND stp.task_id = ANY(:ids)"
                    ),
                    {"sid": student_id, "ids": easy_ids + normal_ids},
                )
            ).scalars().all()
        )

        kept = sample_task_ids(
            easy_ids=easy_ids,
            normal_ids=normal_ids,
            threshold=cfg.threshold,
            easy_ratio=cfg.easy_ratio,
            student_id=student_id,
            course_id=course_id,
            keep_ids=solved,
        )
        return (set(easy_ids) | set(normal_ids)) - kept

    async def _effective_task_rows(
        self,
        db: AsyncSession,
        course_id: int,
        student_id: int,
        root_course_id: Optional[int] = None,
    ) -> List[Tuple[int, Optional[int]]]:
        """(id, order_position) заданий курса, которые студент реально видит.

        С учётом выборки по сложности (tsk-314): если на курсе включена
        выборка, исключает НЕ отобранные EASY/NORMAL задания. THEORY и любая
        сложность вне EASY/NORMAL (HARD/PROJECT) выборке не подлежат —
        остаются в списке всегда. Без выборки — то же, что `_ordered_task_rows`.

        tsk-692: и без заданий, добавленных в курс уже после того, как ученик
        прошёл тему, — им обязательность снята, они ведут себя как
        `recommended`. `root_course_id` — корень, от которого считать правило
        (оно смотрит и на предков узла); без него правило видит только сам
        курс и потому прощает меньше, а не больше.
        """
        rows = await self._ordered_task_rows(db, course_id)
        if not rows:
            return rows

        cfg_map = await self._sampling_enabled_courses(
            db, [course_id], student_id=student_id
        )
        config = cfg_map.get(course_id)
        dropped: set[int] = set()
        if config:
            dropped = await self._sampled_out_task_ids(db, course_id, student_id, config)

        graced = await compute_graced_items(db, student_id, root_course_id or course_id)
        if not dropped and not graced.tasks:
            return rows
        return [
            (i, op) for i, op in rows if i not in dropped and i not in graced.tasks
        ]

    async def _effective_material_rows(
        self,
        db: AsyncSession,
        course_id: int,
        student_id: int,
        root_course_id: Optional[int] = None,
    ) -> List[Tuple[int, Optional[int]]]:
        """(id, order_position) материалов курса, которые студент реально видит.

        tsk-692: без материалов, добавленных после того, как ученик прошёл
        тему. Парный к `_effective_task_rows`; выборки по сложности у
        материалов нет, поэтому здесь только это правило.
        """
        rows = await self._ordered_material_rows(db, course_id)
        if not rows:
            return rows
        graced = await compute_graced_items(db, student_id, root_course_id or course_id)
        if not graced.materials:
            return rows
        return [(i, op) for i, op in rows if i not in graced.materials]

    async def _first_incomplete_material(
        self,
        db: AsyncSession,
        student_id: int,
        course_id: int,
        material_ids: Optional[List[int]] = None,
        root_course_id: Optional[int] = None,
    ) -> Optional[int]:
        """ID первого материала курса, не отмеченного как completed для студента.

        `material_ids` — необязательный заранее суженный список (tsk-261: обход от
        текущей позиции передаёт сюда только материалы ПОСЛЕ неё).

        `root_course_id` (tsk-692) — корень, от которого считается правило
        «добавленное после прохождения не долг».
        """
        if material_ids is None:
            material_ids = [
                i
                for i, _ in await self._effective_material_rows(
                    db, course_id, student_id, root_course_id=root_course_id
                )
            ]

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
            # tsk-314: сужение по выборке — тот же список, что и narrowing
            # в resolve_next_item (иначе следующим предлагалось бы задание,
            # которое сама выборка исключила).
            task_ids = [
                i
                for i, _ in await self._effective_task_rows(
                    db, course_id, student_id, root_course_id=root_course_id
                )
            ]
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

        # tsk-662: состояния считаются ПАКЕТОМ — три запроса на весь список
        # заданий узла вместо ~6 на каждое. Поэлементный `compute_task_state`
        # здесь был главной ценой `/me/last-position`: движок идёт по учебному
        # порядку до первого незавершённого элемента, то есть проверяет ВСЁ, что
        # ученик уже прошёл, — 533 запроса из 627 на замере боевой базы
        # (tsk-655). Цена росла по мере прохождения курса: хуже всего вызов
        # чувствовал себя у самых сильных учеников.
        candidates = [tid for tid in task_ids if tid not in skipped_ids]
        if not candidates:
            return (None, None)

        states = await self.compute_task_states_batch(
            db, student_id, candidates, root_course_id=root_course_id
        )
        for tid in candidates:
            state_result = states.get(tid)
            if state_result is None:
                continue
            if state_result.state == "BLOCKED_LIMIT":
                return (None, tid)
            if state_result.state in ("OPEN", "IN_PROGRESS", "FAILED"):
                return (tid, None)
        return (None, None)
