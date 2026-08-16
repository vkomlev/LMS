"""tsk-301 Фаза 9: управление тарифами персоналом.

Три вещи, ради которых фаза и существует, и ни одна не проверяется чтением кода:

1. **Преподавателю нельзя** (решение 10). Тариф — это деньги и права; преподаватель
   распоряжается занятиями. Гейт проверяется по каждой роли отдельно, включая
   сервисный ключ: он пропускается `require_role` без роли вовсе, и через него
   тариф менялся бы анонимно (`changed_by = NULL`).
2. **Права сразу, деньги со следующего месяца** (решение 14). Смена тарифа посреди
   месяца не должна переписывать уже названную человеку сумму — это проверяется
   сверкой строки начисления до и после, а не рассуждением о том, что пересчёт
   «здесь не зовётся».
3. **След остаётся.** История получает строку с причиной и автором, `audit_event` —
   событие. Через месяц вопрос «почему у него Self» иначе не разбирается.

Тесты идут по НАСТОЯЩЕЙ БД и проверяют ТЕЛО ответа (урок tsk-302).
"""
from __future__ import annotations

import random
from datetime import date

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

pytestmark = pytest.mark.asyncio

_TAG = "tsk301f9"


async def _new_user(db, *, role: str | None, name: str) -> tuple[int, str]:
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
    await db.commit()
    return user.id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _assign_plan(db, student_id: int, plan_code: str) -> None:
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, pricing_group_id, starts_on) "
            "SELECT :s, id, pricing_group_id, CURRENT_DATE FROM subscription_plan WHERE code = :c"
        ),
        {"s": student_id, "c": plan_code},
    )
    await db.commit()


# ───────────────────────────── Кто имеет право ──────────────────────────────


@pytest.mark.parametrize("role", ["teacher", "methodist", "student", None])
async def test_only_money_roles_manage_plans(db, client, role):
    """Преподаватель, методист и ученик тарифами не управляют (решение 10).

    Особенно преподаватель: он рядом с учеником каждый день, и «повысить тариф,
    чтобы дать наставника» выглядело бы услугой — но это чужие деньги.
    """
    _uid, token = await _new_user(db, role=role, name=f"deny-{role}")
    student_id, _ = await _new_user(db, role="student", name="target")

    assert (await client.get("/api/v1/subscriptions/plans", headers=_auth(token))).status_code == 403
    assert (
        await client.get(f"/api/v1/subscriptions/students/{student_id}", headers=_auth(token))
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/subscriptions/students/{student_id}",
            headers=_auth(token),
            json={"plan_code": "ai", "reason": "проба"},
        )
    ).status_code == 403


@pytest.mark.parametrize("role", ["marketer", "admin"])
async def test_marketer_and_admin_are_allowed(db, client, role):
    _uid, token = await _new_user(db, role=role, name=f"allow-{role}")
    response = await client.get("/api/v1/subscriptions/plans", headers=_auth(token))
    assert response.status_code == 200, response.text
    assert len(response.json()) >= 9, "витрина персонала обязана показывать все тарифы"


async def test_service_key_cannot_change_plans(db, client):
    """Ключ TG_LMS менял бы тарифы безымянно — в истории остался бы NULL."""
    student_id, _ = await _new_user(db, role="student", name="svc-target")
    key = next(iter(Settings().valid_api_keys))

    response = await client.post(
        f"/api/v1/subscriptions/students/{student_id}",
        headers={"X-API-Key": key},
        json={"plan_code": "ai", "reason": "через ключ"},
    )
    assert response.status_code == 403, response.text


async def test_anonymous_gets_401(client):
    assert (await client.get("/api/v1/subscriptions/plans")).status_code == 401


# ────────────────────────── Витрина тарифов ────────────────────────────────


async def test_plans_show_rights_and_group_name(db, client):
    """Права и ИМЯ группы: «группа 6» маркетологу ни о чём не говорит."""
    _uid, token = await _new_user(db, role="marketer", name="plans")
    body = (await client.get("/api/v1/subscriptions/plans", headers=_auth(token))).json()

    by_code = {row["code"]: row for row in body}
    assert by_code["demo"]["ai_tutor_limit"] == 0
    assert by_code["ai"]["ai_tutor_limit"] == 40
    assert by_code["flagship"]["ai_tutor_limit"] is None, "безлимит — это null, не ноль"
    assert by_code["base"]["lessons"] is True
    assert by_code["base"]["pricing_group_name"], "тариф с деньгами без имени группы"
    assert by_code["demo"]["pricing_group_id"] is None, "у demo денег нет вовсе"


async def test_staff_list_includes_unsellable_plans(db, client):
    """Витрина персонала шире витрины покупки — иначе `test` некому выдать."""
    _uid, token = await _new_user(db, role="admin", name="unsellable")
    codes = {
        row["code"]
        for row in (await client.get("/api/v1/subscriptions/plans", headers=_auth(token))).json()
    }
    assert {"test", "base_legacy", "alumni"} <= codes


# ──────────────────────── Присвоение и его след ─────────────────────────────


async def test_change_writes_history_with_author_and_reason(db, client):
    staff_id, token = await _new_user(db, role="marketer", name="author")
    student_id, _ = await _new_user(db, role="student", name="changed")
    await _assign_plan(db, student_id, "demo")

    response = await client.post(
        f"/api/v1/subscriptions/students/{student_id}",
        headers=_auth(token),
        json={"plan_code": "ai", "reason": "оплатил наставника переводом"},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["current"]["plan_code"] == "ai"
    assert body["current"]["changed_by"] == staff_id
    assert body["current"]["reason"] == "оплатил наставника переводом"
    assert body["current"]["ends_on"] is None

    closed = [h for h in body["history"] if h["plan_code"] == "demo"]
    assert closed and closed[0]["ends_on"] is not None, (
        "прошлый тариф обязан остаться в истории закрытым, а не исчезнуть"
    )


async def test_change_is_recorded_in_audit(db, client):
    """Строка истории не хранит ни прежний тариф, ни адрес — их держит журнал."""
    staff_id, token = await _new_user(db, role="admin", name="audited")
    student_id, _ = await _new_user(db, role="student", name="audit-target")
    await _assign_plan(db, student_id, "demo")

    await client.post(
        f"/api/v1/subscriptions/students/{student_id}",
        headers=_auth(token),
        json={"plan_code": "self", "reason": "перевёл на самостоятельный"},
    )

    row = (
        await db.execute(
            text(
                "SELECT details FROM audit_event "
                " WHERE event_type = 'staff.subscription.changed' AND user_id = :u "
                " ORDER BY id DESC LIMIT 1"
            ),
            {"u": staff_id},
        )
    ).scalar()
    assert row is not None, "смена тарифа не попала в журнал"
    assert row["student_id"] == student_id
    assert (row["from_plan"], row["to_plan"]) == ("demo", "self")


async def test_student_without_plan_can_be_assigned(db, client):
    """У ученика может не быть тарифа вовсе — это не ошибка, а первый случай."""
    _staff, token = await _new_user(db, role="marketer", name="first")
    student_id, _ = await _new_user(db, role="student", name="no-plan")

    before = (
        await client.get(f"/api/v1/subscriptions/students/{student_id}", headers=_auth(token))
    ).json()
    assert before["current"] is None and before["history"] == []

    response = await client.post(
        f"/api/v1/subscriptions/students/{student_id}",
        headers=_auth(token),
        json={"plan_code": "base", "reason": "начал заниматься"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["current"]["plan_code"] == "base"


# ─────────────────────────── Разведённые отказы ─────────────────────────────


async def test_same_plan_is_a_conflict_not_a_silent_success(db, client):
    """Пустое действие обязано быть видно: иначе «нажал и ничего не произошло»."""
    _staff, token = await _new_user(db, role="marketer", name="same")
    student_id, _ = await _new_user(db, role="student", name="same-target")
    await _assign_plan(db, student_id, "ai")

    response = await client.post(
        f"/api/v1/subscriptions/students/{student_id}",
        headers=_auth(token),
        json={"plan_code": "ai", "reason": "ещё раз"},
    )
    assert response.status_code == 409, response.text


async def test_unknown_plan_is_404_not_500(db, client):
    """Опечатка в коде тарифа — ответ человеку, а не падение внешнего ключа."""
    _staff, token = await _new_user(db, role="admin", name="typo")
    student_id, _ = await _new_user(db, role="student", name="typo-target")

    response = await client.post(
        f"/api/v1/subscriptions/students/{student_id}",
        headers=_auth(token),
        json={"plan_code": "aI ", "reason": "опечатка"},
    )
    assert response.status_code == 404, response.text


async def test_unknown_student_is_404_on_both_verbs(db, client):
    """«Тарифа нет» и «человека нет» — разные ответы."""
    _staff, token = await _new_user(db, role="admin", name="ghost")
    ghost = 99_000_000

    assert (
        await client.get(f"/api/v1/subscriptions/students/{ghost}", headers=_auth(token))
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/subscriptions/students/{ghost}",
            headers=_auth(token),
            json={"plan_code": "ai", "reason": "кому-то"},
        )
    ).status_code == 404


async def test_reason_is_required(db, client):
    """Без причины через месяц не разобрать, почему у человека этот тариф."""
    _staff, token = await _new_user(db, role="marketer", name="noreason")
    student_id, _ = await _new_user(db, role="student", name="noreason-target")

    for bad in ({"plan_code": "ai"}, {"plan_code": "ai", "reason": "   "}):
        response = await client.post(
            f"/api/v1/subscriptions/students/{student_id}", headers=_auth(token), json=bad
        )
        assert response.status_code == 422, response.text


# ──────────────── Решение 14: деньги текущего месяца не трогаем ─────────────


async def test_change_does_not_rewrite_the_open_month(db, client):
    """Сумма, уже названная человеку, не меняется задним числом.

    Это и есть решение 14. Проверяется сверкой строки начисления до и после, а
    не рассуждением «пересчёт здесь не зовётся»: пересчёт мог бы приехать из
    любого места, которое смена тарифа задевает.
    """
    _staff, token = await _new_user(db, role="marketer", name="money")
    student_id, _ = await _new_user(db, role="student", name="money-target")
    await _assign_plan(db, student_id, "base")

    period = date.today().replace(day=1)
    group_id = (
        await db.execute(
            text("SELECT pricing_group_id FROM subscription_plan WHERE code = 'base'")
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO student_monthly_charge "
            "  (student_id, group_id, period, calculated_minor, expected_lessons, status) "
            "VALUES (:s, :g, :p, 300000, 4, 'open')"
        ),
        {"s": student_id, "g": group_id, "p": period},
    )
    await db.commit()

    await client.post(
        f"/api/v1/subscriptions/students/{student_id}",
        headers=_auth(token),
        json={"plan_code": "self", "reason": "перешёл на самостоятельный с середины месяца"},
    )

    rows = (
        await db.execute(
            text(
                "SELECT group_id, calculated_minor, status FROM student_monthly_charge "
                " WHERE student_id = :s AND period = :p"
            ),
            {"s": student_id, "p": period},
        )
    ).mappings().all()
    assert len(rows) == 1, "смена тарифа не должна плодить начисления за текущий месяц"
    assert (rows[0]["group_id"], rows[0]["calculated_minor"]) == (group_id, 300000)


async def test_rights_switch_on_immediately(db, client):
    """Права включаются сразу — это вторая половина решения 14.

    Деньги ждут следующего месяца, права не ждут ничего: человек, которому
    только что открыли наставника, должен им пользоваться сегодня, а не с
    первого числа.
    """
    from app.services import entitlements_service as ent

    _staff, token = await _new_user(db, role="marketer", name="rights")
    student_id, _ = await _new_user(db, role="student", name="rights-target")
    await _assign_plan(db, student_id, "demo")

    before = await ent.check(db, student_id=student_id, capability="ai_tutor")
    assert before.allowed is False

    response = await client.post(
        f"/api/v1/subscriptions/students/{student_id}",
        headers=_auth(token),
        json={"plan_code": "ai", "reason": "оплатил наставника"},
    )
    assert response.status_code == 200, response.text

    after = await ent.check(db, student_id=student_id, capability="ai_tutor")
    assert (after.allowed, after.limit) == (True, 40)
