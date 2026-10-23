"""tsk-997: учебные достижения ученика для чтения (ученик, преподаватель).

Только учебные достижения (`achievements_engine.LEARNING_TYPES`): вехи
удержания ученик уже видит в своём блоке (`GET /me/retention`), дублировать
их здесь — показать одно и то же дважды.

Чтение без побочных эффектов: фиксацию делает фоновый тик
(`retention_achievements_cron_service`). Выполненное, но ещё не
зафиксированное достижение видно сразу, с ``earned_at = None`` — тот же
приём, что в `retention_service.get_retention`.

Наружу — только числа одного ученика. Баллы (`reward_points`) не отдаются:
у учебных достижений их нет, и валюты наград мы не заводим.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import achievements_engine, retention_service


async def get_student_achievements(db: AsyncSession, *, student_id: int) -> dict[str, Any]:
    """Полученные учебные достижения и ближайшее невыполненное по каждому виду.

    «Ближайшее» — с наименьшим порогом среди невыполненных того же вида:
    «Довёл до верного: 2 из 5», а не все ступени сразу."""
    catalog = await retention_service.load_catalog(db, types=achievements_engine.LEARNING_TYPES)
    facts = (await achievements_engine.load_learning_facts(db, student_ids=[student_id]))[student_id]
    earned_at = await retention_service.load_earned_at(db, student_ids=[student_id])

    earned: list[dict[str, Any]] = []
    nearest: dict[str, dict[str, Any]] = {}
    for item in catalog:
        aid = int(item["id"])
        fixed_at = earned_at.get((student_id, aid))
        current, target = achievements_engine.progress_of(item["condition"], facts)
        base = {
            "id": aid,
            "name": item["name"],
            "description": item["description"],
            "badge_image_url": item["badge_image_url"],
        }
        # Зафиксированное остаётся полученным, даже если основание потом
        # исчезло (неверный повтор сбросил стадию) — веха однократна.
        if fixed_at is not None or achievements_engine.is_earned(item["condition"], facts):
            earned.append({**base, "earned_at": fixed_at})
            continue
        if current is None or target is None:
            continue
        ctype = achievements_engine.condition_type(item["condition"]) or ""
        prev = nearest.get(ctype)
        if prev is None or target < prev["target"]:
            nearest[ctype] = {**base, "current": current, "target": target}

    return {
        "earned": earned,
        "next": sorted(nearest.values(), key=lambda n: (n["target"] - n["current"]) / n["target"]),
    }
