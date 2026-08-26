"""tsk-684: `syllabus-states` отдаёт названия элементов — веер запросов не нужен.

Кабинет добирал название, тип и ссылку отдельным запросом НА КАЖДЫЙ подкурс
(`GET /tasks/by-course/{id}` + `GET /courses/{id}/materials`): 110 запросов и
7,5 МБ за одно открытие флагмана (замер `docs/qa/2026-08-26-tsk684-zamer-do.md`).
Поля лежат в строках, которые SQL синтабуса уже читает, — теперь они в ответе.

Покрывает:
- поля задания (`external_uid`, `title`, `task_type`) и материала
  (`title`, `material_type`) доезжают и совпадают с базой;
- задание без своего названия отдаёт `stem_preview` — очищённый от разметки;
- задание СО своим названием `stem_preview` не отдаёт (не возим условие впустую);
- **число обращений к базе на вызов не выросло** — сторож первопричины tsk-662:
  задания и материалы читаются одним запросом каждый, независимо от размера
  дерева. Без этого сторожа поштучный дозапрос названий вернулся бы незаметно.
"""
from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import event, text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


# ────────────────────────── Helpers ────────────────────────────────────────


async def _create_student(db, *, prefix: str = "tsk684") -> tuple[int, str]:
    """Создать student-юзера с email-identity и сессией. Returns (user_id, token)."""
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-stud", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, token


async def _make_tree(db) -> dict:
    """Изолированный root-курс с подкурсом, двумя заданиями и материалом.

    Подкурс нужен намеренно: веер вырастал именно из обхода подкурсов, и
    сторож числа запросов обязан считать дерево, а не одиночный курс.

    Returns:
        dict с id: root_id, child_id, titled_task_id, untitled_task_id,
        material_id, а также ожидаемыми значениями полей.
    """
    suffix = uuid.uuid4().hex[:8]
    root_id = int(
        (
            await db.execute(
                text("INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"),
                {"t": f"tsk684-root {suffix}"},
            )
        ).scalar()
    )
    child_id = int(
        (
            await db.execute(
                text("INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"),
                {"t": f"tsk684-child {suffix}"},
            )
        ).scalar()
    )
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
            "VALUES (:c, :p, 1)"
        ),
        {"c": child_id, "p": root_id},
    )
    diff = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()

    titled_uid = f"tsk684:titled:{suffix}"
    titled_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO tasks (course_id, difficulty_id, external_uid, task_content, solution_rules) "
                    "VALUES (:c, :d, :uid, CAST(:tc AS jsonb), CAST(:sr AS jsonb)) RETURNING id"
                ),
                {
                    "c": root_id,
                    "d": diff,
                    "uid": titled_uid,
                    "tc": '{"type":"SA","title":"Своё название задания","stem":"Условие с названием"}',
                    "sr": '{"max_score":1}',
                },
            )
        ).scalar()
    )

    # Задание без curated title и с разметкой в условии — тот самый случай, ради
    # которого нужен stem_preview (45 таких из 6409 активных на бою, 26.08).
    untitled_uid = f"tsk684:untitled:{suffix}"
    untitled_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO tasks (course_id, difficulty_id, external_uid, task_content, solution_rules) "
                    "VALUES (:c, :d, :uid, CAST(:tc AS jsonb), CAST(:sr AS jsonb)) RETURNING id"
                ),
                {
                    "c": child_id,
                    "d": diff,
                    "uid": untitled_uid,
                    "tc": (
                        '{"type":"SA","title":"","stem":'
                        '"<html><body><p>Выведите &quot;Привет!&quot;</p></body></html>"}'
                    ),
                    "sr": '{"max_score":1}',
                },
            )
        ).scalar()
    )

    material_title = f"tsk684-material-{suffix}"
    material_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO materials "
                    "(title, course_id, type, content, order_position, is_active, "
                    " external_uid, created_at, updated_at) "
                    "VALUES (:title, :c, 'video', '{}'::jsonb, 1, true, :uid, now(), now()) "
                    "RETURNING id"
                ),
                {"title": material_title, "c": child_id, "uid": f"tsk684:mat:{suffix}"},
            )
        ).scalar()
    )
    await db.commit()
    return {
        "root_id": root_id,
        "child_id": child_id,
        "titled_task_id": titled_id,
        "titled_uid": titled_uid,
        "untitled_task_id": untitled_id,
        "untitled_uid": untitled_uid,
        "material_id": material_id,
        "material_title": material_title,
    }


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "c": course_id},
    )
    await db.commit()


async def _drop(db, tree: dict, user_id: int) -> None:
    await db.execute(
        text("DELETE FROM materials WHERE id = :m"), {"m": tree["material_id"]}
    )
    await db.execute(
        text("DELETE FROM tasks WHERE id = ANY(:ids)"),
        {"ids": [tree["titled_task_id"], tree["untitled_task_id"]]},
    )
    await db.execute(
        text("DELETE FROM course_parents WHERE course_id = :c"), {"c": tree["child_id"]}
    )
    await db.execute(text("DELETE FROM user_courses WHERE user_id = :u"), {"u": user_id})
    await db.execute(
        text("DELETE FROM courses WHERE id = ANY(:ids)"),
        {"ids": [tree["root_id"], tree["child_id"]]},
    )
    await db.execute(text("DELETE FROM user_session WHERE user_id = :u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = :u"), {"u": user_id})
    # Строку `users` не трогаем: её удаление упирается в append-only
    # `audit_event`, а тесты и так идут внутри откатываемой транзакции
    # (tsk-333). Тот же порядок, что в `_cleanup` тестов Y-6.2.
    await db.commit()


def _items_by_id(body: dict) -> tuple[dict, dict]:
    """Разложить items ответа на (задания по task_id, материалы по material_id)."""
    tasks = {it["task_id"]: it for it in body["items"] if it["kind"] == "task"}
    materials = {it["material_id"]: it for it in body["items"] if it["kind"] == "material"}
    return tasks, materials


# ────────────────────────── Tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_and_material_fields_present(db, client):
    """Название, тип и uid приходят в самом syllabus-states — веер не нужен."""
    tree = await _make_tree(db)
    user_id, token = await _create_student(db)
    await _enroll(db, user_id, tree["root_id"])
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{tree['root_id']}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        tasks, materials = _items_by_id(resp.json())

        titled = tasks[tree["titled_task_id"]]
        assert titled["title"] == "Своё название задания"
        assert titled["external_uid"] == tree["titled_uid"]
        assert titled["task_type"] == "SA"

        material = materials[tree["material_id"]]
        assert material["title"] == tree["material_title"]
        assert material["material_type"] == "video"
    finally:
        await _drop(db, tree, user_id)


@pytest.mark.asyncio
async def test_stem_preview_only_for_task_without_title(db, client):
    """Запасная подпись — только заданию без своего названия, и без разметки.

    Задание с названием отдаёт `stem_preview = null`: иначе ответ возил бы
    условие всех заданий курса, ради чего веер и затевался.
    """
    tree = await _make_tree(db)
    user_id, token = await _create_student(db, prefix="tsk684-stem")
    await _enroll(db, user_id, tree["root_id"])
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{tree['root_id']}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        tasks, _ = _items_by_id(resp.json())

        untitled = tasks[tree["untitled_task_id"]]
        assert untitled["title"] is None, (
            "пустое название отдаём одной формой — null, а не пустой строкой"
        )
        preview = untitled["stem_preview"]
        assert preview, "заданию без названия нужна запасная подпись"
        assert "<" not in preview and "&quot;" not in preview, f"разметка не снята: {preview!r}"
        assert 'Выведите "Привет!"' in preview

        assert tasks[tree["titled_task_id"]]["stem_preview"] is None, (
            "заданию со своим названием условие не отдаём"
        )
    finally:
        await _drop(db, tree, user_id)


@pytest.mark.asyncio
async def test_no_extra_db_roundtrips_per_item(db, db_engine, client):
    """Сторож tsk-662: задания и материалы дерева читаются одним запросом каждый.

    Названия добавлены В СУЩЕСТВУЮЩИЕ выборки. Если кто-то позже начнёт
    добирать их поштучно или по подкурсам, число обращений к базе вырастет — а
    именно это и было первопричиной заторов. Тест ловит возврат к поштучному
    опросу, которого не увидят ни ответ 200, ни проверка полей выше.
    """
    tree = await _make_tree(db)
    user_id, token = await _create_student(db, prefix="tsk684-count")
    await _enroll(db, user_id, tree["root_id"])

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    event.listen(db_engine.sync_engine, "before_cursor_execute", _record)
    try:
        resp = await client.get(
            f"/api/v1/me/courses/{tree['root_id']}/syllabus-states",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        event.remove(db_engine.sync_engine, "before_cursor_execute", _record)
        await _drop(db, tree, user_id)

    task_reads = [s for s in statements if "attempts_per_task" in s]
    # tsk-692: у правила «добавленное после прохождения — не долг» своя выборка
    # по дереву, и она тоже читает материалы вместе с прогрессом ученика.
    # Сторож здесь про ДРУГОЕ — про возврат к поштучному дозапросу, — поэтому
    # выборки различаются по псевдониму синтабуса, а число обращений правила
    # проверяется отдельной строкой ниже: одно на дерево, не на элемент.
    material_reads = [
        s
        for s in statements
        if "student_material_progress" in s and "m.id AS material_id" in s
    ]
    grace_reads = [s for s in statements if "tsk692-grace-items" in s]
    assert len(task_reads) == 1, f"выборка заданий должна быть одна, стало {len(task_reads)}"
    assert len(material_reads) == 1, (
        f"выборка материалов должна быть одна, стало {len(material_reads)}"
    )
    assert len(grace_reads) <= 1, (
        "правило tsk-692 обязано читать дерево один раз за запрос (кеш на сессию), "
        f"стало {len(grace_reads)}"
    )

    # Ни одной выборки задания или материала ПО ОДНОМУ ID: так выглядел бы
    # возврат к поштучному дозапросу названий.
    per_item = [
        s
        for s in statements
        if ("FROM tasks" in s or "FROM materials" in s) and "WHERE id = " in s.replace("\n", " ")
    ]
    assert not per_item, f"поштучный опрос вернулся: {per_item[:2]}"

    # Шире поштучного: к заданиям и материалам за весь вызов не должно быть
    # обращений сверх двух — выборка самого синтабуса плюс одна выборка правила
    # tsk-692 (она читает дерево целиком одним запросом и кешируется на сессию).
    # Оба числа не зависят от количества элементов и подкурсов, а именно рост по
    # элементам и был первопричиной заторов.
    touch_tasks = [s for s in statements if "FROM tasks" in s]
    touch_materials = [s for s in statements if "FROM materials" in s]
    assert len(touch_tasks) - len(grace_reads) == 1, (
        f"обращений к tasks сверх правила tsk-692 должно быть 1, стало "
        f"{len(touch_tasks) - len(grace_reads)}"
    )
    assert len(touch_materials) - len(grace_reads) == 1, (
        f"обращений к materials сверх правила tsk-692 должно быть 1, стало "
        f"{len(touch_materials) - len(grace_reads)}"
    )

