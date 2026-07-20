"""Штатная правка прогресса ученика преподавателем (tsk-297).

Проверяем на НАСТОЯЩЕЙ БД (не на моках): согласованность зачёта с учебным
движком живёт в SQL — рекурсивные CTE дерева, JOIN попытки с результатом,
предикат очереди проверки. Мок этого не воспроизводит.

Ключевые инварианты:
* зачёт делает задание PASSED и next-item больше его не выдаёт;
* зачёт НЕ расходует лимит попыток ученика (суть ``root_course_id=NULL``);
* снятие возвращает задание в OPEN, строки не удаляются;
* повторный зачёт идемпотентен (``already=True``, второй попытки нет);
* зачтённое SA_COM не падает в очередь ручной проверки (``checked_at`` заполнен);
* teacher правит только своих; methodist/admin — bypass;
* массовая операция покрывает всё поддерево и идемпотентна;
* снятие отметки материала не трогает строку с ``source='system'``;
* КВИЗ (``SC_Qw``/``MC_Qw``) вручную зачесть нельзя — 422 на единичной операции,
  пропуск в массовой; снятие при этом остаётся разрешённым (находка ревью S3-2).

Граф фикстуры:
    root ──> child
    root: task_root_a, task_root_b (SA), task_quiz (SC_Qw), material_root
    child: task_child (SA_COM с ручной проверкой), material_child
"""
from __future__ import annotations

import json
import random

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.models.users import Users
from app.services import (
    learning_events_service,
    manual_progress_service,
    teacher_queue_service,
)
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.services.learning_engine_service import LearningEngineService

engine_svc = LearningEngineService()

_TAG = "tsk297"


async def _answer_quiz(
    db, student_id: int, task_id: int, scale_scores: dict | None = None
) -> int:
    """Реальный ответ ученика на квиз: попытка + результат со шкалами.

    Именно то, что ручной зачёт подделать НЕ может (у него нет `scale_scores`) и
    ради чего квиз закрыт от зачёта. Пишем напрямую в БД, а не через
    `POST /attempts/{id}/answers`: тесту нужен факт результата, а не тракт приёма.
    """
    course_id = (
        await db.execute(text("SELECT course_id FROM tasks WHERE id = :t"), {"t": task_id})
    ).scalar()
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :c, 'test') RETURNING id"
            ),
            {"u": student_id, "c": course_id},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, checked_at, "
            "  source_system, scale_scores) "
            "VALUES (:u, :t, :a, 10, 10, true, now(), now(), 0, now(), 'test', "
            "        CAST(:ss AS jsonb))"
        ),
        {
            "u": student_id,
            "t": task_id,
            "a": attempt_id,
            "ss": json.dumps(scale_scores or {"информатика": 2, "python": 0}),
        },
    )
    return int(attempt_id)


async def _submit_result(
    db, student_id: int, task_id: int, *, score: int, max_score: int = 10,
) -> int:
    """Реальный результат по заданию (не квиз): попытка + task_result напрямую в БД.

    Как `_answer_quiz`, но без `scale_scores` — для SA/SA_COM заданий в тесте
    эквивалентности batch/поэлементного расчёта (review tsk-297, находка S3-3).
    """
    course_id = (
        await db.execute(text("SELECT course_id FROM tasks WHERE id = :t"), {"t": task_id})
    ).scalar()
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :c, 'test') RETURNING id"
            ),
            {"u": student_id, "c": course_id},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, checked_at, source_system) "
            "VALUES (:u, :t, :a, :sc, :mx, :ok, now(), now(), 0, now(), 'test')"
        ),
        {
            "u": student_id, "t": task_id, "a": attempt_id,
            "sc": score, "mx": max_score, "ok": score >= max_score,
        },
    )
    return int(attempt_id)


async def _new_user(db, role: str | None, name: str) -> tuple[int, str]:
    """Создать пользователя с сессией и (опционально) ролью."""
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
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


@pytest.fixture
async def graph(db):
    """Учебный граф + ученик + преподаватели. Полная уборка за собой."""
    ids: dict[str, int] = {}
    try:
        async def new_course(title: str) -> int:
            return (
                await db.execute(
                    text(
                        "INSERT INTO courses (title, access_level) "
                        "VALUES (:t, 'self_guided') RETURNING id"
                    ),
                    {"t": title},
                )
            ).scalar()

        ids["root"] = await new_course(f"{_TAG} корень")
        ids["child"] = await new_course(f"{_TAG} подкурс")
        await db.execute(
            text(
                "INSERT INTO course_parents (course_id, parent_course_id) VALUES (:c, :p)"
            ),
            {"c": ids["child"], "p": ids["root"]},
        )

        difficulty_id = (
            await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
        ).scalar()
        assert difficulty_id is not None, "нет difficulties — граф не собрать"

        async def new_task(
            course_id: int, uid: str, *, type_: str = "SA", manual: bool = False,
            order_position: int = 1,
        ) -> int:
            rules: dict = {"max_score": 10}
            if manual:
                rules["manual_review_required"] = True
            content: dict = {"type": type_, "stem": f"{_TAG} условие"}
            if type_ in ("SC_Qw", "MC_Qw"):
                # Квиз описываем как настоящий (варианты + шкалы), а не одним
                # полем type: гейт смотрит только на тип, но тест не должен
                # опираться на заведомо невалидное по `TaskContent` задание.
                content["scales"] = ["информатика", "python"]
                content["options"] = [
                    {"id": "a", "text": "вариант А", "scores": {"информатика": 2, "python": 0}},
                    {"id": "b", "text": "вариант Б", "scores": {"информатика": 0, "python": 2}},
                ]
                rules["quiz"] = {"mode": "single" if type_ == "SC_Qw" else "multiple"}
            return (
                await db.execute(
                    text(
                        "INSERT INTO tasks (task_content, solution_rules, course_id, "
                        "difficulty_id, external_uid, max_score, order_position) "
                        "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, "
                        ":uid, 10, :op) RETURNING id"
                    ),
                    {
                        "tc": json.dumps(content),
                        "sr": json.dumps(rules),
                        "cid": course_id,
                        "did": difficulty_id,
                        "uid": f"{_TAG}-{uid}-{random.randint(10**8, 10**10)}",
                        "op": order_position,
                    },
                )
            ).scalar()

        async def new_material(course_id: int, title: str, order_position: int = 1) -> int:
            return (
                await db.execute(
                    text(
                        "INSERT INTO materials (course_id, title, type, content, "
                        "order_position) VALUES (:cid, :t, 'text', "
                        "CAST(:c AS jsonb), :op) RETURNING id"
                    ),
                    {
                        "cid": course_id,
                        "t": title,
                        "c": json.dumps({"body": f"{_TAG}"}),
                        "op": order_position,
                    },
                )
            ).scalar()

        ids["task_child"] = await new_task(
            ids["child"], "child", type_="SA_COM", manual=True, order_position=1
        )
        ids["task_root_a"] = await new_task(ids["root"], "root-a", order_position=1)
        ids["task_root_b"] = await new_task(ids["root"], "root-b", order_position=2)
        ids["task_quiz"] = await new_task(
            ids["root"], "quiz", type_="SC_Qw", order_position=3
        )
        ids["material_child"] = await new_material(ids["child"], f"{_TAG} материал подкурса")
        ids["material_root"] = await new_material(ids["root"], f"{_TAG} материал корня")

        student_id, _ = await _new_user(db, "student", "stud")
        teacher_id, teacher_token = await _new_user(db, "teacher", "teach")
        other_id, other_token = await _new_user(db, "teacher", "other")
        methodist_id, methodist_token = await _new_user(db, "methodist", "met")
        ids["student"] = student_id
        ids["teacher"] = teacher_id
        ids["other"] = other_id
        ids["methodist"] = methodist_id

        await db.execute(
            text(
                "INSERT INTO student_teacher_links (student_id, teacher_id) "
                "VALUES (:s, :t) ON CONFLICT DO NOTHING"
            ),
            {"s": student_id, "t": teacher_id},
        )
        await db.execute(
            text(
                "INSERT INTO user_courses (user_id, course_id, is_active) "
                "VALUES (:u, :c, true)"
            ),
            {"u": student_id, "c": ids["root"]},
        )
        await db.commit()

        yield {
            "ids": ids,
            "db": db,
            "tokens": {
                "teacher": teacher_token,
                "other": other_token,
                "methodist": methodist_token,
            },
        }
    finally:
        await db.rollback()
        user_ids = [ids.get(k) for k in ("student", "teacher", "other", "methodist") if k in ids]
        task_ids = [
            ids[k]
            for k in ("task_child", "task_root_a", "task_root_b", "task_quiz")
            if k in ids
        ]
        material_ids = [ids[k] for k in ("material_child", "material_root") if k in ids]
        course_ids = [ids[k] for k in ("root", "child") if k in ids]
        if user_ids:
            await db.execute(
                text("DELETE FROM task_results WHERE user_id = ANY(:u)"), {"u": user_ids}
            )
            await db.execute(
                text("DELETE FROM attempts WHERE user_id = ANY(:u)"), {"u": user_ids}
            )
            await db.execute(
                text("DELETE FROM student_material_progress WHERE student_id = ANY(:u)"),
                {"u": user_ids},
            )
            await db.execute(
                text("DELETE FROM student_task_progress WHERE student_id = ANY(:u)"),
                {"u": user_ids},
            )
            await db.execute(
                text("DELETE FROM student_course_state WHERE student_id = ANY(:u)"),
                {"u": user_ids},
            )
            await db.execute(
                text("DELETE FROM user_courses WHERE user_id = ANY(:u)"), {"u": user_ids}
            )
            await db.execute(
                text(
                    "DELETE FROM student_teacher_links "
                    "WHERE student_id = ANY(:u) OR teacher_id = ANY(:u)"
                ),
                {"u": user_ids},
            )
            await db.execute(
                text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": user_ids}
            )
            await db.execute(
                text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": user_ids}
            )
            await db.execute(
                text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": user_ids}
            )
        if task_ids:
            await db.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": task_ids})
        if material_ids:
            await db.execute(
                text("DELETE FROM materials WHERE id = ANY(:m)"), {"m": material_ids}
            )
        if course_ids:
            await db.execute(
                text("DELETE FROM course_parents WHERE course_id = ANY(:c)"), {"c": course_ids}
            )
        # Сами `users` не удаляем: FK `audit_event.user_id` — SET NULL, а таблица
        # append-only (триггер `audit_event_no_modify`). Пользователей с
        # `@example.com` подбирает session-scoped sweep из conftest, который
        # умеет временно снимать триггер.
        if course_ids:
            await db.execute(text("DELETE FROM courses WHERE id = ANY(:c)"), {"c": course_ids})
        await db.commit()


# ─── Согласованность с учебным движком ──────────────────────────────────────


async def test_grant_makes_task_passed(graph):
    """Зачёт задания переводит его в PASSED в глазах движка."""
    ids, db = graph["ids"], graph["db"]
    before = await engine_svc.compute_task_state(db, ids["student"], ids["task_root_a"])
    assert before.state == "OPEN"

    res = await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], granted_by=ids["teacher"]
    )
    await db.commit()
    assert res["granted"] is True and res["already"] is False

    after = await engine_svc.compute_task_state(db, ids["student"], ids["task_root_a"])
    assert after.state == "PASSED", "зачёт обязан быть виден движку как пройденное"


async def test_next_item_does_not_reissue_granted_task(graph):
    """Ключевая согласованность: next-item не выдаёт зачтённое задание заново."""
    ids, db = graph["ids"], graph["db"]

    # Материалы идут раньше заданий — зачитываем их, чтобы движок дошёл до задач.
    for key in ("material_child", "material_root"):
        await manual_progress_service.grant_material(
            db, student_id=ids["student"], material_id=ids[key], granted_by=ids["teacher"]
        )
    await db.commit()

    first = await engine_svc.resolve_next_item(
        db, ids["student"], root_course_id=ids["root"]
    )
    # Обход post-order: подкурс идёт раньше корня, поэтому первым выдаётся
    # задание подкурса.
    assert first.type == "task"
    assert first.task_id == ids["task_child"]
    granted_task = first.task_id

    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=granted_task, granted_by=ids["teacher"]
    )
    await db.commit()

    following = await engine_svc.resolve_next_item(
        db, ids["student"], root_course_id=ids["root"]
    )
    # Ассертим КОНКРЕТНОЕ следующее задание, а не «!= зачтённого»: последнее
    # проходило бы и при `task_id is None` (движок вообще ничего не выдал).
    assert following.type == "task"
    assert following.task_id == ids["task_root_a"], (
        "после зачёта движок обязан выдать следующее по порядку задание, "
        "а не повторить зачтённое и не остановиться"
    )


async def test_grant_does_not_consume_attempt_limit(graph):
    """Зачёт не расходует попытки ученика — суть `root_course_id = NULL`."""
    ids, db = graph["ids"], graph["db"]
    before = await engine_svc.compute_task_state(
        db, ids["student"], ids["task_root_a"], root_course_id=ids["root"]
    )
    assert before.attempts_used == 0

    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], granted_by=ids["teacher"]
    )
    await db.commit()

    after = await engine_svc.compute_task_state(
        db, ids["student"], ids["task_root_a"], root_course_id=ids["root"]
    )
    assert after.attempts_used == 0, (
        "синтетическая попытка съела попытку ученика — root_course_id должен быть NULL"
    )
    assert after.state == "PASSED"


async def test_revoke_cancels_synthetic_attempt(graph):
    """Снятие зачёта аннулирует синтетическую попытку, строки остаются в истории.

    OPEN здесь — частный случай: у ученика по этому заданию нет РЕАЛЬНЫХ попыток.
    Если бы они были, задание вернулось бы в своё настоящее состояние
    (``IN_PROGRESS`` / ``FAILED`` / ``BLOCKED_LIMIT``), а не в ``OPEN``.
    """
    ids, db = graph["ids"], graph["db"]
    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], granted_by=ids["teacher"]
    )
    await db.commit()

    res = await manual_progress_service.revoke_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], revoked_by=ids["teacher"]
    )
    await db.commit()
    assert res["already"] is False

    state = await engine_svc.compute_task_state(db, ids["student"], ids["task_root_a"])
    assert state.state == "OPEN", "после снятия зачёта задание обязано снова быть открытым"

    kept = (
        await db.execute(
            text(
                "SELECT count(*) FROM attempts WHERE user_id = :u "
                "AND source_system = :s AND cancel_reason = :r"
            ),
            {
                "u": ids["student"],
                "s": manual_progress_service.MANUAL_SOURCE,
                "r": manual_progress_service.REVOKE_REASON,
            },
        )
    ).scalar()
    assert kept == 1, "строки не удаляются — история правок сохраняется"


async def test_revoke_without_grant_is_idempotent(graph):
    """Снятие несуществующего зачёта — не ошибка, а already=True."""
    ids, db = graph["ids"], graph["db"]
    res = await manual_progress_service.revoke_task(
        db, student_id=ids["student"], task_id=ids["task_root_b"], revoked_by=ids["teacher"]
    )
    await db.commit()
    assert res["already"] is True


async def test_grant_is_idempotent(graph):
    """Повторный зачёт не создаёт вторую попытку и возвращает already=True."""
    ids, db = graph["ids"], graph["db"]
    first = await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], granted_by=ids["teacher"]
    )
    await db.commit()
    second = await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], granted_by=ids["teacher"]
    )
    await db.commit()

    assert first["already"] is False
    assert second["already"] is True

    attempts = (
        await db.execute(
            text(
                "SELECT count(*) FROM attempts WHERE user_id = :u AND source_system = :s"
            ),
            {"u": ids["student"], "s": manual_progress_service.MANUAL_SOURCE},
        )
    ).scalar()
    assert attempts == 1, "повторный зачёт задвоил синтетическую попытку"


async def test_granted_manual_task_not_in_review_queue(graph):
    """Зачтённое SA_COM с ручной проверкой не попадает в очередь преподавателя."""
    ids, db = graph["ids"], graph["db"]
    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_child"], granted_by=ids["methodist"]
    )
    await db.commit()

    items, _total = await teacher_queue_service.list_pending_reviews(
        db, ids["methodist"], limit=200
    )
    assert all(it["task_id"] != ids["task_child"] for it in items), (
        "зачтённая работа встала в очередь проверки — checked_at не заполнен"
    )

    checked = (
        await db.execute(
            text(
                "SELECT checked_at, checked_by FROM task_results "
                "WHERE user_id = :u AND task_id = :t"
            ),
            {"u": ids["student"], "t": ids["task_child"]},
        )
    ).fetchone()
    assert checked is not None and checked[0] is not None
    assert checked[1] == ids["methodist"]


# ─── Материалы ──────────────────────────────────────────────────────────────


async def test_material_grant_and_revoke(graph):
    """Материал: отметка ставится с провенансом и снимается."""
    ids, db = graph["ids"], graph["db"]
    res = await manual_progress_service.grant_material(
        db, student_id=ids["student"], material_id=ids["material_root"],
        granted_by=ids["teacher"],
    )
    await db.commit()
    assert res["already"] is False

    row = (
        await db.execute(
            text(
                "SELECT status, source FROM student_material_progress "
                "WHERE student_id = :s AND material_id = :m"
            ),
            {"s": ids["student"], "m": ids["material_root"]},
        )
    ).fetchone()
    assert row is not None and row[0] == "completed"
    assert row[1] == manual_progress_service.MANUAL_SOURCE

    revoked = await manual_progress_service.revoke_material(
        db, student_id=ids["student"], material_id=ids["material_root"],
        revoked_by=ids["teacher"],
    )
    await db.commit()
    assert revoked["already"] is False

    gone = (
        await db.execute(
            text(
                "SELECT count(*) FROM student_material_progress "
                "WHERE student_id = :s AND material_id = :m"
            ),
            {"s": ids["student"], "m": ids["material_root"]},
        )
    ).scalar()
    assert gone == 0


async def test_material_revoke_keeps_real_progress(graph):
    """Снятие не трогает материал, пройденный самим учеником (source='system')."""
    ids, db = graph["ids"], graph["db"]
    await db.execute(
        text(
            "INSERT INTO student_material_progress "
            "(student_id, material_id, status, completed_at, source) "
            "VALUES (:s, :m, 'completed', now(), 'system')"
        ),
        {"s": ids["student"], "m": ids["material_root"]},
    )
    await db.commit()

    res = await manual_progress_service.revoke_material(
        db, student_id=ids["student"], material_id=ids["material_root"],
        revoked_by=ids["teacher"],
    )
    await db.commit()
    assert res["already"] is True, "реального прохождения ученика тут не было — но и снимать нечего"

    kept = (
        await db.execute(
            text(
                "SELECT source FROM student_material_progress "
                "WHERE student_id = :s AND material_id = :m"
            ),
            {"s": ids["student"], "m": ids["material_root"]},
        )
    ).fetchone()
    assert kept is not None and kept[0] == "system", (
        "снятие ручной отметки удалило реальный прогресс ученика"
    )


async def test_real_completion_overrides_manual_provenance(graph):
    """Ученик реально прошёл материал после ручной отметки → снятие его не сотрёт.

    Порядок «преподаватель отметил → ученик прошёл сам → преподаватель снял»
    ломался: upsert реального прохождения не трогал `source`, тот оставался
    ``manual_teacher``, и снятие удаляло настоящий прогресс ученика.
    """
    ids, db = graph["ids"], graph["db"]
    await manual_progress_service.grant_material(
        db, student_id=ids["student"], material_id=ids["material_root"],
        granted_by=ids["teacher"],
    )
    await db.commit()

    # Реальный путь ученика — ровно тот, которым ходит `POST /learning/materials/{id}/complete`.
    await learning_events_service.set_material_completed(
        db, ids["student"], ids["material_root"]
    )
    await db.commit()

    source = (
        await db.execute(
            text(
                "SELECT source FROM student_material_progress "
                "WHERE student_id = :s AND material_id = :m"
            ),
            {"s": ids["student"], "m": ids["material_root"]},
        )
    ).scalar()
    assert source == manual_progress_service.SYSTEM_SOURCE, (
        "реальное прохождение обязано перебить ручной провенанс"
    )

    res = await manual_progress_service.revoke_material(
        db, student_id=ids["student"], material_id=ids["material_root"],
        revoked_by=ids["teacher"],
    )
    await db.commit()
    assert res["already"] is True

    row = (
        await db.execute(
            text(
                "SELECT status, source FROM student_material_progress "
                "WHERE student_id = :s AND material_id = :m"
            ),
            {"s": ids["student"], "m": ids["material_root"]},
        )
    ).fetchone()
    assert row is not None, "снятие ручной отметки удалило реальное прохождение ученика"
    assert row[0] == "completed" and row[1] == manual_progress_service.SYSTEM_SOURCE


# ─── Массовые операции ──────────────────────────────────────────────────────


async def test_bulk_grant_covers_subtree_and_is_idempotent(graph):
    """Массовый зачёт покрывает всё поддерево; повтор ничего не добавляет."""
    ids, db = graph["ids"], graph["db"]
    res = await manual_progress_service.grant_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
    )
    await db.commit()

    assert res["tasks_affected"] == 3, "в дереве 3 задания (2 в корне + 1 в подкурсе)"
    assert res["materials_affected"] == 2, "в дереве 2 материала"
    assert res["skipped_already"] == 0

    for key in ("task_root_a", "task_root_b", "task_child"):
        state = await engine_svc.compute_task_state(db, ids["student"], ids[key])
        assert state.state == "PASSED", f"{key} не зачтён массовой операцией"

    again = await manual_progress_service.grant_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
    )
    await db.commit()
    assert again["tasks_affected"] == 0 and again["materials_affected"] == 0
    assert again["skipped_already"] == 5, "повтор обязан быть полностью идемпотентным"


async def test_bulk_revoke_rolls_subtree_back(graph):
    """Массовое снятие возвращает всё поддерево в исходное состояние."""
    ids, db = graph["ids"], graph["db"]
    await manual_progress_service.grant_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
    )
    await db.commit()

    res = await manual_progress_service.revoke_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], revoked_by=ids["teacher"]
    )
    await db.commit()
    assert res["tasks_affected"] == 3 and res["materials_affected"] == 2

    for key in ("task_root_a", "task_root_b", "task_child"):
        state = await engine_svc.compute_task_state(db, ids["student"], ids[key])
        assert state.state == "OPEN", f"{key} остался зачтённым после массового снятия"


async def test_bulk_grant_is_atomic_on_failure(graph, monkeypatch):
    """Сбой посреди массового зачёта не оставляет частичных данных.

    Раньше попытка создавалась через `AttemptsService.create_attempt` →
    `BaseRepository.create(commit=True)`, то есть каждое задание коммитилось
    отдельно: исключение на середине фиксировало часть дерева, а запись аудита
    (одна на всю пачку, в конце) не сохранялась — прогресс менялся без следа.
    """
    ids, db = graph["ids"], graph["db"]
    real_load_task = manual_progress_service._load_task  # noqa: SLF001
    calls = {"n": 0}

    async def flaky_load_task(db_, task_id):  # noqa: ANN001, ANN202
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("tsk297: имитация сбоя на третьем задании")
        return await real_load_task(db_, task_id)

    monkeypatch.setattr(manual_progress_service, "_load_task", flaky_load_task)

    with pytest.raises(RuntimeError):
        await manual_progress_service.grant_course_subtree(
            db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
        )
    await db.rollback()

    attempts = (
        await db.execute(
            text(
                "SELECT count(*) FROM attempts WHERE user_id = :u AND source_system = :s"
            ),
            {"u": ids["student"], "s": manual_progress_service.MANUAL_SOURCE},
        )
    ).scalar()
    assert attempts == 0, (
        "после отката осталась синтетическая попытка — значит внутри операции был commit"
    )

    results = (
        await db.execute(
            text(
                "SELECT count(*) FROM task_results WHERE user_id = :u AND source_system = :s"
            ),
            {"u": ids["student"], "s": manual_progress_service.MANUAL_SOURCE},
        )
    ).scalar()
    assert results == 0, "после отката остался синтетический результат"

    materials = (
        await db.execute(
            text(
                "SELECT count(*) FROM student_material_progress "
                "WHERE student_id = :s AND source = :src"
            ),
            {"s": ids["student"], "src": manual_progress_service.MANUAL_SOURCE},
        )
    ).scalar()
    assert materials == 0, "после отката осталась ручная отметка материала"


async def test_bulk_operations_refresh_course_state(graph):
    """Массовые операции пересчитывают `student_course_state`.

    Именно оттуда `me_service.get_courses_with_progress` берёт ``is_completed``:
    без пересчёта ученик видел бы 100% пройденных элементов при незавершённом
    курсе (и завершённый курс после массового снятия).
    """
    ids, db = graph["ids"], graph["db"]

    async def course_state() -> str | None:
        return (
            await db.execute(
                text(
                    "SELECT state FROM student_course_state "
                    "WHERE student_id = :s AND course_id = :c"
                ),
                {"s": ids["student"], "c": ids["root"]},
            )
        ).scalar()

    await manual_progress_service.grant_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
    )
    # Квиз массовый зачёт пропускает (его нельзя зачесть вручную), поэтому без
    # реального ответа курс завершённым не станет — здесь проверяется пересчёт
    # состояния, а не сам запрет.
    quiz_attempt = await _answer_quiz(db, ids["student"], ids["task_quiz"])
    await manual_progress_service._refresh_course_state(  # noqa: SLF001
        db, ids["student"], ids["root"]
    )
    await db.commit()
    assert await course_state() == "COMPLETED", (
        "после массового зачёта курс не отмечен завершённым — ученик увидит 100%, "
        "но курс останется незавершённым"
    )

    # Реальный ответ на квиз массовое снятие не трогает (и не должно) — убираем
    # его отдельно, чтобы проверить откат именно ручной части.
    await db.execute(
        text("UPDATE attempts SET cancelled_at = now() WHERE id = :a"), {"a": quiz_attempt}
    )
    await manual_progress_service.revoke_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], revoked_by=ids["teacher"]
    )
    await db.commit()
    assert await course_state() == "NOT_STARTED", (
        "после массового снятия курс остался завершённым"
    )


async def test_single_grant_refreshes_course_state(graph):
    """Единичный зачёт тоже пересчитывает состояние корня (не только массовый)."""
    ids, db = graph["ids"], graph["db"]
    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], granted_by=ids["teacher"]
    )
    await db.commit()

    state = (
        await db.execute(
            text(
                "SELECT state FROM student_course_state "
                "WHERE student_id = :s AND course_id = :c"
            ),
            {"s": ids["student"], "c": ids["root"]},
        )
    ).scalar()
    assert state == "IN_PROGRESS"


# ─── Чтение прогресса ───────────────────────────────────────────────────────


async def test_progress_tree_marks_manual_items(graph):
    """GET-прогресс помечает ручные отметки флагом manual и автором."""
    ids, db = graph["ids"], graph["db"]
    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], granted_by=ids["teacher"]
    )
    await manual_progress_service.grant_material(
        db, student_id=ids["student"], material_id=ids["material_root"],
        granted_by=ids["teacher"],
    )
    await db.commit()

    data = await manual_progress_service.get_student_progress(
        db, student_id=ids["student"], course_id=ids["root"]
    )
    by_key = {(i["item_type"], i["item_id"]): i for i in data["items"]}

    granted_task = by_key[("task", ids["task_root_a"])]
    assert granted_task["status"] == "PASSED"
    assert granted_task["manual"] is True
    assert granted_task["granted_by"] == ids["teacher"]
    assert granted_task["granted_at"] is not None

    plain_task = by_key[("task", ids["task_root_b"])]
    assert plain_task["status"] == "OPEN" and plain_task["manual"] is False

    granted_material = by_key[("material", ids["material_root"])]
    assert granted_material["status"] == "COMPLETED"
    assert granted_material["manual"] is True

    untouched_material = by_key[("material", ids["material_child"])]
    assert untouched_material["status"] == "NOT_STARTED"


async def test_progress_tree_has_course_nodes_and_parents(graph):
    """Дерево содержит узлы тем с parent_course_id и учебным порядком."""
    ids, db = graph["ids"], graph["db"]
    data = await manual_progress_service.get_student_progress(
        db, student_id=ids["student"], course_id=ids["root"]
    )
    items = data["items"]

    courses = [i for i in items if i["item_type"] == "course"]
    assert {c["item_id"] for c in courses} == {ids["root"], ids["child"]}

    root_node = next(c for c in courses if c["item_id"] == ids["root"])
    child_node = next(c for c in courses if c["item_id"] == ids["child"])
    assert root_node["parent_course_id"] is None, "у запрошенного корня родителя нет"
    assert child_node["parent_course_id"] == ids["root"]
    assert root_node["manual"] is None and child_node["manual"] is None

    # У всех заданий/материалов parent_course_id — их собственный узел.
    for item in items:
        if item["item_type"] in ("task", "material"):
            assert item["parent_course_id"] == item["course_id"]

    # Учебный порядок: post-order — подкурс идёт раньше курса-контейнера,
    # содержимое узла — сразу после его заголовка.
    order = [(i["item_type"], i["item_id"]) for i in items]
    assert order.index(("course", ids["child"])) < order.index(("course", ids["root"]))
    assert order.index(("course", ids["child"])) < order.index(("task", ids["task_child"]))
    assert order.index(("material", ids["material_child"])) < order.index(
        ("task", ids["task_child"])
    ), "внутри узла материалы идут раньше заданий — как у движка"
    assert order.index(("task", ids["task_root_a"])) < order.index(
        ("task", ids["task_root_b"])
    ), "задания узла идут по order_position"


async def test_course_node_status_rolls_up_subtree(graph):
    """Статус узла сворачивается по поддереву: NOT_STARTED → IN_PROGRESS → COMPLETED."""
    ids, db = graph["ids"], graph["db"]

    async def root_node_status() -> str:
        data = await manual_progress_service.get_student_progress(
            db, student_id=ids["student"], course_id=ids["root"]
        )
        return next(
            i["status"] for i in data["items"]
            if i["item_type"] == "course" and i["item_id"] == ids["root"]
        )

    assert await root_node_status() == "NOT_STARTED"

    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_child"], granted_by=ids["teacher"]
    )
    await db.commit()
    assert await root_node_status() == "IN_PROGRESS"

    await manual_progress_service.grant_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
    )
    await db.commit()
    # Ещё НЕ COMPLETED: в корне лежит квиз, а его массовый зачёт пропускает —
    # вручную квиз зачесть нельзя (tsk-297, S3-2). Узел закрывается только после
    # того, как ученик реально ответит на квиз.
    assert await root_node_status() == "IN_PROGRESS"

    await _answer_quiz(db, ids["student"], ids["task_quiz"])
    await db.commit()
    assert await root_node_status() == "COMPLETED"


async def test_progress_read_does_not_escalate(graph, monkeypatch):
    """Чтение карточки не зовёт `compute_course_state` и не рассылает уведомлений.

    Эскалации специально дан ПОВОД сработать: у ученика висит реальный
    непроверенный SA_COM (``checked_at IS NULL``) — именно этот предикат Y-6
    проверяет при COMPLETED. Без него проверка была бы вакуумной: ручной зачёт
    ``checked_at`` заполняет, и тест прошёл бы даже с возвращённым в read-путь
    `compute_course_state`. Дополнительно шпионим за самим вызовом — на случай,
    если эскалацию заглушат гард идемпотентности или rate-limit.
    """
    ids, db = graph["ids"], graph["db"]

    # Реальная непроверенная работа ученика по SA_COM-заданию подкурса.
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, source_system) "
                "VALUES (:u, :c, 'test') RETURNING id"
            ),
            {"u": ids["student"], "c": ids["child"]},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, checked_at) "
            "VALUES (:u, :t, :a, 0, 10, false, now(), now(), 0, NULL)"
        ),
        {"u": ids["student"], "t": ids["task_child"], "a": attempt_id},
    )
    await db.commit()

    await manual_progress_service.grant_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
    )
    # Квиз зачесть нельзя — доводим курс до COMPLETED реальным ответом, иначе
    # у эскалации не будет повода сработать и проверка станет вакуумной.
    await _answer_quiz(db, ids["student"], ids["task_quiz"])
    await db.commit()

    calls: list[int] = []
    original = LearningEngineService.compute_course_state

    async def spy(self, db_, student_id, course_id, **kwargs):  # noqa: ANN001, ANN202
        calls.append(int(course_id))
        return await original(self, db_, student_id, course_id, **kwargs)

    monkeypatch.setattr(LearningEngineService, "compute_course_state", spy)

    notifications_before = (
        await db.execute(
            text("SELECT count(*) FROM notifications WHERE user_id = :u"),
            {"u": ids["methodist"]},
        )
    ).scalar()

    data = await manual_progress_service.get_student_progress(
        db, student_id=ids["student"], course_id=ids["root"]
    )
    await db.commit()

    root_status = next(
        i["status"] for i in data["items"]
        if i["item_type"] == "course" and i["item_id"] == ids["root"]
    )
    assert root_status == "COMPLETED"
    assert calls == [], (
        "чтение прогресса дёрнуло compute_course_state — свёртка на read-пути "
        "обязана считаться локально, иначе просмотр карточки рассылает уведомления"
    )

    notifications_after = (
        await db.execute(
            text("SELECT count(*) FROM notifications WHERE user_id = :u"),
            {"u": ids["methodist"]},
        )
    ).scalar()
    assert notifications_after == notifications_before, (
        "чтение прогресса разослало уведомления — read-эндпоинт не должен этого делать"
    )


# ─── ACL и HTTP-контракт ────────────────────────────────────────────────────


async def test_api_foreign_teacher_forbidden(graph, client):
    """Преподаватель без связки с учеником и без ACL на курс → 403."""
    ids = graph["ids"]
    resp = await client.post(
        f"/api/v1/teacher/students/{ids['student']}/progress/tasks/{ids['task_root_a']}",
        json={"comment": "чужой ученик"},
        headers={"Authorization": f"Bearer {graph['tokens']['other']}"},
    )
    assert resp.status_code == 403, resp.text


async def test_api_course_acl_requires_student_enrollment(graph, client):
    """ACL на курс не даёт власти над ЛЮБЫМ пользователем — только над учениками курса.

    Раньше ветка курсового ACL проверяла только «курс мой» и ничего не знала про
    ученика: преподаватель курса X мог править прогресс произвольного user_id
    (включая другого преподавателя), просто перебирая идентификаторы.
    """
    ids, db = graph["ids"], graph["db"]
    outsider_id, _ = await _new_user(db, "student", "outsider")
    try:
        # `other` — преподаватель без связки с учеником; даём ему ACL на корень.
        await db.execute(
            text(
                "INSERT INTO teacher_courses (teacher_id, course_id) "
                "VALUES (:t, :c) ON CONFLICT DO NOTHING"
            ),
            {"t": ids["other"], "c": ids["root"]},
        )
        await db.commit()

        headers = {"Authorization": f"Bearer {graph['tokens']['other']}"}

        # Контроль: ученик КУРСА теперь доступен — ACL работает.
        allowed = await client.post(
            f"/api/v1/teacher/students/{ids['student']}/progress/tasks/{ids['task_root_a']}",
            json={},
            headers=headers,
        )
        assert allowed.status_code == 200, allowed.text

        # А посторонний пользователь, не записанный на этот курс, — нет.
        denied = await client.post(
            f"/api/v1/teacher/students/{outsider_id}/progress/tasks/{ids['task_root_a']}",
            json={},
            headers=headers,
        )
        assert denied.status_code == 403, denied.text
    finally:
        await db.execute(
            text("DELETE FROM teacher_courses WHERE teacher_id = :t"), {"t": ids["other"]}
        )
        for table, col in (
            ("user_roles", "user_id"),
            ("user_session", "user_id"),
            ("identity_link", "user_id"),
        ):
            await db.execute(
                text(f"DELETE FROM {table} WHERE {col} = :u"),  # nosec B608
                {"u": outsider_id},
            )
        await db.commit()


async def test_api_methodist_can_grant(graph, client):
    """Методист правит прогресс любого ученика (bypass) → 200."""
    ids = graph["ids"]
    resp = await client.post(
        f"/api/v1/teacher/students/{ids['student']}/progress/tasks/{ids['task_root_a']}",
        json={"comment": "перенос наработок"},
        headers={"Authorization": f"Bearer {graph['tokens']['methodist']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["granted"] is True and body["already"] is False
    assert body["source"] == manual_progress_service.MANUAL_SOURCE


async def test_api_linked_teacher_full_cycle(graph, client):
    """Свой преподаватель: зачёт → прогресс → снятие через HTTP."""
    ids = graph["ids"]
    headers = {"Authorization": f"Bearer {graph['tokens']['teacher']}"}
    base = f"/api/v1/teacher/students/{ids['student']}/progress"

    granted = await client.post(f"{base}/tasks/{ids['task_root_a']}", json={}, headers=headers)
    assert granted.status_code == 200, granted.text

    tree = await client.get(f"{base}?course_id={ids['root']}", headers=headers)
    assert tree.status_code == 200, tree.text
    item = next(
        i for i in tree.json()["items"]
        if i["item_type"] == "task" and i["item_id"] == ids["task_root_a"]
    )
    assert item["status"] == "PASSED" and item["manual"] is True

    revoked = await client.delete(f"{base}/tasks/{ids['task_root_a']}", headers=headers)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["already"] is False


async def test_api_progress_returns_course_selector(graph, client):
    """GET отдаёт список доступных курсов ученика — селектор питается им же."""
    ids = graph["ids"]
    headers = {"Authorization": f"Bearer {graph['tokens']['teacher']}"}
    base = f"/api/v1/teacher/students/{ids['student']}/progress"

    with_tree = await client.get(f"{base}?course_id={ids['root']}", headers=headers)
    assert with_tree.status_code == 200, with_tree.text
    body = with_tree.json()
    assert any(c["course_id"] == ids["root"] for c in body["courses"])
    assert body["items"], "с course_id дерево обязано быть заполнено"

    only_courses = await client.get(base, headers=headers)
    assert only_courses.status_code == 200, only_courses.text
    body = only_courses.json()
    assert body["course_id"] is None
    assert body["items"] == [], "без course_id дерево пустое"
    assert any(c["course_id"] == ids["root"] for c in body["courses"])
    assert all(c.get("title") for c in body["courses"])


async def test_api_course_selector_respects_acl(graph, client):
    """Курсы чужого ученика в селектор постороннего преподавателя не попадают."""
    ids = graph["ids"]
    resp = await client.get(
        f"/api/v1/teacher/students/{ids['student']}/progress",
        headers={"Authorization": f"Bearer {graph['tokens']['other']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["courses"] == []


async def test_api_bulk_endpoint(graph, client):
    """Массовый эндпоинт по узлу отдаёт счётчики по всему поддереву."""
    ids = graph["ids"]
    headers = {"Authorization": f"Bearer {graph['tokens']['methodist']}"}
    resp = await client.post(
        f"/api/v1/teacher/students/{ids['student']}/progress/courses/{ids['root']}",
        json={"comment": "перевод ученика на его место"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tasks_affected"] == 3 and body["materials_affected"] == 2


async def test_audit_events_written(graph):
    """Каждая операция оставляет запись аудита нужного типа."""
    ids, db = graph["ids"], graph["db"]
    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"],
        granted_by=ids["teacher"], comment="перенос",
    )
    await manual_progress_service.revoke_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], revoked_by=ids["teacher"]
    )
    await db.commit()

    rows = (
        await db.execute(
            text(
                "SELECT event_type, details FROM audit_event "
                "WHERE user_id = :u AND event_type = ANY(:types) ORDER BY id"
            ),
            {
                "u": ids["teacher"],
                "types": ["teacher.progress.granted", "teacher.progress.revoked"],
            },
        )
    ).fetchall()
    types = [r[0] for r in rows]
    assert "teacher.progress.granted" in types
    assert "teacher.progress.revoked" in types
    granted_details = next(r[1] for r in rows if r[0] == "teacher.progress.granted")
    assert granted_details["student_id"] == ids["student"]
    assert granted_details["bulk"] is False


# ─── Квиз-вопросы: ручной зачёт запрещён (tsk-297, находка ревью S3-2) ───────
#
# Квиз (SC_Qw/MC_Qw) — диагностика, а не учебное задание, которое ученик мог
# «освоить вне платформы». Синтетический результат зачёта не несёт scale_scores,
# из-за чего зачёт (а) закрывал бы ученику реальный ответ — приём отклоняет 409
# повторный ответ при наличии результата в неотменённой попытке, и (б) ломал бы
# подбор курса правилом trigger_event='quiz_scale' (argmax по нулевым шкалам).


async def test_quiz_grant_rejected(graph):
    """Ручной зачёт квиза отклоняется 422, а не создаёт синтетический результат."""
    ids, db = graph["ids"], graph["db"]

    with pytest.raises(HTTPException) as exc:
        await manual_progress_service.grant_task(
            db, student_id=ids["student"], task_id=ids["task_quiz"],
            granted_by=ids["teacher"],
        )
    assert exc.value.status_code == 422
    assert "квиз" in exc.value.detail.lower()

    rows = (
        await db.execute(
            text("SELECT count(*) FROM task_results WHERE user_id = :u AND task_id = :t"),
            {"u": ids["student"], "t": ids["task_quiz"]},
        )
    ).scalar()
    assert rows == 0, "отклонённый зачёт не должен оставлять результат"


async def test_quiz_stays_answerable_after_rejected_grant(graph):
    """Суть запрета: после отказа ученик по-прежнему может реально ответить.

    Именно это ломал зачёт — результат в неотменённой попытке заставлял приём
    ответа (`POST /attempts/{id}/answers`, шаг 2.3a) вернуть 409, и снять это
    можно было только снятием зачёта.
    """
    ids, db = graph["ids"], graph["db"]

    with pytest.raises(HTTPException):
        await manual_progress_service.grant_task(
            db, student_id=ids["student"], task_id=ids["task_quiz"],
            granted_by=ids["teacher"],
        )

    # Тот же предикат, что у гейта приёма ответа: пусто — значит 409 не сработает.
    blocking = (
        await db.execute(
            text(
                "SELECT 1 FROM task_results tr "
                "INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL "
                "WHERE tr.user_id = :u AND tr.task_id = :t LIMIT 1"
            ),
            {"u": ids["student"], "t": ids["task_quiz"]},
        )
    ).first()
    assert blocking is None, "после отказа приём ответа на квиз обязан оставаться открыт"

    state = await engine_svc.compute_task_state(db, ids["student"], ids["task_quiz"])
    assert state.state == "OPEN"


async def test_quiz_grant_rejected_via_api(graph, client):
    """Через HTTP отказ приходит тем же 422 с внятным русским detail."""
    ids = graph["ids"]
    resp = await client.post(
        f"/api/v1/teacher/students/{ids['student']}/progress/tasks/{ids['task_quiz']}",
        headers={"Authorization": f"Bearer {graph['tokens']['teacher']}"},
        json={},
    )
    assert resp.status_code == 422, resp.text
    assert "квиз" in resp.json()["detail"].lower()


async def test_bulk_grant_skips_quiz_without_failing(graph):
    """Массовый зачёт пропускает квиз, а не падает 422 на всём дереве."""
    ids, db = graph["ids"], graph["db"]
    res = await manual_progress_service.grant_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
    )
    await db.commit()

    assert res["skipped_quiz"] == 1, "квиз дерева обязан попасть в отдельный счётчик"
    assert res["tasks_affected"] == 3, "остальные 3 задания зачтены, квиз их не блокирует"

    quiz_state = await engine_svc.compute_task_state(db, ids["student"], ids["task_quiz"])
    assert quiz_state.state == "OPEN", "квиз остался непройденным"
    for key in ("task_root_a", "task_root_b", "task_child"):
        state = await engine_svc.compute_task_state(db, ids["student"], ids[key])
        assert state.state == "PASSED", f"{key} не зачтён из-за соседнего квиза"


async def test_quiz_revoke_is_allowed(graph):
    """Снятие зачёта квиза НЕ запрещено — иначе старые зачёты было бы не убрать.

    Запрет закрывает только новый путь (grant). Обратимость уже поставленного
    важнее симметрии: на момент правки на проде таких зачётов не было, но
    операция обязана оставаться доступной.
    """
    ids, db = graph["ids"], graph["db"]
    res = await manual_progress_service.revoke_task(
        db, student_id=ids["student"], task_id=ids["task_quiz"], revoked_by=ids["teacher"]
    )
    await db.commit()
    assert res["already"] is True, "снятия не было — идемпотентный ответ, а не 422"


async def test_progress_tree_marks_quiz_not_grantable(graph):
    """Дерево прогресса помечает квиз `manual_grantable=False` — SPW прячет кнопку."""
    ids, db = graph["ids"], graph["db"]
    data = await manual_progress_service.get_student_progress(
        db, student_id=ids["student"], course_id=ids["root"]
    )
    by_key = {(i["item_type"], i["item_id"]): i for i in data["items"]}

    assert by_key[("task", ids["task_quiz"])]["manual_grantable"] is False
    assert by_key[("task", ids["task_root_a"])]["manual_grantable"] is True
    assert by_key[("material", ids["material_root"])]["manual_grantable"] is True
    assert by_key[("course", ids["root"])]["manual_grantable"] is True


async def test_quiz_scale_scores_survive_only_real_answer(graph):
    """Шкалы копятся только с реального ответа — то, что зачёт подделать не мог.

    Проверяем инвариант, ради которого запрет и введён: правило
    ``trigger_event='quiz_scale'`` считает argmax по накопленным
    ``scale_scores``, и зачтённый квиз внёс бы в него нули.
    """
    ids, db = graph["ids"], graph["db"]

    await _answer_quiz(db, ids["student"], ids["task_quiz"], {"информатика": 3, "python": 1})
    await db.commit()

    stored = (
        await db.execute(
            text(
                "SELECT tr.scale_scores FROM task_results tr "
                "INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL "
                "WHERE tr.user_id = :u AND tr.task_id = :t"
            ),
            {"u": ids["student"], "t": ids["task_quiz"]},
        )
    ).scalars().all()
    assert stored == [{"информатика": 3, "python": 1}]
    assert all(s is not None for s in stored), "шкалы не должны теряться"


# ─── Пакетный расчёт статусов (review tsk-297, находка S3-3) ───────────────


async def test_batch_task_states_match_individual_compute(graph):
    """`compute_task_states_batch` эквивалентен `compute_task_state` в цикле.

    N+1 на карточке ученика (`get_student_progress` дёргал `compute_task_state`
    по каждому заданию дерева — ~5 запросов на задание, ~860 на курсе из 172
    заданий) заменён на batch: 2 запроса на всё дерево + переиспользование
    уже загруженного `last_results`. Тест — не по одному состоянию, а по
    СМЕШАННОМУ дереву (passed/failed/blocked_limit/open+skipped), чтобы
    исключить совпадение по одной ветке if/elif.
    """
    ids, db = graph["ids"], graph["db"]

    # PASSED: один результат с ratio >= 0.5.
    await _submit_result(db, ids["student"], ids["task_root_a"], score=10, max_score=10)
    # FAILED: одна неудачная попытка, лимит (3 по умолчанию) не исчерпан.
    await _submit_result(db, ids["student"], ids["task_child"], score=0, max_score=10)
    # BLOCKED_LIMIT: все 3 попытки по умолчанию исчерпаны, PASSED не было.
    for _ in range(3):
        await _submit_result(db, ids["student"], ids["task_root_b"], score=0, max_score=10)
    # OPEN + отдельно отмечен skipped (student_task_progress) — задание без
    # единого результата, но учтено пройденным при свёртке узла.
    await learning_events_service.set_task_skipped(db, ids["student"], ids["task_quiz"])
    await db.commit()

    task_ids = [ids["task_root_a"], ids["task_root_b"], ids["task_child"], ids["task_quiz"]]

    individual = {
        tid: await engine_svc.compute_task_state(db, ids["student"], tid)
        for tid in task_ids
    }
    batch = await engine_svc.compute_task_states_batch(db, ids["student"], task_ids)

    # Сверяем, что смешанное дерево действительно накрывает разные состояния —
    # иначе тест мог бы пройти и при сломанной batch-ветке одного из case'ов.
    assert {s.state for s in individual.values()} == {"PASSED", "FAILED", "BLOCKED_LIMIT", "OPEN"}

    for tid in task_ids:
        exp, got = individual[tid], batch[tid]
        assert got.state == exp.state, f"task {tid}: state разошёлся"
        assert got.attempts_used == exp.attempts_used, f"task {tid}: attempts_used разошёлся"
        assert got.attempts_limit_effective == exp.attempts_limit_effective, (
            f"task {tid}: attempts_limit_effective разошёлся"
        )
        assert got.last_score == exp.last_score, f"task {tid}: last_score разошёлся"
        assert got.last_max_score == exp.last_max_score, f"task {tid}: last_max_score разошёлся"
        assert got.last_attempt_id == exp.last_attempt_id, f"task {tid}: last_attempt_id разошёлся"
        assert got.last_is_correct == exp.last_is_correct, f"task {tid}: last_is_correct разошёлся"

    # И то же самое — эквивалентность видна и на уровне публичного API карточки.
    data = await manual_progress_service.get_student_progress(
        db, student_id=ids["student"], course_id=ids["root"]
    )
    by_id = {(i["item_type"], i["item_id"]): i for i in data["items"]}
    for tid in task_ids:
        assert by_id[("task", tid)]["status"] == individual[tid].state, (
            f"task {tid}: карточка ученика разошлась с поэлементным расчётом движка"
        )


async def test_batch_task_states_empty_list_is_noop(graph):
    """Пустой список заданий — пустой результат без обращения к БД."""
    ids, db = graph["ids"], graph["db"]
    assert await engine_svc.compute_task_states_batch(db, ids["student"], []) == {}
