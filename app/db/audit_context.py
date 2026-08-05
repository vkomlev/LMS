from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def set_audit_actor(db: AsyncSession, actor: str) -> None:
    """
    Проставляет session-var ``app.audit_actor`` (tsk-114) для текущей
    транзакции. Читается триггером ``log_task_audit`` на ``tasks`` как
    ``changed_by`` в ``task_audit``.

    ``is_local=true`` — тот же принцип изоляции, что у
    ``app.skip_task_order_trigger``: значение видно только текущей
    транзакции и сбрасывается на ``COMMIT``/``ROLLBACK``, не утекает в
    другие сессии пула. Вызывающий код обязан звать это ПЕРЕД каждой
    мутацией, если между ними есть промежуточный commit (см.
    ``TasksService.bulk_upsert``, где ``repo.create``/``repo.update``
    коммитят построчно) — иначе метка проживёт только первую транзакцию.

    Soft-fail: сбой простановки метки не должен ронять саму мутацию —
    в худшем случае ``changed_by`` останется NULL, а ``db_role`` в
    ``task_audit`` всё равно даст независимый от кооперации след.
    """
    try:
        await db.execute(
            text("SELECT set_config('app.audit_actor', :actor, true)"),
            {"actor": actor},
        )
    except Exception:
        logger.warning(
            "tsk-114: не удалось установить app.audit_actor=%s", actor, exc_info=True
        )
