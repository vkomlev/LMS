"""tsk-653: признак ИИ-авторства как повод для сигнала «нужно повторение».

Что закрываем:
- новый датчик находит ученика, которого прежний не увидит НИКОГДА (у него нет
  ошибок — все работы приняты);
- пороги держат шум: три работы из трёхсот сигналом не становятся;
- счёт идёт по КОРНЕВОМУ курсу — иначе у ученика с одной работой в каждом
  подкурсе родилось бы по карточке на подкурс;
- сигналы разных поводов по одной паре «курс + ученик» сосуществуют, а не
  подавляют друг друга молча;
- у эскалации появился выход: методист может её закрыть.

Живой проход контура, из которого всё это выросло, —
docs/qa/2026-08-23-tsk653-progon-kontura-eskalatsii.md.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import learning_gap_signals_service as sig
from app.services.auth import identity_link_service


async def _user(db, prefix: str) -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=prefix, tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id


async def _course(db, title: str, parent_id: int | None = None) -> int:
    cid = int((await db.execute(text(
        "INSERT INTO courses (title, access_level, is_required, course_uid) "
        "VALUES (:t,'auto_check',false,:u) RETURNING id"
    ), {"t": title, "u": f"tsk653-{random.randint(10**8, 10**10)}"})).scalar_one())
    if parent_id is not None:
        await db.execute(text(
            "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
            "VALUES (:c, :p, 1)"
        ), {"c": cid, "p": parent_id})
    await db.commit()
    return cid


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(text(
        "INSERT INTO user_courses (user_id, course_id) VALUES (:u, :c)"
    ), {"u": user_id, "c": course_id})
    await db.commit()


async def _submission(db, *, user_id: int, course_id: int, flagged: bool) -> int:
    """Разобранная работа: принятая (ошибок нет) и с признаком либо без него.

    `is_correct=True` намеренно у ВСЕХ: в этом и суть случая — ученик, сдающий
    чужое, ошибок не делает, и датчик по доле ошибок его не увидит.
    """
    task_id = int((await db.execute(text(
        "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
        " course_id, difficulty_id, is_active) "
        "VALUES (:e, 6, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, 1, true) RETURNING id"
    ), {
        "e": f"tsk653-t-{random.randint(10**8, 10**10)}",
        "c": json.dumps({"type": "TA", "stem": "Опиши алгоритм"}),
        "r": json.dumps({"max_score": 6}),
        "cid": course_id,
    })).scalar_one())

    review = {"status": "done", "kind": "text"}
    if flagged:
        review["signals"] = [{"code": "math_render_residue", "label": "…", "evidence": "…"}]
        review["ai_authorship"] = {"verdict": "ai_likely", "reasoning": "…"}
    else:
        review["signals"] = []
        review["ai_authorship"] = {"verdict": "student_likely", "reasoning": "…"}

    now = datetime.now(timezone.utc)
    return int((await db.execute(text(
        "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, "
        " received_at, max_score, source_system, is_correct, answer_json, code_review) "
        "VALUES (6, :u, :t, :now, 0, :now, 6, 'spw_web', true, CAST(:a AS jsonb), "
        " CAST(:cr AS jsonb)) RETURNING id"
    ), {
        "u": user_id, "t": task_id, "now": now,
        "a": json.dumps({"type": "TA", "response": {"text": "…"}}),
        "cr": json.dumps(review, ensure_ascii=False),
    })).scalar_one())


async def _cleanup(db, users: list[int], courses: list[int]) -> None:
    await db.execute(text(
        "DELETE FROM task_results WHERE task_id IN "
        "(SELECT id FROM tasks WHERE course_id = ANY(:c))"
    ), {"c": courses})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:c)"), {"c": courses})
    await db.execute(text("DELETE FROM user_courses WHERE course_id = ANY(:c)"), {"c": courses})
    await db.execute(text("DELETE FROM learning_gap_signal WHERE course_id = ANY(:c)"), {"c": courses})
    await db.execute(text("DELETE FROM notifications WHERE user_id = ANY(:u)"), {"u": users})
    await db.commit()
    if users:
        try:
            await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": users})
            await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": users})
            await db.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": users})
            await db.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": users})
            await db.commit()
        except Exception:
            await db.rollback()
    await db.execute(text("DELETE FROM course_parents WHERE course_id = ANY(:c)"), {"c": courses})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:c)"), {"c": courses})
    await db.commit()


# ───────────────────────────── Датчик ────────────────────────────────────────


@pytest.mark.asyncio
async def test_finds_student_whom_error_sensor_never_sees(db):
    """Главный случай: все работы приняты, ошибок ноль — и признак всё равно виден.

    Прежний датчик (`find_student_gaps`) ищет долю неверных ≥ 50 %. Ученик,
    сдающий чужое, выглядит отличником и не попадает в него никогда — именно так
    ученица, с которой началась tsk-646, не имела ни одного сигнала за всё время.
    """
    root = await _course(db, "tsk653 корень")
    student = await _user(db, "tsk653-ai")
    await _enroll(db, student, root)
    try:
        for _ in range(4):
            await _submission(db, user_id=student, course_id=root, flagged=True)
        await _submission(db, user_id=student, course_id=root, flagged=False)

        found = await sig.find_ai_authorship_gaps(db)
        mine = [r for r in found if r["student_id"] == student]
        assert len(mine) == 1, "ученик с четырьмя помеченными работами не найден"
        assert mine[0]["course_id"] == root
        assert mine[0]["reviewed"] == 5 and mine[0]["flagged"] == 4

        # И тут же — контроль: старый датчик его действительно не видит.
        by_errors = await sig.find_student_gaps(db)
        assert not [r for r in by_errors if r["student_id"] == student]
    finally:
        await _cleanup(db, [student], [root])


@pytest.mark.asyncio
async def test_counts_by_root_course_not_by_subcourse(db):
    """Работы разбросаны по подкурсам — сигнал всё равно один, на корень.

    У ученицы 4538 двенадцать работ лежат в двенадцати подкурсах по одной.
    По подкурсам сигнал не родился бы вовсе (одна работа — не статистика), а
    роди он их по одному на подкурс, методист получил бы одиннадцать карточек
    об одном человеке.
    """
    root = await _course(db, "tsk653 корень с главами")
    subs = [await _course(db, f"tsk653 подкурс {i}", parent_id=root) for i in range(4)]
    student = await _user(db, "tsk653-tree")
    await _enroll(db, student, root)
    try:
        for sub in subs:
            await _submission(db, user_id=student, course_id=sub, flagged=True)

        found = [r for r in await sig.find_ai_authorship_gaps(db)
                 if r["student_id"] == student]
        assert len(found) == 1, "сигнал размножился по подкурсам"
        assert found[0]["course_id"] == root
        assert found[0]["flagged"] == 4
    finally:
        await _cleanup(db, [student], [root, *subs])


@pytest.mark.asyncio
async def test_thresholds_hold_back_noise(db):
    """Две помеченные работы и низкая доля сигналом не становятся.

    Оба порога нужны сразу: три работы из трёхсот — шум, а две из двух — слишком
    мало, чтобы звать человека и говорить с ребёнком.
    """
    root_few = await _course(db, "tsk653 мало работ")
    root_share = await _course(db, "tsk653 низкая доля")
    quiet = await _user(db, "tsk653-quiet")
    diligent = await _user(db, "tsk653-diligent")
    await _enroll(db, quiet, root_few)
    await _enroll(db, diligent, root_share)
    try:
        # Порог числа: помечены две работы из двух — доля высокая, объём нет.
        for _ in range(2):
            await _submission(db, user_id=quiet, course_id=root_few, flagged=True)

        # Порог доли: помечены три работы, но из двенадцати.
        for _ in range(3):
            await _submission(db, user_id=diligent, course_id=root_share, flagged=True)
        for _ in range(9):
            await _submission(db, user_id=diligent, course_id=root_share, flagged=False)

        found = await sig.find_ai_authorship_gaps(db)
        assert not [r for r in found if r["student_id"] == quiet]
        assert not [r for r in found if r["student_id"] == diligent]
    finally:
        await _cleanup(db, [quiet, diligent], [root_few, root_share])


@pytest.mark.asyncio
async def test_manual_teacher_marks_do_not_count(db):
    """Ручная простановка прогресса — не сдача ученика и в счёт не идёт.

    То же правило, что у соседних датчиков: источник берётся из общего фильтра
    `real_student_results_filter`, а не переписывается здесь.
    """
    root = await _course(db, "tsk653 ручная простановка")
    student = await _user(db, "tsk653-manual")
    await _enroll(db, student, root)
    try:
        for _ in range(4):
            rid = await _submission(db, user_id=student, course_id=root, flagged=True)
            await db.execute(text(
                "UPDATE task_results SET source_system = 'manual_teacher' WHERE id = :r"
            ), {"r": rid})
        await db.commit()

        found = await sig.find_ai_authorship_gaps(db)
        assert not [r for r in found if r["student_id"] == student]
    finally:
        await _cleanup(db, [student], [root])


# ─────────────────────── Причина как первоклассное поле ──────────────────────


@pytest.mark.asyncio
async def test_two_reasons_coexist_for_the_same_pair(db):
    """Сигналы разных поводов по одной паре «курс + ученик» не подавляют друг друга.

    Без причины в частичном уникальном индексе второй сигнал молча не завёлся
    бы: `upsert_signal` написан на `ON CONFLICT DO NOTHING`, и пропуск выглядел
    бы как штатная работа.
    """
    root = await _course(db, "tsk653 два повода")
    student = await _user(db, "tsk653-two-reasons")
    try:
        by_errors = await sig.upsert_signal(
            db, course_id=root, student_id=student,
            submissions=10, students=1, wrong_rate=0.7,
        )
        by_authorship = await sig.upsert_signal(
            db, course_id=root, student_id=student,
            submissions=12, students=1, wrong_rate=0.0,
            reason=sig.REASON_AI_AUTHORSHIP,
            meta={"reviewed": 12, "flagged": 11},
        )
        await db.commit()

        assert by_errors is not None
        assert by_authorship is not None, "сигнал второго повода молча не завёлся"

        # А вот повтор ТОГО ЖЕ повода по-прежнему подавляется.
        again = await sig.upsert_signal(
            db, course_id=root, student_id=student,
            submissions=13, students=1, wrong_rate=0.0,
            reason=sig.REASON_AI_AUTHORSHIP, meta={"reviewed": 13, "flagged": 12},
        )
        await db.commit()
        assert again is None
    finally:
        await _cleanup(db, [student], [root])


@pytest.mark.asyncio
async def test_authorship_signal_is_not_sorted_to_the_bottom(db):
    """Сигнал о признаке не уезжает вниз списка из-за нулевой доли ошибок.

    Живой проход показал ровно это: карточка выехала методисту последней строкой
    с бейджем «0% ошибок», то есть список работал ПРОТИВ сигнала.
    """
    root_weak = await _course(db, "tsk653 слабый по ошибкам")
    root_strong = await _course(db, "tsk653 сильный по признаку")
    student = await _user(db, "tsk653-order")
    try:
        await sig.upsert_signal(db, course_id=root_weak, student_id=student,
                                submissions=10, students=1, wrong_rate=0.55)
        await sig.upsert_signal(
            db, course_id=root_strong, student_id=student,
            submissions=12, students=1, wrong_rate=0.0,
            reason=sig.REASON_AI_AUTHORSHIP, meta={"reviewed": 12, "flagged": 11},
        )
        await db.commit()

        rows = [s for s in await sig.list_signals(db, for_student=True)
                if s["course_id"] in (root_weak, root_strong)]
        assert [r["course_id"] for r in rows] == [root_strong, root_weak]
        # Числа повода доехали — без них карточке нечего показать вместо доли.
        strong = rows[0]
        assert strong["reason"] == sig.REASON_AI_AUTHORSHIP
        assert strong["meta"]["flagged"] == 11 and strong["meta"]["reviewed"] == 12
    finally:
        await _cleanup(db, [student], [root_weak, root_strong])


@pytest.mark.asyncio
async def test_old_signals_keep_their_reason(db):
    """Сигнал без явного повода — про ошибки. Это правда о 27 прежних строках."""
    root = await _course(db, "tsk653 умолчание повода")
    try:
        sid = await sig.upsert_signal(db, course_id=root, student_id=None,
                                      submissions=40, students=5, wrong_rate=0.6)
        await db.commit()
        reason = (await db.execute(
            text("SELECT reason FROM learning_gap_signal WHERE id = :s"), {"s": sid},
        )).scalar_one()
        assert reason == sig.REASON_ERROR_RATE
    finally:
        await _cleanup(db, [], [root])


# ────────────────────────── Выход из эскалации ───────────────────────────────


@pytest.mark.asyncio
async def test_escalated_signal_can_finally_be_closed(db):
    """У эскалации появился выход — до tsk-653 его не было вовсе.

    `dismiss_signal` работает только из `new`/`acknowledged`; из `escalated`
    закрыть сигнал было нечем, и 5 сигналов висели в проде с 06.08.
    """
    root = await _course(db, "tsk653 закрытие эскалации")
    student = await _user(db, "tsk653-resolve-student")
    teacher = await _user(db, "tsk653-resolve-teacher")
    methodist = await _user(db, "tsk653-resolve-methodist")
    try:
        sid = await sig.upsert_signal(
            db, course_id=root, student_id=student,
            submissions=12, students=1, wrong_rate=0.0,
            reason=sig.REASON_AI_AUTHORSHIP, meta={"reviewed": 12, "flagged": 11},
        )
        await db.commit()

        # Пока сигнал не передан, закрывать методисту нечего.
        assert await sig.resolve_signal(
            db, signal_id=sid, methodist_id=methodist
        ) is False

        await sig.acknowledge_signal(
            db, signal_id=sid, teacher_id=teacher,
            comment="Работы не дают судить о знаниях", escalate=True,
        )

        ok = await sig.resolve_signal(
            db, signal_id=sid, methodist_id=methodist,
            comment="Собран мини-курс повторения", mini_course_id=4242,
        )
        assert ok is True

        row = (await db.execute(text(
            "SELECT status, meta FROM learning_gap_signal WHERE id = :s"
        ), {"s": sid})).mappings().one()
        assert row["status"] == "resolved"
        # Ссылка на курс — единственное место, где видно, ЧЕМ кончилась эскалация.
        assert row["meta"]["mini_course_id"] == 4242
        assert row["meta"]["resolved_by"] == methodist
        # Числа повода не затёрты слиянием.
        assert row["meta"]["flagged"] == 11

        # Повторное нажатие не должно выглядеть как успех.
        assert await sig.resolve_signal(
            db, signal_id=sid, methodist_id=methodist
        ) is False
    finally:
        await _cleanup(db, [student, teacher, methodist], [root])


@pytest.mark.asyncio
async def test_resolved_signal_leaves_the_methodist_screen(db):
    """Закрытый сигнал уходит из списка — иначе экран копит сделанную работу."""
    root = await _course(db, "tsk653 уходит с экрана")
    student = await _user(db, "tsk653-gone-student")
    teacher = await _user(db, "tsk653-gone-teacher")
    methodist = await _user(db, "tsk653-gone-methodist")
    try:
        sid = await sig.upsert_signal(db, course_id=root, student_id=student,
                                      submissions=10, students=1, wrong_rate=0.8)
        await db.commit()
        await sig.acknowledge_signal(db, signal_id=sid, teacher_id=teacher,
                                     comment="Передаю", escalate=True)

        before = await sig.list_signals(
            db, for_student=False, statuses=("new", "acknowledged", "escalated"),
        )
        assert any(s["id"] == sid for s in before)

        await sig.resolve_signal(db, signal_id=sid, methodist_id=methodist)

        after = await sig.list_signals(
            db, for_student=False, statuses=("new", "acknowledged", "escalated"),
        )
        assert not any(s["id"] == sid for s in after)
    finally:
        await _cleanup(db, [student, teacher, methodist], [root])


# ───────────────── Соседняя находка: решение без комментария ─────────────────


@pytest.mark.asyncio
async def test_decision_without_comment_does_not_break(db):
    """Кнопки «Не нужно» и «Разберу сам» без комментария не падают.

    Путь был не покрыт: все существующие тесты передают комментарий, а
    интерфейс при пустом поле шлёт `comment: null` — то есть преподаватель
    попадает сюда, просто нажав кнопку без текста. Проверено при работе над
    tsk-653: дефекта здесь НЕТ, драйвер выводит тип параметра из присваивания
    текстовой колонке. Тест закрепляет это, чтобы вывод не пришлось делать
    заново.
    """
    course = await _course(db, "tsk653 решение без текста")
    student = await _user(db, "tsk653-nocomment-student")
    teacher = await _user(db, "tsk653-nocomment-teacher")
    try:
        first = await sig.upsert_signal(db, course_id=course, student_id=student,
                                        submissions=9, students=1, wrong_rate=0.6)
        await db.commit()
        assert await sig.dismiss_signal(db, signal_id=first, teacher_id=teacher) is True

        second = await sig.upsert_signal(db, course_id=course, student_id=student,
                                         submissions=9, students=1, wrong_rate=0.6)
        await db.commit()
        assert await sig.acknowledge_signal(
            db, signal_id=second, teacher_id=teacher, escalate=False
        ) is True

        # Транзакция цела — следующий запрос проходит.
        assert (await db.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await _cleanup(db, [student, teacher], [course])
