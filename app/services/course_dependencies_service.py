# app/services/course_dependencies_service.py

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courses import Courses
from app.repos.course_dependencies_repository import CourseDependenciesRepository
from app.services import course_dependencies_enrollment_service
from app.services.learning_engine_service import LearningEngineService


class CourseDependenciesService:
    """
    Сервис для бизнес-логики работы с зависимостями курсов.
    """
    def __init__(
        self,
        repo: CourseDependenciesRepository = None,
        engine: Optional[LearningEngineService] = None,
    ):
        self.repo = repo or CourseDependenciesRepository()
        # tsk-541: пересчёт кеша student_course_state для узла, ставшего
        # required_course_id, сразу при записи зависимости — см. add_dependency.
        self.engine = engine or LearningEngineService()

    async def list_dependencies(
        self, db: AsyncSession, course_id: int
    ) -> List[Courses]:
        return await self.repo.list_dependencies(db, course_id)

    async def _enroll_existing_students(
        self, db: AsyncSession, course_id: int, required_course_id: int,
        auto_assign: bool = True,
    ) -> int:
        """tsk-231: доназначить required_course_id ученикам, уже зачисленным
        на course_id, в момент ДОБАВЛЕНИЯ зависимости к уже идущему курсу.

        При `auto_assign=False` (фаза 6) не делает ничего: точечную зависимость
        методист выдаёт адресно. Ранний выход тут — экономия, а не защита:
        durable-фильтр стоит в SQL `collect_required_course_ids`, поэтому даже
        без этой ветки цикл не назначил бы точечный курс никому. Без раннего
        выхода мы бы прогнали пустой запрос по каждому ученику курса.

        tsk-261 закрыл симметричный путь — доназначение зависимостей в
        момент НАЗНАЧЕНИЯ курса ученику (`user_courses_service`,
        `assignment_rules_service`). Но методист чаще добавляет зависимость
        к курсу, который уже идёт (на нём уже есть ученики) — этот путь
        (`add_dependency`/`bulk_add_dependencies`) раньше доназначения не
        делал вовсе: `_BLOCKED_COURSES_SQL`/`resolve_next_item` блокируют
        таких учеников немедленно (после backfill_dependency_state выше), а
        required_course_id физически недостижим — его нет в их
        `user_courses`. Замок без выхода (тот же класс проблемы, что и
        tsk-261, только на другом пути записи).

        Переиспользует `ensure_dependencies_assigned` — та же идемпотентность
        (`INSERT ... ON CONFLICT DO NOTHING`, атомарно на уровне БД без
        отдельного lock'а) и пропуск некорневых required-курсов, что и в
        существующих вызовах (tsk-261).

        Остаточный риск: если цикл прервётся исключением на каком-то
        студенте, уже обработанные до него доназначения останутся
        незакоммиченными (общая транзакция с `add_dependency`) и откатятся
        вместе с ней — НО сама зависимость в `course_dependencies` к этому
        моменту уже закоммичена репозиторием (`repo.add_dependency` коммитит
        сама). В отличие от кеша `student_course_state` (чинит фоновый тик
        `course_dependency_state_cron_service`), отсутствующее зачисление
        сам себя не лечит — повторный вызов add_dependency идемпотентен и
        закрывает пробел вручную. См. review Фазы 1.

        Returns:
            Число студентов, которым реально доназначен курс.
        """
        if not auto_assign:
            return 0
        student_ids = await self.engine.list_active_students_with_node_in_tree(
            db, course_id
        )
        enrolled_count = 0
        for student_id in student_ids:
            assigned = await course_dependencies_enrollment_service.ensure_dependencies_assigned(
                db, student_id=student_id, course_ids=[course_id]
            )
            if assigned:
                enrolled_count += 1
        return enrolled_count

    async def count_affected_students(
        self, db: AsyncSession, course_id: int, auto_assign: bool = True
    ) -> int:
        """tsk-231: сколько уже зачисленных на course_id студентов мгновенно
        заблокирует добавление НОВОЙ зависимости (превью для confirm-диалога
        методиста, до фактического add_dependency).

        Считает тем же критерием, что и `_enroll_existing_students` —
        активные студенты, у кого course_id входит в дерево (не только
        прямое зачисление на сам course_id).

        Фаза 6: у точечной зависимости (`auto_assign=False`) мгновенно
        заблокированных нет вовсе — требуемый курс никому не выдаётся, а
        блокирует он только назначенных. Превью обязано это отражать: цифра
        «заблокирует 35» на связке, которая не заблокирует никого, отпугнула бы
        методиста ровно от того сценария, ради которого флаг и вводился.
        """
        if not auto_assign:
            return 0
        student_ids = await self.engine.list_active_students_with_node_in_tree(
            db, course_id
        )
        return len(student_ids)

    async def add_dependency(
        self, db: AsyncSession, course_id: int, required_course_id: int,
        auto_assign: bool = True,
    ) -> None:
        await self.repo.add_dependency(
            db, course_id, required_course_id, auto_assign=auto_assign
        )
        # tsk-541: без этого student_course_state для required_course_id не
        # пишет никто до следующего прогона фонового тика — активный студент,
        # уже прошедший пререквизит, видит новую зависимость как блокировку
        # (ровно регрессия tsk-523).
        await self.engine.backfill_dependency_state(db, course_id, required_course_id)
        # tsk-231: см. docstring _enroll_existing_students.
        await self._enroll_existing_students(
            db, course_id, required_course_id, auto_assign=auto_assign
        )
        await db.commit()

    async def remove_dependency(
        self, db: AsyncSession, course_id: int, required_course_id: int
    ) -> None:
        await self.repo.remove_dependency(db, course_id, required_course_id)

    async def bulk_add_dependencies(
        self, db: AsyncSession, course_id: int, required_course_ids: List[int],
        auto_assign: bool = True,
    ) -> List[Courses]:
        """
        Массовое добавление зависимостей для курса.

        :param db: асинхронная сессия БД.
        :param course_id: ID курса.
        :param required_course_ids: Список ID курсов-зависимостей.
        :param auto_assign: False — точечные зависимости (tsk-231): требуемые
            курсы не раздаются автоматически и блокируют только адресатов.
        :return: Список успешно добавленных зависимостей.
        """
        added = await self.repo.bulk_add_dependencies(
            db, course_id, required_course_ids, auto_assign=auto_assign
        )
        # tsk-541: тот же бэкфилл, что в add_dependency — по одному пересчёту
        # на каждую реально добавленную зависимость (пропущенные конфликты/
        # self-dependency уже отфильтрованы репозиторием).
        for dep in added:
            await self.engine.backfill_dependency_state(db, course_id, dep.id)
            # tsk-231: см. docstring _enroll_existing_students.
            await self._enroll_existing_students(
                db, course_id, dep.id, auto_assign=auto_assign
            )
        await db.commit()
        return added