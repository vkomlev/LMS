"""tsk-572 фаза 7: датчик учебных пробелов.

Главный тест здесь — на СМЕШАННОЙ фикстуре. Дефект, от которого он защищает,
молчаливый: датчик, считающий по сырому `task_results`, получает частоту ошибок,
разбавленную ручной простановкой преподавателя, порог не берёт и не срабатывает
никогда. Ни ошибки, ни лога — просто тишина там, где должен быть сигнал.

Пропорция в фикстуре взята с прода: на 11 643 строки `manual_teacher` (0% ошибок)
приходится 2 191 строка `spw_web` (24.5% ошибок).
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.learning_gaps_service import (
    REAL_STUDENT_SOURCES,
    find_topic_gaps,
    real_student_results_filter,
    source_breakdown,
)


async def _student(db, prefix: str) -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=prefix, tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id


async def _course(db, title: str) -> int:
    res = await db.execute(text(
        "INSERT INTO courses (title, access_level, is_required, course_uid) "
        "VALUES (:t,'self_guided',false,:u) RETURNING id"
    ), {"t": title, "u": f"gaps-{random.randint(10**8, 10**10)}"})
    cid = int(res.scalar_one())
    await db.commit()
    return cid


async def _task(db, course_id: int) -> int:
    did = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()
    if did is None:
        pytest.skip("нет ни одной difficulty")
    res = await db.execute(text(
        "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid) "
        "VALUES (CAST('{\"type\":\"SA\",\"stem\":\"g\"}' AS jsonb), :c, :d, :u) RETURNING id"
    ), {"c": course_id, "d": did, "u": f"gaps-{random.randint(10**8, 10**10)}"})
    tid = int(res.scalar_one())
    await db.commit()
    return tid


async def _results(db, *, user_id: int, task_id: int, course_id: int,
                   source: str, correct: int, wrong: int) -> None:
    await db.execute(text(
        "INSERT INTO attempts (user_id, course_id) VALUES (:u,:c)"
    ), {"u": user_id, "c": course_id})
    att = (await db.execute(text(
        "SELECT id FROM attempts WHERE user_id = :u ORDER BY id DESC LIMIT 1"
    ), {"u": user_id})).scalar()
    for i in range(correct + wrong):
        ok = i < correct
        await db.execute(text("""
            INSERT INTO task_results (user_id, task_id, attempt_id, answer_json,
                                      score, max_score, is_correct,
                                      submitted_at, received_at, source_system)
            VALUES (:u,:t,:a, CAST('{"answer":"x"}' AS jsonb), :s, 1, :ok,
                    now(), now(), :src)
        """), {"u": user_id, "t": task_id, "a": att,
               "s": 1 if ok else 0, "ok": ok, "src": source})
    await db.commit()


async def _cleanup(db, user_ids: list[int], course_ids: list[int]) -> None:
    await db.execute(text("DELETE FROM task_results WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM attempts WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM users WHERE id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:i)"), {"i": course_ids})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:i)"), {"i": course_ids})
    await db.commit()


@pytest.mark.asyncio
async def test_gap_survives_dilution_by_teacher_backfill(db):
    """Датчик обязан увидеть пробел, утонувший в ручной простановке.

    Тема реально провальная: 12 верных против 18 неверных — 60% ошибок. Но рядом
    лежит 150 строк ручной простановки преподавателя с нулём ошибок. По сырой
    таблице выходит 10%, порог 35% не берётся, заявки нет. Это и есть тот самый
    молчаливый отказ.
    """
    course = await _course(db, "Тема с реальным пробелом")
    task = await _task(db, course)
    # Трое учеников: тема, а не личный затык одного (см. MIN_STUDENTS).
    reals = [await _student(db, f"gap-real{i}") for i in range(3)]
    s2 = await _student(db, "gap-teacher")
    try:
        for i, sid in enumerate(reals):
            await _results(db, user_id=sid, task_id=task, course_id=course,
                           source="spw_web", correct=4, wrong=6)
        # Разбавление: пропорция взята с прода.
        await _results(db, user_id=s2, task_id=task, course_id=course,
                       source="manual_teacher", correct=150, wrong=0)

        gaps = await find_topic_gaps(db, days=7, min_submissions=20, threshold=0.35)
        mine = [g for g in gaps if g.course_id == course]
        assert mine, (
            "пробел утонул в ручной простановке — датчик молча не сработает никогда"
        )
        assert mine[0].submissions == 30, (
            f"в счёт попали чужие строки: {mine[0].submissions} вместо 30"
        )
        assert mine[0].wrong_percent == 60
    finally:
        await _cleanup(db, [*reals, s2], [course])


@pytest.mark.asyncio
async def test_teacher_backfill_alone_never_looks_like_a_gap(db):
    """Одна ручная простановка не должна порождать тему-кандидата.

    Обратная сторона: если бы фильтр был инвертирован, датчик завалил бы
    методиста заявками на темы, где ученики вообще ничего не сдавали.
    """
    course = await _course(db, "Только ручная простановка")
    task = await _task(db, course)
    s = await _student(db, "gap-only-teacher")
    try:
        await _results(db, user_id=s, task_id=task, course_id=course,
                       source="manual_teacher", correct=10, wrong=40)
        gaps = await find_topic_gaps(db, days=7, min_submissions=20, threshold=0.35)
        assert not [g for g in gaps if g.course_id == course]
    finally:
        await _cleanup(db, [s], [course])


@pytest.mark.asyncio
async def test_small_sample_is_not_a_conclusion(db):
    """На трёх ответах «66% ошибок» не значит ничего, кроме того, что отвечали трое."""
    course = await _course(db, "Мало данных")
    task = await _task(db, course)
    s = await _student(db, "gap-small")
    try:
        await _results(db, user_id=s, task_id=task, course_id=course,
                       source="spw_web", correct=1, wrong=2)
        gaps = await find_topic_gaps(db, days=7, min_submissions=20, threshold=0.35)
        assert not [g for g in gaps if g.course_id == course]
    finally:
        await _cleanup(db, [s], [course])


def test_source_filter_is_an_allowlist_not_an_exclusion():
    """Список разрешённых, а не «всё кроме manual_teacher».

    Иначе новый служебный источник (прогон, импорт, миграция) автоматически
    посчитался бы ученической работой только потому, что его забыли исключить.
    """
    cond = real_student_results_filter("tr")
    assert "IN (" in cond and "spw_web" in cond
    assert "NOT IN" not in cond and "!=" not in cond
    assert "manual_teacher" not in cond
    assert "spw_web" in REAL_STUDENT_SOURCES


@pytest.mark.asyncio
async def test_source_breakdown_exposes_skew(db):
    """Диагностика показывает перекос ДО того, как он обнулит сигнал."""
    rows = await source_breakdown(db, days=3650)
    assert isinstance(rows, list)
    assert all("source_system" in r and "submissions" in r for r in rows)


@pytest.mark.asyncio
async def test_one_student_is_not_a_topic_gap(db):
    """Один ученик — это личный затык, а не пробел темы.

    Найдено живым прогоном по проду: почти все кандидаты по порогу ошибок
    оказались с одним учеником. Мини-курс на такое заводить нельзя, а поток
    таких заявок отучил бы методиста их читать.
    """
    course = await _course(db, "Затык одного ученика")
    task = await _task(db, course)
    s = await _student(db, "gap-solo")
    try:
        await _results(db, user_id=s, task_id=task, course_id=course,
                       source="spw_web", correct=10, wrong=20)
        gaps = await find_topic_gaps(db, days=7, min_submissions=20, threshold=0.35)
        assert not [g for g in gaps if g.course_id == course], (
            "тема с одним учеником попала в кандидаты на мини-курс"
        )
    finally:
        await _cleanup(db, [s], [course])
