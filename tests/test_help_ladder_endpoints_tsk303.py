"""tsk-303 Фаза 2: лестница помощи, сторона ученика.

Покрывают четыре новых эндпоинта и, главное, ГЕЙТЫ между уровнями — именно они
несут продуктовое решение оператора, а не механика записи:

- уровень 1 → «Вернуть заявку» доступен только по ЗАКРЫТОЙ заявке `manual_help`;
- уровень 2 → «Запросить индивидуальный разбор» только после возврата по ТОМУ ЖЕ
  заданию (то есть по той же заявке — возврат реоткрывает её, а не плодит новую);
- уровень 3 → оценка только когда преподаватель уже прислал ссылку, ровно один
  раз, и «непонятно» уводит заявку методисту.

Отдельно проверяется атрибуция возврата: он начисляется тому, чей ответ не
помог, а не назначенному преподавателю — это и есть KPI, и перепутать здесь
человека значит оценить не того.

Тесты идут внутри общей откатываемой транзакции; уборка за собой — на случай
прогонов вне изоляции.
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

_WEBINAR = "https://meet.example/room-303"


def _headers() -> dict[str, str]:
    """Заголовки сервисного ключа (действует от имени владельца заявки)."""
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _user(db, name: str, *, role: str | None = None, with_session: bool = False):
    """Создать пользователя; при with_session — вернуть (id, token)."""
    u = Users(
        email=f"t303-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=name,
        tg_id=None,
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
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
            "course_id, difficulty_id) "
            "VALUES (:e, 10, CAST(:c AS jsonb), CAST(:r AS jsonb), 1, 1) RETURNING id"
        ),
        {
            "e": f"t303-{random.randint(10**8, 10**10)}",
            "c": json.dumps({"type": "SA", "stem": "x"}),
            "r": json.dumps({"max_score": 10}),
        },
    )
    return res.scalar_one()


async def _help_request(
    db,
    *,
    student_id: int,
    task_id: int,
    assigned_teacher_id: int | None = None,
    request_type: str = "manual_help",
    status: str = "open",
    closed_by: int | None = None,
) -> int:
    res = await db.execute(
        text(
            "INSERT INTO help_requests (status, student_id, task_id, request_type, "
            "assigned_teacher_id, closed_by, closed_at, auto_created, context_json, "
            "priority, message, created_at, updated_at) "
            "VALUES (:st, :s, :t, :rt, :at, :cb, "
            "        CASE WHEN :is_closed THEN now() ELSE NULL END, "
            "        false, '{}'::jsonb, 100, 'не понимаю', now(), now()) RETURNING id"
        ),
        {
            "st": status,
            "is_closed": status == "closed",
            "s": student_id,
            "t": task_id,
            "rt": request_type,
            "at": assigned_teacher_id,
            "cb": closed_by,
        },
    )
    return res.scalar_one()


async def _row(db, request_id: int) -> dict:
    r = (
        await db.execute(
            text(
                "SELECT status, request_type, webinar_link, review_understood, "
                "escalated_to_methodist_at, closed_by, resolution_comment "
                "FROM help_requests WHERE id = :id"
            ),
            {"id": request_id},
        )
    ).fetchone()
    return {
        "status": r[0], "request_type": r[1], "webinar_link": r[2],
        "review_understood": r[3], "escalated_at": r[4], "closed_by": r[5],
        "resolution_comment": r[6],
    }


async def _cleanup(db, *, user_ids=(), task_ids=(), request_ids=()):
    for rid in request_ids:
        await db.execute(text("DELETE FROM help_request_reopens WHERE request_id=:r"), {"r": rid})
        await db.execute(text("DELETE FROM help_requests WHERE id=:r"), {"r": rid})
    for uid in user_ids:
        await db.execute(text("DELETE FROM notifications WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM help_requests WHERE student_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": uid})
        # Сами `users` не удаляем: FK `audit_event.user_id` — ON DELETE SET NULL,
        # то есть UPDATE по append-only таблице, и триггер его отбивает. Тот же
        # приём, что в tsk-298-тестах; строки всё равно уходят с откатом
        # транзакции теста.
    for tid in task_ids:
        await db.execute(text("DELETE FROM tasks WHERE id=:t"), {"t": tid})
    await db.commit()


# ── чтение состояния ─────────────────────────────────────────────────────────

async def test_state_is_null_when_student_never_asked(db, client):
    """Ученик помощь не просил — эндпоинт отдаёт null, а не 404."""
    sid, token = await _user(db, "t303 ученик", with_session=True)
    tid = await _task(db)
    await db.commit()
    try:
        resp = await client.get(
            f"/api/v1/learning/tasks/{tid}/help-request?student_id={sid}",
            headers=_bearer(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() is None
    finally:
        await _cleanup(db, user_ids=[sid], task_ids=[tid])


async def test_state_of_foreign_student_forbidden(db, client):
    """Чужой student_id в запросе состояния — 403, а не чужая заявка в ответе."""
    sid, token = await _user(db, "t303 ученик", with_session=True)
    other_id, _ = await _user(db, "t303 чужой")
    tid = await _task(db)
    await db.commit()
    try:
        resp = await client.get(
            f"/api/v1/learning/tasks/{tid}/help-request?student_id={other_id}",
            headers=_bearer(token),
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, user_ids=[sid, other_id], task_ids=[tid])


async def test_blocked_limit_request_not_shown_as_ladder(db, client):
    """Заявка «лимит попыток» — другой механизм, в лестницу не попадает."""
    sid, token = await _user(db, "t303 ученик", with_session=True)
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, request_type="blocked_limit")
    await db.commit()
    try:
        resp = await client.get(
            f"/api/v1/learning/tasks/{tid}/help-request?student_id={sid}",
            headers=_bearer(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() is None, "blocked_limit не должен показываться как заявка лестницы"
    finally:
        await _cleanup(db, user_ids=[sid], task_ids=[tid], request_ids=[rid])


# ── уровень 1: возврат заявки ────────────────────────────────────────────────

async def test_reopen_rejected_while_request_is_open(db, client):
    """Открытую заявку возвращать нечего — 409, а не молчаливый успех."""
    sid, token = await _user(db, "t303 ученик", with_session=True)
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid)
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/learning/help-requests/{rid}/reopen", headers=_bearer(token)
        )
        assert resp.status_code == 409, resp.text
    finally:
        await _cleanup(db, user_ids=[sid], task_ids=[tid], request_ids=[rid])


async def test_reopen_of_foreign_request_forbidden(db, client):
    """Чужая заявка по её id — 403. Перебор id чужую заявку не открывает."""
    sid, token = await _user(db, "t303 ученик", with_session=True)
    other_id, _ = await _user(db, "t303 чужой")
    tid = await _task(db)
    rid = await _help_request(db, student_id=other_id, task_id=tid, status="closed")
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/learning/help-requests/{rid}/reopen", headers=_bearer(token)
        )
        assert resp.status_code == 403, resp.text
        assert (await _row(db, rid))["status"] == "closed", "чужая заявка не должна открыться"
    finally:
        await _cleanup(db, user_ids=[sid, other_id], task_ids=[tid], request_ids=[rid])


async def test_reopen_is_credited_to_the_teacher_who_answered(db, client):
    """Возврат начисляется тому, чей ответ не помог, а не назначенному.

    По ACL заявку может закрыть не только назначенный преподаватель (методист
    по роли, преподаватель по связи с учеником). Ровно поэтому история
    возвратов — таблица со ссылкой на человека, а не счётчик на заявке:
    счётчик повесил бы чужой промах на `assigned_teacher_id`.
    """
    sid, token = await _user(db, "t303 ученик", with_session=True)
    assigned_id, _ = await _user(db, "t303 назначенный")
    answered_id, _ = await _user(db, "t303 ответивший")
    tid = await _task(db)
    rid = await _help_request(
        db, student_id=sid, task_id=tid, assigned_teacher_id=assigned_id,
        status="closed", closed_by=answered_id,
    )
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/learning/help-requests/{rid}/reopen", headers=_bearer(token)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["reopen_count"] == 1

        credited = (
            await db.execute(
                text("SELECT teacher_id FROM help_request_reopens WHERE request_id=:r"),
                {"r": rid},
            )
        ).scalar_one()
        assert credited == answered_id, "возврат обязан достаться ответившему, а не назначенному"

        row = await _row(db, rid)
        assert row["status"] == "open"
        assert row["closed_by"] is None, "поля закрытия обязаны очиститься при возврате"
        assert row["resolution_comment"] is None
    finally:
        await _cleanup(
            db, user_ids=[sid, assigned_id, answered_id], task_ids=[tid], request_ids=[rid]
        )


async def test_reopen_falls_back_to_assigned_teacher(db, client):
    """Заявку закрыли системно (`closed_by IS NULL`) — возврат идёт назначенному."""
    sid, token = await _user(db, "t303 ученик", with_session=True)
    assigned_id, _ = await _user(db, "t303 назначенный")
    tid = await _task(db)
    rid = await _help_request(
        db, student_id=sid, task_id=tid, assigned_teacher_id=assigned_id, status="closed",
    )
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/learning/help-requests/{rid}/reopen", headers=_bearer(token)
        )
        assert resp.status_code == 200, resp.text
        credited = (
            await db.execute(
                text("SELECT teacher_id FROM help_request_reopens WHERE request_id=:r"),
                {"r": rid},
            )
        ).scalar_one()
        assert credited == assigned_id
    finally:
        await _cleanup(db, user_ids=[sid, assigned_id], task_ids=[tid], request_ids=[rid])


# ── уровень 2: индивидуальный разбор ─────────────────────────────────────────

async def test_individual_review_requires_a_prior_reopen(db, client):
    """Без возврата разбор недоступен — это и есть «повторная заявка»."""
    sid, token = await _user(db, "t303 ученик", with_session=True)
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid)
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/learning/help-requests/{rid}/request-individual-review",
            headers=_bearer(token),
        )
        assert resp.status_code == 409, resp.text
        assert (await _row(db, rid))["request_type"] == "manual_help"
    finally:
        await _cleanup(db, user_ids=[sid], task_ids=[tid], request_ids=[rid])


async def test_second_help_request_on_same_task_also_opens_review(db, client):
    """Повтор бывает не только через кнопку «Вернуть заявку».

    `request-help` дедуплицирует обращения лишь коротким окном, поэтому ученик
    достижимо заводит по тому же заданию ВТОРУЮ заявку. Оператор задал гейт как
    «повторная заявка по тому же `task_id`» — засчитывать надо и этот путь,
    иначе разбор не откроется именно тому, кто просил помощь дважды.
    """
    sid, token = await _user(db, "t303 ученик", with_session=True)
    tid = await _task(db)
    first = await _help_request(db, student_id=sid, task_id=tid, status="closed")
    second = await _help_request(db, student_id=sid, task_id=tid)
    await db.commit()
    try:
        state = (
            await client.get(
                f"/api/v1/learning/tasks/{tid}/help-request?student_id={sid}",
                headers=_bearer(token),
            )
        ).json()
        assert state["request_id"] == second, "показываем последнюю заявку"
        assert state["reopen_count"] == 0, "эту заявку не возвращали"
        assert state["can_request_individual_review"] is True, (
            "вторая заявка по тому же заданию — это и есть повторное обращение"
        )

        resp = await client.post(
            f"/api/v1/learning/help-requests/{second}/request-individual-review",
            headers=_bearer(token),
        )
        assert resp.status_code == 200, resp.text
        assert (await _row(db, second))["request_type"] == "individual_review"
    finally:
        await _cleanup(db, user_ids=[sid], task_ids=[tid], request_ids=[first, second])


async def test_individual_review_is_idempotent(db, client):
    """Повторный клик по кнопке — не ошибка: заявка уже в нужном классе."""
    sid, token = await _user(db, "t303 ученик", with_session=True)
    teacher_id, _ = await _user(db, "t303 учитель")
    tid = await _task(db)
    rid = await _help_request(
        db, student_id=sid, task_id=tid, assigned_teacher_id=teacher_id,
        status="closed", closed_by=teacher_id,
    )
    await db.commit()
    try:
        await client.post(f"/api/v1/learning/help-requests/{rid}/reopen", headers=_bearer(token))
        first = await client.post(
            f"/api/v1/learning/help-requests/{rid}/request-individual-review",
            headers=_bearer(token),
        )
        assert first.status_code == 200, first.text
        assert first.json()["already"] is False

        second = await client.post(
            f"/api/v1/learning/help-requests/{rid}/request-individual-review",
            headers=_bearer(token),
        )
        assert second.status_code == 200, second.text
        assert second.json()["already"] is True
    finally:
        await _cleanup(db, user_ids=[sid, teacher_id], task_ids=[tid], request_ids=[rid])


# ── уровень 3: оценка разбора ────────────────────────────────────────────────

async def test_rate_review_rejected_without_webinar_link(db, client):
    """Оценивать нечего, пока преподаватель не прислал ссылку."""
    sid, token = await _user(db, "t303 ученик", with_session=True)
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, request_type="individual_review")
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/learning/help-requests/{rid}/rate-review",
            json={"understood": True},
            headers=_bearer(token),
        )
        assert resp.status_code == 409, resp.text
    finally:
        await _cleanup(db, user_ids=[sid], task_ids=[tid], request_ids=[rid])


async def test_positive_rating_closes_request_and_clears_link(db, client):
    """«Понятно» закрывает заявку, ссылка на комнату не остаётся жить вечно.

    TTL ссылки — решение оператора: она живёт, пока заявка открыта. Оценка при
    этом сохраняется: она история разбора, а ссылка ведёт в уже несуществующую
    комнату.
    """
    sid, token = await _user(db, "t303 ученик", with_session=True)
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, request_type="individual_review")
    await db.execute(
        text("UPDATE help_requests SET webinar_link = :l WHERE id = :r"),
        {"l": _WEBINAR, "r": rid},
    )
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/learning/help-requests/{rid}/rate-review",
            json={"understood": True},
            headers=_bearer(token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "closed" and body["escalated"] is False

        row = await _row(db, rid)
        assert row["status"] == "closed"
        assert row["review_understood"] is True, "оценка обязана пережить закрытие"
        assert row["webinar_link"] is None, "ссылка обязана обнулиться при закрытии (TTL)"
        assert row["closed_by"] is None, "закрыл не учитель — не выдумываем автора"
        assert row["escalated_at"] is None
    finally:
        await _cleanup(db, user_ids=[sid], task_ids=[tid], request_ids=[rid])


async def test_negative_rating_escalates_to_methodist(db, client):
    """«Непонятно» уводит заявку методисту и оставляет её ОТКРЫТОЙ.

    Заявка не закрывается: методисту с ней ещё работать, он и закроет её через
    существующий `/teacher/help-requests/{id}/close`. Эскалация обязана быть
    видна в очереди методиста — иначе уведомление создано, а показать его негде.
    """
    sid, token = await _user(db, "t303 ученик", with_session=True)
    m_id, m_token = await _user(db, "t303 методист", role="methodist", with_session=True)
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, request_type="individual_review")
    await db.execute(
        text("UPDATE help_requests SET webinar_link = :l WHERE id = :r"),
        {"l": _WEBINAR, "r": rid},
    )
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/learning/help-requests/{rid}/rate-review",
            json={"understood": False},
            headers=_bearer(token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["escalated"] is True and body["status"] == "open"

        row = await _row(db, rid)
        assert row["status"] == "open", "методисту ещё работать с этой заявкой"
        assert row["escalated_at"] is not None
        assert row["review_understood"] is False

        queue = await client.get(
            "/api/v1/methodist/escalations/pending", headers=_bearer(m_token)
        )
        assert queue.status_code == 200, queue.text
        mine = [
            i for i in queue.json()["items"]
            if i["kind"] == "help_request_escalated"
            and i["payload"].get("request_id") == rid
        ]
        assert len(mine) == 1, "эскалация обязана быть видна методисту в его очереди"
    finally:
        await _cleanup(db, user_ids=[sid, m_id], task_ids=[tid], request_ids=[rid])


async def test_rating_is_accepted_only_once(db, client):
    """Оценка — развилка маршрута, а не мнение: второй ответ отбивается.

    Иначе «понятно» после «непонятно» закрыло бы заявку, которую уже забрал
    методист, а обратный порядок дёрнул бы методистов повторно.
    """
    sid, token = await _user(db, "t303 ученик", with_session=True)
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, request_type="individual_review")
    await db.execute(
        text("UPDATE help_requests SET webinar_link = :l WHERE id = :r"),
        {"l": _WEBINAR, "r": rid},
    )
    await db.commit()
    try:
        first = await client.post(
            f"/api/v1/learning/help-requests/{rid}/rate-review",
            json={"understood": False},
            headers=_bearer(token),
        )
        assert first.status_code == 200, first.text
        second = await client.post(
            f"/api/v1/learning/help-requests/{rid}/rate-review",
            json={"understood": True},
            headers=_bearer(token),
        )
        assert second.status_code == 409, second.text
        assert (await _row(db, rid))["review_understood"] is False, "первая оценка не переписывается"
    finally:
        await _cleanup(db, user_ids=[sid], task_ids=[tid], request_ids=[rid])


async def test_individual_review_of_foreign_request_forbidden(db, client):
    """Проверка владельца — своя в каждой операции, а не общая на все три."""
    sid, token = await _user(db, "t303 ученик", with_session=True)
    other_id, _ = await _user(db, "t303 чужой")
    tid = await _task(db)
    rid = await _help_request(db, student_id=other_id, task_id=tid)
    await db.execute(
        text("INSERT INTO help_request_reopens (request_id, teacher_id) VALUES (:r, NULL)"),
        {"r": rid},
    )
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/learning/help-requests/{rid}/request-individual-review",
            headers=_bearer(token),
        )
        assert resp.status_code == 403, resp.text
        assert (await _row(db, rid))["request_type"] == "manual_help"
    finally:
        await _cleanup(db, user_ids=[sid, other_id], task_ids=[tid], request_ids=[rid])


async def test_rate_review_of_foreign_request_forbidden(db, client):
    """Оценить чужой разбор нельзя — иначе посторонний уводил бы заявку методисту."""
    sid, token = await _user(db, "t303 ученик", with_session=True)
    other_id, _ = await _user(db, "t303 чужой")
    tid = await _task(db)
    rid = await _help_request(
        db, student_id=other_id, task_id=tid, request_type="individual_review"
    )
    await db.execute(
        text("UPDATE help_requests SET webinar_link = :l WHERE id = :r"),
        {"l": _WEBINAR, "r": rid},
    )
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/learning/help-requests/{rid}/rate-review",
            json={"understood": False},
            headers=_bearer(token),
        )
        assert resp.status_code == 403, resp.text
        row = await _row(db, rid)
        assert row["review_understood"] is None and row["escalated_at"] is None
    finally:
        await _cleanup(db, user_ids=[sid, other_id], task_ids=[tid], request_ids=[rid])


# ── сквозной путь ────────────────────────────────────────────────────────────

async def test_full_ladder_flow_visible_to_student(db, client):
    """Весь путь глазами ученика: признаки can_* ведут его по лестнице.

    Клиент не считает гейты сам — он читает их отсюда. Тест фиксирует именно
    эту последовательность, потому что рассинхрон признаков и операций даст
    кнопку, которая при нажатии отдаёт 409.
    """
    sid, token = await _user(db, "t303 ученик", with_session=True)
    teacher_id, _ = await _user(db, "t303 учитель")
    tid = await _task(db)
    rid = await _help_request(
        db, student_id=sid, task_id=tid, assigned_teacher_id=teacher_id,
        status="closed", closed_by=teacher_id,
    )
    await db.commit()
    url = f"/api/v1/learning/tasks/{tid}/help-request?student_id={sid}"
    try:
        state = (await client.get(url, headers=_bearer(token))).json()
        assert state["can_reopen"] is True
        assert state["can_request_individual_review"] is False
        assert state["reopen_count"] == 0

        await client.post(f"/api/v1/learning/help-requests/{rid}/reopen", headers=_bearer(token))
        state = (await client.get(url, headers=_bearer(token))).json()
        assert state["status"] == "open"
        assert state["reopen_count"] == 1
        assert state["can_reopen"] is False, "открытую заявку возвращать нельзя"
        assert state["can_request_individual_review"] is True

        await client.post(
            f"/api/v1/learning/help-requests/{rid}/request-individual-review",
            headers=_bearer(token),
        )
        # Ссылку присылает преподаватель — его эндпоинт появится в фазе 3,
        # здесь подставляем её напрямую, чтобы проверить оценку.
        await db.execute(
            text("UPDATE help_requests SET webinar_link = :l WHERE id = :r"),
            {"l": _WEBINAR, "r": rid},
        )
        await db.commit()

        state = (await client.get(url, headers=_bearer(token))).json()
        assert state["request_type"] == "individual_review"
        assert state["webinar_link"] == _WEBINAR
        assert state["can_rate_review"] is True

        await client.post(
            f"/api/v1/learning/help-requests/{rid}/rate-review",
            json={"understood": True},
            headers=_bearer(token),
        )
        state = (await client.get(url, headers=_bearer(token))).json()
        assert state["status"] == "closed"
        assert state["review_understood"] is True
        assert state["webinar_link"] is None
        assert state["can_rate_review"] is False
        assert state["can_reopen"] is False, "разбор возвращать нельзя — у него своя оценка"
    finally:
        await _cleanup(db, user_ids=[sid, teacher_id], task_ids=[tid], request_ids=[rid])
