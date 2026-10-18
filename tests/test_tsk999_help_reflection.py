"""tsk-999 (П10): рефлексия ученика после ответа на заявку помощи.

Решение оператора Р2: после ЛЮБОГО ответа на заявку — текстового ответа
преподавателя и ссылки на индивидуальный разбор — ученик может коротко
написать, что было главным и что осталось непонятным. Необязательно, один раз.

Проверяется:
- рубильник `HELP_REFLECTION_ENABLED` по умолчанию выключен: `can_reflect`
  false, эндпоинт — 409 `reflection_disabled`, ничего не пишется;
- когда можно: есть ответ преподавателя (после последнего возврата) или ссылка;
- один раз за круг заявки (повтор → 409 `already_reflected`); после возврата
  заявки и нового ответа — снова (Р2 «после любого ответа»); только владелец,
  длина ≤ 500;
- для разбора «понятно?» идёт по развилке `rate_individual_review`
  (закрытие / эскалация методисту), для обычной заявки — только сохраняется;
- преподаватель видит рефлексию в карточке заявки.

Помощники — из тестов лестницы tsk-303, чтобы заявки создавались тем же путём.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.services import help_requests_service
from test_help_ladder_endpoints_tsk303 import (
    _WEBINAR,
    _bearer,
    _cleanup,
    _headers,
    _help_request,
    _task,
    _user,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def reflection_on(monkeypatch):
    """Включить рубильник рефлексии на время теста."""
    monkeypatch.setattr(help_requests_service._settings, "help_reflection_enabled", True)


async def _teacher_reply(client, rid: int, teacher_id: int, body: str = "Смотри формулу X") -> None:
    """Ответ преподавателя штатным путём (сервисный ключ, как бот)."""
    resp = await client.post(
        f"/api/v1/teacher/help-requests/{rid}/reply",
        json={"teacher_id": teacher_id, "message": body},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text


async def _state(client, token: str, task_id: int, student_id: int) -> dict:
    resp = await client.get(
        f"/api/v1/learning/tasks/{task_id}/help-request?student_id={student_id}",
        headers=_bearer(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _reflect(client, token: str, rid: int, body: dict):
    return await client.post(
        f"/api/v1/learning/help-requests/{rid}/reflection", json=body, headers=_bearer(token)
    )


async def _reflection_row(db, rid: int) -> dict:
    r = (
        await db.execute(
            text(
                "SELECT reflection_main, reflection_unclear, reflection_understood, "
                "reflection_at, status, review_understood, escalated_to_methodist_at "
                "FROM help_requests WHERE id = :id"
            ),
            {"id": rid},
        )
    ).fetchone()
    return {
        "main": r[0], "unclear": r[1], "understood": r[2], "at": r[3],
        "status": r[4], "review_understood": r[5], "escalated_at": r[6],
    }


async def _cleanup_all(db, *, user_ids, task_ids, request_ids):
    for rid in request_ids:
        await db.execute(text("DELETE FROM help_request_replies WHERE request_id=:r"), {"r": rid})
    for uid in user_ids:
        await db.execute(text("DELETE FROM messages WHERE recipient_id=:u"), {"u": uid})
    await _cleanup(db, user_ids=user_ids, task_ids=task_ids, request_ids=request_ids)


# ── рубильник выключен (по умолчанию) ────────────────────────────────────────

async def test_switch_is_off_by_default():
    """Выкат кода не меняет поведение, пока оператор не включит."""
    assert Settings().help_reflection_enabled is False


async def test_disabled_switch_hides_and_rejects_reflection(db, client):
    """При выключенном рубильнике: can_reflect=false и 409 без записи."""
    sid, token = await _user(db, "t999 ученик", with_session=True)
    teacher_id, _ = await _user(db, "t999 учитель")
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, assigned_teacher_id=teacher_id)
    await db.commit()
    try:
        await _teacher_reply(client, rid, teacher_id)
        state = await _state(client, token, tid, sid)
        assert state["can_reflect"] is False
        assert state["can_reopen"] is True, "лестница работает как раньше"

        resp = await _reflect(client, token, rid, {"main": "формула"})
        assert resp.status_code == 409, resp.text
        assert resp.json()["payload"]["code"] == "reflection_disabled"
        assert (await _reflection_row(db, rid))["at"] is None
    finally:
        await _cleanup_all(db, user_ids=[sid, teacher_id], task_ids=[tid], request_ids=[rid])


async def test_disabled_switch_keeps_old_individual_review_rating(db, client):
    """Разбор при выключенном рубильнике — прежние кнопки оценки (can_rate_review)."""
    sid, token = await _user(db, "t999 ученик", with_session=True)
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, request_type="individual_review")
    await db.execute(
        text("UPDATE help_requests SET webinar_link = :l WHERE id = :r"), {"l": _WEBINAR, "r": rid}
    )
    await db.commit()
    try:
        state = await _state(client, token, tid, sid)
        assert state["can_rate_review"] is True
        assert state["can_reflect"] is False
    finally:
        await _cleanup_all(db, user_ids=[sid], task_ids=[tid], request_ids=[rid])


# ── когда можно ──────────────────────────────────────────────────────────────

async def test_no_reflection_before_teacher_answer(db, client, reflection_on):
    """Ответа нет — спрашивать не о чем: can_reflect=false, 409 no_teacher_answer."""
    sid, token = await _user(db, "t999 ученик", with_session=True)
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid)
    await db.commit()
    try:
        assert (await _state(client, token, tid, sid))["can_reflect"] is False
        resp = await _reflect(client, token, rid, {})
        assert resp.status_code == 409, resp.text
        assert resp.json()["payload"]["code"] == "no_teacher_answer"
    finally:
        await _cleanup_all(db, user_ids=[sid], task_ids=[tid], request_ids=[rid])


async def test_reflection_after_text_reply_saved_once(db, client, reflection_on):
    """Обычный ответ: рефлексия сохраняется, «не понял» не эскалирует, повтор — 409."""
    sid, token = await _user(db, "t999 ученик", with_session=True)
    teacher_id, _ = await _user(db, "t999 учитель")
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, assigned_teacher_id=teacher_id)
    await db.commit()
    try:
        await _teacher_reply(client, rid, teacher_id)
        state = await _state(client, token, tid, sid)
        assert state["status"] == "closed", "ответ закрывает manual_help (tsk-303)"
        assert state["can_reflect"] is True, "рефлексия доступна и по закрытой заявке"

        resp = await _reflect(
            client, token, rid,
            {"main": "  формула площади  ", "unclear": "откуда берётся 1/2", "understood": False},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["escalated"] is False and body["status"] == "closed"

        row = await _reflection_row(db, rid)
        assert row["main"] == "формула площади", "крайние пробелы срезаются"
        assert row["unclear"] == "откуда берётся 1/2"
        assert row["understood"] is False
        assert row["at"] is not None
        assert row["review_understood"] is None, "развилка разбора не тронута"
        assert row["escalated_at"] is None, "для обычной заявки авто-эскалации нет"

        state = await _state(client, token, tid, sid)
        assert state["can_reflect"] is False and state["reflection_at"] is not None

        again = await _reflect(client, token, rid, {"main": "ещё"})
        assert again.status_code == 409, again.text
        assert again.json()["payload"]["code"] == "already_reflected"
    finally:
        await _cleanup_all(db, user_ids=[sid, teacher_id], task_ids=[tid], request_ids=[rid])


async def test_skip_with_empty_body_marks_reflection(db, client, reflection_on):
    """«Пропустить»: пустое тело принимается, больше не спрашиваем."""
    sid, token = await _user(db, "t999 ученик", with_session=True)
    teacher_id, _ = await _user(db, "t999 учитель")
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, assigned_teacher_id=teacher_id)
    await db.commit()
    try:
        await _teacher_reply(client, rid, teacher_id)
        resp = await _reflect(client, token, rid, {"main": "   ", "unclear": None})
        assert resp.status_code == 200, resp.text
        row = await _reflection_row(db, rid)
        assert row["main"] is None and row["unclear"] is None and row["understood"] is None
        assert row["at"] is not None
        assert (await _state(client, token, tid, sid))["can_reflect"] is False
    finally:
        await _cleanup_all(db, user_ids=[sid, teacher_id], task_ids=[tid], request_ids=[rid])


async def test_reopened_request_waits_for_new_answer(db, client, reflection_on):
    """После возврата старый ответ уже отвергнут — спрашиваем после нового."""
    sid, token = await _user(db, "t999 ученик", with_session=True)
    teacher_id, _ = await _user(db, "t999 учитель")
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, assigned_teacher_id=teacher_id)
    await db.commit()
    try:
        await _teacher_reply(client, rid, teacher_id)
        reopen = await client.post(
            f"/api/v1/learning/help-requests/{rid}/reopen", headers=_bearer(token)
        )
        assert reopen.status_code == 200, reopen.text
        # Тест идёт в одной общей транзакции — `now()` у всех строк одинаков.
        # Разносим события по времени, как они разнесены в жизни.
        await db.execute(
            text("UPDATE help_request_replies SET created_at = now() - interval '2 hours' "
                 "WHERE request_id = :r"), {"r": rid},
        )
        await db.execute(
            text("UPDATE help_request_reopens SET reopened_at = now() - interval '1 hour' "
                 "WHERE request_id = :r"), {"r": rid},
        )
        await db.commit()
        assert (await _state(client, token, tid, sid))["can_reflect"] is False

        await _teacher_reply(client, rid, teacher_id, "Попробуй иначе")
        assert (await _state(client, token, tid, sid))["can_reflect"] is True
    finally:
        await _cleanup_all(db, user_ids=[sid, teacher_id], task_ids=[tid], request_ids=[rid])


async def test_foreign_request_forbidden(db, client, reflection_on):
    """Чужая заявка — 403, рефлексия не записана."""
    sid, token = await _user(db, "t999 ученик", with_session=True)
    other_id, _ = await _user(db, "t999 чужой")
    teacher_id, _ = await _user(db, "t999 учитель")
    tid = await _task(db)
    rid = await _help_request(db, student_id=other_id, task_id=tid, assigned_teacher_id=teacher_id)
    await db.commit()
    try:
        await _teacher_reply(client, rid, teacher_id)
        resp = await _reflect(client, token, rid, {"main": "x"})
        assert resp.status_code == 403, resp.text
        assert (await _reflection_row(db, rid))["at"] is None
    finally:
        await _cleanup_all(
            db, user_ids=[sid, other_id, teacher_id], task_ids=[tid], request_ids=[rid]
        )


async def test_field_longer_than_500_rejected(db, client, reflection_on):
    """Потолок длины — 500 символов на поле (422)."""
    sid, token = await _user(db, "t999 ученик", with_session=True)
    teacher_id, _ = await _user(db, "t999 учитель")
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, assigned_teacher_id=teacher_id)
    await db.commit()
    try:
        await _teacher_reply(client, rid, teacher_id)
        resp = await _reflect(client, token, rid, {"unclear": "я" * 501})
        assert resp.status_code == 422, resp.text
        ok = await _reflect(client, token, rid, {"unclear": "я" * 500})
        assert ok.status_code == 200, ok.text
    finally:
        await _cleanup_all(db, user_ids=[sid, teacher_id], task_ids=[tid], request_ids=[rid])


async def test_blocked_limit_is_not_reflectable(db, client, reflection_on):
    """Заявка «лимит попыток» — не вопрос по существу: 409 wrong_type."""
    sid, token = await _user(db, "t999 ученик", with_session=True)
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, request_type="blocked_limit")
    await db.commit()
    try:
        resp = await _reflect(client, token, rid, {})
        assert resp.status_code == 409, resp.text
        assert resp.json()["payload"]["code"] == "wrong_type"
    finally:
        await _cleanup_all(db, user_ids=[sid], task_ids=[tid], request_ids=[rid])


# ── индивидуальный разбор: развилка rate_individual_review ───────────────────

async def _review_with_link(db, sid: int, tid: int) -> int:
    rid = await _help_request(db, student_id=sid, task_id=tid, request_type="individual_review")
    await db.execute(
        text("UPDATE help_requests SET webinar_link = :l WHERE id = :r"), {"l": _WEBINAR, "r": rid}
    )
    return rid


async def test_review_link_opens_reflection_and_understood_closes(db, client, reflection_on):
    """Ссылка на разбор — тоже ответ; «понятно» закрывает заявку, как rate-review."""
    sid, token = await _user(db, "t999 ученик", with_session=True)
    tid = await _task(db)
    rid = await _review_with_link(db, sid, tid)
    await db.commit()
    try:
        assert (await _state(client, token, tid, sid))["can_reflect"] is True
        resp = await _reflect(client, token, rid, {"main": "разобрали пример", "understood": True})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "closed"
        row = await _reflection_row(db, rid)
        assert row["status"] == "closed"
        assert row["review_understood"] is True, "оценка разбора записана той же логикой"
        assert row["main"] == "разобрали пример"
    finally:
        await _cleanup_all(db, user_ids=[sid], task_ids=[tid], request_ids=[rid])


async def test_review_not_understood_escalates_to_methodist(db, client, reflection_on):
    """«Не понял» после разбора по-прежнему уводит заявку методисту."""
    sid, token = await _user(db, "t999 ученик", with_session=True)
    m_id, m_token = await _user(db, "t999 методист", role="methodist", with_session=True)
    tid = await _task(db)
    rid = await _review_with_link(db, sid, tid)
    await db.commit()
    try:
        resp = await _reflect(client, token, rid, {"unclear": "всё ещё не ясно", "understood": False})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["escalated"] is True and body["status"] == "open"
        row = await _reflection_row(db, rid)
        assert row["escalated_at"] is not None and row["review_understood"] is False

        queue = await client.get("/api/v1/methodist/escalations/pending", headers=_bearer(m_token))
        assert queue.status_code == 200, queue.text
        assert any(
            i["kind"] == "help_request_escalated" and i["payload"].get("request_id") == rid
            for i in queue.json()["items"]
        ), "эскалация видна методисту"

        state = await _state(client, token, tid, sid)
        assert state["can_reflect"] is False and state["can_rate_review"] is False
    finally:
        await _cleanup_all(db, user_ids=[sid, m_id], task_ids=[tid], request_ids=[rid])


async def test_review_skip_leaves_old_rating_available(db, client, reflection_on):
    """Пропуск рефлексии по разбору не оценивает его: прежняя оценка остаётся."""
    sid, token = await _user(db, "t999 ученик", with_session=True)
    tid = await _task(db)
    rid = await _review_with_link(db, sid, tid)
    await db.commit()
    try:
        resp = await _reflect(client, token, rid, {})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "open"
        state = await _state(client, token, tid, sid)
        assert state["can_reflect"] is False
        assert state["can_rate_review"] is True
    finally:
        await _cleanup_all(db, user_ids=[sid], task_ids=[tid], request_ids=[rid])


async def test_already_rated_review_not_asked_again(db, client, reflection_on):
    """Разбор, оценённый старыми кнопками, рефлексию не предлагает."""
    sid, token = await _user(db, "t999 ученик", with_session=True)
    tid = await _task(db)
    rid = await _review_with_link(db, sid, tid)
    await db.commit()
    try:
        rate = await client.post(
            f"/api/v1/learning/help-requests/{rid}/rate-review",
            json={"understood": False}, headers=_bearer(token),
        )
        assert rate.status_code == 200, rate.text
        assert (await _state(client, token, tid, sid))["can_reflect"] is False
        resp = await _reflect(client, token, rid, {"understood": True})
        assert resp.status_code == 409, resp.text
        assert resp.json()["payload"]["code"] == "already_rated"
    finally:
        await _cleanup_all(db, user_ids=[sid], task_ids=[tid], request_ids=[rid])


# ── карточка преподавателя ───────────────────────────────────────────────────

async def test_teacher_card_shows_reflection(db, client, reflection_on):
    """Преподаватель видит рефлексию ученика в карточке заявки."""
    sid, token = await _user(db, "t999 ученик", with_session=True)
    teacher_id, _ = await _user(db, "t999 учитель")
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, assigned_teacher_id=teacher_id)
    await db.commit()
    try:
        await _teacher_reply(client, rid, teacher_id)
        card = await client.get(
            f"/api/v1/teacher/help-requests/{rid}?teacher_id={teacher_id}", headers=_headers()
        )
        assert card.status_code == 200, card.text
        assert card.json()["reflection_at"] is None

        text_with_tags = "<b>главное</b> — формула"
        resp = await _reflect(client, token, rid, {"main": text_with_tags, "understood": True})
        assert resp.status_code == 200, resp.text

        card = await client.get(
            f"/api/v1/teacher/help-requests/{rid}?teacher_id={teacher_id}", headers=_headers()
        )
        body = card.json()
        assert body["reflection_main"] == text_with_tags, "текст хранится как есть, экранирует клиент"
        assert body["reflection_unclear"] is None
        assert body["reflection_understood"] is True
        assert body["reflection_at"] is not None
    finally:
        await _cleanup_all(db, user_ids=[sid, teacher_id], task_ids=[tid], request_ids=[rid])


async def test_reflection_again_after_reopen_and_review_link(db, client, reflection_on):
    """Основной путь к разбору: рефлексия → возврат → разбор → ссылка → снова форма.

    Возврат начинает новый круг: ответ на разбор заслуживает своего вопроса,
    и форма разбора (с развилкой к методисту) должна быть достижима.
    """
    sid, token = await _user(db, "t999 ученик", with_session=True)
    m_id, _ = await _user(db, "t999 методист", role="methodist")
    teacher_id, _ = await _user(db, "t999 учитель")
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, assigned_teacher_id=teacher_id)
    await db.commit()
    try:
        await _teacher_reply(client, rid, teacher_id)
        first = await _reflect(client, token, rid, {"main": "первое", "understood": False})
        assert first.status_code == 200, first.text
        # Общая транзакция теста: разносим время, как в жизни.
        await db.execute(
            text("UPDATE help_requests SET reflection_at = now() - interval '2 hours' "
                 "WHERE id = :r"), {"r": rid},
        )
        await db.execute(
            text("UPDATE help_request_replies SET created_at = now() - interval '3 hours' "
                 "WHERE request_id = :r"), {"r": rid},
        )
        await db.commit()

        reopen = await client.post(
            f"/api/v1/learning/help-requests/{rid}/reopen", headers=_bearer(token)
        )
        assert reopen.status_code == 200, reopen.text
        review = await client.post(
            f"/api/v1/learning/help-requests/{rid}/request-individual-review",
            headers=_bearer(token),
        )
        assert review.status_code == 200, review.text

        state = await _state(client, token, tid, sid)
        assert state["can_reflect"] is False, "разбор без ссылки — ответа ещё нет"

        # Текст преподавателя по разбору («пришлю ссылку») — ещё не ответ.
        await _teacher_reply(client, rid, teacher_id, "Скоро пришлю ссылку")
        assert (await _state(client, token, tid, sid))["can_reflect"] is False

        await db.execute(
            text("UPDATE help_requests SET webinar_link = :l WHERE id = :r"),
            {"l": _WEBINAR, "r": rid},
        )
        await db.commit()
        state = await _state(client, token, tid, sid)
        assert state["can_reflect"] is True, "после ссылки на разбор — новая рефлексия"

        second = await _reflect(client, token, rid, {"unclear": "всё ещё", "understood": False})
        assert second.status_code == 200, second.text
        assert second.json()["escalated"] is True
        row = await _reflection_row(db, rid)
        assert row["main"] is None and row["unclear"] == "всё ещё", "поля — от последнего круга"
        assert row["escalated_at"] is not None
    finally:
        await _cleanup_all(
            db, user_ids=[sid, m_id, teacher_id], task_ids=[tid], request_ids=[rid]
        )


async def test_service_key_acts_for_request_owner(db, client, reflection_on):
    """Сервисный ключ (бот) действует от имени владельца заявки."""
    sid, _ = await _user(db, "t999 ученик")
    teacher_id, _ = await _user(db, "t999 учитель")
    tid = await _task(db)
    rid = await _help_request(db, student_id=sid, task_id=tid, assigned_teacher_id=teacher_id)
    await db.commit()
    try:
        await _teacher_reply(client, rid, teacher_id)
        resp = await client.post(
            f"/api/v1/learning/help-requests/{rid}/reflection",
            json={"unclear": "про знак"}, headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        card = await client.get(
            f"/api/v1/teacher/help-requests/{rid}?teacher_id={teacher_id}", headers=_headers()
        )
        assert card.json()["reflection_unclear"] == "про знак"
    finally:
        await _cleanup_all(db, user_ids=[sid, teacher_id], task_ids=[tid], request_ids=[rid])
