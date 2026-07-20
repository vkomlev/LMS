"""Штатная правка прогресса ученика преподавателем (tsk-297).

Не миграция из внешнего источника, а функция продукта: преподаватель/методист в
любой момент отмечает задание или материал как пройденные, чтобы ученик,
пришедший с наработками, продолжал со своего места, а не с нуля.

Модель данных (решение зафиксировано в
`docs/specs/2026-07-20-tech-spec-tsk297-manual-progress.md`, обосновано разведкой
прода):

* **Задание** — синтетическая ПОПЫТКА + результат, а не «результат без попытки».
  Движок (`compute_task_state`) склеивает результат с попыткой через
  `INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL`,
  поэтому результат без попытки он просто не увидел бы. Попытка пишется с
  ``root_course_id = NULL`` — «путь неизвестен», такая попытка не расходует лимит
  ни в одном корне (документированная семантика tsk-264), то есть зачёт НЕ съедает
  попытку ученика. ``score = max_score`` даёт ratio 1.0 ≥ ``PASS_THRESHOLD_RATIO``
  → задание становится ``PASSED`` и next-item его больше не выдаёт.
  ``checked_at``/``checked_by`` заполняются, иначе зачтённое задание ручного типа
  (SA_COM/TA) упало бы в очередь проверки преподавателя.
* **Материал** — строка `student_material_progress` со `source='manual_teacher'`
  (колонка добавлена миграцией `tsk297_manual_progress_source`).

Обратимость:

* задание — синтетическая попытка помечается ``cancelled_at``/``cancel_reason``,
  строки НЕ удаляются (история правок сохраняется), движок отсекает её тем же
  ``a.cancelled_at IS NULL``. Задание возвращается к состоянию, которое дают его
  РЕАЛЬНЫЕ попытки: ``OPEN``, если ученик его не решал, иначе то, что было до
  зачёта (``IN_PROGRESS`` / ``FAILED`` / ``BLOCKED_LIMIT``);
* материал — строка удаляется, но ТОЛЬКО при ``source='manual_teacher'``:
  реальное прохождение ученика не трогаем.

`student_task_progress` (пропуск задания, tsk-111) не затрагивается вовсе: зачёт
и пропуск — разные сущности.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.current_user import CurrentUser
from app.services import audit_service
from app.services.checking_service import CheckingService
from app.services.learning_engine_service import LearningEngineService
from app.services.teacher_queue_service import teacher_course_acl
from app.utils.task_title import humanize_task_title

logger = logging.getLogger(__name__)

#: Значение провенанса для всего, что поставлено преподавателем вручную.
MANUAL_SOURCE = "manual_teacher"
#: Причина аннулирования синтетической попытки при снятии зачёта.
REVOKE_REASON = "manual_progress_revoked"
#: Провенанс реального прохождения ученика (дефолт колонки `source`).
SYSTEM_SOURCE = "system"
#: Фильтр обхода — тот же, что у учебного движка (см. `compute_course_state`).
_REQUIREMENT_LEVELS = ("required", "skippable")
#: Максимальная длина комментария преподавателя (обрезается, а не отклоняется).
_COMMENT_MAX_LEN = 500

_engine = LearningEngineService()
_checking_service = CheckingService()

_ELEVATED_ROLES = frozenset({"admin", "methodist"})
_TEACHER_ROLES = frozenset({"teacher"})


# ─── ACL ────────────────────────────────────────────────────────────────────


async def can_edit_progress(
    db: AsyncSession,
    current_user: CurrentUser,
    student_id: int,
    course_id: Optional[int] = None,
) -> bool:
    """Может ли пользователь править прогресс данного ученика (без исключения).

    Иерархия (тот же принцип, что у `teacher_can_override_limit`):

    * сервисный токен — полный доступ (bypass);
    * роль ``admin`` / ``methodist`` — полный доступ (любой ученик);
    * роль ``teacher`` — ученик закреплён за ним (``student_teacher_links``)
      ИЛИ курс попадает под его ACL (``teacher_course_acl``, рекурсия вверх по
      ``course_parents``);
    * иначе — нет.

    :param db: async-сессия.
    :param current_user: текущий пользователь (или сервисный токен).
    :param student_id: ID ученика, чей прогресс правится.
    :param course_id: курс правимого элемента; None — проверка только по связке
        «ученик закреплён за преподавателем».
    :returns: True — доступ есть.
    """
    if current_user.is_service:
        return True

    from app.services import roles_service  # noqa: PLC0415 — избегаем цикла импортов

    roles = {r.lower().strip() for r in await roles_service.get_user_role_names(db, current_user.id)}
    if roles & _ELEVATED_ROLES:
        return True

    if not (roles & _TEACHER_ROLES):
        return False

    linked = (
        await db.execute(
            text(
                "SELECT 1 FROM student_teacher_links "
                "WHERE student_id = :student_id AND teacher_id = :teacher_id"
            ),
            {"student_id": student_id, "teacher_id": current_user.id},
        )
    ).fetchone()
    if linked is not None:
        return True

    if course_id is not None:
        allowed = (
            await db.execute(
                text(
                    f"SELECT {teacher_course_acl(':target_course_id')}"  # nosec B608
                ),
                {"target_course_id": course_id, "teacher_id": current_user.id},
            )
        ).scalar()
        # Одного ACL на курс мало: он говорит «этот курс мой», но ничего не
        # говорит про ученика. Без второй проверки преподаватель курса X мог бы
        # править прогресс ЛЮБОГО user_id (включая другого преподавателя),
        # просто перебирая идентификаторы. Требуем, чтобы ученик был реально
        # записан на корень дерева, в которое входит этот узел.
        if allowed:
            roots = await _engine.list_active_roots_of_node(db, student_id, course_id)
            if roots:
                return True

    return False


async def ensure_can_edit_progress(
    db: AsyncSession,
    current_user: CurrentUser,
    student_id: int,
    course_id: Optional[int] = None,
) -> None:
    """То же, что `can_edit_progress`, но при отказе бросает 403 с русским detail.

    :raises HTTPException: 403, если прав нет.
    """
    if await can_edit_progress(db, current_user, student_id, course_id):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail=(
            "Править прогресс ученика может преподаватель — только своих учеников "
            "или учеников на закреплённых за ним курсах; полный доступ у методиста и админа"
        ),
    )


async def list_accessible_student_courses(
    db: AsyncSession,
    current_user: CurrentUser,
    student_id: int,
) -> list[dict[str, Any]]:
    """Корневые курсы ученика, доступные текущему преподавателю.

    Питает селектор курсов в карточке ученика на портале: иначе фронт был бы
    вынужден звать `GET /users/{id}/courses`, объявленный только с
    APIKeyQuery-безопасностью, — лишняя точка отказа под cookie-сессией.

    Источник — активные записи ``user_courses`` (то, на что ученик записан),
    отфильтрованные тем же ACL, что и правка прогресса.

    :returns: список ``{"course_id": int, "title": str}`` в порядке
        ``user_courses.order_number``.
    """
    rows = (
        await db.execute(
            text(
                "SELECT c.id AS course_id, c.title "
                "FROM user_courses uc "
                "JOIN courses c ON c.id = uc.course_id "
                "WHERE uc.user_id = :student_id AND uc.is_active = true "
                "ORDER BY uc.order_number ASC NULLS LAST, c.id"
            ),
            {"student_id": student_id},
        )
    ).mappings().fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        cid = int(row["course_id"])
        if await can_edit_progress(db, current_user, student_id, cid):
            result.append({"course_id": cid, "title": row["title"]})
    return result


# ─── Вспомогательные запросы ────────────────────────────────────────────────


async def _load_task(db: AsyncSession, task_id: int) -> dict[str, Any]:
    """Курс, max_score и правила проверки задания. 404, если задания нет."""
    row = (
        await db.execute(
            text(
                "SELECT id, course_id, max_score, solution_rules, external_uid, "
                "       task_content->>'title' AS tc_title, task_content->>'stem' AS tc_stem "
                "FROM tasks WHERE id = :task_id"
            ),
            {"task_id": task_id},
        )
    ).mappings().fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Задание {task_id} не найдено")
    return dict(row)


async def _load_material(db: AsyncSession, material_id: int) -> dict[str, Any]:
    """Курс и заголовок материала. 404, если материала нет."""
    row = (
        await db.execute(
            text("SELECT id, course_id, title FROM materials WHERE id = :material_id"),
            {"material_id": material_id},
        )
    ).mappings().fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Материал {material_id} не найден")
    return dict(row)


def _resolve_max_score(task: dict[str, Any]) -> int:
    """Максимальный балл задания — ровно тем же путём, что и реальный приём ответа.

    Приём ответа (`POST /attempts/{id}/answers`) строит правило через
    ``CheckingService.build_solution_rules(task.solution_rules, task.max_score)``
    и берёт ``max_score`` оттуда. Повторяем этот же вызов, чтобы синтетический
    результат нельзя было отличить по шкале от честно решённого:

    * есть ``solution_rules`` → ``solution_rules.max_score`` (обязательное поле схемы);
    * правило пустое (JSON null у 1116 импортированных заданий, tsk-325) →
      ``tasks.max_score``;
    * и он пуст/нулевой → ``1`` (дефолт `build_solution_rules`; ratio 1/1 = 1.0
      всё равно даёт PASSED).

    Нижняя граница ``1`` ставится жёстко: в схеме `SolutionRules.max_score` нет
    ``ge=1``, и при нуле/отрицательном значении результат ушёл бы со шкалой 0 —
    движок считает PASSED через ``last_max > 0``, то есть зачёт вернул бы
    ``granted: true``, а задание так и не стало бы пройденным.
    """
    rules = _checking_service.build_solution_rules(
        task.get("solution_rules"), task.get("max_score")
    )
    return max(1, int(rules.max_score))


async def _lock(db: AsyncSession, key1: int, key2: int) -> None:
    """Сериализовать операцию по паре ключей на время транзакции."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"), {"k1": key1, "k2": key2}
    )


async def _refresh_course_state(db: AsyncSession, student_id: int, course_id: int) -> None:
    """Пересчитать `student_course_state` по корням, в чьё дерево входит узел.

    Без этого массовый зачёт давал расхождение: `me_service.get_courses_with_progress`
    берёт ``is_completed`` из `student_course_state`, а не считает на лету, — ученик
    видел 100% выполненных элементов при незавершённом курсе (и наоборот после
    массового снятия), пока сам не дёргал движок.

    Вызывается ТОЛЬКО на операциях записи и ОДИН раз на операцию (в массовой — в
    конце, а не на каждый элемент). В чтении (`get_student_progress`)
    `compute_course_state` по-прежнему не зовётся: при COMPLETED он дёргает Y-6
    эскалацию (уведомление методисту), а просмотр карточки рассылать ничего не
    должен. На записи эскалация уместна — курс действительно сменил состояние.

    :param course_id: затронутый узел (курс задания/материала либо корень массовой
        операции).
    """
    roots = await _engine.list_active_roots_of_node(db, student_id, course_id)
    for root_id in roots:
        await _engine.compute_course_state(
            db, student_id, root_id, update_state_table=True
        )


def _clip_comment(comment: Optional[str]) -> Optional[str]:
    """Обрезать комментарий преподавателя до лимита; пустой → None."""
    if comment is None:
        return None
    trimmed = comment.strip()
    if not trimmed:
        return None
    return trimmed[:_COMMENT_MAX_LEN]


async def _subtree_course_ids(db: AsyncSession, course_id: int) -> list[int]:
    """Курсы дерева узла (сам узел + все потомки) в порядке обхода движка."""
    tree_ids = await _engine._collect_courses_in_order(db, course_id)  # noqa: SLF001
    return tree_ids or [course_id]


async def _tree_task_rows(db: AsyncSession, course_ids: list[int]) -> list[dict[str, Any]]:
    """Активные задания дерева с тем же фильтром обязательности, что у движка."""
    rows = (
        await db.execute(
            text(
                "SELECT id, course_id, external_uid, "
                "       task_content->>'title' AS tc_title, task_content->>'stem' AS tc_stem "
                "FROM tasks "
                "WHERE course_id = ANY(:course_ids) AND is_active = true "
                "  AND requirement_level = ANY(:levels) "
                "ORDER BY course_id, order_position ASC NULLS LAST, id"
            ),
            {"course_ids": course_ids, "levels": list(_REQUIREMENT_LEVELS)},
        )
    ).mappings().fetchall()
    return [dict(r) for r in rows]


async def _tree_material_rows(db: AsyncSession, course_ids: list[int]) -> list[dict[str, Any]]:
    """Активные материалы дерева с тем же фильтром обязательности, что у движка."""
    rows = (
        await db.execute(
            text(
                "SELECT id, course_id, title FROM materials "
                "WHERE course_id = ANY(:course_ids) AND is_active = true "
                "  AND requirement_level = ANY(:levels) "
                "ORDER BY course_id, order_position ASC NULLS LAST, id"
            ),
            {"course_ids": course_ids, "levels": list(_REQUIREMENT_LEVELS)},
        )
    ).mappings().fetchall()
    return [dict(r) for r in rows]


# ─── Задания ────────────────────────────────────────────────────────────────


async def grant_task(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    granted_by: Optional[int],
    comment: Optional[str] = None,
    _standalone: bool = True,
) -> dict[str, Any]:
    """Идемпотентно зачесть задание ученику.

    Создаёт синтетическую попытку (``root_course_id=NULL`` — лимит ученика не
    расходуется) и результат с ``score = max_score``, из-за чего движок начинает
    считать задание ``PASSED``.

    :param db: async-сессия (коммит — на вызывающей стороне).
    :param student_id: ID ученика.
    :param task_id: ID задания.
    :param granted_by: ID преподавателя, поставившего зачёт.
    :param comment: причина/пояснение, попадает в ``task_results.metrics``.
    :param _standalone: True — операция самостоятельная: пишем событие аудита и
        пересчитываем `student_course_state`. False — элемент массовой операции:
        и аудит, и пересчёт состояния делаются один раз на всю пачку.
    :returns: словарь с ``granted``/``already``/``attempt_id``.
    """
    await _lock(db, student_id, task_id)

    task = await _load_task(db, task_id)

    state = await _engine.compute_task_state(db, student_id, task_id)
    if state.state == "PASSED":
        logger.info(
            "tsk-297: задание %s у ученика %s уже PASSED — зачёт не нужен",
            task_id, student_id,
        )
        return {
            "student_id": student_id,
            "item_type": "task",
            "item_id": task_id,
            "granted": True,
            "already": True,
            "source": MANUAL_SOURCE,
            "attempt_id": None,
        }

    max_score = _resolve_max_score(task)
    clean_comment = _clip_comment(comment)

    # Попытка вставляется сырым INSERT'ом, а НЕ через `AttemptsService.create_attempt`:
    # тот идёт в `BaseRepository.create` с `commit=True` и коммитит прямо посреди
    # операции. Это рвало транзакционный `pg_advisory_xact_lock` (он держится до
    # конца транзакции: коммит после вставки попытки, но до вставки результата —
    # и два параллельных POST создавали две попытки), а в массовой операции
    # фиксировало часть дерева до записи аудита. Коммитит только роутер.
    attempt_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO attempts "
                    "  (user_id, course_id, root_course_id, source_system, meta) "
                    # NULL намеренно: «путь неизвестен» → попытка не расходует лимит
                    # ни в одном корне (tsk-264). Зачёт не съедает попытку ученика.
                    "VALUES (:user_id, :course_id, NULL, :source_system, CAST(:meta AS jsonb)) "
                    "RETURNING id"
                ),
                {
                    "user_id": student_id,
                    "course_id": task["course_id"],
                    "source_system": MANUAL_SOURCE,
                    "meta": json.dumps(
                        {"granted_by": granted_by, "task_ids": [task_id], "manual_grant": True},
                        ensure_ascii=False,
                    ),
                },
            )
        ).scalar()
    )

    await db.execute(
        text(
            "INSERT INTO task_results "
            "  (user_id, task_id, attempt_id, score, max_score, is_correct, "
            "   submitted_at, received_at, count_retry, checked_at, checked_by, "
            "   source_system, metrics) "
            "VALUES "
            "  (:user_id, :task_id, :attempt_id, :score, :max_score, true, "
            "   now(), now(), 0, now(), :checked_by, :source_system, "
            "   CAST(:metrics AS jsonb))"
        ),
        {
            "user_id": student_id,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "score": max_score,
            "max_score": max_score,
            # checked_at/checked_by обязательны: без них зачтённое SA_COM/TA
            # попало бы в очередь ручной проверки (предикат `checked_at IS NULL`).
            "checked_by": granted_by,
            "source_system": MANUAL_SOURCE,
            "metrics": json.dumps(
                {"manual_grant": True, "comment": clean_comment}, ensure_ascii=False
            ),
        },
    )

    if _standalone:
        await audit_service.log_event(
            db,
            audit_service.TEACHER_PROGRESS_GRANTED,
            user_id=granted_by,
            details={
                "student_id": student_id,
                "item_type": "task",
                "item_id": task_id,
                "course_id": task["course_id"],
                "bulk": False,
                "affected": 1,
                "comment": clean_comment,
            },
        )
        await _refresh_course_state(db, student_id, int(task["course_id"]))

    logger.info(
        "tsk-297: зачёт задания %s ученику %s преподавателем %s (attempt=%s, score=%s)",
        task_id, student_id, granted_by, attempt_id, max_score,
    )
    return {
        "student_id": student_id,
        "item_type": "task",
        "item_id": task_id,
        "granted": True,
        "already": False,
        "source": MANUAL_SOURCE,
        "attempt_id": attempt_id,
    }


async def revoke_task(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    revoked_by: Optional[int],
    _standalone: bool = True,
) -> dict[str, Any]:
    """Снять ручной зачёт задания.

    Синтетические попытки помечаются ``cancelled_at``/``cancel_reason``; движок
    отсекает их тем же ``a.cancelled_at IS NULL``. Строки не удаляются — история
    правок сохраняется. Реальные попытки ученика
    (``source_system <> 'manual_teacher'``) не трогаются, поэтому задание
    возвращается НЕ обязательно в ``OPEN``, а в то состояние, которое дают эти
    реальные попытки: ``OPEN`` — если ученик задание не решал, иначе
    ``IN_PROGRESS`` / ``FAILED`` / ``BLOCKED_LIMIT``.

    :returns: словарь с ``granted=False`` и ``already=True``, если зачёта не было
        (идемпотентность, а не ошибка).
    """
    await _lock(db, student_id, task_id)

    result = await db.execute(
        text(
            "UPDATE attempts a "
            "SET cancelled_at = now(), cancel_reason = :reason "
            "WHERE a.id IN ( "
            "    SELECT DISTINCT tr.attempt_id FROM task_results tr "
            "    JOIN attempts src ON src.id = tr.attempt_id "
            "    WHERE tr.user_id = :student_id AND tr.task_id = :task_id "
            "      AND src.source_system = :source "
            "      AND src.cancelled_at IS NULL "
            ")"
        ),
        {
            "reason": REVOKE_REASON,
            "student_id": student_id,
            "task_id": task_id,
            "source": MANUAL_SOURCE,
        },
    )
    affected = result.rowcount or 0

    if affected and _standalone:
        task = await _load_task(db, task_id)
        await audit_service.log_event(
            db,
            audit_service.TEACHER_PROGRESS_REVOKED,
            user_id=revoked_by,
            details={
                "student_id": student_id,
                "item_type": "task",
                "item_id": task_id,
                "course_id": task["course_id"],
                "bulk": False,
                "affected": affected,
                "comment": None,
            },
        )
        await _refresh_course_state(db, student_id, int(task["course_id"]))

    logger.info(
        "tsk-297: снятие зачёта задания %s у ученика %s преподавателем %s (попыток=%s)",
        task_id, student_id, revoked_by, affected,
    )
    return {
        "student_id": student_id,
        "item_type": "task",
        "item_id": task_id,
        "granted": False,
        "already": affected == 0,
        "source": MANUAL_SOURCE,
        "attempt_id": None,
    }


# ─── Материалы ──────────────────────────────────────────────────────────────


async def grant_material(
    db: AsyncSession,
    *,
    student_id: int,
    material_id: int,
    granted_by: Optional[int],
    comment: Optional[str] = None,
    _standalone: bool = True,
) -> dict[str, Any]:
    """Идемпотентно отметить материал пройденным от лица преподавателя.

    Если материал УЖЕ отмечен пройденным (в том числе самим учеником) — ничего не
    меняем и не перетираем провенанс: возвращаем ``already=True``.
    """
    await _lock(db, student_id, material_id)

    material = await _load_material(db, material_id)

    current = (
        await db.execute(
            text(
                "SELECT status, source FROM student_material_progress "
                "WHERE student_id = :student_id AND material_id = :material_id"
            ),
            {"student_id": student_id, "material_id": material_id},
        )
    ).fetchone()
    if current is not None and current[0] == "completed":
        return {
            "student_id": student_id,
            "item_type": "material",
            "item_id": material_id,
            "granted": True,
            "already": True,
            "source": current[1],
        }

    await db.execute(
        text(
            "INSERT INTO student_material_progress "
            "  (student_id, material_id, status, completed_at, skipped_at, source) "
            "VALUES (:student_id, :material_id, 'completed', now(), NULL, :source) "
            "ON CONFLICT (student_id, material_id) DO UPDATE SET "
            "  status = 'completed', completed_at = now(), skipped_at = NULL, "
            "  source = EXCLUDED.source"
        ),
        {"student_id": student_id, "material_id": material_id, "source": MANUAL_SOURCE},
    )

    if _standalone:
        await audit_service.log_event(
            db,
            audit_service.TEACHER_PROGRESS_GRANTED,
            user_id=granted_by,
            details={
                "student_id": student_id,
                "item_type": "material",
                "item_id": material_id,
                "course_id": material["course_id"],
                "bulk": False,
                "affected": 1,
                "comment": _clip_comment(comment),
            },
        )
        await _refresh_course_state(db, student_id, int(material["course_id"]))

    logger.info(
        "tsk-297: отметка материала %s ученику %s преподавателем %s",
        material_id, student_id, granted_by,
    )
    return {
        "student_id": student_id,
        "item_type": "material",
        "item_id": material_id,
        "granted": True,
        "already": False,
        "source": MANUAL_SOURCE,
    }


async def revoke_material(
    db: AsyncSession,
    *,
    student_id: int,
    material_id: int,
    revoked_by: Optional[int],
    _standalone: bool = True,
) -> dict[str, Any]:
    """Снять ручную отметку материала.

    Удаляет строку ТОЛЬКО при ``source='manual_teacher'``: если ученик прошёл
    материал сам (``source='system'``), его прогресс не трогаем и возвращаем
    ``already=True``.
    """
    await _lock(db, student_id, material_id)

    result = await db.execute(
        text(
            "DELETE FROM student_material_progress "
            "WHERE student_id = :student_id AND material_id = :material_id "
            "  AND source = :source"
        ),
        {"student_id": student_id, "material_id": material_id, "source": MANUAL_SOURCE},
    )
    affected = result.rowcount or 0

    if affected and _standalone:
        material = await _load_material(db, material_id)
        await audit_service.log_event(
            db,
            audit_service.TEACHER_PROGRESS_REVOKED,
            user_id=revoked_by,
            details={
                "student_id": student_id,
                "item_type": "material",
                "item_id": material_id,
                "course_id": material["course_id"],
                "bulk": False,
                "affected": affected,
                "comment": None,
            },
        )
        await _refresh_course_state(db, student_id, int(material["course_id"]))

    logger.info(
        "tsk-297: снятие отметки материала %s у ученика %s преподавателем %s (строк=%s)",
        material_id, student_id, revoked_by, affected,
    )
    return {
        "student_id": student_id,
        "item_type": "material",
        "item_id": material_id,
        "granted": False,
        "already": affected == 0,
        "source": MANUAL_SOURCE,
    }


# ─── Массовые операции по дереву узла ───────────────────────────────────────


async def grant_course_subtree(
    db: AsyncSession,
    *,
    student_id: int,
    course_id: int,
    granted_by: Optional[int],
    comment: Optional[str] = None,
) -> dict[str, Any]:
    """Массово зачесть всё дерево узла (сам узел + потомки по ``course_parents``).

    Обходятся только активные элементы с ``requirement_level IN
    ('required','skippable')`` — тот же фильтр, что у движка, иначе счётчики
    разъехались бы с представлением «курс пройден».

    :returns: счётчики ``tasks_affected`` / ``materials_affected`` /
        ``skipped_already`` (элементы, которые уже были пройдены).
    """
    tree_ids = await _subtree_course_ids(db, course_id)
    tasks = await _tree_task_rows(db, tree_ids)
    materials = await _tree_material_rows(db, tree_ids)

    tasks_affected = 0
    materials_affected = 0
    skipped_already = 0

    for task in tasks:
        res = await grant_task(
            db,
            student_id=student_id,
            task_id=int(task["id"]),
            granted_by=granted_by,
            comment=comment,
            _standalone=False,
        )
        if res["already"]:
            skipped_already += 1
        else:
            tasks_affected += 1

    for material in materials:
        res = await grant_material(
            db,
            student_id=student_id,
            material_id=int(material["id"]),
            granted_by=granted_by,
            comment=comment,
            _standalone=False,
        )
        if res["already"]:
            skipped_already += 1
        else:
            materials_affected += 1

    await audit_service.log_event(
        db,
        audit_service.TEACHER_PROGRESS_GRANTED,
        user_id=granted_by,
        details={
            "student_id": student_id,
            "item_type": "course",
            "item_id": course_id,
            "course_id": course_id,
            "bulk": True,
            "affected": tasks_affected + materials_affected,
            "tasks_affected": tasks_affected,
            "materials_affected": materials_affected,
            "skipped_already": skipped_already,
            "comment": _clip_comment(comment),
        },
    )

    # Один раз на всю пачку, а не на каждый элемент.
    await _refresh_course_state(db, student_id, course_id)

    logger.info(
        "tsk-297: массовый зачёт дерева %s ученику %s: заданий=%s, материалов=%s, уже было=%s",
        course_id, student_id, tasks_affected, materials_affected, skipped_already,
    )
    return {
        "student_id": student_id,
        "course_id": course_id,
        "tasks_affected": tasks_affected,
        "materials_affected": materials_affected,
        "skipped_already": skipped_already,
    }


async def revoke_course_subtree(
    db: AsyncSession,
    *,
    student_id: int,
    course_id: int,
    revoked_by: Optional[int],
) -> dict[str, Any]:
    """Массово снять ручные зачёты в дереве узла.

    Реальный прогресс ученика не затрагивается: у заданий отменяются только
    синтетические попытки, у материалов удаляются только строки
    ``source='manual_teacher'``.
    """
    tree_ids = await _subtree_course_ids(db, course_id)
    tasks = await _tree_task_rows(db, tree_ids)
    materials = await _tree_material_rows(db, tree_ids)

    tasks_affected = 0
    materials_affected = 0
    skipped_already = 0

    for task in tasks:
        res = await revoke_task(
            db,
            student_id=student_id,
            task_id=int(task["id"]),
            revoked_by=revoked_by,
            _standalone=False,
        )
        if res["already"]:
            skipped_already += 1
        else:
            tasks_affected += 1

    for material in materials:
        res = await revoke_material(
            db,
            student_id=student_id,
            material_id=int(material["id"]),
            revoked_by=revoked_by,
            _standalone=False,
        )
        if res["already"]:
            skipped_already += 1
        else:
            materials_affected += 1

    await audit_service.log_event(
        db,
        audit_service.TEACHER_PROGRESS_REVOKED,
        user_id=revoked_by,
        details={
            "student_id": student_id,
            "item_type": "course",
            "item_id": course_id,
            "course_id": course_id,
            "bulk": True,
            "affected": tasks_affected + materials_affected,
            "tasks_affected": tasks_affected,
            "materials_affected": materials_affected,
            "skipped_already": skipped_already,
            "comment": None,
        },
    )

    # Один раз на всю пачку, а не на каждый элемент.
    await _refresh_course_state(db, student_id, course_id)

    logger.info(
        "tsk-297: массовое снятие зачётов дерева %s у ученика %s: заданий=%s, материалов=%s",
        course_id, student_id, tasks_affected, materials_affected,
    )
    return {
        "student_id": student_id,
        "course_id": course_id,
        "tasks_affected": tasks_affected,
        "materials_affected": materials_affected,
        "skipped_already": skipped_already,
    }


# ─── Чтение прогресса ───────────────────────────────────────────────────────


async def get_student_progress(
    db: AsyncSession,
    *,
    student_id: int,
    course_id: int,
) -> dict[str, Any]:
    """Прогресс ученика по дереву курса для карточки преподавателя.

    Плоский список ``items`` трёх типов, в УЧЕБНОМ порядке — фронт на него
    опирается и не пересортировывает:

    * ``course`` — узел темы/подкурса; идёт непосредственно перед своим
      содержимым, сами узлы следуют порядку обхода движка (post-order:
      подкурсы раньше курса-контейнера, см. `_collect_courses_in_order`);
    * ``material`` — материалы узла по ``order_position``;
    * ``task`` — задания узла по ``order_position``.

    У каждого элемента есть ``parent_course_id`` — узел, которому элемент
    непосредственно принадлежит; у запрошенного корня он ``None``.

    Статусы: задания — ``OPEN``/``IN_PROGRESS``/``FAILED``/``PASSED``/
    ``BLOCKED_LIMIT`` (как их отдаёт движок), материалы —
    ``NOT_STARTED``/``COMPLETED``/``SKIPPED``. У узлов ``course`` статус
    ``NOT_STARTED``/``IN_PROGRESS``/``COMPLETED`` берётся у движка
    (`compute_course_state`, без записи в таблицу состояний — это чтение).

    Флаг ``manual``:

    * задание — ``True``, если ПОСЛЕДНИЙ учтённый результат записан с
      ``source_system='manual_teacher'`` (отменённые попытки не учитываются —
      снятый зачёт сразу перестаёт считаться ручным);
    * материал — ``True`` при ``source='manual_teacher'``;
    * узел ``course`` — всегда ``None``: массовая операция адресуется его
      ``item_id``, а «ручным» узел сам по себе не бывает.

    ``granted_by``/``granted_at`` у задания — ``checked_by``/``checked_at``
    последнего результата. У материала ``granted_by`` всегда ``None``: колонки
    автора у ``student_material_progress`` нет (провенанс ограничен ``source``,
    см. миграцию `tsk297_manual_progress_source`).
    """
    tree_ids = await _subtree_course_ids(db, course_id)
    tasks = await _tree_task_rows(db, tree_ids)
    materials = await _tree_material_rows(db, tree_ids)

    task_ids = [int(t["id"]) for t in tasks]
    material_ids = [int(m["id"]) for m in materials]

    last_results: dict[int, dict[str, Any]] = {}
    if task_ids:
        rows = (
            await db.execute(
                text(
                    "SELECT DISTINCT ON (tr.task_id) "
                    "       tr.task_id, tr.source_system, tr.checked_by, tr.checked_at "
                    "FROM task_results tr "
                    "JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL "
                    "WHERE tr.user_id = :student_id AND tr.task_id = ANY(:task_ids) "
                    "ORDER BY tr.task_id, tr.submitted_at DESC, tr.id DESC"
                ),
                {"student_id": student_id, "task_ids": task_ids},
            )
        ).mappings().fetchall()
        last_results = {int(r["task_id"]): dict(r) for r in rows}

    material_progress: dict[int, dict[str, Any]] = {}
    if material_ids:
        rows = (
            await db.execute(
                text(
                    "SELECT material_id, status, source, completed_at, skipped_at "
                    "FROM student_material_progress "
                    "WHERE student_id = :student_id AND material_id = ANY(:material_ids)"
                ),
                {"student_id": student_id, "material_ids": material_ids},
            )
        ).mappings().fetchall()
        material_progress = {int(r["material_id"]): dict(r) for r in rows}

    # Курс → элементы, чтобы сохранить порядок обхода движка.
    by_course_materials: dict[int, list[dict[str, Any]]] = {}
    by_course_tasks: dict[int, list[dict[str, Any]]] = {}
    for m in materials:
        by_course_materials.setdefault(int(m["course_id"]), []).append(m)
    for t in tasks:
        by_course_tasks.setdefault(int(t["course_id"]), []).append(t)

    # Заголовки узлов и их родители внутри запрошенного дерева.
    course_titles = {
        int(r["id"]): r["title"]
        for r in (
            await db.execute(
                text("SELECT id, title FROM courses WHERE id = ANY(:ids)"),
                {"ids": tree_ids},
            )
        ).mappings().fetchall()
    }
    parent_of: dict[int, Optional[int]] = {}
    if len(tree_ids) > 1:
        for row in (
            await db.execute(
                text(
                    "SELECT course_id, parent_course_id FROM course_parents "
                    "WHERE course_id = ANY(:ids) AND parent_course_id = ANY(:ids) "
                    "ORDER BY order_number ASC NULLS LAST, parent_course_id"
                ),
                {"ids": tree_ids},
            )
        ).mappings().fetchall():
            # Узел может висеть под несколькими родителями одного дерева —
            # берём первый по тому же порядку, что и обход движка.
            parent_of.setdefault(int(row["course_id"]), int(row["parent_course_id"]))

    # Пропущенные задания (tsk-111) считаются пройденными при свёртке узла —
    # ровно как в `compute_course_state`.
    skipped_task_ids: set[int] = set()
    if task_ids:
        skipped_task_ids = {
            int(r[0])
            for r in (
                await db.execute(
                    text(
                        "SELECT task_id FROM student_task_progress "
                        "WHERE student_id = :student_id AND task_id = ANY(:task_ids) "
                        "  AND status = 'skipped'"
                    ),
                    {"student_id": student_id, "task_ids": task_ids},
                )
            ).fetchall()
        }

    # Элементы каждого узла собираем отдельно, чтобы сначала свернуть статусы
    # узлов, а потом выложить всё в учебном порядке.
    per_course_items: dict[int, list[dict[str, Any]]] = {cid: [] for cid in tree_ids}
    own_total: dict[int, int] = {cid: 0 for cid in tree_ids}
    own_done: dict[int, int] = {cid: 0 for cid in tree_ids}

    for cid in tree_ids:
        for m in by_course_materials.get(cid, []):
            mid = int(m["id"])
            prog = material_progress.get(mid)
            if prog is None:
                m_status = "NOT_STARTED"
            elif prog["status"] == "completed":
                m_status = "COMPLETED"
            elif prog["status"] == "skipped":
                m_status = "SKIPPED"
            else:
                m_status = str(prog["status"]).upper()
            per_course_items[cid].append({
                "item_type": "material",
                "item_id": mid,
                "course_id": cid,
                "parent_course_id": cid,
                "title": m["title"],
                "status": m_status,
                "manual": bool(prog is not None and prog.get("source") == MANUAL_SOURCE),
                "granted_by": None,
                "granted_at": prog.get("completed_at") if prog else None,
            })
            own_total[cid] += 1
            if m_status in ("COMPLETED", "SKIPPED"):
                own_done[cid] += 1
        for t in by_course_tasks.get(cid, []):
            tid = int(t["id"])
            state = await _engine.compute_task_state(db, student_id, tid)
            last = last_results.get(tid)
            is_manual = bool(last is not None and last.get("source_system") == MANUAL_SOURCE)
            per_course_items[cid].append({
                "item_type": "task",
                "item_id": tid,
                "course_id": cid,
                "parent_course_id": cid,
                "title": humanize_task_title(
                    tid, t.get("tc_title"), t.get("tc_stem"), t.get("external_uid")
                ),
                "status": state.state,
                "manual": is_manual,
                "granted_by": (last or {}).get("checked_by") if is_manual else None,
                "granted_at": (last or {}).get("checked_at") if is_manual else None,
            })
            own_total[cid] += 1
            if state.state == "PASSED" or tid in skipped_task_ids:
                own_done[cid] += 1

    # Свёртка статуса узла по его поддереву. Считаем сами, а НЕ через
    # `compute_course_state`: тот при COMPLETED дёргает Y-6 эскалацию
    # (уведомление методисту + запись в `task_results.metrics`), а это чтение —
    # просмотр карточки ученика не должен ничего рассылать и писать.
    children_of: dict[int, list[int]] = {cid: [] for cid in tree_ids}
    for child, parent in parent_of.items():
        if parent in children_of:
            children_of[parent].append(child)

    def _rollup(node: int, seen: frozenset[int] = frozenset()) -> tuple[int, int]:
        """(всего, пройдено) по поддереву узла; `seen` защищает от циклов в DAG."""
        if node in seen:
            return (0, 0)
        seen = seen | {node}
        total, done = own_total.get(node, 0), own_done.get(node, 0)
        for child in children_of.get(node, []):
            c_total, c_done = _rollup(child, seen)
            total += c_total
            done += c_done
        return (total, done)

    def _course_status(node: int) -> str:
        total, done = _rollup(node)
        if total == 0 or done >= total:
            return "COMPLETED"
        if done == 0:
            return "NOT_STARTED"
        return "IN_PROGRESS"

    items: list[dict[str, Any]] = []
    for cid in tree_ids:
        items.append({
            "item_type": "course",
            "item_id": cid,
            "course_id": cid,
            # У запрошенного корня родителя в этом дереве нет.
            "parent_course_id": None if cid == course_id else parent_of.get(cid),
            "title": course_titles.get(cid),
            "status": _course_status(cid),
            "manual": None,
            "granted_by": None,
            "granted_at": None,
        })
        items.extend(per_course_items[cid])

    return {"student_id": student_id, "course_id": course_id, "items": items}


__all__ = [
    "MANUAL_SOURCE",
    "REVOKE_REASON",
    "SYSTEM_SOURCE",
    "can_edit_progress",
    "ensure_can_edit_progress",
    "get_student_progress",
    "list_accessible_student_courses",
    "grant_course_subtree",
    "grant_material",
    "grant_task",
    "revoke_course_subtree",
    "revoke_material",
    "revoke_task",
]
