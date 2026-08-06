"""tsk-303 Фаза 4: KPI возвратов + обращения (Поток B).

**KPI.** Оператор решил, что показатель видят ОБА: преподаватель у себя (для
самоконтроля) и методист по всем (это оценка преподавателей). Отсюда главное,
что здесь проверяется, — разграничение: свой показатель видит каждый, чужой —
только методист/админ. И что цифра одна и та же, потому что считает её один
агрегат, а не две похожие панели.

**Поток B.** Отдельная сущность: у обращения о битой ссылке нет ни ученика, ни
задания, и адресат другой — методист. Проверяется видимость (преподаватель
видит только свои, методист все), сужение НА УРОВНЕ ЗАПРОСА (постфильтр поверх
`limit` молча терял бы строки — класс из tsk-473) и то, что пустой текст не
проходит.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

pytestmark = pytest.mark.asyncio

_settings = Settings()


def _svc() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _user(db, name: str, *, role: str | None = None, with_session: bool = False):
    u = Users(
        email=f"t303f-{random.randint(10**8, 10**10)}@example.com",
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
                "e": f"t303f-{random.randint(10**8, 10**10)}",
                "c": json.dumps({"type": "SA", "stem": "x"}),
                "r": json.dumps({"max_score": 10}),
            },
        )
    ).scalar_one()


async def _request_with_reopens(db, *, student_id, task_id, teacher_id, count: int, when=None):
    rid = (
        await db.execute(
            text(
                "INSERT INTO help_requests (status, student_id, task_id, request_type, "
                "assigned_teacher_id, auto_created, context_json, priority, created_at, updated_at) "
                "VALUES ('open', :s, :t, 'manual_help', :at, false, '{}'::jsonb, 100, now(), now()) "
                "RETURNING id"
            ),
            {"s": student_id, "t": task_id, "at": teacher_id},
        )
    ).scalar_one()
    for _ in range(count):
        if when is None:
            await db.execute(
                text(
                    "INSERT INTO help_request_reopens (request_id, teacher_id) VALUES (:r, :t)"
                ),
                {"r": rid, "t": teacher_id},
            )
        else:
            await db.execute(
                text(
                    "INSERT INTO help_request_reopens (request_id, teacher_id, reopened_at) "
                    "VALUES (:r, :t, :w)"
                ),
                {"r": rid, "t": teacher_id, "w": when},
            )
    return rid


async def _cleanup(db, *, user_ids=(), task_ids=(), request_ids=(), report_ids=()):
    for rid in request_ids:
        await db.execute(text("DELETE FROM help_request_reopens WHERE request_id=:r"), {"r": rid})
        await db.execute(text("DELETE FROM help_requests WHERE id=:r"), {"r": rid})
    for fid in report_ids:
        await db.execute(text("DELETE FROM feedback_reports WHERE id=:f"), {"f": fid})
    for uid in user_ids:
        await db.execute(text("DELETE FROM feedback_reports WHERE author_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM notifications WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": uid})
    for tid in task_ids:
        await db.execute(text("DELETE FROM tasks WHERE id=:t"), {"t": tid})
    await db.commit()


# ── KPI возвратов ────────────────────────────────────────────────────────────

async def test_teacher_sees_own_reopen_kpi(db, client):
    """Свой показатель — для самоконтроля, доступен без особых ролей."""
    sid, _ = await _user(db, "t303f ученик")
    tid_user, token = await _user(db, "t303f учитель", role="teacher", with_session=True)
    task_id = await _task(db)
    rid = await _request_with_reopens(
        db, student_id=sid, task_id=task_id, teacher_id=tid_user, count=3
    )
    await db.commit()
    try:
        resp = await client.get(
            f"/api/v1/teacher/help-requests/kpi/reopens?teacher_id={tid_user}",
            headers=_bearer(token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_reopens"] == 3
        assert len(body["items"]) == 1
        assert body["items"][0]["teacher_id"] == tid_user
        assert body["items"][0]["requests"] == 1, "три возврата по одной заявке"
    finally:
        await _cleanup(db, user_ids=[sid, tid_user], task_ids=[task_id], request_ids=[rid])


async def test_teacher_cannot_see_foreign_kpi(db, client):
    """Чужой показатель — оценка человека, её видит только методист/админ."""
    tid_user, token = await _user(db, "t303f учитель", role="teacher", with_session=True)
    other_id, _ = await _user(db, "t303f другой учитель")
    await db.commit()
    try:
        resp = await client.get(
            f"/api/v1/teacher/help-requests/kpi/reopens?teacher_id={other_id}",
            headers=_bearer(token),
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, user_ids=[tid_user, other_id])


async def test_teacher_cannot_see_overview(db, client):
    """Сводка по всем — тоже оценка, преподавателю её не отдаём."""
    tid_user, token = await _user(db, "t303f учитель", role="teacher", with_session=True)
    await db.commit()
    try:
        resp = await client.get(
            "/api/v1/teacher/help-requests/kpi/reopens", headers=_bearer(token)
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, user_ids=[tid_user])


async def test_methodist_sees_all_teachers_and_same_numbers(db, client):
    """Методист видит сводку, и цифры совпадают с личной панелью преподавателя.

    Это и есть смысл одного агрегата на двух потребителей: расхождение между
    «моя панель» и «панель методиста» было бы спором о фактах.
    """
    sid, _ = await _user(db, "t303f ученик")
    t_a, token_a = await _user(db, "t303f учитель А", role="teacher", with_session=True)
    t_b, _ = await _user(db, "t303f учитель Б", role="teacher")
    m_id, m_token = await _user(db, "t303f методист", role="methodist", with_session=True)
    task_id = await _task(db)
    r_a = await _request_with_reopens(
        db, student_id=sid, task_id=task_id, teacher_id=t_a, count=2
    )
    r_b = await _request_with_reopens(
        db, student_id=sid, task_id=task_id, teacher_id=t_b, count=1
    )
    await db.commit()
    try:
        overview = await client.get(
            "/api/v1/teacher/help-requests/kpi/reopens", headers=_bearer(m_token)
        )
        assert overview.status_code == 200, overview.text
        rows = {i["teacher_id"]: i["reopens"] for i in overview.json()["items"]}
        assert rows.get(t_a) == 2 and rows.get(t_b) == 1
        assert rows[t_a] > rows[t_b], "сортировка по убыванию — худшие сверху"

        own = await client.get(
            f"/api/v1/teacher/help-requests/kpi/reopens?teacher_id={t_a}",
            headers=_bearer(token_a),
        )
        assert own.json()["items"][0]["reopens"] == rows[t_a], (
            "личная панель и сводка методиста обязаны показывать одно число"
        )
    finally:
        await _cleanup(
            db, user_ids=[sid, t_a, t_b, m_id], task_ids=[task_id], request_ids=[r_a, r_b]
        )


async def test_kpi_window_excludes_older_reopens(db, client):
    """Окно «за период» — то, ради чего история хранится строками, а не счётчиком."""
    sid, _ = await _user(db, "t303f ученик")
    tid_user, token = await _user(db, "t303f учитель", role="teacher", with_session=True)
    task_id = await _task(db)
    old = datetime.now(timezone.utc) - timedelta(days=90)
    r_old = await _request_with_reopens(
        db, student_id=sid, task_id=task_id, teacher_id=tid_user, count=5, when=old
    )
    r_new = await _request_with_reopens(
        db, student_id=sid, task_id=task_id, teacher_id=tid_user, count=2
    )
    await db.commit()
    try:
        # `since` передаётся параметром, а не склейкой в строку: смещение
        # `+00:00` в сыром URL декодируется как пробел, и сервер отвечает 422.
        # Ловушка не тестовая — на неё наступит любой клиент, собирающий URL
        # руками.
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        resp = await client.get(
            "/api/v1/teacher/help-requests/kpi/reopens",
            params={"teacher_id": tid_user, "since": since},
            headers=_bearer(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["total_reopens"] == 2, "старые возвраты в окно не входят"

        all_time = await client.get(
            f"/api/v1/teacher/help-requests/kpi/reopens?teacher_id={tid_user}",
            headers=_bearer(token),
        )
        assert all_time.json()["total_reopens"] == 7
    finally:
        await _cleanup(
            db, user_ids=[sid, tid_user], task_ids=[task_id], request_ids=[r_old, r_new]
        )


# ── Поток B: обращения ───────────────────────────────────────────────────────

async def test_teacher_creates_report_and_methodist_sees_it(db, client):
    """Точка входа у преподавателя, инбокс — у методиста (решение оператора)."""
    t_id, t_token = await _user(db, "t303f учитель", role="teacher", with_session=True)
    m_id, m_token = await _user(db, "t303f методист", role="methodist", with_session=True)
    await db.commit()
    report_id = None
    try:
        created = await client.post(
            "/api/v1/feedback-reports",
            json={"report_type": "content", "body": "В уроке 3 битая ссылка на材ериал"},
            headers=_bearer(t_token),
        )
        assert created.status_code == 201, created.text
        report_id = created.json()["report_id"]

        inbox = await client.get("/api/v1/feedback-reports", headers=_bearer(m_token))
        assert inbox.status_code == 200, inbox.text
        assert inbox.json()["scope"] == "all"
        assert any(i["report_id"] == report_id for i in inbox.json()["items"])
    finally:
        await _cleanup(db, user_ids=[t_id, m_id], report_ids=[report_id] if report_id else [])


async def test_teacher_sees_only_own_reports(db, client):
    """Чужие жалобы на контент преподавателю не адресованы.

    Сужение делает запрос к БД, а не постфильтр по уже выбранной странице —
    иначе чужие строки съедали бы `limit` и свои терялись (класс из tsk-473).
    """
    t_a, token_a = await _user(db, "t303f учитель А", role="teacher", with_session=True)
    t_b, token_b = await _user(db, "t303f учитель Б", role="teacher", with_session=True)
    await db.commit()
    ids = []
    try:
        for token in (token_a, token_b):
            r = await client.post(
                "/api/v1/feedback-reports",
                json={"report_type": "bug", "body": "Кнопка не нажимается"},
                headers=_bearer(token),
            )
            assert r.status_code == 201, r.text
            ids.append(r.json()["report_id"])

        mine = await client.get("/api/v1/feedback-reports", headers=_bearer(token_a))
        assert mine.status_code == 200, mine.text
        body = mine.json()
        assert body["scope"] == "own"
        assert body["total"] == 1, "в счётчике тоже только свои, а не всё с постфильтром"
        assert body["items"][0]["report_id"] == ids[0]
    finally:
        await _cleanup(db, user_ids=[t_a, t_b], report_ids=ids)


async def test_student_cannot_create_report(db, client):
    """Ученику этот поток не адресован — для его задания есть заявка помощи."""
    s_id, s_token = await _user(db, "t303f ученик", role="student", with_session=True)
    await db.commit()
    try:
        resp = await client.post(
            "/api/v1/feedback-reports",
            json={"report_type": "feature_idea", "body": "Хочу тёмную тему"},
            headers=_bearer(s_token),
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, user_ids=[s_id])


@pytest.mark.parametrize("bad_body", ["", "   ", "\t\n"])
async def test_blank_report_body_rejected(db, client, bad_body):
    """Обращение без содержания — строка в инбоксе, которую нечего разбирать."""
    t_id, t_token = await _user(db, "t303f учитель", role="teacher", with_session=True)
    await db.commit()
    try:
        resp = await client.post(
            "/api/v1/feedback-reports",
            json={"report_type": "bug", "body": bad_body},
            headers=_bearer(t_token),
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_ids=[t_id])


async def test_unknown_report_type_rejected(db, client):
    """Тип из закрытого набора: иначе инбокс не разложить по разделам."""
    t_id, t_token = await _user(db, "t303f учитель", role="teacher", with_session=True)
    await db.commit()
    try:
        resp = await client.post(
            "/api/v1/feedback-reports",
            json={"report_type": "прочее", "body": "что-то не так"},
            headers=_bearer(t_token),
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_ids=[t_id])


async def test_methodist_closes_report_idempotently(db, client):
    """Закрытие методистом; повтор — не ошибка (кнопку жмут дважды)."""
    t_id, t_token = await _user(db, "t303f учитель", role="teacher", with_session=True)
    m_id, m_token = await _user(db, "t303f методист", role="methodist", with_session=True)
    await db.commit()
    report_id = None
    try:
        created = await client.post(
            "/api/v1/feedback-reports",
            json={"report_type": "bug", "body": "Падает экспорт"},
            headers=_bearer(t_token),
        )
        report_id = created.json()["report_id"]

        first = await client.post(
            f"/api/v1/feedback-reports/{report_id}/close",
            json={"resolution_comment": "Починили"},
            headers=_bearer(m_token),
        )
        assert first.status_code == 200, first.text
        assert first.json()["already_closed"] is False

        second = await client.post(
            f"/api/v1/feedback-reports/{report_id}/close",
            json={},
            headers=_bearer(m_token),
        )
        assert second.status_code == 200, second.text
        assert second.json()["already_closed"] is True

        row = (
            await db.execute(
                text(
                    "SELECT status, closed_by, resolution_comment, closed_at "
                    "FROM feedback_reports WHERE id = :f"
                ),
                {"f": report_id},
            )
        ).fetchone()
        assert row[0] == "closed" and row[1] == m_id
        assert row[2] == "Починили", "повторное закрытие не затирает разбор первого"
        assert row[3] is not None, "закрытие всегда со следом времени"
    finally:
        await _cleanup(db, user_ids=[t_id, m_id], report_ids=[report_id] if report_id else [])


async def test_foreign_teacher_cannot_close_report(db, client):
    """Закрыть может автор или методист/админ — но не посторонний преподаватель."""
    t_a, token_a = await _user(db, "t303f учитель А", role="teacher", with_session=True)
    t_b, token_b = await _user(db, "t303f учитель Б", role="teacher", with_session=True)
    await db.commit()
    report_id = None
    try:
        created = await client.post(
            "/api/v1/feedback-reports",
            json={"report_type": "content", "body": "Опечатка в задании"},
            headers=_bearer(token_a),
        )
        report_id = created.json()["report_id"]

        resp = await client.post(
            f"/api/v1/feedback-reports/{report_id}/close", json={}, headers=_bearer(token_b)
        )
        assert resp.status_code == 403, resp.text

        own = await client.post(
            f"/api/v1/feedback-reports/{report_id}/close", json={}, headers=_bearer(token_a)
        )
        assert own.status_code == 200, "автор своё обращение закрыть может"
    finally:
        await _cleanup(db, user_ids=[t_a, t_b], report_ids=[report_id] if report_id else [])
