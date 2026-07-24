"""История выполнения задания по паре (ученик, задание) — tsk-349.

Проверяем на НАСТОЯЩЕЙ БД: сборка агрегата (попытки + комментарии + заявки помощи
с диалогом + подсказки + эталон) и — главное — РАЗГРАНИЧЕНИЕ ВИДИМОСТИ:

* преподаватель видит полную историю И блок ``solution`` (правило проверки/эталон);
* ученик видит свою историю, но ``solution`` всегда ``null`` (эталон ему не отдаём —
  инвариант answer-in-stem, tsk-254; утечка = регресс);
* ученический эндпоинт строго self-scoped (``/me/...``) — чужую историю не отдаёт;
* teacher видит только своих учеников (ACL портала); methodist/admin — bypass;
* ученик не может прочитать историю/условие задания вне своих курсов (403).

Граф фикстуры:
    root (ученик записан) ──> task_sa (SA_COM с эталоном)
    foreign (ученик НЕ записан) ──> task_foreign
    student1 (2 попытки + заявка помощи + подсказка), student2 (пусто),
    teacher (связан со student1), other (не связан), methodist.
"""
from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import learning_events_service, task_history_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_TAG = "tsk349"


async def _new_user(db, role: str | None, name: str) -> tuple[int, str]:
    """Пользователь с сессией и (опционально) ролью → (id, session_token)."""
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.commit()
    return u.id, token


async def _insert_attempt_result(
    db, *, user_id, task_id, course_id, score, is_correct,
    checked_by: int | None, comment: str | None, source: str = "test",
) -> None:
    """Реальная попытка + результат по заданию (напрямую в БД).

    ``checked_by`` не None → результат проверен преподавателем (checked_at=now()).
    """
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :c, :src) RETURNING id"
            ),
            {"u": user_id, "c": course_id, "src": source},
        )
    ).scalar()
    metrics = json.dumps({"comment": comment}) if comment is not None else None
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, checked_at, checked_by, "
            "  source_system, metrics) "
            "VALUES (:u, :t, :a, :sc, 10, :ok, now(), now(), 0, "
            "        CASE WHEN CAST(:chk AS integer) IS NULL THEN NULL ELSE now() END, "
            "        CAST(:chk AS integer), "
            "        :src, CAST(:m AS jsonb))"
        ),
        {
            "u": user_id, "t": task_id, "a": attempt_id, "sc": score, "ok": is_correct,
            "chk": checked_by, "src": source, "m": metrics,
        },
    )


@pytest.fixture
async def hgraph(db):
    ids: dict[str, int] = {}
    try:
        async def new_course(title: str) -> int:
            return (
                await db.execute(
                    text(
                        "INSERT INTO courses (title, access_level) "
                        "VALUES (:t, 'self_guided') RETURNING id"
                    ),
                    {"t": title},
                )
            ).scalar()

        difficulty_id = (
            await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
        ).scalar()

        async def new_task(course_id: int, uid: str, rules: dict) -> int:
            content = {"type": "SA_COM", "stem": f"{_TAG} условие задания", "title": f"{_TAG} задание"}
            return (
                await db.execute(
                    text(
                        "INSERT INTO tasks (task_content, solution_rules, course_id, "
                        "difficulty_id, external_uid, max_score, order_position) "
                        "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, "
                        ":uid, 10, 1) RETURNING id"
                    ),
                    {
                        "tc": json.dumps(content),
                        "sr": json.dumps(rules),
                        "cid": course_id,
                        "did": difficulty_id,
                        "uid": f"{_TAG}-{uid}-{random.randint(10**8, 10**10)}",
                    },
                )
            ).scalar()

        ids["root"] = await new_course(f"{_TAG} корень")
        ids["foreign"] = await new_course(f"{_TAG} чужой курс")
        # Эталон: короткий ответ "42" (для проверки блока solution учителю).
        ids["task_sa"] = await new_task(
            ids["root"], "sa",
            {
                "max_score": 10,
                "short_answer": {
                    "accepted_answers": [{"value": "42", "score": 10}],
                    "normalization": ["trim", "lower"],
                },
            },
        )
        ids["task_foreign"] = await new_task(ids["foreign"], "foreign", {"max_score": 10})

        student1, tok_s1 = await _new_user(db, "student", "stud1")
        student2, tok_s2 = await _new_user(db, "student", "stud2")
        teacher_id, tok_teacher = await _new_user(db, "teacher", "teach")
        other_id, tok_other = await _new_user(db, "teacher", "other")
        methodist_id, tok_met = await _new_user(db, "methodist", "met")
        ids.update(student1=student1, student2=student2, teacher=teacher_id,
                   other=other_id, methodist=methodist_id)

        # student1 и student2 записаны на root; teacher связан со student1.
        for sid in (student1, student2):
            await db.execute(
                text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
                {"u": sid, "c": ids["root"]},
            )
        await db.execute(
            text(
                "INSERT INTO student_teacher_links (student_id, teacher_id) "
                "VALUES (:s, :t) ON CONFLICT DO NOTHING"
            ),
            {"s": student1, "t": teacher_id},
        )

        # История student1 по task_sa: 2 попытки (неверная + верная с комментарием).
        await _insert_attempt_result(
            db, user_id=student1, task_id=ids["task_sa"], course_id=ids["root"],
            score=0, is_correct=False, checked_by=None, comment=None,
        )
        await _insert_attempt_result(
            db, user_id=student1, task_id=ids["task_sa"], course_id=ids["root"],
            score=10, is_correct=True, checked_by=teacher_id, comment="проверено вручную",
        )

        # Заявка помощи + сообщение + ответ преподавателя.
        req_id = (
            await db.execute(
                text(
                    "INSERT INTO help_requests (status, request_type, student_id, task_id, "
                    "course_id, message, created_at, updated_at) "
                    "VALUES ('open', 'manual_help', :s, :t, :c, :msg, now(), now()) RETURNING id"
                ),
                {"s": student1, "t": ids["task_sa"], "c": ids["root"], "msg": "не понимаю задачу"},
            )
        ).scalar()
        msg_id = (
            await db.execute(
                text(
                    "INSERT INTO messages (message_type, content, recipient_id) "
                    "VALUES ('help_reply', CAST(:c AS jsonb), :r) RETURNING id"
                ),
                {"c": json.dumps({"text": "смотри пример в теории"}), "r": student1},
            )
        ).scalar()
        await db.execute(
            text(
                "INSERT INTO help_request_replies (request_id, teacher_id, message_id, body, "
                "close_after_reply, created_at) "
                "VALUES (:rq, :tid, :mid, :body, false, now())"
            ),
            {"rq": req_id, "tid": teacher_id, "mid": msg_id, "body": "смотри пример в теории"},
        )
        ids["help_request"] = int(req_id)

        # Одна открытая подсказка student1 по task_sa.
        await learning_events_service.record_hint_open(
            db, student_id=student1, attempt_id=1, task_id=ids["task_sa"],
            hint_type="text", hint_index=0, action="open", source="test",
        )
        await db.commit()

        yield {
            "ids": ids,
            "db": db,
            "tokens": {
                "student1": tok_s1, "student2": tok_s2, "teacher": tok_teacher,
                "other": tok_other, "methodist": tok_met,
            },
        }
    finally:
        await db.rollback()
        user_ids = [ids.get(k) for k in ("student1", "student2", "teacher", "other", "methodist") if k in ids]
        task_ids = [ids[k] for k in ("task_sa", "task_foreign") if k in ids]
        course_ids = [ids[k] for k in ("root", "foreign") if k in ids]
        if user_ids:
            await db.execute(
                text("DELETE FROM help_request_replies WHERE teacher_id = ANY(:u)"), {"u": user_ids}
            )
            if "help_request" in ids:
                await db.execute(
                    text("DELETE FROM help_requests WHERE id = :r"), {"r": ids["help_request"]}
                )
            await db.execute(text("DELETE FROM messages WHERE recipient_id = ANY(:u)"), {"u": user_ids})
            await db.execute(text("DELETE FROM learning_events WHERE student_id = ANY(:u)"), {"u": user_ids})
            await db.execute(text("DELETE FROM task_results WHERE user_id = ANY(:u)"), {"u": user_ids})
            await db.execute(text("DELETE FROM attempts WHERE user_id = ANY(:u)"), {"u": user_ids})
            await db.execute(text("DELETE FROM user_courses WHERE user_id = ANY(:u)"), {"u": user_ids})
            await db.execute(
                text("DELETE FROM student_teacher_links WHERE student_id = ANY(:u) OR teacher_id = ANY(:u)"),
                {"u": user_ids},
            )
            await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": user_ids})
            await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": user_ids})
            await db.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": user_ids})
        if task_ids:
            await db.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": task_ids})
        if course_ids:
            await db.execute(text("DELETE FROM courses WHERE id = ANY(:c)"), {"c": course_ids})
        await db.commit()


def _teacher_url(ids, student_key="student1", task_key="task_sa") -> str:
    return f"/api/v1/teacher/students/{ids[student_key]}/tasks/{ids[task_key]}/history"


# ─── Преподаватель: полная история + эталон ─────────────────────────────────


async def test_teacher_sees_full_history_and_solution(hgraph, client):
    ids = hgraph["ids"]
    resp = await client.get(
        _teacher_url(ids),
        headers={"Authorization": f"Bearer {hgraph['tokens']['teacher']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Попытки в хронологии: неверная (1) → верная с комментарием (2).
    assert [a["attempt_no"] for a in body["attempts"]] == [1, 2]
    assert [a["status"] for a in body["attempts"]] == ["failed", "passed"]
    assert body["attempts"][1]["comment"] == "проверено вручную"

    # Заявка помощи с диалогом.
    assert len(body["help_requests"]) == 1
    hr = body["help_requests"][0]
    assert hr["request_type"] == "manual_help"
    assert hr["message"] == "не понимаю задачу"
    assert len(hr["replies"]) == 1
    assert hr["replies"][0]["body"] == "смотри пример в теории"

    # Подсказки.
    assert body["hints"]["total"] == 1 and body["hints"]["text"] == 1

    # Условие задания.
    assert body["task"]["stem"] == f"{_TAG} условие задания"

    # Эталон — учителю виден.
    assert body["solution"] is not None
    assert body["solution"]["accepted_answers"] == [{"value": "42", "score": 10}]
    assert "trim" in body["solution"]["normalization"]


async def test_methodist_bypass_sees_solution(hgraph, client):
    ids = hgraph["ids"]
    resp = await client.get(
        _teacher_url(ids),
        headers={"Authorization": f"Bearer {hgraph['tokens']['methodist']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["solution"] is not None


async def test_other_teacher_forbidden(hgraph, client):
    """Посторонний преподаватель не видит историю чужого ученика — 403."""
    ids = hgraph["ids"]
    resp = await client.get(
        _teacher_url(ids),
        headers={"Authorization": f"Bearer {hgraph['tokens']['other']}"},
    )
    assert resp.status_code == 403, resp.text


async def test_teacher_task_not_found(hgraph, client):
    ids = hgraph["ids"]
    resp = await client.get(
        f"/api/v1/teacher/students/{ids['student1']}/tasks/999000111/history",
        headers={"Authorization": f"Bearer {hgraph['tokens']['teacher']}"},
    )
    assert resp.status_code == 404, resp.text


# ─── Ученик: своя история БЕЗ эталона ───────────────────────────────────────


async def test_student_sees_own_history_without_solution(hgraph, client):
    """Ключевой инвариант: ученик видит свою историю, но solution = null."""
    ids = hgraph["ids"]
    resp = await client.get(
        f"/api/v1/me/tasks/{ids['task_sa']}/history",
        headers={"Authorization": f"Bearer {hgraph['tokens']['student1']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["attempts"]) == 2
    assert len(body["help_requests"]) == 1
    assert body["hints"]["total"] == 1
    # Эталон ученику НЕ отдаётся — регресс класса answer-in-stem, если появится.
    assert body["solution"] is None


async def test_student_endpoint_is_self_scoped(hgraph, client):
    """Другой ученик по тому же заданию видит ПУСТУЮ историю, не student1."""
    ids = hgraph["ids"]
    resp = await client.get(
        f"/api/v1/me/tasks/{ids['task_sa']}/history",
        headers={"Authorization": f"Bearer {hgraph['tokens']['student2']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["attempts"] == []
    assert body["help_requests"] == []
    assert body["solution"] is None


async def test_student_cannot_read_task_outside_courses(hgraph, client):
    """Условие/историю задания вне своих курсов ученик не читает — 403."""
    ids = hgraph["ids"]
    resp = await client.get(
        f"/api/v1/me/tasks/{ids['task_foreign']}/history",
        headers={"Authorization": f"Bearer {hgraph['tokens']['student1']}"},
    )
    assert resp.status_code == 403, resp.text


# ─── Сервис: структурное разграничение видимости эталона ────────────────────


async def test_service_gates_solution_by_flag(hgraph):
    """build_task_history собирает solution ТОЛЬКО при include_solution=True."""
    ids, db = hgraph["ids"], hgraph["db"]
    without = await task_history_service.build_task_history(
        db, user_id=ids["student1"], task_id=ids["task_sa"], include_solution=False
    )
    assert without is not None and without["solution"] is None

    with_sol = await task_history_service.build_task_history(
        db, user_id=ids["student1"], task_id=ids["task_sa"], include_solution=True
    )
    assert with_sol is not None and with_sol["solution"] is not None
    assert with_sol["solution"]["accepted_answers"] == [{"value": "42", "score": 10}]
