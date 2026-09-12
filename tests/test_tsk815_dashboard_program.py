"""tsk-815: программа подготовки на дашборде ученика и родителя.

Главный вопрос родителя — «успеет ли ребёнок», и до сих пор ответить на него по
дашборду было нельзя: проценты по курсам говорят, где ученик сейчас, но не о
том, хватит ли оставшегося времени.

Здесь проверяется то, что легко потерять при переносе цифр на второй экран:
у преподавателя в сводке и у родителя на дашборде должен быть ОДИН ответ на
вопрос «сколько нужно в неделю». Две формулы означали бы, что система говорит
про одного ребёнка разное двум людям, которые между собой разговаривают.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.services import homework_volume_service, student_dashboard_service


#: «Сегодня» для расчётов — константа из тестов нормы (tsk-818/819): блок
#: дашборда берёт тот же `homework_volume_service.compute`, и календарная
#: сезонность у него та же. Десятикласснику альтернативные сроки показываются,
#: только пока до его срока больше 400 дней: в марте `datetime.now(UTC)`
#: обнулял их, и `test_non_graduate_gets_the_summer_argument` краснел при
#: верном поведении сервиса.
from tests.test_tsk741_homework import _PACE_NOW  # noqa: E402


async def _student_on_program(db, monkeypatch, *, grade: int = 11,
                              total_tasks: int = 200, done: int = 0):
    """Ученик программы подготовки с заданным прогрессом."""
    from app.core import settings_store
    from tests.test_tsk741_homework import (
        _program_student, _settings_with_program,
    )

    student_id, course_id = await _program_student(
        db, done_tasks=done, total_tasks=total_tasks, grade=grade,
    )
    monkeypatch.setattr(settings_store, "get_str", _settings_with_program(course_id))
    return student_id, course_id


@pytest.mark.asyncio
async def test_program_block_answers_will_he_make_it(db, monkeypatch):
    """Блок отвечает на вопрос «успеет ли»: срок, остаток, надо и выходит."""
    student_id, _ = await _student_on_program(db, monkeypatch)

    block = await student_dashboard_service._program_progress(
        db, student_id=student_id, now=_PACE_NOW,
    )

    assert block is not None
    assert block["kind"] == "ege"
    assert block["deadline"] is not None
    assert block["remaining"] > 0
    assert block["target_per_week"] > 0
    assert block["on_track"] is False, "темпа нет — успеть нельзя, и это сказано"


@pytest.mark.asyncio
async def test_numbers_match_the_teacher_summary(db, monkeypatch):
    """Родитель видит те же числа, что преподаватель.

    Разойдись они — родитель и учитель обсуждали бы одного ребёнка по разным
    цифрам. Поэтому блок берёт готовый расчёт, а не считает заново.
    """
    student_id, _ = await _student_on_program(db, monkeypatch)
    now = _PACE_NOW

    plan = await homework_volume_service.compute(db, student_id=student_id, now=now)
    block = await student_dashboard_service._program_progress(
        db, student_id=student_id, now=now,
    )

    assert block["target_per_week"] == plan.target_per_week
    assert block["fact_per_week"] == plan.fact_per_week
    assert block["remaining"] == plan.remaining_items
    assert block["deadline"] == plan.program_deadline


@pytest.mark.asyncio
async def test_forecast_needs_a_pace_and_stays_empty_without_one(db, monkeypatch):
    """Без темпа прогноз пуст: выдуманная дата хуже пустого места.

    Родитель прочитает дату как оценку, а не как «данных нет».
    """
    student_id, course_id = await _student_on_program(db, monkeypatch)
    now = _PACE_NOW

    empty = await student_dashboard_service._program_progress(
        db, student_id=student_id, now=now,
    )
    assert empty["forecast_date"] is None
    assert empty["fact_per_week"] == 0

    from tests.test_tsk741_homework import _submit

    tasks = (
        await db.execute(
            text("SELECT id FROM tasks WHERE course_id = :c LIMIT 20"), {"c": course_id}
        )
    ).scalars().all()
    for task_id in tasks:
        await _submit(
            db, student_id=student_id, task_id=int(task_id), course_id=course_id,
            is_correct=True, at=now - timedelta(days=2),
        )

    filled = await student_dashboard_service._program_progress(
        db, student_id=student_id, now=now,
    )
    assert filled["fact_per_week"] > 0
    assert filled["forecast_date"] is not None
    assert filled["forecast_date"] > now.date()


@pytest.mark.asyncio
async def test_non_graduate_gets_the_summer_argument(db, monkeypatch):
    """Десятикласснику показываются два альтернативных срока.

    Родителю этот выбор адресован прямее, чем ученику: решение о летних
    занятиях принимает он.
    """
    student_id, _ = await _student_on_program(db, monkeypatch, grade=10)

    block = await student_dashboard_service._program_progress(
        db, student_id=student_id, now=_PACE_NOW,
    )

    assert block["early_target_per_week"] is not None
    assert block["summer_target_per_week"] is not None
    assert block["early_deadline"] < block["summer_deadline"]


@pytest.mark.asyncio
async def test_student_outside_any_program_has_no_block(db, monkeypatch):
    """Ученик вне программ подготовки блока не получает.

    Вопрос «успеет ли к экзамену» для него не стоит, а пустая карточка на
    экране родителя выглядела бы поломкой.
    """
    from tests.test_tsk741_homework import _new_user

    student_id, _ = await _new_user(db)

    block = await student_dashboard_service._program_progress(
        db, student_id=student_id, now=_PACE_NOW,
    )

    assert block is None


@pytest.mark.asyncio
async def test_program_course_forecast_equals_the_program_forecast(db, monkeypatch):
    """У курса программы прогноз — тот же, что у блока программы (tsk-921).

    У Нуженко 12.09 на одной странице было «программа будет пройдена к 12
    ноября» (блок, по минутам) и «прогноз окончания 04.02.27» (карточка
    курса, по штучному темпу) про один и тот же ЕГЭ. Решение оператора:
    прогноз один, по минутам.
    """
    from datetime import datetime, timezone

    from tests.test_tsk741_homework import _submit

    student_id, course_id = await _student_on_program(db, monkeypatch)
    # Дашборд берёт «сейчас» от часов, а не из параметров — сдачи тоже
    # относительно настоящего «сейчас».
    now = datetime.now(timezone.utc)
    tasks = (
        await db.execute(
            text("SELECT id FROM tasks WHERE course_id = :c LIMIT 20"), {"c": course_id}
        )
    ).scalars().all()
    for task_id in tasks:
        await _submit(
            db, student_id=student_id, task_id=int(task_id), course_id=course_id,
            is_correct=True, at=now - timedelta(days=2),
        )

    dashboard = await student_dashboard_service.get_student_dashboard(
        db, student_id=student_id,
        period_from=now - timedelta(days=30), period_to=now,
    )
    program = dashboard["program"]
    course = next(c for c in dashboard["courses"] if c["course_id"] == course_id)
    assert program["forecast_date"] is not None
    assert course["forecast_completion_date"] == program["forecast_date"]
    assert course["is_service"] is False
