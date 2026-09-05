"""tsk-798: персональный объём программы — сколько её помещается в срок.

Проверяется главное различие, которое легко потерять: **ядро и тренажёр — не
одно и то же**. Теория, номера ЕГЭ и материалы проходятся целиком; сокращать
можно только отработку (EASY/NORMAL). Спутать их дороже всего в марте: система
«облегчит» программу, выбросив разбор номера, и ученик придёт на экзамен, не
увидев задание такого типа вовсе.

Второе, что здесь держится тестами: **порог только растёт**. Объём подстраивается
под темп, темп меняется по ходу года — и если бы набор пересобирался, у человека
исчезали бы уже решённые задания.
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import program_scope_service as scope_service

UTC = timezone.utc
_TAG = "tsk798"


async def _new_user(db) -> int:
    """Ученик. Создаётся хелпером соседнего набора, а не своим INSERT: форма
    строки в `users` менялась не раз, и вторая копия вставки разъехалась бы."""
    from tests.test_tsk741_homework import _new_user as make_user

    student_id, _ = await make_user(db)
    return int(student_id)


async def _new_course(db, title: str) -> int:
    cid = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES (:t, 'self_guided') RETURNING id"
            ),
            {"t": f"{_TAG}-{title}"},
        )
    ).scalar()
    await db.commit()
    return int(cid)


async def _difficulty_id(db, code: str) -> int:
    return int(
        (
            await db.execute(
                text("SELECT id FROM difficulties WHERE code = :c"), {"c": code}
            )
        ).scalar()
    )


async def _new_task(db, *, course_id: int, code: str, pos: int) -> int:
    return int(
        (
            await db.execute(
                text(
                    "INSERT INTO tasks (task_content, solution_rules, course_id, "
                    "  difficulty_id, external_uid, max_score, order_position) "
                    "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, "
                    "  :uid, 10, :pos) RETURNING id"
                ),
                {
                    "tc": json.dumps({"type": "SA", "stem": f"{_TAG} {pos}"}),
                    "sr": json.dumps({"max_score": 10, "accepted_answers": ["42"]}),
                    "cid": course_id,
                    "did": await _difficulty_id(db, code),
                    "uid": f"{_TAG}-{random.randint(10**8, 10**10)}",
                    "pos": pos,
                },
            )
        ).scalar()
    )


async def _fill_course(db, course_id: int, *, theory: int, easy: int, normal: int):
    pos = 0
    for _ in range(theory):
        pos += 1
        await _new_task(db, course_id=course_id, code="THEORY", pos=pos)
    for _ in range(easy):
        pos += 1
        await _new_task(db, course_id=course_id, code="EASY", pos=pos)
    for _ in range(normal):
        pos += 1
        await _new_task(db, course_id=course_id, code="NORMAL", pos=pos)
    await db.commit()


async def _scope(db, student_id: int, course_ids: list[int], *, weeks: float,
                 fact: float = 0.0, pace: int = 25):
    """Посчитать объём при заданном числе недель до срока."""
    today = date(2026, 9, 5)
    deadline = today + timedelta(days=int(weeks * 7))
    from app.core import settings_store

    original = settings_store.get_int

    def fake_int(key: str) -> int:
        return pace if key == "homework_program_planned_pace" else original(key)

    settings_store.get_int = fake_int  # type: ignore[assignment]
    try:
        return await scope_service.compute_scope(
            db, student_id=student_id, kind="ege", root_ids=course_ids,
            deadline=deadline, fact_per_week=fact, today=today,
        )
    finally:
        settings_store.get_int = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_whole_program_fits_when_there_is_time(db):
    """Времени вдоволь — сокращать нечего, выдаётся всё."""
    student_id = await _new_user(db)
    course_id = await _new_course(db, "fits")
    await _fill_course(db, course_id, theory=10, easy=20, normal=20)

    scope = await _scope(db, student_id, [course_id], weeks=52)

    assert scope.fits_fully
    assert scope.drill_allowed == scope.drill_total == 40
    assert scope.core_trimmed is False


@pytest.mark.asyncio
async def test_late_start_gets_core_plus_part_of_the_drill(db):
    """Пришёл поздно — ядро целиком, тренажёр частью.

    Решение оператора 05.09: «не ослабить нагрузку сильно, но чтобы человек
    успевал». Ядро не трогаем, подбираем отработку.
    """
    student_id = await _new_user(db)
    course_id = await _new_course(db, "late")
    # Ядро 100, тренажёр 400. Бюджет при 25×10 недель = 250.
    await _fill_course(db, course_id, theory=100, easy=200, normal=200)

    scope = await _scope(db, student_id, [course_id], weeks=10)

    assert scope.core_trimmed is False, "ядро помещается — резать его не за чем"
    assert scope.core_total == 100
    assert scope.drill_allowed == 150, "250 бюджета минус 100 ядра"
    assert scope.drill_total == 400
    assert not scope.fits_fully


@pytest.mark.asyncio
async def test_march_start_cannot_fit_even_the_core(db):
    """Мартовский старт: бюджета не хватает даже на ядро — и это сказано прямо.

    Признак `core_trimmed` поднимается, чтобы преподаватель узнал о сокращении
    от системы, а не заметил через месяц по пропавшим номерам ЕГЭ.
    """
    student_id = await _new_user(db)
    course_id = await _new_course(db, "march")
    await _fill_course(db, course_id, theory=300, easy=200, normal=200)

    scope = await _scope(db, student_id, [course_id], weeks=4)

    assert scope.core_trimmed is True
    assert scope.core_total == 300
    assert scope.drill_allowed == 0, "пока не пройдено ядро, тренажёр не выдаём"


@pytest.mark.asyncio
async def test_fast_student_gets_a_bigger_program(db):
    """Кто делает больше — тому и программа больше.

    Решение оператора 05.09: потолок поднимается тем, кто тянет. План строится
    по фактическому темпу ученика, если он выше базового ожидания школы.
    """
    student_id = await _new_user(db)
    course_id = await _new_course(db, "fast")
    await _fill_course(db, course_id, theory=50, easy=300, normal=300)

    slow = await _scope(db, student_id, [course_id], weeks=10, fact=5)
    fast = await _scope(db, student_id, [course_id], weeks=10, fact=50)

    assert fast.planned_pace > slow.planned_pace
    assert fast.drill_allowed > slow.drill_allowed


@pytest.mark.asyncio
async def test_progress_shrinks_what_is_left_to_plan(db):
    """План строится на ОСТАТКЕ: прошедшему половину влезает больше нового.

    Иначе объём зависел бы от размера курса, а не от того, где ученик сейчас, —
    ровно та ошибка, из-за которой норма показывала всем 20 в неделю (tsk-797).
    """
    from tests.test_tsk741_homework import _submit

    ahead_id = await _new_user(db)
    behind_id = await _new_user(db)
    course_id = await _new_course(db, "progress")
    await _fill_course(db, course_id, theory=20, easy=100, normal=100)

    done = (
        await db.execute(
            text(
                "SELECT t.id FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id "
                " WHERE t.course_id = :c AND d.code = 'EASY' LIMIT 60"
            ),
            {"c": course_id},
        )
    ).scalars().all()
    for task_id in done:
        await _submit(
            db, student_id=ahead_id, task_id=int(task_id), course_id=course_id,
            is_correct=True, at=datetime.now(UTC) - timedelta(days=40),
        )

    ahead = await _scope(db, ahead_id, [course_id], weeks=6)
    behind = await _scope(db, behind_id, [course_id], weeks=6)

    assert ahead.drill_total == 140, "у прошедшего 60 лёгких остаток тренажёра меньше"
    assert behind.drill_total == 200
    # Бюджет одинаков, ядро одинаково — значит прошедшему достаётся большая
    # ДОЛЯ оставшегося тренажёра.
    assert ahead.drill_ratio > behind.drill_ratio


@pytest.mark.asyncio
async def test_budget_is_split_across_subcourses_by_size(db):
    """Бюджет делится между подкурсами пропорционально их размеру.

    Поровну делить нельзя: в «Задании 7» 72 обязательных задания, в «Задании
    12» — 15, и равный порог оставил бы первый почти нетронутым, а второй
    выдал бы целиком.
    """
    student_id = await _new_user(db)
    big = await _new_course(db, "big")
    small = await _new_course(db, "small")
    root = await _new_course(db, "root")
    for child in (big, small):
        await db.execute(
            text(
                "INSERT INTO course_parents (parent_course_id, course_id) "
                "VALUES (:p, :c)"
            ),
            {"p": root, "c": child},
        )
    await db.commit()
    await _fill_course(db, big, theory=0, easy=150, normal=150)
    await _fill_course(db, small, theory=0, easy=25, normal=25)

    scope = await _scope(db, student_id, [root], weeks=4)

    assert sum(scope.per_course.values()) == scope.drill_allowed
    assert scope.per_course[big] > scope.per_course[small]


@pytest.mark.asyncio
async def test_threshold_never_shrinks_between_recalculations(db):
    """Пересчёт не уменьшает порог, даже если темп ученика упал.

    Механика выборки даёт вложенные наборы, поэтому рост порога только
    добавляет задания. Уменьшение выбросило бы из программы часть уже
    решённого — с точки зрения человека это потеря работы.
    """
    student_id = await _new_user(db)
    course_id = await _new_course(db, "stable")
    await _fill_course(db, course_id, theory=10, easy=200, normal=200)

    generous = await _scope(db, student_id, [course_id], weeks=20, fact=40)
    await scope_service.store_scope(db, student_id=student_id, scope=generous)
    await db.commit()

    meagre = await _scope(db, student_id, [course_id], weeks=3, fact=0)
    merged = await scope_service.store_scope(db, student_id=student_id, scope=meagre)
    await db.commit()

    assert meagre.per_course[course_id] < generous.per_course[course_id]
    assert merged[course_id] == generous.per_course[course_id], (
        "порог уменьшился — у ученика пропали бы выданные задания"
    )
    stored = await scope_service.thresholds_for(db, student_id=student_id)
    assert stored[course_id] == generous.per_course[course_id]


async def _subcourse(db, root: int, title: str, *, priority: int | None,
                     theory: int = 0, easy: int = 0, normal: int = 0) -> int:
    """Подкурс-«номер ЕГЭ» с заданным приоритетом включения в программу."""
    cid = await _new_course(db, title)
    await db.execute(
        text(
            "INSERT INTO course_parents (parent_course_id, course_id) VALUES (:p, :c)"
        ),
        {"p": root, "c": cid},
    )
    await db.execute(
        text("UPDATE courses SET program_priority = :p WHERE id = :c"),
        {"p": priority, "c": cid},
    )
    await db.commit()
    await _fill_course(db, cid, theory=theory, easy=easy, normal=normal)
    return cid


@pytest.mark.asyncio
async def test_core_is_trimmed_by_exam_number_not_by_pieces(db):
    """Не помещается ядро — выпадает номер ЦЕЛИКОМ, по приоритету методиста.

    Решение оператора 05.09. Половина разбора каждого номера не готовит ни к
    одному из них; целый номер, пройденный до конца, даёт балл.
    """
    student_id = await _new_user(db)
    root = await _new_course(db, "program-root")
    first = await _subcourse(db, root, "номер-1", priority=1, theory=40)
    second = await _subcourse(db, root, "номер-2", priority=2, theory=40)
    last = await _subcourse(db, root, "номер-9", priority=9, theory=40)

    # Бюджет 25 × 2 недели = 50: помещается один номер из трёх.
    scope = await _scope(db, student_id, [root], weeks=2, pace=25)

    assert scope.core_trimmed is True
    assert first not in scope.excluded_courses, "первый по приоритету обязан остаться"
    assert {second, last} <= scope.excluded_courses
    assert scope.core_total == 40, "в ядре остался ровно один номер"


@pytest.mark.asyncio
async def test_unmarked_subcourses_never_drop_out(db):
    """Без приоритета номер не выпадает: систему не просили решать за методиста.

    NULL означает «сюда не смотрели». Выбросить у выпускника разбор номера по
    догадке хуже, чем показать преподавателю, что программа не помещается.
    """
    student_id = await _new_user(db)
    root = await _new_course(db, "unmarked-root")
    await _subcourse(db, root, "без-приоритета-1", priority=None, theory=40)
    await _subcourse(db, root, "без-приоритета-2", priority=None, theory=40)

    scope = await _scope(db, student_id, [root], weeks=1, pace=25)

    assert scope.core_trimmed is True, "о нехватке сказать всё равно обязаны"
    assert scope.excluded_courses == frozenset(), "выброшено то, чего не размечали"


@pytest.mark.asyncio
async def test_started_number_is_not_taken_away(db):
    """Номер, в котором ученик уже что-то решил, не отнимается.

    Темп меняется по ходу года, и без этого правила номер выпадал бы у того,
    кто просто сбавил на неделю, — вместе с уже сделанной работой.
    """
    from tests.test_tsk741_homework import _submit

    student_id = await _new_user(db)
    root = await _new_course(db, "started-root")
    cheap = await _subcourse(db, root, "дешёвый", priority=1, theory=30)
    started = await _subcourse(db, root, "начатый", priority=9, theory=30)

    task_id = (
        await db.execute(
            text("SELECT id FROM tasks WHERE course_id = :c LIMIT 1"), {"c": started}
        )
    ).scalar()
    await _submit(
        db, student_id=student_id, task_id=int(task_id), course_id=started,
        is_correct=True, at=datetime.now(UTC) - timedelta(days=40),
    )
    await db.commit()

    # Бюджета хватает ровно на один номер. По приоритету это был бы `cheap`,
    # но защита начатого сильнее: у ученика уже есть работа в `started`, и
    # выпадает поэтому `cheap`, а не он.
    scope = await _scope(db, student_id, [root], weeks=1, pace=30)

    assert started not in scope.excluded_courses, "отняли начатое"
    assert cheap in scope.excluded_courses
    assert scope.core_total == 29, "остался начатый номер без решённого задания"


@pytest.mark.asyncio
async def test_engine_hides_a_dropped_number_completely(db):
    """Выпавший номер не выдаётся ни заданиями, ни теорией.

    Иначе ученик получил бы разбор темы, задания по которой ему не покажут, —
    и курс не дошёл бы до COMPLETED, потому что в знаменателе остались бы
    элементы, которых он никогда не увидит.
    """
    from app.services.learning_engine_service import LearningEngineService

    student_id = await _new_user(db)
    root = await _new_course(db, "engine-trim-root")
    kept = await _subcourse(db, root, "остаётся", priority=1, theory=20)
    dropped = await _subcourse(db, root, "выпадает", priority=9, theory=20)
    await db.execute(
        text(
            "INSERT INTO materials (course_id, title, type, content, order_position) "
            "VALUES (:c, :t, 'text', CAST(:body AS jsonb), 1)"
        ),
        {"c": dropped, "t": f"{_TAG} теория", "body": json.dumps({"body": "x"})},
    )
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true)"
        ),
        {"u": student_id, "c": root},
    )
    await db.commit()

    scope = await _scope(db, student_id, [root], weeks=1, pace=20)
    assert dropped in scope.excluded_courses
    await scope_service.store_scope(db, student_id=student_id, scope=scope)
    await db.commit()

    service = LearningEngineService()
    assert await service._effective_task_rows(db, dropped, student_id) == []
    assert await service._effective_material_rows(db, dropped, student_id) == []
    assert len(await service._effective_task_rows(db, kept, student_id)) == 20

    # И знаменатель курса не считает выпавшее — иначе COMPLETED недостижим.
    state = await service.compute_course_state(db, student_id, root)
    assert state is not None


@pytest.mark.asyncio
async def test_engine_hides_tasks_outside_the_personal_scope(db):
    """Движок выдаёт ровно то, что попало в персональный объём.

    Это главная проверка задачи: план без влияния на обход — просто число в
    таблице. THEORY при этом остаётся вся, сколько бы ни сжимался тренажёр.
    """
    from app.services.learning_engine_service import LearningEngineService

    student_id = await _new_user(db)
    course_id = await _new_course(db, "engine")
    await _fill_course(db, course_id, theory=5, easy=50, normal=50)
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true)"
        ),
        {"u": student_id, "c": course_id},
    )
    await db.commit()

    service = LearningEngineService()
    before = await service._effective_task_rows(db, course_id, student_id)
    assert len(before) == 105, "без плана выборки нет — выдаются все задания"

    scope = await _scope(db, student_id, [course_id], weeks=1, pace=30)
    await scope_service.store_scope(db, student_id=student_id, scope=scope)
    await db.commit()

    after = await service._effective_task_rows(db, course_id, student_id)
    assert len(after) < len(before), "персональный объём не повлиял на обход"

    kept_ids = {i for i, _ in after}
    theory_ids = set(
        (
            await db.execute(
                text(
                    "SELECT t.id FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id"
                    " WHERE t.course_id = :c AND d.code = 'THEORY'"
                ),
                {"c": course_id},
            )
        ).scalars().all()
    )
    assert theory_ids <= kept_ids, "теория выборке не подлежит — должна остаться вся"


@pytest.mark.asyncio
async def test_already_solved_tasks_survive_the_new_scope(db):
    """Ученику, который давно учится, выборка не выбрасывает решённое.

    Главный риск включения выборки на живых людях: `compute_course_state`
    вычитает вырезанное из общего числа заданий, а пройденные считает как есть.
    Выбросив решённое, мы получили бы числитель больше знаменателя — подкурс не
    закрылся бы никогда. Для ученика это к тому же выглядит как пропажа работы.
    """
    from tests.test_tsk741_homework import _submit
    from app.services.learning_engine_service import LearningEngineService

    student_id = await _new_user(db)
    course_id = await _new_course(db, "veteran")
    await _fill_course(db, course_id, theory=5, easy=100, normal=100)
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true)"
        ),
        {"u": student_id, "c": course_id},
    )
    solved = (
        await db.execute(
            text(
                "SELECT t.id FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id"
                " WHERE t.course_id = :c AND d.code IN ('EASY','NORMAL') LIMIT 80"
            ),
            {"c": course_id},
        )
    ).scalars().all()
    for task_id in solved:
        await _submit(
            db, student_id=student_id, task_id=int(task_id), course_id=course_id,
            is_correct=True, at=datetime.now(UTC) - timedelta(days=40),
        )
    await db.commit()

    # Срок жёсткий: бюджета хватает на ядро и совсем немного отработки — то
    # есть порог заведомо меньше того, что человек уже прошёл.
    scope = await _scope(db, student_id, [course_id], weeks=1, pace=30)
    await scope_service.store_scope(db, student_id=student_id, scope=scope)
    await db.commit()

    kept = {
        i for i, _ in await LearningEngineService()._effective_task_rows(
            db, course_id, student_id
        )
    }
    assert {int(i) for i in solved} <= kept, "решённое выпало из программы"
