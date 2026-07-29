"""tsk-460: правило проверки с верными ответами не уходит ученику.

Схема `TaskRead` отдавалась одинаково всем, кто прошёл ACL, а ACL решает
только «дать/не дать задачу целиком». Ученик, открыв задание, видел
`solution_rules.correct_options` во вкладке «Сеть» до того, как отправил
свой ответ (подтверждено живым запросом на проде 2026-07-29).

Проверяем три эндпоинта `tasks_extra.py`, доступные ученику по cookie:

- ученик получает 200, текст задания на месте, `solution_rules` = null;
- teacher / methodist и сервисный ключ видят правило как раньше
  (на этом держатся ТГ-боты, ContentBackbone и кабинет методиста);
- запрос ученика не затирает правило в БД (обнуление идёт на копии
  Pydantic-модели, мутация ORM-строки попала бы в autoflush).
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.courses import Courses
from app.models.tasks import Tasks
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()

_CORRECT_OPTIONS = ["B"]
_SOLUTION_RULES = {
    "max_score": 10,
    "correct_options": _CORRECT_OPTIONS,
    "auto_check": True,
    "scoring_mode": "all_or_nothing",
}
_TASK_CONTENT = {
    "type": "SC",
    "stem": "tsk-460: какой вариант верный?",
    "options": [
        {"id": "A", "text": "неверный", "is_active": True},
        {"id": "B", "text": "верный", "is_active": True},
    ],
    "hints_text": ["подсказка ученику"],
}


async def _user_with_session(db, role: str | None) -> tuple[int, str]:
    """Создать пользователя с опциональной ролью и вернуть (id, bearer-токен)."""
    u = Users(
        email=f"t460-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t460-{role or 'norole'}",
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
                "SELECT :u, r.id FROM roles r WHERE r.name = :r ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "r": role},
        )
    await db.commit()
    return u.id, token


async def _course_with_task(db) -> tuple[int, int, str]:
    """Создать курс с одним SC-заданием. Вернуть (course_id, task_id, external_uid)."""
    c = Courses(
        title=f"t460-course-{random.randint(10**8, 10**10)}",
        access_level="self_guided",
    )
    db.add(c)
    await db.flush()

    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar_one()

    external_uid = f"t460-task-{random.randint(10**8, 10**10)}"
    t = Tasks(
        external_uid=external_uid,
        course_id=c.id,
        difficulty_id=difficulty_id,
        task_content=_TASK_CONTENT,
        solution_rules=_SOLUTION_RULES,
        max_score=10,
        is_active=True,
    )
    db.add(t)
    await db.flush()
    await db.commit()
    return c.id, t.id, external_uid


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "c": course_id},
    )
    await db.commit()


async def _cleanup(db, user_ids: list[int], course_ids: list[int]) -> None:
    for uid in user_ids:
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_courses WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM users WHERE id=:u"), {"u": uid})
    for cid in course_ids:
        await db.execute(text("DELETE FROM tasks WHERE course_id=:c"), {"c": cid})
        await db.execute(text("DELETE FROM user_courses WHERE course_id=:c"), {"c": cid})
        await db.execute(text("DELETE FROM courses WHERE id=:c"), {"c": cid})
    await db.commit()


def _paths(course_id: int, task_id: int, external_uid: str) -> list[str]:
    """Три эндпоинта, которые ученический фронт SPW реально зовёт."""
    return [
        f"/api/v1/tasks/{task_id}",
        f"/api/v1/tasks/by-external/{external_uid}",
        f"/api/v1/tasks/by-course/{course_id}",
    ]


def _tasks_in(payload) -> list[dict]:
    """Ответ by-course — список, остальные два — один объект."""
    return payload if isinstance(payload, list) else [payload]


@pytest.mark.asyncio
async def test_student_does_not_see_solution_rules(db, client):
    """Ученик зачислен в курс: задание отдаётся, верный ответ — нет."""
    uid, token = await _user_with_session(db, "student")
    cid, tid, uid_ext = await _course_with_task(db)
    await _enroll(db, uid, cid)
    try:
        for path in _paths(cid, tid, uid_ext):
            resp = await client.get(path, headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200, f"{path} → {resp.status_code} {resp.text}"
            tasks = _tasks_in(resp.json())
            assert tasks, f"{path} → пустой ответ"
            for t in tasks:
                assert t["solution_rules"] is None, f"{path} → утечка: {t['solution_rules']}"
                # Правило скрыто, а само задание не должно пострадать.
                assert t["task_content"]["stem"] == _TASK_CONTENT["stem"], path
                assert t["hints_text"] == ["подсказка ученику"], path
            assert "correct_options" not in resp.text, f"{path} → верный ответ в теле"
    finally:
        await _cleanup(db, [uid], [cid])


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["teacher", "methodist", "admin"])
async def test_privileged_roles_still_see_solution_rules(db, client, role):
    """Кабинет методиста и проверка учителем работают как раньше."""
    uid, token = await _user_with_session(db, role)
    cid, tid, uid_ext = await _course_with_task(db)
    try:
        for path in _paths(cid, tid, uid_ext):
            resp = await client.get(path, headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200, f"{path} → {resp.status_code} {resp.text}"
            for t in _tasks_in(resp.json()):
                assert t["solution_rules"] is not None, f"{path} ({role}) → правило потеряно"
                assert t["solution_rules"]["correct_options"] == _CORRECT_OPTIONS, path
    finally:
        await _cleanup(db, [uid], [cid])


@pytest.mark.asyncio
async def test_service_key_still_sees_solution_rules(db, client):
    """Главный риск правки: ТГ-боты и ContentBackbone ходят по сервисному ключу."""
    cid, tid, uid_ext = await _course_with_task(db)
    api_key = next(iter(_settings.valid_api_keys))
    try:
        for path in _paths(cid, tid, uid_ext):
            resp = await client.get(path, headers={"X-API-Key": api_key})
            assert resp.status_code == 200, f"{path} → {resp.status_code} {resp.text}"
            for t in _tasks_in(resp.json()):
                assert t["solution_rules"] is not None, f"{path} → правило потеряно"
                assert t["solution_rules"]["correct_options"] == _CORRECT_OPTIONS, path
    finally:
        await _cleanup(db, [], [cid])


@pytest.mark.asyncio
async def test_legacy_query_api_key_still_sees_solution_rules(db, client):
    """TG_LMS шлёт ключ не заголовком, а в query (`?api_key=`) — тот же путь."""
    cid, tid, uid_ext = await _course_with_task(db)
    api_key = next(iter(_settings.valid_api_keys))
    try:
        for path in _paths(cid, tid, uid_ext):
            sep = "&" if "?" in path else "?"
            resp = await client.get(f"{path}{sep}api_key={api_key}")
            assert resp.status_code == 200, f"{path} → {resp.status_code} {resp.text}"
            for t in _tasks_in(resp.json()):
                assert t["solution_rules"] is not None, f"{path} → правило потеряно"
                assert t["solution_rules"]["correct_options"] == _CORRECT_OPTIONS, path
    finally:
        await _cleanup(db, [], [cid])


@pytest.mark.asyncio
async def test_student_read_does_not_erase_rules_in_db(db, client):
    """Скрытие поля не должно доехать до БД через autoflush."""
    uid, token = await _user_with_session(db, "student")
    cid, tid, uid_ext = await _course_with_task(db)
    await _enroll(db, uid, cid)
    try:
        for path in _paths(cid, tid, uid_ext):
            resp = await client.get(path, headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200, path

        stored = (
            await db.execute(
                text("SELECT solution_rules FROM tasks WHERE id = :t"), {"t": tid}
            )
        ).scalar_one()
        assert stored is not None, "правило проверки затёрлось в БД"
        assert stored["correct_options"] == _CORRECT_OPTIONS
    finally:
        await _cleanup(db, [uid], [cid])


@pytest.mark.asyncio
async def test_student_outside_course_still_denied(db, client):
    """ACL не ослаб: не зачисленный ученик по-прежнему получает 403."""
    uid, token = await _user_with_session(db, "student")
    cid, tid, uid_ext = await _course_with_task(db)
    try:
        for path in _paths(cid, tid, uid_ext):
            resp = await client.get(path, headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 403, f"{path} → {resp.status_code} {resp.text}"
    finally:
        await _cleanup(db, [uid], [cid])
