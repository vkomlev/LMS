"""Повторное назначение курса ученику отдаёт 409, а не 500 (tsk-574).

Прод 2026-08-06: скрипт пакетного назначения отработал дважды. Первый прогон
создал все связи, второй вернул пять «Internal server error» — оператор не мог
отличить «уже назначено» от отказа сервера и повторял запрос вслепую.
Причина: `UniqueViolationError` на составном PK `user_courses_pkey` доходил до
глобального обработчика `Exception` и превращался в 500.

Проверяем оба слоя защиты:
* штатный дубль — ранняя проверка в `UserCoursesService.create`;
* гонка (строку создали между проверкой и вставкой) — перехват
  `IntegrityError`; заодно доказываем, что после конфликта соединение
  остаётся рабочим (следующий запрос отвечает нормально).
"""
from __future__ import annotations

import os
import random

import pytest
from sqlalchemy import text

from app.api.v1 import user_courses as user_courses_api
from app.models.users import Users

_TAG = "tsk574"


def _api_key_qs() -> str:
    """Legacy `get_db` ждёт api_key в QUERY-параметре, не в заголовке."""
    key = os.environ.get("VALID_API_KEYS", "").split(",")[0].strip()
    if not key:
        pytest.skip("VALID_API_KEYS не задан в .env — пропускаем")
    return f"api_key={key}"


async def _create_student(db) -> int:
    """Завести ученика для теста."""
    user = Users(
        email=f"{_TAG}-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-stud",
        tg_id=None,
    )
    db.add(user)
    await db.flush()
    await db.commit()
    return user.id


async def _create_root_course(db) -> int:
    """Завести корневой курс (без родителей — иначе триггер запретит связь)."""
    res = await db.execute(
        text(
            "INSERT INTO courses (title, access_level) "
            "VALUES (:title, 'self_guided') RETURNING id"
        ),
        {"title": f"{_TAG}-course-{random.randint(10**8, 10**10)}"},
    )
    course_id = res.scalar_one()
    await db.commit()
    return course_id


async def _link_count(db, user_id: int, course_id: int) -> int:
    """Сколько строк связи лежит в БД для пары ученик↔курс."""
    res = await db.execute(
        text(
            "SELECT count(*) FROM user_courses "
            "WHERE user_id = :u AND course_id = :c"
        ),
        {"u": user_id, "c": course_id},
    )
    return int(res.scalar_one())


@pytest.mark.asyncio
async def test_duplicate_assignment_returns_409(db, client):
    """Второй POST той же пары — 409 с понятным телом, дубля в БД нет."""
    student_id = await _create_student(db)
    course_id = await _create_root_course(db)
    payload = {"user_id": student_id, "course_id": course_id, "is_active": True}
    url = f"/api/v1/user-courses/?{_api_key_qs()}"

    first = await client.post(url, json=payload)
    assert first.status_code == 201, first.text

    second = await client.post(url, json=payload)
    assert second.status_code == 409, second.text

    body = second.json()
    assert body["error"] == "domain_error"
    assert "уже назначен" in body["detail"]
    assert body["payload"] == {"user_id": student_id, "course_id": course_id}

    assert await _link_count(db, student_id, course_id) == 1


@pytest.mark.asyncio
async def test_duplicate_race_returns_409_and_keeps_session_usable(
    db, client, monkeypatch
):
    """Гонка: строка появилась между проверкой и вставкой — тоже 409.

    Раннюю проверку глушим (`get_by_keys` возвращает None), чтобы запрос дошёл
    до INSERT и упал на `user_courses_pkey`. Это ровно то, что произойдёт при
    двух параллельных назначениях одной пары.
    """
    student_id = await _create_student(db)
    course_id = await _create_root_course(db)
    payload = {"user_id": student_id, "course_id": course_id, "is_active": True}
    url = f"/api/v1/user-courses/?{_api_key_qs()}"

    first = await client.post(url, json=payload)
    assert first.status_code == 201, first.text

    original_get_by_keys = user_courses_api.service.repo.get_by_keys

    async def _blind_get_by_keys(*args, **kwargs):
        """Сделать вид, что связи ещё нет — как соседний запрос до вставки."""
        return None

    monkeypatch.setattr(
        user_courses_api.service.repo, "get_by_keys", _blind_get_by_keys
    )
    try:
        raced = await client.post(url, json=payload)
    finally:
        monkeypatch.setattr(
            user_courses_api.service.repo, "get_by_keys", original_get_by_keys
        )

    assert raced.status_code == 409, raced.text
    assert raced.json()["error"] == "domain_error"
    assert await _link_count(db, student_id, course_id) == 1

    # После конфликта соединение обязано остаться рабочим: откат сессии не
    # должен обрывать транзакцию так, чтобы следующий запрос падал.
    after = await client.get(
        f"/api/v1/user-courses/{student_id}/{course_id}?{_api_key_qs()}"
    )
    assert after.status_code == 200, after.text
    assert after.json()["user_id"] == student_id
