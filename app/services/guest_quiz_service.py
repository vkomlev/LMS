"""Гостевой квиз-лид-магнит: прохождение без регистрации и подбор программы (tsk-053, фаза 1).

Опирается на то, что уже построено, а не строит движок заново:

* вопросы — обычные задания типов ``SC_Qw``/``MC_Qw`` (ADR-0003): баллы по шкалам
  вместо «верно/неверно»;
* подсчёт баллов — тот же ``CheckingService``, что и у авторизованного ученика;
* подбор программы — те же правила ``assignment_rule`` с ``trigger_event='quiz_scale'``.
  Ученику правило НАЗНАЧАЕТ курс, посетителю — показывает рекомендацию. Условие одно
  и то же, настроенное методистом в одном месте.

Отличия гостевого контура от ученического — ровно два, и оба намеренные:

1. **Ответ можно поменять.** У ученика квиз-вопрос жёстко ограничен одной попыткой
   (tsk-124), иначе задваивается накопление шкал. Здесь опрос, а не экзамен: человек
   вправе передумать на середине, и упереться в «ответ уже принят» он не должен.
   Задвоения нет, потому что накопление берёт ПОСЛЕДНИЙ ответ по каждому вопросу —
   так же, как ``_accumulate_course_scales`` у ученика.
2. **Демо-лимит заданий не применяется.** ``demo_task_limit`` бережёт платный контент
   от бесплатного решения; у квиза беречь нечего — верных ответов в нём нет вовсе, а
   лимит в 3-5 заданий просто не дал бы пройти опрос из шести вопросов до конца.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.courses import Courses
from app.models.guest_attempt import GuestAttempt
from app.models.lead import Lead, LeadSource
from app.models.tasks import Tasks
from app.schemas.checking import StudentAnswer, StudentResponse
from app.schemas.guest_quiz import (
    QuizAnswerResponse,
    QuizOption,
    QuizQuestion,
    QuizRecommendation,
    QuizResponse,
    QuizResultResponse,
)
from app.schemas.solution_rules import SolutionRules
from app.schemas.task_content import TaskContent
from app.services import lead_magnet_service
from app.services.assignment_rules_service import quiz_scale_matched
from app.services.checking_service import CheckingService
from app.services.learning_guest_service import is_task_visible_to_guest
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

_settings = Settings()
_checking_service = CheckingService()

#: Типы заданий, из которых состоит квиз. Остальные типы в курсе-квизе
#: игнорируются — иначе демо-задача случайно стала бы вопросом опроса.
QUIZ_QUESTION_TYPES: Tuple[str, ...] = ("SC_Qw", "MC_Qw")

#: Код канала привлечения для заявок с квиза (см. миграцию tsk053_guest_quiz_lead).
QUIZ_LEAD_SOURCE_CODE = "quiz"


async def _load_quiz_course(db: AsyncSession, course_uid: str) -> Optional[Courses]:
    """Найти курс-квиз среди публичных демо. ACL тот же, что у гостевых заданий."""
    result = await db.execute(
        select(Courses).where(
            Courses.course_uid == course_uid,
            Courses.is_public_demo.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def _load_questions(db: AsyncSession, course_id: int) -> List[Tuple[Tasks, TaskContent]]:
    """Активные квиз-вопросы курса по порядку.

    Порядок берётся из ``order_position``, при равенстве — по ``id``: у опроса
    вопросы связаны по смыслу («сколько лет» → «что ближе» → «как учимся»), и
    случайная перестановка сделала бы из него бессвязный набор.
    """
    result = await db.execute(
        select(Tasks)
        .where(Tasks.course_id == course_id)
        .order_by(Tasks.order_position.nulls_last(), Tasks.id)
    )
    questions: List[Tuple[Tasks, TaskContent]] = []
    for task in result.scalars():
        if not is_task_visible_to_guest(task, surface="quiz_read"):
            continue
        try:
            content = TaskContent.model_validate(task.task_content)
        except Exception:  # noqa: BLE001 — битую задачу пропускаем, квиз не роняем
            logger.warning("guest_quiz: некорректный task_content task_id=%s", task.id)
            continue
        if content.type not in QUIZ_QUESTION_TYPES:
            continue
        questions.append((task, content))
    return questions


async def _last_answers(
    db: AsyncSession, guest_session_id: Optional[UUID], task_ids: List[int]
) -> Dict[int, GuestAttempt]:
    """Последняя попытка гостя по каждому вопросу.

    Именно последняя, а не первая: ответ разрешено менять, и итог обязан считаться
    по тому, что человек оставил в конце.
    """
    if guest_session_id is None or not task_ids:
        return {}

    rows = (
        await db.execute(
            select(GuestAttempt)
            .where(
                GuestAttempt.guest_session_id == guest_session_id,
                GuestAttempt.task_id.in_(task_ids),
            )
            .order_by(GuestAttempt.task_id, GuestAttempt.id.desc())
            .distinct(GuestAttempt.task_id)
        )
    ).scalars()
    return {row.task_id: row for row in rows if row.task_id is not None}


def _selected_ids(attempt: Optional[GuestAttempt]) -> Optional[List[str]]:
    """Достать выбранные варианты из сохранённого ответа."""
    if attempt is None or not isinstance(attempt.answer_json, dict):
        return None
    response = attempt.answer_json.get("response")
    if not isinstance(response, dict):
        return None
    selected = response.get("selected_option_ids")
    return selected if isinstance(selected, list) else None


async def get_quiz(
    db: AsyncSession, course_uid: str, guest_session_id: Optional[UUID]
) -> Optional[QuizResponse]:
    """Отдать вопросы квиза и то, что гость уже успел ответить.

    :return: None — курса нет среди публичных демо либо в нём нет квиз-вопросов
        (для вызывающего это одинаковое «квиза нет»).
    """
    course = await _load_quiz_course(db, course_uid)
    if course is None:
        return None

    questions = await _load_questions(db, course.id)
    if not questions:
        logger.info("guest_quiz: курс %s публичный, но квиз-вопросов в нём нет", course_uid)
        return None

    answers = await _last_answers(db, guest_session_id, [t.id for t, _ in questions])

    items: List[QuizQuestion] = []
    for order, (task, content) in enumerate(questions, start=1):
        items.append(
            QuizQuestion(
                task_id=task.id,
                order=order,
                type=content.type,  # type: ignore[arg-type]
                stem=content.stem,
                options=[
                    QuizOption(id=opt.id, text=opt.text)
                    for opt in (content.options or [])
                    if opt.is_active
                ],
                selected_option_ids=_selected_ids(answers.get(task.id)),
            )
        )

    answered = sum(1 for i in items if i.selected_option_ids)
    return QuizResponse(
        course_uid=course.course_uid or course_uid,
        title=course.title,
        description=course.description,
        questions=items,
        answered_count=answered,
        total_count=len(items),
        is_complete=answered == len(items),
    )


async def submit_quiz_answer(
    db: AsyncSession,
    guest_session_id: UUID,
    task_id: int,
    selected_option_ids: List[str],
) -> QuizAnswerResponse:
    """Принять ответ на вопрос квиза и записать баллы по шкалам.

    Raises:
        DomainError 404: вопроса нет среди публичных квизов либо он снят с публикации.
        DomainError 400: структура задания повреждена или выбор не подходит типу
            вопроса (для ``SC_Qw`` — больше одного варианта).
    """
    row = (
        await db.execute(
            select(Tasks, Courses)
            .join(Courses, Tasks.course_id == Courses.id)
            .where(Tasks.id == task_id, Courses.is_public_demo.is_(True))
        )
    ).first()
    if row is None or not is_task_visible_to_guest(row[0], surface="quiz_submit"):
        raise DomainError(
            detail="Вопрос не найден среди публичных квизов.",
            status_code=404,
            payload={"task_id": task_id},
        )
    task, course = row

    try:
        content = TaskContent.model_validate(task.task_content)
        rules = SolutionRules.model_validate(task.solution_rules or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("guest_quiz: невалидное задание task_id=%s: %s", task_id, exc)
        raise DomainError(detail="Структура вопроса повреждена.", status_code=400) from exc

    if content.type not in QUIZ_QUESTION_TYPES:
        raise DomainError(
            detail="Это задание не является вопросом квиза.",
            status_code=400,
            payload={"task_type": content.type},
        )

    answer = StudentAnswer(
        type=content.type,  # type: ignore[arg-type]
        response=StudentResponse(selected_option_ids=selected_option_ids),
    )
    # to_thread — как в гостевых попытках: проверка синхронная, но блокировать
    # цикл событий на ней незачем.
    check_result = await asyncio.to_thread(
        _checking_service.check_task,
        task_content=content,
        solution_rules=rules,
        answer=answer,
    )

    attempt = GuestAttempt(
        guest_session_id=guest_session_id,
        task_id=task.id,
        answer_json=answer.model_dump(mode="json"),
        # is_correct у квиз-вопроса не определён (верного варианта нет) — пишем
        # NULL, а не False: False читалось бы как «ответил неправильно».
        is_correct=None,
        scale_scores=check_result.scale_scores,
    )
    db.add(attempt)
    await db.flush()

    questions = await _load_questions(db, course.id)
    answers = await _last_answers(db, guest_session_id, [t.id for t, _ in questions])
    answered = sum(1 for t, _ in questions if _selected_ids(answers.get(t.id)))

    return QuizAnswerResponse(
        task_id=task.id,
        answered_count=answered,
        total_count=len(questions),
        is_complete=answered == len(questions) and len(questions) > 0,
    )


async def _accumulate_guest_scales(
    db: AsyncSession, guest_session_id: Optional[UUID], task_ids: List[int]
) -> Dict[str, int]:
    """Сложить баллы по шкалам последних ответов гостя."""
    totals: Dict[str, int] = {}
    for attempt in (await _last_answers(db, guest_session_id, task_ids)).values():
        if not isinstance(attempt.scale_scores, dict):
            continue
        for scale, points in attempt.scale_scores.items():
            try:
                totals[scale] = totals.get(scale, 0) + int(points)
            except (TypeError, ValueError):
                continue
    return totals


async def _resolve_recommendation(
    db: AsyncSession, course_id: int, totals: Dict[str, int]
) -> Optional[QuizRecommendation]:
    """Подобрать программу по правилам ``quiz_scale`` этого квиза.

    Правила перебираются по ``id``: если методист настроил два совпадающих условия,
    выигрывает заведённое раньше — детерминированно, а не «как ляжет».
    """
    rules = (
        await db.execute(
            text(
                "SELECT id, condition, target_course_uid FROM assignment_rule "
                "WHERE is_active = true AND course_id = :cid "
                "  AND trigger_event = 'quiz_scale' ORDER BY id"
            ),
            {"cid": course_id},
        )
    ).fetchall()

    for rule_id, condition, target_uid in rules:
        if not isinstance(condition, dict) or not target_uid:
            continue
        if not quiz_scale_matched(condition, totals):
            continue

        target = (
            await db.execute(select(Courses).where(Courses.course_uid == target_uid))
        ).scalar_one_or_none()
        if target is None:
            # Правило указывает на курс, которого нет: показать посетителю
            # несуществующую программу хуже, чем не показать ничего.
            logger.warning(
                "guest_quiz: правило %s ведёт на неизвестный курс %s", rule_id, target_uid
            )
            continue
        return QuizRecommendation(
            course_uid=target_uid,
            title=target.title,
            description=target.description,
        )
    return None


def _contact_url(quiz_title: str, recommendation: Optional[QuizRecommendation]) -> str:
    """Ссылка на переписку с заранее заполненным сообщением.

    Сообщение пишется от лица человека, а не от лица системы: он отправляет его сам,
    и текст вроде «лид с квиза» в его собственном сообщении выглядел бы дико.
    """
    if recommendation is not None:
        message = (
            f"Здравствуйте! Прошёл квиз «{quiz_title}», мне подошла программа "
            f"«{recommendation.title}». Хочу записаться на осенний набор."
        )
    else:
        message = (
            f"Здравствуйте! Прошёл квиз «{quiz_title}», "
            "хочу подобрать программу на осенний набор."
        )
    return f"https://t.me/{_settings.quiz_contact_tg}?text={quote(message)}"


async def get_quiz_result(
    db: AsyncSession, course_uid: str, guest_session_id: Optional[UUID]
) -> Optional[QuizResultResponse]:
    """Итог квиза: накопленные шкалы, рекомендация и куда идти записываться."""
    course = await _load_quiz_course(db, course_uid)
    if course is None:
        return None

    questions = await _load_questions(db, course.id)
    if not questions:
        return None

    task_ids = [t.id for t, _ in questions]
    answers = await _last_answers(db, guest_session_id, task_ids)
    answered = sum(1 for tid in task_ids if _selected_ids(answers.get(tid)))
    is_complete = answered == len(task_ids)

    totals = await _accumulate_guest_scales(db, guest_session_id, task_ids)
    # Рекомендация — только по полностью пройденному квизу: на половине ответов
    # argmax покажет случайного лидера, и человек уйдёт с чужой программой.
    recommendation = (
        await _resolve_recommendation(db, course.id, totals) if is_complete else None
    )

    lead_submitted = False
    if guest_session_id is not None:
        lead_submitted = (
            await lead_magnet_service.find_lead(db, guest_session_id, course.id)
        ) is not None

    return QuizResultResponse(
        course_uid=course.course_uid or course_uid,
        title=course.title,
        is_complete=is_complete,
        answered_count=answered,
        total_count=len(task_ids),
        scales=totals,
        recommendation=recommendation,
        contact_url=_contact_url(course.title, recommendation),
        lead_submitted=lead_submitted,
    )


async def create_quiz_lead(
    db: AsyncSession,
    course_uid: str,
    guest_session_id: UUID,
    contact: str,
    full_name: Optional[str],
) -> Tuple[int, bool]:
    """Завести заявку по итогам квиза.

    Повторная отправка из той же сессии обновляет существующую заявку, а не заводит
    вторую: человек, поправивший опечатку в телефоне, не должен превращаться в двух
    разных людей в кабинете маркетолога.

    :return: (id заявки, была ли она уже до этого).
    :raises DomainError 404: квиза с таким ``course_uid`` нет среди публичных.
    """
    course = await _load_quiz_course(db, course_uid)
    if course is None:
        raise DomainError(
            detail="Квиз не найден среди публичных.",
            status_code=404,
            payload={"course_uid": course_uid},
        )

    questions = await _load_questions(db, course.id)
    totals = await _accumulate_guest_scales(db, guest_session_id, [t.id for t, _ in questions])
    recommendation = await _resolve_recommendation(db, course.id, totals)
    note = (
        f"Квиз «{course.title}». Рекомендация: "
        f"{recommendation.title if recommendation else 'не определилась'}. "
        f"Шкалы: {totals or '—'}."
    )
    return await lead_magnet_service.upsert_lead(
        db,
        course=course,
        guest_session_id=guest_session_id,
        contact=contact,
        full_name=full_name,
        note=note,
    )


async def get_quiz_funnel(db: AsyncSession) -> List[Dict[str, Any]]:
    """Воронка лид-магнитов. Считается в общем модуле: с появлением диагностики
    (фаза 2) она перестала быть «воронкой квизов» и стала общей для обоих."""
    return await lead_magnet_service.get_funnel(db)
