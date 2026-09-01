"""tsk-433 Волна 2.2: правка задания методистом и её защита от импорта.

Продолжение защиты материалов, но с двумя отличиями, которые и проверяем:

1. **Пара неразделима.** `task_content` и `solution_rules` защищаются только
   вместе. Порознь они разъезжаются: правило из источника ссылается на СВОИ
   варианты (`correct_options` ↔ `task_content.options`), перекрёстная
   валидация это ловит и возвращает ошибку — а ошибка одного задания роняет
   весь пакет импорта, то есть одна ручная правка сломала бы публикацию курса.
2. **Правка проверяется на согласованность.** Верный вариант обязан
   существовать, у SC он ровно один — иначе 422, а не молча сломанная проверка
   ответов у учеников.
"""
from __future__ import annotations

import json
import random
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()

TASKS_URL = "/api/v1/tasks/bulk-upsert"
EASY = 2


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


async def _user_with_session(db, role: str | None) -> tuple[int, str]:
    u = Users(
        email=f"t433t-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t433t-{role or 'norole'}",
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
                "SELECT :u, r.id FROM roles r WHERE r.name = :r ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "r": role},
        )
    await db.commit()
    return u.id, token


async def _new_course(db) -> int:
    row = (
        await db.execute(
            text(
                "INSERT INTO courses (title, description, access_level, is_required, course_uid) "
                "VALUES (:t, 'tsk-433', 'self_guided', false, :uid) RETURNING id"
            ),
            {"t": "test_tsk433_task_edit", "uid": f"lms:test:t433t:{uuid.uuid4().hex[:12]}"},
        )
    ).first()
    await db.flush()
    return int(row.id)


def _source_task(external_uid: str, course_id: int, *, stem: str) -> dict[str, Any]:
    """Payload переиздания из источника (как кладёт ContentBackbone)."""
    return {
        "external_uid": external_uid,
        "course_id": course_id,
        "difficulty_id": EASY,
        "task_content": {
            "type": "SC",
            "stem": stem,
            "options": [
                {"id": "a", "text": "из источника A"},
                {"id": "b", "text": "из источника B"},
            ],
        },
        "solution_rules": {"type": "SC", "correct_options": ["a"], "max_score": 1},
        "max_score": 1,
    }


async def _post(client, items: list[dict[str, Any]]):
    return await client.post(TASKS_URL, params={"api_key": _api_key()}, json={"items": items})


async def _row(db, external_uid: str):
    row = (
        await db.execute(
            text(
                "SELECT id, task_content, solution_rules, content_provenance "
                "FROM tasks WHERE external_uid = :uid"
            ),
            {"uid": external_uid},
        )
    ).first()
    assert row is not None, f"задание {external_uid} не найдено"
    return row


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_methodist_edits_task_and_import_respects_it(db, client):
    """Правка условия переживает переиздание из источника."""
    course_id = await _new_course(db)
    uid = f"t433t-{uuid.uuid4().hex[:8]}"
    await _post(client, [_source_task(uid, course_id, stem="условие из источника")])
    task = await _row(db, uid)

    _, token = await _user_with_session(db, "methodist")
    patched = await client.patch(
        f"/api/v1/tasks/{task.id}",
        json={
            "task_content": {
                "type": "SC",
                "stem": "условие переписано методистом",
                "options": [
                    {"id": "a", "text": "из источника A"},
                    {"id": "b", "text": "из источника B"},
                ],
            }
        },
        headers=_auth(token),
    )
    assert patched.status_code == 200, patched.text

    await _post(client, [_source_task(uid, course_id, stem="условие из источника")])
    after = await _row(db, uid)
    assert "переписано методистом" in json.dumps(after.task_content, ensure_ascii=False), (
        "правка условия затёрта импортом"
    )


@pytest.mark.asyncio
async def test_provenance_marks_both_fields(db, client):
    """Правка одного поля помечает пару — порознь они разъедутся."""
    course_id = await _new_course(db)
    uid = f"t433t-{uuid.uuid4().hex[:8]}"
    await _post(client, [_source_task(uid, course_id, stem="условие")])
    task = await _row(db, uid)
    _, token = await _user_with_session(db, "methodist")

    r = await client.patch(
        f"/api/v1/tasks/{task.id}",
        json={"solution_rules": {"type": "SC", "correct_options": ["b"], "max_score": 1}},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text

    after = await _row(db, uid)
    assert after.content_provenance["fields"] == ["solution_rules", "task_content"], (
        "пометка обязана покрывать пару целиком, иначе импорт рассинхронизирует "
        "правило и варианты и уронит весь пакет"
    )
    assert r.json().get("content_provenance") is not None, "пометка обязана дойти до клиента"


@pytest.mark.asyncio
async def test_edited_task_does_not_break_batch_import(db, client):
    """Правленое задание не роняет пакет — соседнее обновляется нормально.

    Это главный сценарий, ради которого пара защищается целиком.
    """
    course_id = await _new_course(db)
    edited_uid = f"t433t-ed-{uuid.uuid4().hex[:6]}"
    plain_uid = f"t433t-pl-{uuid.uuid4().hex[:6]}"
    await _post(
        client,
        [
            _source_task(edited_uid, course_id, stem="первое"),
            _source_task(plain_uid, course_id, stem="второе"),
        ],
    )
    edited = await _row(db, edited_uid)
    _, token = await _user_with_session(db, "methodist")

    # методист меняет варианты — теперь они не совпадают с источником
    r = await client.patch(
        f"/api/v1/tasks/{edited.id}",
        json={
            "task_content": {
                "type": "SC",
                "stem": "первое",
                "options": [
                    {"id": "x", "text": "свой вариант X"},
                    {"id": "y", "text": "свой вариант Y"},
                ],
            },
            "solution_rules": {"type": "SC", "correct_options": ["x"], "max_score": 1},
        },
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text

    resp = await _post(
        client,
        [
            _source_task(edited_uid, course_id, stem="первое обновлено"),
            _source_task(plain_uid, course_id, stem="второе обновлено"),
        ],
    )
    assert resp.status_code == 200, f"пакет упал из-за правленого задания: {resp.text}"

    after_edited = await _row(db, edited_uid)
    after_plain = await _row(db, plain_uid)
    assert "свой вариант X" in json.dumps(after_edited.task_content, ensure_ascii=False)
    assert "второе обновлено" in json.dumps(after_plain.task_content, ensure_ascii=False), (
        "соседнее задание обязано обновиться как раньше"
    )


@pytest.mark.asyncio
async def test_inconsistent_rule_rejected(db, client):
    """Верный вариант, которого нет среди вариантов, — 422, а не тихая поломка."""
    course_id = await _new_course(db)
    uid = f"t433t-{uuid.uuid4().hex[:8]}"
    await _post(client, [_source_task(uid, course_id, stem="условие")])
    task = await _row(db, uid)
    _, token = await _user_with_session(db, "methodist")

    r = await client.patch(
        f"/api/v1/tasks/{task.id}",
        json={"solution_rules": {"type": "SC", "correct_options": ["zzz"], "max_score": 1}},
        headers=_auth(token),
    )
    assert r.status_code == 422, (
        f"ожидали 422 на правиле с несуществующим вариантом, получено {r.status_code}"
    )


@pytest.mark.asyncio
async def test_sc_with_two_correct_rejected(db, client):
    """У задания с одиночным выбором не может быть двух верных ответов."""
    course_id = await _new_course(db)
    uid = f"t433t-{uuid.uuid4().hex[:8]}"
    await _post(client, [_source_task(uid, course_id, stem="условие")])
    task = await _row(db, uid)
    _, token = await _user_with_session(db, "methodist")

    r = await client.patch(
        f"/api/v1/tasks/{task.id}",
        json={"solution_rules": {"type": "SC", "correct_options": ["a", "b"], "max_score": 1}},
        headers=_auth(token),
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_student_cannot_patch_task(db, client):
    course_id = await _new_course(db)
    uid = f"t433t-{uuid.uuid4().hex[:8]}"
    await _post(client, [_source_task(uid, course_id, stem="условие")])
    task = await _row(db, uid)
    _, token = await _user_with_session(db, "student")

    r = await client.patch(
        f"/api/v1/tasks/{task.id}",
        json={"solution_rules": {"type": "SC", "correct_options": ["b"], "max_score": 1}},
        headers=_auth(token),
    )
    assert r.status_code == 403, f"ученик не должен править задания, получено {r.status_code}"


@pytest.mark.asyncio
async def test_clear_manual_edit(db, client):
    course_id = await _new_course(db)
    uid = f"t433t-{uuid.uuid4().hex[:8]}"
    await _post(client, [_source_task(uid, course_id, stem="условие")])
    task = await _row(db, uid)
    _, token = await _user_with_session(db, "methodist")

    await client.patch(
        f"/api/v1/tasks/{task.id}",
        json={"solution_rules": {"type": "SC", "correct_options": ["b"], "max_score": 1}},
        headers=_auth(token),
    )
    r = await client.delete(f"/api/v1/tasks/{task.id}/manual-edit", headers=_auth(token))
    assert r.status_code == 200, r.text

    after = await _row(db, uid)
    assert after.content_provenance is None

    # после снятия пометки источник снова управляет содержимым
    await _post(client, [_source_task(uid, course_id, stem="вернулось из источника")])
    restored = await _row(db, uid)
    assert "вернулось из источника" in json.dumps(restored.task_content, ensure_ascii=False)


@pytest.mark.asyncio
async def test_task_without_provenance_unchanged_behaviour(db, client):
    """Без пометки импорт обновляет задание как раньше — регресс."""
    course_id = await _new_course(db)
    uid = f"t433t-{uuid.uuid4().hex[:8]}"
    await _post(client, [_source_task(uid, course_id, stem="было")])
    await _post(client, [_source_task(uid, course_id, stem="стало")])

    after = await _row(db, uid)
    assert "стало" in json.dumps(after.task_content, ensure_ascii=False)
    assert after.content_provenance is None


@pytest.mark.asyncio
async def test_manual_script_source_respected_by_import(db, client):
    """tsk-760: правка, помеченная как сделанная скриптом, тоже переживает импорт.

    Правки августа делались не через кабинет, а скриптами и запросами к БД
    (эталоны, условия, перенос картинок) — импорт считал их своими и
    перезаписывал. Источник `manual_script` ставит разовая простановка
    `scripts/tsk760_mark_manual_edits.py`, и он уважается наравне с `manual_web`.
    """
    course_id = await _new_course(db)
    uid = f"t760-{uuid.uuid4().hex[:8]}"
    await _post(client, [_source_task(uid, course_id, stem="условие из источника")])

    await db.execute(
        text(
            "UPDATE tasks SET task_content = CAST(:tc AS jsonb), "
            "content_provenance = CAST(:prov AS jsonb) WHERE external_uid = :uid"
        ),
        {
            "tc": json.dumps(
                {
                    "type": "SC",
                    "stem": "условие поправлено скриптом",
                    "options": [
                        {"id": "a", "text": "из источника A"},
                        {"id": "b", "text": "из источника B"},
                    ],
                },
                ensure_ascii=False,
            ),
            "prov": json.dumps(
                {
                    "source": "manual_script",
                    "edited_by": "tsk-760",
                    "fields": ["task_content", "solution_rules"],
                },
                ensure_ascii=False,
            ),
            "uid": uid,
        },
    )
    await db.flush()

    await _post(client, [_source_task(uid, course_id, stem="условие из источника")])

    after = await _row(db, uid)
    assert "поправлено скриптом" in json.dumps(after.task_content, ensure_ascii=False), (
        "правка, помеченная manual_script, затёрта импортом"
    )


@pytest.mark.asyncio
async def test_unknown_provenance_source_does_not_protect(db, client):
    """Чужой/битый источник в пометке защиты не даёт — иначе ей можно заморозить что угодно."""
    course_id = await _new_course(db)
    uid = f"t760x-{uuid.uuid4().hex[:8]}"
    await _post(client, [_source_task(uid, course_id, stem="условие из источника")])

    await db.execute(
        text(
            "UPDATE tasks SET task_content = CAST(:tc AS jsonb), "
            "content_provenance = CAST(:prov AS jsonb) WHERE external_uid = :uid"
        ),
        {
            "tc": json.dumps(
                {
                    "type": "SC",
                    "stem": "правка неизвестного происхождения",
                    "options": [
                        {"id": "a", "text": "из источника A"},
                        {"id": "b", "text": "из источника B"},
                    ],
                },
                ensure_ascii=False,
            ),
            "prov": json.dumps(
                {"source": "robot_unknown", "fields": ["task_content"]}, ensure_ascii=False
            ),
            "uid": uid,
        },
    )
    await db.flush()

    await _post(client, [_source_task(uid, course_id, stem="условие из источника")])

    after = await _row(db, uid)
    assert "из источника" in json.dumps(after.task_content, ensure_ascii=False)


@pytest.mark.asyncio
async def test_override_manual_edit_позволяет_служебную_правку(db, client):
    """tsk-760: round-trip инструменты пробивают защиту явным флагом.

    Гигиена условия и докачка картинок читают задание ИЗ LMS, чинят и кладут
    обратно — то есть правят как раз ту версию, которую защита бережёт. Без
    обхода они молча перестали бы работать на всех помеченных заданиях.
    """
    course_id = await _new_course(db)
    uid = f"t760o-{uuid.uuid4().hex[:8]}"
    await _post(client, [_source_task(uid, course_id, stem="условие из источника")])

    await db.execute(
        text(
            "UPDATE tasks SET content_provenance = CAST(:prov AS jsonb) WHERE external_uid = :uid"
        ),
        {
            "prov": json.dumps(
                {"source": "manual_script", "fields": ["task_content", "solution_rules"]},
                ensure_ascii=False,
            ),
            "uid": uid,
        },
    )
    await db.flush()

    payload = _source_task(uid, course_id, stem="условие починено служебным прогоном")
    payload["override_manual_edit"] = True
    await _post(client, [payload])

    after = await _row(db, uid)
    assert "починено служебным прогоном" in json.dumps(after.task_content, ensure_ascii=False)
    # Пометка остаётся: обход разовый, задание по-прежнему считается правленным.
    prov = after.content_provenance
    prov = json.loads(prov) if isinstance(prov, str) else prov
    assert prov["source"] == "manual_script"
