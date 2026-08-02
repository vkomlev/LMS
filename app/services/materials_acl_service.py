"""Y-5.1: ACL helper для GET /materials/{id} с cookie-auth.

Параллель к `tasks_acl_service.py` (Y-4 post-S5). Разблокирует SPW frontend:
запрос `GET /api/v1/materials/{id}` через student cookie ранее получал
403 «Invalid or missing API Key» от `Depends(get_db)` (legacy service-key
gate в CRUD router). Теперь cookie auth работает с ACL по дереву
`user_courses` + `course_parents` (recursive).

Правила доступа (тождественны tasks_acl_service):
- `current_user.is_service` (X-API-Key) → bypass (TG_LMS, ContentBackbone CLI).
- Methodist / admin / teacher (любая extended-роль) — bypass.
- Student / без расширенных ролей — material доступен, если его `course_id`
  лежит в дереве `user_courses` пользователя (root или потомок через
  `course_parents`).
- Иначе → 403.
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
    """True если course_id лежит в дереве user_courses пользователя."""
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


async def assert_material_access(
    db: AsyncSession,
    *,
    current_user: CurrentUser,
    material_course_id: int | None,
) -> None:
    """Проверить доступ к material (Y-5.1 fix).

    Raises HTTPException 403 если current_user не имеет права видеть material.
    is_service / extended-role bypass'ит проверку.
    student имеет доступ если material.course_id лежит в дереве user_courses.
    """
    # Service-key (X-API-Key) — bypass для backward compat (TG_LMS, CB CLI).
    if current_user.is_service:
        return

    has_extended = await _user_has_extended_role(db, current_user.id)
    if has_extended:
        return

    # Student-level: material должен иметь course_id и попадать в дерево user_courses.
    if material_course_id is None:
        logger.info(
            "Y-5.1: material без course_id; student user_id=%s deny",
            current_user.id,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Доступ к материалу запрещён: материал не привязан к курсу",
        )

    in_tree = await _user_has_course_in_tree(db, current_user.id, material_course_id)
    if not in_tree:
        logger.info(
            "Y-5.1: deny student user_id=%s material.course_id=%s "
            "(не в дереве user_courses)",
            current_user.id, material_course_id,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Доступ к материалу запрещён: вы не зачислены в этот курс",
        )

    from app.services import payment_access_service

    # tsk-010: зачислен, но просрочил оплату — материалы закрыты. Проверка стоит
    # ПОСЛЕ bypass'ов роли: долг ученика не должен закрывать материал
    # преподавателю или методисту.
    await payment_access_service.assert_content_allowed(db, current_user.id)


# Префикс url'а, который `POST /materials/upload` кладёт в `content` материала.
# Единственная нить, связывающая файл на диске с курсом: имени файла
# (`{uuid4hex}_{оригинал}`) в БД нет, отдельной таблицы вложений тоже.
_FILE_URL_PREFIX = "/api/v1/materials/files/"


async def assert_material_file_access(
    db: AsyncSession,
    *,
    current_user: CurrentUser,
    file_id: str,
) -> None:
    """Проверить доступ к загруженному файлу материала (tsk-516).

    Файл не хранит ссылки на курс: `upload_material_file` кладёт его на диск
    под именем `{uuid4hex}_{оригинал}` и возвращает url, который клиент сам
    вписывает в `content` материала отдельным PATCH. Поэтому курс ищется в
    обратную сторону — по вхождению url в `content` материалов. Один файл
    может быть вписан в несколько материалов (копирование материала в другой
    курс), и тогда достаточно доступа к любому из них: содержимое одно и то же.

    Правила совпадают с `assert_material_access` — сервисный ключ и
    расширенная роль проходят, ученик обязан быть зачислен и не иметь
    просроченной оплаты.

    Файл, на который не ссылается ни один материал (окно между `upload` и
    PATCH, либо забытый мусор), закрыт для всех, кроме сервиса и расширенных
    ролей: привязать его к курсу не по чему, а значит и подтвердить право
    ученика нечем.
    """
    if current_user.is_service:
        return

    if await _user_has_extended_role(db, current_user.id):
        return

    # strpos вместо LIKE: имя файла содержит `_`, который в LIKE значит
    # «любой символ» и расширил бы совпадение на соседние файлы.
    res = await db.execute(
        text(
            "SELECT DISTINCT course_id FROM materials "
            "WHERE strpos(content::text, :needle) > 0"
        ),
        {"needle": f"{_FILE_URL_PREFIX}{file_id}"},
    )
    course_ids = [row[0] for row in res.fetchall() if row[0] is not None]

    if not course_ids:
        logger.info(
            "tsk-516: файл %r не привязан ни к одному материалу; user_id=%s deny",
            file_id, current_user.id,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Доступ к файлу запрещён: файл не привязан к материалу курса",
        )

    for course_id in course_ids:
        if await _user_has_course_in_tree(db, current_user.id, course_id):
            from app.services import payment_access_service

            await payment_access_service.assert_content_allowed(db, current_user.id)
            return

    logger.info(
        "tsk-516: deny user_id=%s file=%r (курсы %s не в дереве user_courses)",
        current_user.id, file_id, course_ids,
    )
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "Доступ к файлу запрещён: вы не зачислены в курс этого материала",
    )
