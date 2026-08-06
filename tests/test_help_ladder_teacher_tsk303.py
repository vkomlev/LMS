"""tsk-303 Фаза 3: лестница помощи, сторона преподавателя.

Два продуктовых правила и их последствия для уже существующего кода:

1. **Текстовый ответ на `manual_help` закрывает заявку** — это решение
   оператора, и держит его сервер, а не каждый клиент по-своему. Дальше ход за
   учеником: не помогло — «Вернуть заявку», возврат попадёт в KPI.
   Проверяется и обратное: `individual_review` и `blocked_limit` ответ НЕ
   закрывает, у них своё закрытие.
2. **Ссылку на разбор присылает преподаватель вручную** — заявку это не
   закрывает, разбор ещё впереди.

Отдельный блок — про то, что новый класс заявки не должен «провалиться» в уже
написанном коде: он обязан считаться в панели нагрузки и переживать
сериализацию карточки. И то и другое ломалось бы молча.
"""
from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

pytestmark = pytest.mark.asyncio

_settings = Settings()
_WEBINAR = "https://meet.example/tsk303"


def _svc() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


async def _user(db, name: str, *, role: str | None = None, with_session: bool = False):
    u = Users(
        email=f"t303t-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name=name, tg_id=None,
    )
    db.add(u)
    await db.flush()
    if role:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    if not with_session:
        return u.id, None
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    return u.id, token


async def _task(db) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
                "course_id, difficulty_id) "
                "VALUES (:e, 10, CAST(:c AS jsonb), CAST(:r AS jsonb), 1, 1) RETURNING id"
            ),
            {
                "e": f"t303t-{random.randint(10**8, 10**10)}",
                "c": json.dumps({"type": "SA", "stem": "x"}),
                "r": json.dumps({"max_score": 10}),
            },
        )
    ).scalar_one()


async def _help_request(db, *, student_id, task_id, teacher_id, request_type="manual_help") -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO help_requests (status, student_id, task_id, request_type, "
                "assigned_teacher_id, auto_created, context_json, priority, message, "
                "created_at, updated_at) "
                "VALUES ('open', :s, :t, :rt, :at, false, '{}'::jsonb, 100, 'не понимаю', "
                "        now(), now()) RETURNING id"
            ),
            {"s": student_id, "t": task_id, "rt": request_type, "at": teacher_id},
        )
    ).scalar_one()


async def _row(db, rid: int) -> dict:
    r = (
        await db.execute(
            text(
                "SELECT status, request_type, webinar_link, closed_by FROM help_requests "
                "WHERE id = :id"
            ),
            {"id": rid},
        )
    ).fetchone()
    return {"status": r[0], "request_type": r[1], "webinar_link": r[2], "closed_by": r[3]}


async def _cleanup(db, *, user_ids=(), task_ids=(), request_ids=()):
    for rid in request_ids:
        await db.execute(text("DELETE FROM help_request_reopens WHERE request_id=:r"), {"r": rid})
        await db.execute(text("DELETE FROM help_request_replies WHERE request_id=:r"), {"r": rid})
        await db.execute(text("DELETE FROM help_requests WHERE id=:r"), {"r": rid})
    for uid in user_ids:
        await db.execute(text("DELETE FROM notifications WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": uid})
    for tid in task_ids:
        await db.execute(text("DELETE FROM tasks WHERE id=:t"), {"t": tid})
    await db.commit()


# ── правило: ответ закрывает заявку ──────────────────────────────────────────

async def test_text_reply_closes_manual_help_even_when_client_says_otherwise(db, client):
    """Клиент прислал `close_after_reply=false` — заявка всё равно закрывается.

    Это ядро решения оператора. Если бы правило жило в клиентах, веб и бот
    закрывали бы заявки по-разному, и KPI возвратов считался бы по разным
    основаниям.
    """
    sid, _ = await _user(db, "t303t ученик")
    tid_user, _ = await _user(db, "t303t учитель")
    task_id = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=task_id, teacher_id=tid_user)
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/teacher/help-requests/{rid}/reply",
            json={"teacher_id": tid_user, "message": "Смотри формулу X", "close_after_reply": False},
            headers=_svc(),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["request_status"] == "closed"
        row = await _row(db, rid)
        assert row["status"] == "closed"
        assert row["closed_by"] == tid_user, "закрыл ответивший преподаватель"
    finally:
        await _cleanup(db, user_ids=[sid, tid_user], task_ids=[task_id], request_ids=[rid])


async def test_reply_does_not_close_individual_review(db, client):
    """У разбора своё закрытие — оценка ученика. Ответ текстом его не закрывает."""
    sid, _ = await _user(db, "t303t ученик")
    tid_user, _ = await _user(db, "t303t учитель")
    task_id = await _task(db)
    rid = await _help_request(
        db, student_id=sid, task_id=task_id, teacher_id=tid_user,
        request_type="individual_review",
    )
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/teacher/help-requests/{rid}/reply",
            json={"teacher_id": tid_user, "message": "Давай разберём", "close_after_reply": False},
            headers=_svc(),
        )
        assert resp.status_code == 200, resp.text
        assert (await _row(db, rid))["status"] == "open"
    finally:
        await _cleanup(db, user_ids=[sid, tid_user], task_ids=[task_id], request_ids=[rid])


async def test_reply_does_not_close_blocked_limit(db, client):
    """Регресс: заявка по лимиту попыток закрывается выдачей лимита, не ответом."""
    sid, _ = await _user(db, "t303t ученик")
    tid_user, _ = await _user(db, "t303t учитель")
    task_id = await _task(db)
    rid = await _help_request(
        db, student_id=sid, task_id=task_id, teacher_id=tid_user, request_type="blocked_limit",
    )
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/teacher/help-requests/{rid}/reply",
            json={"teacher_id": tid_user, "message": "Продлил", "close_after_reply": False},
            headers=_svc(),
        )
        assert resp.status_code == 200, resp.text
        assert (await _row(db, rid))["status"] == "open"
    finally:
        await _cleanup(db, user_ids=[sid, tid_user], task_ids=[task_id], request_ids=[rid])


async def test_second_reply_to_closed_request_is_rejected_clearly(db, client):
    """Второй ответ подряд отбивается — и это про закрытие, а не про блокировку.

    Именно на это опирается парная правка бота TG_LMS: он раскладывал ЛЮБОЙ 409
    как «кейс занят другим преподавателем», что после введения авто-закрытия
    стало неправдой.
    """
    sid, _ = await _user(db, "t303t ученик")
    tid_user, _ = await _user(db, "t303t учитель")
    task_id = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=task_id, teacher_id=tid_user)
    await db.commit()
    try:
        await client.post(
            f"/api/v1/teacher/help-requests/{rid}/reply",
            json={"teacher_id": tid_user, "message": "Первый ответ"},
            headers=_svc(),
        )
        second = await client.post(
            f"/api/v1/teacher/help-requests/{rid}/reply",
            json={"teacher_id": tid_user, "message": "Дополнение"},
            headers=_svc(),
        )
        assert second.status_code == 409, second.text
        assert "закрыт" in second.json()["detail"].lower(), (
            "текст ошибки — опора клиента: по нему бот отличает закрытие от блокировки"
        )
    finally:
        await _cleanup(db, user_ids=[sid, tid_user], task_ids=[task_id], request_ids=[rid])


# ── ссылка на разбор ─────────────────────────────────────────────────────────

async def test_webinar_link_delivered_without_closing(db, client):
    """Ссылка уходит ученику, заявка остаётся открытой — разбор впереди."""
    sid, _ = await _user(db, "t303t ученик")
    tid_user, _ = await _user(db, "t303t учитель")
    task_id = await _task(db)
    rid = await _help_request(
        db, student_id=sid, task_id=task_id, teacher_id=tid_user,
        request_type="individual_review",
    )
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/teacher/help-requests/{rid}/webinar-link",
            json={"teacher_id": tid_user, "webinar_link": _WEBINAR},
            headers=_svc(),
        )
        assert resp.status_code == 200, resp.text
        row = await _row(db, rid)
        assert row["webinar_link"] == _WEBINAR
        assert row["status"] == "open", "ссылка не закрывает заявку"

        note = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM notifications WHERE user_id=:u "
                    "AND kind='individual_review_scheduled'"
                ),
                {"u": sid},
            )
        ).scalar_one()
        assert note == 1, "ученик обязан узнать о ссылке, иначе она бесполезна"
    finally:
        await _cleanup(db, user_ids=[sid, tid_user], task_ids=[task_id], request_ids=[rid])


async def test_webinar_link_rejected_for_wrong_request_type(db, client):
    """Ссылка по обычной заявке — 409: ученик её там не увидит."""
    sid, _ = await _user(db, "t303t ученик")
    tid_user, _ = await _user(db, "t303t учитель")
    task_id = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=task_id, teacher_id=tid_user)
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/teacher/help-requests/{rid}/webinar-link",
            json={"teacher_id": tid_user, "webinar_link": _WEBINAR},
            headers=_svc(),
        )
        assert resp.status_code == 409, resp.text
        assert (await _row(db, rid))["webinar_link"] is None
    finally:
        await _cleanup(db, user_ids=[sid, tid_user], task_ids=[task_id], request_ids=[rid])


@pytest.mark.parametrize("bad_link", ["", "   ", "смотри в телеграме", "meet.example/room"])
async def test_webinar_link_must_be_a_real_url(db, client, bad_link):
    """Не-ссылка даёт ученику кнопку в никуда при формально отвеченной заявке."""
    sid, _ = await _user(db, "t303t ученик")
    tid_user, _ = await _user(db, "t303t учитель")
    task_id = await _task(db)
    rid = await _help_request(
        db, student_id=sid, task_id=task_id, teacher_id=tid_user,
        request_type="individual_review",
    )
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/teacher/help-requests/{rid}/webinar-link",
            json={"teacher_id": tid_user, "webinar_link": bad_link},
            headers=_svc(),
        )
        assert resp.status_code == 422, resp.text
        assert (await _row(db, rid))["webinar_link"] is None
    finally:
        await _cleanup(db, user_ids=[sid, tid_user], task_ids=[task_id], request_ids=[rid])


async def test_reply_with_close_clears_webinar_link(db, client):
    """Второй путь закрытия (ответ с закрытием) тоже обязан снимать ссылку.

    Закрытие заявки живёт в двух местах: `close_help_request` и ветка внутри
    `reply_help_request`. Делегировать одно в другое нельзя (получилось бы два
    уведомления на одно действие), поэтому TTL продублирован — и это ровно тот
    случай, который тихо разъезжается без теста.
    """
    sid, _ = await _user(db, "t303t ученик")
    tid_user, _ = await _user(db, "t303t учитель")
    task_id = await _task(db)
    rid = await _help_request(
        db, student_id=sid, task_id=task_id, teacher_id=tid_user,
        request_type="individual_review",
    )
    await db.execute(
        text("UPDATE help_requests SET webinar_link = :l WHERE id = :r"),
        {"l": _WEBINAR, "r": rid},
    )
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/teacher/help-requests/{rid}/reply",
            json={
                "teacher_id": tid_user,
                "message": "Разобрали, закрываю",
                "close_after_reply": True,
            },
            headers=_svc(),
        )
        assert resp.status_code == 200, resp.text
        row = await _row(db, rid)
        assert row["status"] == "closed"
        assert row["webinar_link"] is None, "TTL ссылки обязан работать и на этом пути закрытия"
    finally:
        await _cleanup(db, user_ids=[sid, tid_user], task_ids=[task_id], request_ids=[rid])


# ── новый класс заявки в уже существующем коде ───────────────────────────────

async def test_individual_review_counted_in_teacher_workload(db, client):
    """Заявка на разбор обязана быть видна в панели нагрузки.

    Итог `open_help_requests_total` складывался из двух типов поимённо — новый
    класс выпал бы из панели целиком, и преподаватель не заметил бы ученика,
    ждущего ссылку. А это самый срочный случай лестницы.
    """
    sid, _ = await _user(db, "t303t ученик")
    tid_user, token = await _user(db, "t303t учитель", role="teacher", with_session=True)
    task_id = await _task(db)
    rid = await _help_request(
        db, student_id=sid, task_id=task_id, teacher_id=tid_user,
        request_type="individual_review",
    )
    await db.commit()
    try:
        resp = await client.get(
            f"/api/v1/teacher/workload?teacher_id={tid_user}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["open_individual_review_total"] == 1
        assert body["open_help_requests_total"] >= 1, "новый тип обязан попадать в общий итог"
    finally:
        await _cleanup(db, user_ids=[sid, tid_user], task_ids=[task_id], request_ids=[rid])


async def test_individual_review_detail_survives_serialization(db, client):
    """Карточка заявки нового класса открывается, а не падает на типе.

    `HelpRequestType` — закрытый литерал: пока в нём не было
    `individual_review`, карточка и список у преподавателя роняли бы 500 ровно
    в тот момент, когда ученик поднялся на уровень 2.
    """
    sid, _ = await _user(db, "t303t ученик")
    tid_user, token = await _user(db, "t303t учитель", role="teacher", with_session=True)
    task_id = await _task(db)
    rid = await _help_request(
        db, student_id=sid, task_id=task_id, teacher_id=tid_user,
        request_type="individual_review",
    )
    await db.execute(
        text("INSERT INTO help_request_reopens (request_id, teacher_id) VALUES (:r, :t)"),
        {"r": rid, "t": tid_user},
    )
    await db.commit()
    try:
        detail = await client.get(
            f"/api/v1/teacher/help-requests/{rid}?teacher_id={tid_user}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["request_type"] == "individual_review"
        assert body["reopen_count"] == 1, "преподаватель должен видеть, что заявку возвращали"

        listing = await client.get(
            f"/api/v1/teacher/help-requests?teacher_id={tid_user}"
            "&request_type=individual_review",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert listing.status_code == 200, listing.text
        assert any(i["request_id"] == rid for i in listing.json()["items"])
    finally:
        await _cleanup(db, user_ids=[sid, tid_user], task_ids=[task_id], request_ids=[rid])
