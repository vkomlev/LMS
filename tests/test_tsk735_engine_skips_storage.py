"""tsk-735: учебный движок не ходит в хранилище файлов.

**Что чинили.** `compute_task_states_batch` на каждый узел дерева спрашивал у
объектного хранилища, лежат ли на месте файлы прошлых ответов ученика, — чтобы
пометить утраченные вложения (tsk-575). Пометка нужна только экрану работы, а
платили за неё все: движок (`GET /learning/next-item`, `GET /me/last-position`)
и карточка ученика у преподавателя, которые `last_answer_json` не читают вовсе.

**Чем это обернулось.** Замер на боевой машине 29.08.2026: один проход движка у
ученика 4515 — 183 проверки файлов, 5,96 с, из них 5,66 с (95%) ожидание
хранилища. Проверки идут через общий на процесс пул из шести потоков, поэтому на
границе занятия, когда группа разом жмёт «дальше», они выстраивались в общую
очередь: `next-item` и `last-position` висели по 14-21 с — и всё это время база
простаивала, активных соединений было ноль.

Поэтому тест сторожит не скорость (её на дев-базе не измерить), а сам факт:
**без явного запроса пометки обращений к хранилищу не происходит**. Скорость —
следствие, и она вернётся ровно тогда, когда этот инвариант нарушат.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.services import learning_engine_service as les
from app.services.learning_engine_service import LearningEngineService

_TAG = "tsk735"


@pytest.fixture
async def case(db, monkeypatch):
    """Ученик с одним решённым заданием, к ответу приложен файл.

    Возвращает `(student_id, task_id, calls)`, где `calls` — список запросов к
    хранилищу, накопленный подменённой `existing_attachment_ids`.
    """
    uid = random.randint(10**8, 10**10)
    course_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO courses (title, access_level, is_required, course_uid) "
                    "VALUES (:t, 'self_guided', false, :uid) RETURNING id"
                ),
                {"t": f"{_TAG}-курс", "uid": f"{_TAG}-{uid}"},
            )
        ).scalar_one()
    )
    student_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO users (email, full_name, is_active) "
                    "VALUES (:e, :n, true) RETURNING id"
                ),
                {"e": f"{_TAG}-{uid}@example.test", "n": f"{_TAG}-ученик"},
            )
        ).scalar_one()
    )
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    task_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO tasks (course_id, difficulty_id, external_uid, task_content, "
                    "  max_score, is_active, requirement_level, order_position) "
                    "VALUES (:c, :d, :uid, CAST(:content AS jsonb), 10, true, 'required', 1) "
                    "RETURNING id"
                ),
                {
                    "c": course_id,
                    "d": difficulty_id,
                    "uid": f"{_TAG}-task-{uid}",
                    "content": '{"type": "TA", "title": "работа с файлом"}',
                },
            )
        ).scalar_one()
    )
    attempt_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                    "VALUES (:u, :c, :c, 'test') RETURNING id"
                ),
                {"u": student_id, "c": course_id},
            )
        ).scalar_one()
    )
    answer = (
        '{"response": {"text": "готово", "meta": {"attachments": ['
        f'{{"attachment_id": "{attempt_id}_{_TAG}_task1.py", "filename": "task1.py"}}'
        "]}}}"
    )
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, answer_json, submitted_at, received_at, count_retry, source_system) "
            "VALUES (:u, :t, :a, 0, 10, false, CAST(:ans AS jsonb), now(), now(), 0, 'test')"
        ),
        {"u": student_id, "t": task_id, "a": attempt_id, "ans": answer},
    )
    # Зачисление обязательно: без него `resolve_next_item` возвращает «нет
    # активных курсов», не заходя в дерево, и третий тест ничего бы не проверял.
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active, order_number) "
            "VALUES (:u, :c, true, 1) ON CONFLICT DO NOTHING"
        ),
        {"u": student_id, "c": course_id},
    )
    await db.commit()

    calls: list[list] = []

    async def _spy(answer_jsons):
        calls.append(list(answer_jsons))
        return set()

    monkeypatch.setattr(les, "existing_attachment_ids", _spy)
    return student_id, task_id, calls


async def test_batch_does_not_touch_storage_by_default(case, db):
    """Пакетный расчёт статусов не спрашивает хранилище, пока не попросили."""
    student_id, task_id, calls = case

    states = await LearningEngineService().compute_task_states_batch(
        db, student_id, [task_id]
    )

    assert calls == [], "движок обратился в хранилище, хотя пометка не запрашивалась"
    assert states[task_id].state == "FAILED"
    # Пустое поле — намеренно: отдать ответ без пометки значило бы показать
    # живую ссылку на файл, которого может уже не быть (tsk-575).
    assert states[task_id].last_answer_json is None


async def test_batch_marks_attachments_when_asked(case, db):
    """С `mark_missing=True` пометка считается — и вложение помечается утраченным."""
    student_id, task_id, calls = case

    states = await LearningEngineService().compute_task_states_batch(
        db, student_id, [task_id], mark_missing=True
    )

    assert len(calls) == 1, "пометка запрошена, но хранилище не спросили"
    attachments = states[task_id].last_answer_json["response"]["meta"]["attachments"]
    assert attachments[0]["missing"] is True


async def test_next_item_walk_does_not_touch_storage(case, db):
    """Проход движка по дереву курса обходится без единого запроса в хранилище."""
    student_id, task_id, calls = case

    result = await LearningEngineService().resolve_next_item(db, student_id)

    # Сначала убеждаемся, что обход дошёл до задания с вложением: иначе тест
    # проверял бы тишину там, где движок и не начинал работу.
    assert (result.type, result.task_id) == ("task", task_id)
    assert calls == [], f"проход движка сходил в хранилище {len(calls)} раз(а)"
