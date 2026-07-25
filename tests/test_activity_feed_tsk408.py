"""Лента активности учеников для преподавателя — tsk-408.

Проверяем на НАСТОЯЩЕЙ БД: агрегация трёх источников событий (решение задания,
запрос помощи, изучение материала), сортировку по времени (убывание),
курсорную пагинацию (``before``/``next_before``), исключение синтетических
(``source='manual_teacher'``) записей ручного зачёта (tsk-297) и ACL:

* преподаватель видит события ученика, закреплённого напрямую
  (``student_teacher_links``), И ученика на курсе под его ``teacher_courses``
  ACL — но НЕ ученика вне обеих связей;
* methodist — bypass (видит всё);
* лишний преподаватель без связей получает пустую ленту.

Граф фикстуры:
    root_direct (student_direct записан)  ──> task_direct, material_direct
    root_course (student_course записан) ──> task_course, material_course
    root_other  (student_other записан)  ──> task_other, material_other

    teacher: student_teacher_links → student_direct; teacher_courses → root_course.
    student_other вне обеих связей преподавателя.
"""
from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_TAG = "tsk408"


async def _new_user(db, role: str | None, name: str) -> tuple[int, str]:
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


async def _insert_result(
    db, *, user_id, task_id, course_id, is_correct, source_system="test"
) -> None:
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :c, :src) RETURNING id"
            ),
            {"u": user_id, "c": course_id, "src": source_system},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, source_system) "
            "VALUES (:u, :t, :a, :sc, 10, :ok, now(), now(), 0, :src)"
        ),
        {
            "u": user_id, "t": task_id, "a": attempt_id,
            "sc": 10 if is_correct else 0, "ok": is_correct, "src": source_system,
        },
    )


async def _insert_help_request(db, *, student_id, task_id, course_id) -> None:
    await db.execute(
        text(
            "INSERT INTO help_requests (status, request_type, student_id, task_id, "
            "course_id, message, created_at, updated_at) "
            "VALUES ('open', 'manual_help', :s, :t, :c, :msg, now(), now())"
        ),
        {"s": student_id, "t": task_id, "c": course_id, "msg": f"{_TAG} нужна помощь"},
    )


async def _insert_material_progress(db, *, student_id, material_id, source="system") -> None:
    await db.execute(
        text(
            "INSERT INTO student_material_progress "
            "  (student_id, material_id, status, completed_at, source) "
            "VALUES (:s, :m, 'completed', now(), :src) "
            "ON CONFLICT (student_id, material_id) DO UPDATE SET "
            "  status = 'completed', completed_at = now(), source = EXCLUDED.source"
        ),
        {"s": student_id, "m": material_id, "src": source},
    )


@pytest.fixture
async def agraph(db):
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

        async def new_task(course_id: int, uid: str) -> int:
            content = {"type": "SA_COM", "stem": f"{_TAG} условие {uid}", "title": ""}
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
                        "sr": json.dumps({"max_score": 10}),
                        "cid": course_id,
                        "did": difficulty_id,
                        "uid": f"{_TAG}-{uid}-{random.randint(10**8, 10**10)}",
                    },
                )
            ).scalar()

        async def new_material(course_id: int, title: str) -> int:
            return (
                await db.execute(
                    text(
                        "INSERT INTO materials (course_id, title, type, content, order_position) "
                        "VALUES (:c, :t, 'text', CAST(:content AS jsonb), 1) RETURNING id"
                    ),
                    {"c": course_id, "t": title, "content": json.dumps({"body": "x"})},
                )
            ).scalar()

        ids["root_direct"] = await new_course(f"{_TAG} прямая связь")
        ids["root_course"] = await new_course(f"{_TAG} курсовой ACL")
        ids["root_other"] = await new_course(f"{_TAG} чужой")

        ids["task_direct"] = await new_task(ids["root_direct"], "direct")
        ids["task_course"] = await new_task(ids["root_course"], "course")
        ids["task_other"] = await new_task(ids["root_other"], "other")

        ids["material_direct"] = await new_material(ids["root_direct"], f"{_TAG} материал прямой")
        ids["material_course"] = await new_material(ids["root_course"], f"{_TAG} материал курсовой")
        ids["material_other"] = await new_material(ids["root_other"], f"{_TAG} материал чужой")

        student_direct, _ = await _new_user(db, "student", "sdirect")
        student_course, _ = await _new_user(db, "student", "scourse")
        student_other, _ = await _new_user(db, "student", "sother")
        teacher_id, tok_teacher = await _new_user(db, "teacher", "teach")
        other_id, tok_other = await _new_user(db, "teacher", "other")
        methodist_id, tok_met = await _new_user(db, "methodist", "met")
        ids.update(
            student_direct=student_direct, student_course=student_course,
            student_other=student_other, teacher=teacher_id, other=other_id,
            methodist=methodist_id,
        )

        for sid, cid in (
            (student_direct, ids["root_direct"]),
            (student_course, ids["root_course"]),
            (student_other, ids["root_other"]),
        ):
            await db.execute(
                text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
                {"u": sid, "c": cid},
            )

        # teacher: связь напрямую со student_direct + курсовой ACL на root_course.
        await db.execute(
            text(
                "INSERT INTO student_teacher_links (student_id, teacher_id) "
                "VALUES (:s, :t) ON CONFLICT DO NOTHING"
            ),
            {"s": student_direct, "t": teacher_id},
        )
        await db.execute(
            text(
                "INSERT INTO teacher_courses (teacher_id, course_id) "
                "VALUES (:t, :c) ON CONFLICT DO NOTHING"
            ),
            {"t": teacher_id, "c": ids["root_course"]},
        )

        # Реальные события: по одному каждого типа на student_direct и student_course.
        await _insert_result(
            db, user_id=student_direct, task_id=ids["task_direct"],
            course_id=ids["root_direct"], is_correct=True,
        )
        await _insert_help_request(
            db, student_id=student_direct, task_id=ids["task_direct"], course_id=ids["root_direct"],
        )
        await _insert_material_progress(db, student_id=student_direct, material_id=ids["material_direct"])

        await _insert_result(
            db, user_id=student_course, task_id=ids["task_course"],
            course_id=ids["root_course"], is_correct=False,
        )
        await _insert_help_request(
            db, student_id=student_course, task_id=ids["task_course"], course_id=ids["root_course"],
        )
        await _insert_material_progress(db, student_id=student_course, material_id=ids["material_course"])

        # student_other — вне ACL teacher'а (только methodist должен это увидеть).
        await _insert_result(
            db, user_id=student_other, task_id=ids["task_other"],
            course_id=ids["root_other"], is_correct=True,
        )

        # Синтетика ручного зачёта (tsk-297) — НЕ должна попасть в ленту вовсе.
        await _insert_result(
            db, user_id=student_direct, task_id=ids["task_direct"],
            course_id=ids["root_direct"], is_correct=True, source_system="manual_teacher",
        )
        await _insert_material_progress(
            db, student_id=student_direct, material_id=ids["material_course"], source="manual_teacher",
        )

        await db.commit()

        yield {
            "ids": ids,
            "db": db,
            "tokens": {"teacher": tok_teacher, "other": tok_other, "methodist": tok_met},
        }
    finally:
        await db.rollback()
        user_ids = [
            ids[k] for k in ("student_direct", "student_course", "student_other", "teacher", "other", "methodist")
            if k in ids
        ]
        task_ids = [ids[k] for k in ("task_direct", "task_course", "task_other") if k in ids]
        course_ids = [ids[k] for k in ("root_direct", "root_course", "root_other") if k in ids]
        material_ids = [
            ids[k] for k in ("material_direct", "material_course", "material_other") if k in ids
        ]
        if user_ids:
            await db.execute(text("DELETE FROM help_requests WHERE student_id = ANY(:u)"), {"u": user_ids})
            await db.execute(
                text("DELETE FROM student_material_progress WHERE student_id = ANY(:u)"), {"u": user_ids}
            )
            await db.execute(text("DELETE FROM task_results WHERE user_id = ANY(:u)"), {"u": user_ids})
            await db.execute(text("DELETE FROM attempts WHERE user_id = ANY(:u)"), {"u": user_ids})
            await db.execute(text("DELETE FROM user_courses WHERE user_id = ANY(:u)"), {"u": user_ids})
            await db.execute(
                text("DELETE FROM student_teacher_links WHERE student_id = ANY(:u) OR teacher_id = ANY(:u)"),
                {"u": user_ids},
            )
            await db.execute(text("DELETE FROM teacher_courses WHERE teacher_id = ANY(:u)"), {"u": user_ids})
            await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": user_ids})
            await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": user_ids})
            await db.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": user_ids})
        if material_ids:
            await db.execute(text("DELETE FROM materials WHERE id = ANY(:m)"), {"m": material_ids})
        if task_ids:
            await db.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": task_ids})
        if course_ids:
            await db.execute(text("DELETE FROM courses WHERE id = ANY(:c)"), {"c": course_ids})
        await db.commit()


def _feed_url(limit: int | None = None, before: str | None = None) -> str:
    qs = []
    if limit is not None:
        qs.append(f"limit={limit}")
    if before is not None:
        qs.append(f"before={before}")
    return "/api/v1/teacher/activity-feed" + (f"?{'&'.join(qs)}" if qs else "")


async def test_teacher_sees_direct_and_course_scoped_students(agraph, client):
    ids = agraph["ids"]
    resp = await client.get(
        _feed_url(), headers={"Authorization": f"Bearer {agraph['tokens']['teacher']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    student_ids = {e["student_id"] for e in body["events"]}
    assert ids["student_direct"] in student_ids
    assert ids["student_course"] in student_ids
    assert ids["student_other"] not in student_ids

    # 3 события на каждого из двух видимых учеников = 6; синтетика исключена.
    assert len(body["events"]) == 6

    types = {e["type"] for e in body["events"]}
    assert types == {"task_solved", "help_requested", "material_studied"}

    # Сортировка по убыванию времени.
    timestamps = [e["timestamp"] for e in body["events"]]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_task_solved_outcome_and_summary(agraph, client):
    ids = agraph["ids"]
    resp = await client.get(
        _feed_url(), headers={"Authorization": f"Bearer {agraph['tokens']['teacher']}"},
    )
    body = resp.json()
    solved = [
        e for e in body["events"]
        if e["type"] == "task_solved" and e["student_id"] == ids["student_direct"]
    ]
    assert len(solved) == 1
    assert solved[0]["outcome"] == "correct"
    assert "верно" in solved[0]["summary"]

    failed = [
        e for e in body["events"]
        if e["type"] == "task_solved" and e["student_id"] == ids["student_course"]
    ]
    assert len(failed) == 1
    assert failed[0]["outcome"] == "incorrect"
    assert "неверно" in failed[0]["summary"]


async def test_manual_teacher_grants_excluded(agraph, client):
    """Синтетические зачёты (tsk-297) — не активность ученика, в ленту не попадают."""
    ids = agraph["ids"]
    resp = await client.get(
        _feed_url(), headers={"Authorization": f"Bearer {agraph['tokens']['teacher']}"},
    )
    body = resp.json()
    # ровно 1 task_solved у student_direct (реальная, не 2 — вторая синтетическая).
    solved_direct = [
        e for e in body["events"]
        if e["type"] == "task_solved" and e["student_id"] == ids["student_direct"]
    ]
    assert len(solved_direct) == 1
    materials_direct = [
        e for e in body["events"]
        if e["type"] == "material_studied" and e["student_id"] == ids["student_direct"]
    ]
    # Только material_direct (реальный); синтетический зачёт material_course исключён.
    assert len(materials_direct) == 1
    assert materials_direct[0]["material_id"] == ids["material_direct"]


async def test_methodist_sees_everyone(agraph, client):
    ids = agraph["ids"]
    resp = await client.get(
        _feed_url(), headers={"Authorization": f"Bearer {agraph['tokens']['methodist']}"},
    )
    assert resp.status_code == 200, resp.text
    student_ids = {e["student_id"] for e in resp.json()["events"]}
    assert ids["student_direct"] in student_ids
    assert ids["student_course"] in student_ids
    assert ids["student_other"] in student_ids


async def test_unrelated_teacher_sees_empty_feed(agraph, client):
    resp = await client.get(
        _feed_url(), headers={"Authorization": f"Bearer {agraph['tokens']['other']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["events"] == []


async def test_has_more_when_merged_total_exceeds_limit_without_single_source_capping(agraph, client):
    """Регресс: 3 источника по 2 строки каждый (6 всего) не капаются по отдельности
    ни один при limit=5, но слитая страница (топ-5) обрезает 1 реальное событие —
    has_more обязан быть True, даже если ни один источник не вернул ровно `limit` строк.
    """
    resp = await client.get(
        _feed_url(limit=5), headers={"Authorization": f"Bearer {agraph['tokens']['teacher']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["events"]) == 5
    assert body["has_more"] is True
    assert body["next_before"] is not None


async def test_limit_and_cursor_pagination(agraph, client):
    ids = agraph["ids"]
    headers = {"Authorization": f"Bearer {agraph['tokens']['teacher']}"}

    first = await client.get(_feed_url(limit=1), headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["events"]) == 1
    assert body["has_more"] is True
    assert body["next_before"] is not None

    second = await client.get(
        _feed_url(limit=100, before=body["next_before"]), headers=headers,
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    # Курсор строго "раньше" — первое событие второй страницы не совпадает с первым.
    if second_body["events"]:
        assert second_body["events"][0]["timestamp"] != body["events"][0]["timestamp"] or (
            second_body["events"][0]["student_id"] != body["events"][0]["student_id"]
        )
    # Суммарно вторая страница не содержит событие уже показанное первым (по убыванию).
    all_after = {(e["type"], e["student_id"], e["timestamp"]) for e in second_body["events"]}
    shown_first = (body["events"][0]["type"], body["events"][0]["student_id"], body["events"][0]["timestamp"])
    assert shown_first not in all_after
