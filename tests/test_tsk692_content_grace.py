"""tsk-692: содержимое, добавленное после прохождения темы, не становится долгом.

Правило (выбрано оператором 27.08): новое обязательное содержимое приходит тому,
кто тему уже прошёл, как **рекомендуемое**; тому, кто не проходил, оно остаётся
**обязательным**.

Сценарии:
  1. Прошедший тему: материал, заведённый позже, обязательность теряет.
  2. **Обратный случай** — не проходивший тему видит тот же материал
     обязательным. Проверяется отдельным тестом, а не рассуждением: это та
     половина правила, которая ломается молча.
  3. Незакрытый старый элемент отменяет прощение всего узла: узел не был
     пройден, значит и «после прохождения» не про него.
  4. Задание без даты появления (`created_at IS NULL`, всё, что заведено до
     tsk-692) не прощается никогда и блокирует прощение узла — так у выката
     нулевая цена по заданиям.
  5. Прощённое перестаёт быть долгом и в состоянии курса: `compute_course_state`
     доходит до COMPLETED, а не откатывается назад от каждой правки курса.
  6. Правило смотрит на предков: целиком новый подкурс внутри пройденного узла
     тоже приходит рекомендуемым.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.content_grace_service import compute_graced_items
from app.services.learning_engine_service import LearningEngineService

# Опорные моменты: «давно» — когда тема заводилась и проходилась, «позже» —
# когда методист дописал в неё новое.
LONG_AGO = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)


async def _new_course(db, title: str) -> int:
    res = await db.execute(
        text(
            "INSERT INTO courses (title, access_level) "
            "VALUES (:t, 'self_guided') RETURNING id"
        ),
        {"t": f"{title} {uuid4().hex[:8]}"},
    )
    return int(res.scalar_one())


async def _link_child(db, parent_id: int, child_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
            "VALUES (:c, :p, 1)"
        ),
        {"c": child_id, "p": parent_id},
    )


async def _new_material(db, *, course_id: int, created_at: datetime, title: str) -> int:
    res = await db.execute(
        text(
            "INSERT INTO materials (title, type, content, course_id, created_at, updated_at) "
            "VALUES (:t, 'text', CAST('{}' AS jsonb), :c, :ts, :ts) RETURNING id"
        ),
        {"t": title, "c": course_id, "ts": created_at},
    )
    return int(res.scalar_one())


async def _new_task(db, *, course_id: int, created_at: datetime | None) -> int:
    difficulty_id = int(
        (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar_one()
    )
    res = await db.execute(
        text(
            "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid, created_at) "
            "VALUES (CAST(:tc AS jsonb), :c, :d, :uid, :ts) RETURNING id"
        ),
        {
            "tc": '{"type": "SA", "question": "tsk692"}',
            "c": course_id,
            "d": difficulty_id,
            "uid": f"tsk692-{uuid4().hex[:10]}",
            "ts": created_at,
        },
    )
    return int(res.scalar_one())


async def _new_student(db, prefix: str) -> int:
    res = await db.execute(
        text("INSERT INTO users (full_name) VALUES (:n) RETURNING id"),
        {"n": f"{prefix} tsk692 {uuid4().hex[:6]}"},
    )
    return int(res.scalar_one())


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true)"
        ),
        {"u": user_id, "c": course_id},
    )


async def _complete_material(db, *, user_id: int, material_id: int, at: datetime) -> None:
    await db.execute(
        text(
            "INSERT INTO student_material_progress "
            "(student_id, material_id, status, completed_at, source) "
            "VALUES (:u, :m, 'completed', :ts, 'system')"
        ),
        {"u": user_id, "m": material_id, "ts": at},
    )


async def _pass_task(
    db, *, user_id: int, task_id: int, course_id: int, root_course_id: int, at: datetime
) -> None:
    """Верно решённое задание — как реальный сабмит ученика."""
    attempt_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                    "VALUES (:u, :c, :r, 'test_tsk692') RETURNING id"
                ),
                {"u": user_id, "c": course_id, "r": root_course_id},
            )
        ).scalar_one()
    )
    await db.execute(
        text(
            "INSERT INTO task_results "
            "(user_id, task_id, attempt_id, score, max_score, is_correct, submitted_at) "
            "VALUES (:u, :t, :a, 10, 10, true, :ts)"
        ),
        {"u": user_id, "t": task_id, "a": attempt_id, "ts": at},
    )


@pytest_asyncio.fixture
async def passed_topic(db):
    """Тема из двух старых элементов; ученик прошёл её целиком «давно».

    После прохождения в тему добавлен ещё один материал — тот самый случай,
    ради которого задача и заведена.
    """
    course_id = await _new_course(db, "tsk692 тема")
    old_material = await _new_material(
        db, course_id=course_id, created_at=LONG_AGO, title="Старый материал"
    )
    old_task = await _new_task(db, course_id=course_id, created_at=LONG_AGO)
    fresh_material = await _new_material(
        db, course_id=course_id, created_at=LATER, title="Дописано позже"
    )

    student = await _new_student(db, "прошёл")
    await _enroll(db, student, course_id)
    await _complete_material(
        db, user_id=student, material_id=old_material, at=LONG_AGO + timedelta(days=1)
    )
    await _pass_task(
        db,
        user_id=student,
        task_id=old_task,
        course_id=course_id,
        root_course_id=course_id,
        at=LONG_AGO + timedelta(days=2),
    )
    await db.commit()
    return {
        "course_id": course_id,
        "old_material": old_material,
        "old_task": old_task,
        "fresh_material": fresh_material,
        "student": student,
    }


@pytest.mark.asyncio
async def test_material_added_after_completion_is_not_a_debt(db, passed_topic):
    """Сценарий 1: прошедшему тему новый материал приходит без обязательности."""
    graced = await compute_graced_items(
        db, passed_topic["student"], passed_topic["course_id"]
    )
    assert passed_topic["fresh_material"] in graced.materials, (
        "Материал, заведённый после того, как ученик закрыл тему, обязан "
        "потерять обязательность — иначе правка курса создаёт долг"
    )

    engine = LearningEngineService()
    rows = await engine._effective_material_rows(
        db, passed_topic["course_id"], passed_topic["student"]
    )
    assert passed_topic["fresh_material"] not in {i for i, _ in rows}, (
        "Движок не должен вести прошедшего тему на прощённый материал"
    )


@pytest.mark.asyncio
async def test_student_who_did_not_pass_still_owes_it(db, passed_topic):
    """Сценарий 2 (обратный случай): не проходившему тему новое — обязательно.

    Тот же курс, тот же материал, другой ученик — он не закрыл ни одного
    элемента темы. Правило обязано промолчать.
    """
    newcomer = await _new_student(db, "не проходил")
    await _enroll(db, newcomer, passed_topic["course_id"])
    await db.commit()

    graced = await compute_graced_items(db, newcomer, passed_topic["course_id"])
    assert not graced.materials and not graced.tasks, (
        "У ученика, не проходившего тему, обязательность снимать нельзя: "
        f"снято {sorted(graced.materials)} материалов и {sorted(graced.tasks)} заданий"
    )

    engine = LearningEngineService()
    rows = await engine._effective_material_rows(
        db, passed_topic["course_id"], newcomer
    )
    assert passed_topic["fresh_material"] in {i for i, _ in rows}, (
        "Новому ученику новый материал обязан оставаться обязательным"
    )


@pytest.mark.asyncio
async def test_unfinished_old_item_cancels_grace(db, passed_topic):
    """Сценарий 3: незакрытый старый элемент — значит тема не пройдена.

    Ученик закрыл один старый элемент из двух: он тему не проходил, а бросил
    на середине. Материал, добавленный позже, остаётся долгом наравне с тем,
    что он не доделал.
    """
    halfway = await _new_student(db, "на середине")
    await _enroll(db, halfway, passed_topic["course_id"])
    await _complete_material(
        db,
        user_id=halfway,
        material_id=passed_topic["old_material"],
        at=LONG_AGO + timedelta(days=1),
    )
    await db.commit()

    graced = await compute_graced_items(db, halfway, passed_topic["course_id"])
    assert not graced.materials, (
        "Пока в теме висит незакрытый СТАРЫЙ элемент, тема не пройдена — "
        "прощать новое нельзя"
    )


@pytest.mark.asyncio
async def test_migration_left_existing_tasks_without_date(db):
    """Сторож: заданиям, заведённым до колонки, миграция обязана оставить NULL.

    Первая редакция миграции задавала `server_default now()` прямо в
    `add_column` — а `ALTER TABLE ... ADD COLUMN ... DEFAULT <expr>` в
    PostgreSQL **заполняет умолчанием все существующие строки**. На проде 26.08
    все 7629 заданий разом получили дату накатки, и правило начало снимать
    обязательность с 622 элементов у 36 учеников вместо 13 у 13. Данные
    восстановлены, порядок в миграции исправлен (add_column без умолчания →
    alter_column SET DEFAULT).

    Тест ловит возврат к прежнему порядку по следу, который тот оставляет:
    множество заданий с ОДНОЙ И ТОЙ ЖЕ отметкой времени. Настоящие задания
    заводятся по одному и такой отметки не дают; исключение — импорт пачкой,
    поэтому порог намеренно высокий.
    """
    row = (
        await db.execute(
            text(
                "SELECT created_at, count(*) AS cnt FROM tasks "
                "WHERE created_at IS NOT NULL "
                "GROUP BY created_at ORDER BY cnt DESC LIMIT 1"
            )
        )
    ).fetchone()
    if row is None:
        return  # ни у одного задания даты нет — как и должно быть после миграции
    assert row[1] < 500, (
        f"{row[1]} заданий имеют одну и ту же отметку {row[0]} — похоже, "
        "`ADD COLUMN ... DEFAULT` снова проставил дату накатки существующим "
        "строкам. Такие задания правило считает только что добавленными и "
        "снимает с них обязательность у всех, кто до них не дошёл"
    )


@pytest.mark.asyncio
async def test_task_without_created_at_is_never_graced(db):
    """Сценарий 4: задание без даты появления считается существовавшим всегда.

    Все 6406 заданий боевой базы на день выката именно такие. Правило обязано
    их игнорировать — иначе выкат разом снял бы обязательность там, где ученик
    просто не дошёл до задания.
    """
    course_id = await _new_course(db, "tsk692 задания без даты")
    old_material = await _new_material(
        db, course_id=course_id, created_at=LONG_AGO, title="Материал темы"
    )
    legacy_task = await _new_task(db, course_id=course_id, created_at=None)

    student = await _new_student(db, "закрыл материал")
    await _enroll(db, student, course_id)
    await _complete_material(
        db, user_id=student, material_id=old_material, at=LATER
    )
    await db.commit()

    graced = await compute_graced_items(db, student, course_id)
    assert legacy_task not in graced.tasks, (
        "Задание без даты появления прощать нельзя: неизвестно, было оно в "
        "курсе до прохождения темы или нет"
    )
    assert not graced.materials, (
        "Незакрытое задание без даты появления обязано отменять прощение всего "
        "узла — иначе выкат меняет картину у учеников, которые просто не дошли"
    )


@pytest.mark.asyncio
async def test_completion_without_timestamp_cancels_grace(db, passed_topic):
    """Зачёт без отметки времени отменяет прощение, а не сдвигает границу в 1970.

    «Закрыл, но неизвестно когда» — это отсутствие ответа на главный вопрос
    правила. Подставь такому зачёту условный ноль — и `T` узла упало бы в 1970,
    любой элемент курса оказался бы «новее последнего зачёта», а правило сняло
    бы обязательность со всего узла разом. На боевой базе таких строк сегодня
    нет, но появиться они могут — например ручным зачётом мимо сервиса.
    """
    murky = await _new_student(db, "зачёт без времени")
    await _enroll(db, murky, passed_topic["course_id"])
    await db.execute(
        text(
            "INSERT INTO student_material_progress "
            "(student_id, material_id, status, completed_at, source) "
            "VALUES (:u, :m, 'completed', NULL, 'manual_teacher')"
        ),
        {"u": murky, "m": passed_topic["old_material"]},
    )
    await _pass_task(
        db,
        user_id=murky,
        task_id=passed_topic["old_task"],
        course_id=passed_topic["course_id"],
        root_course_id=passed_topic["course_id"],
        at=LONG_AGO + timedelta(days=2),
    )
    await db.commit()

    graced = await compute_graced_items(db, murky, passed_topic["course_id"])
    assert not graced.materials and not graced.tasks, (
        "Зачёт с неизвестным временем обязан отменять прощение узла, снято: "
        f"{sorted(graced.materials)} материалов, {sorted(graced.tasks)} заданий"
    )


@pytest.mark.asyncio
async def test_course_state_reaches_completed(db, passed_topic):
    """Сценарий 5: прощённое не держит курс в IN_PROGRESS.

    Без правила добавление материала откатывало бы COMPLETED назад, а вместе с
    ним включало бы замок `course_dependencies` на курсах, которые ученик уже
    прошёл.
    """
    engine = LearningEngineService()
    state = await engine.compute_course_state(
        db,
        passed_topic["student"],
        passed_topic["course_id"],
        update_state_table=False,
    )
    assert state.state == "COMPLETED", (
        "Ученик закрыл всё, что было в теме на момент прохождения — курс обязан "
        f"остаться пройденным, получено {state.state} "
        f"({state.tasks_with_result} из {state.total_tasks})"
    )


@pytest.mark.asyncio
async def test_new_subcourse_inside_passed_topic_is_graced(db):
    """Сценарий 6: целиком новый подкурс внутри пройденной темы — не долг.

    У нового узла своих закрытых элементов нет, судить можно только по
    родителю: правило смотрит на предков, иначе живой случай «подкурс
    „Черепашья графика“ внутри задания 6» им не покрывался бы.
    """
    parent_id = await _new_course(db, "tsk692 родитель")
    old_material = await _new_material(
        db, course_id=parent_id, created_at=LONG_AGO, title="Материал родителя"
    )

    student = await _new_student(db, "прошёл родителя")
    await _enroll(db, student, parent_id)
    await _complete_material(
        db, user_id=student, material_id=old_material, at=LONG_AGO + timedelta(days=1)
    )

    # Позже методист завёл внутри темы отдельный подкурс с новым материалом.
    child_id = await _new_course(db, "tsk692 новый подкурс")
    await _link_child(db, parent_id, child_id)
    fresh_material = await _new_material(
        db, course_id=child_id, created_at=LATER, title="Материал нового подкурса"
    )
    await db.commit()

    graced = await compute_graced_items(db, student, parent_id)
    assert fresh_material in graced.materials, (
        "Подкурс, заведённый внутри уже пройденной темы, не должен приходить "
        "прошедшему долгом"
    )
