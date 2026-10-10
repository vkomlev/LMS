# app/services/user_courses_service.py

from __future__ import annotations

from typing import Optional, List, Dict

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_courses import UserCourses
from app.repos.user_courses_repo import UserCoursesRepository
from app.services import (
    alumni_enrollment_guard,
    course_activity_service,
    course_dependencies_enrollment_service,
)
from app.services.base import BaseService
from app.utils.exceptions import DomainError

# tsk-574: PG-код нарушения уникальности. Составной PK `user_courses_pkey`
# (user_id, course_id) ловит повторное назначение курса тому же ученику.
_PG_UNIQUE_VIOLATION = "23505"

# Единый код ответа на повторное назначение. 409, а не 400: запрос корректен,
# конфликтует состояние ресурса — как у остальных конфликтов LMS (лимит попыток
# tsk-269, identity-overlap VK). Раньше этот путь отдавал 500, и оператор при
# пакетном назначении не мог отличить «уже назначено» от отказа сервера.
_DUPLICATE_STATUS = 409
_DUPLICATE_DETAIL = "Курс уже назначен этому ученику"


def _is_unique_violation(exc: IntegrityError) -> bool:
    """Отличить дубль связи от прочих нарушений целостности (например, FK).

    :param exc: исключение SQLAlchemy, полученное на вставке.
    :return: True, если это нарушение уникальности (PG 23505).
    """
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate == _PG_UNIQUE_VIOLATION:
        return True
    # Драйвер без sqlstate — опираемся на имя ограничения в тексте ошибки.
    return "user_courses_pkey" in str(orig)


class UserCoursesService(BaseService[UserCourses]):
    """
    Сервис для связей пользователей с курсами.
    
    ⚠️ ВАЖНО: Бизнес-логика для order_number реализована в БД через триггеры.
    Не дублировать логику автоматической нумерации в коде!
    См. docs/database-triggers-contract.md
    """
    def __init__(self, repo: UserCoursesRepository = UserCoursesRepository()):
        super().__init__(repo)

    async def create(self, db: AsyncSession, obj_in: Dict) -> UserCourses:
        """Создать связь ученик↔курс и доназначить курсы-зависимости (tsk-261, A2).

        Оверрайд `BaseService.create`: `POST /api/v1/user-courses/` (назначение из
        UI) идёт мимо `assignment_rules_service.assign_course_to_student`, поэтому
        автоназначение зависимостей подключается и здесь. Иначе назначенный из UI
        зависимый курс остался бы заблокированным навсегда.

        Зависимости вставляются ДО основной связи, и коммитит их общий
        `repo.create(commit=True)` — иначе атомарности нет: `BaseRepository.create`
        коммитит сам, и падение доназначения оставило бы курс назначенным без
        зависимости (замок навсегда), а вызывающему отдало 500.

        tsk-574: повторное назначение — это `DomainError` 409, а не 500. Защита
        двухслойная и оба слоя нужны. Ранняя проверка отсекает дубль ДО
        доназначения зависимостей (иначе побочные вставки делаются впустую и
        сносятся откатом), а перехват `IntegrityError` закрывает гонку двух
        параллельных назначений — между проверкой и вставкой строку может
        создать соседний запрос.

        :param db: асинхронная сессия БД.
        :param obj_in: данные связи (`user_id`, `course_id`, опц. `order_number`).
        :return: созданная связь.
        :raises DomainError: 409, если связь ученик↔курс уже существует.
        """
        user_id = obj_in.get("user_id")
        course_id = obj_in.get("course_id")
        if user_id is not None and course_id is not None:
            keys = {"user_id": int(user_id), "course_id": int(course_id)}
            if await self.repo.get_by_keys(db, keys):
                raise DomainError(
                    detail=_DUPLICATE_DETAIL,
                    status_code=_DUPLICATE_STATUS,
                    payload=keys,
                )
            # tsk-886: курс вне работы новых записей не принимает. Проверка
            # стоит ПОСЛЕ отсечения дубля (повторное назначение ничего не
            # создаёт — отказывать там не за что) и ДО доназначения
            # зависимостей: иначе побочные вставки делаются впустую.
            await course_activity_service.assert_courses_active(
                db, [int(course_id)], action="зачисление ученика на курс"
            )
            # tsk-894: та же логика для ученика-выпускника — новую связь
            # ученик↔курс заводить незачем, обучение уже закрыто.
            await alumni_enrollment_guard.assert_not_alumni(
                db, int(user_id), action="зачисление ученика на курс"
            )
            await course_dependencies_enrollment_service.ensure_dependencies_assigned(
                db, student_id=int(user_id), course_ids=[int(course_id)]
            )
        try:
            return await super().create(db, obj_in)
        except IntegrityError as exc:
            # Сессия после провала вставки непригодна к работе — откатываем,
            # иначе следующий запрос по ней упадёт уже на чтении.
            await db.rollback()
            if not _is_unique_violation(exc):
                raise
            raise DomainError(
                detail=_DUPLICATE_DETAIL,
                status_code=_DUPLICATE_STATUS,
                payload={"user_id": user_id, "course_id": course_id},
            ) from exc

    async def get_user_courses(
        self,
        db: AsyncSession,
        user_id: int,
        order_by_order: bool = True,
    ) -> List[UserCourses]:
        """
        Получить курсы пользователя с сортировкой.

        :param db: асинхронная сессия БД.
        :param user_id: ID пользователя.
        :param order_by_order: Если True, сортировать по order_number, иначе по added_at.
        :return: Список связей пользователя с курсами.
        """
        return await self.repo.get_user_courses(db, user_id, order_by_order)

    async def assign_course_with_order(
        self,
        db: AsyncSession,
        user_id: int,
        course_id: int,
        order_number: Optional[int] = None,
    ) -> UserCourses:
        """
        Привязать курс к пользователю с указанием порядкового номера.
        Если order_number не указан, установится автоматически через триггер БД.

        :param db: асинхронная сессия БД.
        :param user_id: ID пользователя.
        :param course_id: ID курса.
        :param order_number: Порядковый номер (опционально, установится автоматически если None).
        :return: Созданная связь пользователя с курсом.
        :raises DomainError: 409, если связь ученик↔курс уже существует.
        """
        # Дубль ловит `create` (проверка + перехват IntegrityError на гонке),
        # tsk-574: код один на оба пути назначения, отдельная проверка здесь
        # больше не нужна.
        # Создаем связь (order_number установится триггером, если не указан)
        return await self.create(
            db,
            {
                "user_id": user_id,
                "course_id": course_id,
                "order_number": order_number,
            },
        )

    async def bulk_assign_courses(
        self,
        db: AsyncSession,
        user_id: int,
        course_ids: List[int],
    ) -> List[UserCourses]:
        """
        Массовая привязка курсов к пользователю.
        order_number установится автоматически для каждой записи через триггер БД.

        tsk-261 (A2): вместе с курсами доназначаются их курсы-зависимости
        (`course_dependencies`, транзитивно). Иначе зависимый курс остаётся
        заблокированным навсегда: замок снимается только по `COMPLETED`
        required-курса, а пройти неназначенный курс ученик не может.

        Зависимости вставляются ДО основной привязки: `batch_create` коммитит сам,
        и общий коммит забирает обе вставки. Обратный порядок ломал атомарность —
        падение доназначения оставило бы курсы назначенными без зависимостей.
        Возвращаются только явно запрошенные связи; доназначенные зависимости в
        ответ не попадают (их видно в `GET /me/courses`).

        :param db: асинхронная сессия БД.
        :param user_id: ID пользователя.
        :param course_ids: Список ID курсов для привязки.
        :return: Список созданных связей пользователя с курсами.
        """
        # tsk-886: отказываем, только если выключенный курс ученику ЕЩЁ НЕ
        # назначен. Уже назначенный курс пачка и так пропускает — новой связи
        # там не появляется, и разворачивать из-за него всю операцию значило бы
        # ломать чтение того, что уже есть.
        existing = set(
            (
                await db.execute(
                    text(
                        "SELECT course_id FROM user_courses "
                        "WHERE user_id = :uid AND course_id = ANY(:cids)"
                    ),
                    {"uid": int(user_id), "cids": [int(c) for c in course_ids]},
                )
            ).scalars().all()
        )
        await course_activity_service.assert_courses_active(
            db,
            [int(c) for c in course_ids if int(c) not in existing],
            action="пакетное зачисление ученика на курсы",
        )
        # tsk-894: пакетная привязка — тот же выпускник, что и одиночная.
        # Проверяем, только если пачка что-то реально добавит (см. комментарий
        # выше про `existing`): иначе отказывали бы там, где новой связи нет.
        if len(existing) < len(course_ids):
            await alumni_enrollment_guard.assert_not_alumni(
                db, int(user_id), action="пакетное зачисление ученика на курсы"
            )
        await course_dependencies_enrollment_service.ensure_dependencies_assigned(
            db, student_id=user_id, course_ids=course_ids
        )
        return await self.repo.bulk_create_user_courses(db, user_id, course_ids)

    async def switch_direction(
        self,
        db: AsyncSession,
        user_id: int,
        enroll_course_ids: List[int],
        deactivate_course_ids: List[int],
    ) -> List[int]:
        """Сменить направление ученика одним действием (tsk-1291).

        Старые курсы выключаются (`is_active=false`) — не удаляются: прогресс и
        место в истории сохраняются, и курс можно включить обратно. Новые
        курсы зачисляются; если запись уже есть, но выключена — включается.

        Раньше смену делали отчислением (DELETE стирал запись) либо не делали
        вовсе: Мочалов осенью получил «Информатику 8–9» и ОГЭ, а «Python для
        ЕГЭ» с лета остался активным и продолжал влиять на выдачу ДЗ.

        Атомарность: выключение и включение идут во flush, коммит делает
        `bulk_assign_courses` (или этот метод, если зачислять заново нечего).
        Отказ пачки — 409 и откат всего на вызывающем.

        :return: ID курсов, которые после операции активны у ученика.
        :raises DomainError: пустая операция, пересечение списков, курс к
            выключению не назначен ученику.
        """
        enroll = list(dict.fromkeys(int(c) for c in enroll_course_ids))
        deactivate = list(dict.fromkeys(int(c) for c in deactivate_course_ids))
        if not enroll and not deactivate:
            raise DomainError("Укажите, что зачислить или что выключить.")
        both = set(enroll) & set(deactivate)
        if both:
            raise DomainError(
                f"Курс не может быть одновременно новым и старым: {sorted(both)}."
            )

        rows = dict(
            (
                await db.execute(
                    text(
                        "SELECT course_id, is_active FROM user_courses "
                        " WHERE user_id = :uid AND course_id = ANY(:cids)"
                    ),
                    {"uid": int(user_id), "cids": enroll + deactivate},
                )
            ).all()
        )
        missing = [c for c in deactivate if c not in rows]
        if missing:
            raise DomainError(
                f"Ученик не записан на курсы {missing} — выключать нечего.",
                status_code=404,
            )

        if deactivate:
            await db.execute(
                text(
                    "UPDATE user_courses SET is_active = false "
                    " WHERE user_id = :uid AND course_id = ANY(:cids)"
                ),
                {"uid": int(user_id), "cids": deactivate},
            )
        reactivate = [c for c in enroll if c in rows and not rows[c]]
        if reactivate:
            # Выведенный из работы курс обратно не включаем — то же правило,
            # что у зачисления (tsk-886).
            await course_activity_service.assert_courses_active(
                db, reactivate, action="включение курса ученику"
            )
            await db.execute(
                text(
                    "UPDATE user_courses SET is_active = true "
                    " WHERE user_id = :uid AND course_id = ANY(:cids)"
                ),
                {"uid": int(user_id), "cids": reactivate},
            )
        new = [c for c in enroll if c not in rows]
        if new:
            await self.bulk_assign_courses(db, user_id, new)
        else:
            await db.commit()

        return list(
            (
                await db.execute(
                    text(
                        "SELECT course_id FROM user_courses "
                        " WHERE user_id = :uid AND is_active = true "
                        " ORDER BY order_number NULLS LAST, added_at"
                    ),
                    {"uid": int(user_id)},
                )
            ).scalars().all()
        )

    async def reorder_courses(
        self,
        db: AsyncSession,
        user_id: int,
        course_orders: List[Dict[str, int]],
    ) -> List[UserCourses]:
        """
        Переупорядочить курсы пользователя (явное обновление order_number).

        :param db: асинхронная сессия БД.
        :param user_id: ID пользователя.
        :param course_orders: Список словарей вида [{"course_id": 1, "order_number": 1}, ...].
        :return: Список обновленных связей пользователя с курсами.
        """
        return await self.repo.reorder_user_courses(db, user_id, course_orders)

    async def get_course_users(
        self,
        db: AsyncSession,
        course_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> List[UserCourses]:
        """
        Получить список пользователей (студентов) курса.

        :param db: асинхронная сессия БД.
        :param course_id: ID курса.
        :param limit: Максимум результатов.
        :param offset: Смещение.
        :return: Список связей пользователей с курсом.
        """
        return await self.repo.get_course_users(db, course_id, limit, offset)
