"""tsk-301: слияние учёток переносит тариф, квоту и пакеты.

**Найдено на проде 2026-08-14, а не тестом.** Слияние переносило расписание и
курс, но не подписку: старый аккаунт с `base_legacy` уходил в неактивные, новый
оставался с `demo`, выданным при регистрации. `demo` денежной привязки не имеет и
перекрывает группу курса — ученик, ходивший два раза в неделю, остался вообще без
начисления. Дыру создали два правила вместе: «подписка перекрывает группу курса»
и «регистрация даёт demo».

Класс ошибки: **новая таблица участвует в личности пользователя, а путь слияния о
ней не знает**. Он не падает и не жалуется — просто теряет данные.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import user_merge_service

pytestmark = pytest.mark.asyncio


async def _user(db: AsyncSession) -> int:
    return int(
        (
            await db.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES ('tsk301 слияние', :e, true) RETURNING id"
                ),
                {"e": f"tsk301-merge-{uuid.uuid4().hex[:12]}@example.test"},
            )
        ).scalar_one()
    )


async def _subscribe(db: AsyncSession, student_id: int, plan_code: str) -> None:
    await db.execute(
        text(
            "INSERT INTO student_subscription "
            "  (student_id, plan_id, pricing_group_id, starts_on) "
            "SELECT :s, id, pricing_group_id, CURRENT_DATE "
            "  FROM subscription_plan WHERE code = :c"
        ),
        {"s": student_id, "c": plan_code},
    )


async def _plan_of(db: AsyncSession, student_id: int) -> str | None:
    return (
        await db.execute(
            text(
                "SELECT p.code FROM student_subscription s "
                "  JOIN subscription_plan p ON p.id = s.plan_id "
                " WHERE s.student_id = :s AND s.ends_on IS NULL"
            ),
            {"s": student_id},
        )
    ).scalar()


@pytest_asyncio.fixture(scope="function")
async def pair(db: AsyncSession) -> tuple[int, int]:
    """(источник, цель) — как при повторной регистрации живого ученика."""
    return await _user(db), await _user(db)


async def test_source_plan_wins_over_auto_demo(
    db: AsyncSession, pair: tuple[int, int]
) -> None:
    """Тариф источника вытесняет автоматический `demo` цели.

    Ровно случай Грабовского: давний клиент перерегистрировался, новая учётка
    получила `demo`, слияние перенесло расписание — и человек остался без денег
    и без прав.
    """
    source, target = pair
    await _subscribe(db, source, "base_legacy")
    await _subscribe(db, target, "demo")

    await user_merge_service._move_subscription(db, source, target)

    assert await _plan_of(db, target) == "base_legacy"
    assert await _plan_of(db, source) is None, "у источника не должно остаться действующей"


async def test_deliberate_target_plan_is_kept(
    db: AsyncSession, pair: tuple[int, int]
) -> None:
    """Осознанно выбранный тариф цели слияние не переписывает.

    Обратное направление того же правила: `demo` уступает, потому что он не
    выбран, а проставлен автоматически. Всё остальное — решение человека.
    """
    source, target = pair
    await _subscribe(db, source, "demo")
    await _subscribe(db, target, "flagship")

    await user_merge_service._move_subscription(db, source, target)

    assert await _plan_of(db, target) == "flagship"


async def test_single_active_subscription_after_merge(
    db: AsyncSession, pair: tuple[int, int]
) -> None:
    """После слияния действующая подписка ровно одна, история цела.

    Инвариант держит частичный уникальный индекс — если бы перенос забыл закрыть
    одну из строк, слияние упало бы прямо здесь.
    """
    source, target = pair
    await _subscribe(db, source, "base_legacy")
    await _subscribe(db, target, "demo")

    await user_merge_service._move_subscription(db, source, target)

    active = (
        await db.execute(
            text(
                "SELECT count(*) FROM student_subscription "
                " WHERE student_id = :s AND ends_on IS NULL"
            ),
            {"s": target},
        )
    ).scalar()
    total = (
        await db.execute(
            text("SELECT count(*) FROM student_subscription WHERE student_id = :s"),
            {"s": target},
        )
    ).scalar()
    assert (active, total) == (1, 2), "история обеих учёток обязана сохраниться"


async def test_quota_is_summed_not_dropped(
    db: AsyncSession, pair: tuple[int, int]
) -> None:
    """Расход обеих учёток за один месяц — расход одного человека.

    Простой перенос уронил бы строку цели на уникальности `(ученик, месяц)` либо
    потерял бы расход источника, и лимит молча обнулился бы наполовину.
    """
    source, target = pair
    period = date.today().replace(day=1)
    for student_id, used in ((source, 7), (target, 5)):
        await db.execute(
            text(
                "INSERT INTO student_ai_quota (student_id, period, used) "
                "VALUES (:s, :p, :u)"
            ),
            {"s": student_id, "p": period, "u": used},
        )

    await user_merge_service._move_subscription(db, source, target)

    rows = (
        await db.execute(
            text("SELECT period, used FROM student_ai_quota WHERE student_id = :s"),
            {"s": target},
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].used == 12, "расход сложился неверно"


async def test_grants_move_to_target(db: AsyncSession, pair: tuple[int, int]) -> None:
    """Купленные пакеты переезжают: они оплачены человеком, а не учёткой."""
    source, target = pair
    await db.execute(
        text("INSERT INTO student_ai_grant (student_id, granted) VALUES (:s, 40)"),
        {"s": source},
    )

    await user_merge_service._move_subscription(db, source, target)

    moved = (
        await db.execute(
            text("SELECT count(*) FROM student_ai_grant WHERE student_id = :s"),
            {"s": target},
        )
    ).scalar()
    assert moved == 1


async def test_merge_without_any_subscription_is_noop(
    db: AsyncSession, pair: tuple[int, int]
) -> None:
    """Ни у кого нет подписки — перенос просто ничего не делает."""
    source, target = pair
    await user_merge_service._move_subscription(db, source, target)
    assert await _plan_of(db, target) is None
