"""
Сервис назначения курсов и движок оценки правил (tsk-031).

Содержит:
- ``assign_course_to_student`` — идемпотентное ядро назначения курса
  (используется и ручным эндпоинтом учителя, и автоматическим движком);
- ``evaluate_rules_for_answer`` — оценка правил после ответа на задачу
  (``answer_value`` / ``task_failed``);
- ``evaluate_rules_for_attempt`` — оценка правил после завершения попытки
  (``course_failed``).

Идемпотентность: зачисление защищено PK ``user_courses(user_id, course_id)``,
повторное срабатывание правил — журналом ``assignment_event`` и advisory-lock'ом.
Движок по умолчанию выполняет no-op: пока таблица ``assignment_rule`` пуста,
ни один хук не меняет поведение системы.

Модель данных — docs/ai/adr/0002-course-assignment-trigger-rules.md.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import course_dependencies_enrollment_service
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

# Порог по умолчанию для course_failed: доля верных задач, ниже/равно которой
# тема считается не освоенной.
DEFAULT_MIN_CORRECT_RATIO = 0.5


@dataclass
class AssignResult:
    """Результат назначения курса ученику."""

    course_id: int
    already_enrolled: bool
    event_id: Optional[int]


async def _resolve_course_id(
    db: AsyncSession,
    *,
    course_id: Optional[int],
    course_uid: Optional[str],
) -> int:
    """
    Привести ссылку на курс к числовому ``courses.id``.

    :raises DomainError: курс не найден (404).
    """
    if course_id is not None:
        r = await db.execute(
            text("SELECT id FROM courses WHERE id = :cid"),
            {"cid": course_id},
        )
        row = r.fetchone()
        if row is None:
            raise DomainError(detail=f"Курс id={course_id} не найден", status_code=404)
        return int(row[0])

    if course_uid:
        r = await db.execute(
            text("SELECT id FROM courses WHERE course_uid = :uid"),
            {"uid": course_uid},
        )
        row = r.fetchone()
        if row is None:
            raise DomainError(
                detail=f"Курс с course_uid='{course_uid}' не найден",
                status_code=404,
            )
        return int(row[0])

    raise DomainError(detail="Не указан course_id или course_uid", status_code=400)


async def assign_course_to_student(
    db: AsyncSession,
    *,
    student_id: int,
    course_id: Optional[int] = None,
    course_uid: Optional[str] = None,
    source: str,
    rule_id: Optional[int] = None,
    assigned_by: Optional[int] = None,
    attempt_id: Optional[int] = None,
    task_result_id: Optional[int] = None,
    detail: Optional[dict[str, Any]] = None,
    skip_event_if_enrolled: bool = False,
) -> AssignResult:
    """
    Идемпотентно назначить курс ученику и записать событие в журнал.

    Зачисление пишется в ``user_courses`` (``order_number`` ставит триггер БД).
    Повторный вызов для уже привязанного курса не создаёт дубль и возвращает
    ``already_enrolled=True``.

    :param source: ``auto_rule`` (движок) или ``manual_teacher`` (учитель).
    :param skip_event_if_enrolled: если True и ученик уже на курсе — не писать
        событие (для ручных кликов, чтобы не засорять журнал). Для авто-правил
        оставлять False: событие нужно как запись о срабатывании.
    :raises DomainError: курс не найден.
    """
    resolved_course_id = await _resolve_course_id(
        db, course_id=course_id, course_uid=course_uid
    )

    # Сериализация check-then-insert по (student_id, course_id) от гонок.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": student_id, "k2": resolved_course_id},
    )

    existing = await db.execute(
        text(
            "SELECT 1 FROM user_courses "
            "WHERE user_id = :uid AND course_id = :cid"
        ),
        {"uid": student_id, "cid": resolved_course_id},
    )
    already_enrolled = existing.fetchone() is not None

    if not already_enrolled:
        # order_number проставит триггер trg_set_user_course_order_number.
        await db.execute(
            text(
                "INSERT INTO user_courses (user_id, course_id) "
                "VALUES (:uid, :cid) "
                "ON CONFLICT (user_id, course_id) DO NOTHING"
            ),
            {"uid": student_id, "cid": resolved_course_id},
        )

    # tsk-261 (A2): курс может требовать прохождения другого курса. Если
    # зависимость ученику не назначена, пройти её нельзя — и замок на этом курсе
    # не снимется никогда. Доназначаем в той же транзакции.
    #
    # ВНЕ ветки `if not already_enrolled` намеренно: живой кейс приёмки — курс
    # 823 ученику УЖЕ назначен, а зависимость 682 нет. Внутри ветки повторное
    # назначение уходило бы в already_enrolled и ничего не чинило.
    await course_dependencies_enrollment_service.ensure_dependencies_assigned(
        db, student_id=student_id, course_ids=[resolved_course_id]
    )

    event_id: Optional[int] = None
    if not (already_enrolled and skip_event_if_enrolled):
        r = await db.execute(
            text(
                "INSERT INTO assignment_event "
                "(student_id, assigned_course_id, rule_id, source, assigned_by, "
                " attempt_id, task_result_id, already_enrolled, detail) "
                "VALUES (:student_id, :course_id, :rule_id, :source, :assigned_by, "
                " :attempt_id, :task_result_id, :already_enrolled, CAST(:detail AS jsonb)) "
                "RETURNING id"
            ),
            {
                "student_id": student_id,
                "course_id": resolved_course_id,
                "rule_id": rule_id,
                "source": source,
                "assigned_by": assigned_by,
                "attempt_id": attempt_id,
                "task_result_id": task_result_id,
                "already_enrolled": already_enrolled,
                "detail": json.dumps(detail) if detail is not None else None,
            },
        )
        event_id = int(r.scalar())

    await db.commit()
    return AssignResult(
        course_id=resolved_course_id,
        already_enrolled=already_enrolled,
        event_id=event_id,
    )


async def _rule_already_fired(
    db: AsyncSession, *, rule_id: int, student_id: int
) -> bool:
    """True, если правило уже срабатывало для ученика (для once_per_student)."""
    r = await db.execute(
        text(
            "SELECT 1 FROM assignment_event "
            "WHERE rule_id = :rid AND student_id = :sid LIMIT 1"
        ),
        {"rid": rule_id, "sid": student_id},
    )
    return r.fetchone() is not None


def _match_answer_value(condition: dict[str, Any], answer: Any) -> bool:
    """
    Совпал ли ответ ученика с условием правила ``answer_value``.

    condition: ``{"option_id": "..."}`` (SC/MC) или ``{"value": "..."}`` (SA).
    """
    response = getattr(answer, "response", None)
    if response is None:
        return False

    option_id = condition.get("option_id")
    if option_id is not None:
        selected = getattr(response, "selected_option_ids", None) or []
        return option_id in selected

    expected = condition.get("value")
    if expected is not None:
        actual = getattr(response, "value", None)
        if actual is None:
            return False
        return str(actual).strip().lower() == str(expected).strip().lower()

    return False


async def evaluate_rules_for_answer(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    answer: Any,
    check_result: Any,
    attempt_id: Optional[int] = None,
    task_result_id: Optional[int] = None,
) -> int:
    """
    Оценить правила, привязанные к задаче, после ответа ученика.

    Обрабатывает ``answer_value`` (совпадение ответа) и ``task_failed``
    (``check_result.is_correct`` ложно). Каждое правило обрабатывается
    с soft-fail: сбой одного не мешает остальным и не ломает учебный поток.

    :return: число фактически сработавших (назначивших курс) правил.
    """
    rows = (
        await db.execute(
            text(
                "SELECT id, trigger_event, condition, target_course_uid, refire_policy "
                "FROM assignment_rule "
                "WHERE is_active = true AND task_id = :task_id "
                "AND trigger_event IN ('answer_value', 'task_failed')"
            ),
            {"task_id": task_id},
        )
    ).fetchall()

    fired = 0
    for row in rows:
        rule_id, trigger_event, condition, target_uid, refire_policy = row
        condition = condition or {}
        try:
            if refire_policy == "once_per_student" and await _rule_already_fired(
                db, rule_id=rule_id, student_id=student_id
            ):
                continue

            if trigger_event == "answer_value":
                matched = _match_answer_value(condition, answer)
                detail = {"matched": "answer_value", "condition": condition}
            elif trigger_event == "task_failed":
                matched = getattr(check_result, "is_correct", None) is False
                detail = {"matched": "task_failed", "task_id": task_id}
            else:
                continue

            if not matched:
                continue

            await assign_course_to_student(
                db,
                student_id=student_id,
                course_uid=target_uid,
                source="auto_rule",
                rule_id=rule_id,
                attempt_id=attempt_id,
                task_result_id=task_result_id,
                detail=detail,
            )
            fired += 1
        except Exception:  # soft-fail: одно правило не валит остальные
            logger.warning(
                "assignment rule %s (task_id=%s, student_id=%s) failed",
                rule_id, task_id, student_id, exc_info=True,
            )
            await db.rollback()
    return fired


async def evaluate_rules_for_attempt(
    db: AsyncSession,
    *,
    student_id: int,
    attempt_id: int,
) -> int:
    """
    Оценить правила ``course_failed`` и ``quiz_scale`` после завершения попытки.

    Тема = курс попытки (``attempts.course_id``).

    ``course_failed``: считается доля верных задач попытки; при значении ниже/равном
    ``condition.min_correct_ratio`` (по умолчанию 0.5) назначается курс повторения.

    ``quiz_scale`` (tsk-122): по всем квиз-задачам курса у ученика суммируются
    ``scale_scores`` (накопление по курсу, ADR-0003), затем интерпретируются:
    ``{"scale": S, "mode": "argmax"}`` — шкала S строго максимальна; либо
    ``{"scale": S, "min_score": N}`` — накопленный балл S не ниже порога N.

    Soft-fail на уровне каждого правила.

    :return: число сработавших правил.
    """
    arow = (
        await db.execute(
            text("SELECT course_id FROM attempts WHERE id = :aid"),
            {"aid": attempt_id},
        )
    ).fetchone()
    if arow is None or arow[0] is None:
        return 0
    course_id = int(arow[0])

    fired = 0
    fired += await _evaluate_course_failed(
        db, student_id=student_id, attempt_id=attempt_id, course_id=course_id
    )
    fired += await _evaluate_quiz_scale(
        db, student_id=student_id, attempt_id=attempt_id, course_id=course_id
    )
    return fired


async def _evaluate_course_failed(
    db: AsyncSession,
    *,
    student_id: int,
    attempt_id: int,
    course_id: int,
) -> int:
    """Оценка правил ``course_failed`` по доле верных задач попытки."""
    rules = (
        await db.execute(
            text(
                "SELECT id, condition, target_course_uid, refire_policy "
                "FROM assignment_rule "
                "WHERE is_active = true AND course_id = :course_id "
                "AND trigger_event = 'course_failed'"
            ),
            {"course_id": course_id},
        )
    ).fetchall()
    if not rules:
        return 0

    # Доля верных задач в рамках попытки (учитываются только авто-проверяемые,
    # где is_correct не NULL).
    stats = (
        await db.execute(
            text(
                "SELECT "
                "  COUNT(*) FILTER (WHERE is_correct IS NOT NULL) AS checked, "
                "  COUNT(*) FILTER (WHERE is_correct = true) AS correct "
                "FROM task_results WHERE attempt_id = :aid"
            ),
            {"aid": attempt_id},
        )
    ).fetchone()
    checked = int(stats[0] or 0)
    correct = int(stats[1] or 0)
    if checked == 0:
        return 0
    correct_ratio = correct / checked

    fired = 0
    for rule_id, condition, target_uid, refire_policy in rules:
        condition = condition or {}
        try:
            if refire_policy == "once_per_student" and await _rule_already_fired(
                db, rule_id=rule_id, student_id=student_id
            ):
                continue

            threshold = condition.get("min_correct_ratio", DEFAULT_MIN_CORRECT_RATIO)
            if correct_ratio > threshold:
                continue

            await assign_course_to_student(
                db,
                student_id=student_id,
                course_uid=target_uid,
                source="auto_rule",
                rule_id=rule_id,
                attempt_id=attempt_id,
                detail={
                    "matched": "course_failed",
                    "course_id": course_id,
                    "correct_ratio": round(correct_ratio, 3),
                    "threshold": threshold,
                },
            )
            fired += 1
        except Exception:  # soft-fail
            logger.warning(
                "course_failed rule %s (course_id=%s, student_id=%s) failed",
                rule_id, course_id, student_id, exc_info=True,
            )
            await db.rollback()
    return fired


async def _accumulate_course_scales(
    db: AsyncSession, *, student_id: int, course_id: int
) -> dict[str, int]:
    """
    Суммировать ``scale_scores`` по всем квиз-задачам курса у ученика (накопление по курсу).

    Берётся последний результат по каждой задаче курса (по ``submitted_at``), чтобы
    повторные попытки не задваивали баллы. Возвращает словарь ``{scale: points}``.
    """
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT ON (tr.task_id) tr.scale_scores "
                "FROM task_results tr "
                "JOIN tasks t ON t.id = tr.task_id "
                "WHERE tr.user_id = :uid AND t.course_id = :cid "
                "  AND tr.scale_scores IS NOT NULL "
                "ORDER BY tr.task_id, tr.submitted_at DESC, tr.id DESC"
            ),
            {"uid": student_id, "cid": course_id},
        )
    ).fetchall()

    totals: dict[str, int] = {}
    for (scale_scores,) in rows:
        if not isinstance(scale_scores, dict):
            continue
        for scale, points in scale_scores.items():
            try:
                totals[scale] = totals.get(scale, 0) + int(points)
            except (TypeError, ValueError):
                continue
    return totals


def _quiz_scale_matched(condition: dict[str, Any], totals: dict[str, int]) -> bool:
    """
    Совпало ли правило ``quiz_scale`` с накопленными шкалами.

    ``min_score`` имеет приоритет: ``totals[scale] >= min_score``. Иначе режим
    ``argmax``: шкала строго максимальна (уникальный победитель, балл > 0). При
    отсутствии накопленных шкал — не срабатывает.
    """
    scale = condition.get("scale")
    if not scale or scale not in totals:
        return False

    if "min_score" in condition:
        try:
            return totals[scale] >= int(condition["min_score"])
        except (TypeError, ValueError):
            return False

    # mode argmax (по умолчанию): строгий уникальный максимум, балл > 0.
    target = totals[scale]
    if target <= 0:
        return False
    return all(target > other for s, other in totals.items() if s != scale)


async def _evaluate_quiz_scale(
    db: AsyncSession,
    *,
    student_id: int,
    attempt_id: int,
    course_id: int,
) -> int:
    """Оценка правил ``quiz_scale`` по накопленным шкалам курса (tsk-122)."""
    rules = (
        await db.execute(
            text(
                "SELECT id, condition, target_course_uid, refire_policy "
                "FROM assignment_rule "
                "WHERE is_active = true AND course_id = :course_id "
                "AND trigger_event = 'quiz_scale'"
            ),
            {"course_id": course_id},
        )
    ).fetchall()
    if not rules:
        return 0

    totals = await _accumulate_course_scales(
        db, student_id=student_id, course_id=course_id
    )
    if not totals:
        return 0

    fired = 0
    for rule_id, condition, target_uid, refire_policy in rules:
        condition = condition or {}
        try:
            if refire_policy == "once_per_student" and await _rule_already_fired(
                db, rule_id=rule_id, student_id=student_id
            ):
                continue

            if not _quiz_scale_matched(condition, totals):
                continue

            await assign_course_to_student(
                db,
                student_id=student_id,
                course_uid=target_uid,
                source="auto_rule",
                rule_id=rule_id,
                attempt_id=attempt_id,
                detail={
                    "matched": "quiz_scale",
                    "course_id": course_id,
                    "scale": condition.get("scale"),
                    "totals": totals,
                },
            )
            fired += 1
        except Exception:  # soft-fail
            logger.warning(
                "quiz_scale rule %s (course_id=%s, student_id=%s) failed",
                rule_id, course_id, student_id, exc_info=True,
            )
            await db.rollback()
    return fired


async def bulk_upsert_rules(
    db: AsyncSession, items: list[Any]
) -> list[dict[str, Any]]:
    """Идемпотентный upsert правил назначения по ``code`` (для публикатора, tsk-120).

    Резолвит ``task_external_uid`` → ``task_id`` и ``course_uid`` → ``course_id``
    (отслеживаемые сущности). ``target_course_uid`` хранится как есть (резолвится
    движком в момент срабатывания). Устойчиво по элементам: ошибка одного правила
    не валит весь batch. ``action_type`` берётся из server_default (assign_course).
    """
    results: list[dict[str, Any]] = []
    for it in items:
        try:
            task_id = it.task_id
            if task_id is None and it.task_external_uid:
                row = (await db.execute(
                    text("SELECT id FROM tasks WHERE external_uid = :u"),
                    {"u": it.task_external_uid},
                )).first()
                if row is None:
                    results.append({"code": it.code, "id": None, "action": "error",
                                    "error": f"задача не найдена: {it.task_external_uid}"})
                    continue
                task_id = int(row[0])

            course_id = it.course_id
            if course_id is None and it.course_uid:
                course_id = await _resolve_course_id(db, course_uid=it.course_uid)

            row = (await db.execute(text("""
                INSERT INTO assignment_rule
                  (code, title, trigger_event, task_id, course_id, condition,
                   target_course_uid, refire_policy, is_active)
                VALUES
                  (:code, :title, :te, :tid, :cid, CAST(:cond AS jsonb),
                   :target, :refire, :active)
                ON CONFLICT (code) DO UPDATE SET
                  title = EXCLUDED.title,
                  trigger_event = EXCLUDED.trigger_event,
                  task_id = EXCLUDED.task_id,
                  course_id = EXCLUDED.course_id,
                  condition = EXCLUDED.condition,
                  target_course_uid = EXCLUDED.target_course_uid,
                  refire_policy = EXCLUDED.refire_policy,
                  is_active = EXCLUDED.is_active,
                  updated_at = now()
                RETURNING id, (xmax = 0) AS created
            """), {
                "code": it.code, "title": it.title, "te": it.trigger_event,
                "tid": task_id, "cid": course_id,
                "cond": json.dumps(it.condition or {}),
                "target": it.target_course_uid, "refire": it.refire_policy,
                "active": it.is_active,
            })).first()
            results.append({"code": it.code, "id": int(row[0]),
                            "action": "created" if row[1] else "updated"})
        except DomainError as e:
            results.append({"code": it.code, "id": None, "action": "error", "error": str(e)})
    await db.commit()
    return results
