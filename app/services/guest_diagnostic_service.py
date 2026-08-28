"""Гостевая ЕГЭ-диагностика: короткая проверка тем без регистрации (tsk-053, фаза 2).

Отличие от квиза подбора (фаза 1) — в природе задач. Там вопросы о предпочтениях, где
верного ответа нет вовсе, и итог считается баллами по шкалам. Здесь у каждой задачи есть
эталон, проверка идёт тем же ``CheckingService``, что и у ученика, а итог — карта тем:
где справился, где нет и что подтянуть в первую очередь.

**Задачи — зонды, а не задания ЕГЭ.** В банке лежит 793 разобранных задания, и собрать
диагностику из них было первым побуждением. Но настоящее задание — это 3-5 минут даже
на уровне «легко»: восемь таких превращают «диагностику за 15 минут» в получасовую
контрольную. Зонд проверяет тот же навык за минуту («сколько единиц в двоичной записи
числа 2345» вместо полного задания 5). Побочная выгода оказалась важнее исходной:
платные курсы ЕГЭ остаются закрытыми — диагностика ничего из них наружу не открывает.

**Набор закреплён за гостевой сессией без единой строки в базе.** На каждую тему
заготовлено несколько вариантов, показывается один — выбранный по остатку от хеша
``(сессия, тема)``. Тот же человек, обновив страницу, увидит свои задачи; двое разных
получат разные. Хранить выбор не нужно, а значит нечему и рассинхронизироваться.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.courses import Courses
from app.models.guest_attempt import GuestAttempt
from app.models.tasks import Tasks
from app.schemas.checking import StudentAnswer, StudentResponse
from app.schemas.guest_diagnostic import (
    DiagnosticAnswerResponse,
    DiagnosticQuestion,
    DiagnosticResponse,
    DiagnosticResultResponse,
    DiagnosticTopicResult,
)
from app.schemas.solution_rules import SolutionRules
from app.schemas.task_content import TaskContent
from app.services import lead_magnet_service
from app.services.checking_service import CheckingService
from app.services.learning_guest_service import is_task_visible_to_guest
from app.utils.exceptions import DomainError
from urllib.parse import quote

logger = logging.getLogger(__name__)

_settings = Settings()
_checking_service = CheckingService()

#: Курс, куда ведём подготовиться целиком, если тем с провалами много.
EGE_COURSE_UID = "wp:ege-informatika"


def _topic_of(content: TaskContent) -> Optional[Dict[str, str]]:
    """Тема зонда. Лежит в самой задаче (`TaskContent` разрешает свои поля)."""
    topic = getattr(content, "diagnostic_topic", None)
    if isinstance(topic, dict) and topic.get("code"):
        return topic
    return None


def _pick_variant(guest_session_id: Optional[UUID], topic_code: str, count: int) -> int:
    """Какой вариант зонда показать этому посетителю.

    Хеш вместо случайного числа: выбор обязан быть одинаковым при каждом запросе одной
    и той же сессии, иначе человек, обновивший страницу, увидит другие задачи, а уже
    данные ответы повиснут в воздухе. Без сессии (первое открытие до создания cookie)
    показываем первый вариант — набор всё равно пересоберётся, когда сессия появится.
    """
    if guest_session_id is None or count <= 1:
        return 0
    digest = hashlib.sha256(f"{guest_session_id}:{topic_code}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % count


async def _load_course(db: AsyncSession, course_uid: str) -> Optional[Courses]:
    """Курс диагностики среди публичных демо. ACL тот же, что у гостевых заданий."""
    return (
        await db.execute(
            select(Courses).where(
                Courses.course_uid == course_uid,
                Courses.is_public_demo.is_(True),
            )
        )
    ).scalar_one_or_none()


async def _load_probes(
    db: AsyncSession, course_id: int
) -> Dict[str, List[Tuple[Tasks, TaskContent]]]:
    """Зонды курса, сгруппированные по темам, в порядке `order_position`."""
    result = await db.execute(
        select(Tasks)
        .where(Tasks.course_id == course_id)
        .order_by(Tasks.order_position.nulls_last(), Tasks.id)
    )
    by_topic: Dict[str, List[Tuple[Tasks, TaskContent]]] = {}
    for task in result.scalars():
        if not is_task_visible_to_guest(task, surface="diagnostic_read"):
            continue
        try:
            content = TaskContent.model_validate(task.task_content)
        except Exception:  # noqa: BLE001 — битый зонд пропускаем, диагностику не роняем
            logger.warning("diagnostic: некорректный task_content task_id=%s", task.id)
            continue
        topic = _topic_of(content)
        if topic is None:
            continue
        by_topic.setdefault(topic["code"], []).append((task, content))
    return by_topic


def _selected_probes(
    by_topic: Dict[str, List[Tuple[Tasks, TaskContent]]],
    guest_session_id: Optional[UUID],
) -> List[Tuple[Tasks, TaskContent, Dict[str, str]]]:
    """По одному зонду на тему, в порядке появления тем."""
    chosen: List[Tuple[Tasks, TaskContent, Dict[str, str]]] = []
    for topic_code, variants in by_topic.items():
        index = _pick_variant(guest_session_id, topic_code, len(variants))
        task, content = variants[index]
        topic = _topic_of(content) or {"code": topic_code, "title": topic_code}
        chosen.append((task, content, topic))
    return chosen


async def _answers_for(
    db: AsyncSession, guest_session_id: Optional[UUID], task_ids: List[int]
) -> Dict[int, GuestAttempt]:
    """Последняя попытка гостя по каждой из показанных задач."""
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


def _answer_value(attempt: Optional[GuestAttempt]) -> Optional[str]:
    """Что человек ответил, из сохранённого ответа."""
    if attempt is None or not isinstance(attempt.answer_json, dict):
        return None
    response = attempt.answer_json.get("response")
    if not isinstance(response, dict):
        return None
    value = response.get("value")
    return value if isinstance(value, str) else None


async def get_diagnostic(
    db: AsyncSession, course_uid: str, guest_session_id: Optional[UUID]
) -> Optional[DiagnosticResponse]:
    """Отдать набор задач этого посетителя и то, что он уже ответил."""
    course = await _load_course(db, course_uid)
    if course is None:
        return None

    by_topic = await _load_probes(db, course.id)
    if not by_topic:
        logger.info("diagnostic: курс %s публичный, но зондов в нём нет", course_uid)
        return None

    probes = _selected_probes(by_topic, guest_session_id)
    answers = await _answers_for(db, guest_session_id, [t.id for t, _, _ in probes])

    questions: List[DiagnosticQuestion] = []
    for order, (task, content, topic) in enumerate(probes, start=1):
        attempt = answers.get(task.id)
        questions.append(
            DiagnosticQuestion(
                task_id=task.id,
                order=order,
                topic_code=topic["code"],
                topic_title=topic.get("title", topic["code"]),
                stem=content.stem,
                answered=attempt is not None,
                answer_value=_answer_value(attempt),
            )
        )

    answered = sum(1 for q in questions if q.answered)
    return DiagnosticResponse(
        course_uid=course.course_uid or course_uid,
        title=course.title,
        description=course.description,
        questions=questions,
        answered_count=answered,
        total_count=len(questions),
        is_complete=answered == len(questions),
    )


async def submit_answer(
    db: AsyncSession, guest_session_id: UUID, task_id: int, value: str
) -> DiagnosticAnswerResponse:
    """Принять ответ на зонд.

    Правильность наружу не возвращается: человек не должен подбирать ответ по отклику,
    иначе диагностика перестанет что-либо измерять. Проверка выполняется сразу и
    сохраняется — разбор человек увидит один раз, в итоге.

    Raises:
        DomainError 404: задачи нет среди публичных зондов либо она снята с публикации.
        DomainError 400: задача не является зондом диагностики или её структура повреждена.
    """
    row = (
        await db.execute(
            select(Tasks, Courses)
            .join(Courses, Tasks.course_id == Courses.id)
            .where(Tasks.id == task_id, Courses.is_public_demo.is_(True))
        )
    ).first()
    if row is None or not is_task_visible_to_guest(row[0], surface="diagnostic_submit"):
        raise DomainError(
            detail="Задача не найдена среди публичных.",
            status_code=404,
            payload={"task_id": task_id},
        )
    task, course = row

    try:
        content = TaskContent.model_validate(task.task_content)
        rules = SolutionRules.model_validate(task.solution_rules or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("diagnostic: невалидная задача task_id=%s: %s", task_id, exc)
        raise DomainError(detail="Структура задачи повреждена.", status_code=400) from exc

    if _topic_of(content) is None:
        raise DomainError(
            detail="Эта задача не входит в диагностику.",
            status_code=400,
            payload={"task_id": task_id},
        )

    # Ответ принимается только на задачу ИЗ НАБОРА этого посетителя. Без проверки
    # можно было бы отвечать на чужие варианты той же темы: сам человек их не
    # увидит (итог считает по своему набору), но воронка засчитывает тему по
    # любому ответу в ней — и «прошёл до конца» наступало бы от чужих задач.
    own_probes = _selected_probes(await _load_probes(db, course.id), guest_session_id)
    if task.id not in {t.id for t, _, _ in own_probes}:
        raise DomainError(
            detail="Эта задача не входит в ваш набор диагностики.",
            status_code=400,
            payload={"task_id": task_id},
        )

    answer = StudentAnswer(
        type=content.type,  # type: ignore[arg-type]
        response=StudentResponse(value=value),
    )
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
        is_correct=check_result.is_correct,
    )
    db.add(attempt)
    await db.flush()

    answers = await _answers_for(db, guest_session_id, [t.id for t, _, _ in own_probes])
    answered = sum(1 for t, _, _ in own_probes if t.id in answers)

    return DiagnosticAnswerResponse(
        task_id=task.id,
        answered_count=answered,
        total_count=len(own_probes),
        is_complete=answered == len(own_probes) and len(own_probes) > 0,
    )


def _reference_answer(rules: SolutionRules) -> Optional[str]:
    """Верный ответ для показа в разборе. Отдаётся только в итоге, не при приёме."""
    short = rules.short_answer
    if short is None or not short.accepted_answers:
        return None
    top = max(short.accepted_answers, key=lambda a: a.score or 0)
    return top.value


def _contact_url(solved: int, total: int, weak: List[DiagnosticTopicResult]) -> str:
    """Ссылка на переписку с заранее заполненным сообщением от лица человека."""
    if weak:
        themes = ", ".join(t.topic_title.split(".")[0] for t in weak[:3])
        message = (
            f"Здравствуйте! Прошёл диагностику по информатике: {solved} из {total}. "
            f"Просели темы: {themes}. Хочу подготовиться к ЕГЭ."
        )
    else:
        message = (
            f"Здравствуйте! Прошёл диагностику по информатике: {solved} из {total}. "
            "Хочу готовиться к ЕГЭ дальше."
        )
    return f"https://t.me/{_settings.quiz_contact_tg}?text={quote(message)}"


async def get_result(
    db: AsyncSession, course_uid: str, guest_session_id: Optional[UUID]
) -> Optional[DiagnosticResultResponse]:
    """Итог: сколько решено, разбор по темам и что подтянуть."""
    course = await _load_course(db, course_uid)
    if course is None:
        return None

    by_topic = await _load_probes(db, course.id)
    if not by_topic:
        return None

    probes = _selected_probes(by_topic, guest_session_id)
    answers = await _answers_for(db, guest_session_id, [t.id for t, _, _ in probes])

    topics: List[DiagnosticTopicResult] = []
    solved = 0
    for task, content, topic in probes:
        attempt = answers.get(task.id)
        correct = bool(attempt and attempt.is_correct)
        if correct:
            solved += 1
        # Верный ответ показываем только по задаче, на которую человек ОТВЕТИЛ.
        # Иначе итог открывается сразу, без решения, и выдаёт готовые ответы —
        # диагностика перестаёт что-либо измерять, а зонды разлетаются по чатам.
        reference = None
        if attempt is not None:
            try:
                rules = SolutionRules.model_validate(task.solution_rules or {})
                reference = _reference_answer(rules)
            except Exception:  # noqa: BLE001
                reference = None
        topics.append(
            DiagnosticTopicResult(
                topic_code=topic["code"],
                topic_title=topic.get("title", topic["code"]),
                is_correct=correct,
                your_answer=_answer_value(attempt),
                correct_answer=reference,
                course_uid=topic.get("course_uid"),
            )
        )

    answered = sum(1 for t, _, _ in probes if t.id in answers)
    is_complete = answered == len(probes)
    # Слабые темы показываем только по пройденной до конца диагностике: на середине
    # «просело задание 14» означало бы всего лишь «до него ещё не дошли».
    weak = [t for t in topics if not t.is_correct] if is_complete else []

    ege_course = (
        await db.execute(select(Courses).where(Courses.course_uid == EGE_COURSE_UID))
    ).scalar_one_or_none()

    lead_submitted = False
    if guest_session_id is not None:
        lead_submitted = (
            await lead_magnet_service.find_lead(db, guest_session_id, course.id)
        ) is not None

    return DiagnosticResultResponse(
        course_uid=course.course_uid or course_uid,
        title=course.title,
        is_complete=is_complete,
        solved=solved,
        total=len(probes),
        topics=topics,
        weak_topics=weak,
        recommendation_course_uid=ege_course.course_uid if ege_course else None,
        recommendation_title=ege_course.title if ege_course else None,
        contact_url=_contact_url(solved, len(probes), weak),
        lead_submitted=lead_submitted,
    )


async def create_lead(
    db: AsyncSession,
    course_uid: str,
    guest_session_id: UUID,
    contact: str,
    full_name: Optional[str],
) -> Tuple[int, bool]:
    """Завести заявку по итогам диагностики.

    :raises DomainError 404: диагностики с таким `course_uid` нет среди публичных.
    """
    course = await _load_course(db, course_uid)
    if course is None:
        raise DomainError(
            detail="Диагностика не найдена.",
            status_code=404,
            payload={"course_uid": course_uid},
        )

    result = await get_result(db, course_uid, guest_session_id)
    if result is None:
        raise DomainError(detail="Диагностика не найдена.", status_code=404)

    weak = ", ".join(t.topic_title for t in result.weak_topics) or "нет"
    note = (
        f"Диагностика «{course.title}». Решено {result.solved} из {result.total}. "
        f"Просели темы: {weak}."
    )
    return await lead_magnet_service.upsert_lead(
        db,
        course=course,
        guest_session_id=guest_session_id,
        contact=contact,
        full_name=full_name,
        note=note,
    )
