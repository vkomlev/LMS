"""tsk-231 Фаза 6: зависимость, которая выдаётся точечно (`auto_assign=False`).

Требование оператора: на основной курс вешаем жёсткую зависимость, но мини-курс
повторения НАЗНАЧАЕМ адресно — того, кто тему уже освоил, блокировать нельзя.

Проверяются два инварианта, и оба молчаливые — ни один не упадёт ни ошибкой, ни
тестом эндпоинта, если сломается:

1. **Точечность.** Требуемый курс не раздаётся автоматически, и блокирует
   только тех, кому назначен. Сломается — заблокирует весь поток разом
   (на проде курса 88 это 35 учеников, из них 22 активных и не нуждающихся).
2. **Совпадение движка и синтабуса.** `resolve_next_item` и
   `me_service._BLOCKED_COURSES_SQL` — два независимых пути к одному ответу
   «заблокирован ли ученик». Разойдись они — ученик видит замок на странице
   курса, продолжая нормально получать задания (или наоборот).

Плюс регресс-набор: связки с умолчанием `auto_assign=True` ведут себя ровно как
до фазы 6 (на проде их 80+, и трогать их поведение нельзя).

План: docs/specs/2026-08-06-plan-tsk231-mini-kursy-blokirovka.md, Фаза 6.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import me_service
from app.services.auth import identity_link_service
from app.services.course_dependencies_service import CourseDependenciesService
from app.services.learning_engine_service import LearningEngineService
from app.services import course_dependencies_enrollment_service as enroll_service


async def _create_student(db, *, prefix: str = "tsk231p6") -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-stud", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id


async def _create_course(db, *, title: str, course_uid: str | None = None) -> int:
    uid = course_uid or f"tsk231p6-{random.randint(10**8, 10**10)}"
    res = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, is_required, course_uid) "
            "VALUES (:t, 'self_guided', false, :uid) RETURNING id"
        ),
        {"t": title, "uid": uid},
    )
    cid = int(res.scalar_one())
    await db.commit()
    return cid


async def _new_task(db, *, course_id: int) -> int:
    """Курс без единого элемента тривиально COMPLETED (total_items=0) — чтобы
    зависимость реально блокировала, требуемому курсу нужен непройденный шаг."""
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    if difficulty_id is None:
        pytest.skip("Нет ни одной difficulty — задание не создать")
    res = await db.execute(
        text(
            "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid) "
            "VALUES (CAST(:tc AS jsonb), :cid, :did, :uid) RETURNING id"
        ),
        {
            "tc": '{"type": "SA", "question": "tsk231p6"}',
            "cid": course_id,
            "did": difficulty_id,
            "uid": f"tsk231p6-{random.randint(10**8, 10**10)}",
        },
    )
    tid = int(res.scalar_one())
    await db.commit()
    return tid


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "c": course_id},
    )
    await db.commit()


async def _add_parent(db, *, course_id: int, parent_course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id) "
            "VALUES (:c, :p) ON CONFLICT DO NOTHING"
        ),
        {"c": course_id, "p": parent_course_id},
    )
    await db.commit()


async def _raw_dependency(db, *, course_id: int, required_course_id: int) -> None:
    """Зависимость мимо сервиса — так её ставит методист между ПОДКУРСАМИ.

    Сервисный путь тут не годится: `_enroll_existing_students` попытался бы
    закрепить ученика на некорневом курсе, что запрещено триггером.
    """
    await db.execute(
        text(
            "INSERT INTO course_dependencies (course_id, required_course_id) "
            "VALUES (:c, :r) ON CONFLICT DO NOTHING"
        ),
        {"c": course_id, "r": required_course_id},
    )
    await db.commit()


async def _enrolled_ids(db, user_id: int) -> set[int]:
    res = await db.execute(
        text("SELECT course_id FROM user_courses WHERE user_id = :u"), {"u": user_id}
    )
    return {int(r[0]) for r in res.fetchall()}


async def _cleanup(db, *, user_ids: list[int], course_ids: list[int]) -> None:
    await db.execute(
        text("DELETE FROM user_courses WHERE user_id = ANY(:ids)"), {"ids": user_ids}
    )
    await db.execute(
        text("DELETE FROM student_course_state WHERE student_id = ANY(:ids)"),
        {"ids": user_ids},
    )
    await db.execute(
        text("DELETE FROM user_session WHERE user_id = ANY(:ids)"), {"ids": user_ids}
    )
    await db.execute(
        text("DELETE FROM identity_link WHERE user_id = ANY(:ids)"), {"ids": user_ids}
    )
    await db.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": user_ids})
    if course_ids:
        await db.execute(
            text(
                "DELETE FROM course_dependencies "
                "WHERE course_id = ANY(:ids) OR required_course_id = ANY(:ids)"
            ),
            {"ids": course_ids},
        )
        await db.execute(
            text(
                "DELETE FROM course_parents "
                "WHERE course_id = ANY(:ids) OR parent_course_id = ANY(:ids)"
            ),
            {"ids": course_ids},
        )
        await db.execute(
            text("DELETE FROM tasks WHERE course_id = ANY(:ids)"), {"ids": course_ids}
        )
        await db.execute(
            text("DELETE FROM courses WHERE id = ANY(:ids)"), {"ids": course_ids}
        )
    await db.commit()


# ───────────────────────── 6.1 Точечность назначения ────────────────────────


@pytest.mark.asyncio
async def test_targeted_dependency_does_not_enroll_anyone(db):
    """auto_assign=False: мини-курс не раздаётся уже зачисленным на основной.

    Зеркало теста фазы 1 (`..._enrolls_already_enrolled_students`): там
    доназначение обязательно, здесь — запрещено. Оба поведения нужны, режим
    выбирает методист.
    """
    main = await _create_course(db, title="p6 основной (точечный режим)")
    mini = await _create_course(db, title="p6 мини-курс повторения")
    student = await _create_student(db, prefix="p6-noenroll")
    await _enroll(db, student, main)
    try:
        await CourseDependenciesService().add_dependency(
            db, course_id=main, required_course_id=mini, auto_assign=False
        )
        enrolled = await _enrolled_ids(db, student)
        assert mini not in enrolled, (
            "точечная зависимость не должна раздавать мини-курс автоматически — "
            "иначе «назначаем адресно» отменяется самим фактом её создания"
        )
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, mini])


@pytest.mark.asyncio
async def test_targeted_dependency_skipped_by_transitive_walk(db):
    """Точечная связь обрывает транзитивный обход: A →(авто) B →(точечно) C.

    Без фильтра в рекурсивном шаге назначение A раздало бы и C — то есть
    точечный мини-курс уехал бы всем через вторую руку.
    """
    a = await _create_course(db, title="p6 транзит A")
    b = await _create_course(db, title="p6 транзит B (пререквизит)")
    c = await _create_course(db, title="p6 транзит C (точечный)")
    try:
        service = CourseDependenciesService()
        await service.add_dependency(db, course_id=a, required_course_id=b)
        await service.add_dependency(
            db, course_id=b, required_course_id=c, auto_assign=False
        )

        required = await enroll_service.collect_required_course_ids(db, [a])
        assert b in required, "обычный пререквизит обязан собираться как раньше"
        assert c not in required, (
            "точечная связь обязана обрывать ветку — иначе мини-курс раздаётся "
            "всем через транзитивный обход"
        )
    finally:
        await _cleanup(db, user_ids=[], course_ids=[a, b, c])


@pytest.mark.asyncio
async def test_impact_preview_is_zero_for_targeted_dependency(db):
    """Превью для методиста обязано совпадать с тем, что реально произойдёт.

    Цифра «заблокирует 35» на связке, которая не заблокирует никого, отпугнула
    бы методиста ровно от того сценария, ради которого флаг и вводился.
    """
    main = await _create_course(db, title="p6 превью основной")
    student_a = await _create_student(db, prefix="p6-prev-a")
    student_b = await _create_student(db, prefix="p6-prev-b")
    await _enroll(db, student_a, main)
    await _enroll(db, student_b, main)
    try:
        service = CourseDependenciesService()
        assert await service.count_affected_students(db, main) == 2
        assert await service.count_affected_students(db, main, auto_assign=False) == 0
    finally:
        await _cleanup(db, user_ids=[student_a, student_b], course_ids=[main])


# ─────────────────── 6.2 Блокирует только назначенных ───────────────────────


@pytest.mark.asyncio
async def test_targeted_dependency_does_not_block_unassigned_student(db):
    """Ученик без назначенного мини-курса продолжает учиться как ни в чём не бывало.

    Это тот самый вопрос оператора: «для разобравшихся с темой блокировки не
    будет?». До фазы 6 ответ был «будет» — compute_course_state не смотрит в
    user_courses и даёт неназначенному 0 из N, то есть NOT_STARTED.
    """
    main = await _create_course(db, title="p6 не блокирует основной")
    mini = await _create_course(db, title="p6 не блокирует мини")
    await _new_task(db, course_id=main)
    await _new_task(db, course_id=mini)
    student = await _create_student(db, prefix="p6-free")
    await _enroll(db, student, main)
    try:
        await CourseDependenciesService().add_dependency(
            db, course_id=main, required_course_id=mini, auto_assign=False
        )
        result = await LearningEngineService().resolve_next_item(
            db, student, root_course_id=main
        )
        assert result.type != "blocked_dependency", (
            f"ученик без назначенного мини-курса заблокирован (type={result.type}) — "
            "точечная зависимость затронула весь поток"
        )
        assert result.type == "task", f"ожидали задание основного курса, получили {result.type}"
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, mini])


@pytest.mark.asyncio
async def test_targeted_dependency_blocks_assigned_student(db):
    """А вот адресату мини-курса замок ставится — иначе флаг бессмысленен."""
    main = await _create_course(db, title="p6 блокирует основной")
    mini = await _create_course(
        db, title="Мини-курс: точный диапазон", course_uid=f"p6-blk-{random.randint(10**8, 10**10)}"
    )
    await _new_task(db, course_id=main)
    await _new_task(db, course_id=mini)
    student = await _create_student(db, prefix="p6-target")
    await _enroll(db, student, main)
    try:
        await CourseDependenciesService().add_dependency(
            db, course_id=main, required_course_id=mini, auto_assign=False
        )
        # Методист назначает мини-курс адресно — ровно этот шаг включает замок.
        await _enroll(db, student, mini)

        result = await LearningEngineService().resolve_next_item(
            db, student, root_course_id=main
        )
        assert result.type == "blocked_dependency", (
            f"назначенный мини-курс обязан блокировать основной, получили {result.type}"
        )
        assert result.dependency_course_id == mini
        assert result.dependency_course_title == "Мини-курс: точный диапазон"
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, mini])


@pytest.mark.asyncio
async def test_unassigning_mini_course_lifts_the_block(db):
    """Снятие назначения снимает замок — второй выход помимо прохождения.

    Нужен методисту: назначил не тому — вернул на место, не заставляя проходить
    16 заданий ради разблокировки.
    """
    main = await _create_course(db, title="p6 снятие основной")
    mini = await _create_course(db, title="p6 снятие мини")
    await _new_task(db, course_id=main)
    await _new_task(db, course_id=mini)
    student = await _create_student(db, prefix="p6-unassign")
    await _enroll(db, student, main)
    await _enroll(db, student, mini)
    try:
        await CourseDependenciesService().add_dependency(
            db, course_id=main, required_course_id=mini, auto_assign=False
        )
        blocked = await LearningEngineService().resolve_next_item(
            db, student, root_course_id=main
        )
        assert blocked.type == "blocked_dependency"

        await db.execute(
            text(
                "UPDATE user_courses SET is_active = false "
                "WHERE user_id = :u AND course_id = :c"
            ),
            {"u": student, "c": mini},
        )
        await db.commit()

        freed = await LearningEngineService().resolve_next_item(
            db, student, root_course_id=main
        )
        assert freed.type != "blocked_dependency", (
            "снятое назначение обязано снимать замок — иначе методист не может "
            "откатить ошибочное назначение"
        )
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, mini])


# ───────── 6.2a Доступность ≠ назначение (регресс, пойманный гейтом) ────────


@pytest.mark.asyncio
async def test_subcourse_dependency_still_blocks(db):
    """Зависимость между ПОДКУРСАМИ обязана продолжать блокировать.

    Регресс, найденный merge-gate'ом на живых данных. Первая версия правила
    звучала как «блокирует только НАЗНАЧЕННЫЙ курс» — а закрепить ученика можно
    лишь на КОРНЕВОМ курсе (триггер `trg_check_user_course_no_parents`), тогда
    как 79 из 81 прод-зависимости требуют подкурс («Списки» после «Циклов»
    внутри «Python для ЕГЭ»). Проверка по `user_courses` для них ложна всегда и
    молча сняла бы 214 из 248 действующих блокировок у 38 учеников.

    Верное правило — ДОСТУПНОСТЬ: подкурс достижим через свой корень.
    """
    root = await _create_course(db, title="p6 корень с главами")
    chapter_a = await _create_course(db, title="p6 глава A (блокируемая)")
    chapter_b = await _create_course(db, title="p6 глава B (требуемая)")
    await _add_parent(db, course_id=chapter_a, parent_course_id=root)
    await _add_parent(db, course_id=chapter_b, parent_course_id=root)
    await _new_task(db, course_id=chapter_a)
    await _new_task(db, course_id=chapter_b)

    student = await _create_student(db, prefix="p6-subcourse")
    # Закрепление только на корне — иначе триггер БД не даст.
    await _enroll(db, student, root)
    try:
        await _raw_dependency(db, course_id=chapter_a, required_course_id=chapter_b)

        body = await me_service.get_syllabus_states(db, student, root)
        assert chapter_a in body["blocked_courses"], (
            "порядок прохождения глав внутри курса перестал работать — "
            "правило спутало «назначен» с «доступен»"
        )
        dep_rows = [d for d in body["blocked_dependencies"] if d["course_id"] == chapter_a]
        assert len(dep_rows) == 1
        assert dep_rows[0]["required_course_id"] == chapter_b
    finally:
        await _cleanup(
            db, user_ids=[student], course_ids=[root, chapter_a, chapter_b]
        )


@pytest.mark.asyncio
async def test_unreachable_dependency_does_not_block(db):
    """Требуемый курс из ЧУЖОГО дерева — замок без выхода, снимаем.

    На проде таких пар 7 (курсы Excel): узел требует главу, которой у ученика
    нет ни в одном дереве. Пройти её физически нельзя, и до фазы 6 замок висел
    вечно — тот же класс, что закрывал tsk-261.
    """
    root = await _create_course(db, title="p6 свой корень")
    mine = await _create_course(db, title="p6 своя глава")
    await _add_parent(db, course_id=mine, parent_course_id=root)
    await _new_task(db, course_id=mine)

    alien_root = await _create_course(db, title="p6 чужой корень")
    alien = await _create_course(db, title="p6 чужая глава")
    await _add_parent(db, course_id=alien, parent_course_id=alien_root)
    await _new_task(db, course_id=alien)

    student = await _create_student(db, prefix="p6-unreachable")
    await _enroll(db, student, root)  # чужой корень НЕ назначен
    try:
        await _raw_dependency(db, course_id=mine, required_course_id=alien)

        body = await me_service.get_syllabus_states(db, student, root)
        assert mine not in body["blocked_courses"], (
            "замок на недостижимом курсе — выхода из него у ученика нет"
        )
    finally:
        await _cleanup(
            db, user_ids=[student], course_ids=[root, mine, alien_root, alien]
        )


@pytest.mark.asyncio
async def test_cross_root_prerequisite_survives_root_filter(db):
    """Пререквизит из ДРУГОГО корня блокирует и при фильтре по корню.

    Живая связка проды: «ЕГЭ по информатике» (112) требует «Python для ЕГЭ» (88)
    — 33 ученика. SPW всегда зовёт next-item с `root_course_id`, и если считать
    доступность по отфильтрованному списку, второй корень выпадает из виду и
    замок молча исчезает. Ловушка замыкания: `active` ниже переопределяется
    фильтром, поэтому множество строится по отдельной привязке `all_active`.
    """
    main = await _create_course(db, title="p6 кросс-корень основной")
    prereq = await _create_course(db, title="p6 кросс-корень пререквизит")
    await _new_task(db, course_id=main)
    await _new_task(db, course_id=prereq)
    student = await _create_student(db, prefix="p6-crossroot")
    await _enroll(db, student, main)
    try:
        # Режим по умолчанию: пререквизит доназначится сам (tsk-261).
        await CourseDependenciesService().add_dependency(
            db, course_id=main, required_course_id=prereq
        )
        assert prereq in await _enrolled_ids(db, student)

        result = await LearningEngineService().resolve_next_item(
            db, student, root_course_id=main
        )
        assert result.type == "blocked_dependency", (
            "пререквизит из другого корня перестал блокировать при фильтре "
            f"root_course_id — получили {result.type}"
        )
        assert result.dependency_course_id == prereq
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, prereq])


# ────────────── 6.3 Движок и синтабус обязаны говорить одно ─────────────────


@pytest.mark.asyncio
async def test_syllabus_agrees_with_engine_for_unassigned_student(db):
    """Молчаливое расхождение: движок пускает, синтабус рисует замок.

    Условие «блокирует только назначенный курс» живёт в ДВУХ местах —
    `resolve_next_item` и `_BLOCKED_COURSES_SQL`. Забыть одно из них ничего не
    уронит: ученик просто увидит замок на странице курса и при этом продолжит
    получать задания.
    """
    main = await _create_course(db, title="p6 синтабус основной")
    mini = await _create_course(db, title="p6 синтабус мини")
    await _new_task(db, course_id=main)
    await _new_task(db, course_id=mini)
    student = await _create_student(db, prefix="p6-syl-free")
    await _enroll(db, student, main)
    try:
        await CourseDependenciesService().add_dependency(
            db, course_id=main, required_course_id=mini, auto_assign=False
        )
        engine_result = await LearningEngineService().resolve_next_item(
            db, student, root_course_id=main
        )
        body = await me_service.get_syllabus_states(db, student, main)

        assert engine_result.type != "blocked_dependency"
        assert main not in body["blocked_courses"], (
            "синтабус рисует замок там, где движок пускает — ученик видит "
            "блокировку и одновременно получает задания"
        )
        assert not [d for d in body["blocked_dependencies"] if d["course_id"] == main]
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, mini])


@pytest.mark.asyncio
async def test_syllabus_agrees_with_engine_for_assigned_student(db):
    """Обратная сторона того же инварианта: назначен — замок виден обоим путям."""
    main = await _create_course(db, title="p6 синтабус блок основной")
    mini = await _create_course(db, title="p6 синтабус блок мини")
    await _new_task(db, course_id=main)
    await _new_task(db, course_id=mini)
    student = await _create_student(db, prefix="p6-syl-blk")
    await _enroll(db, student, main)
    await _enroll(db, student, mini)
    try:
        await CourseDependenciesService().add_dependency(
            db, course_id=main, required_course_id=mini, auto_assign=False
        )
        engine_result = await LearningEngineService().resolve_next_item(
            db, student, root_course_id=main
        )
        body = await me_service.get_syllabus_states(db, student, main)

        assert engine_result.type == "blocked_dependency"
        assert main in body["blocked_courses"], (
            "движок блокирует, а синтабус — нет: ученик упирается в стену без "
            "объяснения, что именно пройти"
        )
        dep_rows = [d for d in body["blocked_dependencies"] if d["course_id"] == main]
        assert len(dep_rows) == 1
        assert dep_rows[0]["required_course_id"] == mini
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, mini])


# ──────────────────── 6.4 Регресс: прежний режим не тронут ──────────────────


@pytest.mark.asyncio
async def test_auto_assign_default_keeps_previous_behaviour(db):
    """Умолчание (80+ прод-связок): раздаётся всем и блокирует всех, как раньше."""
    main = await _create_course(db, title="p6 регресс основной")
    mini = await _create_course(db, title="p6 регресс пререквизит")
    await _new_task(db, course_id=main)
    await _new_task(db, course_id=mini)
    student = await _create_student(db, prefix="p6-regress")
    await _enroll(db, student, main)
    try:
        # Флаг не передаём вовсе — ровно так зовут существующие вызовы.
        await CourseDependenciesService().add_dependency(
            db, course_id=main, required_course_id=mini
        )
        enrolled = await _enrolled_ids(db, student)
        assert mini in enrolled, "пререквизит обязан доназначаться (tsk-261/фаза 1)"

        result = await LearningEngineService().resolve_next_item(
            db, student, root_course_id=main
        )
        assert result.type == "blocked_dependency"
        assert result.dependency_course_id == mini

        body = await me_service.get_syllabus_states(db, student, main)
        assert main in body["blocked_courses"]
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, mini])


@pytest.mark.asyncio
async def test_bulk_add_respects_targeted_mode(db):
    """Массовый путь методиста (реальный write-путь SPW) тоже знает про режим."""
    main = await _create_course(db, title="p6 bulk основной")
    mini_a = await _create_course(db, title="p6 bulk мини A")
    mini_b = await _create_course(db, title="p6 bulk мини B")
    student = await _create_student(db, prefix="p6-bulk")
    await _enroll(db, student, main)
    try:
        added = await CourseDependenciesService().bulk_add_dependencies(
            db, main, [mini_a, mini_b], auto_assign=False
        )
        assert len(added) == 2

        enrolled = await _enrolled_ids(db, student)
        assert not ({mini_a, mini_b} & enrolled), (
            "bulk-путь раздал точечные мини-курсы — режим до репозитория не доехал"
        )

        res = await db.execute(
            text(
                "SELECT bool_or(auto_assign) FROM course_dependencies "
                "WHERE course_id = :c"
            ),
            {"c": main},
        )
        assert res.scalar_one() is False, "флаг не записался в БД"
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, mini_a, mini_b])
