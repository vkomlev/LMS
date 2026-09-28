"""tsk-1147: способ помощи у ответа и дашборд преподавателей у методиста.

Проверяется:
* догадка о способе помощи и её двойник в миграции (одни и те же примеры
  прогоняются через Python и через регулярки PostgreSQL — иначе история
  размечена одним правилом, а новые ответы другим);
* ответ пишет выбранный тип, без выбора — догадку;
* дашборд: «на занятии» по окну занятия ученика, медиана и доля в пороге,
  «голосом» = закрыта без ответа, «мало данных» ниже порога объёма, доступ
  только методисту.

Все заявки — в марте 2020, чтобы период не пересекался с другими данными базы.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.services.help_reply_kind import guess_reply_kind
from app.services.help_requests_service import close_help_request, reply_help_request
from tests.test_feedback_and_kpi_tsk303 import _bearer, _task, _user

pytestmark = pytest.mark.asyncio

_MSK = ZoneInfo("Europe/Moscow")
_LESSON_START = datetime(2020, 3, 10, 10, 0, tzinfo=_MSK)

_SAMPLES = [
    ("Заходи https://telemost.yandex.ru/j/123456", "telemost"),
    ("Разбор: https://www.youtube.com/watch?v=abc и встреча https://telemost.yandex.ru/j/1", "telemost"),
    ("Смотри запись https://youtu.be/xyz", "video"),
    ("https://rutube.ru/video/1/", "video"),
    ("https://disk.yandex.ru/i/abc", "video"),
    ("файл razbor.mp4 в чате", "video"),
    ("Проверь условие цикла, там off-by-one", "text"),
    ("Смотри https://docs.python.org/3/", "text"),
]


def _load_migration():
    """Модуль миграции tsk-1147 (имя файла начинается с даты — импорт по пути)."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "app/db/migrations/versions/2026_09_28_tsk1147_reply_kind.py"
    )
    spec = importlib.util.spec_from_file_location("tsk1147_migration", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("body,kind", _SAMPLES)
async def test_guess_reply_kind(body, kind):
    assert guess_reply_kind(body) == kind


async def test_migration_regex_matches_python_guess(db):
    """Разметка истории (SQL) и догадка сервера (Python) обязаны совпадать."""
    mig = _load_migration()
    for body, kind in _SAMPLES:
        got = (
            await db.execute(
                text(
                    "SELECT CASE WHEN :b ~* :tm THEN 'telemost' "
                    "WHEN :b ~* :vd THEN 'video' ELSE 'text' END"
                ),
                {"b": body, "tm": mig._TELEMOST, "vd": mig._VIDEO},
            )
        ).scalar_one()
        assert got == kind, body


async def _request(db, *, sid: int, task_id: int, teacher_id: int) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO help_requests (status, student_id, task_id, request_type, "
                "assigned_teacher_id, auto_created, context_json, priority, created_at, updated_at) "
                "VALUES ('open', :s, :t, 'manual_help', :at, false, '{}'::jsonb, 100, now(), now()) "
                "RETURNING id"
            ),
            {"s": sid, "t": task_id, "at": teacher_id},
        )
    ).scalar_one()


async def _backdate(db, rid: int, created: datetime, reacted: datetime) -> None:
    """Перенести заявку, её ответ и закрытие в прошлое."""
    await db.execute(
        text("UPDATE help_requests SET created_at=:c, closed_at=:r WHERE id=:id"),
        {"c": created, "r": reacted, "id": rid},
    )
    await db.execute(
        text("UPDATE help_request_replies SET created_at=:r WHERE request_id=:id"),
        {"r": reacted, "id": rid},
    )


async def test_reply_stores_chosen_or_guessed_kind(db):
    sid, _ = await _user(db, "t1147 ученик")
    tid, _ = await _user(db, "t1147 учитель", role="teacher")
    task_id = await _task(db)
    r1 = await _request(db, sid=sid, task_id=task_id, teacher_id=tid)
    r2 = await _request(db, sid=sid, task_id=task_id, teacher_id=tid)
    await db.commit()
    try:
        _, err = await reply_help_request(
            db, r1, tid, "Встреча https://telemost.yandex.ru/j/1"
        )
        assert err is None
        _, err = await reply_help_request(
            db, r2, tid, "Разобрали на занятии", reply_kind="voice"
        )
        assert err is None
        await db.commit()
        kinds = dict(
            (
                await db.execute(
                    text(
                        "SELECT request_id, reply_kind FROM help_request_replies "
                        "WHERE request_id = ANY(:ids)"
                    ),
                    {"ids": [r1, r2]},
                )
            ).fetchall()
        )
        assert kinds == {r1: "telemost", r2: "voice"}
    finally:
        await _cleanup(db, [sid, tid], [task_id], [r1, r2])


async def _cleanup(db, user_ids, task_ids, request_ids, occurrence_ids=()) -> None:
    for rid in request_ids:
        await db.execute(text("DELETE FROM help_request_reopens WHERE request_id=:r"), {"r": rid})
        await db.execute(text("DELETE FROM help_request_replies WHERE request_id=:r"), {"r": rid})
        await db.execute(text("DELETE FROM help_requests WHERE id=:r"), {"r": rid})
    for oid in occurrence_ids:
        await db.execute(
            text("DELETE FROM lesson_occurrence_participant WHERE occurrence_id=:o"), {"o": oid}
        )
        await db.execute(text("DELETE FROM lesson_occurrence WHERE id=:o"), {"o": oid})
    for uid in user_ids:
        await db.execute(
            text("DELETE FROM messages WHERE sender_id=:u OR recipient_id=:u"), {"u": uid}
        )
        await db.execute(text("DELETE FROM notifications WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": uid})
    for tid in task_ids:
        await db.execute(text("DELETE FROM tasks WHERE id=:t"), {"t": tid})
    await db.commit()


async def test_dashboard_numbers(db, client):
    sid, _ = await _user(db, "t1147 ученик")
    tid, t_token = await _user(db, "t1147 учитель", role="teacher", with_session=True)
    mid, m_token = await _user(db, "t1147 методист", role="methodist", with_session=True)
    task_id = await _task(db)
    oid = (
        await db.execute(
            text(
                "INSERT INTO lesson_occurrence (slot_id, teacher_id, scheduled_at, duration_minutes) "
                "VALUES (NULL, :t, :s, 90) RETURNING id"
            ),
            {"t": tid, "s": _LESSON_START},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
            "VALUES (:o, :s, 'completed')"
        ),
        {"o": oid, "s": sid},
    )
    await db.commit()

    rids: list[int] = []
    try:
        created_in = _LESSON_START + timedelta(minutes=5)
        # 9 ответов на занятии: реакция 1..9 мин; тексты дают 6 text, 2 telemost, 1 video.
        bodies = ["подсказка"] * 6 + ["https://telemost.yandex.ru/j/1"] * 2 + ["https://youtu.be/x"]
        for i, body in enumerate(bodies):
            rid = await _request(db, sid=sid, task_id=task_id, teacher_id=tid)
            rids.append(rid)
            _, err = await reply_help_request(db, rid, tid, body)
            assert err is None
            await _backdate(db, rid, created_in, created_in + timedelta(minutes=i + 1))
        # 10-я — закрыта без ответа через 20 мин: «голосом».
        rid = await _request(db, sid=sid, task_id=task_id, teacher_id=tid)
        rids.append(rid)
        await close_help_request(db, rid, tid)
        await _backdate(db, rid, created_in, created_in + timedelta(minutes=20))
        # Одну из заявок на занятии ученик вернул.
        await db.execute(
            text("INSERT INTO help_request_reopens (request_id, teacher_id) VALUES (:r, :t)"),
            {"r": rids[0], "t": tid},
        )
        # 2 заявки вне занятия, реакция 30 и 180 мин.
        created_off = datetime(2020, 3, 11, 12, 0, tzinfo=_MSK)
        for minutes in (30, 180):
            rid = await _request(db, sid=sid, task_id=task_id, teacher_id=tid)
            rids.append(rid)
            _, err = await reply_help_request(db, rid, tid, "подсказка")
            assert err is None
            await _backdate(db, rid, created_off, created_off + timedelta(minutes=minutes))
        await db.commit()

        url = "/api/v1/teacher/help-requests/kpi/dashboard?date_from=2020-03-01&date_to=2020-03-31"
        assert (await client.get(url, headers=_bearer(t_token))).status_code == 403

        resp = await client.get(url, headers=_bearer(m_token))
        assert resp.status_code == 200, resp.text
        row = next(i for i in resp.json()["items"] if i["teacher_id"] == tid)

        assert row["requests"] == 12 and row["reopened_requests"] == 1
        assert row["reopen_rate"] == pytest.approx(1 / 12, abs=1e-4)

        ins = row["in_lesson"]
        assert ins["requests"] == 10 and ins["processed"] == 10
        assert ins["reaction_median_min"] == pytest.approx(5.5, abs=0.1)
        assert ins["within_limit"] == 9 and ins["within_limit_rate"] == pytest.approx(0.9)
        assert ins["by_kind"] == {"text": 6, "voice": 1, "video": 1, "telemost": 2}

        off = row["off_lesson"]
        assert off["requests"] == 2 and off["processed"] == 2
        assert off["reaction_median_min"] is None, "две заявки — мало данных"
        assert off["reopen_rate"] is None
        assert off["within_limit"] == 1 and off["reaction_limit_min"] == 120

        # Период, не задевающий занятие, ничего этому преподавателю не приписывает.
        resp = await client.get(
            "/api/v1/teacher/help-requests/kpi/dashboard?date_from=2020-04-01&date_to=2020-04-30",
            headers=_bearer(m_token),
        )
        row = next(i for i in resp.json()["items"] if i["teacher_id"] == tid)
        assert row["requests"] == 0

        bad = await client.get(
            "/api/v1/teacher/help-requests/kpi/dashboard?date_from=2020-04-30&date_to=2020-04-01",
            headers=_bearer(m_token),
        )
        assert bad.status_code == 422
    finally:
        await _cleanup(db, [sid, tid, mid], [task_id], rids, [oid])


async def test_individual_review_counts_as_telemost(db, client):
    """Разбор закрывается системно и без строки ответа — это консультация в Телемосте."""
    sid, _ = await _user(db, "t1147 ученик ир")
    tid, _ = await _user(db, "t1147 учитель ир", role="teacher")
    mid, m_token = await _user(db, "t1147 методист ир", role="methodist", with_session=True)
    task_id = await _task(db)
    rid = await _request(db, sid=sid, task_id=task_id, teacher_id=tid)
    await db.execute(
        text("UPDATE help_requests SET request_type='individual_review' WHERE id=:r"), {"r": rid}
    )
    await db.commit()
    try:
        await close_help_request(db, rid, None)
        created = datetime(2020, 5, 12, 18, 0, tzinfo=_MSK)
        await _backdate(db, rid, created, created + timedelta(hours=26))
        await db.commit()
        resp = await client.get(
            "/api/v1/teacher/help-requests/kpi/dashboard?date_from=2020-05-01&date_to=2020-05-31",
            headers=_bearer(m_token),
        )
        row = next(i for i in resp.json()["items"] if i["teacher_id"] == tid)
        off = row["off_lesson"]
        assert off["processed"] == 1
        assert off["by_kind"]["telemost"] == 1 and off["by_kind"]["voice"] == 0
        assert off["reacted"] == 0, "системное закрытие — не реакция преподавателя"
    finally:
        await _cleanup(db, [sid, tid, mid], [task_id], [rid])
