"""Y-4 post-S5: ACL для GET /tasks/by-external/{uid} и GET /tasks/{id}.

Регрессионный bug-fix: cookie-auth student'а получал 403 от
`Depends(get_db)` (legacy service-key gate). Теперь cookie auth + ACL по
дереву `user_courses` + `course_parents` recursive.

Сценарии:
- 401 без auth (нет cookie/X-API-Key)
- 200 student с user_course на root + task на grandchild → access (recursive)
- 403 student с user_course на чужом root + task на nested → deny
- 200 teacher (extended-role) bypass — даже без user_courses
- 200 service-key bypass — для legacy TG_LMS / CB CLI
- 200 methodist bypass
- 404 несуществующая задача (для аутентифицированного user)
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


def _service_api_key() -> str:
    raw = os.environ.get("VALID_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        pytest.skip("VALID_API_KEYS пуст в .env")
    return keys[0]


async def _create_user_with_session(
    db, *, role_name: str | None = None,
) -> tuple[int, str]:
    """User + session; опц. role_name из roles (student/teacher/methodist/admin)."""
    u = Users(
        email=f"y4ps5-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="y4ps5", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role_name:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, id FROM roles WHERE name = :rn "
                "ON CONFLICT (user_id, role_id) DO NOTHING"
            ),
            {"u": u.id, "rn": role_name},
        )
    await db.commit()
    return u.id, token


async def _create_task(
    db, *, course_id: int, type_: str = "SC", is_active: bool = True,
) -> tuple[int, str]:
    ext = f"y4ps5-task-{random.randint(10**8, 10**10)}"
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
            "difficulty_id, is_active) "
            "VALUES (:ext, 10, CAST(:content AS jsonb), :cid, 1, :act) RETURNING id"
        ),
        {
            "ext": ext,
            "content": json.dumps({"type": type_, "stem": "y4ps5-test"}),
            "cid": course_id,
            "act": is_active,
        },
    )
    tid = res.scalar_one()
    await db.commit()
    return tid, ext


async def _enroll_user_in_course(db, *, user_id: int, course_id: int) -> None:
    """INSERT user_courses(user_id, course_id, is_active=true). DB-trigger
    запрещает non-root курсы — caller должен передавать root."""
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active, order_number) "
            "VALUES (:u, :c, true, 1) "
            "ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "c": course_id},
    )
    await db.commit()


async def _pick_root_with_grandchild(db) -> tuple[int, int, int]:
    """Найти триплет (root, child, grandchild) с реальной 3-уровневой иерархией."""
    row = (
        await db.execute(
            text(
                """
                SELECT cp_outer.parent_course_id AS root_id,
                       cp_outer.course_id AS child_id,
                       cp_inner.course_id AS grandchild_id
                FROM course_parents cp_outer
                JOIN course_parents cp_inner ON cp_inner.parent_course_id = cp_outer.course_id
                WHERE cp_outer.parent_course_id NOT IN (SELECT course_id FROM course_parents)
                LIMIT 1
                """
            )
        )
    ).fetchone()
    if row is None:
        pytest.skip("Нет цепочки root→child→grandchild в БД")
    return int(row[0]), int(row[1]), int(row[2])


async def _pick_other_root(db, exclude_root_id: int) -> int:
    row = (
        await db.execute(
            text(
                "SELECT id FROM courses "
                "WHERE id != :exc "
                "  AND id NOT IN (SELECT course_id FROM course_parents) "
                "LIMIT 1"
            ),
            {"exc": exclude_root_id},
        )
    ).fetchone()
    if row is None:
        pytest.skip("Нет другого root-курса для negative-теста")
    return int(row[0])


async def _cleanup(db, *, user_ids: list[int], task_ids: list[int]):
    if task_ids:
        await db.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": task_ids})
    if user_ids:
        await db.execute(text("DELETE FROM user_courses WHERE user_id = ANY(:u)"), {"u": user_ids})
        await db.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": user_ids})
        await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": user_ids})
        await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": user_ids})
    await db.commit()


# ─── 401 без auth ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_task_by_external_requires_auth(db, client):
    root_id, _c, gid = await _pick_root_with_grandchild(db)
    tid, ext = await _create_task(db, course_id=gid)
    try:
        resp = await client.get(f"/api/v1/tasks/by-external/{ext}")
        assert resp.status_code == 401
    finally:
        await _cleanup(db, user_ids=[], task_ids=[tid])


@pytest.mark.asyncio
async def test_get_task_by_id_requires_auth(db, client):
    root_id, _c, gid = await _pick_root_with_grandchild(db)
    tid, _ = await _create_task(db, course_id=gid)
    try:
        resp = await client.get(f"/api/v1/tasks/{tid}")
        assert resp.status_code == 401
    finally:
        await _cleanup(db, user_ids=[], task_ids=[tid])


# ─── student: ACL по дереву user_courses ──────────────────────────────────

@pytest.mark.asyncio
async def test_student_access_grandchild_task_through_root_enrollment(db, client):
    """Student зачислен в root → видит task на grandchild через recursive."""
    root_id, _child, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="student")
    await _enroll_user_in_course(db, user_id=uid, course_id=root_id)
    tid, ext = await _create_task(db, course_id=gid)
    try:
        resp_ext = await client.get(
            f"/api/v1/tasks/by-external/{ext}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_ext.status_code == 200, resp_ext.text
        assert resp_ext.json()["id"] == tid

        resp_id = await client.get(
            f"/api/v1/tasks/{tid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_id.status_code == 200
        assert resp_id.json()["id"] == tid
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[tid])


@pytest.mark.asyncio
async def test_student_denied_task_in_other_branch(db, client):
    """Student зачислен в чужой root → 403 на task в другой ветке."""
    root_id, _child, gid = await _pick_root_with_grandchild(db)
    other_root = await _pick_other_root(db, exclude_root_id=root_id)
    uid, token = await _create_user_with_session(db, role_name="student")
    await _enroll_user_in_course(db, user_id=uid, course_id=other_root)
    tid, ext = await _create_task(db, course_id=gid)
    try:
        resp = await client.get(
            f"/api/v1/tasks/by-external/{ext}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text

        resp2 = await client.get(
            f"/api/v1/tasks/{tid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 403
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[tid])


@pytest.mark.asyncio
async def test_student_without_user_courses_denied(db, client):
    """Student без записей в user_courses → 403."""
    root_id, _c, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="student")
    tid, _ = await _create_task(db, course_id=gid)
    try:
        resp = await client.get(
            f"/api/v1/tasks/{tid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[tid])


# ─── extended-role: bypass ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_teacher_role_bypasses_user_courses_check(db, client):
    """Teacher без user_courses всё равно видит задачи (bypass для управления)."""
    root_id, _c, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="teacher")
    tid, _ = await _create_task(db, course_id=gid)
    try:
        resp = await client.get(
            f"/api/v1/tasks/{tid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[tid])


@pytest.mark.asyncio
async def test_methodist_role_bypasses(db, client):
    root_id, _c, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="methodist")
    tid, ext = await _create_task(db, course_id=gid)
    try:
        resp = await client.get(
            f"/api/v1/tasks/by-external/{ext}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[tid])


# ─── service-key bypass: backward compat ──────────────────────────────────

@pytest.mark.asyncio
async def test_service_api_key_bypasses(db, client):
    """X-API-Key header → CurrentUser(is_service=True) → bypass ACL."""
    root_id, _c, gid = await _pick_root_with_grandchild(db)
    tid, ext = await _create_task(db, course_id=gid)
    try:
        resp = await client.get(
            f"/api/v1/tasks/{tid}",
            headers={"X-API-Key": _service_api_key()},
        )
        assert resp.status_code == 200, resp.text

        resp2 = await client.get(
            f"/api/v1/tasks/by-external/{ext}",
            headers={"X-API-Key": _service_api_key()},
        )
        assert resp2.status_code == 200
    finally:
        await _cleanup(db, user_ids=[], task_ids=[tid])


@pytest.mark.asyncio
async def test_legacy_query_api_key_bypasses(db, client):
    """Legacy ?api_key= в query тоже даёт is_service=True (TG_LMS legacy)."""
    root_id, _c, gid = await _pick_root_with_grandchild(db)
    tid, ext = await _create_task(db, course_id=gid)
    try:
        resp = await client.get(
            f"/api/v1/tasks/{tid}?api_key={_service_api_key()}",
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db, user_ids=[], task_ids=[tid])


# ─── tsk-697: выключенное задание закрыто для ученика ──────────────────────


@pytest.mark.asyncio
async def test_student_denied_inactive_task_by_id(db, client):
    """Ученик зачислен в курс, но задание выключено → 404, а не содержимое.

    Регресс-тест на дефект, найденный живьём 26.08.2026: движок отдавал по
    выключенному заданию 404 (`/learning/tasks/{id}/state`), а прямая ссылка на
    карточку возвращала стем и варианты целиком.
    """
    root_id, _child, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="student")
    await _enroll_user_in_course(db, user_id=uid, course_id=root_id)
    tid, _ext = await _create_task(db, course_id=gid, is_active=False)
    try:
        resp = await client.get(
            f"/api/v1/tasks/{tid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, resp.text
        assert "y4ps5-test" not in resp.text, "стем не должен утечь в тело 404"
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[tid])


@pytest.mark.asyncio
async def test_student_denied_inactive_task_by_external(db, client):
    """Вторая дверь к тому же заданию — по внешнему идентификатору."""
    root_id, _child, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="student")
    await _enroll_user_in_course(db, user_id=uid, course_id=root_id)
    tid, ext = await _create_task(db, course_id=gid, is_active=False)
    try:
        resp = await client.get(
            f"/api/v1/tasks/by-external/{ext}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, resp.text
        assert "y4ps5-test" not in resp.text, "стем не должен утечь в тело 404"
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[tid])


@pytest.mark.asyncio
async def test_student_denied_inactive_task_before_acl_check(db, client):
    """Чужое выключенное задание тоже 404 — ACL срабатывает первым (403 →
    отличать «нет доступа» от «выключено» ученику не даём в обе стороны)."""
    root_id, _child, gid = await _pick_root_with_grandchild(db)
    other_root = await _pick_other_root(db, exclude_root_id=root_id)
    uid, token = await _create_user_with_session(db, role_name="student")
    await _enroll_user_in_course(db, user_id=uid, course_id=other_root)
    tid, _ext = await _create_task(db, course_id=gid, is_active=False)
    try:
        resp = await client.get(
            f"/api/v1/tasks/{tid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (403, 404), resp.text
        assert "y4ps5-test" not in resp.text
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[tid])


@pytest.mark.asyncio
async def test_methodist_still_sees_inactive_task(db, client):
    """Методисту выключенное задание нужно: на нём стоит карточка кабинета
    (`/methodist/tasks/{id}`) и переключатель активности."""
    _root, _c, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="methodist")
    tid, ext = await _create_task(db, course_id=gid, is_active=False)
    try:
        resp = await client.get(
            f"/api/v1/tasks/{tid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is False

        resp2 = await client.get(
            f"/api/v1/tasks/by-external/{ext}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200, resp2.text
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[tid])


@pytest.mark.asyncio
async def test_teacher_still_sees_inactive_task(db, client):
    """Преподаватель разбирает сдачу по снятому заданию — экран проверки
    ответа тянет карточку задания и без неё оценивает вслепую."""
    _root, _c, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="teacher")
    tid, _ext = await _create_task(db, course_id=gid, is_active=False)
    try:
        resp = await client.get(
            f"/api/v1/tasks/{tid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[tid])


@pytest.mark.asyncio
async def test_service_key_still_sees_inactive_task(db, client):
    """Сервисный ключ (ТГ-боты, ContentBackbone CLI) читает задание независимо
    от активности: превью «как ученик» и переиздание идут по нему."""
    _root, _c, gid = await _pick_root_with_grandchild(db)
    tid, ext = await _create_task(db, course_id=gid, is_active=False)
    try:
        resp = await client.get(
            f"/api/v1/tasks/{tid}",
            headers={"X-API-Key": _service_api_key()},
        )
        assert resp.status_code == 200, resp.text

        resp2 = await client.get(
            f"/api/v1/tasks/by-external/{ext}",
            headers={"X-API-Key": _service_api_key()},
        )
        assert resp2.status_code == 200, resp2.text
    finally:
        await _cleanup(db, user_ids=[], task_ids=[tid])


@pytest.mark.asyncio
async def test_student_history_survives_inactive_task(db, client):
    """Своя история сдач не завязана на карточку задания: `/me/tasks/{id}/history`
    отвечает по выключенному заданию как прежде.

    На проде 443 выключенных задания имеют сдачи (7 учеников, 1552 записи) —
    404 на карточке не должен закрывать ученику просмотр собственных ответов.
    """
    root_id, _child, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="student")
    await _enroll_user_in_course(db, user_id=uid, course_id=root_id)
    tid, _ext = await _create_task(db, course_id=gid, is_active=False)
    try:
        resp = await client.get(
            f"/api/v1/me/tasks/{tid}/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[tid])


# ─── tsk-699: список заданий курса не отдаёт ученику выключенные ───────────


@pytest.mark.asyncio
async def test_student_course_list_hides_inactive_tasks(db, client):
    """Хвост tsk-697, но оптом: `GET /tasks/by-course/{id}` по умолчанию
    (`is_active` не передан) отдавал ученику стемы ВСЕХ заданий курса, включая
    снятые с публикации. На проде 26.08.2026 — 1220 выключенных из 7629.
    """
    root_id, _child, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="student")
    await _enroll_user_in_course(db, user_id=uid, course_id=root_id)
    active_id, _ea = await _create_task(db, course_id=gid, is_active=True)
    inactive_id, _ei = await _create_task(db, course_id=gid, is_active=False)
    try:
        resp = await client.get(
            f"/api/v1/tasks/by-course/{gid}?limit=1000",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        ids = {t["id"] for t in resp.json()}
        assert active_id in ids, "активное задание курса ученику по-прежнему видно"
        assert inactive_id not in ids
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[active_id, inactive_id])


@pytest.mark.asyncio
async def test_student_cannot_request_inactive_tasks_explicitly(db, client):
    """`?is_active=false` не должен быть дверью в обход: ученику фильтр
    принудительно `true`, поэтому в срезе выключенных для него пусто."""
    root_id, _child, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="student")
    await _enroll_user_in_course(db, user_id=uid, course_id=root_id)
    active_id, _ea = await _create_task(db, course_id=gid, is_active=True)
    inactive_id, _ei = await _create_task(db, course_id=gid, is_active=False)
    try:
        resp = await client.get(
            f"/api/v1/tasks/by-course/{gid}?is_active=false&limit=1000",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        ids = {t["id"] for t in resp.json()}
        assert inactive_id not in ids
        assert active_id in ids, (
            "фильтр подменён на is_active=true, поэтому отдаются активные"
        )
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[active_id, inactive_id])


@pytest.mark.asyncio
async def test_methodist_course_list_still_shows_inactive_tasks(db, client):
    """Кабинет методиста стоит на прежнем поведении: без параметра — оба
    задания, `is_active=false` — срез выключенных (tsk-559)."""
    _root, _child, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="methodist")
    active_id, _ea = await _create_task(db, course_id=gid, is_active=True)
    inactive_id, _ei = await _create_task(db, course_id=gid, is_active=False)
    try:
        resp = await client.get(
            f"/api/v1/tasks/by-course/{gid}?limit=1000",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        ids = {t["id"] for t in resp.json()}
        assert {active_id, inactive_id} <= ids

        resp2 = await client.get(
            f"/api/v1/tasks/by-course/{gid}?is_active=false&limit=1000",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200, resp2.text
        ids2 = {t["id"] for t in resp2.json()}
        assert inactive_id in ids2
        assert active_id not in ids2
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[active_id, inactive_id])


@pytest.mark.asyncio
async def test_service_key_course_list_still_shows_inactive_tasks(db, client):
    """Сервисный ключ: переиздание ContentBackbone и ТГ-боты обходят курс
    через `by-course` и рассчитывают увидеть выключенные задания тоже."""
    _root, _child, gid = await _pick_root_with_grandchild(db)
    active_id, _ea = await _create_task(db, course_id=gid, is_active=True)
    inactive_id, _ei = await _create_task(db, course_id=gid, is_active=False)
    try:
        resp = await client.get(
            f"/api/v1/tasks/by-course/{gid}?limit=1000",
            headers={"X-API-Key": _service_api_key()},
        )
        assert resp.status_code == 200, resp.text
        ids = {t["id"] for t in resp.json()}
        assert {active_id, inactive_id} <= ids
    finally:
        await _cleanup(db, user_ids=[], task_ids=[active_id, inactive_id])


@pytest.mark.asyncio
async def test_teacher_course_list_still_shows_inactive_tasks(db, client):
    """Преподаватель разбирает сдачи по снятым заданиям — список курса ему
    отдаётся как раньше."""
    _root, _child, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="teacher")
    active_id, _ea = await _create_task(db, course_id=gid, is_active=True)
    inactive_id, _ei = await _create_task(db, course_id=gid, is_active=False)
    try:
        resp = await client.get(
            f"/api/v1/tasks/by-course/{gid}?limit=1000",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        ids = {t["id"] for t in resp.json()}
        assert {active_id, inactive_id} <= ids
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[active_id, inactive_id])


@pytest.mark.asyncio
async def test_student_course_list_stem_not_leaked(db, client):
    """Прямая проверка утечки: стем выключенного задания не должен попасть в
    тело ответа ученику ни в каком виде."""
    root_id, _child, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="student")
    await _enroll_user_in_course(db, user_id=uid, course_id=root_id)
    inactive_id, ext = await _create_task(db, course_id=gid, is_active=False)
    try:
        resp = await client.get(
            f"/api/v1/tasks/by-course/{gid}?limit=1000",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert ext not in resp.text, "external_uid выключенного задания утёк"
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[inactive_id])


# ─── 404 ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_404_task_not_found_authenticated(db, client):
    uid, token = await _create_user_with_session(db, role_name="student")
    try:
        resp = await client.get(
            "/api/v1/tasks/9999999",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
    finally:
        await _cleanup(db, user_ids=[uid], task_ids=[])
