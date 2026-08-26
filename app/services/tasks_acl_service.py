"""Y-4 post-S5: ACL helper для GET /tasks/{by-external,{id}} с cookie-auth.

Разблокирует регрессию post-S5: SPW frontend через student cookie получал
403 «Invalid or missing API Key» от `Depends(get_db)` (legacy service-key
gate). Теперь cookie auth работает с ACL по дереву `user_courses` +
`course_parents` (recursive).

Правила доступа:
- `current_user.is_service` (X-API-Key auth) → bypass (legacy TG_LMS bots,
  ContentBackbone CLI продолжают работать).
- Methodist / admin / teacher (любой role в `user_roles`) — bypass
  (для проверки/управления задачами).
- Student / без расширенных ролей — task доступна, если её `course_id`
  лежит в дереве `user_courses` пользователя (root или любой потомок
  через `course_parents`).
- Иначе → 403.

Schema reminder (verified MCP 2026-05-01):
- `user_roles(user_id, role_id)` PK; FK на `roles` (id=4='student',
  id=3='teacher', id=2='methodist', id=1='admin').
- `course_parents(course_id, parent_course_id)` — child → parent,
  глубина дерева 2 (verified Y-4.1).
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.current_user import CurrentUser

logger = logging.getLogger(__name__)


async def _user_has_extended_role(db: AsyncSession, user_id: int) -> bool:
    """True если у user есть роль admin / methodist / teacher (любая)."""
    res = await db.execute(
        text(
            "SELECT 1 FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id "
            "WHERE ur.user_id = :uid "
            "  AND r.name IN ('admin','methodist','teacher') "
            "LIMIT 1"
        ),
        {"uid": user_id},
    )
    return res.fetchone() is not None


async def _user_has_course_in_tree(
    db: AsyncSession, user_id: int, course_id: int
) -> bool:
    """True если course_id лежит в дереве user_courses пользователя
    (root или потомок через course_parents recursive).

    Дерево строится ВНИЗ от user_courses (root) → потомки через
    course_parents.parent_course_id → course_parents.course_id (child).
    Глубина=2 в реальной схеме (verified Y-4.1).
    """
    res = await db.execute(
        text(
            """
            WITH RECURSIVE user_course_tree AS (
                SELECT course_id
                FROM user_courses
                WHERE user_id = :uid AND is_active = true
                UNION ALL
                SELECT cp.course_id
                FROM course_parents cp
                JOIN user_course_tree uct
                  ON cp.parent_course_id = uct.course_id
            )
            SELECT 1 FROM user_course_tree
            WHERE course_id = :tcid
            LIMIT 1
            """
        ),
        {"uid": user_id, "tcid": course_id},
    )
    return res.fetchone() is not None


async def assert_task_access(
    db: AsyncSession,
    *,
    current_user: CurrentUser,
    task_course_id: int | None,
) -> bool:
    """Проверить доступ к task (Y-4 post-S5 fix).

    Raises HTTPException 403 если current_user не имеет права видеть task.
    is_service / extended-role bypass'ит проверку.
    student имеет доступ если task.course_id лежит в дереве user_courses.

    Args:
        db: async session
        current_user: resolved через `Depends(get_current_user)`
        task_course_id: `tasks.course_id` (может быть NULL для legacy задач —
                        в этом случае только service / extended-role видят)

    Returns:
        True — вызывающий привилегирован (сервисный ключ либо роль
        admin / methodist / teacher); False — доступ дан ученику по дереву
        его курсов. Признак нужен вызывающему, чтобы решить, какие поля
        задания отдавать: правило проверки с верными ответами ученику не
        показываем (tsk-460).
    """
    # Service-key (X-API-Key) — bypass для backward compat (TG_LMS, CB CLI).
    if current_user.is_service:
        return True

    # Аутентифицированный user, проверяем роль.
    has_extended = await _user_has_extended_role(db, current_user.id)
    if has_extended:
        return True

    # Student-level: task должна иметь course_id и попадать в дерево user_courses.
    if task_course_id is None:
        logger.info(
            "Y-4 post-S5: task без course_id; student user_id=%s deny",
            current_user.id,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Доступ к задаче запрещён: задача не привязана к курсу",
        )

    in_tree = await _user_has_course_in_tree(db, current_user.id, task_course_id)
    if not in_tree:
        logger.info(
            "Y-4 post-S5: deny student user_id=%s task.course_id=%s "
            "(не в дереве user_courses)",
            current_user.id, task_course_id,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Доступ к задаче запрещён: вы не зачислены в этот курс",
        )

    from app.services import payment_access_service

    # tsk-010: просроченная оплата закрывает задания — см. materials_acl_service.
    await payment_access_service.assert_content_allowed(db, current_user.id)

    return False


async def assert_task_active_for_student(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    is_active: bool,
) -> None:
    """Закрыть ученику ОТПРАВКУ ответа на выключенное задание (tsk-701).

    Хвост линии tsk-695 → tsk-697 → tsk-699: чтение выключенного задания ученику
    закрыли (одна ручка, список курса, клиенты ТГ), а запись — нет. Приём ответа
    `POST /attempts/{id}/answers` смотрел на запись ученика в курс
    (`assert_task_access`, tsk-272) и на дерево корня попытки
    (`root_contains_course`, tsk-264), но не на активность: ответ по снятому с
    публикации заданию проверялся и ложился в `task_results` — то есть попадал в
    прогресс, статистику и расход лимита по контенту, которого для ученика
    больше нет.

    **Проверяется УЧЕНИК (владелец попытки), а не вызывающий** — по той же
    причине, что в `payment_access_service.assert_content_allowed` (tsk-617) и
    `graduation_service.assert_course_work_allowed` (tsk-673): ВСЕ боты TG_LMS
    ходят по сервисному ключу, и освобождение сервисного вызова означало бы
    «через браузер нельзя, через Telegram можно». Ровно эта дыра в чтении
    потребовала клиентской фильтрации в ботах (tsk-696, tsk-700) — повторять её
    на записи нельзя. Владелец с расширенной ролью (teacher / methodist / admin)
    проходит: у него своя причина открыть снятое задание — превью «как ученик» и
    разбор старой сдачи.

    400, а не 404 как на чтении: существование задания вызывающему уже открыто
    (`assert_task_access` выше прошёл — ученик зачислен на курс), скрывать нечего,
    а 404 на этой ручке означает «попытка не найдена». 400 — это и есть код
    остальных поштучных отказов цикла (задание не найдено, задание вне дерева
    корня).

    :param student_id: владелец попытки (`attempts.user_id`), а не вызывающий.
    :param task_id: id задания — только для журнала.
    :param is_active: `tasks.is_active` (NOT NULL, default true).
    :raises HTTPException: 400, если задание выключено, а владелец попытки —
        обычный ученик.
    """
    if is_active:
        return

    if await _user_has_extended_role(db, student_id):
        return

    logger.info(
        "tsk-701: deny answer student_id=%s task_id=%s (is_active=false)",
        student_id, task_id,
    )
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "Задание снято с публикации и больше не принимает ответы.",
    )
