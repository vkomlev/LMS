"""tsk-301 Фаза 7: покупка пакета обращений к наставнику.

Главное здесь — **пробел П6 контракта: «деньги списаны, грант не создан»**.
Он не про красоту кода: человек заплатил, а получил ноль, и узнать об этом можно
только по его жалобе. Поэтому проверяется не «зачисление работает», а поведение
на сбоях: повтор доставки не удваивает пакет, а ошибка зачисления возвращает 5xx,
чтобы платёжный сервис повторил и пакет доехал сам.

Форма проверок — по ТЕЛУ ответа эндпоинта, не по схемам (урок tsk-302).
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import payments_gateway
from app.services import subscription_service as subs

pytestmark = pytest.mark.asyncio


class _Payment:
    """Ответ шлюза в том виде, в каком его читает обработчик уведомления."""

    def __init__(self, payment_id: str, metadata: dict) -> None:
        self.id = payment_id
        self.metadata = metadata
        self.status = "succeeded"
        self.paid = True
        self.amount_minor = 50000
        self.test = True


async def _student(db: AsyncSession) -> int:
    return int(
        (
            await db.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES ('tsk301 пакет', :e, true) RETURNING id"
                ),
                {"e": f"tsk301-pkg-{uuid.uuid4().hex[:12]}@example.test"},
            )
        ).scalar_one()
    )


async def _grants(db: AsyncSession, student_id: int) -> list[tuple[int, int]]:
    rows = (
        await db.execute(
            text(
                "SELECT granted, used FROM student_ai_grant "
                " WHERE student_id = :s ORDER BY id"
            ),
            {"s": student_id},
        )
    ).all()
    return [(r.granted, r.used) for r in rows]


@pytest_asyncio.fixture(scope="function")
async def student(db: AsyncSession) -> int:
    return await _student(db)


# ───────────────────────── Зачисление пакета ────────────────────────────────


async def test_package_is_granted(db: AsyncSession, student: int) -> None:
    created = await subs.grant_ai_package(
        db, student, units=40, gateway_payment_id=f"pay-{uuid.uuid4().hex[:10]}"
    )
    assert created is True
    assert await _grants(db, student) == [(40, 0)]


async def test_repeated_delivery_does_not_double_grant(
    db: AsyncSession, student: int
) -> None:
    """Повтор доставки того же платежа не удваивает пакет.

    Платёжный сервис повторяет уведомление, пока не получит 200. Без
    уникальности человек получил бы два пакета за одну оплату — и заметили бы
    это не мы.
    """
    payment_id = f"pay-{uuid.uuid4().hex[:10]}"
    first = await subs.grant_ai_package(
        db, student, units=40, gateway_payment_id=payment_id
    )
    second = await subs.grant_ai_package(
        db, student, units=40, gateway_payment_id=payment_id
    )
    assert (first, second) == (True, False)
    assert await _grants(db, student) == [(40, 0)], "пакет зачислен дважды"


async def test_manual_grants_are_not_deduplicated(
    db: AsyncSession, student: int
) -> None:
    """Две ручные выдачи — это две разные договорённости, а не повтор.

    У них нет номера платежа, значит и идемпотентности быть не может: схлопнуть
    их означало бы потерять вторую выдачу.
    """
    for _ in range(2):
        assert await subs.grant_ai_package(db, student, units=10) is True
    assert len(await _grants(db, student)) == 2


async def test_zero_units_rejected(db: AsyncSession, student: int) -> None:
    with pytest.raises(ValueError):
        await subs.grant_ai_package(db, student, units=0)


# ──────────────── Уведомление шлюза: тело ответа и сбои ─────────────────────


async def test_webhook_records_package(db: AsyncSession, student: int) -> None:
    payment = _Payment(
        f"pay-{uuid.uuid4().hex[:10]}",
        {"purpose": "ai_package", "student_id": str(student), "units": "40"},
    )
    result = await payments_gateway._record_ai_package(db, payment)
    assert result == {"status": "recorded"}
    assert await _grants(db, student) == [(40, 0)]


async def test_webhook_second_delivery_says_already(
    db: AsyncSession, student: int
) -> None:
    payment = _Payment(
        f"pay-{uuid.uuid4().hex[:10]}",
        {"purpose": "ai_package", "student_id": str(student), "units": "40"},
    )
    await payments_gateway._record_ai_package(db, payment)
    again = await payments_gateway._record_ai_package(db, payment)
    assert again == {"status": "already_recorded"}
    assert len(await _grants(db, student)) == 1


async def test_webhook_failure_asks_for_retry(
    db: AsyncSession, student: int, monkeypatch
) -> None:
    """Сбой зачисления отвечает 5xx, а не 200.

    Это и есть закрытие пробела П6: 200 означал бы «мы всё сделали», сервис
    больше не повторит, и оплаченный пакет исчезнет молча.
    """
    from fastapi import HTTPException

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("БД недоступна")

    monkeypatch.setattr(subs, "grant_ai_package", _boom)
    payment = _Payment(
        f"pay-{uuid.uuid4().hex[:10]}",
        {"purpose": "ai_package", "student_id": str(student), "units": "40"},
    )
    with pytest.raises(HTTPException) as exc:
        await payments_gateway._record_ai_package(db, payment)
    assert exc.value.status_code >= 500, "сбой обязан просить о повторной доставке"


@pytest.mark.parametrize(
    "metadata",
    [
        {"purpose": "ai_package", "units": "40"},                    # нет ученика
        {"purpose": "ai_package", "student_id": "1"},                # нет объёма
        {"purpose": "ai_package", "student_id": "1", "units": "0"},  # пустой пакет
    ],
)
async def test_webhook_ignores_unusable_metadata(
    db: AsyncSession, metadata: dict
) -> None:
    """Непригодное уведомление не просит повтора: чинить его нечем.

    Повторять доставку бессмысленно — в ответе шлюза нет, кому и сколько
    зачислять. Отвечаем 200, но в лог уходит ошибка.
    """
    payment = _Payment(f"pay-{uuid.uuid4().hex[:10]}", metadata)
    assert await payments_gateway._record_ai_package(db, payment) == {"status": "ignored"}


# ──────────────────── Кому пакет вообще продаётся ───────────────────────────


@pytest.mark.parametrize(
    "plan_code,sellable",
    [
        ("ai", True),           # лимит 40 — пакет имеет смысл
        ("base", True),         # лимит 100
        ("demo", False),        # наставника нет вовсе
        ("alumni", False),      # то же
        ("test", False),        # безлимит
        ("flagship", False),    # безлимит
    ],
)
async def test_package_is_offered_only_where_it_helps(
    db: AsyncSession, student: int, plan_code: str, sellable: bool
) -> None:
    """Пакет продаётся только там, где он что-то даёт.

    Признак — наличие ЧИСЛЕННОГО лимита, а не `allowed`: при исчерпанном лимите
    `allowed` тоже False, но там пакет как раз и нужен.
    """
    from app.services import entitlements_service as ent

    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
            "SELECT :s, id, CURRENT_DATE FROM subscription_plan WHERE code = :c"
        ),
        {"s": student, "c": plan_code},
    )
    decision = await ent.check(db, student_id=student, capability="ai_tutor")
    assert (decision.limit is not None) is sellable
