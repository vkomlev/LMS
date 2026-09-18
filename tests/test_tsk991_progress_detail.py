"""tsk-991 (П2 педагогического аудита LMS, docs/qa/lms-pedagogy-audit-2026-09-17.md):
`GET /me/courses/{course_id}/progress-detail` — ученический срез уже
посчитанного дашборда персонала/родителя (`student_dashboard_service`).

Покрывает:
- 401 без auth, 403 student без enrollment (тот же гейт, что у соседних
  `/me/courses/{id}/...` эндпоинтов);
- 404 — курс без единого элемента (не задан);
- «точка затыка»: ученик прошёл 3-е задание раньше 1-го и 2-го (якорь по
  времени сдачи, tsk-918) → `pace_status="behind"`, `behind_count=1`,
  `behind_item_title` называет пропущенное задание, `current_item_title` —
  первый непройденный элемент фронта;
- курс пройден целиком → `pace_status="completed"`;
- **безопасность среза (сама находка П2 аудита)**: в ответе НЕТ `pace_level`
  и вообще никаких сырых чисел/состава когорты — терциль по сверстникам
  курса (tsk-504) остаётся только у персонала/родителя.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


# ────────────────────────── Helpers ────────────────────────────────────────


async def _create_student(db, *, prefix: str = "tsk991") -> tuple[int, str]:
    """Создать student-юзера с email-identity и сессией. Returns (user_id, token)."""
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-stud", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, token


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "c": course_id},
    )
    await db.commit()


async def _make_course(db, *, n_tasks: int) -> dict:
    """Изолированный root-курс с `n_tasks` заданиями (order_position 1..N), без
    материалов и подкурсов — course_position не различает их природу."""
    suffix = uuid.uuid4().hex[:8]
    root_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO courses (title, access_level) "
                    "VALUES (:t, 'auto_check') RETURNING id"
                ),
                {"t": f"tsk991-root {suffix}"},
            )
        ).scalar()
    )
    diff = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()

    task_ids: list[int] = []
    for i in range(1, n_tasks + 1):
        uid = f"tsk991:{suffix}:{i}"
        tid = int(
            (
                await db.execute(
                    text(
                        "INSERT INTO tasks "
                        "(course_id, difficulty_id, external_uid, order_position, "
                        " task_content, solution_rules) "
                        "VALUES (:c, :d, :uid, :pos, CAST(:tc AS jsonb), CAST(:sr AS jsonb)) "
                        "RETURNING id"
                    ),
                    {
                        "c": root_id,
                        "d": diff,
                        "uid": uid,
                        "pos": i,
                        "tc": f'{{"type":"SA","title":"tsk991 задание {i}","stem":"Условие {i}"}}',
                        "sr": '{"max_score":1}',
                    },
                )
            ).scalar()
        )
        task_ids.append(tid)
    await db.commit()
    return {"root_id": root_id, "task_ids": task_ids}


async def _pass_task(db, *, user_id: int, task_id: int, course_id: int, submitted_at: datetime) -> None:
    """Записать РЕАЛЬНУЮ (не ручную) верную сдачу — `source_system='spw_web'`,
    как того требует `real_student_results_filter` (единственное место, где
    решается «это настоящая сдача ученика», см. `learning_gaps_service.py`).
    Без него `anchor_for`/прогноз тихо не увидели бы эту сдачу вовсе."""
    aid = int(
        (
            await db.execute(
                text(
                    "INSERT INTO attempts (user_id, course_id, root_course_id, source_system, "
                    "finished_at) VALUES (:u, :c, :c, 'spw', :now) RETURNING id"
                ),
                {"u": user_id, "c": course_id, "now": submitted_at},
            )
        ).scalar()
    )
    await db.execute(
        text(
            "INSERT INTO task_results "
            "(score, user_id, task_id, attempt_id, submitted_at, count_retry, received_at, "
            " max_score, source_system, is_correct, checked_at) "
            "VALUES (1, :u, :t, :aid, :now, 0, :now, 1, 'spw_web', true, null)"
        ),
        {"u": user_id, "t": task_id, "aid": aid, "now": submitted_at},
    )
    await db.commit()


# ────────────────────────── Tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_requires_auth(client):
    resp = await client.get("/api/v1/me/courses/1/progress-detail")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_403_for_unenrolled_student(db, client):
    user_id, token = await _create_student(db)
    tree = await _make_course(db, n_tasks=1)
    resp = await client.get(
        f"/api/v1/me/courses/{tree['root_id']}/progress-detail",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_404_for_empty_course(db, client):
    """Курс без единого задания/материала — показывать прогресс нечего."""
    user_id, token = await _create_student(db, prefix="tsk991-empty")
    tree = await _make_course(db, n_tasks=0)
    await _enroll(db, user_id, tree["root_id"])
    resp = await client.get(
        f"/api/v1/me/courses/{tree['root_id']}/progress-detail",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_behind_point_of_stall_no_cohort_leak(db, client):
    """Ученик сдал 3-е задание, 1-е и 2-е — нет: якорь съезжает вперёд (tsk-918),
    1-е становится фронтом («ты здесь»), 2-е — хвостом («точка затыка»).

    Это же тело ответа проверяется на отсутствие `pace_level` и любых чисел
    когорты — сама суть П2 находки педагогического аудита.
    """
    user_id, token = await _create_student(db, prefix="tsk991-behind")
    tree = await _make_course(db, n_tasks=3)
    root_id, task_ids = tree["root_id"], tree["task_ids"]
    await _enroll(db, user_id, root_id)

    now = datetime.now(timezone.utc)
    await _pass_task(db, user_id=user_id, task_id=task_ids[2], course_id=root_id, submitted_at=now)

    resp = await client.get(
        f"/api/v1/me/courses/{root_id}/progress-detail",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["course_id"] == root_id
    assert body["percent_complete"] == 33
    assert body["is_completed"] is False
    assert body["remaining_count"] == 2
    assert body["pace_status"] == "behind"
    assert body["behind_count"] == 1
    assert body["behind_item_title"] == "tsk991 задание 2"
    assert body["current_item_title"] == "tsk991 задание 1"
    # Тип поля соблюдён (float | null) — точное значение зависит от телеметрии
    # окружения (task_effort_service), поэтому не фиксируем число минут.
    assert body["remaining_minutes"] is None or isinstance(body["remaining_minutes"], float)

    # П2 находка аудита: терциль когорты (`pace_level`) и любые числа/состав
    # других учеников курса ученику отдаваться не должны вовсе — ни этим, ни
    # каким-либо другим ключом.
    assert "pace_level" not in body
    forbidden_keys = {"pace_level", "cohort", "cohort_size", "peer", "peers", "tercile"}
    assert forbidden_keys.isdisjoint(body.keys())


@pytest.mark.asyncio
async def test_completed_course(db, client):
    user_id, token = await _create_student(db, prefix="tsk991-done")
    tree = await _make_course(db, n_tasks=1)
    root_id, task_ids = tree["root_id"], tree["task_ids"]
    await _enroll(db, user_id, root_id)
    await _pass_task(
        db, user_id=user_id, task_id=task_ids[0], course_id=root_id,
        submitted_at=datetime.now(timezone.utc),
    )

    resp = await client.get(
        f"/api/v1/me/courses/{root_id}/progress-detail",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["percent_complete"] == 100
    assert body["is_completed"] is True
    assert body["remaining_count"] == 0
    assert body["pace_status"] == "completed"
    assert body["behind_count"] == 0
