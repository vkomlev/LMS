"""tsk-619: сводка тарифов — кто на каком тарифе и кто без тарифа.

Что здесь проверяется и почему именно это:

1. **Тот же гейт, что у Фазы 9.** Новый адрес — новая дверь: роль проверяется по
   каждой роли отдельно, сервисный ключ не пускается. Обзор тарифов — это ФИО
   всех учеников школы разом, а не карточка одного человека.
2. **Строка «без тарифа» существует всегда.** Ради неё задача и заведена: между
   авто-`demo` при регистрации и авто-`base` при появлении расписания человека
   было неоткуда увидеть. Исчезнув при нуле, строка стала бы неотличима от «мы
   это не считаем».
3. **Сводка и её разворот считают ОДИН И ТОТ ЖЕ список.** Разойдись они, в
   строке стояло бы «трое», а по нажатию открывалось бы двое.
4. **«Ученик» определён так же, как в поиске маркетолога.** Подписка неактивной
   учётки в счёт не идёт — на проде такая строка есть (пользователь 4558).

Тесты идут по НАСТОЯЩЕЙ БД и проверяют ТЕЛО ответа (урок tsk-302). Числа
сверяются относительно своих учеников, а не абсолютными значениями: соседняя
сессия на той же dev-базе меняет общие счётчики.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services import subscription_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

pytestmark = pytest.mark.asyncio

_TAG = "tsk619"


async def _new_user(db, *, role: str | None, name: str, is_active: bool = True) -> tuple[int, str]:
    user = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}-{random.randint(10**6, 10**7)}",
        tg_id=None,
    )
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", user.email)
    token, _, _ = await create_session(db, user_id=user.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": user.id, "role": role},
        )
    if not is_active:
        await db.execute(
            text("UPDATE users SET is_active = false WHERE id = :u"), {"u": user.id}
        )
    await db.commit()
    return user.id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _assign_plan(db, student_id: int, plan_code: str, *, since: date | None = None) -> None:
    """Дать ученику действующий тариф с нужной датой начала."""
    await db.execute(
        text(
            "INSERT INTO student_subscription "
            "  (student_id, plan_id, pricing_group_id, starts_on, reason) "
            "SELECT :s, id, pricing_group_id, :d, 'tsk-619 тест' "
            "  FROM subscription_plan WHERE code = :c"
        ),
        {"s": student_id, "c": plan_code, "d": since or date.today()},
    )
    await db.commit()


async def _put_in_schedule(db, student_id: int) -> None:
    """Активная привязка к активному слоту — то, что автоматика считает «учится»."""
    teacher_id, _ = await _new_user(db, role="teacher", name="slot-teacher")
    slot_id = (
        await db.execute(
            text(
                "INSERT INTO lesson_slot "
                "  (teacher_id, weekday, start_time, duration_minutes, is_active) "
                "VALUES (:t, 1, '10:00', 60, true) RETURNING id"
            ),
            {"t": teacher_id},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO lesson_slot_student (slot_id, student_id, is_active) "
            "VALUES (:s, :u, true)"
        ),
        {"s": int(slot_id), "u": student_id},
    )
    await db.commit()


async def _staff_token(db) -> str:
    _uid, token = await _new_user(db, role="marketer", name="staff")
    return token


def _row(payload: dict, plan_code: str | None) -> dict:
    row = next((r for r in payload["rows"] if r["plan_code"] == plan_code), None)
    assert row is not None, f"строка {plan_code!r} обязана быть в сводке"
    return row


# ───────────────────────────── Кто имеет право ──────────────────────────────


@pytest.mark.parametrize("role", ["teacher", "methodist", "student", None])
async def test_summary_is_closed_for_non_money_roles(db, client, role):
    """Сводка — это ФИО всех учеников школы разом, гейт тот же, что у Фазы 9."""
    _uid, token = await _new_user(db, role=role, name=f"deny-{role}")

    assert (
        await client.get("/api/v1/subscriptions/summary", headers=_auth(token))
    ).status_code == 403
    assert (
        await client.get(
            "/api/v1/subscriptions/summary/students?plan_code=demo", headers=_auth(token)
        )
    ).status_code == 403


async def test_service_key_cannot_read_summary(db, client):
    """Держатель ключа TG_LMS читал бы весь список учеников школы."""
    key = next(iter(Settings().valid_api_keys))

    assert (
        await client.get("/api/v1/subscriptions/summary", headers={"X-API-Key": key})
    ).status_code == 403
    assert (
        await client.get(
            "/api/v1/subscriptions/summary/students", headers={"X-API-Key": key}
        )
    ).status_code == 403


async def test_anonymous_gets_401(client):
    assert (await client.get("/api/v1/subscriptions/summary")).status_code == 401


# ──────────────────────────────── Сводка ────────────────────────────────────


async def test_summary_counts_student_on_his_plan(db, client):
    """Ученик виден в строке своего тарифа, а не где-нибудь ещё."""
    student_id, _ = await _new_user(db, role="student", name="on-alumni")
    await _assign_plan(db, student_id, "alumni")
    token = await _staff_token(db)

    before = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()

    student2, _ = await _new_user(db, role="student", name="on-alumni-2")
    await _assign_plan(db, student2, "alumni")

    after = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()
    assert _row(after, "alumni")["students"] == _row(before, "alumni")["students"] + 1


async def test_no_plan_row_is_always_there(db, client):
    """Строка «без тарифа» — то, ради чего сводка и заведена.

    Она обязана быть даже при нуле: исчезнув, она стала бы неотличима от «мы
    это не считаем», а на проде без тарифа сейчас действительно никого.
    """
    token = await _staff_token(db)
    payload = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()

    no_plan = _row(payload, None)
    assert no_plan["plan_name"] == subscription_service.NO_PLAN_ROW_NAME
    assert no_plan["pricing_group_name"] is None


async def test_student_without_plan_lands_in_no_plan_row(db, client):
    """Ученик без подписки попадает именно в «без тарифа», а не пропадает."""
    token = await _staff_token(db)
    before = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()

    student_id, _ = await _new_user(db, role="student", name="no-plan")

    after = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()
    assert _row(after, None)["students"] == _row(before, None)["students"] + 1

    listing = (
        await client.get("/api/v1/subscriptions/summary/students", headers=_auth(token))
    ).json()
    mine = next((s for s in listing if s["student_id"] == student_id), None)
    assert mine is not None, "ученик без тарифа обязан открываться из своей строки"
    assert mine["plan_since"] is None
    assert mine["days_on_plan"] is None
    assert mine["registered_on"] == date.today().isoformat()


async def test_empty_plans_stay_as_zero_rows(db, client):
    """«На Self никого» — это ответ; пустая строка из сводки не выпадает."""
    token = await _staff_token(db)
    payload = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()

    codes = {r["plan_code"] for r in payload["rows"]}
    plans = await subscription_service.list_plans(db)
    assert {p["code"] for p in plans} <= codes, "каждый действующий тариф — строка сводки"
    assert all(r["students"] >= 0 for r in payload["rows"])


async def test_total_equals_sum_of_rows(db, client):
    """Итог обязан сходиться со строками: иначе сводке нельзя верить."""
    token = await _staff_token(db)
    payload = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()

    assert payload["total_students"] == sum(r["students"] for r in payload["rows"])
    assert payload["as_of"] == date.today().isoformat()
    assert payload["long_standing_days"] == subscription_service.LONG_STANDING_DAYS


async def test_inactive_account_is_not_counted(db, client):
    """Подписка неактивной учётки в счёт не идёт (на проде такая есть — 4558).

    Определение «ученик» здесь то же, что в поиске маркетолога: иначе строка
    сводки обещала бы человека, которого поиск панели не находит.
    """
    token = await _staff_token(db)
    before = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()

    ghost_id, _ = await _new_user(db, role="student", name="ghost", is_active=False)
    await _assign_plan(db, ghost_id, "demo")

    after = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()
    assert _row(after, "demo")["students"] == _row(before, "demo")["students"]

    listing = (
        await client.get(
            "/api/v1/subscriptions/summary/students?plan_code=demo", headers=_auth(token)
        )
    ).json()
    assert all(s["student_id"] != ghost_id for s in listing)


# ─────────────────────────────── Разрезы ────────────────────────────────────


async def test_schedule_split(db, client):
    """Расписание — тот самый признак, которым автоматика меряет «стал учеником».

    `demo` с расписанием значит, что перевод на `base` не сработал; `base` без
    расписания — что человек перестал ходить, а деньги считаются.
    """
    token = await _staff_token(db)
    before = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()

    student_id, _ = await _new_user(db, role="student", name="scheduled")
    await _assign_plan(db, student_id, "flagship")
    await _put_in_schedule(db, student_id)

    after = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()
    row_before, row_after = _row(before, "flagship"), _row(after, "flagship")
    assert row_after["with_schedule"] == row_before["with_schedule"] + 1
    assert row_after["without_schedule"] == row_before["without_schedule"]
    assert row_after["students"] == row_after["with_schedule"] + row_after["without_schedule"]

    listing = (
        await client.get(
            "/api/v1/subscriptions/summary/students?plan_code=flagship", headers=_auth(token)
        )
    ).json()
    mine = next(s for s in listing if s["student_id"] == student_id)
    assert mine["has_schedule"] is True


async def test_long_standing_counts_only_those_past_threshold(db, client):
    """«Второй месяц на Demo» и «зарегистрировался вчера» — разные люди."""
    token = await _staff_token(db)
    before = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()

    old_id, _ = await _new_user(db, role="student", name="old-adults")
    await _assign_plan(
        db,
        old_id,
        "adults",
        since=date.today() - timedelta(days=subscription_service.LONG_STANDING_DAYS + 10),
    )
    fresh_id, _ = await _new_user(db, role="student", name="fresh-adults")
    await _assign_plan(db, fresh_id, "adults", since=date.today() - timedelta(days=1))

    after = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()
    assert _row(after, "adults")["long_standing"] == _row(before, "adults")["long_standing"] + 1
    assert _row(after, "adults")["students"] == _row(before, "adults")["students"] + 2

    listing = (
        await client.get(
            "/api/v1/subscriptions/summary/students?plan_code=adults", headers=_auth(token)
        )
    ).json()
    days = {s["student_id"]: s["days_on_plan"] for s in listing}
    assert days[old_id] == subscription_service.LONG_STANDING_DAYS + 10
    assert days[fresh_id] == 1

    # Дольше всех — первым: с этого конца списка и начинается работа.
    positions = [s["student_id"] for s in listing]
    assert positions.index(old_id) < positions.index(fresh_id)


async def test_overdue_payment_uses_the_reminder_source(db, client):
    """Признак долга берётся у того же источника, что рассылает письма.

    Своя SQL-версия «есть долг» стала бы четвёртой копией формулы «ручная сумма
    важнее расчётной, поверх поправки» и разъехалась бы с рассылкой.
    """
    from app.services import charge_service

    token = await _staff_token(db)
    student_id, _ = await _new_user(db, role="student", name="debtor")
    await _assign_plan(db, student_id, "base")

    last_month = charge_service.month_start(date.today()) - timedelta(days=1)
    period = charge_service.month_start(last_month)
    await db.execute(
        text(
            "INSERT INTO student_monthly_charge "
            "  (student_id, group_id, period, calculated_minor, expected_lessons, "
            "   break_lessons, status) "
            "SELECT :s, pricing_group_id, :p, 300000, 4, 0, 'open' "
            "  FROM subscription_plan WHERE code = 'base'"
        ),
        {"s": student_id, "p": period},
    )
    await db.commit()

    payload = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()
    assert _row(payload, "base")["with_overdue_payment"] >= 1

    listing = (
        await client.get(
            "/api/v1/subscriptions/summary/students?plan_code=base", headers=_auth(token)
        )
    ).json()
    mine = next(s for s in listing if s["student_id"] == student_id)
    assert mine["has_overdue_payment"] is True


# ────────────────────── Разворот строки в список людей ──────────────────────


async def test_listing_matches_the_row_count(db, client):
    """Сводка и её разворот считают один и тот же список.

    Разойдись они — в строке стояло бы «трое», а по нажатию открывалось бы
    двое, и доверия к экрану не осталось бы.
    """
    token = await _staff_token(db)
    student_id, _ = await _new_user(db, role="student", name="match")
    await _assign_plan(db, student_id, "self")

    payload = (await client.get("/api/v1/subscriptions/summary", headers=_auth(token))).json()
    listing = (
        await client.get(
            "/api/v1/subscriptions/summary/students?plan_code=self", headers=_auth(token)
        )
    ).json()

    assert _row(payload, "self")["students"] == len(listing)
    assert any(s["student_id"] == student_id for s in listing)


async def test_disabled_plan_still_has_a_row(db, client):
    """Выключенный тариф, на котором кто-то сидит, остаётся строкой.

    Витрина отдаёт только активные тарифы, и без отдельной ветки такой ученик
    не попал бы ни в одну строку: итог перестал бы сходиться с суммой — молча.
    А выключают тариф ровно тогда, когда хотят посмотреть, кто на нём ещё есть.
    """
    token = await _staff_token(db)
    code = f"{_TAG}_retired_{random.randint(10**6, 10**7)}"
    await db.execute(
        text(
            "INSERT INTO subscription_plan "
            "  (code, name, ai_tutor_limit, code_review, teacher_escalation, "
            "   lessons, content, is_active, sort_order) "
            "VALUES (:c, 'Снятый с продажи', 0, false, false, false, 'demo', false, 99)"
        ),
        {"c": code},
    )
    student_id, _ = await _new_user(db, role="student", name="retired")
    await _assign_plan(db, student_id, code)

    try:
        payload = (
            await client.get("/api/v1/subscriptions/summary", headers=_auth(token))
        ).json()
        row = _row(payload, code)
        assert row["students"] >= 1
        assert "выключен" in row["plan_name"]
        assert payload["total_students"] == sum(r["students"] for r in payload["rows"])

        listing = (
            await client.get(
                f"/api/v1/subscriptions/summary/students?plan_code={code}",
                headers=_auth(token),
            )
        ).json()
        assert any(s["student_id"] == student_id for s in listing)
    finally:
        # Справочник тарифов общий для всей dev-базы — свой мусор убираем, иначе
        # соседняя сессия увидит лишнюю строку в сводке.
        await db.execute(
            text("DELETE FROM student_subscription WHERE student_id = :s"),
            {"s": student_id},
        )
        await db.execute(text("DELETE FROM subscription_plan WHERE code = :c"), {"c": code})
        await db.commit()


async def test_unknown_plan_is_404_not_empty_list(db, client):
    """Опечатка в коде тарифа — не «никого нет».

    Пустой список здесь законный ответ («на Self никого»), и свести с ним
    ошибку значило бы показывать маркетологу неправду.
    """
    token = await _staff_token(db)
    response = await client.get(
        "/api/v1/subscriptions/summary/students?plan_code=nosuchplan", headers=_auth(token)
    )
    assert response.status_code == 404, response.text
