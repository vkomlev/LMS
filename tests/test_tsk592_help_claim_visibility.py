"""tsk-592: заявка помощи, взятая в работу, видна остальным преподавателям.

Что здесь проверяется и почему именно это:

1. **Захват ставится при открытии конкретной заявки.** До tsk-592 отметка
   появлялась только на пути «взять следующую» (`claim-next`). Преподаватель,
   открывший заявку из списка, не отмечался нигде — а оператор описывает именно
   этот путь. Прод-факт: из 49 заявок с ответами по 4 отвечали РАЗНЫЕ
   преподаватели (read-only аудит 2026-08-19).
2. **Состояние захвата дошло до API.** Колонки `claimed_by`/`claim_expires_at`
   жили в БД с этапа 3.9, но наружу не отдавались — интерфейс физически не мог
   показать «уже в работе». Проверяется и список, и карточка: два экрана обязаны
   одинаково понимать занятость.
3. **Мягкая блокировка, а не жёсткая.** Второй преподаватель получает 409 с
   именем владельца, но может перехватить (`takeover=true`) — и перехват
   остаётся в журнале событий.
4. **Истёкший захват = свободная заявка.** Иначе «вечно занятые» заявки: клиент
   мог не позвать release (закрыл вкладку, упал бот).

Тесты идут по НАСТОЯЩЕЙ БД и проверяют ТЕЛО ответа. Свои сущности создаются в
тесте, чужие строки не трогаются — соседняя сессия на той же dev-базе меняет
общие счётчики.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

pytestmark = pytest.mark.asyncio

_TAG = "tsk592"


async def _new_user(db, *, role: str | None, name: str) -> tuple[int, str]:
    """Создать пользователя с ролью и живой сессией; вернуть (id, токен)."""
    user = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}-{random.randint(10**6, 10**7)}",
        tg_id=None,
    )
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", user.email)
    token, _, _ = await create_session(db, user_id=user.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": user.id, "role": role},
        )
    await db.commit()
    return user.id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_request(db, *, student_id: int, teacher_id: int) -> int:
    """Открытая заявка на помощь, закреплённая за преподавателем."""
    task_id = (await db.execute(text("SELECT id FROM tasks ORDER BY id LIMIT 1"))).scalar()
    assert task_id is not None, "в dev-БД нет ни одного задания — тест не на чем сидеть"
    request_id = (
        await db.execute(
            text(
                "INSERT INTO help_requests "
                "  (status, request_type, auto_created, context_json, student_id, "
                "   task_id, assigned_teacher_id, message, created_at, updated_at, priority) "
                "VALUES ('open', 'manual_help', false, '{}'::jsonb, :s, :t, :teacher, "
                "        :msg, now(), now(), 100) "
                "RETURNING id"
            ),
            {"s": student_id, "t": task_id, "teacher": teacher_id, "msg": f"{_TAG} вопрос"},
        )
    ).scalar()
    await db.commit()
    return int(request_id)


async def _link_student(db, *, student_id: int, teacher_id: int) -> None:
    """Закрепить ученика за вторым преподавателем — он тоже видит заявку по ACL."""
    await db.execute(
        text(
            "INSERT INTO student_teacher_links (student_id, teacher_id) "
            "VALUES (:s, :t) ON CONFLICT DO NOTHING"
        ),
        {"s": student_id, "t": teacher_id},
    )
    await db.commit()


async def _detail(client, request_id: int, teacher_id: int, token: str) -> dict:
    r = await client.get(
        f"/api/v1/teacher/help-requests/{request_id}",
        params={"teacher_id": teacher_id},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _list_item(client, request_id: int, teacher_id: int, token: str) -> dict:
    """Строка этой заявки из списка преподавателя (не постфильтр по странице)."""
    r = await client.get(
        "/api/v1/teacher/help-requests",
        params={"teacher_id": teacher_id, "status": "open", "limit": 100},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    rows = [i for i in r.json()["items"] if i["request_id"] == request_id]
    assert rows, f"заявка {request_id} не попала в список преподавателя {teacher_id}"
    return rows[0]


@pytest.fixture
async def scene(db):
    """Ученик, два преподавателя с доступом к заявке и третий — без доступа."""
    student_id, _ = await _new_user(db, role="student", name="student")
    t1_id, t1_token = await _new_user(db, role="teacher", name="teacher1")
    t2_id, t2_token = await _new_user(db, role="teacher", name="teacher2")
    stranger_id, stranger_token = await _new_user(db, role="teacher", name="stranger")
    request_id = await _seed_request(db, student_id=student_id, teacher_id=t1_id)
    await _link_student(db, student_id=student_id, teacher_id=t2_id)
    return {
        "student_id": student_id,
        "t1": (t1_id, t1_token),
        "t2": (t2_id, t2_token),
        "stranger": (stranger_id, stranger_token),
        "request_id": request_id,
    }


async def test_free_request_is_not_claimed(client, scene):
    """До захвата заявка свободна — и в карточке, и в списке."""
    t1_id, t1_token = scene["t1"]
    detail = await _detail(client, scene["request_id"], t1_id, t1_token)
    assert detail["is_claimed"] is False
    assert detail["claimed_by"] is None
    assert detail["claimed_by_me"] is False
    row = await _list_item(client, scene["request_id"], t1_id, t1_token)
    assert row["is_claimed"] is False


async def test_claim_makes_request_visible_as_busy_to_other_teacher(client, scene):
    """Главное требование: взял один — второй видит «в работе у него»."""
    t1_id, t1_token = scene["t1"]
    t2_id, t2_token = scene["t2"]
    r = await client.post(
        f"/api/v1/teacher/help-requests/{scene['request_id']}/claim",
        json={"teacher_id": t1_id},
        headers=_auth(t1_token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lock_token"]
    assert body["took_over_from"] is None
    assert body["item"]["request_id"] == scene["request_id"]

    # Владелец: захват свой, блокировкой не считается.
    mine = await _detail(client, scene["request_id"], t1_id, t1_token)
    assert mine["is_claimed"] is True
    assert mine["claimed_by"] == t1_id
    assert mine["claimed_by_me"] is True
    assert mine["claim_expires_at"] is not None

    # Второй преподаватель: видит имя владельца и срок — и в карточке, и в списке.
    theirs = await _detail(client, scene["request_id"], t2_id, t2_token)
    assert theirs["is_claimed"] is True
    assert theirs["claimed_by"] == t1_id
    assert theirs["claimed_by_me"] is False
    assert theirs["claimed_by_name"] and _TAG in theirs["claimed_by_name"]
    row = await _list_item(client, scene["request_id"], t2_id, t2_token)
    assert row["is_claimed"] is True
    assert row["claimed_by"] == t1_id
    assert row["claimed_by_me"] is False


async def test_second_claim_conflicts_and_names_owner(client, scene):
    """Второй захват без перехвата — 409, и в тексте видно, у кого заявка."""
    t1_id, t1_token = scene["t1"]
    t2_id, t2_token = scene["t2"]
    await client.post(
        f"/api/v1/teacher/help-requests/{scene['request_id']}/claim",
        json={"teacher_id": t1_id},
        headers=_auth(t1_token),
    )
    r = await client.post(
        f"/api/v1/teacher/help-requests/{scene['request_id']}/claim",
        json={"teacher_id": t2_id},
        headers=_auth(t2_token),
    )
    assert r.status_code == 409, r.text
    assert _TAG in r.json()["detail"]


async def test_takeover_wins_and_is_recorded(client, db, scene):
    """Мягкая блокировка: «всё равно взять» проходит и остаётся в журнале."""
    t1_id, t1_token = scene["t1"]
    t2_id, t2_token = scene["t2"]
    await client.post(
        f"/api/v1/teacher/help-requests/{scene['request_id']}/claim",
        json={"teacher_id": t1_id},
        headers=_auth(t1_token),
    )
    r = await client.post(
        f"/api/v1/teacher/help-requests/{scene['request_id']}/claim",
        json={"teacher_id": t2_id, "takeover": True},
        headers=_auth(t2_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["took_over_from"] == t1_id
    assert _TAG in (r.json()["took_over_from_name"] or "")

    after = await _detail(client, scene["request_id"], t2_id, t2_token)
    assert after["claimed_by"] == t2_id
    assert after["claimed_by_me"] is True

    logged = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM audit_event "
                "WHERE event_type = 'teacher.help_request.claim_taken_over' "
                "  AND user_id = :u AND (details->>'request_id')::bigint = :r"
            ),
            {"u": t2_id, "r": scene["request_id"]},
        )
    ).scalar()
    assert int(logged or 0) == 1, "перехват обязан оставлять след в журнале"


async def test_expired_claim_frees_the_request(client, db, scene):
    """Просроченный захват — заявка свободна и берётся без перехвата."""
    t1_id, t1_token = scene["t1"]
    t2_id, t2_token = scene["t2"]
    await client.post(
        f"/api/v1/teacher/help-requests/{scene['request_id']}/claim",
        json={"teacher_id": t1_id},
        headers=_auth(t1_token),
    )
    await db.execute(
        text("UPDATE help_requests SET claim_expires_at = :exp WHERE id = :r"),
        {
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            "r": scene["request_id"],
        },
    )
    await db.commit()

    stale = await _detail(client, scene["request_id"], t2_id, t2_token)
    assert stale["is_claimed"] is False
    assert stale["claimed_by"] is None, "истёкший захват не должен читаться как занятость"

    r = await client.post(
        f"/api/v1/teacher/help-requests/{scene['request_id']}/claim",
        json={"teacher_id": t2_id},
        headers=_auth(t2_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["took_over_from"] is None


async def test_own_claim_is_extended_not_conflicted(client, scene):
    """Повторное открытие карточки своим же владельцем продлевает захват."""
    t1_id, t1_token = scene["t1"]
    first = await client.post(
        f"/api/v1/teacher/help-requests/{scene['request_id']}/claim",
        json={"teacher_id": t1_id, "ttl_sec": 60},
        headers=_auth(t1_token),
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"/api/v1/teacher/help-requests/{scene['request_id']}/claim",
        json={"teacher_id": t1_id, "ttl_sec": 600},
        headers=_auth(t1_token),
    )
    assert second.status_code == 200, second.text
    assert second.json()["lock_token"] != first.json()["lock_token"]
    assert second.json()["lock_expires_at"] > first.json()["lock_expires_at"]


async def test_release_frees_the_request(client, scene):
    """Уход с экрана снимает отметку — заявка снова свободна для всех."""
    t1_id, t1_token = scene["t1"]
    t2_id, t2_token = scene["t2"]
    claimed = await client.post(
        f"/api/v1/teacher/help-requests/{scene['request_id']}/claim",
        json={"teacher_id": t1_id},
        headers=_auth(t1_token),
    )
    lock_token = claimed.json()["lock_token"]
    r = await client.post(
        f"/api/v1/teacher/help-requests/{scene['request_id']}/release",
        json={"teacher_id": t1_id, "lock_token": lock_token},
        headers=_auth(t1_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["released"] is True
    free = await _detail(client, scene["request_id"], t2_id, t2_token)
    assert free["is_claimed"] is False


async def test_claim_denied_without_acl(client, scene):
    """Заявка вне зоны ответственности — 403, а не 409 и не молчаливый захват."""
    stranger_id, stranger_token = scene["stranger"]
    r = await client.post(
        f"/api/v1/teacher/help-requests/{scene['request_id']}/claim",
        json={"teacher_id": stranger_id},
        headers=_auth(stranger_token),
    )
    assert r.status_code == 403, r.text


async def test_claim_on_closed_request_conflicts(client, db, scene):
    """Закрытую заявку в работу не берут — 409, а не «взял и держит»."""
    t1_id, t1_token = scene["t1"]
    await db.execute(
        text("UPDATE help_requests SET status = 'closed', closed_at = now() WHERE id = :r"),
        {"r": scene["request_id"]},
    )
    await db.commit()
    r = await client.post(
        f"/api/v1/teacher/help-requests/{scene['request_id']}/claim",
        json={"teacher_id": t1_id},
        headers=_auth(t1_token),
    )
    assert r.status_code == 409, r.text
