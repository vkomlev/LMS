"""tsk-301 Фаза 1: инварианты схемы подписной модели и полнота тарифной сетки.

Проверяется не наличие таблиц (это видно и глазами), а ровно то, что легко
сломать незаметно:

1. **Матрица прав.** Девять планов сверяются по ЗНАЧЕНИЯМ с матрицей контракта
   §2. Тест на «таблица существует» пропустил бы перепутанные права — например
   `code_review` у Demo, что нарушает обещание «токены не расходуем».
2. **Полнота сетки.** У каждого платного плана обязана быть тарифная группа, а
   у группы — ступень под нужную частоту. Сид ищет группы по имени; опечатка в
   имени оставила бы план без денег молча (`pricing_group_id IS NULL` — это
   валидное состояние для Test/Demo/Выпускник, и отличить его от опечатки может
   только явная проверка).
3. **Один действующий тариф на ученика.** Инвариант держит частичный уникальный
   индекс, а не код: код проверял бы его гонкой между SELECT и INSERT.
4. **Пустые строки.** Набор «пустых» значений включает табуляцию и перевод
   строки — `length(btrim(x)) > 0` их пропускает, потому что `btrim` без второго
   аргумента срезает только пробелы (урок tsk-303 от 2026-08-06).
5. **Идемпотентность пакета.** Повторный номер платежа ЮKassa не создаёт второй
   грант — иначе повторная доставка уведомления удваивала бы купленное.

Тесты идут внутри общей откатываемой транзакции (savepoint'ы для нарушений
ограничений), поэтому за собой ничего не чистят.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


#: Матрица контракта §2: code → (лимит наставника, оценка кода, эскалация,
#: занятия, контент, имя тарифной группы или None).
_MATRIX: dict[str, tuple[int | None, bool, bool, bool, str, str | None]] = {
    "test":        (None, True,  True,  False, "full", None),
    "demo":        (0,    False, False, False, "demo", None),
    "self":        (0,    False, False, False, "full", "Self"),
    "ai":          (40,   True,  False, False, "full", "AI"),
    "base":        (100,  True,  True,  True,  "full", "Базовый 2026"),
    "base_legacy": (100,  True,  True,  True,  "full", "Базовый"),
    "adults":      (100,  True,  True,  True,  "full", "Обучение взрослых"),
    "flagship":    (None, True,  True,  True,  "full", "ИИ-предприниматель"),
    "alumni":      (0,    False, False, False, "full", None),
}

#: Ступени, которые обязаны быть в сетке после досева: группа → частота → ₽.
_GRID_EXPECTED: dict[str, dict[str, int]] = {
    "Базовый":        {"1": 2750, "2": 5500, "3": 7750},
    "Базовый 2026":   {"1": 3000, "2": 6000, "3": 9000},
    "Обучение взрослых": {"1": 3500, "2": 7000},
}

#: Группы с единственным вариантом без оси: имя → ₽.
_FLAT_EXPECTED: dict[str, int] = {"Self": 1000, "AI": 1500}


@pytest_asyncio.fixture(scope="function")
async def student_id(db: AsyncSession) -> int:
    """Ученик под FK. Почта уникальна — иначе тест ловит чужой partial unique."""
    return (
        await db.execute(
            text(
                "INSERT INTO users (full_name, email, is_active) "
                "VALUES ('tsk301 ученик подписки', :email, true) RETURNING id"
            ),
            {"email": f"tsk301-{uuid.uuid4().hex[:12]}@example.test"},
        )
    ).scalar_one()


@pytest_asyncio.fixture(scope="function")
async def plan_id(db: AsyncSession) -> int:
    """Любой засеянный план — для строк подписки."""
    value = (
        await db.execute(text("SELECT id FROM subscription_plan WHERE code = 'self'"))
    ).scalar()
    assert value is not None, "план self не засеян — миграция tsk301 не применена"
    return int(value)


# ─────────────────────────── 1. Матрица прав ────────────────────────────────


async def test_all_nine_plans_seeded(db: AsyncSession) -> None:
    codes = {
        row[0]
        for row in (
            await db.execute(text("SELECT code FROM subscription_plan"))
        ).all()
    }
    assert codes == set(_MATRIX), "состав планов разошёлся с матрицей контракта §2"


@pytest.mark.parametrize("code", sorted(_MATRIX))
async def test_plan_rights_match_contract_matrix(db: AsyncSession, code: str) -> None:
    """Права сверяются по значениям, а не по факту существования строки."""
    row = (
        await db.execute(
            text(
                "SELECT p.ai_tutor_limit, p.code_review, p.teacher_escalation, "
                "       p.lessons, p.content, g.name "
                "  FROM subscription_plan p "
                "  LEFT JOIN pricing_group g ON g.id = p.pricing_group_id "
                " WHERE p.code = :c"
            ),
            {"c": code},
        )
    ).first()
    assert row is not None, f"план {code} не засеян"
    assert tuple(row) == _MATRIX[code], f"права плана {code} разошлись с матрицей"


async def test_demo_and_self_do_not_spend_tokens(db: AsyncSession) -> None:
    """Обещание Demo «токены не расходуем» — отдельным тестом, не только матрицей.

    Это не дубль предыдущего: матрицу можно поправить целиком и «согласовать»
    дефект. Здесь зафиксировано само обещание из брифа.
    """
    rows = (
        await db.execute(
            text(
                "SELECT code, ai_tutor_limit, code_review FROM subscription_plan "
                " WHERE code IN ('demo', 'self', 'alumni')"
            )
        )
    ).all()
    assert len(rows) == 3
    for code, limit, review in rows:
        assert limit == 0, f"{code}: наставник обязан быть недоступен"
        assert review is False, f"{code}: оценка кода обязана быть недоступна"


# ─────────────────────────── 2. Полнота сетки ───────────────────────────────


@pytest.mark.parametrize("code", sorted(c for c, m in _MATRIX.items() if m[5]))
async def test_paid_plan_has_pricing_group(db: AsyncSession, code: str) -> None:
    """У платного плана обязана быть группа: NULL здесь неотличим от опечатки."""
    group_id = (
        await db.execute(
            text("SELECT pricing_group_id FROM subscription_plan WHERE code = :c"),
            {"c": code},
        )
    ).scalar()
    assert group_id is not None, (
        f"план {code} остался без тарифной группы — вероятно, имя группы в сиде "
        f"не совпало с фактическим"
    )


@pytest.mark.parametrize("group_name", sorted(_GRID_EXPECTED))
async def test_frequency_steps_complete(db: AsyncSession, group_name: str) -> None:
    rows = (
        await db.execute(
            text(
                "SELECT t.match_value, t.price_minor "
                "  FROM pricing_tariff t JOIN pricing_group g ON g.id = t.group_id "
                " WHERE g.name = :n AND t.is_active "
                "   AND t.match_kind = 'attendance_frequency'"
            ),
            {"n": group_name},
        )
    ).all()
    actual = {value: minor // 100 for value, minor in rows}
    assert actual == _GRID_EXPECTED[group_name], (
        f"ступени группы «{group_name}» разошлись с матрицей контракта"
    )


@pytest.mark.parametrize("group_name", sorted(_FLAT_EXPECTED))
async def test_flat_group_has_single_axisless_tariff(
    db: AsyncSession, group_name: str
) -> None:
    """Self и AI — один вариант без оси.

    Это не косметика: `_resolve_group_price` отдаёт цену безусловно только для
    единственного варианта с `match_kind IS NULL`. Появись рядом второй вариант
    или ось частоты — ученик без расписания получил бы `below_grid`, то есть
    начисление за подписку без занятий вообще не создалось бы.
    """
    rows = (
        await db.execute(
            text(
                "SELECT t.match_kind, t.price_minor "
                "  FROM pricing_tariff t JOIN pricing_group g ON g.id = t.group_id "
                " WHERE g.name = :n AND t.is_active"
            ),
            {"n": group_name},
        )
    ).all()
    assert len(rows) == 1, f"в группе «{group_name}» обязан быть ровно один вариант"
    match_kind, price_minor = rows[0]
    assert match_kind is None, "вариант обязан быть без оси, иначе цена не подберётся"
    assert price_minor // 100 == _FLAT_EXPECTED[group_name]


async def test_legacy_group_untouched(db: AsyncSession) -> None:
    """Существующие ступени группы «Базовый» не переписаны досевом.

    На них 37 живых начислений; сдвиг любой из них — молчаливое изменение денег.
    """
    rows = (
        await db.execute(
            text(
                "SELECT t.match_value, t.price_minor "
                "  FROM pricing_tariff t JOIN pricing_group g ON g.id = t.group_id "
                " WHERE g.name = 'Базовый' AND t.match_value IN ('1', '2')"
            )
        )
    ).all()
    assert {v: m // 100 for v, m in rows} == {"1": 2750, "2": 5500}


# ──────────────────── 3. Один действующий тариф на ученика ──────────────────


async def test_second_active_subscription_rejected(
    db: AsyncSession, student_id: int, plan_id: int
) -> None:
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
            "VALUES (:s, :p, CURRENT_DATE)"
        ),
        {"s": student_id, "p": plan_id},
    )
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
                    "VALUES (:s, :p, CURRENT_DATE)"
                ),
                {"s": student_id, "p": plan_id},
            )


async def test_closed_subscription_allows_new_one(
    db: AsyncSession, student_id: int, plan_id: int
) -> None:
    """Смена тарифа: закрыли прежнюю строку — новая проходит.

    Без этого теста «один действующий» мог бы оказаться «один за всю жизнь»,
    и смена тарифа падала бы на проде при первой же попытке.
    """
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, starts_on, ends_on) "
            "VALUES (:s, :p, CURRENT_DATE - 30, CURRENT_DATE - 1)"
        ),
        {"s": student_id, "p": plan_id},
    )
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
            "VALUES (:s, :p, CURRENT_DATE)"
        ),
        {"s": student_id, "p": plan_id},
    )
    active = (
        await db.execute(
            text(
                "SELECT count(*) FROM student_subscription "
                " WHERE student_id = :s AND ends_on IS NULL"
            ),
            {"s": student_id},
        )
    ).scalar()
    assert active == 1


async def test_end_before_start_rejected(
    db: AsyncSession, student_id: int, plan_id: int
) -> None:
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO student_subscription "
                    "  (student_id, plan_id, starts_on, ends_on) "
                    "VALUES (:s, :p, CURRENT_DATE, CURRENT_DATE - 1)"
                ),
                {"s": student_id, "p": plan_id},
            )


# ─────────────────────────── 4. Пустые строки ───────────────────────────────


@pytest.mark.parametrize("blank", ["", "   ", "\t\n", "\r"])
async def test_blank_plan_code_rejected(db: AsyncSession, blank: str) -> None:
    """Набор пустых значений включает табуляцию и перевод строки.

    `length(btrim(x)) > 0` пропустил бы `E'\\t\\n'`: btrim без второго аргумента
    срезает только пробелы. Ограничение написано как `~ '\\S'` (урок tsk-303).
    """
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO subscription_plan (code, name) VALUES (:c, 'x')"
                ),
                {"c": blank},
            )


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
async def test_blank_plan_name_rejected(db: AsyncSession, blank: str) -> None:
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO subscription_plan (code, name) VALUES (:c, :n)"
                ),
                {"c": f"tsk301-blank-{uuid.uuid4().hex[:8]}", "n": blank},
            )


# ───────────────────── 5. Квота и пакеты наставника ─────────────────────────


async def test_duplicate_gateway_payment_rejected(
    db: AsyncSession, student_id: int
) -> None:
    """Повторная доставка уведомления ЮKassa не удваивает пакет."""
    payment_id = f"tsk301-{uuid.uuid4().hex[:16]}"
    await db.execute(
        text(
            "INSERT INTO student_ai_grant (student_id, granted, gateway_payment_id) "
            "VALUES (:s, 40, :p)"
        ),
        {"s": student_id, "p": payment_id},
    )
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO student_ai_grant "
                    "  (student_id, granted, gateway_payment_id) "
                    "VALUES (:s, 40, :p)"
                ),
                {"s": student_id, "p": payment_id},
            )


async def test_manual_grants_allow_many_nulls(
    db: AsyncSession, student_id: int
) -> None:
    """Пакеты, выданные персоналом вручную, номера платежа не имеют.

    UNIQUE в PG не считает NULL дублями — фиксируем это как требование, иначе
    вторая ручная выдача упала бы, и дефект нашёлся бы только у оператора.
    """
    for _ in range(2):
        await db.execute(
            text("INSERT INTO student_ai_grant (student_id, granted) VALUES (:s, 10)"),
            {"s": student_id},
        )
    count = (
        await db.execute(
            text("SELECT count(*) FROM student_ai_grant WHERE student_id = :s"),
            {"s": student_id},
        )
    ).scalar()
    assert count == 2


async def test_grant_used_cannot_exceed_granted(
    db: AsyncSession, student_id: int
) -> None:
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO student_ai_grant (student_id, granted, used) "
                    "VALUES (:s, 10, 11)"
                ),
                {"s": student_id},
            )


async def test_quota_unique_per_month(db: AsyncSession, student_id: int) -> None:
    period = date(2026, 8, 1)
    await db.execute(
        text("INSERT INTO student_ai_quota (student_id, period) VALUES (:s, :p)"),
        {"s": student_id, "p": period},
    )
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO student_ai_quota (student_id, period) VALUES (:s, :p)"
                ),
                {"s": student_id, "p": period},
            )
