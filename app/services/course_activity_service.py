"""tsk-886: курс вне работы (`courses.is_active`) — один страж на все пути.

Признак завёл tsk-873, поведения за ним не было: выключенный курс так же
предлагался в формах, принимал зачисление и попадал в подбор домашней работы.

Правило, по которому проведена граница:

* **Запрещается создание НОВОЙ связи** с выключенным курсом — зачисление,
  закрепление преподавателя, начало попытки. Курс выведён из работы, и
  заводить на нём новое незачем.
* **Чтение и уже начатое не трогаем.** Карточка человека, история, прогресс,
  открытая незавершённая попытка — всё показывается и продолжает работать:
  иначе прошлое ученика на этом курсе перестанет объясняться.

Каскада нет намеренно (граница tsk-873): выключенный корень НЕ выключает
подкурсы. Подкурс живёт под несколькими родителями, и «выключить дерево»
выключило бы его в чужих программах. Поэтому каждый узел проверяется сам по
себе, а не через предков.

Код отказа — **409 Conflict**: тело запроса корректно (это не 422), объект
существует и вызывающему открыт (это не 404), но текущее состояние курса
конфликтует с действием. Тем же 409 в этом кабинете уже отвечают «курс вложен
в другой — зачислять нельзя» (`user_courses_extra`, `teacher_courses`), то есть
клиент разбирает отказ по тексту в одном месте.
"""
from __future__ import annotations

import logging
from typing import Iterable

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Роль владельца берём у соседнего стража (tsk-701): четвёртой копии этого
# запроса заводить не нужно — предикат общий, зовём его, а не копируем.
from app.services.tasks_acl_service import _user_has_extended_role

logger = logging.getLogger(__name__)

#: Текст отказа. Один на все пути: методист выключил курс в одном месте, и
#: объяснение должно быть одинаковым, откуда бы ни пришёл отказ.
INACTIVE_DETAIL = (
    "Курс выведен из работы и не принимает новых записей. "
    "Верните его в работу на карточке курса, если он снова нужен."
)


async def inactive_among(db: AsyncSession, course_ids: Iterable[int]) -> list[int]:
    """Какие из перечисленных курсов выключены.

    :param db: async-сессия.
    :param course_ids: проверяемые id (дубли и пустой список допустимы).
    :returns: id выключенных курсов в порядке возрастания; несуществующие id
        сюда не попадают — их отсутствие ловят свои проверки.
    """
    ids = sorted({int(c) for c in course_ids})
    if not ids:
        return []
    rows = await db.execute(
        text("SELECT id FROM courses WHERE id = ANY(:ids) AND is_active = false"),
        {"ids": ids},
    )
    return [int(r[0]) for r in rows.fetchall()]


async def load_inactive_course_ids(db: AsyncSession) -> set[int]:
    """Все выключенные курсы разом — для обхода дерева.

    Подбор домашней работы идёт по узлам дерева и спрашивать про каждый узел
    отдельно дорого. Выборка ограничена числом ВЫВЕДЕННЫХ курсов (на 10.09 их
    ноль из 843), поэтому «взять все» здесь дешевле точечных запросов.
    """
    rows = await db.execute(text("SELECT id FROM courses WHERE is_active = false"))
    return {int(r[0]) for r in rows.fetchall()}


async def assert_courses_active(
    db: AsyncSession,
    course_ids: Iterable[int],
    *,
    action: str,
) -> None:
    """Отказать, если среди курсов есть выключенный.

    Зовётся ПЕРЕД созданием новой связи. Уже существующую связь проверять не
    нужно и нельзя: повторное (идемпотентное) назначение ничего не создаёт, а
    отказ на нём сломал бы чтение того, что уже есть.

    :param db: async-сессия.
    :param course_ids: курсы, с которыми создаётся связь.
    :param action: что делали — только для журнала.
    :raises HTTPException: 409, если хотя бы один курс выключен.
    """
    inactive = await inactive_among(db, course_ids)
    if not inactive:
        return
    logger.info("tsk-886: отказ «%s» — курсы вне работы: %s", action, inactive)
    raise HTTPException(status.HTTP_409_CONFLICT, INACTIVE_DETAIL)


async def assert_course_active_for_student(
    db: AsyncSession,
    *,
    student_id: int,
    course_id: int | None,
    action: str,
) -> None:
    """То же для ученика, но со снисхождением к сотруднику.

    Расширенная роль (teacher / methodist / admin) проходит: методист выключает
    курс и тут же идёт смотреть его глазами ученика — закрыть ему этот путь
    значит закрыть проверку собственной правки. Ровно то же исключение сделано
    у выключенного задания (`tasks_acl_service.assert_task_active_for_student`,
    tsk-701).

    Проверяется ВЛАДЕЛЕЦ попытки, а не вызывающий: все боты TG_LMS ходят по
    сервисному ключу, и освобождение сервисного вызова означало бы «в браузере
    нельзя, в Telegram можно» (тот же урок tsk-617/tsk-673/tsk-701).

    :param db: async-сессия.
    :param student_id: владелец будущей попытки.
    :param course_id: курс попытки; `None` — проверять нечего.
    :param action: что делали — только для журнала.
    :raises HTTPException: 409, если курс выключен, а владелец — обычный ученик.
    """
    if course_id is None:
        return
    if not await inactive_among(db, [course_id]):
        return
    if await _user_has_extended_role(db, student_id):
        return
    logger.info(
        "tsk-886: отказ «%s» student_id=%s course_id=%s (курс вне работы)",
        action, student_id, course_id,
    )
    raise HTTPException(status.HTTP_409_CONFLICT, INACTIVE_DETAIL)
