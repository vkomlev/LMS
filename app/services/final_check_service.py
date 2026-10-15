"""Итоговый срез после курса (tsk-994, П5).

Решение оператора Р4: срез — ТОЛЬКО диагностика. Статус курса, зависимости и
выпуск он не меняет; ответы пишутся в `task_results` с
`source_system='final_check'`, а этот источник исключён из учебного пути тем же
правилом, что и повтор (`learning_gaps_service.NON_LEARNING_PATH_SOURCES`):
статус задания, счёт и лимит попыток, «пройдено», статус курса его не видят,
аналитика пробелов — тоже (белый список `REAL_STUDENT_SOURCES`).

Устройство:

- рубильник — `courses.final_check_config = {"enabled": bool, "size": int}` на
  КОРНЕВОМ курсе; NULL и `enabled=false` — выключено;
- доступ — только после завершения курса (`compute_course_state == COMPLETED`)
  при включённом рубильнике, иначе 409 `final_check_locked`;
- состав — `size` заданий из ПРОЙДЕННЫХ учеником заданий курса и подкурсов,
  равномерно по главам (подкурсы первого уровня), детерминированно (сид
  `task_sampling.deterministic_seed` с солью «final»); выбирается один раз при
  первом открытии и хранится в `final_check_runs`;
- в срез идут только задания с автопроверкой целиком — тем же условием, что у
  повтора (`retrieval_service.task_supports_review`): без вердикта автоматики
  итог среза не посчитать, а ручной проверке после курса проверять некому;
- итог — доля верных и главы с ошибками; «Навык сохранился» — от 80 % верных.
  Ошибочные задания при включённом на курсе повторении (П3) ставятся в
  повторение на стадию 0 (`retrieval_service.schedule_forgotten`).

Срез один на (ученик, корневой курс): пройденный срез показывает свой итог,
повторно его не пройти.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, NoReturn, Optional, Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courses import Courses
from app.models.final_check_runs import FinalCheckRuns
from app.schemas.checking import CheckResult, StudentAnswer
from app.schemas.final_check import DEFAULT_FINAL_CHECK_SIZE, MAX_FINAL_CHECK_SIZE
from app.schemas.task_content import COMMENT_TASK_TYPES, TaskContent
from app.services import retrieval_service
from app.services.learning_engine_service import PASS_THRESHOLD_RATIO, LearningEngineService
from app.services.learning_gaps_service import (
    FINAL_CHECK_SOURCE,
    MANUAL_TEACHER_SOURCE,
    learning_path_results_filter,
)
from app.services.task_sampling import deterministic_seed
from app.utils.exceptions import DomainError

if TYPE_CHECKING:
    from app.auth.current_user import CurrentUser

logger = logging.getLogger(__name__)

#: «Навык сохранился» — доля верных не ниже этой (постановка, шаг 17).
SKILL_RETAINED_RATIO = 0.8

#: Соль сида выборки: отдельная от выборки по сложности (tsk-314).
SEED_SALT = "final"

#: Коды отказов — клиент различает их по `payload.code`.
FINAL_CHECK_LOCKED_CODE = "final_check_locked"
FINAL_CHECK_EMPTY_CODE = "final_check_empty"
FINAL_CHECK_NOT_STARTED_CODE = "final_check_not_started"
FINAL_CHECK_FINISHED_CODE = "final_check_finished"
FINAL_CHECK_NOT_IN_RUN_CODE = "final_check_task_not_in_run"
FINAL_CHECK_ANSWERED_CODE = "final_check_already_answered"
FINAL_CHECK_UNSUPPORTED_CODE = "final_check_unsupported"
FINAL_CHECK_EVIDENCE_REQUIRED_CODE = "evidence_required"


@dataclass(frozen=True)
class Chapter:
    """Глава курса: подкурс первого уровня либо сам корень."""

    course_id: int
    course_uid: Optional[str]
    title: str


@dataclass
class CourseLayout:
    """Курсы дерева и их главы — в порядке программы."""

    tree_ids: list[int]
    chapters: list[Chapter]
    chapter_of: dict[int, int] = field(default_factory=dict)

    def chapter(self, course_id: Optional[int]) -> Chapter:
        """Глава курса задания; неизвестный курс относится к корню."""
        root = self.chapters[-1]
        cid = self.chapter_of.get(int(course_id)) if course_id is not None else None
        for ch in self.chapters:
            if ch.course_id == cid:
                return ch
        return root


@dataclass(frozen=True)
class AnswerOutcome:
    """Итог ответа на задание среза."""

    task_id: int
    task_result_id: int
    check_result: CheckResult
    is_correct: bool
    run: FinalCheckRuns


def _now() -> datetime:
    """Текущее время, всегда с часовым поясом (UTC)."""
    return datetime.now(timezone.utc)


def parse_config(config: Any) -> tuple[bool, int]:
    """Разобрать `courses.final_check_config` в (включён, размер).

    NULL, не-объект и объект без `enabled=true` — выключено: рубильник
    включается только явно. Размер вне пределов — значение по умолчанию.
    """
    if not isinstance(config, dict):
        return False, DEFAULT_FINAL_CHECK_SIZE
    size = config.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= MAX_FINAL_CHECK_SIZE:
        size = DEFAULT_FINAL_CHECK_SIZE
    return config.get("enabled") is True, size


async def read_config(db: AsyncSession, course_id: int) -> tuple[bool, int]:
    """Рубильник среза курса: (включён, размер)."""
    config = (
        await db.execute(
            select(Courses.final_check_config).where(Courses.id == course_id)
        )
    ).scalar_one_or_none()
    return parse_config(config)


def select_task_ids(
    pools: Sequence[tuple[int, Sequence[int]]],
    *,
    size: int,
    student_id: int,
    course_id: int,
) -> list[int]:
    """Детерминированно выбрать `size` заданий, равномерно по главам.

    Args:
        pools: `(id главы, id пройденных заданий главы)` в порядке программы.
        size: размер среза.
        student_id: ученик (часть сида).
        course_id: корневой курс (часть сида).

    Returns:
        id заданий в порядке показа: по главам в порядке программы.

    Каждая глава получает задания по кругу, по одному за проход, — так при
    глав ≤ `size` в срез попадает хотя бы одно задание каждой главы, а остаток
    делится поровну (недостача одной главы добирается из остальных). Если глав
    больше, чем `size`, порядок обхода глав перемешивается тем же сидом —
    иначе срез всегда брал бы первые главы курса.
    """
    rng = random.Random(deterministic_seed(student_id, course_id, salt=SEED_SALT))
    shuffled: list[tuple[int, list[int]]] = []
    for chapter_id, ids in pools:
        pool = sorted({int(i) for i in ids})
        if not pool:
            continue
        rng.shuffle(pool)
        shuffled.append((chapter_id, pool))
    if size <= 0 or not shuffled:
        return []

    order = list(range(len(shuffled)))
    if len(shuffled) > size:
        rng.shuffle(order)
    picked: dict[int, list[int]] = {i: [] for i in order}
    taken = 0
    depth = 0
    while taken < size:
        progressed = False
        for i in order:
            pool = shuffled[i][1]
            if depth < len(pool) and taken < size:
                picked[i].append(pool[depth])
                taken += 1
                progressed = True
        if not progressed:
            break
        depth += 1
    return [tid for i in range(len(shuffled)) for tid in picked[i]]


async def course_layout(db: AsyncSession, root_course_id: int) -> CourseLayout:
    """Курсы дерева и главы корня — в порядке программы.

    Глава — подкурс первого уровня (`course_parents`, порядок `order_number`)
    со всем своим поддеревом. Задания самого корня и «прозрачных» подкурсов
    (tsk-1198: их задания и так стоят в списке родителя) относятся к корню.
    Подкурс, висящий под несколькими главами, относится к первой из них.
    """
    engine = LearningEngineService()
    tree_ids = await engine._collect_courses_in_order(db, root_course_id)
    if root_course_id not in tree_ids:
        tree_ids = [*tree_ids, root_course_id]
    tree = set(tree_ids)
    rows = (
        await db.execute(
            text(
                """
                SELECT c.id, c.course_uid, c.title
                FROM course_parents cp
                JOIN courses c ON c.id = cp.course_id
                WHERE cp.parent_course_id = :root
                  AND NOT cp.is_transparent
                ORDER BY cp.order_number NULLS LAST, cp.course_id
                """
            ),
            {"root": root_course_id},
        )
    ).mappings().all()
    root_row = (
        await db.execute(
            text("SELECT id, course_uid, title FROM courses WHERE id = :id"),
            {"id": root_course_id},
        )
    ).mappings().first()
    chapters: list[Chapter] = []
    chapter_of: dict[int, int] = {}
    for r in rows:
        cid = int(r["id"])
        if cid not in tree:
            continue
        chapters.append(Chapter(cid, r["course_uid"], r["title"] or f"Глава #{cid}"))
        for sub in await engine._collect_courses_in_order(db, cid):
            chapter_of.setdefault(int(sub), cid)
    # Корень — последняя «глава»: его задания в программе идут после подкурсов.
    chapters.append(
        Chapter(
            root_course_id,
            root_row["course_uid"] if root_row else None,
            (root_row["title"] if root_row else None) or f"Курс #{root_course_id}",
        )
    )
    for cid in tree_ids:
        chapter_of.setdefault(int(cid), root_course_id)
    return CourseLayout(tree_ids=list(tree_ids), chapters=chapters, chapter_of=chapter_of)


async def eligible_task_ids(
    db: AsyncSession, *, student_id: int, layout: CourseLayout
) -> dict[int, list[int]]:
    """Пройденные учеником задания дерева, годные для среза, — по главам.

    «Пройдено» — как в `compute_task_state`: последний результат учебного пути
    (повтор и сам срез не в счёт) в не отменённой попытке проходной. Сверх того
    последний результат должен быть СВОЕЙ сдачей: ручной зачёт преподавателя
    (`manual_teacher`) — не решение ученика, проверять «сохранился ли навык»
    по нему нечего. И задание должно проверяться автоматически целиком
    (`retrieval_service.task_supports_review`).
    """
    from app.services.checking_service import CheckingService

    rows = (
        await db.execute(
            text(
                f"""
                WITH last_per_task AS (
                    SELECT DISTINCT ON (tr.task_id)
                        tr.task_id, tr.score, tr.max_score, tr.source_system
                    FROM task_results tr
                    JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
                    JOIN tasks t
                      ON t.id = tr.task_id
                     AND t.course_id = ANY(:course_ids)
                     AND t.is_active = true
                    WHERE tr.user_id = :sid
                      AND {learning_path_results_filter('tr')}
                    ORDER BY tr.task_id, tr.submitted_at DESC, tr.id DESC
                )
                SELECT l.task_id, t.course_id, t.task_content, t.solution_rules,
                       t.max_score AS task_max_score
                FROM last_per_task l
                JOIN tasks t ON t.id = l.task_id
                WHERE l.max_score > 0
                  AND (l.score::float / l.max_score) >= :ratio
                  AND l.source_system IS DISTINCT FROM :manual
                ORDER BY l.task_id
                """
            ),
            {
                "sid": student_id,
                "course_ids": layout.tree_ids,
                "ratio": PASS_THRESHOLD_RATIO,
                "manual": MANUAL_TEACHER_SOURCE,
            },
        )
    ).mappings().all()
    by_chapter: dict[int, list[int]] = {}
    for r in rows:
        rules = CheckingService.build_solution_rules(r["solution_rules"], r["task_max_score"])
        if not retrieval_service.task_supports_review(r["task_content"], rules):
            continue
        chapter = layout.chapter(r["course_id"])
        by_chapter.setdefault(chapter.course_id, []).append(int(r["task_id"]))
    return by_chapter


async def course_completed(db: AsyncSession, *, student_id: int, course_id: int) -> bool:
    """Завершён ли курс учеником (`compute_course_state == COMPLETED`).

    Кеш состояния (`student_course_state`) не обновляется — срез ничего не
    меняет в учебном пути (Р4). Оговорка: при COMPLETED сам
    `compute_course_state` запускает свою идемпотентную эскалацию «курс
    завершён, есть непроверенные» — так же, как при любом его вызове.
    """
    state = await LearningEngineService().compute_course_state(
        db, student_id, course_id, update_state_table=False
    )
    return state.state == "COMPLETED"


async def get_run(
    db: AsyncSession, *, student_id: int, course_id: int, lock: bool = False
) -> Optional[FinalCheckRuns]:
    """Прогон среза ученика по курсу (или None)."""
    stmt = select(FinalCheckRuns).where(
        FinalCheckRuns.student_id == student_id,
        FinalCheckRuns.course_id == course_id,
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_status(
    db: AsyncSession, *, student_id: int, course_id: int
) -> tuple[str, int, Optional[FinalCheckRuns]]:
    """Состояние среза для карточки на странице курса. Ничего не пишет.

    Returns:
        (статус, размер по настройке, прогон или None). Статус:
        `disabled` — рубильник выключен (карточки нет); `finished` — срез
        пройден; `locked` — курс не завершён; `closed` — тариф закрывает
        работу в курсе (выпускник); `in_progress` / `ready`.
    """
    enabled, size = await read_config(db, course_id)
    if not enabled:
        return "disabled", size, None
    run = await get_run(db, student_id=student_id, course_id=course_id)
    if run is not None and run.finished_at is not None:
        return "finished", size, run
    if not await course_completed(db, student_id=student_id, course_id=course_id):
        return "locked", size, run
    # Тариф закрывает работу в курсе (tsk-673, «Выпускник»): начать срез и
    # ответить нельзя — карточку «Пройти срез» не показываем вовсе.
    from app.services import graduation_service

    try:
        await graduation_service.assert_course_work_allowed(db, student_id)
    except DomainError:
        return "closed", size, run
    return ("in_progress" if run is not None else "ready"), size, run


async def _assert_open(db: AsyncSession, *, student_id: int, course_id: int) -> int:
    """Рубильник включён и курс завершён — иначе 409 `final_check_locked`.

    Returns:
        Размер среза по настройке курса.
    """
    enabled, size = await read_config(db, course_id)
    if not enabled or not await course_completed(
        db, student_id=student_id, course_id=course_id
    ):
        raise DomainError(
            "Итоговый срез откроется, когда курс будет пройден целиком.",
            status_code=409,
            payload={"code": FINAL_CHECK_LOCKED_CODE},
        )
    return size


async def start(db: AsyncSession, *, student_id: int, course_id: int) -> FinalCheckRuns:
    """Открыть срез: при первом открытии выбрать и сохранить состав.

    Повторное открытие возвращает тот же прогон (состав не пересобирается).
    Гонка двух первых открытий решается уникальностью (ученик, курс):
    проигравший получает строку победителя.

    Raises:
        DomainError: 409 `final_check_locked` — срез выключен или курс не
            завершён; 409 `final_check_empty` — нет пройденных заданий с
            автопроверкой.
    """
    size = await _assert_open(db, student_id=student_id, course_id=course_id)
    run = await get_run(db, student_id=student_id, course_id=course_id)
    if run is not None:
        if run.finished_at is None:
            await _skip_deleted(db, student_id=student_id, course_id=course_id)
            run = await get_run(db, student_id=student_id, course_id=course_id)
        assert run is not None
        return run

    layout = await course_layout(db, course_id)
    by_chapter = await eligible_task_ids(db, student_id=student_id, layout=layout)
    pools = [
        (ch.course_id, by_chapter.get(ch.course_id, [])) for ch in layout.chapters
    ]
    task_ids = select_task_ids(pools, size=size, student_id=student_id, course_id=course_id)
    if not task_ids:
        raise DomainError(
            "В курсе нет пройденных заданий, по которым можно собрать срез.",
            status_code=409,
            payload={"code": FINAL_CHECK_EMPTY_CODE},
        )
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    await db.execute(
        pg_insert(FinalCheckRuns)
        .values(student_id=student_id, course_id=course_id, task_ids=task_ids, answers={})
        .on_conflict_do_nothing(constraint="uq_final_check_runs_student_course")
    )
    await db.commit()
    run = await get_run(db, student_id=student_id, course_id=course_id)
    assert run is not None  # строка есть: вставлена здесь или конкурентом
    logger.info(
        "tsk-994: срез открыт student_id=%s course_id=%s run_id=%s task_ids=%s",
        student_id, course_id, run.id, run.task_ids,
    )
    return run


def build_result(
    run_task_ids: Sequence[int],
    answers: dict[str, Any],
    layout: CourseLayout,
    task_courses: dict[int, Optional[int]],
    scheduled: Sequence[int],
) -> dict[str, Any]:
    """Итог среза: доля верных, главы с ошибками (в порядке программы).

    Задания, пропущенные сервером (перестали проверяться автоматически, см.
    `answer`), в счёт не входят.
    """
    scored = [
        int(t) for t in run_task_ids
        if isinstance(answers.get(str(t)), dict) and not answers[str(t)].get("skipped")
    ]
    correct = [t for t in scored if answers[str(t)].get("is_correct") is True]
    wrong = [t for t in scored if answers[str(t)].get("is_correct") is not True]
    total = len(scored)
    ratio = (len(correct) / total) if total else 0.0
    errors: list[dict[str, Any]] = []
    for ch in layout.chapters:
        ids = [t for t in wrong if layout.chapter(task_courses.get(t)).course_id == ch.course_id]
        if ids:
            errors.append(
                {
                    "course_id": ch.course_id,
                    "course_uid": ch.course_uid,
                    "title": ch.title,
                    "task_ids": ids,
                }
            )
    return {
        "total": total,
        "correct": len(correct),
        "ratio": round(ratio, 4),
        "skill_retained": total > 0 and ratio >= SKILL_RETAINED_RATIO,
        "chapters_with_errors": errors,
        "scheduled_for_review": sorted(int(t) for t in scheduled),
    }


async def _task_courses(db: AsyncSession, task_ids: Sequence[int]) -> dict[int, Optional[int]]:
    rows = (
        await db.execute(
            text("SELECT id, course_id FROM tasks WHERE id = ANY(:ids)"),
            {"ids": [int(t) for t in task_ids]},
        )
    ).all()
    return {int(r[0]): (int(r[1]) if r[1] is not None else None) for r in rows}


async def _finish_if_complete(
    db: AsyncSession, run: FinalCheckRuns, *, student_id: int, course_id: int
) -> None:
    """Если отвечены (или пропущены) все задания — посчитать итог и закрыть срез."""
    answers = dict(run.answers or {})
    if any(str(t) not in answers for t in run.task_ids):
        return
    wrong_results = {
        int(t): int(answers[str(t)]["result_id"])
        for t in run.task_ids
        if not answers[str(t)].get("skipped")
        and answers[str(t)].get("is_correct") is not True
        and answers[str(t)].get("result_id") is not None
    }
    # Ошибочные — в повторение (П3), если оно включено на курсе. Логика
    # расписания живёт в retrieval_service, здесь только вызов.
    scheduled = await retrieval_service.schedule_forgotten(
        db, student_id=student_id, root_course_id=course_id, results=wrong_results
    )
    layout = await course_layout(db, course_id)
    task_courses = await _task_courses(db, run.task_ids)
    run.result = build_result(run.task_ids, answers, layout, task_courses, scheduled)
    run.finished_at = _now()
    logger.info(
        "tsk-994: срез окончен student_id=%s course_id=%s run_id=%s correct=%s/%s",
        student_id, course_id, run.id, run.result["correct"], run.result["total"],
    )


async def answer(
    db: AsyncSession,
    *,
    student_id: int,
    course_id: int,
    task_id: int,
    answer: StudentAnswer,
    attempt_source_system: str = "spw_web",
    current_user: Optional["CurrentUser"] = None,
) -> AnswerOutcome:
    """Принять ответ на задание среза; на последнем — посчитать итог.

    Ответ проверяется той же автоматикой, что и обычная сдача, и пишется в
    `task_results` с `source_system='final_check'` в курсовую попытку (та же
    механика, что у повтора, `retrieval_service._course_attempt`). Статус
    задания и курса, счёт и лимит попыток он не меняет.

    Один ответ на задание: срез — снимок «помню ли», а не тренировка.

    Raises:
        DomainError: 409 `final_check_locked` / `final_check_finished` /
            `final_check_already_answered` / `final_check_unsupported`;
            404 `final_check_not_started` / `final_check_task_not_in_run`;
            400 — тип ответа не совпадает с типом задания; 422
            `evidence_required` — SA_COM/TBL_COM без комментария.
            HTTPException 403 — не записан на курс задания (tsk-272).
    """
    from app.services import course_activity_service, dependency_lock_service
    from app.services.checking_service import CheckingService
    from app.services.task_results_service import TaskResultsService
    from app.services.tasks_acl_service import assert_task_access
    from app.services.tasks_service import TasksService

    await _assert_open(db, student_id=student_id, course_id=course_id)

    # Строка прогона под замком СРАЗУ и до единственной фиксации в конце
    # (попытка ниже заводится без commit): два одновременных ответа на одно
    # задание сериализуются, второй увидит его отвеченным и получит 409.
    run = await get_run(db, student_id=student_id, course_id=course_id, lock=True)
    if run is None:
        raise DomainError(
            "Итоговый срез ещё не начат.",
            status_code=404,
            payload={"code": FINAL_CHECK_NOT_STARTED_CODE},
        )
    if run.finished_at is not None:
        raise DomainError(
            "Итоговый срез уже пройден.",
            status_code=409,
            payload={"code": FINAL_CHECK_FINISHED_CODE},
        )
    if task_id not in [int(t) for t in run.task_ids]:
        raise DomainError(
            "Этого задания нет в итоговом срезе.",
            status_code=404,
            payload={"code": FINAL_CHECK_NOT_IN_RUN_CODE},
        )
    answers = dict(run.answers or {})
    if str(task_id) in answers:
        raise DomainError(
            "На это задание в срезе уже есть ответ.",
            status_code=409,
            payload={"code": FINAL_CHECK_ANSWERED_CODE},
        )

    task = await TasksService().get_by_id(db, task_id)
    checking = CheckingService()
    supported = task is not None and task.is_active and task.course_id is not None
    if supported:
        # Те же гейты, что у обычного приёма ответа и у повтора: запись на курс
        # задания (tsk-272, по вызывающему), курс выведен из работы (tsk-886) и
        # закрыт зависимостью (tsk-1276) — по УЧЕНИКУ.
        if current_user is not None:
            await assert_task_access(db, current_user=current_user, task_course_id=task.course_id)
        await dependency_lock_service.assert_course_not_locked(
            db, student_id=student_id, course_id=task.course_id, task_id=task_id,
        )
        for cid in {task.course_id, course_id}:
            await course_activity_service.assert_course_active_for_student(
                db, student_id=student_id, course_id=cid, action="итоговый срез",
            )
        task_content = TaskContent.model_validate(task.task_content)
        solution_rules = checking.build_solution_rules(task.solution_rules, task.max_score)
        supported = retrieval_service.task_supports_review(task_content, solution_rules)
    if not supported or task is None:
        await _skip(db, run, task_id=task_id, student_id=student_id, course_id=course_id)

    if answer.type != task_content.type:
        raise DomainError(
            f"Тип ответа ({answer.type}) не совпадает с типом задачи ({task_content.type}).",
            status_code=400,
        )
    # Гейт 2.3f (tsk-419), как у повтора: SA_COM/TBL_COM без комментария не
    # принимается — отказ, а не незачёт, ответ в срезе не расходуется.
    if task_content.type in COMMENT_TASK_TYPES and not (answer.response.comment or "").strip():
        raise DomainError(
            "Добавьте комментарий: как вы получили ответ.",
            status_code=422,
            payload={"code": FINAL_CHECK_EVIDENCE_REQUIRED_CODE},
        )

    check_result: CheckResult = await asyncio.to_thread(
        checking.check_task,
        task_content=task_content,
        solution_rules=solution_rules,
        answer=answer,
    )
    if check_result.is_correct is None:
        await _skip(db, run, task_id=task_id, student_id=student_id, course_id=course_id)

    attempt = await retrieval_service._course_attempt(
        db,
        student_id=student_id,
        task_id=task_id,
        task_course_id=task.course_id,
        root_course_id=course_id,
        source_system=attempt_source_system,
    )
    task_result = await TaskResultsService().create_from_check_result(
        db=db,
        attempt_id=attempt.id,
        task_id=task_id,
        user_id=student_id,
        answer=answer,
        check_result=check_result,
        source_system=FINAL_CHECK_SOURCE,
        commit=False,
    )
    max_score = check_result.max_score or 0
    passed = max_score > 0 and (check_result.score or 0) / max_score >= PASS_THRESHOLD_RATIO
    task_result_id = int(task_result.id)
    answers[str(task_id)] = {"result_id": task_result_id, "is_correct": passed}
    run.answers = answers
    await _finish_if_complete(db, run, student_id=student_id, course_id=course_id)
    await db.commit()

    logger.info(
        "tsk-994: ответ среза student_id=%s course_id=%s task_id=%s correct=%s",
        student_id, course_id, task_id, passed,
    )
    return AnswerOutcome(
        task_id=task_id,
        task_result_id=task_result_id,
        check_result=check_result,
        is_correct=passed,
        run=run,
    )


async def _skip_deleted(db: AsyncSession, *, student_id: int, course_id: int) -> None:
    """Исключить из незаконченного среза задания, удалённые из базы.

    Удалённое задание ученик не увидит и не ответит на него, а без исключения
    итог не посчитался бы никогда. Зовётся при повторном открытии среза
    (клиент открывает его снова, когда неотвеченных не осталось) и перед
    приёмом ответа. Строка прогона — под замком; фиксация здесь же.
    """
    run = await get_run(db, student_id=student_id, course_id=course_id, lock=True)
    if run is None or run.finished_at is not None:
        return
    existing = set(await _task_courses(db, run.task_ids))
    answers = dict(run.answers or {})
    missing = [int(t) for t in run.task_ids if int(t) not in existing and str(t) not in answers]
    if not missing:
        return
    for tid in missing:
        answers[str(tid)] = {"skipped": True}
    run.answers = answers
    await _finish_if_complete(db, run, student_id=student_id, course_id=course_id)
    await db.commit()
    logger.warning(
        "tsk-994: удалённые задания %s исключены из среза run_id=%s", missing, run.id
    )


async def _skip(
    db: AsyncSession, run: FinalCheckRuns, *, task_id: int, student_id: int, course_id: int
) -> NoReturn:
    """Исключить из среза задание, которое больше не проверяется автоматически.

    Правила задания могли поменяться после выбора состава (включили ручную
    проверку, сняли эталон, задание выключили). Ответить на такое задание
    нельзя, а без исключения срез не закончился бы никогда. Отметка
    фиксируется, затем — отказ 409 `final_check_unsupported`; клиент
    перечитывает срез и идёт к следующему заданию.
    """
    answers = dict(run.answers or {})
    answers[str(task_id)] = {"skipped": True}
    run.answers = answers
    await _finish_if_complete(db, run, student_id=student_id, course_id=course_id)
    await db.commit()
    logger.warning(
        "tsk-994: задание %s исключено из среза run_id=%s — нет автопроверки",
        task_id, run.id,
    )
    raise DomainError(
        "Это задание больше не проверяется автоматически — оно исключено из среза.",
        status_code=409,
        payload={"code": FINAL_CHECK_UNSUPPORTED_CODE},
    )


async def serialize_run(db: AsyncSession, run: FinalCheckRuns) -> dict[str, Any]:
    """Прогон в форму ответа API: задания с главами, ответы, итог."""
    from app.utils.task_title import humanize_task_title

    task_ids = [int(t) for t in run.task_ids]
    rows = (
        await db.execute(
            text(
                """
                SELECT t.id, t.external_uid, t.course_id,
                       t.task_content->>'title' AS title,
                       t.task_content->>'stem' AS stem,
                       t.task_content->>'type' AS task_type
                FROM tasks t WHERE t.id = ANY(:ids)
                """
            ),
            {"ids": task_ids},
        )
    ).mappings().all()
    by_id = {int(r["id"]): r for r in rows}
    layout = await course_layout(db, run.course_id)
    answers = run.answers or {}
    items: list[dict[str, Any]] = []
    for tid in task_ids:
        r = by_id.get(tid)
        if r is None:
            continue
        ch = layout.chapter(r["course_id"])
        a = answers.get(str(tid))
        skipped = isinstance(a, dict) and bool(a.get("skipped"))
        items.append(
            {
                "task_id": tid,
                "external_uid": r["external_uid"],
                "title": humanize_task_title(tid, r["title"], r["stem"], r["external_uid"]),
                "task_type": r["task_type"],
                "chapter_id": ch.course_id,
                "chapter_title": ch.title,
                "answered": isinstance(a, dict),
                "is_correct": (
                    None if not isinstance(a, dict) or skipped else bool(a.get("is_correct"))
                ),
            }
        )
    return {
        "run_id": int(run.id),
        "course_id": int(run.course_id),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "items": items,
        "result": run.result,
    }
