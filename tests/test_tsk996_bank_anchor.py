"""tsk-996 (фаза D): якорь задания банка на материал прозрачного хозяина.

Пилот 20.10 (курс ОГЭ): задания урока «Задание 1» живут в курсе-банке
(6308 в 1948 «lms:tsk1161:bank:oge1»), подвешенном к узлу урока (1111
«wp:oge-z1») прозрачной связью (tsk-1198). Якорь фазы B на материал урока
(2358) давал 422, и `node_sequence` такой якорь не учитывал. Здесь — что
теперь:

* задание банка с якорем на материал хозяина встаёт в голову хозяина сразу
  после материала, в одной группе с заданиями самого хозяина, внутри группы —
  по `task_key` (тот же ключ, что сливает банк и хозяина в хвосте);
* цепочка next-item по хозяину проходит каждый элемент ровно один раз, и
  курсор от любого элемента даёт следующий;
* программа курса, карточка прогресса, предпросмотр и «ушёл вперёд» — тот
  же порядок; «N из M» не меняется;
* в практикуме (второй родитель банка без прозрачности) якорь на чужой
  материал игнорируется — порядок практикума прежний;
* страж отклоняет материал непрозрачного родителя и чужого курса; снятие
  прозрачности, удаление связи и перенос родителя обнуляют якоря на материалы
  бывшего хозяина; удаление курсов каскадом не падает;
* API: PATCH принимает материал хозяина, 422 на прочие; список вариантов для
  редактора включает материалы хозяина; bulk-upsert резолвит
  `anchor_external_uid` и среди материалов хозяина;
* при `NODE_SEQUENCE_ENABLED=false` — прежний порядок.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.repos.courses_repo import CoursesRepository
from app.services import me_service
from app.services.content_grace_service import compute_graced_items
from app.services.learning_engine_service import LearningEngineService
from tests.test_tsk996_anchor import (
    M,
    T,
    _anchor,
    _anchor_row,
    _assert_once,
    _chain_items,
    _next_after,
    _preview_chain,
    _progress_rows,
    _syllabus_section,
    _user_with_session,
)
from tests.test_tsk996_node_sequence import (
    INSERTED,
    LONG_AGO,
    _complete_material,
    _course,
    _link,
    _material,
    _reset_caches,
    _result,
    _student,
    _task,
)
from tests.test_tsk996_publish_anchor import _api_key, _publish_materials, _task_item


# ─── Форма: урок + банк (прозрачный) + практикум ────────────────────────────


async def shape_bank(db) -> dict[str, Any]:
    """Урок L: m1 m2 m3, свои задания lt1(п1)→m1, lt2(п2).

    Банк B — прозрачный подкурс L и обычный раздел практикума P: материал bm,
    задания b1(п1, место 1)→m1, b2(п2, место 2), b3(п3, место 1 ранг 1)→m2.
    У практикума свой материал pm (якорь на него банку недопустим).

    Ожидаемый порядок урока: m1 b1 lt1 m2 b3 m3 | b2 lt2 — в группе m1 b1
    перед lt1 по месту хозяина (как в хвосте), в хвосте b2 перед lt2.
    """
    lesson = await _course(db, "банк урок")
    bank = await _course(db, "банк задания")
    prak = await _course(db, "банк практикум")
    await _link(db, lesson, bank, 1, transparent=True)
    await _link(db, prak, bank, 1)
    m1 = await _material(db, lesson, 1)
    m2 = await _material(db, lesson, 2)
    m3 = await _material(db, lesson, 3)
    lt1 = await _task(db, lesson, 1)
    lt2 = await _task(db, lesson, 2)
    bm = await _material(db, bank, 1)
    b1 = await _task(db, bank, 1, host_position=100)
    b2 = await _task(db, bank, 2, host_position=200)
    b3 = await _task(db, bank, 3, host_position=101)
    pm = await _material(db, prak, 1)
    await _anchor(db, lt1, m1)
    await _anchor(db, b1, m1)
    await _anchor(db, b3, m2, source="publish")
    student = await _student(db, lesson, prak)
    return dict(
        lesson=lesson, bank=bank, prak=prak, m1=m1, m2=m2, m3=m3, lt1=lt1, lt2=lt2,
        bm=bm, b1=b1, b2=b2, b3=b3, pm=pm, student=student,
    )


def _expected_lesson(s: dict[str, Any]) -> list[tuple[str, int]]:
    return [
        M(s["m1"]), T(s["b1"]), T(s["lt1"]), M(s["m2"]), T(s["b3"]), M(s["m3"]),
        T(s["b2"]), T(s["lt2"]),
    ]


def _expected_prak(s: dict[str, Any]) -> list[tuple[str, int]]:
    return [M(s["pm"]), M(s["bm"]), T(s["b1"]), T(s["b2"]), T(s["b3"])]


async def _clear_bank_anchors(db, s: dict[str, Any]) -> None:
    await db.execute(
        text("UPDATE tasks SET anchor_material_id = NULL, anchor_source = NULL "
             "WHERE course_id = :b"),
        {"b": s["bank"]},
    )
    await db.commit()


# ─── Порядок урока ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lesson_chain_each_item_once(db, monkeypatch):
    """Цепочка next-item по уроку: задания банка за материалом хозяина, каждый раз ровно один."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_bank(db)
    await db.commit()
    _assert_once(await _chain_items(db, s["student"], s["lesson"]), _expected_lesson(s))


@pytest.mark.asyncio
async def test_lesson_cursor_from_every_item(db, monkeypatch):
    """next-item от каждого элемента урока (и от заданий банка) — следующий по порядку."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_bank(db)
    await db.commit()
    expected = _expected_lesson(s)
    for pos, nxt in zip(expected, [*expected[1:], None]):
        assert await _next_after(db, s["student"], s["lesson"], pos) == nxt, pos


@pytest.mark.asyncio
async def test_lesson_all_places_follow_bank_anchor(db, monkeypatch):
    """Программа курса, карточка прогресса, предпросмотр — тот же порядок урока."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_bank(db)
    await db.commit()
    # Программа курса показывает и материал банка в списке хозяина (tsk-1198:
    # группой по курсу после материалов хозяина) — якорные задания при своих.
    assert await _syllabus_section(db, s["student"], s["lesson"], s["lesson"]) == [
        M(s["m1"]), T(s["b1"]), T(s["lt1"]), M(s["m2"]), T(s["b3"]), M(s["m3"]),
        M(s["bm"]), T(s["b2"]), T(s["lt2"]),
    ]
    rows = await _progress_rows(db, s["student"], s["lesson"])
    assert rows == [
        ("course", s["lesson"]),
        ("material", s["m1"]), ("task", s["b1"]), ("task", s["lt1"]),
        ("material", s["m2"]), ("task", s["b3"]), ("material", s["m3"]),
        ("material", s["bm"]), ("task", s["b2"]), ("task", s["lt2"]),
    ]
    expected = _expected_lesson(s)
    assert await _preview_chain(db, s["lesson"], expected[0]) == expected


@pytest.mark.asyncio
async def test_lesson_with_only_bank_anchors(db, monkeypatch):
    """У урока якоря только у заданий банка (как на пилоте ОГЭ): голова всё равно их видит.

    Признак «у узла есть якоря» (задания головы читаются только при нём)
    обязан учитывать якоря заданий прозрачного подкурса, иначе задания банка
    молча остались бы в хвосте.
    """
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_bank(db)
    await _anchor(db, s["lt1"], None)
    await db.commit()
    expected = [
        M(s["m1"]), T(s["b1"]), M(s["m2"]), T(s["b3"]), M(s["m3"]),
        T(s["lt1"]), T(s["b2"]), T(s["lt2"]),
    ]
    _assert_once(await _chain_items(db, s["student"], s["lesson"]), expected)
    for pos, nxt in zip(expected, [*expected[1:], None]):
        assert await _next_after(db, s["student"], s["lesson"], pos) == nxt, pos
    assert await _preview_chain(db, s["lesson"], expected[0]) == expected
    assert await _syllabus_section(db, s["student"], s["lesson"], s["lesson"]) == [
        M(s["m1"]), T(s["b1"]), M(s["m2"]), T(s["b3"]), M(s["m3"]),
        M(s["bm"]), T(s["lt1"]), T(s["b2"]), T(s["lt2"]),
    ]


@pytest.mark.asyncio
async def test_practicum_order_unchanged(db, monkeypatch):
    """Практикум (второй родитель без прозрачности): якорь на материал урока игнорируется."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_bank(db)
    await db.commit()
    expected = _expected_prak(s)
    _assert_once(await _chain_items(db, s["student"], s["prak"]), expected)
    for pos, nxt in zip(expected, [*expected[1:], None]):
        assert await _next_after(db, s["student"], s["prak"], pos) == nxt, pos
    syllabus = await _syllabus_section(db, s["student"], s["prak"], s["bank"])
    assert syllabus == [M(s["bm"]), T(s["b1"]), T(s["b2"]), T(s["b3"])]
    assert await _preview_chain(db, s["prak"], expected[0]) == expected
    progress = await _progress_rows(db, s["student"], s["prak"])

    # Без якорей у банка — то же самое: якорь на материал урока практикум не видит.
    await _clear_bank_anchors(db, s)
    _assert_once(await _chain_items(db, s["student"], s["prak"]), expected)
    assert await _syllabus_section(db, s["student"], s["prak"], s["bank"]) == syllabus
    assert await _progress_rows(db, s["student"], s["prak"]) == progress


@pytest.mark.asyncio
async def test_switch_off_restores_previous_order(db, monkeypatch):
    """NODE_SEQUENCE_ENABLED=false — прежний порядок урока: банк в хвосте по местам."""
    s = await shape_bank(db)
    await db.commit()
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "false")
    legacy = [
        M(s["m1"]), M(s["m2"]), M(s["m3"]),
        T(s["b1"]), T(s["b3"]), T(s["lt1"]), T(s["b2"]), T(s["lt2"]),
    ]
    _assert_once(await _chain_items(db, s["student"], s["lesson"]), legacy)
    assert await _preview_chain(db, s["lesson"], legacy[0]) == legacy


@pytest.mark.asyncio
async def test_without_bank_anchors_lesson_is_tail_as_before(db, monkeypatch):
    """Без якорей у банка задания банка — в хвосте урока на местах хозяина (как до фазы D)."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_bank(db)
    await db.commit()
    await _clear_bank_anchors(db, s)
    _assert_once(
        await _chain_items(db, s["student"], s["lesson"]),
        [
            M(s["m1"]), T(s["lt1"]), M(s["m2"]), M(s["m3"]),
            T(s["b1"]), T(s["b3"]), T(s["b2"]), T(s["lt2"]),
        ],
    )


@pytest.mark.asyncio
async def test_progress_counts_do_not_depend_on_bank_anchors(db, monkeypatch):
    """«N из M», состояние курса и статусы программы — те же с якорями банка и без."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_bank(db)
    student = s["student"]
    await _complete_material(db, student, s["m1"])
    await _result(db, student, s["b1"], s["lesson"], s["lesson"])
    await _result(db, student, s["lt2"], s["lesson"], s["lesson"])
    await db.commit()
    engine = LearningEngineService()

    async def snapshot() -> tuple:
        _reset_caches(db)
        out = []
        for root in (s["lesson"], s["prak"]):
            state = await engine.compute_course_state(db, student, root)
            syl = await me_service.get_syllabus_states(db, user_id=student, root_course_id=root)
            statuses = sorted(
                (i["kind"], i.get("material_id") or i.get("task_id"), i["status"])
                for i in syl["items"]
            )
            out.append((state.state, statuses))
        courses = sorted(
            (c.get("course_id", c.get("id")), repr(sorted(c.items())))
            for c in await me_service.get_courses_with_progress(db, student)
        )
        return tuple(out), courses

    with_anchors = await snapshot()
    await _clear_bank_anchors(db, s)
    assert await snapshot() == with_anchors


@pytest.mark.asyncio
async def test_grace_places_bank_task_after_host_material(db, monkeypatch):
    """«Ушёл вперёд» видит задание банка там же, где ученик: за материалом урока.

    x — новое задание банка (создано позже), y — старое, давно решённое, z —
    старое нерешённое (узел банка не закрыт целиком, правило узла молчит). Без
    якоря x стоит после y в разделе банка, за ним только нерешённое z —
    прощать нечего. С якорем на m1 x в голове урока, раньше раздела банка: y
    решено до появления x — x позади ученика.
    """
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    lesson = await _course(db, "банк grace урок")
    bank = await _course(db, "банк grace банк")
    await _link(db, lesson, bank, 1, transparent=True)
    m1 = await _material(db, lesson, 1)
    m2 = await _material(db, lesson, 2)
    y = await _task(db, bank, 1, host_position=100)
    student = await _student(db, lesson)
    await _result(db, student, y, lesson, lesson, at=LONG_AGO + timedelta(days=3))
    x = await _task(db, bank, 2, host_position=200, created_at=INSERTED)
    z = await _task(db, bank, 3, host_position=300)
    await db.commit()

    async def graced() -> set[int]:
        _reset_caches(db)
        return set((await compute_graced_items(db, student, lesson)).tasks)

    assert x not in await graced()
    await _anchor(db, x, m1)
    await db.commit()
    assert x in await graced()
    # Прощённое — не долг: цепочка урока его пропускает.
    _assert_once(await _chain_items(db, student, lesson), [M(m1), M(m2), T(z)])
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "false")
    assert x not in await graced()


# ─── Страж и обнуление ──────────────────────────────────────────────────────


def _is_guard_error(exc: DBAPIError) -> bool:
    return "check_violation" in repr(exc.orig) or "tsk-996" in str(exc)


@pytest.mark.asyncio
async def test_guard_accepts_transparent_host_only(db):
    """Страж: материал прозрачного хозяина — да; непрозрачного родителя и чужого курса — нет."""
    s = await shape_bank(db)
    stranger = await _course(db, "банк чужой")
    sm = await _material(db, stranger, 1)
    await db.commit()
    await db.execute(
        text("UPDATE tasks SET anchor_material_id = :m WHERE id = :t"),
        {"m": s["m3"], "t": s["b2"]},
    )
    for bad in (s["pm"], sm):
        for stmt, params in (
            ("UPDATE tasks SET anchor_material_id = :m WHERE id = :t",
             {"m": bad, "t": s["b2"]}),
            ("INSERT INTO tasks (task_content, course_id, difficulty_id, anchor_material_id) "
             "VALUES (CAST('{\"type\": \"SA\", \"stem\": \"x\"}' AS jsonb), :c, 1, :m)",
             {"c": s["bank"], "m": bad}),
        ):
            nested = await db.begin_nested()
            with pytest.raises(DBAPIError) as exc:
                await db.execute(text(stmt), params)
            await nested.rollback()
            assert _is_guard_error(exc.value), exc.value
    # Обратное направление не разрешено: задание хозяина на материал банка.
    nested = await db.begin_nested()
    with pytest.raises(DBAPIError) as exc:
        await db.execute(
            text("UPDATE tasks SET anchor_material_id = :m WHERE id = :t"),
            {"m": s["bm"], "t": s["lt2"]},
        )
    await nested.rollback()
    assert _is_guard_error(exc.value), exc.value
    assert await _anchor_row(db, s["b2"]) == (s["m3"], None)


@pytest.mark.asyncio
async def test_unset_transparency_clears_host_anchors(db, monkeypatch):
    """Снятие прозрачности: якоря банка на материалы урока обнулены, свой якорь банка цел."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_bank(db)
    await _anchor(db, s["b2"], s["bm"])
    await db.commit()
    await db.execute(
        text("UPDATE course_parents SET is_transparent = false "
             "WHERE course_id = :b AND parent_course_id = :l"),
        {"b": s["bank"], "l": s["lesson"]},
    )
    await db.commit()
    assert await _anchor_row(db, s["b1"]) == (None, None)
    assert await _anchor_row(db, s["b3"]) == (None, None)
    assert await _anchor_row(db, s["b2"]) == (s["bm"], "manual")
    assert await _anchor_row(db, s["lt1"]) == (s["m1"], "manual")
    # Теперь банк в уроке — обычный раздел: m1 lt1 m2 m3 | bm b2 b1 b3 | lt2.
    _assert_once(
        await _chain_items(db, s["student"], s["lesson"]),
        [
            M(s["m1"]), T(s["lt1"]), M(s["m2"]), M(s["m3"]),
            M(s["bm"]), T(s["b2"]), T(s["b1"]), T(s["b3"]),
            T(s["lt2"]),
        ],
    )


@pytest.mark.asyncio
async def test_other_link_updates_keep_anchors(db):
    """Правка порядка связи и прозрачность «true → true» якоря не трогают."""
    s = await shape_bank(db)
    await db.commit()
    await db.execute(
        text("UPDATE course_parents SET order_number = 5, is_transparent = true "
             "WHERE course_id = :b AND parent_course_id = :l"),
        {"b": s["bank"], "l": s["lesson"]},
    )
    await db.execute(
        text("UPDATE course_parents SET is_transparent = false "
             "WHERE course_id = :b AND parent_course_id = :p"),
        {"b": s["bank"], "p": s["prak"]},
    )
    await db.commit()
    assert await _anchor_row(db, s["b1"]) == (s["m1"], "manual")
    assert await _anchor_row(db, s["b3"]) == (s["m2"], "publish")


@pytest.mark.asyncio
async def test_delete_link_clears_host_anchors(db):
    """Удаление прозрачной связи (отвязка банка от урока) обнуляет якоря на материалы урока."""
    s = await shape_bank(db)
    await db.commit()
    await db.execute(
        text("DELETE FROM course_parents WHERE course_id = :b AND parent_course_id = :l"),
        {"b": s["bank"], "l": s["lesson"]},
    )
    await db.commit()
    assert await _anchor_row(db, s["b1"]) == (None, None)
    assert await _anchor_row(db, s["b3"]) == (None, None)
    assert await _anchor_row(db, s["lt1"]) == (s["m1"], "manual")


@pytest.mark.asyncio
async def test_parent_swap_clears_host_anchors(db):
    """Смена родителя (`set_parent_courses` переносит связь UPDATE-ом) — якоря на бывшего хозяина обнулены."""
    s = await shape_bank(db)
    other = await _course(db, "банк другой урок")
    await db.commit()
    await CoursesRepository().set_parent_courses(
        db, s["bank"], parent_course_ids=[other, s["prak"]], replace=True
    )
    links = (
        await db.execute(
            text("SELECT parent_course_id FROM course_parents WHERE course_id = :b"),
            {"b": s["bank"]},
        )
    ).scalars().all()
    assert sorted(links) == sorted([other, s["prak"]])
    assert await _anchor_row(db, s["b1"]) == (None, None)
    assert await _anchor_row(db, s["b3"]) == (None, None)


@pytest.mark.asyncio
async def test_material_move_clears_bank_anchors(db):
    """Материал урока уехал в другой курс — якорь банка на него обнулён."""
    s = await shape_bank(db)
    other = await _course(db, "банк куда уехал материал")
    await db.commit()
    await db.execute(
        text("UPDATE materials SET course_id = :c WHERE id = :m"), {"c": other, "m": s["m1"]}
    )
    await db.commit()
    assert await _anchor_row(db, s["b1"]) == (None, None)
    assert await _anchor_row(db, s["lt1"]) == (None, None)
    assert await _anchor_row(db, s["b3"]) == (s["m2"], "publish")


@pytest.mark.asyncio
async def test_material_move_into_host_keeps_bank_anchor(db, monkeypatch):
    """Материал банка переехал в урок-хозяин — якорь задания банка на него остаётся (допустим)."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_bank(db)
    await _anchor(db, s["b2"], s["bm"])
    await db.commit()
    await db.execute(
        text("UPDATE materials SET course_id = :c, order_position = 4 WHERE id = :m"),
        {"c": s["lesson"], "m": s["bm"]},
    )
    await db.commit()
    assert await _anchor_row(db, s["b2"]) == (s["bm"], "manual")
    _assert_once(
        await _chain_items(db, s["student"], s["lesson"]),
        [
            M(s["m1"]), T(s["b1"]), T(s["lt1"]), M(s["m2"]), T(s["b3"]), M(s["m3"]),
            M(s["bm"]), T(s["b2"]), T(s["lt2"]),
        ],
    )


@pytest.mark.asyncio
async def test_task_move_clears_host_anchor(db):
    """Переезд задания банка в другой курс — якорь обнулён, как в фазе B."""
    s = await shape_bank(db)
    other = await _course(db, "банк куда уехало задание")
    await db.commit()
    await db.execute(
        text("UPDATE tasks SET course_id = :c WHERE id = :t"), {"c": other, "t": s["b1"]}
    )
    await db.commit()
    assert await _anchor_row(db, s["b1"]) == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["lesson", "bank"])
async def test_course_delete_with_bank_anchors(db, which):
    """Удаление урока или банка каскадом не падает на триггере связи."""
    s = await shape_bank(db)
    await db.commit()
    await db.execute(text("DELETE FROM courses WHERE id = :c"), {"c": s[which]})
    await db.commit()
    if which == "lesson":
        assert await _anchor_row(db, s["b1"]) == (None, None)
        assert await _anchor_row(db, s["b2"]) == (None, None)


# ─── API ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_patch_accepts_host_material(db, client):
    """PATCH: материал прозрачного хозяина — 200 (manual); практикума и чужой — 422."""
    s = await shape_bank(db)
    stranger = await _course(db, "банк чужой API")
    sm = await _material(db, stranger, 1)
    token = await _user_with_session(db, "methodist")
    auth = {"Authorization": f"Bearer {token}"}
    url = f"/api/v1/tasks/{s['b2']}"

    r = await client.patch(url, json={"anchor_material_id": s["m3"]}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["anchor_material_id"] == s["m3"]
    assert await _anchor_row(db, s["b2"]) == (s["m3"], "manual")

    for bad in (s["pm"], sm):
        r = await client.patch(url, json={"anchor_material_id": bad}, headers=auth)
        assert r.status_code == 422, r.text
        assert "другого раздела" in r.text
    assert await _anchor_row(db, s["b2"]) == (s["m3"], "manual")

    # Свой материал банка по-прежнему допустим.
    r = await client.patch(url, json={"anchor_material_id": s["bm"]}, headers=auth)
    assert r.status_code == 200, r.text
    # Задание хозяина на материал банка — нет (прозрачность в одну сторону).
    r = await client.patch(
        f"/api/v1/tasks/{s['lt2']}", json={"anchor_material_id": s["bm"]}, headers=auth
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_api_anchor_materials_lists_host(db, client):
    """Варианты якоря: материалы раздела задания, затем прозрачного хозяина, с подписью."""
    s = await shape_bank(db)
    await db.execute(
        text("UPDATE materials SET is_active = false WHERE id = :m"), {"m": s["m2"]}
    )
    token = await _user_with_session(db, "methodist")
    auth = {"Authorization": f"Bearer {token}"}

    r = await client.get(f"/api/v1/tasks/{s['b1']}/anchor-materials", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_id"] == s["b1"] and body["course_id"] == s["bank"]
    got = [(i["id"], i["course_id"], i["origin"], i["is_active"]) for i in body["items"]]
    assert got == [
        (s["bm"], s["bank"], "own", True),
        (s["m1"], s["lesson"], "host", True),
        (s["m2"], s["lesson"], "host", False),
        (s["m3"], s["lesson"], "host", True),
    ]
    assert body["items"][1]["course_title"].endswith("банк урок")
    assert s["pm"] not in {i["id"] for i in body["items"]}

    # Задание урока — только свои материалы (у урока прозрачного хозяина нет).
    r = await client.get(f"/api/v1/tasks/{s['lt1']}/anchor-materials", headers=auth)
    assert [i["id"] for i in r.json()["items"]] == [s["m1"], s["m2"], s["m3"]]
    assert {i["origin"] for i in r.json()["items"]} == {"own"}

    assert (await client.get("/api/v1/tasks/2000000000/anchor-materials", headers=auth)
            ).status_code == 404
    assert (await client.get(f"/api/v1/tasks/{s['b1']}/anchor-materials")).status_code == 401
    student_token = await _user_with_session(db, "student")
    r = await client.get(
        f"/api/v1/tasks/{s['b1']}/anchor-materials",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_bulk_upsert_resolves_host_material(db, client):
    """bulk-upsert: `anchor_external_uid` материала урока ставит якорь заданию банка (publish).

    Свой курс — первым: материал с тем же uid в банке выигрывает у урока.
    Материал только у практикума (непрозрачный родитель) — предупреждение.
    Ручной якорь переиздание не трогает.
    """
    lesson = await _course(db, "банк публикация урок")
    bank = await _course(db, "банк публикация банк")
    prak = await _course(db, "банк публикация практикум")
    await _link(db, lesson, bank, 1, transparent=True)
    await _link(db, prak, bank, 1)
    await db.commit()
    base = f"wp:tsk996d:{uuid4().hex[:8]}"
    lesson_uids = await _publish_materials(client, lesson, base, 2)
    prak_uids = await _publish_materials(client, prak, f"{base}:prak", 1)
    both_uid = (await _publish_materials(client, bank, base, 1))[0]  # тот же uid, что m1 урока
    mat = {
        (int(c), uid): int(i)
        for i, c, uid in (
            await db.execute(
                text("SELECT id, course_id, external_uid FROM materials "
                     "WHERE course_id = ANY(:c)"),
                {"c": [lesson, bank, prak]},
            )
        ).fetchall()
    }
    uid_host, uid_own, uid_prak, uid_manual = (f"{base}:t{i}" for i in range(4))
    items = [
        _task_item(uid_host, bank, anchor_external_uid=lesson_uids[1]),
        _task_item(uid_own, bank, anchor_external_uid=both_uid),
        _task_item(uid_prak, bank, anchor_external_uid=prak_uids[0]),
        _task_item(uid_manual, bank),
    ]
    r = await client.post("/api/v1/tasks/bulk-upsert", params=_api_key(), json={"items": items})
    assert r.status_code == 200, r.text
    warnings = r.json()["warnings"]
    assert len(warnings) == 1 and uid_prak in warnings[0], warnings
    ids = {
        uid: int(i)
        for uid, i in (
            await db.execute(
                text("SELECT external_uid, id FROM tasks WHERE external_uid = ANY(:u)"),
                {"u": [uid_host, uid_own, uid_prak, uid_manual]},
            )
        ).fetchall()
    }
    assert await _anchor_row(db, ids[uid_host]) == (mat[(lesson, lesson_uids[1])], "publish")
    assert await _anchor_row(db, ids[uid_own]) == (mat[(bank, both_uid)], "publish")
    assert await _anchor_row(db, ids[uid_prak]) == (None, None)

    # Ручной якорь на материал урока переиздание с другим якорем не трогает.
    await _anchor(db, ids[uid_manual], mat[(lesson, lesson_uids[0])], source="manual")
    await db.commit()
    items[3] = _task_item(uid_manual, bank, anchor_external_uid=lesson_uids[1])
    r = await client.post("/api/v1/tasks/bulk-upsert", params=_api_key(), json={"items": items})
    assert r.status_code == 200, r.text
    assert await _anchor_row(db, ids[uid_manual]) == (mat[(lesson, lesson_uids[0])], "manual")


@pytest.mark.asyncio
async def test_two_transparent_parents_only_first_host(db, client):
    """Ошибка разметки — банк прозрачен для двух уроков: якорь допустим только у первого.

    Порядок узла берёт первого хозяина (`transparent_links`); якорь на материал
    второго ученик не увидел бы — PATCH и список вариантов его не предлагают.
    """
    s = await shape_bank(db)
    second = await _course(db, "банк второй хозяин")
    await _link(db, second, s["bank"], 2, transparent=True)
    sm = await _material(db, second, 1)
    token = await _user_with_session(db, "methodist")
    auth = {"Authorization": f"Bearer {token}"}
    r = await client.get(f"/api/v1/tasks/{s['b2']}/anchor-materials", headers=auth)
    assert {i["course_id"] for i in r.json()["items"]} == {s["bank"], s["lesson"]}
    r = await client.patch(
        f"/api/v1/tasks/{s['b2']}", json={"anchor_material_id": sm}, headers=auth
    )
    assert r.status_code == 422, r.text
