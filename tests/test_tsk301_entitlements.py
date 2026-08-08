"""tsk-301: поведение единой двери прав (контракт §3-§6).

Проверяется таблица исходов целиком, а не «пускает / не пускает». Смысл в том,
что **«прав нет» и «проверка сломалась» — разные ответы**: сведение их к общему
`False` и есть тот дефект, из-за которого анонимный посетитель 464 демо-курсов
жёг бы токены (пробел П8, прецедент — `CurrentUser(id=0)` в tsk-572).

Отдельно закреплено обратное направление: **ошибка в НАШЕЙ логике доступ не
даёт**. Fail-open только на закрытом перечне инфраструктурных исключений — иначе
первая же опечатка превращается в молча открытую дверь, неотличимую от штатной
работы.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import entitlements_service as ent

pytestmark = pytest.mark.asyncio


async def _make_student(db: AsyncSession) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO users (full_name, email, is_active) "
                "VALUES ('tsk301 ученик прав', :e, true) RETURNING id"
            ),
            {"e": f"tsk301-ent-{uuid.uuid4().hex[:12]}@example.test"},
        )
    ).scalar_one()


async def _subscribe(db: AsyncSession, student_id: int, plan_code: str) -> None:
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
            "SELECT :s, id, CURRENT_DATE FROM subscription_plan WHERE code = :c"
        ),
        {"s": student_id, "c": plan_code},
    )


@pytest_asyncio.fixture(scope="function")
async def student(db: AsyncSession) -> int:
    return await _make_student(db)


# ───────────────────── «Ученик не опознан» — всегда fail-closed ─────────────


@pytest.mark.parametrize("who", [None, 0])
@pytest.mark.parametrize("capability", list(ent.GATED_CAPABILITIES))
async def test_unknown_student_denied(db: AsyncSession, who, capability: str) -> None:
    """Гость, `id=0` и сервисный вызов без ученика — валидный отказ, не сбой.

    Именно здесь ломался tsk-572: `CurrentUser(id=0)` у сервисного вызова
    трактовался как обычный пользователь.
    """
    decision = await ent.check(db, student_id=who, capability=capability)
    assert decision.allowed is False
    assert decision.outcome == "denied_no_plan"


async def test_student_without_subscription_denied(
    db: AsyncSession, student: int
) -> None:
    """Ученик есть, подписки нет — тоже отказ, а не «пустим на всякий случай»."""
    decision = await ent.check(db, student_id=student, capability="code_review")
    assert (decision.allowed, decision.outcome) == (False, "denied_no_plan")


# ─────────────────────────── Права по плану ─────────────────────────────────


@pytest.mark.parametrize(
    "plan_code,capability,expected",
    [
        ("demo", "code_review", False),
        ("demo", "teacher_escalation", False),
        ("self", "code_review", False),
        ("self", "teacher_escalation", False),
        ("ai", "code_review", True),
        ("ai", "teacher_escalation", False),
        ("base", "code_review", True),
        ("base", "teacher_escalation", True),
        ("test", "teacher_escalation", True),
        ("alumni", "code_review", False),
        ("flagship", "teacher_escalation", True),
    ],
)
async def test_binary_capability_matches_matrix(
    db: AsyncSession, student: int, plan_code: str, capability: str, expected: bool
) -> None:
    await _subscribe(db, student, plan_code)
    decision = await ent.check(db, student_id=student, capability=capability)
    assert decision.allowed is expected
    assert decision.outcome == ("allowed" if expected else "denied_not_in_plan")


async def test_denied_carries_upgrade_hint(db: AsyncSession, student: int) -> None:
    """На отказе обязана быть подсказка апгрейда, а не голое «недоступно».

    Это не косметика: заблокированная кнопка без объяснения — тупик, а с
    объяснением — шаг воронки.
    """
    await _subscribe(db, student, "self")
    decision = await ent.check(db, student_id=student, capability="ai_tutor")
    assert decision.allowed is False
    assert decision.upgrade_hint, "план Self обязан объяснять, что даёт апгрейд"


async def test_unlimited_plan_has_no_limit(db: AsyncSession, student: int) -> None:
    await _subscribe(db, student, "flagship")
    decision = await ent.check(db, student_id=student, capability="ai_tutor")
    assert (decision.allowed, decision.limit) == (True, None)


async def test_unknown_capability_raises(db: AsyncSession, student: int) -> None:
    """Опечатка в ключе не должна молча означать «разрешено»."""
    with pytest.raises(ValueError):
        await ent.check(db, student_id=student, capability="ai_tutorr")


# ─────────────────── Счётная возможность: квота и пакеты ────────────────────


async def test_quota_counts_down(db: AsyncSession, student: int) -> None:
    await _subscribe(db, student, "ai")  # лимит 40
    first = await ent.check(db, student_id=student, capability="ai_tutor")
    assert (first.limit, first.remaining) == (40, 40)

    reserved = await ent.check_and_reserve(db, student_id=student)
    assert reserved.allowed is True and reserved.remaining == 39

    after = await ent.check(db, student_id=student, capability="ai_tutor")
    assert after.remaining == 39


async def test_quota_exhausted_denies(db: AsyncSession, student: int) -> None:
    await _subscribe(db, student, "ai")
    await db.execute(
        text(
            "INSERT INTO student_ai_quota (student_id, period, used) "
            "VALUES (:s, :p, 40)"
        ),
        {"s": student, "p": date.today().replace(day=1)},
    )
    decision = await ent.check_and_reserve(db, student_id=student)
    assert (decision.allowed, decision.outcome, decision.remaining) == (
        False, "denied_limit", 0,
    )


async def test_quota_spent_before_grants(db: AsyncSession, student: int) -> None:
    """Порядок списания: сначала бесплатная квота, потом купленный пакет.

    Обратный порядок сжигал бы оплаченное раньше бесплатного — человек терял бы
    деньги в месяц, когда мог не тратить ничего.
    """
    await _subscribe(db, student, "ai")
    await db.execute(
        text("INSERT INTO student_ai_grant (student_id, granted) VALUES (:s, 5)"),
        {"s": student},
    )
    await ent.check_and_reserve(db, student_id=student)

    quota_used = (
        await db.execute(
            text("SELECT used FROM student_ai_quota WHERE student_id = :s"),
            {"s": student},
        )
    ).scalar()
    grant_used = (
        await db.execute(
            text("SELECT used FROM student_ai_grant WHERE student_id = :s"),
            {"s": student},
        )
    ).scalar()
    assert (quota_used, grant_used) == (1, 0), "пакет тронут раньше бесплатной квоты"


async def test_grants_used_after_quota_and_fifo(
    db: AsyncSession, student: int
) -> None:
    """Пакеты тратятся в порядке покупки — раньше купленный первым."""
    await _subscribe(db, student, "ai")
    await db.execute(
        text(
            "INSERT INTO student_ai_quota (student_id, period, used) "
            "VALUES (:s, :p, 40)"
        ),
        {"s": student, "p": date.today().replace(day=1)},
    )
    old_id = (
        await db.execute(
            text(
                "INSERT INTO student_ai_grant (student_id, granted, purchased_at) "
                "VALUES (:s, 2, now() - interval '10 days') RETURNING id"
            ),
            {"s": student},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO student_ai_grant (student_id, granted, purchased_at) "
            "VALUES (:s, 2, now()) RETURNING id"
        ),
        {"s": student},
    )

    decision = await ent.check_and_reserve(db, student_id=student)
    assert decision.allowed is True

    used_old = (
        await db.execute(
            text("SELECT used FROM student_ai_grant WHERE id = :i"), {"i": old_id}
        )
    ).scalar()
    assert used_old == 1, "списание пошло не с раньше купленного пакета"


async def test_started_conversation_may_overrun(
    db: AsyncSession, student: int
) -> None:
    """Разговор, начатый до исчерпания, доводится до конца (решения 2C и 19).

    Перерасход помечается: без отметки щедрость становится невидимой, и вопрос
    «не злоупотребляют ли» нечем закрыть.
    """
    await _subscribe(db, student, "ai")
    await db.execute(
        text(
            "INSERT INTO student_ai_quota (student_id, period, used) "
            "VALUES (:s, :p, 40)"
        ),
        {"s": student, "p": date.today().replace(day=1)},
    )
    blocked = await ent.check_and_reserve(db, student_id=student)
    assert blocked.allowed is False

    allowed = await ent.check_and_reserve(db, student_id=student, allow_overrun=True)
    assert allowed.allowed is True
    assert allowed.over_limit is True


@pytest.mark.parametrize("plan_code", ["self", "demo", "alumni"])
async def test_overrun_does_not_grant_a_right(
    db: AsyncSession, student: int, plan_code: str
) -> None:
    """`allow_overrun` снимает лимит, но не выдаёт право.

    Дыра, найденная ревью: разговоры в `ai_tutor_session` существуют ЗАДОЛГО до
    включения гейта. Если «разговор уже начат» трактовать как разрешение, ученик
    Self со старой сессией получил бы безлимитного наставника навсегда.
    """
    await _subscribe(db, student, plan_code)
    decision = await ent.check_and_reserve(
        db, student_id=student, allow_overrun=True
    )
    assert (decision.allowed, decision.outcome) == (False, "denied_not_in_plan")

    spent = (
        await db.execute(
            text("SELECT count(*) FROM student_ai_quota WHERE student_id = :s"),
            {"s": student},
        )
    ).scalar()
    assert spent == 0, "отказ не должен оставлять следа в счётчике"


# ─────────────────── Возврат единицы при сбое вызова ────────────────────────


async def test_release_returns_quota_unit(db: AsyncSession, student: int) -> None:
    """Неудачный вызов модели возвращает списанную единицу квоты.

    Резерв идёт до вызова — только так закрывается гонка; плата за это —
    обязательная явная компенсация.
    """
    await _subscribe(db, student, "ai")
    decision = await ent.check_and_reserve(db, student_id=student)
    assert decision.reserved_quota is True

    await ent.release(db, decision, student_id=student)
    used = (
        await db.execute(
            text("SELECT used FROM student_ai_quota WHERE student_id = :s"),
            {"s": student},
        )
    ).scalar()
    assert used == 0


async def test_release_returns_the_same_grant(
    db: AsyncSession, student: int
) -> None:
    """Возвращается ровно тот пакет, из которого списали.

    Иначе отдача бесплатной квоты «чинила» бы платный пакет и наоборот.
    """
    await _subscribe(db, student, "ai")
    await db.execute(
        text(
            "INSERT INTO student_ai_quota (student_id, period, used) "
            "VALUES (:s, :p, 40)"
        ),
        {"s": student, "p": date.today().replace(day=1)},
    )
    grant_id = (
        await db.execute(
            text(
                "INSERT INTO student_ai_grant (student_id, granted) "
                "VALUES (:s, 3) RETURNING id"
            ),
            {"s": student},
        )
    ).scalar_one()

    decision = await ent.check_and_reserve(db, student_id=student)
    assert decision.reserved_grant_id == grant_id
    assert decision.reserved_quota is False

    await ent.release(db, decision, student_id=student)
    used = (
        await db.execute(
            text("SELECT used FROM student_ai_grant WHERE id = :i"), {"i": grant_id}
        )
    ).scalar()
    assert used == 0

    quota_used = (
        await db.execute(
            text("SELECT used FROM student_ai_quota WHERE student_id = :s"),
            {"s": student},
        )
    ).scalar()
    assert quota_used == 40, "квоту трогать было нечего — списание шло из пакета"


async def test_release_is_noop_without_reservation(
    db: AsyncSession, student: int
) -> None:
    """Отказ и безлимит резерва не делают — возвращать нечего."""
    await _subscribe(db, student, "flagship")
    decision = await ent.check_and_reserve(db, student_id=student)
    assert decision.reserved_quota is False and decision.reserved_grant_id is None
    await ent.release(db, decision, student_id=student)  # не падает


# ───────────────────── fail-open против fail-closed ─────────────────────────


async def test_infrastructure_failure_is_fail_open(
    db: AsyncSession, student: int, monkeypatch
) -> None:
    """Инфраструктурный сбой пускает — но помечает исход как технический."""

    async def _boom(*_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("соединение потеряно"))

    monkeypatch.setattr(ent, "_load_plan", _boom)
    decision = await ent.check(db, student_id=student, capability="code_review")
    assert (decision.allowed, decision.outcome) == (True, "error_technical")


async def test_timeout_is_fail_open(
    db: AsyncSession, student: int, monkeypatch
) -> None:
    async def _slow(*_args, **_kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(ent, "_load_plan", _slow)
    decision = await ent.check(db, student_id=student, capability="code_review")
    assert decision.outcome == "error_technical"


async def test_our_own_bug_does_not_open_the_door(
    db: AsyncSession, student: int, monkeypatch
) -> None:
    """Ошибка в нашей логике поднимается наружу и доступа НЕ даёт.

    Без этого теста fail-open постепенно накрыл бы любые исключения, и опечатка
    стала бы неотличима от штатного разрешения.
    """

    async def _bug(*_args, **_kwargs):
        raise KeyError("опечатка в ключе плана")

    monkeypatch.setattr(ent, "_load_plan", _bug)
    with pytest.raises(KeyError):
        await ent.check(db, student_id=student, capability="code_review")


# ─────────────────────────── Режимы выката ──────────────────────────────────


@pytest.mark.parametrize(
    "mode,outcome,expected_block",
    [
        ("off", "denied_no_plan", False),
        ("off", "denied_limit", False),
        ("shadow", "denied_no_plan", False),
        ("shadow", "denied_not_in_plan", False),
        ("guests", "denied_no_plan", True),
        ("guests", "denied_not_in_plan", False),
        ("guests", "denied_limit", False),
        ("on", "denied_no_plan", True),
        ("on", "denied_not_in_plan", True),
        ("on", "denied_limit", True),
    ],
)
async def test_gate_mode_decides_what_is_applied(
    monkeypatch, mode: str, outcome: str, expected_block: bool
) -> None:
    """Решение считается всегда, применяется по режиму.

    Режим `guests` — самостоятельный шаг выката, а не оттенок «включено»: он
    закрывает кран токенов гостям, не трогая ни одного действующего ученика.
    """
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", mode)
    decision = ent.Decision(allowed=False, outcome=outcome)
    assert ent.should_block(decision) is expected_block


@pytest.mark.parametrize("mode", ["off", "shadow", "guests", "on"])
async def test_allowed_is_never_blocked(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", mode)
    assert ent.should_block(ent.Decision(allowed=True, outcome="allowed")) is False


@pytest.mark.parametrize("mode", ["off", "shadow", "guests", "on"])
async def test_technical_error_never_blocks(monkeypatch, mode: str) -> None:
    """Технический сбой не режет ни в одном режиме — в этом и смысл fail-open."""
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", mode)
    assert ent.should_block(ent._ERROR_TECHNICAL) is False
