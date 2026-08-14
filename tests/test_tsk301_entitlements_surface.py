"""tsk-301 Фаза 8: витрина прав и счётчик заданий гостю.

Две поверхности, обе из «возможностей восхищения» брифа: остаток на кнопке с
предупреждением ДО исчерпания и «что даёт апгрейд» вместо «недоступно», а гостю —
сколько заданий осталось.

Главное требование к витрине — **она собирается через ту же дверь, что и гейты**.
Иначе интерфейс показывал бы доступное там, где сервер откажет, и человек нажимал
бы кнопку в ошибку вместо объяснения. Поэтому тесты сверяют витрину с реальным
поведением эндпоинтов, а не только с её собственной формой (урок tsk-302: проверять
тело ответа, а не схемы).
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import entitlements_service as ent
from app.services.auth.session_service import create_session

pytestmark = pytest.mark.asyncio


async def _student_with_plan(db: AsyncSession, plan_code: str) -> tuple[int, str]:
    student_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES ('tsk301 витрина', :e, true) RETURNING id"
                ),
                {"e": f"tsk301-surf-{uuid.uuid4().hex[:12]}@example.test"},
            )
        ).scalar_one()
    )
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, r.id FROM roles r WHERE r.name = 'student' ON CONFLICT DO NOTHING"
        ),
        {"u": student_id},
    )
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
            "SELECT :s, id, CURRENT_DATE FROM subscription_plan WHERE code = :c"
        ),
        {"s": student_id, "c": plan_code},
    )
    token, _, _ = await create_session(db, user_id=student_id)
    await db.commit()
    return student_id, token


# ───────────────────────────── Витрина прав ─────────────────────────────────


async def test_surface_matches_the_gate(db: AsyncSession, student_id_free=None) -> None:
    """Витрина и гейт отвечают одинаково по каждой возможности.

    Это и есть смысл общей двери: расхождение здесь означало бы кнопку, ведущую
    в отказ.
    """
    for plan_code in ("demo", "ai", "base", "test", "alumni"):
        student_id, _token = await _student_with_plan(db, plan_code)
        view = await ent.snapshot(db, student_id=student_id)
        for capability in ent.GATED_CAPABILITIES:
            decision = await ent.check(
                db, student_id=student_id, capability=capability
            )
            assert view.capabilities[capability].allowed == decision.allowed, (
                f"{plan_code}/{capability}: витрина разошлась с гейтом"
            )


async def test_denied_always_explains_what_upgrade_gives(db: AsyncSession) -> None:
    """У каждого отказа есть текст. «Недоступно» без объяснения — тупик."""
    for plan_code in ("demo", "self", "alumni"):
        student_id, _ = await _student_with_plan(db, plan_code)
        view = await ent.snapshot(db, student_id=student_id)
        for capability, state in view.capabilities.items():
            if not state.allowed:
                assert state.upgrade_hint, (
                    f"{plan_code}/{capability}: отказ без подсказки апгрейда"
                )


async def test_no_plan_has_fallback_hint(db: AsyncSession) -> None:
    """Без тарифа подсказку взять неоткуда — нужен запасной текст.

    Дыра найдена живым прогоном 08.08: `upgrade_hint` хранится в плане, а плана
    в этом случае нет, и человек видел «недоступно» без единого объяснения.
    """
    student_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES ('tsk301 без тарифа', :e, true) RETURNING id"
                ),
                {"e": f"tsk301-noplan-{uuid.uuid4().hex[:12]}@example.test"},
            )
        ).scalar_one()
    )
    view = await ent.snapshot(db, student_id=student_id)
    assert view.plan_code is None
    for capability, state in view.capabilities.items():
        assert state.reason == "denied_no_plan"
        assert state.upgrade_hint == ent.NO_PLAN_HINT, (
            f"{capability}: нет запасной подсказки при отсутствии тарифа"
        )


async def test_warning_fires_before_the_limit_runs_out(db: AsyncSession) -> None:
    """Предупреждение на исходе остатка, а не по факту исчерпания.

    Порог считает сервер: если бы каждый клиент считал сам, веб и бот однажды
    предупредили бы на разных числах, и это выглядело бы как сбой счётчика.
    """
    student_id, _ = await _student_with_plan(db, "ai")  # лимит 40
    period = date.today().replace(day=1)

    # Половина потрачена — предупреждать рано.
    await db.execute(
        text(
            "INSERT INTO student_ai_quota (student_id, period, used) VALUES (:s, :p, 20)"
        ),
        {"s": student_id, "p": period},
    )
    assert (await ent.snapshot(db, student_id=student_id)).capabilities["ai_tutor"].warn is False

    # Осталось 4 из 40 — пора.
    await db.execute(
        text("UPDATE student_ai_quota SET used = 36 WHERE student_id = :s"),
        {"s": student_id},
    )
    tutor = (await ent.snapshot(db, student_id=student_id)).capabilities["ai_tutor"]
    assert (tutor.warn, tutor.remaining) == (True, 4)


async def test_package_offered_only_where_it_is_sold(db: AsyncSession) -> None:
    """Предложение пакета совпадает с правилом продажи.

    Разъехавшись, они дали бы кнопку «купить», ведущую в отказ.
    """
    for plan_code, expected in (
        ("ai", True), ("base", True),
        ("demo", False), ("alumni", False), ("test", False), ("flagship", False),
    ):
        student_id, _ = await _student_with_plan(db, plan_code)
        view = await ent.snapshot(db, student_id=student_id)
        assert (view.package_offer is not None) is expected, (
            f"{plan_code}: предложение пакета разошлось с правилом продажи"
        )
        if expected:
            assert view.package_offer["units"] > 0
            assert view.package_offer["price_minor"] > 0


async def test_endpoint_returns_body_not_just_schema(
    db: AsyncSession, client
) -> None:
    """Проверяется ТЕЛО ответа эндпоинта (урок tsk-302)."""
    _student_id, token = await _student_with_plan(db, "ai")
    response = await client.get(
        "/api/v1/me/entitlements", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["plan_code"] == "ai"
    assert body["plan_name"]
    assert body["capabilities"]["ai_tutor"]["limit"] == 40
    assert body["capabilities"]["teacher_escalation"]["allowed"] is False
    assert body["capabilities"]["teacher_escalation"]["upgrade_hint"]
    assert body["package_offer"]["units"] > 0


# ──────────────────────── Счётчик заданий гостю ─────────────────────────────


async def test_guest_sees_limit_before_hitting_it(db: AsyncSession, client) -> None:
    """Гость видит лимит и расход ДО исчерпания.

    До этого он узнавал о лимите, только упёршись в стену, — худший момент для
    первого разговора о цене.
    """
    course_uid = f"tsk301-demo-{uuid.uuid4().hex[:8]}"
    await db.execute(
        text(
            "INSERT INTO courses (title, course_uid, access_level, is_public_demo, "
            "                     demo_task_limit) "
            "VALUES ('tsk301 демо-курс', :uid, 'self_guided', true, 3)"
        ),
        {"uid": course_uid},
    )
    await db.commit()

    response = await client.get(f"/api/v1/learning/guest/courses/{course_uid}")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["demo_task_limit"] == 3
    assert body["demo_tasks_used"] == 0, "без cookie расход считать не по чему"


async def test_guest_course_without_limit_reports_null(
    db: AsyncSession, client
) -> None:
    """Курс без лимита отдаёт null, а не ноль.

    Ноль означал бы «лимит есть и он исчерпан» — прямо противоположное.
    """
    course_uid = f"tsk301-nolimit-{uuid.uuid4().hex[:8]}"
    await db.execute(
        text(
            "INSERT INTO courses (title, course_uid, access_level, is_public_demo) "
            "VALUES ('tsk301 демо без лимита', :uid, 'self_guided', true)"
        ),
        {"uid": course_uid},
    )
    await db.commit()

    body = (
        await client.get(f"/api/v1/learning/guest/courses/{course_uid}")
    ).json()
    assert body["demo_task_limit"] is None
