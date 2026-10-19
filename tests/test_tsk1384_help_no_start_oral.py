"""tsk-1384: «Не понимаю, с чего начать» (А) и устная просьба о помощи (Б).

А. Кнопка ученика `stuck_reason='no_start'` — основание гейта П4 наравне с
заметкой ≥20 символов: заявка и новый разговор с наставником открываются без
сдачи и без заметки; преподаватель видит причину в списке и карточке.

Б. Преподаватель записывает устную просьбу ученика (`origin='oral'`):
- права — как у ответа на заявку, доступ к ученику — ACL чтения заявок;
- сразу закрытая — без срока: не «просрочено» и не в метрике реакции, без
  рефлексии П10 у ученика;
- открытая — срок как у обычной;
- попадает в историю заявок и в недельный разбор трудных заданий.
"""
from __future__ import annotations

import importlib.util
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from app.services import help_attempt_gate, help_requests_service
from app.services import entitlements_service as ent
from app.services.ai_tutor import session_service
from app.services.ai_tutor.prompt import STUDENT_DATA_OPEN
from app.services.help_requests_service import (
    get_student_help_request,
    list_help_requests,
)
from app.services.task_history_service import _load_help_requests
from app.services.teacher_dashboard_service import get_teacher_dashboard
from tests.test_tsk993_help_attempt_gate import (
    _bearer,
    _course,
    _link_teacher,
    _request_help,
    _task,
    _user,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def gate_on(monkeypatch):
    """Рубильник П4 включён, подписной гейт выключен."""
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")
    monkeypatch.setattr(help_attempt_gate.settings, "help_requires_attempt", True)


async def _row(db, request_id: int):
    return (
        await db.execute(
            text(
                "SELECT status, stuck_reason, stuck_note, origin, due_at, closed_at, "
                "       closed_by, resolution_comment, message, assigned_teacher_id, "
                "       created_at "
                "FROM help_requests WHERE id = :id"
            ),
            {"id": request_id},
        )
    ).fetchone()


async def _oral(client, token: str, **body):
    return await client.post(
        "/api/v1/teacher/help-requests/oral", headers=_bearer(token), json=body
    )


# ───────────────────── А. «Не понимаю, с чего начать» ────────────────────────


async def test_no_start_opens_request_without_attempt_and_note(db, client, gate_on) -> None:
    student_id, s_token = await _user(db, "s")
    teacher_id, t_token = await _user(db, "t")
    await _link_teacher(db, student_id, teacher_id)
    task_id = await _task(db, await _course(db))

    resp = await client.post(
        f"/api/v1/learning/tasks/{task_id}/request-help",
        headers=_bearer(s_token),
        json={"student_id": student_id, "stuck_reason": "no_start"},
    )
    assert resp.status_code == 200, resp.text
    request_id = resp.json()["request_id"]

    row = await _row(db, request_id)
    assert row.stuck_reason == "no_start"
    assert row.stuck_note is None
    assert row.origin is None
    assert row.status == "open"
    assert row.due_at is not None, "заявка ученика — со сроком П6"

    detail = await client.get(
        f"/api/v1/teacher/help-requests/{request_id}?teacher_id={teacher_id}",
        headers=_bearer(t_token),
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["stuck_reason"] == "no_start"

    lst = await client.get(
        f"/api/v1/teacher/help-requests?teacher_id={teacher_id}", headers=_bearer(t_token)
    )
    item = next(i for i in lst.json()["items"] if i["request_id"] == request_id)
    assert item["stuck_reason"] == "no_start"

    # Уведомление преподавателю — с меткой, а не «запросил помощь» без смысла.
    content = (
        await db.execute(
            text(
                "SELECT content FROM notifications WHERE user_id = :t "
                "AND kind = 'help_request_opened' ORDER BY id DESC LIMIT 1"
            ),
            {"t": teacher_id},
        )
    ).scalar()
    assert content is not None and "с чего начать" in content


async def test_note_reason_is_stored(db, client, gate_on) -> None:
    """Обычная заметка ≥20 символов — `stuck_reason='note'`."""
    student_id, token = await _user(db, "s")
    task_id = await _task(db, await _course(db))
    resp = await _request_help(
        client, token, student_id, task_id,
        stuck_note="Не понимаю, как перевести число в двоичную систему",
    )
    assert resp.status_code == 200, resp.text
    assert (await _row(db, resp.json()["request_id"])).stuck_reason == "note"


async def test_other_reason_does_not_open_gate(db, client, gate_on) -> None:
    """`note` без заметки гейт не снимает — снимает только `no_start`."""
    student_id, token = await _user(db, "s")
    task_id = await _task(db, await _course(db))
    resp = await _request_help(client, token, student_id, task_id, stuck_reason="note")
    assert resp.status_code == 409, resp.text
    assert resp.json()["payload"]["code"] == "attempt_required"


async def test_unknown_reason_is_422(db, client, gate_on) -> None:
    student_id, token = await _user(db, "s")
    task_id = await _task(db, await _course(db))
    resp = await _request_help(client, token, student_id, task_id, stuck_reason="lazy")
    assert resp.status_code == 422, resp.text


async def test_no_start_opens_tutor_session(db, client, gate_on) -> None:
    student_id, token = await _user(db, "s")
    task_id = await _task(db, await _course(db))

    resp = await client.get(
        f"/api/v1/ai-tutor/tasks/{task_id}",
        params={"stuck_reason": "no_start"},
        headers=_bearer(token),
    )
    assert resp.status_code == 200, resp.text

    session, created = await session_service.get_or_create(
        db, student_id=student_id, task_id=task_id
    )
    assert created is False
    assert session.stuck_note == "Не понимаю, с чего начать"
    messages = await session_service.build_llm_messages(db, session, None)
    opening = messages[-1].content
    assert "с чего начать" in opening
    assert STUDENT_DATA_OPEN in opening


async def test_db_rejects_unknown_reason_and_origin(db) -> None:
    student_id, _ = await _user(db, "s")
    task_id = await _task(db, await _course(db))
    for column in ("stuck_reason", "origin"):
        with pytest.raises(Exception):
            async with db.begin_nested():
                await db.execute(
                    text(
                        f"INSERT INTO help_requests (student_id, task_id, {column}) "  # nosec B608
                        "VALUES (:s, :t, 'bogus')"
                    ),
                    {"s": student_id, "t": task_id},
                )


# ───────────────────── Б. устная просьба преподавателю ───────────────────────


async def test_oral_closed_by_default(db, client, gate_on, monkeypatch) -> None:
    """Сразу закрыта, без срока; гейт П4 не действует; без рефлексии П10."""
    monkeypatch.setattr(help_requests_service._settings, "help_reflection_enabled", True)
    student_id, _ = await _user(db, "s")
    teacher_id, t_token = await _user(db, "t")
    await _link_teacher(db, student_id, teacher_id)
    task_id = await _task(db, await _course(db))

    resp = await _oral(
        client, t_token,
        teacher_id=teacher_id, student_id=student_id, task_id=task_id,
        note="  Разобрали, с чего начать перевод  ",
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "closed" and body["origin"] == "oral"

    row = await _row(db, body["request_id"])
    assert row.origin == "oral"
    assert row.status == "closed"
    assert row.due_at is None
    assert row.closed_at is not None and row.closed_by == teacher_id
    assert row.resolution_comment == "Разобрали, с чего начать перевод"
    assert row.message is None, "вопроса ученик не писал — заметка не «его вопрос»"
    assert row.assigned_teacher_id == teacher_id

    # Ни рефлексии П10, ни лестницы: на экране задания ученика запись не
    # становится «его заявкой» (ответа нет — спрашивать не о чем).
    assert await get_student_help_request(db, student_id, task_id) is None

    # Ученику не шлём «ваш вопрос закрыт» — он и так получил помощь лично.
    sent = (
        await db.execute(
            text("SELECT COUNT(*) FROM notifications WHERE user_id = :s"), {"s": student_id}
        )
    ).scalar()
    assert sent == 0


async def test_oral_open_has_regular_due(db, client) -> None:
    student_id, _ = await _user(db, "s")
    teacher_id, t_token = await _user(db, "t")
    await _link_teacher(db, student_id, teacher_id)
    task_id = await _task(db, await _course(db))

    resp = await _oral(
        client, t_token,
        teacher_id=teacher_id, student_id=student_id, task_id=task_id,
        note="Просил разобрать после урока", close=False,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "open"
    row = await _row(db, resp.json()["request_id"])
    assert row.status == "open" and row.origin == "oral"
    assert row.message == "Просил разобрать после урока"
    assert row.due_at is not None
    sla = timedelta(hours=help_requests_service.help_sla_hours())
    assert abs((row.due_at - row.created_at) - sla) < timedelta(seconds=1)


async def test_oral_foreign_student_is_403(db, client) -> None:
    student_id, _ = await _user(db, "s")
    teacher_id, _ = await _user(db, "t")
    stranger_id, stranger_token = await _user(db, "x")
    await _link_teacher(db, student_id, teacher_id)
    task_id = await _task(db, await _course(db))

    resp = await _oral(
        client, stranger_token,
        teacher_id=stranger_id, student_id=student_id, task_id=task_id,
    )
    assert resp.status_code == 403, resp.text
    count = (
        await db.execute(
            text("SELECT COUNT(*) FROM help_requests WHERE student_id = :s"), {"s": student_id}
        )
    ).scalar()
    assert count == 0


async def test_oral_on_behalf_of_other_teacher_is_403(db, client) -> None:
    """Записать можно только от своего имени (как ответ на заявку)."""
    student_id, _ = await _user(db, "s")
    teacher_id, _ = await _user(db, "t")
    other_id, other_token = await _user(db, "o")
    await _link_teacher(db, student_id, teacher_id)
    task_id = await _task(db, await _course(db))
    resp = await _oral(
        client, other_token,
        teacher_id=teacher_id, student_id=student_id, task_id=task_id,
    )
    assert resp.status_code == 403, resp.text


async def test_oral_missing_task_is_404(db, client) -> None:
    student_id, _ = await _user(db, "s")
    teacher_id, t_token = await _user(db, "t")
    await _link_teacher(db, student_id, teacher_id)
    resp = await _oral(
        client, t_token, teacher_id=teacher_id, student_id=student_id, task_id=999999999
    )
    assert resp.status_code == 404, resp.text


async def test_oral_closed_not_overdue_and_listed_with_label(db, client) -> None:
    """Закрытая устная: не в «просрочено», в списке закрытых — с меткой oral."""
    student_id, _ = await _user(db, "s")
    teacher_id, t_token = await _user(db, "t")
    await _link_teacher(db, student_id, teacher_id)
    task_id = await _task(db, await _course(db))
    resp = await _oral(
        client, t_token, teacher_id=teacher_id, student_id=student_id, task_id=task_id
    )
    request_id = resp.json()["request_id"]
    # Даже «старая» закрытая устная не становится просроченной.
    await db.execute(
        text(
            "UPDATE help_requests SET created_at = now() - interval '3 days', "
            "closed_at = now() - interval '3 days' WHERE id = :id"
        ),
        {"id": request_id},
    )
    await db.commit()

    overdue, _ = await list_help_requests(
        db, teacher_id, status_filter="all", overdue=True, student_id=student_id
    )
    assert request_id not in [i["request_id"] for i in overdue]

    closed, _ = await list_help_requests(
        db, teacher_id, status_filter="closed", student_id=student_id
    )
    item = next(i for i in closed if i["request_id"] == request_id)
    assert item["origin"] == "oral" and item["is_overdue"] is False

    detail = await client.get(
        f"/api/v1/teacher/help-requests/{request_id}?teacher_id={teacher_id}",
        headers=_bearer(t_token),
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["origin"] == "oral"


async def test_oral_closed_excluded_from_reaction_metric(db, client) -> None:
    """В дашборде: заявка посчитана и обработана «голосом», но без реакции."""
    student_id, _ = await _user(db, "s")
    teacher_id, t_token = await _user(db, "t")
    await _link_teacher(db, student_id, teacher_id)
    task_id = await _task(db, await _course(db))
    resp = await _oral(
        client, t_token, teacher_id=teacher_id, student_id=student_id, task_id=task_id
    )
    assert resp.status_code == 201, resp.text

    today = datetime.now(timezone.utc).date()
    items = await get_teacher_dashboard(
        db, date_from=today - timedelta(days=1), date_to=today + timedelta(days=1)
    )
    row = next(i for i in items if i["teacher_id"] == teacher_id)
    sides = [row["in_lesson"], row["off_lesson"]]
    assert sum(s["requests"] for s in sides) == 1
    assert sum(s["processed"] for s in sides) == 1
    assert sum(s["by_kind"]["voice"] for s in sides) == 1
    assert sum(s["reacted"] for s in sides) == 0, "устная закрытая — не реакция"


async def test_regular_request_reaction_still_counted(db, client) -> None:
    """Контроль: обычная заявка, закрытая без ответа, остаётся реакцией."""
    student_id, _ = await _user(db, "s")
    teacher_id, _ = await _user(db, "t")
    await _link_teacher(db, student_id, teacher_id)
    task_id = await _task(db, await _course(db))
    rid = (
        await db.execute(
            text(
                "INSERT INTO help_requests (status, student_id, task_id, request_type, "
                "assigned_teacher_id, due_at) VALUES ('open', :s, :t, 'manual_help', :tt, "
                "now() + interval '1 day') RETURNING id"
            ),
            {"s": student_id, "t": task_id, "tt": teacher_id},
        )
    ).scalar_one()
    await help_requests_service.close_help_request(db, rid, teacher_id)
    await db.commit()
    today = datetime.now(timezone.utc).date()
    items = await get_teacher_dashboard(
        db, date_from=today - timedelta(days=1), date_to=today + timedelta(days=1)
    )
    row = next(i for i in items if i["teacher_id"] == teacher_id)
    assert row["in_lesson"]["reacted"] + row["off_lesson"]["reacted"] == 1


async def test_oral_in_task_history(db, client) -> None:
    student_id, _ = await _user(db, "s")
    teacher_id, t_token = await _user(db, "t")
    await _link_teacher(db, student_id, teacher_id)
    task_id = await _task(db, await _course(db))
    resp = await _oral(
        client, t_token, teacher_id=teacher_id, student_id=student_id, task_id=task_id,
        note="Разобрали первый шаг",
    )
    history = await _load_help_requests(db, user_id=student_id, task_id=task_id)
    assert len(history) == 1
    assert history[0]["request_id"] == resp.json()["request_id"]
    assert history[0]["origin"] == "oral"
    assert history[0]["resolution_comment"] == "Разобрали первый шаг"


def _weekly_signals_sql() -> str:
    """SQL недельного разбора (`scripts/weekly_hard_tasks.py`) в синтаксисе bind."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "weekly_hard_tasks.py"
    spec = importlib.util.spec_from_file_location("weekly_hard_tasks_tsk1384", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return re.sub(r"%\((\w+)\)s", r":\1", mod.SELECT_SIGNALS)


async def test_oral_counts_in_weekly_hard_tasks(db, client) -> None:
    student_id, _ = await _user(db, "s")
    teacher_id, t_token = await _user(db, "t")
    await _link_teacher(db, student_id, teacher_id)
    task_id = await _task(db, await _course(db))
    resp = await _oral(
        client, t_token, teacher_id=teacher_id, student_id=student_id, task_id=task_id
    )
    assert resp.status_code == 201, resp.text

    rows = (
        await db.execute(text(_weekly_signals_sql()), {"days": "7", "min_failed": 2})
    ).mappings().fetchall()
    row = next(r for r in rows if r["task_id"] == task_id)
    assert row["help_requests"] == 1



async def test_oral_closed_does_not_replace_student_own_request(db, client, gate_on) -> None:
    """Ревью MAJOR-1: своя открытая заявка остаётся «текущей» на экране задания,
    устная закрытая не считается повторным обращением (лестница tsk-303)."""
    student_id, s_token = await _user(db, "s")
    teacher_id, t_token = await _user(db, "t")
    await _link_teacher(db, student_id, teacher_id)
    task_id = await _task(db, await _course(db))

    own = await client.post(
        f"/api/v1/learning/tasks/{task_id}/request-help",
        headers=_bearer(s_token),
        json={"student_id": student_id, "stuck_reason": "no_start"},
    )
    assert own.status_code == 200, own.text
    own_id = own.json()["request_id"]

    oral = await _oral(
        client, t_token, teacher_id=teacher_id, student_id=student_id, task_id=task_id
    )
    assert oral.status_code == 201, oral.text

    view = await get_student_help_request(db, student_id, task_id)
    assert view is not None and view["request_id"] == own_id
    assert view["status"] == "open"
    repeat = await help_requests_service._repeat_ask_count(db, own_id, student_id, task_id)
    assert repeat == 0


async def test_oral_closed_alone_not_shown_as_student_request(db, client) -> None:
    """Ученик не подавал заявку — «Всё равно не понял» по чужой записи не предлагаем."""
    student_id, _ = await _user(db, "s")
    teacher_id, t_token = await _user(db, "t")
    await _link_teacher(db, student_id, teacher_id)
    task_id = await _task(db, await _course(db))
    resp = await _oral(
        client, t_token, teacher_id=teacher_id, student_id=student_id, task_id=task_id
    )
    assert resp.status_code == 201, resp.text
    assert await get_student_help_request(db, student_id, task_id) is None


async def test_oral_open_conflicts_with_existing_open_request(db, client, gate_on) -> None:
    """Ревью MINOR-4: вторая открытая заявка по той же паре — 409 с id первой."""
    student_id, s_token = await _user(db, "s")
    teacher_id, t_token = await _user(db, "t")
    await _link_teacher(db, student_id, teacher_id)
    task_id = await _task(db, await _course(db))
    own = await client.post(
        f"/api/v1/learning/tasks/{task_id}/request-help",
        headers=_bearer(s_token),
        json={"student_id": student_id, "stuck_reason": "no_start"},
    )
    own_id = own.json()["request_id"]

    resp = await _oral(
        client, t_token, teacher_id=teacher_id, student_id=student_id, task_id=task_id,
        close=False,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["payload"] == {"code": "help_request_open", "request_id": own_id}

    # Закрытую запись факта при этом сделать можно.
    closed = await _oral(
        client, t_token, teacher_id=teacher_id, student_id=student_id, task_id=task_id
    )
    assert closed.status_code == 201, closed.text
