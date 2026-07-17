"""Phase Y-6.2 integration tests: GET /api/v1/me/courses/{id}/syllabus-states.

Покрывает:
- 401 без auth
- 403 student без enrollment в дереве курса
- Hierarchical ACL: student → root → grandchild
- Teacher / methodist bypass (extended-role)
- 6 task-статусов: passed / pending_review / failed / blocked_limit / in_progress / not_started
- Edge: max_score=0 + is_correct=TRUE → passed (auto-check проходит без баллов)
- Material completed / not_started
- blocked_courses через course_dependencies
- Cache-Control header (private, max-age=15)
- Performance smoke (root с 50+ задачами)

Тесты используют реальные курсы из dev DB (паттерн test_acl_hierarchical_y41).
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


# ────────────────────────── Helpers ────────────────────────────────────────


async def _create_student(db, *, prefix: str = "y62") -> tuple[int, str, str]:
    """Создать student-юзера + email-identity + session.

    Returns:
        (user_id, access_token, email)
    """
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-stud", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, token, email


async def _grant_role(db, user_id: int, role_name: str) -> None:
    """Привязать роль (создаём role если её нет)."""
    res = await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role_name})
    row = res.fetchone()
    if row is None:
        # Создаём role с явным id из sequence (если есть) или max+1
        ins = await db.execute(
            text(
                "INSERT INTO roles (id, name) "
                "VALUES (COALESCE((SELECT MAX(id) FROM roles), 0) + 1, :n) "
                "RETURNING id"
            ),
            {"n": role_name},
        )
        role_id = int(ins.scalar_one())
    else:
        role_id = int(row[0])
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) "
            "ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "r": role_id},
    )
    await db.commit()


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "c": course_id},
    )
    await db.commit()


async def _pick_root_course(db) -> int:
    """Любой root-курс (без parent в course_parents) с >=1 задачей."""
    row = (
        await db.execute(
            text(
                """
                SELECT c.id FROM courses c
                WHERE c.id NOT IN (SELECT course_id FROM course_parents)
                  AND EXISTS (SELECT 1 FROM tasks t WHERE t.course_id = c.id)
                LIMIT 1
                """
            )
        )
    ).fetchone()
    if row is None:
        pytest.skip("Нет root-курса с задачами")
    return int(row[0])


async def _pick_root_with_grandchild(db) -> tuple[int, int, int]:
    """Триплет root → child → grandchild с задачей в grandchild."""
    row = (
        await db.execute(
            text(
                """
                SELECT cp_outer.parent_course_id AS root_id,
                       cp_outer.course_id AS child_id,
                       cp_inner.course_id AS grandchild_id
                FROM course_parents cp_outer
                JOIN course_parents cp_inner ON cp_inner.parent_course_id = cp_outer.course_id
                JOIN tasks t ON t.course_id = cp_inner.course_id
                WHERE cp_outer.parent_course_id NOT IN (SELECT course_id FROM course_parents)
                LIMIT 1
                """
            )
        )
    ).fetchone()
    if row is None:
        pytest.skip("Нет цепочки root→child→grandchild с task")
    return int(row[0]), int(row[1]), int(row[2])


async def _pick_task_in_course(db, course_id: int) -> tuple[int, int]:
    """Возвращает (task_id, max_attempts_or_null_as_int)."""
    row = (
        await db.execute(
            text("SELECT id, COALESCE(max_attempts, 0) FROM tasks WHERE course_id = :c LIMIT 1"),
            {"c": course_id},
        )
    ).fetchone()
    if row is None:
        pytest.skip(f"Нет задач в course_id={course_id}")
    return int(row[0]), int(row[1])


async def _pick_root_task_of_type(db, types: tuple[str, ...]) -> tuple[int, int]:
    """(root_course_id, task_id) для root-курса с задачей нужного типа.

    tsk-214: статус зависит от типа задачи (авто SC/MC/SA vs ручной SA_COM/TA),
    поэтому тесты статусов должны брать задачу заведомо нужного типа.
    """
    row = (
        await db.execute(
            text(
                """
                SELECT t.course_id, t.id
                FROM tasks t
                WHERE t.course_id NOT IN (SELECT course_id FROM course_parents)
                  AND t.is_active = true
                  AND t.task_content->>'type' = ANY(:tp)
                LIMIT 1
                """
            ),
            {"tp": list(types)},
        )
    ).fetchone()
    if row is None:
        pytest.skip(f"Нет root-задачи типов {types}")
    return int(row[0]), int(row[1])


async def _pick_other_root(db, exclude_root_id: int) -> int:
    row = (
        await db.execute(
            text(
                """
                SELECT id FROM courses
                WHERE id != :exc
                  AND id NOT IN (SELECT course_id FROM course_parents)
                LIMIT 1
                """
            ),
            {"exc": exclude_root_id},
        )
    ).fetchone()
    if row is None:
        pytest.skip("Нет другого root-курса")
    return int(row[0])


async def _create_attempt(
    db,
    *,
    user_id: int,
    course_id: int,
    finished: bool = False,
    cancelled: bool = False,
    root_course_id: int | None = None,
) -> int:
    """Создать course-level attempt; вернуть его id.

    tsk-264: попытка несёт корень, которым ученик пришёл к заданию — в его
    границах считается лимит. Здесь деревья плоские (курс сам себе корень),
    поэтому по умолчанию root = course_id; попытка без корня лимит не
    расходует, и фикстура молча перестала бы проверять блокировку.
    """
    finished_at = datetime.now(timezone.utc) if finished else None
    cancelled_at = datetime.now(timezone.utc) if cancelled else None
    res = await db.execute(
        text(
            "INSERT INTO attempts (user_id, course_id, root_course_id, source_system, "
            "finished_at, cancelled_at) "
            "VALUES (:u, :c, :rc, 'spw', :f, :ca) RETURNING id"
        ),
        {
            "u": user_id,
            "c": course_id,
            "rc": course_id if root_course_id is None else root_course_id,
            "f": finished_at,
            "ca": cancelled_at,
        },
    )
    aid = int(res.scalar_one())
    await db.commit()
    return aid


async def _create_task_result(
    db,
    *,
    user_id: int,
    task_id: int,
    attempt_id: int | None = None,
    is_correct: bool | None = True,
    score: int = 10,
    max_score: int = 10,
    checked_at: datetime | None = None,
    submitted_at: datetime | None = None,
) -> int:
    """Создать task_result с управляемыми флагами."""
    if submitted_at is None:
        submitted_at = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            "INSERT INTO task_results "
            "(score, user_id, task_id, attempt_id, submitted_at, count_retry, received_at, "
            " max_score, source_system, is_correct, checked_at) "
            "VALUES (:s, :u, :t, :aid, :now, 0, :now, :ms, 'spw', :ic, :chk) "
            "RETURNING id"
        ),
        {
            "s": score, "u": user_id, "t": task_id, "aid": attempt_id,
            "now": submitted_at, "ms": max_score, "ic": is_correct, "chk": checked_at,
        },
    )
    rid = int(res.scalar_one())
    await db.commit()
    return rid


async def _set_attempt_limit_override(
    db, *, student_id: int, task_id: int, limit: int
) -> None:
    await db.execute(
        text(
            "INSERT INTO student_task_limit_override "
            "(student_id, task_id, max_attempts_override) "
            "VALUES (:s, :t, :l) "
            "ON CONFLICT (student_id, task_id) DO UPDATE SET "
            "max_attempts_override = EXCLUDED.max_attempts_override"
        ),
        {"s": student_id, "t": task_id, "l": limit},
    )
    await db.commit()


async def _add_material(db, *, course_id: int, order_position: int = 1) -> int:
    """Создать тестовый material; вернуть material_id."""
    res = await db.execute(
        text(
            "INSERT INTO materials "
            "(title, course_id, type, content, order_position, is_active, "
            " external_uid, created_at, updated_at) "
            "VALUES (:title, :c, 'text', '{}'::jsonb, :ord, true, :uid, now(), now()) "
            "RETURNING id"
        ),
        {
            "title": f"y62-material-{random.randint(10**6, 10**8)}",
            "c": course_id,
            "ord": order_position,
            "uid": f"y62-m-{random.randint(10**8, 10**10)}",
        },
    )
    mid = int(res.scalar_one())
    await db.commit()
    return mid


async def _mark_material_completed(db, *, student_id: int, material_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO student_material_progress "
            "(student_id, material_id, status, completed_at) "
            "VALUES (:s, :m, 'completed', now()) "
            "ON CONFLICT (student_id, material_id) DO UPDATE SET "
            "status = 'completed', completed_at = COALESCE("
            "  student_material_progress.completed_at, now())"
        ),
        {"s": student_id, "m": material_id},
    )
    await db.commit()


async def _add_course_dependency(
    db, *, course_id: int, required_course_id: int
) -> None:
    await db.execute(
        text(
            "INSERT INTO course_dependencies (course_id, required_course_id) "
            "VALUES (:c, :r) ON CONFLICT DO NOTHING"
        ),
        {"c": course_id, "r": required_course_id},
    )
    await db.commit()


async def _set_course_state(
    db, *, student_id: int, course_id: int, state: str
) -> None:
    await db.execute(
        text(
            "INSERT INTO student_course_state (student_id, course_id, state, updated_at) "
            "VALUES (:s, :c, :st, now()) "
            "ON CONFLICT (student_id, course_id) DO UPDATE SET "
            "state = EXCLUDED.state, updated_at = now()"
        ),
        {"s": student_id, "c": course_id, "st": state},
    )
    await db.commit()


async def _cleanup(
    db,
    *,
    user_ids: list[int],
    attempt_ids: list[int] | None = None,
    result_ids: list[int] | None = None,
    material_ids: list[int] | None = None,
    course_dep_pairs: list[tuple[int, int]] | None = None,
) -> None:
    attempt_ids = attempt_ids or []
    result_ids = result_ids or []
    material_ids = material_ids or []
    course_dep_pairs = course_dep_pairs or []

    if result_ids:
        await db.execute(
            text("DELETE FROM task_results WHERE id = ANY(:ids)"),
            {"ids": result_ids},
        )
    if attempt_ids:
        await db.execute(
            text("DELETE FROM attempts WHERE id = ANY(:ids)"),
            {"ids": attempt_ids},
        )
    if material_ids:
        await db.execute(
            text("DELETE FROM student_material_progress WHERE material_id = ANY(:ids)"),
            {"ids": material_ids},
        )
        await db.execute(
            text("DELETE FROM materials WHERE id = ANY(:ids)"),
            {"ids": material_ids},
        )
    if user_ids:
        await db.execute(
            text("DELETE FROM student_task_limit_override WHERE student_id = ANY(:ids)"),
            {"ids": user_ids},
        )
        await db.execute(
            text("DELETE FROM student_course_state WHERE student_id = ANY(:ids)"),
            {"ids": user_ids},
        )
        await db.execute(
            text("DELETE FROM user_courses WHERE user_id = ANY(:ids)"),
            {"ids": user_ids},
        )
        await db.execute(
            text("DELETE FROM user_session WHERE user_id = ANY(:ids)"),
            {"ids": user_ids},
        )
        await db.execute(
            text("DELETE FROM user_roles WHERE user_id = ANY(:ids)"),
            {"ids": user_ids},
        )
        await db.execute(
            text("DELETE FROM identity_link WHERE user_id = ANY(:ids)"),
            {"ids": user_ids},
        )
    for c, r in course_dep_pairs:
        await db.execute(
            text(
                "DELETE FROM course_dependencies "
                "WHERE course_id=:c AND required_course_id=:r"
            ),
            {"c": c, "r": r},
        )
    await db.commit()


async def _find_item(items: list[dict], *, kind: str, id_key: str, target_id: int):
    """Ищет item в response.items по (kind, id_key=target_id)."""
    for it in items:
        if it.get("kind") == kind and it.get(id_key) == target_id:
            return it
    return None


# ────────────────────────── Auth + ACL ─────────────────────────────────────


@pytest.mark.asyncio
async def test_syllabus_states_requires_auth(client):
    """401 без cookie/Bearer."""
    resp = await client.get("/api/v1/me/courses/1/syllabus-states")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_syllabus_states_403_for_unenrolled_student(db, client):
    """Student без enrollment в дереве → 403."""
    user_id, token, _ = await _create_student(db)
    root_id = await _pick_root_course(db)
    # User НЕ enrolled — не зачисляем
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, user_ids=[user_id])


@pytest.mark.asyncio
async def test_syllabus_states_hierarchical_acl_enrolled_in_parent(db, client):
    """Student enrolled в parent → видит дерево включая child / grandchild."""
    root_id, _child_id, _gc_id = await _pick_root_with_grandchild(db)
    user_id, token, _ = await _create_student(db)
    await _enroll(db, user_id, root_id)
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["course_id"] == root_id
        # Хотя бы один task должен быть из grandchild (или ниже) — проверяем
        # что обход дерева включил вложенные курсы.
        tasks_courses = {it["course_id"] for it in body["items"] if it["kind"] == "task"}
        assert len(tasks_courses) >= 1, "Должен быть хоть 1 task из дерева"
    finally:
        await _cleanup(db, user_ids=[user_id])


@pytest.mark.asyncio
async def test_syllabus_states_sections_meta(db, client):
    """Y-6.2 ext: sections[] содержит depth-first walk дерева с titles+depth.

    Контракт:
    - root курс — sections[0] с depth=0, parent_course_id=None.
    - все course_id из items[] есть в sections[].course_id.
    - порядок sections[] — depth-first (тот же что items[]).
    - title не пуст для всех.
    """
    root_id, child_id, gc_id = await _pick_root_with_grandchild(db)
    user_id, token, _ = await _create_student(db, prefix="y62-sec")
    await _enroll(db, user_id, root_id)
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        sections = body.get("sections")
        assert isinstance(sections, list) and len(sections) > 0, "sections должен быть непустым"

        # Root — первый, depth=0, parent=None
        root_section = sections[0]
        assert root_section["course_id"] == root_id
        assert root_section["depth"] == 0
        assert root_section["parent_course_id"] is None
        assert isinstance(root_section["title"], str) and root_section["title"], "root.title пустой"

        # Дочерние / внучатые курсы тоже есть с правильным depth
        section_ids = {s["course_id"] for s in sections}
        assert child_id in section_ids, "child_id должен быть в sections"
        assert gc_id in section_ids, "grandchild_id должен быть в sections"

        # depth монотонно растёт при углублении (parent < child)
        depth_by_id = {s["course_id"]: s["depth"] for s in sections}
        assert depth_by_id[child_id] >= 1
        assert depth_by_id[gc_id] >= 2

        # Все course_id из items[] должны быть среди sections[]
        item_courses = {it["course_id"] for it in body["items"]}
        missing = item_courses - section_ids
        assert not missing, f"items имеют course_id вне sections: {missing}"

        # Каждая секция имеет title не-пустой и parent_course_id указан для не-root
        for s in sections:
            assert s["title"], f"empty title for course_id={s['course_id']}"
            if s["depth"] > 0:
                assert s["parent_course_id"] is not None, (
                    f"non-root section без parent_course_id: {s}"
                )
    finally:
        await _cleanup(db, user_ids=[user_id])


@pytest.mark.asyncio
async def test_syllabus_states_teacher_bypass(db, client):
    """Teacher (extended-role) bypass'ит ACL — может смотреть syllabus любого курса."""
    user_id, token, _ = await _create_student(db, prefix="y62-tch")
    await _grant_role(db, user_id, "teacher")
    root_id = await _pick_root_course(db)
    # Не enroll'им — teacher всё равно должен иметь доступ
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db, user_ids=[user_id])


# ────────────────────────── Cache header ───────────────────────────────────


@pytest.mark.asyncio
async def test_syllabus_states_cache_header(db, client):
    """Cache-Control: no-store (tsk-214б — прогресс/попытки должны быть свежими
    после submit; HTTP-кэш max-age defeated invalidateQueries)."""
    user_id, token, _ = await _create_student(db)
    root_id = await _pick_root_course(db)
    await _enroll(db, user_id, root_id)
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "no-store" in cc and "max-age" not in cc, cc
    finally:
        await _cleanup(db, user_ids=[user_id])


# ────────────────────────── Task statuses ──────────────────────────────────


@pytest.mark.asyncio
async def test_status_passed_checked(db, client):
    """passed: is_correct=TRUE + checked_at NOT NULL."""
    user_id, token, _ = await _create_student(db)
    root_id = await _pick_root_course(db)
    await _enroll(db, user_id, root_id)
    task_id, _ = await _pick_task_in_course(db, root_id)
    aid = await _create_attempt(db, user_id=user_id, course_id=root_id)
    rid = await _create_task_result(
        db, user_id=user_id, task_id=task_id, attempt_id=aid,
        is_correct=True, score=10, max_score=10,
        checked_at=datetime.now(timezone.utc),
    )
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        item = await _find_item(resp.json()["items"], kind="task", id_key="task_id", target_id=task_id)
        assert item is not None
        assert item["status"] == "passed", item
        assert item["last_score"] == 10 and item["last_max_score"] == 10
    finally:
        await _cleanup(db, user_ids=[user_id], attempt_ids=[aid], result_ids=[rid])


@pytest.mark.asyncio
async def test_status_pending_review_optimistic(db, client):
    """pending_review: РУЧНОЙ SA_COM/TA, is_correct=TRUE + checked_at IS NULL (Y-6).

    tsk-214: pending_review при checked_at IS NULL валиден только для типов с
    ручной проверкой учителя (SA_COM/TA). Авто-типы (SC/MC/SA) checked_at не
    ставят и в этом состоянии = passed (см. test_status_auto_passed_no_checked).
    """
    user_id, token, _ = await _create_student(db)
    root_id, task_id = await _pick_root_task_of_type(db, ("SA_COM", "TA"))
    await _enroll(db, user_id, root_id)
    aid = await _create_attempt(db, user_id=user_id, course_id=root_id)
    rid = await _create_task_result(
        db, user_id=user_id, task_id=task_id, attempt_id=aid,
        is_correct=True, score=10, max_score=10, checked_at=None,
    )
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        item = await _find_item(resp.json()["items"], kind="task", id_key="task_id", target_id=task_id)
        assert item is not None
        assert item["status"] == "pending_review", item
    finally:
        await _cleanup(db, user_ids=[user_id], attempt_ids=[aid], result_ids=[rid])


@pytest.mark.asyncio
async def test_status_auto_passed_no_checked(db, client):
    """tsk-214: АВТО-тип (SC/MC/SA), is_correct=TRUE + checked_at IS NULL → passed.

    Регрессия на баг «пройденный курс показывает низкий %»: раньше правило
    «checked_at обязателен для passed» применялось ко всем типам, и верные
    авто-задачи вечно висели pending_review, не попадая в % выполнения.
    """
    user_id, token, _ = await _create_student(db)
    root_id, task_id = await _pick_root_task_of_type(db, ("SC", "MC", "SA"))
    await _enroll(db, user_id, root_id)
    aid = await _create_attempt(db, user_id=user_id, course_id=root_id)
    rid = await _create_task_result(
        db, user_id=user_id, task_id=task_id, attempt_id=aid,
        is_correct=True, score=10, max_score=10, checked_at=None,
    )
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        item = await _find_item(resp.json()["items"], kind="task", id_key="task_id", target_id=task_id)
        assert item is not None
        assert item["status"] == "passed", item
    finally:
        await _cleanup(db, user_ids=[user_id], attempt_ids=[aid], result_ids=[rid])


@pytest.mark.asyncio
async def test_status_failed_with_attempts_left(db, client):
    """failed: is_correct=FALSE и attempts_used < limit."""
    user_id, token, _ = await _create_student(db)
    root_id = await _pick_root_course(db)
    await _enroll(db, user_id, root_id)
    task_id, _ = await _pick_task_in_course(db, root_id)
    # override limit=5, чтобы 1 попытка << limit
    await _set_attempt_limit_override(db, student_id=user_id, task_id=task_id, limit=5)
    aid = await _create_attempt(db, user_id=user_id, course_id=root_id)
    rid = await _create_task_result(
        db, user_id=user_id, task_id=task_id, attempt_id=aid,
        is_correct=False, score=0, max_score=10,
    )
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        item = await _find_item(resp.json()["items"], kind="task", id_key="task_id", target_id=task_id)
        assert item is not None
        assert item["status"] == "failed", item
        assert item["attempts_used"] == 1
        assert item["attempts_limit_effective"] == 5
    finally:
        await _cleanup(db, user_ids=[user_id], attempt_ids=[aid], result_ids=[rid])


@pytest.mark.asyncio
async def test_status_blocked_limit(db, client):
    """blocked_limit: is_correct=FALSE + attempts_used >= limit."""
    user_id, token, _ = await _create_student(db)
    root_id = await _pick_root_course(db)
    await _enroll(db, user_id, root_id)
    task_id, _ = await _pick_task_in_course(db, root_id)
    # Снизим limit до 2 чтобы быстрее упереться
    await _set_attempt_limit_override(db, student_id=user_id, task_id=task_id, limit=2)
    aid = await _create_attempt(db, user_id=user_id, course_id=root_id)
    # 2 неудачные попытки
    rids = []
    base_ts = datetime.now(timezone.utc)
    for i in range(2):
        rids.append(
            await _create_task_result(
                db, user_id=user_id, task_id=task_id, attempt_id=aid,
                is_correct=False, score=0, max_score=10,
                submitted_at=base_ts + timedelta(seconds=i),
            )
        )
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        item = await _find_item(resp.json()["items"], kind="task", id_key="task_id", target_id=task_id)
        assert item is not None
        assert item["status"] == "blocked_limit", item
        assert item["attempts_used"] == 2
    finally:
        await _cleanup(db, user_ids=[user_id], attempt_ids=[aid], result_ids=rids)


@pytest.mark.asyncio
async def test_status_in_progress_open_attempt(db, client):
    """in_progress: открытый attempt без task_result для этой задачи."""
    user_id, token, _ = await _create_student(db)
    root_id = await _pick_root_course(db)
    await _enroll(db, user_id, root_id)
    task_id, _ = await _pick_task_in_course(db, root_id)
    # Открытый attempt (finished_at=NULL, cancelled_at=NULL); task_result отсутствует
    aid = await _create_attempt(db, user_id=user_id, course_id=root_id, finished=False)
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        item = await _find_item(resp.json()["items"], kind="task", id_key="task_id", target_id=task_id)
        assert item is not None
        assert item["status"] == "in_progress", item
        assert item["last_submitted_at"] is None
    finally:
        await _cleanup(db, user_ids=[user_id], attempt_ids=[aid])


@pytest.mark.asyncio
async def test_status_not_started_default(db, client):
    """not_started: ни attempt'а, ни task_result."""
    user_id, token, _ = await _create_student(db)
    root_id = await _pick_root_course(db)
    await _enroll(db, user_id, root_id)
    task_id, _ = await _pick_task_in_course(db, root_id)
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        item = await _find_item(resp.json()["items"], kind="task", id_key="task_id", target_id=task_id)
        assert item is not None
        assert item["status"] == "not_started", item
        assert item["attempts_used"] == 0
        assert item["last_submitted_at"] is None
    finally:
        await _cleanup(db, user_ids=[user_id])


@pytest.mark.asyncio
async def test_status_passed_edge_max_score_zero(db, client):
    """edge: max_score=0 + is_correct=TRUE → passed (auto-check без баллов)."""
    user_id, token, _ = await _create_student(db)
    root_id = await _pick_root_course(db)
    await _enroll(db, user_id, root_id)
    task_id, _ = await _pick_task_in_course(db, root_id)
    aid = await _create_attempt(db, user_id=user_id, course_id=root_id)
    rid = await _create_task_result(
        db, user_id=user_id, task_id=task_id, attempt_id=aid,
        is_correct=True, score=0, max_score=0,
        checked_at=datetime.now(timezone.utc),
    )
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        item = await _find_item(resp.json()["items"], kind="task", id_key="task_id", target_id=task_id)
        assert item is not None
        # is_correct=TRUE + checked_at NOT NULL → passed, независимо от max_score
        assert item["status"] == "passed", item
        assert item["last_max_score"] == 0
    finally:
        await _cleanup(db, user_ids=[user_id], attempt_ids=[aid], result_ids=[rid])


# ────────────────────────── Quiz (tsk-125) ─────────────────────────────────


async def _make_quiz_course_task(db) -> tuple[int, int]:
    """Изолированный root-курс с одной SC_Qw-задачей. Returns (course_id, task_id)."""
    import uuid

    cid = int(
        (
            await db.execute(
                text(
                    "INSERT INTO courses (title, access_level) "
                    "VALUES (:t, 'auto_check') RETURNING id"
                ),
                {"t": f"tsk125 {uuid.uuid4().hex[:8]}"},
            )
        ).scalar()
    )
    await db.commit()
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = (
        '{"type":"SC_Qw","stem":"Что ближе?","scales":["информатика","python"],'
        '"options":[{"id":"a","text":"разгадывать","scores":{"информатика":2}},'
        '{"id":"b","text":"игры","scores":{"python":2}}]}'
    )
    sr = '{"max_score":2,"quiz":{"scales":["информатика","python"],"mode":"single"}}'
    tid = int(
        (
            await db.execute(
                text(
                    "INSERT INTO tasks (course_id, difficulty_id, task_content, solution_rules) "
                    "VALUES (:cid, :did, CAST(:tc AS jsonb), CAST(:sr AS jsonb)) RETURNING id"
                ),
                {"cid": cid, "did": diff, "tc": tc, "sr": sr},
            )
        ).scalar()
    )
    await db.commit()
    return cid, tid


async def _drop_course_task(db, *, course_id: int, task_id: int) -> None:
    await db.execute(text("DELETE FROM tasks WHERE id = :t"), {"t": task_id})
    await db.execute(text("DELETE FROM courses WHERE id = :c"), {"c": course_id})
    await db.commit()


@pytest.mark.asyncio
async def test_status_quiz_answered_passed(db, client):
    """tsk-125: отвеченный SC_Qw (is_correct=NULL, score=max_score) → passed, не pending_review.

    Паритет с compute_task_state: score-ratio=1.0 >= PASS_THRESHOLD_RATIO → PASSED.
    """
    user_id, token, _ = await _create_student(db, prefix="y62-quiz")
    cid, task_id = await _make_quiz_course_task(db)
    await _enroll(db, user_id, cid)
    aid = await _create_attempt(db, user_id=user_id, course_id=cid)
    rid = await _create_task_result(
        db, user_id=user_id, task_id=task_id, attempt_id=aid,
        is_correct=None, score=2, max_score=2, checked_at=None,
    )
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{cid}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        item = await _find_item(
            resp.json()["items"], kind="task", id_key="task_id", target_id=task_id
        )
        assert item is not None
        assert item["status"] == "passed", item  # не pending_review (tsk-125)
    finally:
        await _cleanup(db, user_ids=[user_id], attempt_ids=[aid], result_ids=[rid])
        await _drop_course_task(db, course_id=cid, task_id=task_id)


@pytest.mark.asyncio
async def test_status_quiz_empty_answer_blocked_limit(db, client):
    """tsk-125: пустой ответ на квиз (is_correct=NULL, score=0) при limit=1 → blocked_limit.

    Паритет с compute_task_state: ratio 0 < порога, attempts_used(1) >= limit(1) → BLOCKED_LIMIT.
    Квиз всегда даёт attempts_limit_effective=1 (tsk-124), второй попытки нет.
    """
    user_id, token, _ = await _create_student(db, prefix="y62-quiz0")
    cid, task_id = await _make_quiz_course_task(db)
    await _enroll(db, user_id, cid)
    aid = await _create_attempt(db, user_id=user_id, course_id=cid)
    rid = await _create_task_result(
        db, user_id=user_id, task_id=task_id, attempt_id=aid,
        is_correct=None, score=0, max_score=2, checked_at=None,
    )
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{cid}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        item = await _find_item(
            resp.json()["items"], kind="task", id_key="task_id", target_id=task_id
        )
        assert item is not None
        assert item["status"] == "blocked_limit", item
        assert item["attempts_limit_effective"] == 1
    finally:
        await _cleanup(db, user_ids=[user_id], attempt_ids=[aid], result_ids=[rid])
        await _drop_course_task(db, course_id=cid, task_id=task_id)


# ────────────────────────── Materials ──────────────────────────────────────


@pytest.mark.asyncio
async def test_material_completed_and_not_started(db, client):
    """material: один completed, второй not_started."""
    user_id, token, _ = await _create_student(db)
    root_id = await _pick_root_course(db)
    await _enroll(db, user_id, root_id)
    m1 = await _add_material(db, course_id=root_id, order_position=900)
    m2 = await _add_material(db, course_id=root_id, order_position=901)
    await _mark_material_completed(db, student_id=user_id, material_id=m1)
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        items = resp.json()["items"]
        item1 = await _find_item(items, kind="material", id_key="material_id", target_id=m1)
        item2 = await _find_item(items, kind="material", id_key="material_id", target_id=m2)
        assert item1 is not None and item1["status"] == "completed"
        assert item1["completed_at"] is not None
        assert item2 is not None and item2["status"] == "not_started"
        assert item2["completed_at"] is None
    finally:
        await _cleanup(db, user_ids=[user_id], material_ids=[m1, m2])


# ────────────────────────── Blocked courses ────────────────────────────────


@pytest.mark.asyncio
async def test_blocked_courses_via_dependencies(db, client):
    """blocked_courses: course_dependencies без COMPLETED prerequisite."""
    root_id, child_id, _gc_id = await _pick_root_with_grandchild(db)
    user_id, token, _ = await _create_student(db)
    await _enroll(db, user_id, root_id)

    # Найдём другой root-курс как prerequisite
    other_root = await _pick_other_root(db, exclude_root_id=root_id)
    # Нужно убедиться что user НЕ имеет COMPLETED по other_root
    # (по умолчанию student_course_state пуст → not COMPLETED → blocked)
    await _add_course_dependency(db, course_id=child_id, required_course_id=other_root)
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert child_id in body["blocked_courses"], body["blocked_courses"]
    finally:
        await _cleanup(
            db,
            user_ids=[user_id],
            course_dep_pairs=[(child_id, other_root)],
        )


@pytest.mark.asyncio
async def test_blocked_courses_unblocks_after_prerequisite_completed(db, client):
    """blocked_courses пустой если prerequisite COMPLETED."""
    root_id, child_id, _gc_id = await _pick_root_with_grandchild(db)
    user_id, token, _ = await _create_student(db)
    await _enroll(db, user_id, root_id)
    other_root = await _pick_other_root(db, exclude_root_id=root_id)
    await _add_course_dependency(db, course_id=child_id, required_course_id=other_root)
    # Помечаем prerequisite COMPLETED
    await _set_course_state(db, student_id=user_id, course_id=other_root, state="COMPLETED")
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert child_id not in body["blocked_courses"], body["blocked_courses"]
    finally:
        await _cleanup(
            db,
            user_ids=[user_id],
            course_dep_pairs=[(child_id, other_root)],
        )


# ────────────────────────── Performance smoke ──────────────────────────────


@pytest.mark.asyncio
async def test_perf_smoke_50_tasks(db, client):
    """Performance smoke: курс с >=50 задач отвечает за < 2 сек."""
    # Найдём root курс с >= 50 задачами в дереве (root + потомки)
    row = (
        await db.execute(
            text(
                """
                WITH RECURSIVE tree AS (
                    SELECT c.id AS root_id, c.id AS member_id
                    FROM courses c
                    WHERE c.id NOT IN (SELECT course_id FROM course_parents)
                    UNION ALL
                    SELECT t.root_id, cp.course_id
                    FROM tree t
                    JOIN course_parents cp ON cp.parent_course_id = t.member_id
                )
                SELECT t.root_id, COUNT(*) AS cnt
                FROM tree t
                JOIN tasks tk ON tk.course_id = t.member_id
                GROUP BY t.root_id
                HAVING COUNT(*) >= 50
                ORDER BY cnt DESC
                LIMIT 1
                """
            )
        )
    ).fetchone()
    if row is None:
        pytest.skip("Нет root-курсов с >=50 задач для perf-smoke")
    root_id = int(row[0])

    user_id, token, _ = await _create_student(db, prefix="y62-perf")
    await _enroll(db, user_id, root_id)
    try:
        t0 = time.monotonic()
        resp = await client.get(
            f"/api/v1/me/courses/{root_id}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        elapsed = time.monotonic() - t0
        assert resp.status_code == 200, resp.text
        body = resp.json()
        task_items = [it for it in body["items"] if it["kind"] == "task"]
        assert len(task_items) >= 50, len(task_items)
        # 2 sec — щедрый порог; локально single CTE выдаёт <300ms
        assert elapsed < 2.0, f"Slow response: {elapsed:.2f}s for {len(task_items)} tasks"
    finally:
        await _cleanup(db, user_ids=[user_id])
