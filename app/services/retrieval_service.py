"""Повторение по следам ошибок (tsk-992, П3).

Решение оператора Р2: на повтор ставятся ТОЛЬКО задания «сначала завалил,
потом сдал». Интервалы 3 → 7 → 21 день; неверный повтор откатывает к 3 дням,
верный на последней стадии закрывает строку (`completed_at`).

Устройство:

- рубильник — `courses.retrieval_config = {"enabled": bool}` на КОРНЕВОМ курсе
  (по нему ученик идёт по программе); NULL и `enabled=false` — выключено;
- планировщик (`schedule_after_pass`) зовётся из приёма ответа
  (`POST /attempts/{id}/answers`) после записи результата — нестрого: ошибка
  уходит в лог и не роняет сдачу;
- выдача (`list_due`) и сдача повтора (`submit_review`) — эндпоинты
  `/learning/courses/{course_id}/review-*`.

Сдача повтора пишет обычную строку `task_results` с
`source_system='retrieval'` в существующую курсовую попытку. Чтобы повтор не
менял учебный путь, расчёты статуса задания, счёта попыток и «пройдено»
исключают это значение (`learning_gaps_service.learning_path_results_filter`),
а аналитика пробелов его не видит вовсе (белый список `REAL_STUDENT_SOURCES`).

Повтору подлежат только задания, которые сверяются автоматически целиком:
квиз (ответ один навсегда), развёрнутый ответ, обязательная ручная проверка,
обязательное вложение, песочница (черепаха, тесты ввода/вывода) и короткий
ответ без эталона — не подходят. Иначе неверный повтор уходил бы в очередь
преподавателя или зависал без вердикта.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attempts import Attempts
from app.models.courses import Courses
from app.models.task_review_schedule import TaskReviewSchedule
from app.schemas.checking import CheckResult, StudentAnswer
from app.schemas.solution_rules import SolutionRules
from app.schemas.task_content import (
    COMMENT_TASK_TYPES,
    QUIZ_TASK_TYPES,
    SHORT_ANSWER_TASK_TYPES,
    TaskContent,
)
from app.services.learning_engine_service import (
    PASS_THRESHOLD_RATIO,
    is_mentor_reviewed,
)
from app.services.learning_gaps_service import (
    MANUAL_TEACHER_SOURCE,
    RETRIEVAL_SOURCE,
)
from app.utils.exceptions import DomainError

if TYPE_CHECKING:
    from app.auth.current_user import CurrentUser

logger = logging.getLogger(__name__)

#: Интервал до следующего повтора по стадии: 0 → 3 дня, 1 → 7, 2 → 21 (Р2).
INTERVAL_DAYS: tuple[int, ...] = (3, 7, 21)
LAST_STAGE = len(INTERVAL_DAYS) - 1

#: Коды отказов сдачи повтора — клиент различает их по `payload.code`.
RETRIEVAL_DISABLED_CODE = "retrieval_disabled"
RETRIEVAL_NOT_SCHEDULED_CODE = "retrieval_not_scheduled"
RETRIEVAL_NOT_DUE_CODE = "retrieval_not_due"
RETRIEVAL_UNSUPPORTED_CODE = "retrieval_unsupported"
RETRIEVAL_EVIDENCE_REQUIRED_CODE = "evidence_required"


@dataclass(frozen=True)
class DueReview:
    """Задание, которое пора повторить."""

    task_id: int
    external_uid: Optional[str]
    title: str
    task_type: Optional[str]
    interval_stage: int
    due_at: datetime
    days_since_solved: Optional[int]


@dataclass(frozen=True)
class ReviewOutcome:
    """Итог сдачи повтора."""

    task_id: int
    task_result_id: int
    attempt_id: int
    check_result: CheckResult
    is_correct: bool
    interval_stage: int
    next_due_at: Optional[datetime]
    completed: bool


def _now() -> datetime:
    """Текущее время, всегда с часовым поясом (UTC)."""
    return datetime.now(timezone.utc)


def config_enabled(config: Any) -> bool:
    """Включено ли повторение по значению `courses.retrieval_config`.

    NULL, не-объект и объект без `enabled=true` — выключено: рубильник
    включается только явно.
    """
    return isinstance(config, dict) and config.get("enabled") is True


async def is_enabled(db: AsyncSession, course_id: Optional[int]) -> bool:
    """Включено ли повторение на курсе (рубильник `retrieval_config`)."""
    if course_id is None:
        return False
    config = (
        await db.execute(
            select(Courses.retrieval_config).where(Courses.id == course_id)
        )
    ).scalar_one_or_none()
    return config_enabled(config)


def task_supports_review(task_content: Any, solution_rules: SolutionRules) -> bool:
    """Подходит ли задание для повтора: проверяется автоматически целиком.

    Args:
        task_content: `tasks.task_content` (dict) либо `TaskContent`.
        solution_rules: правила проверки задания (см. `build_solution_rules`).

    Returns:
        False для квиза, развёрнутого ответа, обязательной ручной проверки,
        обязательного вложения, песочницы и короткого ответа без эталона.
    """
    task_type = (
        task_content.type
        if isinstance(task_content, TaskContent)
        else (task_content or {}).get("type")
    )
    if not task_type or task_type in QUIZ_TASK_TYPES or task_type == "TA":
        return False
    if is_mentor_reviewed(task_type, solution_rules.manual_review_required):
        return False
    if solution_rules.requires_attachment:
        return False
    if solution_rules.turtle_sim is not None or solution_rules.io_tests is not None:
        return False
    if task_type in SHORT_ANSWER_TASK_TYPES and not solution_rules.has_reference_answer():
        return False
    return True


async def schedule_after_pass(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    root_course_id: Optional[int],
    task_result_id: Optional[int],
) -> bool:
    """Поставить задание на повтор после сдачи «сначала завалил, потом сдал».

    Условия (все сразу):
      - корень известен и на нём включён рубильник;
      - результат `task_result_id` — сдача ученика (не ручной зачёт
        `manual_teacher` и не сам повтор), и он проходной;
      - раньше по паре (ученик, задание) был хотя бы один непроходной
        результат ученика в не отменённой попытке;
      - задание сверяется автоматически целиком (`task_supports_review`).

    Строка создаётся один раз (уникальность ученик+задание+корень): повторная
    пара «провал → сдача» существующее расписание не сбрасывает.

    Returns:
        True — создана новая строка расписания.
    """
    if root_course_id is None or task_result_id is None:
        return False
    if not await is_enabled(db, root_course_id):
        return False

    row = (
        await db.execute(
            text(
                """
                SELECT tr.score, tr.max_score, tr.source_system, tr.submitted_at,
                       t.task_content, t.solution_rules, t.max_score AS task_max_score
                FROM task_results tr
                JOIN tasks t ON t.id = tr.task_id
                WHERE tr.id = :rid AND tr.user_id = :sid AND tr.task_id = :tid
                """
            ),
            {"rid": task_result_id, "sid": student_id, "tid": task_id},
        )
    ).mappings().first()
    if row is None:
        return False
    if row["source_system"] in (MANUAL_TEACHER_SOURCE, RETRIEVAL_SOURCE):
        return False
    max_score = row["max_score"] or 0
    if max_score <= 0 or (row["score"] or 0) / max_score < PASS_THRESHOLD_RATIO:
        return False

    # Импорт здесь: checking_service тянет песочницы и тяжёлые схемы, а модуль
    # импортируется приёмом ответа на каждом старте приложения.
    from app.services.checking_service import CheckingService

    rules = CheckingService.build_solution_rules(row["solution_rules"], row["task_max_score"])
    if not task_supports_review(row["task_content"], rules):
        return False

    failed_before = (
        await db.execute(
            text(
                """
                SELECT 1
                FROM task_results tr
                JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
                WHERE tr.user_id = :sid AND tr.task_id = :tid
                  AND tr.id <> :rid
                  AND tr.submitted_at <= :submitted_at
                  AND tr.source_system IS DISTINCT FROM :manual
                  AND tr.source_system IS DISTINCT FROM :retrieval
                  AND tr.max_score > 0
                  AND (tr.score::float / tr.max_score) < :ratio
                LIMIT 1
                """
            ),
            {
                "sid": student_id,
                "tid": task_id,
                "rid": task_result_id,
                "submitted_at": row["submitted_at"],
                "manual": MANUAL_TEACHER_SOURCE,
                "retrieval": RETRIEVAL_SOURCE,
                "ratio": PASS_THRESHOLD_RATIO,
            },
        )
    ).first()
    if failed_before is None:
        return False

    due_at = _now() + timedelta(days=INTERVAL_DAYS[0])
    created = (
        await db.execute(
            text(
                """
                INSERT INTO task_review_schedule
                    (student_id, task_id, course_id, interval_stage, due_at, last_result_id)
                VALUES (:sid, :tid, :cid, 0, :due_at, :rid)
                ON CONFLICT ON CONSTRAINT uq_task_review_schedule_student_task_course
                DO NOTHING
                RETURNING id
                """
            ),
            {
                "sid": student_id,
                "tid": task_id,
                "cid": root_course_id,
                "due_at": due_at,
                "rid": task_result_id,
            },
        )
    ).first()
    if created is None:
        return False
    logger.info(
        "tsk-992: задание поставлено на повтор student_id=%s task_id=%s "
        "course_id=%s due_at=%s",
        student_id, task_id, root_course_id, due_at.isoformat(),
    )
    return True


async def list_due(
    db: AsyncSession,
    *,
    student_id: int,
    course_id: int,
) -> list[DueReview]:
    """Задания курса, которые пора повторить (`due_at <= now`, не закрытые).

    Пустой список, если рубильник курса выключен: выключение прячет блок
    сразу, расписание при этом сохраняется.
    """
    if not await is_enabled(db, course_id):
        return []
    rows = (
        await db.execute(
            text(
                """
                SELECT s.task_id, t.external_uid,
                       t.task_content, t.solution_rules, t.max_score,
                       t.task_content->>'title' AS title,
                       t.task_content->>'stem' AS stem,
                       t.task_content->>'type' AS task_type,
                       s.interval_stage, s.due_at,
                       tr.submitted_at AS solved_at
                FROM task_review_schedule s
                JOIN tasks t ON t.id = s.task_id AND t.is_active = true
                LEFT JOIN task_results tr ON tr.id = s.last_result_id
                WHERE s.student_id = :sid AND s.course_id = :cid
                  AND s.completed_at IS NULL
                  AND s.due_at <= now()
                ORDER BY s.due_at, s.task_id
                """
            ),
            {"sid": student_id, "cid": course_id},
        )
    ).mappings().all()
    from app.services.checking_service import CheckingService
    from app.utils.task_title import humanize_task_title

    now = _now()
    result: list[DueReview] = []
    for r in rows:
        # Правила задания могли поменяться после постановки на повтор (например,
        # включили ручную проверку). Такое задание не сдать (409
        # `retrieval_unsupported`) — и в выдаче ему не место, иначе блок
        # показывал бы его вечно.
        rules = CheckingService.build_solution_rules(r["solution_rules"], r["max_score"])
        if not task_supports_review(r["task_content"], rules):
            continue
        solved_at = r["solved_at"]
        days = None
        if isinstance(solved_at, datetime) and solved_at.tzinfo is not None:
            days = max((now - solved_at).days, 0)
        result.append(
            DueReview(
                task_id=int(r["task_id"]),
                external_uid=r["external_uid"],
                title=humanize_task_title(
                    int(r["task_id"]), r["title"], r["stem"], r["external_uid"]
                ),
                task_type=r["task_type"],
                interval_stage=int(r["interval_stage"]),
                due_at=r["due_at"],
                days_since_solved=days,
            )
        )
    return result


async def _course_attempt(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    task_course_id: int,
    root_course_id: int,
    source_system: str,
) -> Attempts:
    """Открытая курсовая попытка (ученик, курс задания, корень) — или новая.

    Та же механика, что у `start-or-get-attempt`: тот же ключ замка, тот же
    поиск открытой попытки, создание через `AttemptsService`. Своей попытки
    повтор не заводит — ответ ложится туда же, куда легли бы обычные ответы.

    Транзакцию НЕ фиксирует (только flush): иначе промежуточный commit снял бы
    замок строки расписания и advisory-замок до записи стадии, а при падении
    записи результата осталась бы пустая зафиксированная попытка.
    """
    from app.services.attempts_service import AttemptsService

    attempts_service = AttemptsService()
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, hashtext(:k2))"),
        {"k1": student_id, "k2": f"{task_course_id}:{root_course_id}"},
    )
    existing = (
        await db.execute(
            select(Attempts)
            .where(
                Attempts.user_id == student_id,
                Attempts.course_id == task_course_id,
                Attempts.root_course_id == root_course_id,
                Attempts.finished_at.is_(None),
                Attempts.cancelled_at.is_(None),
            )
            .order_by(Attempts.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return await attempts_service.ensure_attempt_task_ids(
            db, existing, task_id, commit=False
        )
    attempt = await attempts_service.repo.create(
        db,
        {
            "user_id": student_id,
            "course_id": task_course_id,
            "root_course_id": root_course_id,
            "source_system": source_system,
            "meta": {"task_ids": [task_id]},
        },
        commit=False,
    )
    return attempt


async def _due_schedule(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    course_id: int,
    lock: bool = False,
) -> TaskReviewSchedule:
    """Открытая строка расписания, у которой подошёл срок, — иначе отказ.

    Raises:
        DomainError: 404 `retrieval_not_scheduled` / 409 `retrieval_not_due`.
    """
    stmt = select(TaskReviewSchedule).where(
        TaskReviewSchedule.student_id == student_id,
        TaskReviewSchedule.task_id == task_id,
        TaskReviewSchedule.course_id == course_id,
        TaskReviewSchedule.completed_at.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    schedule = (await db.execute(stmt)).scalar_one_or_none()
    if schedule is None:
        raise DomainError(
            "Это задание сейчас не стоит на повторении.",
            status_code=404,
            payload={"code": RETRIEVAL_NOT_SCHEDULED_CODE},
        )
    if schedule.due_at > _now():
        raise DomainError(
            "Повторять это задание ещё рано.",
            status_code=409,
            payload={
                "code": RETRIEVAL_NOT_DUE_CODE,
                "due_at": schedule.due_at.isoformat(),
            },
        )
    return schedule


async def submit_review(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    course_id: int,
    answer: StudentAnswer,
    attempt_source_system: str = "spw_web",
    current_user: Optional["CurrentUser"] = None,
) -> ReviewOutcome:
    """Принять ответ на повтор, записать результат и сдвинуть расписание.

    Верно — следующая стадия (срок +7/+21 день) или закрытие после стадии 2.
    Неверно — стадия 0, срок +3 дня. Результат пишется в `task_results` с
    `source_system='retrieval'` и в статус задания / лимит попыток не входит.

    Args:
        db: сессия БД.
        student_id: ученик.
        task_id: задание.
        course_id: корневой курс, на котором стоит повтор.
        answer: ответ ученика.
        attempt_source_system: источник для НОВОЙ курсовой попытки, если
            открытой нет (сам результат всё равно помечается `retrieval`).
        current_user: вызывающий — для ACL записи на курс (tsk-272);
            None — проверка пропускается (внутренний вызов).

    Raises:
        DomainError: 409 `retrieval_disabled` / `retrieval_not_due` /
            `retrieval_unsupported`; 404 `retrieval_not_scheduled`; 400 —
            тип ответа не совпадает с типом задания; 422 `evidence_required` —
            SA_COM/TBL_COM без комментария. HTTPException 403 — не записан
            на курс задания.
    """
    from app.services.checking_service import CheckingService
    from app.services.task_results_service import TaskResultsService
    from app.services.tasks_service import TasksService

    if not await is_enabled(db, course_id):
        raise DomainError(
            "Повторение на этом курсе выключено.",
            status_code=409,
            payload={"code": RETRIEVAL_DISABLED_CODE},
        )

    # Строка расписания берётся под замком СРАЗУ и держится до единственной
    # фиксации в конце (попытка ниже заводится без commit): две одновременные
    # сдачи повтора сериализуются, вторая увидит уже сдвинутый срок и получит
    # 409 `retrieval_not_due`. Любой отказ ниже откатывает всё вместе с
    # попыткой — пустых зафиксированных попыток не остаётся.
    schedule = await _due_schedule(
        db, student_id=student_id, task_id=task_id, course_id=course_id, lock=True
    )

    task = await TasksService().get_by_id(db, task_id)
    if task is None or not task.is_active or task.course_id is None:
        raise DomainError(
            "Это задание сейчас не стоит на повторении.",
            status_code=404,
            payload={"code": RETRIEVAL_NOT_SCHEDULED_CODE},
        )

    # Те же гейты, что у обычного приёма ответа: запись на курс задания
    # (tsk-272, по вызывающему — как в `POST /attempts/{id}/answers`), курс
    # выведен из работы (tsk-886) и закрыт зависимостью (tsk-1276) — по УЧЕНИКУ.
    from app.services import course_activity_service, dependency_lock_service
    from app.services.tasks_acl_service import assert_task_access

    if current_user is not None:
        await assert_task_access(db, current_user=current_user, task_course_id=task.course_id)
    await dependency_lock_service.assert_course_not_locked(
        db, student_id=student_id, course_id=task.course_id, task_id=task_id,
    )
    for cid in {task.course_id, course_id}:
        await course_activity_service.assert_course_active_for_student(
            db, student_id=student_id, course_id=cid, action="повтор задания",
        )

    checking = CheckingService()
    task_content = TaskContent.model_validate(task.task_content)
    solution_rules = checking.build_solution_rules(task.solution_rules, task.max_score)
    if not task_supports_review(task_content, solution_rules):
        raise DomainError(
            "Это задание не повторяется автоматически.",
            status_code=409,
            payload={"code": RETRIEVAL_UNSUPPORTED_CODE},
        )
    if answer.type != task_content.type:
        raise DomainError(
            f"Тип ответа ({answer.type}) не совпадает с типом задачи ({task_content.type}).",
            status_code=400,
        )
    # Гейт 2.3f (tsk-419): у SA_COM/TBL_COM без комментария ответ не принимается.
    # Вложений у повтора нет (`requires_attachment` и песочница отсечены
    # `task_supports_review`), поэтому доказательство — только комментарий.
    # В отличие от обычной сдачи отказ, а не незачёт: пустой комментарий —
    # не «забыл решение», и откатывать стадию за него было бы неверно.
    if task_content.type in COMMENT_TASK_TYPES and not (answer.response.comment or "").strip():
        raise DomainError(
            "Добавьте комментарий: как вы получили ответ.",
            status_code=422,
            payload={"code": RETRIEVAL_EVIDENCE_REQUIRED_CODE},
        )

    check_result: CheckResult = await asyncio.to_thread(
        checking.check_task,
        task_content=task_content,
        solution_rules=solution_rules,
        answer=answer,
    )
    if check_result.is_correct is None:
        # Правила задания поменялись после постановки на повтор и вердикта
        # автоматики больше нет — без вердикта двигать расписание нечем.
        raise DomainError(
            "Это задание не повторяется автоматически.",
            status_code=409,
            payload={"code": RETRIEVAL_UNSUPPORTED_CODE},
        )

    attempt = await _course_attempt(
        db,
        student_id=student_id,
        task_id=task_id,
        task_course_id=task.course_id,
        root_course_id=course_id,
        source_system=attempt_source_system,
    )
    attempt_id = attempt.id
    now = _now()
    task_result = await TaskResultsService().create_from_check_result(
        db=db,
        attempt_id=attempt_id,
        task_id=task_id,
        user_id=student_id,
        answer=answer,
        check_result=check_result,
        source_system=RETRIEVAL_SOURCE,
        commit=False,
    )

    max_score = check_result.max_score or 0
    passed = max_score > 0 and (check_result.score or 0) / max_score >= PASS_THRESHOLD_RATIO
    completed = False
    next_due_at: Optional[datetime]
    if passed:
        if schedule.interval_stage >= LAST_STAGE:
            completed = True
            schedule.completed_at = now
            next_due_at = None
        else:
            schedule.interval_stage += 1
            next_due_at = now + timedelta(days=INTERVAL_DAYS[schedule.interval_stage])
            schedule.due_at = next_due_at
    else:
        schedule.interval_stage = 0
        next_due_at = now + timedelta(days=INTERVAL_DAYS[0])
        schedule.due_at = next_due_at
    schedule.last_result_id = task_result.id
    schedule.updated_at = now
    stage = schedule.interval_stage
    task_result_id = task_result.id
    await db.commit()

    logger.info(
        "tsk-992: повтор student_id=%s task_id=%s course_id=%s correct=%s "
        "stage=%s completed=%s",
        student_id, task_id, course_id, passed, stage, completed,
    )
    return ReviewOutcome(
        task_id=task_id,
        task_result_id=task_result_id,
        attempt_id=attempt_id,
        check_result=check_result,
        is_correct=passed,
        interval_stage=stage,
        next_due_at=next_due_at,
        completed=completed,
    )
