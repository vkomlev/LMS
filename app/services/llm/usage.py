"""Учёт расхода LLM (tsk-572 этап 1).

Контракт: docs/specs/2026-08-06-contract-llm-client.md, §8.
Счётчик включаем с первого дня, лимиты — нет: это основа будущих подписных
тарифов и общий инструмент обеих задач (наставник и судья ИИ-авторства).

Ключевое свойство: **запись идёт в СВОЕЙ сессии, вне транзакции потребителя**.
Сбой учёта не должен ронять ни приём ответа ученика (tsk-302), ни диалог с
наставником (tsk-572) — деньги мы считаем, но не ценой отказа в обучении.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text

from app.db.session import async_session_factory
from app.services.llm.contracts import UsageRecord

logger = logging.getLogger(__name__)

_INSERT_SQL = text("""
    INSERT INTO llm_usage_event (
        purpose, student_id, model, provider,
        tokens_in, tokens_out, duration_ms, outcome, meta
    ) VALUES (
        :purpose, :student_id, :model, :provider,
        :tokens_in, :tokens_out, :duration_ms, :outcome, CAST(:meta AS jsonb)
    )
""")


async def record(event: UsageRecord) -> None:
    """Записать факт вызова. Никогда не поднимает исключение наружу.

    Собственная сессия — не только ради изоляции от отката потребителя: у
    неуспешных вызовов запись нужна ОСОБЕННО (по ней видно, куда уходят деньги
    и как часто отбивает квота), а транзакция потребителя в этот момент может
    как раз откатываться из-за той же ошибки.
    """
    try:
        async with async_session_factory() as db:
            await db.execute(_INSERT_SQL, {
                "purpose": event.purpose,
                "student_id": event.student_id,
                "model": event.model,
                "provider": event.provider,
                "tokens_in": event.tokens_in,
                "tokens_out": event.tokens_out,
                "duration_ms": event.duration_ms,
                "outcome": event.outcome,
                "meta": json.dumps(event.meta, ensure_ascii=False),
            })
            await db.commit()
    except Exception:  # noqa: BLE001 — учёт не имеет права ронять вызывающего
        logger.exception(
            "LLM: не удалось записать расход (purpose=%s, model=%s, outcome=%s) — "
            "вызов при этом состоялся, теряется только строка учёта",
            event.purpose, event.model, event.outcome,
        )
