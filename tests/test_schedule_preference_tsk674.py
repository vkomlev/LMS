"""tsk-674 фаза 1: пожелания ученика по осеннему расписанию.

Покрывает то, из-за чего опрос вообще может оказаться бесполезным 30 августа:

- проверка на вводе — час вне диапазона, дубль часа, желательных меньше, чем
  занятий в неделю;
- сохранение и правка: часы перезаписываются целиком, история копится;
- аудитория опроса — выпускник (`alumni`) и демо (`demo`) в неё не входят,
  и флаг `schedule_preference_pending` в `GET /me` для них молчит;
- тестовый тариф (`test`) — опрос видит, но в счёт не идёт (tsk-712);
- сводка охвата: заполнившие, молчащие, спрос по часам;
- гейт сводки — методист/админ, ученику она недоступна.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.schemas.schedule_preference import SchedulePreferenceWrite
from app.services import schedule_plan_service, schedule_preference_service
from app.services.auth.session_service import create_session
from app.services.schedule_preference_service import SchedulePreferenceError


# ============================== Helpers ==============================


async def _create_user(db, *, role: str | None = None, prefix: str = "tsk674") -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-user", tg_id=None)
    db.add(u)
    await db.flush()
    if role:
        # У `roles.id` нет последовательности — вставлять роль вслепую нельзя
        # (NOT NULL проверяется раньше ON CONFLICT). Роли сетки уже есть в базе.
        role_id = (
            await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) "
                "ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "r": role_id},
        )
    await db.commit()
    return u.id


async def _assign_plan(db, student_id: int, code: str) -> None:
    """Дать ученику действующий тариф. Планы сетки уже есть в базе (tsk-301)."""
    plan_id = (
        await db.execute(
            text("SELECT id FROM subscription_plan WHERE code = :c"), {"c": code}
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
            "VALUES (:s, :p, CURRENT_DATE)"
        ),
        {"s": student_id, "p": plan_id},
    )
    await db.commit()


def _body(**kwargs) -> SchedulePreferenceWrite:
    payload = {
        "lessons_per_week": 2,
        "hours": [
            {"weekday": 0, "start_time": "17:00", "kind": "preferred"},
            {"weekday": 2, "start_time": "18:00", "kind": "preferred"},
        ],
    }
    payload.update(kwargs)
    return SchedulePreferenceWrite(**payload)


# ============================== Проверка на вводе ==============================


def test_validate_rejects_hour_outside_grid():
    """10:00 в понедельник осенью не существует — именно из-за этого и опрос."""
    with pytest.raises(SchedulePreferenceError, match="вне расписания"):
        schedule_preference_service.validate(
            _body(
                hours=[
                    {"weekday": 0, "start_time": "10:00", "kind": "preferred"},
                    {"weekday": 2, "start_time": "18:00", "kind": "preferred"},
                ]
            )
        )


def test_validate_rejects_friday_and_sunday():
    """Пятницы и воскресенья в сетке нет вовсе."""
    for weekday in (4, 6):
        with pytest.raises(SchedulePreferenceError, match="вне расписания"):
            schedule_preference_service.validate(
                _body(
                    lessons_per_week=1,
                    hours=[{"weekday": weekday, "start_time": "13:00", "kind": "preferred"}],
                )
            )


def test_validate_saturday_grid_edges():
    """Суббота: 09:00 годится, 14:00 — уже нет (последнее занятие 13:00-14:00)."""
    schedule_preference_service.validate(
        _body(lessons_per_week=1, hours=[{"weekday": 5, "start_time": "09:00", "kind": "preferred"}])
    )
    with pytest.raises(SchedulePreferenceError, match="вне расписания"):
        schedule_preference_service.validate(
            _body(
                lessons_per_week=1,
                hours=[{"weekday": 5, "start_time": "14:00", "kind": "preferred"}],
            )
        )


def test_validate_weekday_grid_edges():
    """Будни: 12:00 годится, 19:00 — нет (последнее занятие 18:00-19:00)."""
    schedule_preference_service.validate(
        _body(lessons_per_week=1, hours=[{"weekday": 3, "start_time": "12:00", "kind": "preferred"}])
    )
    with pytest.raises(SchedulePreferenceError, match="вне расписания"):
        schedule_preference_service.validate(
            _body(
                lessons_per_week=1,
                hours=[{"weekday": 3, "start_time": "19:00", "kind": "preferred"}],
            )
        )


def test_validate_rejects_same_hour_twice():
    with pytest.raises(SchedulePreferenceError, match="дважды"):
        schedule_preference_service.validate(
            _body(
                lessons_per_week=1,
                hours=[
                    {"weekday": 0, "start_time": "17:00", "kind": "preferred"},
                    {"weekday": 0, "start_time": "17:00", "kind": "possible"},
                ],
            )
        )


def test_validate_requires_preferred_at_least_lessons_per_week():
    """Главное правило оператора: желательных не меньше, чем занятий в неделю."""
    with pytest.raises(SchedulePreferenceError, match="Желательных часов"):
        schedule_preference_service.validate(
            _body(
                lessons_per_week=3,
                hours=[
                    {"weekday": 0, "start_time": "17:00", "kind": "preferred"},
                    {"weekday": 1, "start_time": "17:00", "kind": "preferred"},
                    {"weekday": 2, "start_time": "17:00", "kind": "possible"},
                ],
            )
        )


def test_validate_allows_zero_possible_hours():
    """Возможных часов может не быть вовсе — это разрешено явно."""
    hours = schedule_preference_service.validate(_body())
    assert [h.kind for h in hours] == ["preferred", "preferred"]


# ============================== Сохранение и история ==============================


@pytest.mark.asyncio
async def test_save_then_edit_keeps_history(db):
    student_id = await _create_user(db, role="student", prefix="tsk674-stud")
    await _assign_plan(db, student_id, "base_legacy")

    await schedule_preference_service.save_preference(
        db, student_id, _body(), changed_by=student_id
    )
    first = await schedule_preference_service.get_preference(db, student_id)
    assert first["is_filled"] is True
    assert first["lessons_per_week"] == 2
    assert len(first["hours"]) == 2

    await schedule_preference_service.save_preference(
        db,
        student_id,
        _body(
            lessons_per_week=1,
            hours=[
                {"weekday": 5, "start_time": "10:00", "kind": "preferred"},
                {"weekday": 3, "start_time": "16:00", "kind": "possible"},
            ],
            comment="по будням только после 16",
        ),
        changed_by=student_id,
    )
    second = await schedule_preference_service.get_preference(db, student_id)
    # Часы перезаписаны целиком, а не дописаны к прежним.
    assert {(h.weekday, h.start_time.hour) for h in second["hours"]} == {(3, 16), (5, 10)}
    assert second["comment"] == "по будням только после 16"

    history = await schedule_preference_service.list_history(db, student_id)
    assert len(history) == 2, "каждое сохранение оставляет снимок"
    assert history[0]["lessons_per_week"] == 1  # свежая правка сверху
    assert history[1]["lessons_per_week"] == 2


@pytest.mark.asyncio
async def test_unfilled_student_gets_defaults(db):
    student_id = await _create_user(db, role="student", prefix="tsk674-empty")
    await _assign_plan(db, student_id, "base_legacy")

    data = await schedule_preference_service.get_preference(db, student_id)
    assert data["is_filled"] is False
    assert data["lessons_per_week"] == 2, "умолчание оператора — 2 занятия"
    assert data["hours"] == []
    assert data["is_audience"] is True
    # Сетка едет вместе с ответом: 4 будних дня + суббота, 33 часа.
    assert sum(len(day["hours"]) for day in data["grid"]) == 33


# ============================== Аудитория опроса ==============================


@pytest.mark.asyncio
async def test_alumni_and_demo_are_not_audience(db):
    alumni_id = await _create_user(db, role="student", prefix="tsk674-alum")
    await _assign_plan(db, alumni_id, "alumni")
    demo_id = await _create_user(db, role="student", prefix="tsk674-demo")
    await _assign_plan(db, demo_id, "demo")
    active_id = await _create_user(db, role="student", prefix="tsk674-act")
    await _assign_plan(db, active_id, "base_legacy")

    assert await schedule_preference_service.is_audience(db, alumni_id) is False
    assert await schedule_preference_service.is_audience(db, demo_id) is False
    assert await schedule_preference_service.is_audience(db, active_id) is True

    assert await schedule_preference_service.is_pending(db, alumni_id) is False
    assert await schedule_preference_service.is_pending(db, active_id) is True


@pytest.mark.asyncio
async def test_test_plan_sees_survey_but_is_not_counted(db, client):
    """tsk-712: тестовым опрос показываем, но в числа они не идут.

    Обе стороны в одном тесте намеренно: разъедься они — и правка выглядела бы
    сделанной. Тестовая учётка должна и видеть плашку, и заполнить анкету, и
    при этом не появиться ни в списке методиста, ни в спросе по часам; живой
    ученик — появиться, как раньше.
    """
    methodist_id = await _create_user(db, role="methodist", prefix="tsk712-meth")
    token, _, _ = await create_session(db, user_id=methodist_id)

    test_id = await _create_user(db, role="student", prefix="tsk712-test")
    await _assign_plan(db, test_id, "test")
    real_id = await _create_user(db, role="student", prefix="tsk712-real")
    await _assign_plan(db, real_id, "base_legacy")

    # Показ: плашка и напоминание тестовой учётке положены.
    assert await schedule_preference_service.is_audience(db, test_id) is True
    assert await schedule_preference_service.is_pending(db, test_id) is True

    hours = [
        {"weekday": 1, "start_time": "13:00", "kind": "preferred"},
        {"weekday": 3, "start_time": "14:00", "kind": "preferred"},
    ]
    for student_id in (test_id, real_id):
        await schedule_preference_service.save_preference(
            db, student_id, _body(lessons_per_week=2, hours=hours), changed_by=student_id
        )

    resp = await client.get(
        "/api/v1/methodist/schedule-preferences/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    ids = {r["student_id"] for r in body["students"]}
    assert real_id in ids, "настоящий ученик считается, как раньше"
    assert test_id not in ids, "тестовая учётка в сводку охвата не попадает"
    assert body["not_counted_total"] >= 1, "отброшенных показываем числом"

    # Спрос по часам: обе анкеты одинаковые, значит на этот час должна быть
    # засчитана ровно одна — от живого ученика.
    demand = {(c["weekday"], c["start_time"]): c for c in body["demand"]}
    assert demand[(1, "13:00:00")]["preferred_count"] == 1

    # Вёрстка берёт ту же счётную аудиторию: тестовому слот не отводится.
    view, plan_input = await schedule_plan_service.load_students(db)
    assert real_id in {r.student_id for r in view}
    assert test_id not in {r.student_id for r in view}
    assert test_id not in {r.student_id for r in plan_input}


@pytest.mark.asyncio
async def test_pending_flag_goes_off_after_save(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk674-flag")
    await _assign_plan(db, student_id, "base_legacy")
    token, _, _ = await create_session(db, user_id=student_id)

    me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["schedule_preference_pending"] is True

    resp = await client.put(
        "/api/v1/me/schedule-preference",
        json={
            "lessons_per_week": 2,
            "hours": [
                {"weekday": 0, "start_time": "17:00:00", "kind": "preferred"},
                {"weekday": 2, "start_time": "18:00:00", "kind": "preferred"},
                {"weekday": 5, "start_time": "09:00:00", "kind": "possible"},
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_filled"] is True

    me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["schedule_preference_pending"] is False


@pytest.mark.asyncio
async def test_api_rejects_hour_outside_grid_with_readable_message(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk674-422")
    await _assign_plan(db, student_id, "base_legacy")
    token, _, _ = await create_session(db, user_id=student_id)

    resp = await client.put(
        "/api/v1/me/schedule-preference",
        json={
            "lessons_per_week": 2,
            "hours": [
                {"weekday": 0, "start_time": "11:00:00", "kind": "preferred"},
                {"weekday": 2, "start_time": "18:00:00", "kind": "preferred"},
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
    assert "вне расписания" in resp.json()["detail"]


# ============================== Сводка охвата ==============================


@pytest.mark.asyncio
async def test_summary_counts_and_demand(db, client):
    methodist_id = await _create_user(db, role="methodist", prefix="tsk674-meth")
    token, _, _ = await create_session(db, user_id=methodist_id)

    filled_id = await _create_user(db, role="student", prefix="tsk674-sum-filled")
    await _assign_plan(db, filled_id, "base_legacy")
    silent_id = await _create_user(db, role="student", prefix="tsk674-sum-silent")
    await _assign_plan(db, silent_id, "base_legacy")

    await schedule_preference_service.save_preference(
        db,
        filled_id,
        _body(
            lessons_per_week=2,
            hours=[
                {"weekday": 0, "start_time": "17:00", "kind": "preferred"},
                {"weekday": 2, "start_time": "18:00", "kind": "preferred"},
                {"weekday": 5, "start_time": "09:00", "kind": "possible"},
            ],
        ),
        changed_by=filled_id,
    )

    resp = await client.get(
        "/api/v1/methodist/schedule-preferences/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    rows = {r["student_id"]: r for r in body["students"]}
    assert rows[filled_id]["is_filled"] is True
    assert rows[filled_id]["preferred_count"] == 2
    assert rows[filled_id]["possible_count"] == 1
    assert rows[silent_id]["is_filled"] is False

    assert body["audience_total"] >= 2
    assert body["filled_total"] + body["silent_total"] == body["audience_total"]

    demand = {(c["weekday"], c["start_time"]): c for c in body["demand"]}
    assert demand[(0, "17:00:00")]["preferred_count"] >= 1
    assert demand[(5, "09:00:00")]["possible_count"] >= 1

    # Молчащие идут первыми — методист открывает экран, чтобы понять, кого дёргать.
    first_filled = next(
        (i for i, r in enumerate(body["students"]) if r["is_filled"]), len(body["students"])
    )
    last_silent = max(
        (i for i, r in enumerate(body["students"]) if not r["is_filled"]), default=-1
    )
    assert last_silent < first_filled


@pytest.mark.asyncio
async def test_summary_forbidden_for_student(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk674-gate")
    await _assign_plan(db, student_id, "base_legacy")
    token, _, _ = await create_session(db, user_id=student_id)

    resp = await client.get(
        "/api/v1/methodist/schedule-preferences/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ============================== Напоминания молчащим ==============================


@pytest.mark.asyncio
async def test_reminder_goes_only_to_silent_and_respects_cooldown(db):
    """Напоминание уходит молчащим и не повторяется, пока не истекла отсрочка."""
    from app.services import schedule_preference_reminder_service as reminder

    silent_id = await _create_user(db, role="student", prefix="tsk674-rem-silent")
    await _assign_plan(db, silent_id, "base_legacy")
    answered_id = await _create_user(db, role="student", prefix="tsk674-rem-answered")
    await _assign_plan(db, answered_id, "base_legacy")
    alumni_id = await _create_user(db, role="student", prefix="tsk674-rem-alum")
    await _assign_plan(db, alumni_id, "alumni")

    await schedule_preference_service.save_preference(
        db, answered_id, _body(), changed_by=answered_id
    )

    first = await reminder.enqueue_reminders(db)
    assert silent_id in first["students"], "молчащему напоминание нужно"
    assert answered_id not in first["students"], "ответившего не трогаем"
    assert alumni_id not in first["students"], "выпускника опрос не касается"

    # Уведомление действительно легло в inbox — тем же видом, что ждёт бот.
    row = (
        await db.execute(
            text(
                "SELECT kind, title, content, payload FROM notifications "
                " WHERE user_id = :uid AND kind = :kind"
            ),
            {"uid": silent_id, "kind": reminder.REMINDER_KIND},
        )
    ).first()
    assert row is not None
    assert "12:00 до 19:00" in row[2], "в тексте названо новое время школы"
    assert "10:00 и 11:00" in row[2], "и сказано, что утренние занятия переезжают"
    assert row[3]["url"].endswith("/me/schedule")

    second = await reminder.enqueue_reminders(db)
    assert silent_id not in second["students"], "второй раз подряд не пишем"
    assert silent_id in [] or second["skipped_cooldown"] >= 1


@pytest.mark.asyncio
async def test_reminder_dry_run_writes_nothing(db):
    from app.services import schedule_preference_reminder_service as reminder

    student_id = await _create_user(db, role="student", prefix="tsk674-rem-dry")
    await _assign_plan(db, student_id, "base_legacy")

    result = await reminder.enqueue_reminders(db, dry_run=True)
    assert student_id in result["students"]

    left = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM notifications WHERE user_id = :uid AND kind = :kind"
            ),
            {"uid": student_id, "kind": reminder.REMINDER_KIND},
        )
    ).scalar_one()
    assert left == 0, "пробный прогон ничего не пишет"


@pytest.mark.asyncio
async def test_bot_endpoint_returns_reminder_and_gates_strangers(db, client):
    """Бот читает напоминание сервисным ключом; чужой ученик — 403."""
    from app.services import schedule_preference_reminder_service as reminder

    student_id = await _create_user(db, role="student", prefix="tsk674-rem-bot")
    await _assign_plan(db, student_id, "base_legacy")
    await reminder.enqueue_reminders(db)

    token, _, _ = await create_session(db, user_id=student_id)
    resp = await client.get(
        f"/api/v1/students/{student_id}/schedule-preference-reminders/pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] >= 1
    assert body["items"][0]["kind"] == reminder.REMINDER_KIND
    assert body["items"][0]["payload"]["url"].endswith("/me/schedule")

    stranger_id = await _create_user(db, role="student", prefix="tsk674-rem-stranger")
    stranger_token, _, _ = await create_session(db, user_id=stranger_id)
    forbidden = await client.get(
        f"/api/v1/students/{student_id}/schedule-preference-reminders/pending",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_remind_endpoint_dry_run_for_methodist(db, client):
    methodist_id = await _create_user(db, role="methodist", prefix="tsk674-rem-meth")
    token, _, _ = await create_session(db, user_id=methodist_id)

    resp = await client.post(
        "/api/v1/methodist/schedule-preferences/remind?dry_run=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    assert body["silent_total"] >= 0

    student_id = await _create_user(db, role="student", prefix="tsk674-rem-gate")
    await _assign_plan(db, student_id, "base_legacy")
    student_token, _, _ = await create_session(db, user_id=student_id)
    forbidden = await client.post(
        "/api/v1/methodist/schedule-preferences/remind",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert forbidden.status_code == 403


# ─────────────── снятие анкеты сотрудником (tsk-714) ───────────────


@pytest.mark.asyncio
async def test_staff_can_clear_preference(db):
    """Снятая анкета возвращает ученика в молчащие, история при этом остаётся.

    Анкету можно переписать, но нельзя сохранить пустой — проверка требует
    желательных часов. Отменить ответ было нечем, а нужно: заполняют за брата,
    под чужой учётной записью, просят «забудьте, что я выбирал».
    """
    from app.services.schedule_preference_service import SchedulePreferenceError

    student_id = await _create_user(db, role="student")
    staff_id = await _create_user(db, role="methodist")
    await schedule_preference_service.save_preference(
        db,
        student_id,
        SchedulePreferenceWrite(
            lessons_per_week=1,
            hours=[{"weekday": 0, "start_time": "17:00", "kind": "preferred"}],
        ),
        changed_by=student_id,
    )
    assert await schedule_preference_service.is_pending(db, student_id) is False

    after = await schedule_preference_service.clear_preference(
        db, student_id, changed_by=staff_id
    )

    assert after["is_filled"] is False
    assert after["hours"] == []
    # Снова в молчащих: полоса напоминания у ученика должна вернуться.
    assert await schedule_preference_service.is_pending(db, student_id) is True

    history = await schedule_preference_service.list_history(db, student_id)
    assert len(history) == 2, "и что просил, и что анкету сняли"
    assert history[0]["source"] == "staff"
    assert history[0]["hours"] == []
    assert history[1]["hours"], "прежний выбор из истории не пропал"

    # Повторное снятие — понятный отказ, а не молчание.
    with pytest.raises(SchedulePreferenceError):
        await schedule_preference_service.clear_preference(db, student_id)


@pytest.mark.asyncio
async def test_clear_preference_is_closed_for_students(client, db):
    """Снять анкету может только методист: это чужой ответ, а не свой."""
    student_id = await _create_user(db, role="student")
    token, _, _ = await create_session(db, user_id=student_id)

    resp = await client.delete(
        f"/api/v1/methodist/schedule-preferences/{student_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
