"""Поиск задания в кабинете преподавателя по номеру/тексту — tsk-353.

Проверяем на НАСТОЯЩЕЙ БД оба режима поиска (номер / текст) и, главное, ACL: тот
же гейт, что у tsk-297/tsk-349 (``manual_progress_service.can_edit_progress``) —
teacher видит только задания, которые СТУДЕНТ реально может достичь (его
дерево курсов) И которые попадают под ACL преподавателя (прямая связка
ученик-учитель ИЛИ иерархический ``teacher_courses``). Задание вне ACL или вне
дерева ученика должно быть НЕВИДИМО в результатах — иначе клик по нему вёл бы
в тупиковый 404 на эндпоинте истории (tsk-349 follow-up).

Граф фикстуры::

    root (top-level; teacher2 ACL здесь через teacher_courses)
      └── sub (child курс, course_parents)
            └── task_sub  — текст содержит уникальный маркер
    unrelated (top-level; БЕЗ ACL у teacher2, student1 НЕ записан)
      └── task_unrelated — другой уникальный маркер

    student1 записан на root (user_courses). teacher связан со student1 напрямую
    (student_teacher_links) — видит всё в контексте student1, включая task_unrelated
    (прямая связка «ученик закреплён» не завязана на конкретный курс — см.
    ``can_edit_progress``, документированное поведение). teacher2 привязан к root
    только через ``teacher_courses`` (без прямой связки с учеником) — видит
    task_sub (root → sub по иерархии), но НЕ task_unrelated (курс вне его ACL).
    teacher3 не связан ни с чем — не видит ничего.
"""
from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_TAG = "tsk353"


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


@pytest.fixture
async def sgraph(db):
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

        async def new_task(course_id: int, uid: str, stem: str) -> int:
            content = {"type": "SA", "stem": stem, "title": ""}
            rules = {"max_score": 10, "short_answer": {
                "accepted_answers": [{"value": "42", "score": 10}],
                "normalization": ["trim", "lower"],
            }}
            return (
                await db.execute(
                    text(
                        "INSERT INTO tasks (task_content, solution_rules, course_id, "
                        "difficulty_id, external_uid, max_score, order_position) "
                        "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, "
                        ":uid, 10, 1) RETURNING id"
                    ),
                    {
                        "tc": json.dumps(content, ensure_ascii=False),
                        "sr": json.dumps(rules),
                        "cid": course_id,
                        "did": difficulty_id,
                        "uid": f"{_TAG}-{uid}-{random.randint(10**8, 10**10)}",
                    },
                )
            ).scalar()

        ids["root"] = await new_course(f"{_TAG} корень")
        ids["sub"] = await new_course(f"{_TAG} подкурс")
        ids["unrelated"] = await new_course(f"{_TAG} чужой курс")

        await db.execute(
            text(
                "INSERT INTO course_parents (course_id, parent_course_id) "
                "VALUES (:c, :p) ON CONFLICT DO NOTHING"
            ),
            {"c": ids["sub"], "p": ids["root"]},
        )

        ids["task_sub"] = await new_task(
            ids["sub"], "sub", f"{_TAG} условие с маркером маркерсёрч7331 внутри"
        )
        ids["task_unrelated"] = await new_task(
            ids["unrelated"], "unrel", f"{_TAG} условие с маркером маркерчужой9042 внутри"
        )
        # Пара заданий для проверки экранирования ILIKE: без экранирования "%"
        # в запросе учителя работал бы как wildcard и нашёл бы ОБА (100%ok
        # буквально совпадает, а 100Xok совпадает по маске "100" + любой + "ok").
        ids["task_percent_literal"] = await new_task(
            ids["sub"], "pct-lit", f"{_TAG} процент100%okмаркер"
        )
        ids["task_percent_decoy"] = await new_task(
            ids["sub"], "pct-decoy", f"{_TAG} процент100Xokмаркер"
        )

        student1, tok_s1 = await _new_user(db, "student", "stud1")
        teacher_id, tok_teacher = await _new_user(db, "teacher", "teach")
        teacher2_id, tok_teacher2 = await _new_user(db, "teacher", "teach2")
        teacher3_id, tok_teacher3 = await _new_user(db, "teacher", "teach3")
        ids.update(
            student1=student1, teacher=teacher_id, teacher2=teacher2_id, teacher3=teacher3_id,
        )

        # student1 записан на root; teacher связан со student1 напрямую.
        await db.execute(
            text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
            {"u": student1, "c": ids["root"]},
        )
        await db.execute(
            text(
                "INSERT INTO student_teacher_links (student_id, teacher_id) "
                "VALUES (:s, :t) ON CONFLICT DO NOTHING"
            ),
            {"s": student1, "t": teacher_id},
        )
        # teacher2 привязан к root ТОЛЬКО через teacher_courses (без прямой связки с учеником).
        await db.execute(
            text(
                "INSERT INTO teacher_courses (teacher_id, course_id) "
                "VALUES (:t, :c) ON CONFLICT DO NOTHING"
            ),
            {"t": teacher2_id, "c": ids["root"]},
        )
        await db.commit()

        yield {
            "ids": ids,
            "tokens": {
                "student1": tok_s1, "teacher": tok_teacher,
                "teacher2": tok_teacher2, "teacher3": tok_teacher3,
            },
        }
    finally:
        await db.rollback()
        user_ids = [ids.get(k) for k in ("student1", "teacher", "teacher2", "teacher3") if k in ids]
        task_ids = [
            ids[k]
            for k in ("task_sub", "task_unrelated", "task_percent_literal", "task_percent_decoy")
            if k in ids
        ]
        course_ids = [ids[k] for k in ("root", "sub", "unrelated") if k in ids]
        if user_ids:
            await db.execute(text("DELETE FROM task_results WHERE user_id = ANY(:u)"), {"u": user_ids})
            await db.execute(text("DELETE FROM attempts WHERE user_id = ANY(:u)"), {"u": user_ids})
            await db.execute(text("DELETE FROM user_courses WHERE user_id = ANY(:u)"), {"u": user_ids})
            await db.execute(
                text("DELETE FROM teacher_courses WHERE teacher_id = ANY(:u)"), {"u": user_ids}
            )
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
            await db.execute(text("DELETE FROM course_parents WHERE course_id = ANY(:c)"), {"c": course_ids})
            await db.execute(text("DELETE FROM courses WHERE id = ANY(:c)"), {"c": course_ids})
        await db.commit()


def _search_path(ids) -> str:
    return f"/api/v1/teacher/students/{ids['student1']}/tasks/search"


def _search_params(q: str, *, limit: int | None = None) -> dict:
    params = {"q": q}
    if limit is not None:
        params["limit"] = limit
    return params


# ─── Режим «номер» ───────────────────────────────────────────────────────────


async def test_number_search_finds_task_within_acl(sgraph, client):
    """teacher2 (только teacher_courses на root) находит task_sub по голому числу."""
    ids = sgraph["ids"]
    resp = await client.get(
        _search_path(ids), params=_search_params(str(ids["task_sub"])),
        headers={"Authorization": f"Bearer {sgraph['tokens']['teacher2']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["task_id"] == ids["task_sub"]
    assert result["visible_id"] == f"id-{ids['task_sub']}"
    assert result["course_id"] == ids["sub"]


async def test_number_search_id_prefix_case_insensitive(sgraph, client):
    """"ID-<n>" (в любом регистре) распознаётся так же, как голое число."""
    ids = sgraph["ids"]
    resp = await client.get(
        _search_path(ids), params=_search_params(f"ID-{ids['task_sub']}"),
        headers={"Authorization": f"Bearer {sgraph['tokens']['teacher2']}"},
    )
    assert resp.status_code == 200, resp.text
    assert [r["task_id"] for r in resp.json()["results"]] == [ids["task_sub"]]


async def test_number_search_denies_task_outside_acl(sgraph, client):
    """teacher2 не находит task_unrelated: курс вне его teacher_courses ACL."""
    ids = sgraph["ids"]
    resp = await client.get(
        _search_path(ids), params=_search_params(str(ids["task_unrelated"])),
        headers={"Authorization": f"Bearer {sgraph['tokens']['teacher2']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"] == []


async def test_number_search_unknown_task_id_is_empty(sgraph, client):
    ids = sgraph["ids"]
    resp = await client.get(
        _search_path(ids), params=_search_params("999000111"),
        headers={"Authorization": f"Bearer {sgraph['tokens']['teacher2']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"] == []


# ─── Режим «текст» ────────────────────────────────────────────────────────────


async def test_text_search_finds_matching_task_within_acl(sgraph, client):
    ids = sgraph["ids"]
    resp = await client.get(
        _search_path(ids), params=_search_params("маркерсёрч7331"),
        headers={"Authorization": f"Bearer {sgraph['tokens']['teacher2']}"},
    )
    assert resp.status_code == 200, resp.text
    assert [r["task_id"] for r in resp.json()["results"]] == [ids["task_sub"]]


async def test_text_search_excludes_task_outside_acl(sgraph, client):
    """ILIKE нашёл бы task_unrelated по маркеру, но ACL-фильтр его отсеивает."""
    ids = sgraph["ids"]
    resp = await client.get(
        _search_path(ids), params=_search_params("маркерчужой9042"),
        headers={"Authorization": f"Bearer {sgraph['tokens']['teacher2']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"] == []


async def test_text_search_escapes_percent_wildcard(sgraph, client):
    """"100%okмаркер" ищется как ЛИТЕРАЛЬНАЯ строка: без экранирования "%" в
    ILIKE сработал бы как wildcard и нашёл бы decoy-задание тоже ("100Xok"
    подходит под маску "100" + любой символ + "ok")."""
    ids = sgraph["ids"]
    resp = await client.get(
        _search_path(ids), params=_search_params("процент100%okмаркер"),
        headers={"Authorization": f"Bearer {sgraph['tokens']['teacher2']}"},
    )
    assert resp.status_code == 200, resp.text
    found = [r["task_id"] for r in resp.json()["results"]]
    assert found == [ids["task_percent_literal"]]
    assert ids["task_percent_decoy"] not in found


async def test_text_search_no_match_is_empty(sgraph, client):
    ids = sgraph["ids"]
    resp = await client.get(
        _search_path(ids), params=_search_params("несуществующийтекст404"),
        headers={"Authorization": f"Bearer {sgraph['tokens']['teacher2']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"] == []


# ─── ACL: прямая связка vs отсутствие связи ──────────────────────────────────


async def test_directly_linked_teacher_sees_task_outside_own_course_acl(sgraph, client):
    """teacher связан со student1 напрямую — видит task_unrelated тоже (тот же
    инвариант, что у can_edit_progress/tsk-349: прямая связка ученик-учитель
    не завязана на конкретный курс)."""
    ids = sgraph["ids"]
    resp = await client.get(
        _search_path(ids), params=_search_params(str(ids["task_unrelated"])),
        headers={"Authorization": f"Bearer {sgraph['tokens']['teacher']}"},
    )
    assert resp.status_code == 200, resp.text
    assert [r["task_id"] for r in resp.json()["results"]] == [ids["task_unrelated"]]


async def test_unrelated_teacher_gets_empty_results(sgraph, client):
    """teacher3 не связан ни со student1, ни с одним из курсов — пусто в обоих режимах."""
    ids = sgraph["ids"]
    resp_number = await client.get(
        _search_path(ids), params=_search_params(str(ids["task_sub"])),
        headers={"Authorization": f"Bearer {sgraph['tokens']['teacher3']}"},
    )
    resp_text = await client.get(
        _search_path(ids), params=_search_params("маркерсёрч7331"),
        headers={"Authorization": f"Bearer {sgraph['tokens']['teacher3']}"},
    )
    assert resp_number.status_code == 200 and resp_number.json()["results"] == []
    assert resp_text.status_code == 200 and resp_text.json()["results"] == []


# ─── Результат поиска ведёт в существующую карточку истории (tsk-349) ───────


async def test_search_result_opens_in_existing_history_endpoint(sgraph, client):
    """Найденный task_id должен успешно открываться в эндпоинте истории tsk-349
    (не тупиковый клик) — интеграционная сверка двух эндпоинтов."""
    ids = sgraph["ids"]
    headers = {"Authorization": f"Bearer {sgraph['tokens']['teacher2']}"}
    search_resp = await client.get(
        _search_path(ids), params=_search_params(str(ids["task_sub"])), headers=headers
    )
    assert search_resp.status_code == 200
    found_task_id = search_resp.json()["results"][0]["task_id"]

    history_resp = await client.get(
        f"/api/v1/teacher/students/{ids['student1']}/tasks/{found_task_id}/history",
        headers=headers,
    )
    assert history_resp.status_code == 200, history_resp.text
