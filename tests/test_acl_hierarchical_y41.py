"""Integration HTTP-тесты Y-4.1: иерархический REVIEW_ACL_SQL + HELP_REQUESTS_ACL_SQL
через course_parents.

Проверяет, что teacher, привязанный к ROOT-курсу через teacher_courses,
автоматически видит pending review-заявки и help-requests для всех потомков
в иерархии course_parents — без выдачи widely-scoped methodist-роли.

Сценарии (минимум 6 + smoke):
1. teacher на root → видит pending review на grandchild
2. teacher на root → видит pending review на direct child
3. teacher на чужом root → НЕ видит review на child другого root
4. /teacher/reviews/pending-count корректно учитывает hierarchical ACL
5. methodist-bypass работает (regression escape hatch)
6. help-request на child course видится teacher'ом на root через hierarchical
7. teacher на course=root + task на course=root → видит (regression на ровный case)
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


# ──────────────────────── Fixtures helpers ─────────────────────────────────

async def _setup_teacher(db, *, with_methodist: bool = False, course_id: int | None = None):
    """Создать teacher + session + опц. teacher_courses привязку (только root!)
    + опц. methodist роль.
    """
    teacher = Users(
        email=f"y41-tch-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="Y4.1-teacher", tg_id=None,
    )
    db.add(teacher)
    await db.flush()
    await identity_link_service.upsert_identity(db, teacher.id, "email", teacher.email)
    token, _, _ = await create_session(db, user_id=teacher.id)
    if course_id is not None:
        await db.execute(
            text(
                "INSERT INTO teacher_courses (teacher_id, course_id, linked_at) "
                "VALUES (:t, :c, now()) ON CONFLICT DO NOTHING"
            ),
            {"t": teacher.id, "c": course_id},
        )
    if with_methodist:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, id FROM roles WHERE name='methodist' "
                "ON CONFLICT DO NOTHING"
            ),
            {"u": teacher.id},
        )
    await db.commit()
    return teacher.id, token


async def _create_student(db) -> int:
    u = Users(
        email=f"y41-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="y41-stud", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _create_pending_tr(db, *, user_id: int, task_id: int) -> int:
    """Создать pending task_result (is_correct=TRUE, checked_at NULL, без захвата).

    tsk-210: под Y-6 реальный pending на вторичную проверку = is_correct=TRUE
    (optimistic-PASSED для TA / первично-верный SA_COM). Очередь и счётчики
    фильтруются `is_correct IS TRUE`; is_correct=NULL (стар. Y-4.2) больше не
    отражает pending-состояние.
    """
    now = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            "INSERT INTO task_results "
            "(score, user_id, task_id, submitted_at, count_retry, received_at, "
            " max_score, source_system, is_correct) "
            "VALUES (10, :u, :t, :now, 0, :now, 10, 'spw', TRUE) RETURNING id"
        ),
        {"u": user_id, "t": task_id, "now": now},
    )
    rid = res.scalar_one()
    await db.commit()
    return rid


async def _pick_root_with_grandchild(db) -> tuple[int, int, int]:
    """Найти триплет (root_id, child_id, grandchild_id_with_task) — реальные данные.

    На 2026-04-30: chain `1 (PY) → 7 → 10`; task на course_id=10.
    Возвращает (root, child, grandchild_with_task_id).
    """
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
                  AND t.task_content->>'type' IN ('SA_COM', 'TA')
                LIMIT 1
                """
            )
        )
    ).fetchone()
    if row is None:
        pytest.skip("Нет цепочки root→child→grandchild с task в БД")
    return int(row[0]), int(row[1]), int(row[2])


async def _pick_task_in_course(db, course_id: int) -> int:
    row = (
        await db.execute(
            text(
                "SELECT id FROM tasks "
                "WHERE course_id = :c "
                "AND task_content->>'type' IN ('SA_COM', 'TA') "
                "LIMIT 1"
            ),
            {"c": course_id},
        )
    ).fetchone()
    if row is None:
        pytest.skip(f"Нет review-задач SA_COM/TA в course_id={course_id}")
    return int(row[0])


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
        pytest.skip("Нет другого root-курса для теста")
    return int(row[0])


async def _cleanup(db, *, teacher_id: int, student_id: int | None = None, rids: list[int] = None):
    rids = rids or []
    if rids:
        await db.execute(text("DELETE FROM task_results WHERE id = ANY(:r)"), {"r": rids})
    await db.execute(text("DELETE FROM teacher_courses WHERE teacher_id=:t"), {"t": teacher_id})
    await db.execute(text("DELETE FROM user_roles WHERE user_id=:t"), {"t": teacher_id})
    await db.execute(text("DELETE FROM user_session WHERE user_id=:t"), {"t": teacher_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:t"), {"t": teacher_id})
    if student_id is not None:
        await db.execute(text("DELETE FROM user_session WHERE user_id=:s"), {"s": student_id})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:s"), {"s": student_id})
    await db.commit()


# ──────────────────────── Tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_teacher_root_sees_grandchild_review_pending(db, client):
    """Teacher привязан к root; pending review на grandchild → видим через recursive ACL."""
    root_id, _child_id, grandchild_id = await _pick_root_with_grandchild(db)
    task_id = await _pick_task_in_course(db, grandchild_id)
    teacher_id, token = await _setup_teacher(db, course_id=root_id)
    student_id = await _create_student(db)
    rid = await _create_pending_tr(db, user_id=student_id, task_id=task_id)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending-count?teacher_id={teacher_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] >= 1, (
            f"teacher на root={root_id} должен видеть pending на grandchild={grandchild_id}; "
            f"count={body['count']}"
        )
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_id=student_id, rids=[rid])


@pytest.mark.asyncio
async def test_teacher_root_sees_direct_child_review_pending(db, client):
    """Teacher на root; task на direct child — видим через 1 уровень ancestor_chain."""
    root_id, child_id, _grandchild_id = await _pick_root_with_grandchild(db)
    # Возьмём task непосредственно из child (depth=1)
    row = (
        await db.execute(
            text(
                "SELECT id FROM tasks "
                "WHERE course_id = :c "
                "AND task_content->>'type' IN ('SA_COM', 'TA') "
                "LIMIT 1"
            ),
            {"c": child_id},
        )
    ).fetchone()
    if row is None:
        pytest.skip(f"Нет задач непосредственно в child course_id={child_id}")
    task_id = int(row[0])
    teacher_id, token = await _setup_teacher(db, course_id=root_id)
    student_id = await _create_student(db)
    rid = await _create_pending_tr(db, user_id=student_id, task_id=task_id)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending-count?teacher_id={teacher_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_id=student_id, rids=[rid])


@pytest.mark.asyncio
async def test_teacher_unrelated_root_does_not_see_other_branch(db, client):
    """Teacher на чужом root → НЕ видит review в чужой ветке."""
    root_id, _child_id, grandchild_id = await _pick_root_with_grandchild(db)
    other_root = await _pick_other_root(db, exclude_root_id=root_id)
    task_id = await _pick_task_in_course(db, grandchild_id)
    teacher_id, token = await _setup_teacher(db, course_id=other_root)
    student_id = await _create_student(db)
    rid = await _create_pending_tr(db, user_id=student_id, task_id=task_id)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending-count?teacher_id={teacher_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Teacher на other_root не должен видеть pending в чужой ветке через
        # эту единственную привязку. Если в other_root тоже есть свои pending —
        # они НЕ от нашего вставленного rid; но мы не можем гарантировать
        # что other_root абсолютно пуст. Проверяем что наш rid НЕ среди
        # видимых через детальный claim-next запрос.
        claim = await client.post(
            "/api/v1/teacher/reviews/claim-next",
            json={"teacher_id": teacher_id, "ttl_sec": 60},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert claim.status_code == 200
        cb = claim.json()
        if not cb.get("empty"):
            # Если что-то выдалось, это НЕ наш rid (наш rid в чужой ветке)
            assert cb["item"]["id"] != rid, (
                "teacher на other_root не должен захватить наш rid из чужой ветки"
            )
            # Освобождаем для следующих тестов
            await client.post(
                f"/api/v1/teacher/reviews/{cb['item']['id']}/release",
                json={"teacher_id": teacher_id, "lock_token": cb["lock_token"]},
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_id=student_id, rids=[rid])


@pytest.mark.asyncio
async def test_pending_count_uses_hierarchical_acl(db, client):
    """Y-4 endpoint /teacher/reviews/pending-count + Y-4.1 hierarchical = composed correctly."""
    root_id, _child_id, grandchild_id = await _pick_root_with_grandchild(db)
    task_id = await _pick_task_in_course(db, grandchild_id)
    teacher_id, token = await _setup_teacher(db, course_id=root_id)
    student_id = await _create_student(db)
    # Создадим 3 pending в одной ветке
    rids = [
        await _create_pending_tr(db, user_id=student_id, task_id=task_id)
        for _ in range(3)
    ]
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending-count?teacher_id={teacher_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] >= 3
        assert body["oldest_received_at"] is not None
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_id=student_id, rids=rids)


@pytest.mark.asyncio
async def test_methodist_bypass_still_works(db, client):
    """Regression: methodist без teacher_courses видит всё (escape hatch)."""
    root_id, _child_id, grandchild_id = await _pick_root_with_grandchild(db)
    task_id = await _pick_task_in_course(db, grandchild_id)
    # methodist БЕЗ teacher_courses
    teacher_id, token = await _setup_teacher(db, with_methodist=True)
    student_id = await _create_student(db)
    rid = await _create_pending_tr(db, user_id=student_id, task_id=task_id)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending-count?teacher_id={teacher_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        # methodist видит весь pool — count >= 1 (наш rid + любые legacy)
        assert resp.json()["count"] >= 1
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_id=student_id, rids=[rid])


@pytest.mark.asyncio
async def test_teacher_self_attached_root_with_root_task_still_works(db, client):
    """Regression на ровный (depth=0) случай: teacher на course X + task на course X → видит."""
    # Используем root_id; task на root напрямую (не на child)
    root_id, _child, _gchild = await _pick_root_with_grandchild(db)
    row = (
        await db.execute(
            text(
                "SELECT id FROM tasks "
                "WHERE course_id = :c "
                "AND task_content->>'type' IN ('SA_COM', 'TA') "
                "LIMIT 1"
            ),
            {"c": root_id},
        )
    ).fetchone()
    if row is None:
        pytest.skip(f"Нет задач непосредственно в root course_id={root_id}")
    task_id = int(row[0])
    teacher_id, token = await _setup_teacher(db, course_id=root_id)
    student_id = await _create_student(db)
    rid = await _create_pending_tr(db, user_id=student_id, task_id=task_id)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending-count?teacher_id={teacher_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        # ancestor_chain для root = {root}; teacher на root → совпадает → видит
        assert resp.json()["count"] >= 1
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_id=student_id, rids=[rid])


@pytest.mark.asyncio
async def test_teacher_course_acl_bind_variant_smoke(db):
    """M1 follow-up: smoke на bind-вариант helper'а — `:course_id` в seed-row.

    `help_requests_service.can_access_help_request` использует
    `text(f"SELECT 1 WHERE {teacher_course_acl(':course_id')}")` — bind-параметр
    в seed-row WITH RECURSIVE. SQL формально валиден, но требует smoke
    на реальной БД через asyncpg.
    """
    from app.services.teacher_queue_service import teacher_course_acl

    root_id, _child_id, grandchild_id = await _pick_root_with_grandchild(db)
    other_root = await _pick_other_root(db, exclude_root_id=root_id)
    teacher_id, _token = await _setup_teacher(db, course_id=root_id)
    try:
        # 1. teacher на root + course=grandchild → recursive chain находит → 1 row
        sql = f"SELECT 1 WHERE {teacher_course_acl(':course_id')}"
        positive = (
            await db.execute(text(sql), {"teacher_id": teacher_id, "course_id": grandchild_id})
        ).fetchone()
        assert positive is not None, (
            f"bind-вариант helper'а должен находить teacher_id={teacher_id} на root_id={root_id} "
            f"для course_id={grandchild_id} через ancestor_chain"
        )

        # 2. teacher на root + course=other_root (чужая ветка) → 0 rows
        negative = (
            await db.execute(text(sql), {"teacher_id": teacher_id, "course_id": other_root})
        ).fetchone()
        assert negative is None, (
            f"teacher_id={teacher_id} на root={root_id} НЕ должен видеть чужой course_id={other_root}"
        )

        # 3. teacher на root + course=root (depth=0) → 1 row (regression)
        zero_depth = (
            await db.execute(text(sql), {"teacher_id": teacher_id, "course_id": root_id})
        ).fetchone()
        assert zero_depth is not None, (
            f"depth=0 (course_id=root_id={root_id}) должен возвращать row"
        )
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_id=None, rids=[])


@pytest.mark.asyncio
async def test_help_request_hierarchical_acl_via_teacher_courses(db):
    """Help-request на child-курсе виден teacher'у на root через HELP_REQUESTS_ACL_SQL.

    Проверяем напрямую через сервисный SQL (минуя HTTP endpoint, у которого
    иной auth-flow для help-requests). Тест проверяет именно ACL-фильтр.
    """
    from app.services.teacher_queue_service import HELP_REQUESTS_ACL_SQL

    root_id, _child_id, grandchild_id = await _pick_root_with_grandchild(db)
    task_id = await _pick_task_in_course(db, grandchild_id)
    teacher_id, _token = await _setup_teacher(db, course_id=root_id)
    student_id = await _create_student(db)
    now = datetime.now(timezone.utc)
    hr_id = (
        await db.execute(
            text(
                "INSERT INTO help_requests "
                "(student_id, task_id, course_id, status, request_type, priority, created_at) "
                "VALUES (:s, :t, :c, 'open', 'manual_help', 100, :now) RETURNING id"
            ),
            {"s": student_id, "t": task_id, "c": grandchild_id, "now": now},
        )
    ).scalar_one()
    await db.commit()
    try:
        # HELP_REQUESTS_ACL_SQL — то же что использует claim_next_help_request.
        # Проверяем что наш hr_id попадает в видимый pool через hierarchical.
        sql = (
            f"SELECT COUNT(*) FROM help_requests hr "
            f"WHERE hr.id = :hid AND {HELP_REQUESTS_ACL_SQL}"
        )
        count = (
            await db.execute(text(sql), {"hid": hr_id, "teacher_id": teacher_id})
        ).scalar()
        assert count == 1, (
            f"Y-4.1 hierarchical HELP_REQUESTS_ACL_SQL должен включать hr_id={hr_id} "
            f"для teacher_id={teacher_id} на root_id={root_id} "
            f"(заявка на grandchild_id={grandchild_id})"
        )
    finally:
        await db.execute(text("DELETE FROM help_requests WHERE id=:id"), {"id": hr_id})
        await db.commit()
        await _cleanup(db, teacher_id=teacher_id, student_id=student_id, rids=[])
