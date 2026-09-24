"""Самозапись ученика на бесплатный курс (tsk-1109).

Лендинг сайта продаёт бесплатный курс (tsk-1070: «Основы Python для новичков»),
а записывать в LMS умели только методист и админ — вошедший с лендинга видел
пустой кабинет. Здесь единственное исключение из этого правила: ученик сам
записывается на курс, который школа отдаёт даром.

Условия записи (все проверяются на сервере, клиенту не доверяем):
- курс есть и включён (`courses.is_active`);
- курс КОРНЕВОЙ — вложенные темы открываются через корень, а триггер БД
  `check_user_course_has_no_parents` и так не даст записать в подкурс;
- у курса `course_pricing.sale_status = 'free'`. Платный, «не продаётся» и курс
  без цены — отказ 403: такой курс открывает только сотрудник;
- бесплатны и все курсы, которые запись доназначит как зависимости
  (`course_dependencies` с `auto_assign`). Иначе бесплатный курс с платной
  зависимостью открыл бы платный курс даром — тоже 403.

Повторный запрос ничего не создаёт и отвечает успехом (`created=False`) —
кнопку могут нажать дважды, а ответ мог потеряться по дороге. Исключение —
связь есть, но выключена (`user_courses.is_active=false`, план курса поставлен
на паузу сотрудником): её самозапись не включает обратно, это решение персонала.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.course_dependencies_enrollment_service import (
    collect_required_course_ids,
)
from app.services.user_courses_service import UserCoursesService
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

NOT_FOUND_DETAIL = "Курс не найден"
NOT_FREE_DETAIL = "Этот курс не бесплатный — доступ к нему открывает школа"
NOT_ROOT_DETAIL = "Записаться можно только на курс целиком, а не на его тему"
PAID_DEPENDENCY_DETAIL = (
    "Этот курс требует другой, платный курс — доступ к нему открывает школа"
)
PAUSED_DETAIL = "Курс у вас приостановлен — чтобы продолжить, напишите преподавателю"

_COURSE_SQL = text(
    """
    SELECT c.id,
           c.course_uid,
           c.is_active,
           cp.sale_status,
           EXISTS (SELECT 1 FROM course_parents p WHERE p.course_id = c.id) AS has_parent
      FROM courses c
      LEFT JOIN course_pricing cp ON cp.course_id = c.id
     WHERE c.id = :cid
    """
)

_NOT_FREE_AMONG_SQL = text(
    """
    SELECT c.id
      FROM courses c
      LEFT JOIN course_pricing cp ON cp.course_id = c.id
     WHERE c.id = ANY(:ids) AND cp.sale_status IS DISTINCT FROM 'free'
    """
)

_LINK_SQL = text(
    "SELECT is_active FROM user_courses WHERE user_id = :uid AND course_id = :cid"
)

_user_courses_service = UserCoursesService()


@dataclass(frozen=True)
class FreeEnrollmentResult:
    """Итог самозаписи: курс и признак «связь создана сейчас»."""

    course_id: int
    course_uid: str | None
    created: bool


async def enroll_self_free(
    db: AsyncSession, *, user_id: int, course_id: int
) -> FreeEnrollmentResult:
    """Записать ученика на бесплатный корневой курс (идемпотентно).

    Связь создаёт общий `UserCoursesService.create` — с теми же заслонами, что и
    у записи методистом: выключенный курс (409), выпускник (409), доназначение
    курсов-зависимостей. Гонку двух одновременных нажатий он превращает в 409
    дубля — здесь это считается успехом «уже записан».

    :param db: async-сессия.
    :param user_id: кто записывается (текущий пользователь, не из запроса).
    :param course_id: на какой курс.
    :return: курс и признак, создана ли связь этим вызовом.
    :raises HTTPException: 404 — курса нет; 403 — курс или его зависимость не
        бесплатные, либо это не корень;
        409 — курс выключен или связь приостановлена сотрудником.
    """
    row = (await db.execute(_COURSE_SQL, {"cid": course_id})).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND_DETAIL)
    if row["sale_status"] != "free":
        logger.info(
            "tsk-1109: отказ самозаписи user_id=%s course_id=%s sale_status=%s",
            user_id, course_id, row["sale_status"],
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_FREE_DETAIL)
    if row["has_parent"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_ROOT_DETAIL)

    course_uid = row["course_uid"]
    link_active = (
        await db.execute(_LINK_SQL, {"uid": user_id, "cid": course_id})
    ).scalar_one_or_none()
    if link_active is True:
        return FreeEnrollmentResult(course_id, course_uid, created=False)
    if link_active is False:
        raise HTTPException(status.HTTP_409_CONFLICT, PAUSED_DETAIL)

    required = await collect_required_course_ids(db, [course_id])
    if required:
        not_free = (
            await db.execute(_NOT_FREE_AMONG_SQL, {"ids": required})
        ).scalars().all()
        if not_free:
            logger.info(
                "tsk-1109: отказ самозаписи user_id=%s course_id=%s — платные зависимости %s",
                user_id, course_id, list(not_free),
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, PAID_DEPENDENCY_DETAIL)

    try:
        await _user_courses_service.create(
            db, {"user_id": user_id, "course_id": course_id}
        )
    except DomainError:
        # Единственный DomainError записи — дубль: соседний запрос успел записать
        # между проверкой и вставкой. Перечитываем, а не верим тексту ошибки.
        link_active = (
            await db.execute(_LINK_SQL, {"uid": user_id, "cid": course_id})
        ).scalar_one_or_none()
        if link_active is True:
            return FreeEnrollmentResult(course_id, course_uid, created=False)
        raise
    logger.info("tsk-1109: самозапись user_id=%s course_id=%s", user_id, course_id)
    return FreeEnrollmentResult(course_id, course_uid, created=True)
