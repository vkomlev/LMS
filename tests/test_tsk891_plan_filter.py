"""tsk-891: сигналы дашборда не должны заводиться тестовым и выпускным тарифам.

Живая жалоба оператора 2026-09-10 сразу после выката tsk-651: на дашборде
качества обучения показывались Пряхин Михаил (тариф `test`) и Оля Омельченко
(тариф `alumni`, «Выпускник») — оба не реальные действующие ученики школы в
смысле этой аналитики. Список тарифов переиспользован из уже принятого решения
tsk-674/tsk-712 (`schedule_preference_service.EXCLUDED_PLAN_CODES` +
`NOT_COUNTED_PLAN_CODES`), а не изобретён заново.

Проверяется на обоих датчиках, которые уже показывались на дашборде: риск
ухода (tsk-647) и признак ИИ-авторства (tsk-646).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services import learning_gap_signals_service as sig
from tests.test_tsk647_dropout_risk_signal import (
    _cleanup as _cleanup_dropout,
)
from tests.test_tsk647_dropout_risk_signal import (
    _setup_silent_student,
)
from tests.test_tsk653_ai_authorship_signal import (
    _cleanup as _cleanup_authorship,
)
from tests.test_tsk653_ai_authorship_signal import (
    _course as _ai_course,
)
from tests.test_tsk653_ai_authorship_signal import (
    _enroll as _ai_enroll,
)
from tests.test_tsk653_ai_authorship_signal import (
    _submission as _ai_submission,
)
from tests.test_tsk653_ai_authorship_signal import (
    _user as _ai_user,
)


async def _assign_plan(db, student_id: int, code: str) -> None:
    await db.execute(text(
        "INSERT INTO student_subscription (student_id, plan_id) "
        "SELECT :sid, id FROM subscription_plan WHERE code = :code"
    ), {"sid": student_id, "code": code})
    await db.commit()


def _mine(rows: list[dict], student_id: int) -> list[dict]:
    return [r for r in rows if r["student_id"] == student_id]


# ───────────────────────── Риск ухода (tsk-647) ────────────────────────────


@pytest.mark.asyncio
async def test_test_tariff_is_not_flagged_as_dropout_risk(db):
    student, teacher, course = await _setup_silent_student(db, "tsk891-dr-test")
    try:
        await _assign_plan(db, student, "test")
        found = await sig.find_dropout_risk(db)
        assert not _mine(found, student), "тестовая учётка не должна попадать в риск ухода"
    finally:
        await _cleanup_dropout(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_alumni_tariff_is_not_flagged_as_dropout_risk(db):
    student, teacher, course = await _setup_silent_student(db, "tsk891-dr-alumni")
    try:
        await _assign_plan(db, student, "alumni")
        found = await sig.find_dropout_risk(db)
        assert not _mine(found, student), "выпускник не должен попадать в риск ухода"
    finally:
        await _cleanup_dropout(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_paying_tariff_is_still_flagged_as_dropout_risk(db):
    """Контроль: фильтр не выключил датчик целиком, платящий ученик виден."""
    student, teacher, course = await _setup_silent_student(db, "tsk891-dr-base")
    try:
        await _assign_plan(db, student, "base")
        found = await sig.find_dropout_risk(db)
        assert _mine(found, student), "платящий ученик обязан остаться в риске ухода"
    finally:
        await _cleanup_dropout(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_student_without_subscription_row_is_still_flagged(db):
    """Отсутствие тарифа вообще — не повод убрать ученика из сигналов.

    Это другой класс людей (см. tsk-596/tsk-610: школа ведёт занятия и денег
    не берёт из-за пробела в оформлении, а не потому что это не ученик).
    """
    student, teacher, course = await _setup_silent_student(db, "tsk891-dr-noplan")
    try:
        found = await sig.find_dropout_risk(db)
        assert _mine(found, student), "ученик без тарифа не должен молча выпадать из датчика"
    finally:
        await _cleanup_dropout(db, [student, teacher], [course])


# ──────────────────────── Признак ИИ-авторства (tsk-646) ───────────────────


@pytest.mark.asyncio
async def test_test_tariff_is_not_flagged_for_ai_authorship(db):
    course = await _ai_course(db, "tsk891-ai-test курс")
    student = await _ai_user(db, "tsk891-ai-test")
    try:
        await _ai_enroll(db, student, course)
        await _assign_plan(db, student, "test")
        for _ in range(3):
            await _ai_submission(db, user_id=student, course_id=course, flagged=True)
        found = await sig.find_ai_authorship_gaps(db)
        assert not _mine(found, student), "тестовая учётка не должна попадать в признак ИИ"
    finally:
        await _cleanup_authorship(db, [student], [course])


@pytest.mark.asyncio
async def test_alumni_tariff_is_not_flagged_for_ai_authorship(db):
    course = await _ai_course(db, "tsk891-ai-alumni курс")
    student = await _ai_user(db, "tsk891-ai-alumni")
    try:
        await _ai_enroll(db, student, course)
        await _assign_plan(db, student, "alumni")
        for _ in range(3):
            await _ai_submission(db, user_id=student, course_id=course, flagged=True)
        found = await sig.find_ai_authorship_gaps(db)
        assert not _mine(found, student), "выпускник не должен попадать в признак ИИ"
    finally:
        await _cleanup_authorship(db, [student], [course])


@pytest.mark.asyncio
async def test_paying_tariff_is_still_flagged_for_ai_authorship(db):
    course = await _ai_course(db, "tsk891-ai-base курс")
    student = await _ai_user(db, "tsk891-ai-base")
    try:
        await _ai_enroll(db, student, course)
        await _assign_plan(db, student, "base")
        for _ in range(3):
            await _ai_submission(db, user_id=student, course_id=course, flagged=True)
        found = await sig.find_ai_authorship_gaps(db)
        assert _mine(found, student), "платящий ученик обязан остаться в признаке ИИ"
    finally:
        await _cleanup_authorship(db, [student], [course])
