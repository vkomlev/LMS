# app/repos/tasks_repo.py

from typing import Dict, List, Optional, Set
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tasks import Tasks
from app.repos.base import BaseRepository


class TasksRepository(BaseRepository[Tasks]):
    """
    Репозиторий для заданий.
    """
    def __init__(self) -> None:
        super().__init__(Tasks)

    async def list_ids_by_course(
        self,
        db: AsyncSession,
        course_id: int,
    ) -> Set[int]:
        """Множество id всех заданий курса. Используется в reorder-валидации."""
        stmt = select(Tasks.id).where(Tasks.course_id == course_id)
        rows = (await db.execute(stmt)).all()
        return {int(r[0]) for r in rows}

    async def reorder_tasks(
        self,
        db: AsyncSession,
        course_id: int,
        task_orders: List[Dict[str, int]],
    ) -> List[Tasks]:
        """
        Массовое изменение порядка заданий курса.

        Отключает триггер ``trg_set_task_order_position`` на время операции через
        session-variable ``app.skip_task_order_trigger`` (`is_local=true` —
        действие в пределах текущей транзакции), затем выполняет
        ``UPDATE order_position`` для каждой пары ``(task_id, order_position)``.
        Коммитит транзакцию в конце. Возвращает обновлённые задания,
        отсортированные по ``order_position NULLS LAST``.

        Зеркало ``MaterialsRepository.reorder_materials`` (см.
        ``app/repos/materials_repo.py``); описание session-var — раздел 15
        ``docs/database-triggers-contract.md``.
        """
        if not task_orders:
            return []

        await db.execute(
            text("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
        )
        for item in task_orders:
            tid = item["task_id"]
            pos = item["order_position"]
            await db.execute(
                update(Tasks)
                .where(Tasks.id == tid, Tasks.course_id == course_id)
                .values(order_position=pos)
            )
        await db.commit()

        task_ids = [item["task_id"] for item in task_orders]
        stmt = (
            select(Tasks)
            .where(Tasks.course_id == course_id, Tasks.id.in_(task_ids))
            .order_by(Tasks.order_position.asc().nulls_last(), Tasks.id.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())