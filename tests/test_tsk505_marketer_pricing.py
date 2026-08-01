"""tsk-505 (кабинет маркетолога: тарифы и расчётная цена ученика).

Проверяем на НАСТОЯЩЕЙ БД, по образцу test_tsk478_parent_portal.py.

Покрывает:
- гейт `marketer|admin`: 403 для teacher/student/methodist и для «без роли»;
- расчёт цены на всех исходах: точное попадание по частоте, ближайший меньший
  тариф при частоте вне сетки, нужен выбор человека (сегмент), нет расписания;
- ключевое правило модели: цена берётся ОДИН раз на тарифную группу —
  два курса одной группы не удваивают сумму (пара ЕГЭ = один продукт);
- общий `GET /users/search` НЕ открыт маркетологу (гейт ПДн не расширен);
- назначение цены курсу и запрет платного курса без тарифной группы.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_TAG = "tsk505"


async def _new_user(db, *, role: str | None, name: str) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}-{random.randint(10**6, 10**7)}",
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
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.commit()
    return u.id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _new_course(db, title: str) -> int:
    course_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES (:t, 'self_guided') RETURNING id"
            ),
            {"t": f"{_TAG}-{title}-{random.randint(10**6, 10**7)}"},
        )
    ).scalar()
    await db.commit()
    return course_id


async def _new_group(db, name: str, tariffs: list[tuple[str, int, str | None, str | None]]) -> int:
    group_id = (
        await db.execute(
            text("INSERT INTO pricing_group (name) VALUES (:n) RETURNING id"),
            {"n": f"{_TAG}-{name}-{random.randint(10**6, 10**7)}"},
        )
    ).scalar()
    for idx, (tname, price, kind, value) in enumerate(tariffs):
        await db.execute(
            text(
                "INSERT INTO pricing_tariff "
                "(group_id, name, price_minor, match_kind, match_value, sort_order) "
                "VALUES (:g, :n, :p, :k, :v, :s)"
            ),
            {"g": group_id, "n": tname, "p": price, "k": kind, "v": value, "s": idx},
        )
    await db.commit()
    return group_id


async def _price_course(db, *, course_id: int, group_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO course_pricing (course_id, sale_status, group_id) "
            "VALUES (:c, 'paid', :g) ON CONFLICT (course_id) DO UPDATE "
            "SET sale_status = 'paid', group_id = EXCLUDED.group_id"
        ),
        {"c": course_id, "g": group_id},
    )
    await db.commit()


async def _enroll(db, *, student_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": student_id, "c": course_id},
    )
    await db.commit()


async def _give_slots(db, *, student_id: int, teacher_id: int, count: int) -> None:
    """Сажает ученика в `count` активных недельных слотов."""
    for i in range(count):
        slot_id = (
            await db.execute(
                text(
                    "INSERT INTO lesson_slot "
                    "(teacher_id, weekday, start_time, duration_minutes, timezone, is_active) "
                    "VALUES (:t, :w, '10:00', 60, 'Europe/Moscow', true) RETURNING id"
                ),
                {"t": teacher_id, "w": i % 7},
            )
        ).scalar()
        await db.execute(
            text(
                "INSERT INTO lesson_slot_student (slot_id, student_id, is_active) "
                "VALUES (:s, :u, true)"
            ),
            {"s": slot_id, "u": student_id},
        )
    await db.commit()


async def _find_student(client, token: str, student_id: int) -> dict | None:
    resp = await client.get("/api/v1/marketer/students/pricing", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return next((s for s in resp.json() if s["student_id"] == student_id), None)


# ------------------------------------------------------------------ гейты


@pytest.mark.parametrize("role", ["teacher", "student", "methodist", None])
async def test_pricing_gate_rejects_other_roles(db, client, role):
    """Кабинет маркетолога закрыт для соседних ролей и для «без роли».

    Методист здесь не исключение и не забытая роль: он отвечает за учебное
    содержание, а не за деньги.
    """
    _, token = await _new_user(db, role=role, name=f"gate-{role}")
    for path in (
        "/api/v1/marketer/pricing/courses",
        "/api/v1/marketer/pricing/groups",
        "/api/v1/marketer/students/pricing",
        "/api/v1/marketer/leads",
        "/api/v1/marketer/lead-sources",
    ):
        resp = await client.get(path, headers=_auth(token))
        assert resp.status_code == 403, f"{path} под ролью {role}: {resp.status_code}"


@pytest.mark.parametrize("role", ["marketer", "admin"])
async def test_pricing_gate_allows_marketer_and_admin(db, client, role):
    _, token = await _new_user(db, role=role, name=f"ok-{role}")
    resp = await client.get("/api/v1/marketer/pricing/courses", headers=_auth(token))
    assert resp.status_code == 200, resp.text


async def test_service_token_is_not_allowed_into_marketer_cabinet(db, client):
    """Сервисный ключ в денежный контур не пускаем.

    `require_role` пропускает сервисный токен БЕЗ проверки роли — это сделано
    ради ботов. Здесь это не годится: держатель legacy-ключа TG_LMS читал бы
    ФИО всех платящих учеников с ценами, а правка цены писалась бы без следа
    «кто менял». Решение зафиксировано тестом, а не комментарием.
    """
    api_key = next(iter(Settings().valid_api_keys))
    headers = {"X-API-Key": api_key}
    for path in (
        "/api/v1/marketer/pricing/courses",
        "/api/v1/marketer/students/pricing",
        "/api/v1/marketer/leads",
    ):
        resp = await client.get(path, headers=headers)
        assert resp.status_code == 403, f"{path} под сервисным ключом: {resp.status_code}"


async def test_marketer_cannot_reach_general_people_search(db, client):
    """Гейт персональных данных не расширен: общий поиск людей маркетологу закрыт.

    Для привязки лида у него есть свой узкий адрес, отдающий только номер и имя.
    """
    _, token = await _new_user(db, role="marketer", name="no-pd")

    denied = await client.get("/api/v1/users/search?q=ив", headers=_auth(token))
    assert denied.status_code == 403

    allowed = await client.get(
        "/api/v1/marketer/students/search?q=ив", headers=_auth(token)
    )
    assert allowed.status_code == 200
    for item in allowed.json():
        assert set(item.keys()) == {"id", "full_name"}


# ------------------------------------------------------------------ расчёт цены


async def test_two_courses_of_one_group_are_priced_once(db, client):
    """Пара курсов одной группы — один продукт, а не двойная цена.

    Это главное правило модели: на проде 24 ученика из 34 зачислены сразу на
    «Python для ЕГЭ» + «ЕГЭ по информатике» и платят 5500, а не 11000.
    """
    _, marketer_token = await _new_user(db, role="marketer", name="m1")
    teacher_id, _ = await _new_user(db, role="teacher", name="t1")
    student_id, _ = await _new_user(db, role="student", name="s-pair")

    group_id = await _new_group(
        db, "pair", [("2 раза", 550000, "attendance_frequency", "2"),
                     ("1 раз", 275000, "attendance_frequency", "1")]
    )
    course_a = await _new_course(db, "ege")
    course_b = await _new_course(db, "python")
    await _price_course(db, course_id=course_a, group_id=group_id)
    await _price_course(db, course_id=course_b, group_id=group_id)
    await _enroll(db, student_id=student_id, course_id=course_a)
    await _enroll(db, student_id=student_id, course_id=course_b)
    await _give_slots(db, student_id=student_id, teacher_id=teacher_id, count=2)

    row = await _find_student(client, marketer_token, student_id)
    assert row is not None
    assert row["weekly_lessons"] == 2
    assert len(row["groups"]) == 1, "два курса одной группы должны схлопнуться в одну строку"
    group = row["groups"][0]
    assert group["status"] == "exact"
    assert group["price_minor"] == 550000
    assert len(group["course_titles"]) == 2
    assert row["total_price_minor"] == 550000


async def test_single_slot_gets_cheaper_tariff(db, client):
    _, marketer_token = await _new_user(db, role="marketer", name="m2")
    teacher_id, _ = await _new_user(db, role="teacher", name="t2")
    student_id, _ = await _new_user(db, role="student", name="s-one")

    group_id = await _new_group(
        db, "freq", [("2 раза", 550000, "attendance_frequency", "2"),
                     ("1 раз", 275000, "attendance_frequency", "1")]
    )
    course_id = await _new_course(db, "one")
    await _price_course(db, course_id=course_id, group_id=group_id)
    await _enroll(db, student_id=student_id, course_id=course_id)
    await _give_slots(db, student_id=student_id, teacher_id=teacher_id, count=1)

    row = await _find_student(client, marketer_token, student_id)
    assert row["weekly_lessons"] == 1
    assert row["groups"][0]["status"] == "exact"
    assert row["groups"][0]["price_minor"] == 275000


async def test_frequency_above_grid_falls_back_and_is_marked(db, client):
    """Частота вне сетки берёт ближайший меньший тариф — и помечает это.

    Молчаливая подстановка выдала бы догадку за точный расчёт.
    """
    _, marketer_token = await _new_user(db, role="marketer", name="m3")
    teacher_id, _ = await _new_user(db, role="teacher", name="t3")
    student_id, _ = await _new_user(db, role="student", name="s-three")

    group_id = await _new_group(
        db, "grid", [("2 раза", 550000, "attendance_frequency", "2"),
                     ("1 раз", 275000, "attendance_frequency", "1")]
    )
    course_id = await _new_course(db, "three")
    await _price_course(db, course_id=course_id, group_id=group_id)
    await _enroll(db, student_id=student_id, course_id=course_id)
    await _give_slots(db, student_id=student_id, teacher_id=teacher_id, count=3)

    row = await _find_student(client, marketer_token, student_id)
    assert row["weekly_lessons"] == 3
    group = row["groups"][0]
    assert group["status"] == "fallback_lower"
    assert group["price_minor"] == 550000


async def test_segment_group_needs_human_choice(db, client):
    """Сегмент («для своих» / «улица») автоматически не выбирается."""
    _, marketer_token = await _new_user(db, role="marketer", name="m4")
    teacher_id, _ = await _new_user(db, role="teacher", name="t4")
    student_id, _ = await _new_user(db, role="student", name="s-seg")

    group_id = await _new_group(
        db, "seg", [("Для своих", 1000000, "segment", "insider"),
                    ("Улица", 2000000, "segment", "street")]
    )
    course_id = await _new_course(db, "track")
    await _price_course(db, course_id=course_id, group_id=group_id)
    await _enroll(db, student_id=student_id, course_id=course_id)
    await _give_slots(db, student_id=student_id, teacher_id=teacher_id, count=1)

    row = await _find_student(client, marketer_token, student_id)
    group = row["groups"][0]
    assert group["status"] == "needs_choice"
    assert group["price_minor"] is None
    assert len(group["options"]) == 2
    assert row["total_price_minor"] is None, "частичная сумма не должна выглядеть полной"


async def test_student_without_schedule_has_no_price(db, client):
    _, marketer_token = await _new_user(db, role="marketer", name="m5")
    student_id, _ = await _new_user(db, role="student", name="s-noslot")

    group_id = await _new_group(
        db, "noslot", [("2 раза", 550000, "attendance_frequency", "2")]
    )
    course_id = await _new_course(db, "noslot")
    await _price_course(db, course_id=course_id, group_id=group_id)
    await _enroll(db, student_id=student_id, course_id=course_id)

    row = await _find_student(client, marketer_token, student_id)
    assert row["weekly_lessons"] == 0
    assert row["groups"][0]["status"] == "no_schedule"
    assert row["total_price_minor"] is None


async def test_different_groups_are_summed(db, client):
    """Разные тарифные группы — разные продукты, они складываются."""
    _, marketer_token = await _new_user(db, role="marketer", name="m6")
    teacher_id, _ = await _new_user(db, role="teacher", name="t6")
    student_id, _ = await _new_user(db, role="student", name="s-two-groups")

    group_a = await _new_group(db, "ga", [("1 раз", 275000, "attendance_frequency", "1")])
    group_b = await _new_group(db, "gb", [("Всё включено", 400000, None, None)])
    course_a = await _new_course(db, "ga-course")
    course_b = await _new_course(db, "gb-course")
    await _price_course(db, course_id=course_a, group_id=group_a)
    await _price_course(db, course_id=course_b, group_id=group_b)
    await _enroll(db, student_id=student_id, course_id=course_a)
    await _enroll(db, student_id=student_id, course_id=course_b)
    await _give_slots(db, student_id=student_id, teacher_id=teacher_id, count=1)

    row = await _find_student(client, marketer_token, student_id)
    assert len(row["groups"]) == 2
    assert row["total_price_minor"] == 675000


# ------------------------------------------------------------------ назначение цены


async def test_set_course_pricing_roundtrip(db, client):
    _, token = await _new_user(db, role="marketer", name="m7")
    group_id = await _new_group(db, "set", [("Базовый", 100000, None, None)])
    course_id = await _new_course(db, "to-price")

    resp = await client.put(
        f"/api/v1/marketer/pricing/courses/{course_id}",
        json={"sale_status": "paid", "group_id": group_id},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sale_status"] == "paid"
    assert resp.json()["group_id"] == group_id

    off = await client.put(
        f"/api/v1/marketer/pricing/courses/{course_id}",
        json={"sale_status": "not_for_sale"},
        headers=_auth(token),
    )
    assert off.status_code == 200, off.text
    assert off.json()["sale_status"] == "not_for_sale"
    assert off.json()["group_id"] is None


async def test_paid_course_requires_group(db, client):
    """Платный курс без тарифной группы посчитать нечем — отклоняем."""
    _, token = await _new_user(db, role="marketer", name="m8")
    course_id = await _new_course(db, "no-group")

    resp = await client.put(
        f"/api/v1/marketer/pricing/courses/{course_id}",
        json={"sale_status": "paid"},
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


async def test_below_grid_is_not_reported_as_exact(db, client):
    """Промах ниже тарифной сетки — не «точное совпадение».

    Раньше при сетке {2,3} и одном занятии исполнение проваливалось к варианту
    «без оси» и отдавало его со статусом `exact`: догадка выглядела точным
    расчётом, зелёным бейджем на экране.
    """
    _, marketer_token = await _new_user(db, role="marketer", name="m-below")
    teacher_id, _ = await _new_user(db, role="teacher", name="t-below")
    student_id, _ = await _new_user(db, role="student", name="s-below")

    group_id = await _new_group(
        db,
        "below",
        [
            ("2 раза", 550000, "attendance_frequency", "2"),
            ("3 раза", 700000, "attendance_frequency", "3"),
            ("Общий", 100000, None, None),
        ],
    )
    course_id = await _new_course(db, "below")
    await _price_course(db, course_id=course_id, group_id=group_id)
    await _enroll(db, student_id=student_id, course_id=course_id)
    await _give_slots(db, student_id=student_id, teacher_id=teacher_id, count=1)

    row = await _find_student(client, marketer_token, student_id)
    group = row["groups"][0]
    assert group["status"] == "below_grid"
    assert group["price_minor"] is None
    assert row["total_price_minor"] is None


async def test_non_numeric_frequency_is_rejected_on_create(db, client):
    """Нечисловая частота не заводится: иначе тариф молча становился бы ценой."""
    _, token = await _new_user(db, role="marketer", name="m-badfreq")
    group_id = await _new_group(db, "badfreq", [("Общий", 100000, None, None)])

    resp = await client.post(
        "/api/v1/marketer/pricing/tariffs",
        json={
            "group_id": group_id,
            "name": "два раза",
            "price_minor": 500000,
            "match_kind": "attendance_frequency",
            "match_value": "два",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


async def test_pricing_for_nested_course_is_rejected_without_writing(db, client):
    """Цена вложенному курсу не назначается — и строка при этом не создаётся."""
    _, token = await _new_user(db, role="marketer", name="m-nested")
    group_id = await _new_group(db, "nested", [("Общий", 100000, None, None)])
    parent_id = await _new_course(db, "parent")
    child_id = await _new_course(db, "child")
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id) "
            "VALUES (:c, :p) ON CONFLICT DO NOTHING"
        ),
        {"c": child_id, "p": parent_id},
    )
    await db.commit()

    resp = await client.put(
        f"/api/v1/marketer/pricing/courses/{child_id}",
        json={"sale_status": "paid", "group_id": group_id},
        headers=_auth(token),
    )
    assert resp.status_code == 404, resp.text

    leftover = (
        await db.execute(
            text("SELECT count(*) FROM course_pricing WHERE course_id = :c"),
            {"c": child_id},
        )
    ).scalar()
    assert leftover == 0, "отказ не должен оставлять строку цены в базе"


async def test_group_description_can_be_cleared(db, client):
    _, token = await _new_user(db, role="marketer", name="m-cleardesc")
    group_id = await _new_group(db, "cleardesc", [("Общий", 100000, None, None)])
    await db.execute(
        text("UPDATE pricing_group SET description = 'временное' WHERE id = :id"),
        {"id": group_id},
    )
    await db.commit()

    resp = await client.patch(
        f"/api/v1/marketer/pricing/groups/{group_id}",
        json={"description": None},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] is None


async def test_course_list_marks_unpriced_courses(db, client):
    """Курс без назначенной цены отдаётся как `null`, а не как ноль."""
    _, token = await _new_user(db, role="marketer", name="m9")
    course_id = await _new_course(db, "untouched")

    resp = await client.get("/api/v1/marketer/pricing/courses", headers=_auth(token))
    assert resp.status_code == 200
    row = next((c for c in resp.json() if c["course_id"] == course_id), None)
    assert row is not None, "новый корневой курс должен попадать в список"
    assert row["sale_status"] is None
    assert row["tariffs"] == []
