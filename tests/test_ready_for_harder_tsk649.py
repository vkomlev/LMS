"""tsk-649: «пора усложнить» — кому сводка занятия предлагает дать сложнее.

Проверяем на НАСТОЯЩЕЙ БД, как и остальная сводка занятия (tsk-022/410/648),
поле `ready_for_harder` ответа
`GET /teacher/lesson-occurrences/{id}/summary`.

Свойства, за которыми тут следим, — ровно те, на которых признак ломается:

- база признака — НЕлёгкие задания: после переоценки сложности (tsk-389) 66 %
  курса стало лёгким, и доля верных на лёгких не различает учеников;
- «с первой попытки» — по факту самой ранней сдачи, а НЕ по `count_retry`:
  в боевом потоке эта колонка всегда 0 (см. шапку сервиса);
- ручная простановка преподавателя — не ученическая работа;
- малая выборка молчит: «три из трёх верно» не доказательство;
- признак не участвует в очерёдности `attention`: это разные вопросы.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services.teacher_lesson_summary_service import (
    _HARDER_FIRST_TRY_RATE,
    _HARDER_MIN_TASKS,
)
from tests.test_teacher_lesson_summary_tsk022_410 import (
    _create_occurrence_with_participant,
    _new_course,
    _new_user,
)

UTC = timezone.utc
_TAG = "tsk649"

#: Уровни сложности из справочника: 2 — «Легко», 3 — «Средняя», 4 — «Сложно».
_EASY, _NORMAL, _HARD = 2, 3, 4


async def _new_task(db, *, course_id: int, difficulty_id: int) -> int:
    """Задание заданного уровня сложности — уровень здесь и есть предмет теста."""
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, "
                "  difficulty_id, external_uid, max_score, order_position) "
                "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 10, 1) "
                "RETURNING id"
            ),
            {
                "tc": json.dumps({"type": "SA", "stem": f"{_TAG} условие"}),
                "sr": json.dumps({"max_score": 10}),
                "cid": course_id,
                "did": difficulty_id,
                "uid": f"{_TAG}-{random.randint(10**8, 10**10)}",
            },
        )
    ).scalar()


async def _submit(
    db, *, student_id: int, task_id: int, course_id: int, is_correct: bool,
    at: datetime, source: str = "spw_web",
) -> None:
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :c, :src) RETURNING id"
            ),
            {"u": student_id, "c": course_id, "src": source},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, checked_at, source_system) "
            "VALUES (:u, :t, :a, :sc, 10, :ok, :ts, :ts, 0, :ts, :src)"
        ),
        {
            "u": student_id, "t": task_id, "a": attempt_id,
            "sc": 10 if is_correct else 0, "ok": is_correct, "ts": at, "src": source,
        },
    )
    await db.commit()


async def _help_requested(db, *, student_id: int, task_id: int, at: datetime) -> None:
    await db.execute(
        text(
            "INSERT INTO learning_events (student_id, event_type, payload, created_at) "
            "VALUES (:s, 'help_requested', CAST(:p AS jsonb), :ts)"
        ),
        {"s": student_id, "p": json.dumps({"task_id": task_id}), "ts": at},
    )
    await db.commit()


async def _solve_many(
    db, *, student_id: int, course_id: int, count: int, difficulty_id: int,
    correct: int | None = None, source: str = "spw_web",
) -> list[int]:
    """Решить `count` заданий уровня `difficulty_id`, из них `correct` верно."""
    correct = count if correct is None else correct
    now = datetime.now(UTC)
    task_ids = []
    for i in range(count):
        task_id = await _new_task(db, course_id=course_id, difficulty_id=difficulty_id)
        task_ids.append(task_id)
        await _submit(
            db, student_id=student_id, task_id=task_id, course_id=course_id,
            is_correct=i < correct, at=now - timedelta(days=1, minutes=i), source=source,
        )
    return task_ids


async def _summary_row(client, *, occ_id: int, teacher_id: int, token: str) -> dict:
    resp = await client.get(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/summary",
        params={"teacher_id": teacher_id, "include_progress": "false"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    (row,) = resp.json()["participants"]
    return row


async def _setup(db, name: str) -> tuple[int, str, int, int, int]:
    teacher_id, token = await _new_user(db, role="teacher", name=f"t{name}")
    student_id, _ = await _new_user(db, role="student", name=f"s{name}")
    course_id = await _new_course(db, f"{_TAG}-{name}")
    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
    )
    return teacher_id, token, student_id, course_id, occ_id


@pytest.mark.asyncio
async def test_strong_student_is_marked(db, client):
    """Решает нелёгкие с первой попытки и не просит помощи — признак есть."""
    teacher_id, token, student_id, course_id, occ_id = await _setup(db, "a")
    await _solve_many(
        db, student_id=student_id, course_id=course_id,
        count=_HARDER_MIN_TASKS, difficulty_id=_NORMAL,
    )

    row = await _summary_row(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    mark = row["ready_for_harder"]
    assert mark is not None, "ученик с идеальной первой попыткой должен получить признак"
    assert mark["tasks"] == _HARDER_MIN_TASKS
    assert mark["percent"] == 100
    assert "с первой попытки" in mark["detail"]


@pytest.mark.asyncio
async def test_exam_program_never_triggers_the_mark(db, client):
    """Курсы подготовки признак не питают (tsk-873).

    Требование оператора 10.09: на экзаменационных курсах правит СРОК, а объём
    и состав уже подбираются автоматически (tsk-798). На замере 09.09 у всех
    четверых, кого признак назвал сильными, банк заданий был отдан целиком —
    методисту показывали рычаг, выкрученный до упора.

    Признак смотрит вверх по дереву: помечается КОРЕНЬ программы, а ученик
    решает задание подкурса.
    """
    teacher_id, token, student_id, course_id, occ_id = await _setup(db, "exam")
    root_id = await _new_course(db, f"{_TAG}-exam-root")
    await db.execute(
        text("INSERT INTO course_parents (parent_course_id, course_id) VALUES (:p, :c)"),
        {"p": root_id, "c": course_id},
    )
    await db.execute(
        text("UPDATE courses SET is_exam = true WHERE id = :c"), {"c": root_id}
    )
    await db.commit()

    await _solve_many(
        db, student_id=student_id, course_id=course_id,
        count=_HARDER_MIN_TASKS, difficulty_id=_NORMAL,
    )

    row = await _summary_row(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    assert row["ready_for_harder"] is None, (
        "признак сработал на программе подготовки — методисту нечего крутить"
    )


@pytest.mark.asyncio
async def test_small_sample_stays_silent(db, client):
    """На выборке ниже порога признака нет: «три из трёх» — совпадение.

    Это не теоретическая осторожность. Первым заходом признак строился на
    заданиях уровня «Сложно» — и оказалось, что на проде их решают по 2–3 на
    человека (сложные вынесены в опциональный подкурс, tsk-347).
    """
    teacher_id, token, student_id, course_id, occ_id = await _setup(db, "b")
    await _solve_many(
        db, student_id=student_id, course_id=course_id,
        count=_HARDER_MIN_TASKS - 1, difficulty_id=_HARD,
    )

    row = await _summary_row(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    assert row["ready_for_harder"] is None


@pytest.mark.asyncio
async def test_easy_tasks_do_not_count(db, client):
    """Гора лёгких заданий на сто процентов признака не даёт."""
    teacher_id, token, student_id, course_id, occ_id = await _setup(db, "c")
    await _solve_many(
        db, student_id=student_id, course_id=course_id,
        count=_HARDER_MIN_TASKS + 10, difficulty_id=_EASY,
    )

    row = await _summary_row(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    assert row["ready_for_harder"] is None


@pytest.mark.asyncio
async def test_second_attempt_does_not_become_first(db, client):
    """Верно со второго раза — это НЕ «с первой попытки».

    Колонка `count_retry` в боевом потоке всегда 0, поэтому первая попытка
    определяется самой ранней сдачей по заданию. Тест держит именно это
    правило: если его подменить на `count_retry`, ученик ниже станет отличником.
    """
    teacher_id, token, student_id, course_id, occ_id = await _setup(db, "d")
    now = datetime.now(UTC)
    for i in range(_HARDER_MIN_TASKS):
        task_id = await _new_task(db, course_id=course_id, difficulty_id=_NORMAL)
        await _submit(
            db, student_id=student_id, task_id=task_id, course_id=course_id,
            is_correct=False, at=now - timedelta(days=2, minutes=i),
        )
        await _submit(
            db, student_id=student_id, task_id=task_id, course_id=course_id,
            is_correct=True, at=now - timedelta(days=1, minutes=i),
        )

    row = await _summary_row(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    assert row["ready_for_harder"] is None


@pytest.mark.asyncio
async def test_teacher_marked_results_do_not_count(db, client):
    """Ручная простановка преподавателя — не ученическая работа.

    Мимо фильтра `real_student_results_filter` она попала бы в выборку, а на
    проде таких строк больше, чем настоящих сдач: доля верных ушла бы в потолок
    у всех подряд.
    """
    teacher_id, token, student_id, course_id, occ_id = await _setup(db, "e")
    await _solve_many(
        db, student_id=student_id, course_id=course_id,
        count=_HARDER_MIN_TASKS, difficulty_id=_NORMAL, source="manual_teacher",
    )

    row = await _summary_row(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    assert row["ready_for_harder"] is None


@pytest.mark.asyncio
async def test_asking_for_help_removes_the_mark(db, client):
    """Просил помощи на этих же заданиях — сам он их не тянет, признака нет."""
    teacher_id, token, student_id, course_id, occ_id = await _setup(db, "f")
    task_ids = await _solve_many(
        db, student_id=student_id, course_id=course_id,
        count=_HARDER_MIN_TASKS, difficulty_id=_NORMAL,
    )
    now = datetime.now(UTC)
    # Порог самостоятельности — доля, а не ноль: один вопрос за два месяца
    # признака не отменяет, поэтому заявок должно быть заметно больше одной.
    for i, task_id in enumerate(task_ids[:3]):
        await _help_requested(
            db, student_id=student_id, task_id=task_id, at=now - timedelta(hours=i + 1),
        )

    row = await _summary_row(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    assert row["ready_for_harder"] is None


@pytest.mark.asyncio
async def test_mark_does_not_enter_attention_queue(db, client):
    """Признак не подменяет собой повод подойти: очерёдность про другое.

    Смешать их значит поднять сильного ученика выше тех, кому нужна помощь.
    """
    teacher_id, token, student_id, course_id, occ_id = await _setup(db, "g")
    await _solve_many(
        db, student_id=student_id, course_id=course_id,
        count=_HARDER_MIN_TASKS, difficulty_id=_NORMAL,
    )

    row = await _summary_row(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    assert row["ready_for_harder"] is not None
    assert row["attention"] is None


@pytest.mark.asyncio
async def test_rate_threshold_is_a_share_not_perfection(db, client):
    """Порог — доля, а не безошибочность: одна ошибка признака не отменяет."""
    teacher_id, token, student_id, course_id, occ_id = await _setup(db, "h")
    count = _HARDER_MIN_TASKS + 10
    wrong = 1
    assert (count - wrong) >= count * _HARDER_FIRST_TRY_RATE
    await _solve_many(
        db, student_id=student_id, course_id=course_id,
        count=count, difficulty_id=_NORMAL, correct=count - wrong,
    )

    row = await _summary_row(client, occ_id=occ_id, teacher_id=teacher_id, token=token)
    assert row["ready_for_harder"] is not None
    assert row["ready_for_harder"]["percent"] < 100
