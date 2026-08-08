"""tsk-301 Фаза 3: три точки принуждения ходят через единую дверь.

Проверяется **тело ответа эндпоинта**, а не схемы и не факт вызова сервиса: в
tsk-302 тест «поле только в схемах персонала» зазеленел и закрепил утечку —
схема была права, а эндпоинт отдавал лишнее. Здесь та же осторожность.

Ключевое свойство фазы, которое проверяется первым: **при режиме `off` (значение
по умолчанию) поведение не меняется ни в одной точке.** Выкат кода и включение
гейта разнесены намеренно; если бы проводка что-то меняла уже сейчас, разнести
их было бы бессмысленно.

Отдельно закреплено, что авто-заявка `blocked_limit` НЕ гейтится: ученик,
упёршийся в лимит попыток, иначе остался бы вовсе без выхода — тариф закрыл бы
ему единственную дверь, которую сам же и захлопнул.
"""
from __future__ import annotations

import random
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import entitlements_service as ent
from app.services.auth.session_service import create_session

pytestmark = pytest.mark.asyncio

_TAG = "tsk301ep"


async def _student_with_plan(db: AsyncSession, plan_code: str) -> tuple[int, str]:
    """Ученик с подпиской и живой сессией. Возвращает (id, токен)."""
    email = f"{_TAG}-{random.randint(10**8, 10**10)}@example.test"
    student_id = (
        await db.execute(
            text(
                "INSERT INTO users (full_name, email, is_active) "
                "VALUES (:n, :e, true) RETURNING id"
            ),
            {"n": f"{_TAG} ученик", "e": email},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, r.id FROM roles r WHERE r.name = 'student' "
            "ON CONFLICT DO NOTHING"
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
    token, _, _ = await create_session(db, user_id=int(student_id))
    await db.commit()
    return int(student_id), token


@pytest_asyncio.fixture(scope="function")
async def task_id(db: AsyncSession) -> int:
    """Задание в самостоятельном курсе — минимум для обращения к наставнику."""
    course_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES (:t, 'self_guided') RETURNING id"
            ),
            {"t": f"{_TAG} курс {uuid.uuid4().hex[:6]}"},
        )
    ).scalar_one()
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    value = (
        await db.execute(
            text(
                "INSERT INTO tasks (course_id, difficulty_id, task_content, "
                "                   solution_rules, is_active) "
                "VALUES (:c, :d, CAST(:tc AS jsonb), CAST(:sr AS jsonb), true) "
                "RETURNING id"
            ),
            {
                "c": course_id,
                "d": difficulty_id,
                "tc": '{"type":"SA","stem":"2+2?"}',
                "sr": '{"max_score":1,"correct_answer":"4"}',
            },
        )
    ).scalar_one()
    await db.commit()
    return int(value)


# ───────── Главное: при режиме по умолчанию ничего не меняется ──────────────


async def test_default_mode_is_off(monkeypatch) -> None:
    """Значение по умолчанию — `off`. Выкат кода не включает гейт."""
    from app.core.config import Settings

    monkeypatch.delenv("SUBSCRIPTION_GATE_MODE", raising=False)
    assert Settings().subscription_gate_mode == "off"


async def test_tutor_open_allowed_in_off_mode(
    db: AsyncSession, client, monkeypatch, task_id: int
) -> None:
    """Ученик Self (наставника в тарифе нет) при `off` разговор всё равно откроет."""
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")
    _sid, token = await _student_with_plan(db, "self")

    response = await client.get(
        f"/api/v1/ai-tutor/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text


# ───────────────────────── Точка 1: наставник ───────────────────────────────


async def test_tutor_open_denied_when_gate_on(
    db: AsyncSession, client, monkeypatch, task_id: int
) -> None:
    """При `on` ученик Self получает отказ — и в теле есть, что даёт апгрейд."""
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "on")
    _sid, token = await _student_with_plan(db, "self")

    response = await client.get(
        f"/api/v1/ai-tutor/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403, response.text

    body = response.json()
    payload = body.get("payload") or {}
    assert payload.get("code") == "subscription_denied"
    assert payload.get("outcome") == "denied_not_in_plan"
    assert payload.get("upgrade_hint"), (
        "отказ без объяснения — тупик; ученик обязан видеть, что даёт апгрейд"
    )


async def test_tutor_open_allowed_for_paid_plan(
    db: AsyncSession, client, monkeypatch, task_id: int
) -> None:
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "on")
    _sid, token = await _student_with_plan(db, "ai")

    response = await client.get(
        f"/api/v1/ai-tutor/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text


async def test_tutor_denied_leaves_no_session(
    db: AsyncSession, client, monkeypatch, task_id: int
) -> None:
    """Отказ не создаёт пустой разговор.

    Гейт стоит ДО `get_or_create` намеренно: сессия, которую нельзя продолжить,
    осталась бы висеть в списке преподавателя как начатый и брошенный диалог.
    """
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "on")
    student_id, token = await _student_with_plan(db, "demo")

    await client.get(
        f"/api/v1/ai-tutor/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    count = (
        await db.execute(
            text("SELECT count(*) FROM ai_tutor_session WHERE student_id = :s"),
            {"s": student_id},
        )
    ).scalar()
    assert count == 0


async def test_existing_conversation_stays_reachable_after_limit(
    db: AsyncSession, client, monkeypatch, task_id: int
) -> None:
    """Исчерпавший лимит открывает СВОЙ уже начатый разговор.

    Дыра, найденная ревью: гейт на открытии закрывал и существующий диалог.
    Обещание «начатый разговор доводится до конца» держится на том, что ученик
    до него дотянется — иначе оно пустое. Расход при этом не растёт: платит
    реплика, а не просмотр.
    """
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "on")
    student_id, token = await _student_with_plan(db, "ai")

    opened = await client.get(
        f"/api/v1/ai-tutor/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert opened.status_code == 200, opened.text

    # Лимит кончился уже после начала разговора.
    from datetime import date

    await db.execute(
        text(
            "INSERT INTO student_ai_quota (student_id, period, used) "
            "VALUES (:s, :p, 40) "
            "ON CONFLICT (student_id, period) DO UPDATE SET used = 40"
        ),
        {"s": student_id, "p": date.today().replace(day=1)},
    )
    await db.commit()

    again = await client.get(
        f"/api/v1/ai-tutor/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert again.status_code == 200, (
        "свой начатый разговор перестал открываться после исчерпания лимита"
    )


async def test_new_conversation_denied_after_limit(
    db: AsyncSession, client, monkeypatch, task_id: int
) -> None:
    """А вот НОВЫЙ разговор при исчерпанном лимите не начинается."""
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "on")
    student_id, token = await _student_with_plan(db, "ai")
    from datetime import date

    await db.execute(
        text(
            "INSERT INTO student_ai_quota (student_id, period, used) "
            "VALUES (:s, :p, 40)"
        ),
        {"s": student_id, "p": date.today().replace(day=1)},
    )
    await db.commit()

    response = await client.get(
        f"/api/v1/ai-tutor/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403, response.text
    assert (response.json().get("payload") or {}).get("outcome") == "denied_limit"


# ─────────────────── Точка 2: эскалация преподавателю ───────────────────────


async def test_manual_help_denied_when_gate_on(
    db: AsyncSession, client, monkeypatch, task_id: int
) -> None:
    """Ручной запрос помощи на тарифе без эскалации отклоняется."""
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "on")
    student_id, token = await _student_with_plan(db, "ai")  # эскалации нет

    response = await client.post(
        f"/api/v1/learning/tasks/{task_id}/request-help",
        json={"student_id": student_id, "message": "не понимаю"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403, response.text
    assert (response.json().get("payload") or {}).get("code") == "subscription_denied"

    created = (
        await db.execute(
            text("SELECT count(*) FROM help_requests WHERE student_id = :s"),
            {"s": student_id},
        )
    ).scalar()
    assert created == 0, "отклонённый запрос не должен оставлять заявку"


async def test_manual_help_allowed_for_plan_with_escalation(
    db: AsyncSession, client, monkeypatch, task_id: int
) -> None:
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "on")
    student_id, token = await _student_with_plan(db, "base")

    response = await client.post(
        f"/api/v1/learning/tasks/{task_id}/request-help",
        json={"student_id": student_id, "message": "не понимаю"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True


async def test_manual_help_allowed_in_off_mode(
    db: AsyncSession, client, monkeypatch, task_id: int
) -> None:
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")
    student_id, token = await _student_with_plan(db, "ai")

    response = await client.post(
        f"/api/v1/learning/tasks/{task_id}/request-help",
        json={"student_id": student_id, "message": "не понимаю"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text


async def test_blocked_limit_request_is_not_gated(
    db: AsyncSession, monkeypatch, task_id: int
) -> None:
    """Авто-заявка при исчерпании попыток проходит на любом тарифе.

    Её создаёт система, а не ученик, и она — единственный выход из тупика.
    Закрыть её тарифом значило бы запереть человека в задании без выхода.
    """
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "on")
    student_id, _token = await _student_with_plan(db, "demo")

    from app.services.help_requests_service import (
        get_or_create_blocked_limit_help_request,
    )

    course_id = (
        await db.execute(
            text("SELECT course_id FROM tasks WHERE id = :t"), {"t": task_id}
        )
    ).scalar()
    request_id, _created, _reused = await get_or_create_blocked_limit_help_request(
        db, student_id=student_id, task_id=task_id, course_id=int(course_id)
    )
    assert request_id is not None, "авто-заявка обязана создаваться независимо от тарифа"


# ──────────────────── Точка 3: оценка кода до очереди ───────────────────────


async def test_code_review_gate_stands_before_the_queue() -> None:
    """Гейт врезан ДО `pick_code_for_review`, а не после.

    Порядок проверяется по исходнику: если гейт окажется ниже постановки в
    очередь, работа уже помечена и фоновый тик её заберёт — обещание Demo
    «токены не расходуем» нарушится молча, без единой ошибки (пробел П2).
    Поведенческим тестом такой порядок не поймать: в обоих случаях ответ
    ученику одинаков.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "attempts.py"
    ).read_text(encoding="utf-8")

    gate_at = source.index("capability=\"code_review\"")
    queue_at = source.index("pick_code_for_review,")
    assert gate_at < queue_at, (
        "гейт подписки оказался ниже постановки в очередь — работа успеет "
        "попасть к фоновому тику"
    )


async def test_code_review_not_queued_for_plan_without_it(
    db: AsyncSession, monkeypatch
) -> None:
    """Решение двери для тарифа без оценки кода — «не пускать»."""
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "on")
    student_id, _ = await _student_with_plan(db, "demo")

    decision = await ent.check(db, student_id=student_id, capability="code_review")
    assert ent.should_block(decision, capability="code_review") is True


async def test_code_review_queued_for_plan_with_it(
    db: AsyncSession, monkeypatch
) -> None:
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "on")
    student_id, _ = await _student_with_plan(db, "base")

    decision = await ent.check(db, student_id=student_id, capability="code_review")
    assert ent.should_block(decision, capability="code_review") is False
