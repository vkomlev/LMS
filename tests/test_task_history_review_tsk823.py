"""Разбор прошлой попытки в блоке «Моя история по заданию» — tsk-823.

Откуда. После tsk-822 ученик видит «Совпало 3 значения из 4» в момент сдачи, но,
вернувшись на страницу позже, не находит ничего: в истории показывались только балл
и его собственный ответ. Текст проверки нигде не хранится (в ``task_results`` лежат
score / is_correct / answer_json), поэтому в истории он ПЕРЕСЧИТЫВАЕТСЯ.

Пересчёт по текущим правилам опасен ровно тем, чем опасна любая ретроспектива, и
здесь проверяются оба предохранителя:

* **задание правили после сдачи** — разбора нет, стоит ``review_stale``. Это не
  редкость: 22% всех работ прода и 296 незачётных из 1636. Ровно так в tsk-800
  ученик дважды сдал задание по условию, сменившемуся у него под вкладкой;
* **пересчёт разошёлся с записанным вердиктом** — разбора нет. Так отсекаются
  ручные зачёты преподавателя (балл стоит, автопроверка тот же ответ не
  засчитывает) и любые расхождения, которых мы не предвидели.

Проверяется на настоящей БД, как и tsk-349.
"""
from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import task_history_service
from app.services.auth import identity_link_service

_TAG = "tsk823"

#: Эталон из четырёх строк — форма заданий ЕГЭ 19-21 (курс 147).
REFERENCE = "20\n34\n38\n33"
#: Три строки из четырёх верны.
ALMOST = "20\n34\n38\n99"

TBL_RULES = {
    "max_score": 1,
    "scoring_mode": "all_or_nothing",
    "short_answer": {
        "normalization": ["trim", "lower"],
        "accepted_answers": [{"value": REFERENCE, "score": 1}],
    },
}
TBL_CONTENT = {
    "type": "TBL_COM",
    "stem": f"{_TAG} задания 19-21",
    "title": f"{_TAG} таблица",
    "table": {"columns": 1},
}


async def _new_student(db) -> int:
    u = Users(
        email=f"{_TAG}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG} ученик",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    await db.commit()
    return u.id


async def _new_course(db) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES (:t, 'self_guided') RETURNING id"
            ),
            {"t": f"{_TAG} {random.randint(1000, 9999)}"},
        )
    ).scalar()


async def _new_task(db, course_id: int) -> int:
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, difficulty_id, "
                "  max_score, is_active) "
                "VALUES (CAST(:c AS jsonb), CAST(:r AS jsonb), :course, :diff, 1, true) "
                "RETURNING id"
            ),
            {
                "c": json.dumps(TBL_CONTENT, ensure_ascii=False),
                "r": json.dumps(TBL_RULES, ensure_ascii=False),
                "course": course_id,
                "diff": difficulty_id,
            },
        )
    ).scalar()


async def _submit(
    db, *, user_id: int, task_id: int, course_id: int, value: str,
    score: int = 0, is_correct: bool = False, source: str = "spw_web",
) -> None:
    """Сдача ученика прямо в БД (как её пишет движок)."""
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :c, :src) RETURNING id"
            ),
            {"u": user_id, "c": course_id, "src": source},
        )
    ).scalar()
    answer = {"type": "TBL_COM", "response": {"value": value, "comment": "ход решения"}}
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, source_system, answer_json) "
            "VALUES (:u, :t, :a, :sc, 1, :ok, now(), now(), 0, :src, CAST(:ans AS jsonb))"
        ),
        {
            "u": user_id, "t": task_id, "a": attempt_id, "sc": score, "ok": is_correct,
            "src": source, "ans": json.dumps(answer, ensure_ascii=False),
        },
    )
    await db.commit()


async def _history(db, *, user_id: int, task_id: int) -> dict:
    data = await task_history_service.build_task_history(
        db, user_id=user_id, task_id=task_id, include_solution=False
    )
    assert data is not None
    return data


@pytest.fixture
async def graph(db):
    """Ученик + курс + табличное задание с эталоном из четырёх строк."""
    course_id = await _new_course(db)
    task_id = await _new_task(db, course_id)
    user_id = await _new_student(db)
    try:
        yield {"user_id": user_id, "task_id": task_id, "course_id": course_id}
    finally:
        await db.execute(text("DELETE FROM courses WHERE id = :c"), {"c": course_id})
        await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        await db.commit()


async def test_разбор_виден_в_истории(graph, db):
    """Главное: вернувшись на страницу, ученик снова видит, сколько строк сошлось."""
    await _submit(db, value=ALMOST, **{k: graph[k] for k in ("user_id", "task_id", "course_id")})

    data = await _history(db, user_id=graph["user_id"], task_id=graph["task_id"])
    attempt = data["attempts"][0]

    assert "Совпало 3 значения из 4" in (attempt["review"] or "")
    assert attempt["review_stale"] is False
    assert attempt["score"] == 0  # разбор ничего не меняет в оценке


async def test_верная_попытка_тоже_получает_разбор(graph, db):
    await _submit(
        db, value=REFERENCE, score=1, is_correct=True,
        **{k: graph[k] for k in ("user_id", "task_id", "course_id")},
    )

    data = await _history(db, user_id=graph["user_id"], task_id=graph["task_id"])
    attempt = data["attempts"][0]

    assert "правильный" in (attempt["review"] or "").lower()
    assert attempt["review_stale"] is False


async def test_правка_задания_после_сдачи_прячет_разбор(graph, db):
    """Предохранитель 1. Иначе ученику покажут разбор по правилам, которых он не видел."""
    await _submit(db, value=ALMOST, **{k: graph[k] for k in ("user_id", "task_id", "course_id")})

    # Условие переписано уже ПОСЛЕ сдачи — как в tsk-820.
    await db.execute(
        text(
            "UPDATE tasks SET task_content = jsonb_set(task_content, '{stem}', "
            "  to_jsonb('переписанное условие'::text)), updated_at = now() WHERE id = :t"
        ),
        {"t": graph["task_id"]},
    )
    await db.commit()

    data = await _history(db, user_id=graph["user_id"], task_id=graph["task_id"])
    attempt = data["attempts"][0]

    assert attempt["review"] is None
    assert attempt["review_stale"] is True


async def test_ручной_зачёт_не_получает_разбора(graph, db):
    """Предохранитель 2: балл поставлен человеком, автопроверка тот же ответ не засчитает."""
    await _submit(
        db, value=ALMOST, score=1, is_correct=True, source="manual_teacher",
        **{k: graph[k] for k in ("user_id", "task_id", "course_id")},
    )

    data = await _history(db, user_id=graph["user_id"], task_id=graph["task_id"])
    attempt = data["attempts"][0]

    assert attempt["manual"] is True
    assert attempt["review"] is None
    assert attempt["review_stale"] is False


async def test_дооценка_преподавателем_не_получает_разбора(graph, db):
    """Тот же предохранитель, но балл проставлен не синтетической попыткой, а правкой
    вердикта живой работы: пересчёт даст «неверно» против записанного «верно»."""
    await _submit(
        db, value=ALMOST, score=1, is_correct=True,
        **{k: graph[k] for k in ("user_id", "task_id", "course_id")},
    )

    data = await _history(db, user_id=graph["user_id"], task_id=graph["task_id"])
    attempt = data["attempts"][0]

    assert attempt["review"] is None
    assert attempt["review_stale"] is False


async def test_эталон_не_утекает_в_разбор(graph, db):
    """Инвариант answer-in-stem (tsk-254): текст проверки не должен нести эталон."""
    await _submit(db, value=ALMOST, **{k: graph[k] for k in ("user_id", "task_id", "course_id")})

    data = await _history(db, user_id=graph["user_id"], task_id=graph["task_id"])
    review = data["attempts"][0]["review"] or ""

    assert data["solution"] is None
    for cell in REFERENCE.split("\n"):
        assert cell not in review
