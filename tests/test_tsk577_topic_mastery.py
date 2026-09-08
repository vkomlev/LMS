"""tsk-577: обзор освоения тем для методиста.

Экран заводился ровно потому, что «Повторение» показывает только провалы. Тесты
здесь охраняют три вещи, каждая из которых при поломке молчит:

1. считается по реальным сдачам, а не по ручной простановке преподавателя;
2. тема с малой выборкой ОСТАЁТСЯ в списке — помеченной, а не отфильтрованной;
3. подозрительно лёгкая тема видна наравне со сложной.

Первое и третье при поломке дают «всё хорошо», второе — «тем нет».
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.learning_gaps_service import MIN_STUDENTS, MIN_SUBMISSIONS
from app.services import topic_mastery_service as mastery
from app.services.topic_mastery_service import (
    EASY_WRONG_RATE,
    FAST_PACE_RATIO,
    MIN_PACE_SAMPLES_FOR_EASY,
    MIN_TYPE_PACE_SAMPLES,
    SIGNAL_EASY,
    SIGNAL_HARD,
    SIGNAL_OK,
    SIGNAL_UNTOUCHED,
    classify_topic,
    topic_overview,
    topic_students,
    topic_tasks,
)


async def _student(db, prefix: str) -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=prefix, tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id


async def _course(db, title: str) -> int:
    res = await db.execute(text(
        "INSERT INTO courses (title, access_level, is_required, course_uid) "
        "VALUES (:t,'self_guided',false,:u) RETURNING id"
    ), {"t": title, "u": f"mastery-{random.randint(10**8, 10**10)}"})
    cid = int(res.scalar_one())
    await db.commit()
    return cid


async def _task(
    db, course_id: int, stem: str = "Условие задания", task_type: str = "SA",
) -> int:
    did = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()
    if did is None:
        pytest.skip("нет ни одной difficulty")
    res = await db.execute(text(
        "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid) "
        "VALUES (jsonb_build_object('type', CAST(:tt AS text), "
        "                           'stem', CAST(:s AS text)), :c, :d, :u) "
        "RETURNING id"
    ), {"s": stem, "tt": task_type, "c": course_id, "d": did,
        "u": f"mastery-{random.randint(10**8, 10**10)}"})
    tid = int(res.scalar_one())
    await db.commit()
    return tid


def _unique_type() -> str:
    """Тип задания, встречающийся ТОЛЬКО в этом тесте (tsk-846).

    Признак «подозрительно лёгкая» сравнивает тему с медианой её типа по всей
    базе. На общем типе (`SA`) результат зависел бы от данных соседних тестов —
    тест то проходил бы, то нет, и объяснить это было бы нечем.
    """
    return f"T846_{random.randint(10**8, 10**10)}"


async def _results(db, *, user_id: int, task_id: int, course_id: int, source: str,
                   correct: int, wrong: int, pace_seconds: int = 60) -> None:
    """Сдачи с заданным шагом по времени — темп считается по их промежуткам."""
    await db.execute(text(
        "INSERT INTO attempts (user_id, course_id) VALUES (:u,:c)"
    ), {"u": user_id, "c": course_id})
    att = (await db.execute(text(
        "SELECT id FROM attempts WHERE user_id = :u ORDER BY id DESC LIMIT 1"
    ), {"u": user_id})).scalar()
    total = correct + wrong
    for i in range(total):
        ok = i < correct
        # Отсчёт назад от now(): последняя сдача — самая свежая, промежуток
        # между соседними ровно `pace_seconds`.
        offset = (total - i) * pace_seconds
        await db.execute(text("""
            INSERT INTO task_results (user_id, task_id, attempt_id, answer_json,
                                      score, max_score, is_correct,
                                      submitted_at, received_at, source_system)
            VALUES (:u,:t,:a, CAST('{"answer":"x"}' AS jsonb), :s, 1, :ok,
                    now() - make_interval(secs => :off),
                    now() - make_interval(secs => :off), :src)
        """), {"u": user_id, "t": task_id, "a": att, "s": 1 if ok else 0,
               "ok": ok, "off": offset, "src": source})
    await db.commit()


async def _cleanup(db, user_ids: list[int], course_ids: list[int]) -> None:
    await db.execute(text("DELETE FROM task_results WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM attempts WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM users WHERE id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:i)"), {"i": course_ids})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:i)"), {"i": course_ids})
    await db.commit()


def _find(overview: dict, course_id: int) -> dict | None:
    return next((t for t in overview["topics"] if t["course_id"] == course_id), None)


# --- признак темы: чистая функция, без БД ------------------------------------

def test_high_error_rate_is_hard_regardless_of_pace():
    """Треть неверных — дефект контента независимо от скорости."""
    assert classify_topic(0.4, None, 0) == SIGNAL_HARD
    assert classify_topic(0.4, 0.2, 50) == SIGNAL_HARD
    assert classify_topic(0.4, 3.0, 50) == SIGNAL_HARD


def test_easy_needs_pace_not_just_low_errors():
    """Мало ошибок само по себе — это хорошая тема, а не подозрительная.

    Подозрительной её делает скорость ОТНОСИТЕЛЬНО таких же заданий (tsk-846).
    Без отношения признак «лёгкая» не ставится вовсе — иначе экран объявил бы
    браком каждую удачно сделанную тему.
    """
    assert classify_topic(0.0, None, 50) == SIGNAL_OK
    assert classify_topic(0.0, 1.2, 50) == SIGNAL_OK
    assert classify_topic(0.0, FAST_PACE_RATIO - 0.1, 50) == SIGNAL_EASY
    assert classify_topic(EASY_WRONG_RATE, FAST_PACE_RATIO, 50) == SIGNAL_EASY


def test_pace_source_literals_match_constants():
    """Имена источников в SQL и в коде не должны разъехаться (tsk-846).

    В запросе они записаны литералами ('real' / 'proxy') ради читаемости, а
    решения по ним принимает Python через константы. Переименуют константу —
    JOIN с базой сравнения молча перестанет находить строки, отношение станет
    `None` у всех тем, и признак просто исчезнет. Без ошибки и без лога.
    """
    assert mastery.PACE_SOURCE_REAL == "real"
    assert mastery.PACE_SOURCE_PROXY == "proxy"
    assert "'real' AS source" in mastery._TYPE_BASE_CTE
    assert "'proxy'" in mastery._TYPE_BASE_CTE
    for sql in (mastery._OVERVIEW_SQL, mastery._TOPIC_TASKS_SQL):
        assert "b.source = 'real'" in sql
        assert "b.source = 'proxy'" in sql


def test_easy_needs_enough_pace_samples():
    """На двух наблюдениях «быстро» не значит ничего (tsk-846).

    До этого условия из 22 тем, помеченных на проде подозрительно лёгкими, 20
    стояли на 2–10 сдачах: экран предлагал методисту переделать материал по
    двум точкам.
    """
    assert classify_topic(0.0, 0.2, MIN_PACE_SAMPLES_FOR_EASY - 1) == SIGNAL_OK
    assert classify_topic(0.0, 0.2, MIN_PACE_SAMPLES_FOR_EASY) == SIGNAL_EASY


# --- источник данных ---------------------------------------------------------

@pytest.mark.asyncio
async def test_overview_ignores_teacher_backfill(db):
    """Ручная простановка не должна улучшать картину темы.

    Тот же дефект, что у датчика пробелов: по сырому `task_results` доля ошибок
    разбавляется примерно вшестеро, и обзор молча показывает благополучие.
    """
    course = await _course(db, "Освоение: разбавление простановкой")
    task = await _task(db, course)
    real = await _student(db, "mastery-real")
    teacher_row = await _student(db, "mastery-teacher")
    try:
        await _results(db, user_id=real, task_id=task, course_id=course,
                       source="spw_web", correct=2, wrong=8)
        await _results(db, user_id=teacher_row, task_id=task, course_id=course,
                       source="manual_teacher", correct=200, wrong=0)

        topic = _find(await topic_overview(db, days=7), course)
        assert topic is not None
        assert topic["submissions"] == 10, (
            f"в счёт попала ручная простановка: {topic['submissions']} вместо 10"
        )
        assert topic["wrong_percent"] == 80
        assert topic["students_reached"] == 1
    finally:
        await _cleanup(db, [real, teacher_row], [course])


# --- малая выборка -----------------------------------------------------------

@pytest.mark.asyncio
async def test_small_sample_is_shown_but_marked_unreliable(db):
    """Тема с малой выборкой остаётся в списке — помеченной, а не скрытой.

    Живой прогон по проду: тем со сдачами 115, порогам выборки удовлетворяют 8.
    Фильтр по порогу превратил бы обзор в тот же экран «Повторение».
    """
    course = await _course(db, "Освоение: мало данных")
    task = await _task(db, course)
    s = await _student(db, "mastery-small")
    try:
        await _results(db, user_id=s, task_id=task, course_id=course,
                       source="spw_web", correct=1, wrong=2)
        topic = _find(await topic_overview(db, days=7), course)
        assert topic is not None, "тема с малой выборкой пропала из обзора"
        assert topic["reliable"] is False
    finally:
        await _cleanup(db, [s], [course])


@pytest.mark.asyncio
async def test_reliable_needs_both_thresholds(db):
    """Надёжность — это и сдачи, и разные ученики.

    Много сдач одного человека — это про человека, а не про тему.
    """
    course = await _course(db, "Освоение: один старательный ученик")
    task = await _task(db, course)
    s = await _student(db, "mastery-solo")
    try:
        await _results(db, user_id=s, task_id=task, course_id=course,
                       source="spw_web", correct=MIN_SUBMISSIONS, wrong=0)
        topic = _find(await topic_overview(db, days=7), course)
        assert topic is not None
        assert topic["submissions"] >= MIN_SUBMISSIONS
        assert topic["students_reached"] < MIN_STUDENTS
        assert topic["reliable"] is False
    finally:
        await _cleanup(db, [s], [course])


# --- нормальные и лёгкие темы видны ------------------------------------------

@pytest.mark.asyncio
async def test_healthy_topic_is_in_the_overview(db):
    """Тема, которую все проходят нормально, обязана быть в списке.

    Ради этого экран и заводился: «Повторение» показывает только провалы, и
    благополучная тема там не появляется вовсе.
    """
    course = await _course(db, "Освоение: нормальная тема")
    task = await _task(db, course)
    students = [await _student(db, f"mastery-ok{i}") for i in range(3)]
    try:
        for sid in students:
            await _results(db, user_id=sid, task_id=task, course_id=course,
                           source="spw_web", correct=8, wrong=2, pace_seconds=60)
        topic = _find(await topic_overview(db, days=7), course)
        assert topic is not None
        assert topic["signal"] == SIGNAL_OK
        assert topic["correct_percent"] == 80
        assert topic["students_reached"] == 3
    finally:
        await _cleanup(db, students, [course])


async def _pace_scene(db, *, fast_pace: int, slow_pace: int, fast_subs: int = 13):
    """Две темы одного типа заданий: «обычная» (фон) и «быстрая» (проверяемая).

    Фон нужен обязательно (tsk-846): признак сравнивает тему с медианой её типа
    по всей базе, а тема, оставшаяся единственным источником данных, сама себе
    норма — отношение вышло бы около единицы у чего угодно.
    """
    ttype = _unique_type()
    background = await _course(db, "Освоение: обычный темп (фон)")
    target = await _course(db, "Освоение: проверяемая тема")
    bg_task = await _task(db, background, task_type=ttype)
    target_task = await _task(db, target, task_type=ttype)
    students = [await _student(db, f"mastery-scene{i}") for i in range(5)]
    for sid in students[:3]:
        await _results(db, user_id=sid, task_id=bg_task, course_id=background,
                       source="spw_web", correct=13, wrong=0, pace_seconds=slow_pace)
    for sid in students[3:]:
        await _results(db, user_id=sid, task_id=target_task, course_id=target,
                       source="spw_web", correct=fast_subs, wrong=0,
                       pace_seconds=fast_pace)
    return students, [background, target], target


@pytest.mark.asyncio
async def test_suspiciously_easy_topic_is_flagged(db):
    """Ноль ошибок и вчетверо быстрее обычного для таких заданий — дефект.

    Проверено на проде: тема «Раздел 2. Оценки: сколько баллов на какую» идёт
    втрое быстрее ожидаемого при нуле ошибок. Ни один порог экрана
    «Повторение» такое не ловит.
    """
    students, courses, target = await _pace_scene(db, fast_pace=15, slow_pace=60)
    try:
        topic = _find(await topic_overview(db, days=7), target)
        assert topic is not None
        assert topic["signal"] == SIGNAL_EASY, (
            f"лёгкая тема не помечена: {topic['signal']}, "
            f"темп {topic['median_pace_seconds']}, отношение {topic['pace_ratio']}"
        )
        assert topic["pace_ratio"] is not None and topic["pace_ratio"] < 1
    finally:
        await _cleanup(db, students, courses)


@pytest.mark.asyncio
async def test_fast_format_alone_is_not_a_defect(db):
    """Тема ИЗ ТЕСТОВ быстра по своей природе — и это не повод её править.

    Ровно тот дефект, ради которого заведена tsk-846: при абсолютном пороге
    (15 с) тема, где задания отвечаются за девять секунд, помечалась
    подозрительно лёгкой, хотя такие задания и решаются за секунды. Теперь она
    сравнивается с себе подобными и остаётся благополучной.
    """
    students, courses, target = await _pace_scene(db, fast_pace=9, slow_pace=10)
    try:
        topic = _find(await topic_overview(db, days=7), target)
        assert topic is not None
        assert topic["median_pace_seconds"] is not None
        assert topic["median_pace_seconds"] < 15, "сцена должна быть быстрой в секундах"
        assert topic["signal"] != SIGNAL_EASY, (
            "быстрый ФОРМАТ задания принят за лёгкую тему: "
            f"темп {topic['median_pace_seconds']} с, отношение {topic['pace_ratio']}"
        )
    finally:
        await _cleanup(db, students, courses)


@pytest.mark.asyncio
async def test_easy_not_flagged_on_tiny_sample(db):
    """Пять сдач одного ученика — не выборка, признак не ставится."""
    students, courses, target = await _pace_scene(
        db, fast_pace=15, slow_pace=60, fast_subs=4,
    )
    try:
        topic = _find(await topic_overview(db, days=7), target)
        assert topic is not None
        assert topic["signal"] != SIGNAL_EASY, (
            "признак поставлен по нескольким наблюдениям: "
            f"отношение {topic['pace_ratio']}"
        )
    finally:
        await _cleanup(db, students, courses)


@pytest.mark.asyncio
async def test_rare_task_type_gets_no_ratio(db):
    """Тип задания без достаточной базы сравнения отношения не даёт.

    Делить на «медиану» из одного наблюдения — значит объявить случайную
    величину нормой. Тогда признак не ставится вовсе: не с чем сравнивать.
    """
    ttype = _unique_type()
    course = await _course(db, "Освоение: редкий тип задания")
    task = await _task(db, course, task_type=ttype)
    students = [await _student(db, f"mastery-rare{i}") for i in range(2)]
    try:
        for sid in students:
            await _results(db, user_id=sid, task_id=task, course_id=course,
                           source="spw_web", correct=8, wrong=0, pace_seconds=5)
        # Два ученика по восемь сдач дают 2 x 7 промежутков — ниже порога базы.
        assert 2 * 7 < MIN_TYPE_PACE_SAMPLES, "сцена должна быть ниже порога базы"
        topic = _find(await topic_overview(db, days=7), course)
        assert topic is not None
        assert topic["pace_ratio"] is None
        assert topic["signal"] != SIGNAL_EASY
    finally:
        await _cleanup(db, students, [course])


# --- разрезы внутри темы -----------------------------------------------------

@pytest.mark.asyncio
async def test_topic_tasks_keep_untouched_ones(db):
    """Задание без сдач остаётся в разборе темы.

    До него просто не дошли — это и есть ответ на вопрос «почему тему никто не
    проходит». Убрать такие строки значит спрятать самый однозначный сигнал.
    """
    course = await _course(db, "Освоение: до второго не дошли")
    solved = await _task(db, course, stem="<p>Первое задание</p>")
    untouched = await _task(db, course, stem="Второе задание")
    s = await _student(db, "mastery-tasks")
    try:
        await _results(db, user_id=s, task_id=solved, course_id=course,
                       source="spw_web", correct=3, wrong=1)
        rows = await topic_tasks(db, course_id=course, days=7)
        by_id = {r["task_id"]: r for r in rows}
        assert set(by_id) == {solved, untouched}
        assert by_id[untouched]["submissions"] == 0
        assert by_id[untouched]["wrong_percent"] is None
        # «Без выбросов» на задании, которого никто не открывал, — ложь: выбросов
        # нет потому, что нет данных. Правда должна быть в ответе API, а не
        # только в подписи на экране.
        assert by_id[untouched]["signal"] == SIGNAL_UNTOUCHED
        assert by_id[solved]["signal"] == SIGNAL_OK
        assert by_id[solved]["wrong_percent"] == 25
        # Подпись — очищенное условие, а не сырая разметка.
        assert by_id[solved]["title"] == "Первое задание"
    finally:
        await _cleanup(db, [s], [course])


@pytest.mark.asyncio
async def test_topic_students_split_mastered_and_not(db):
    """Разрез по ученику: кто закрыл все задания темы, а кто нет."""
    course = await _course(db, "Освоение: разрез по ученику")
    t1 = await _task(db, course, stem="Задание один")
    t2 = await _task(db, course, stem="Задание два")
    done = await _student(db, "mastery-done")
    stuck = await _student(db, "mastery-stuck")
    try:
        for t in (t1, t2):
            await _results(db, user_id=done, task_id=t, course_id=course,
                           source="spw_web", correct=1, wrong=0)
        await _results(db, user_id=stuck, task_id=t1, course_id=course,
                       source="spw_web", correct=1, wrong=0)
        await _results(db, user_id=stuck, task_id=t2, course_id=course,
                       source="spw_web", correct=0, wrong=4)

        rows = {r["student_id"]: r for r in await topic_students(db, course_id=course, days=7)}
        assert rows[done]["mastered"] is True
        assert rows[done]["tasks_correct"] == 2
        assert rows[stuck]["mastered"] is False
        assert rows[stuck]["tasks_correct"] == 1
        assert rows[stuck]["tasks_total"] == 2
    finally:
        await _cleanup(db, [done, stuck], [course])


@pytest.mark.asyncio
async def test_pace_ignores_between_session_breaks(db):
    """Возврат к теме на следующий день — не «долго думал».

    Без обрезки промежутка любая тема, к которой ученик возвращался, выглядела
    бы медленной, и признак темпа стал бы бессмысленным.
    """
    course = await _course(db, "Освоение: перерыв между занятиями")
    task = await _task(db, course)
    s = await _student(db, "mastery-pace")
    try:
        # Два коротких промежутка внутри занятия…
        await _results(db, user_id=s, task_id=task, course_id=course,
                       source="spw_web", correct=3, wrong=0, pace_seconds=10)
        # …и сдача сутками позже — она даёт огромный промежуток.
        await db.execute(text("""
            INSERT INTO task_results (user_id, task_id, attempt_id, answer_json,
                                      score, max_score, is_correct,
                                      submitted_at, received_at, source_system)
            SELECT :u, :t, tr.attempt_id, CAST('{"answer":"x"}' AS jsonb), 1, 1, true,
                   now() - interval '2 days', now() - interval '2 days', 'spw_web'
            FROM task_results tr WHERE tr.user_id = :u LIMIT 1
        """), {"u": s, "t": task})
        await db.commit()

        topic = _find(await topic_overview(db, days=7), course)
        assert topic is not None
        assert topic["median_pace_seconds"] is not None
        assert topic["median_pace_seconds"] < 60, (
            "перерыв между занятиями попал в темп: "
            f"{topic['median_pace_seconds']} с"
        )
    finally:
        await _cleanup(db, [s], [course])
