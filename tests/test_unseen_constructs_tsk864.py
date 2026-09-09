# tests/test_unseen_constructs_tsk864.py
"""
tsk-864: конструкция из непройденной темы — пометка преподавателю.

Что закрываем:
- разбор кода идёт деревом, а не текстом: `lambda` в комментарии и `class` в
  строке пометку не поднимают;
- сверка идёт с материалами, которые отметил пройденными ИМЕННО ЭТОТ ученик;
- тема, в которой сдана работа, считается доступной целиком;
- «сверили, всё пройдено» и «сверить не с чем» — разные вещи и в отчёте
  выглядят по-разному;
- ученику пометка не видна.

Живой замер по боевой базе (5 работ из 2299 разборов) — в самой задаче; здесь
проверяется механика, а не калибровка.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from typing import Any, Dict

import pytest
from sqlalchemy import text

from app.services import code_review_cron_service
from app.services.unseen_constructs_service import codes_in_material, detect_in_code


# ---------- Разбор кода ученика ----------

def test_fstring_and_dunder_are_seen() -> None:
    """Та самая работа с прода: f-строка и `__name__` во второй теме Python."""
    code = (
        "a=5+4\n"
        'print(f"После a = 5+4: значение = {a}, тип = {type(a).__name__}")\n'
    )
    found = dict(detect_in_code(code))
    assert "fstring" in found and "dunder" in found
    assert "type(a).__name__" in found["dunder"]


def test_plain_beginner_code_raises_nothing() -> None:
    """Обычное решение новичка не содержит ни одной конструкции каталога."""
    code = "a = int(input())\nb = int(input())\nprint(a + b)\n"
    assert detect_in_code(code) == []


def test_constructs_inside_strings_and_comments_are_ignored() -> None:
    """
    Разбор идёт деревом, поэтому слова в прозе пометку не поднимают.

    Регулярка по тексту нашла бы здесь и `lambda`, и `class`, и `except` — то
    есть выдала бы преподавателю три пометки на решении из двух строк. Ровно
    этого признак обязан избегать: ложная пометка дороже пропущенной.
    """
    code = (
        "# тут мог быть lambda и class, но их нет\n"
        's = "except ValueError: не ошибка, а строка"\n'
        "print(s)\n"
    )
    assert detect_in_code(code) == []


def test_non_python_code_is_not_parsed() -> None:
    """Arduino/C++ (40 заданий курсов «МАМ») не разбирается — каталога для него нет."""
    arduino = "void setup() {\n  pinMode(13, OUTPUT);\n}\n"
    assert detect_in_code(arduino) == []


def test_broken_code_does_not_raise() -> None:
    """Незаконченная программа — обычное дело у ученика, падать на ней нельзя."""
    assert detect_in_code("print(f'привет'") == []
    assert detect_in_code("") == []


# ---------- Сверка с материалом ----------

def _material(html: str) -> str:
    return json.dumps({"text": html}, ensure_ascii=False)


def test_material_explaining_fstrings_counts_as_covered() -> None:
    """Материал темы «Работа со строками» — так он выглядит на проде."""
    html = (
        "<p>В Python есть несколько способов форматирования строк, включая "
        "метод <code>format()</code>, а также f-строк (начиная с Python 3.6).</p>"
    )
    assert "fstring" in codes_in_material(_material(html))


def test_material_with_code_example_counts_as_covered() -> None:
    """Объяснением считается и код примера, не только слова о конструкции."""
    html = '<pre><code>name = "Вася"\nprint(f"Привет, {name}!")</code></pre>'
    assert "fstring" in codes_in_material(_material(html))


def test_material_of_first_topic_does_not_cover_fstrings() -> None:
    """Материал первой темы: ввод-вывод, переменные — f-строк там нет."""
    html = (
        "<p>Функция <code>print()</code> выводит значение на экран, "
        "а <code>input()</code> читает строку с клавиатуры.</p>"
    )
    assert "fstring" not in codes_in_material(_material(html))


def test_unreadable_material_is_survived() -> None:
    """Битое содержимое материала не роняет сверку — просто ничего не даёт."""
    assert codes_in_material("не json") == set()
    assert codes_in_material(None) == set()


# ---------- Через фоновый тик, на живой базе ----------

async def _seed(
    db,
    *,
    code: str,
    course_materials: list[str],
    completed_materials: list[str],
) -> tuple[int, int, int]:
    """
    Работа на оценку + тема со своими материалами + пройденные учеником материалы.

    `course_materials` кладутся в тему задания, `completed_materials` — в
    ОТДЕЛЬНУЮ прошлую тему и отмечаются пройденными. Разделение принципиально:
    иначе не отличить «ученик это проходил» от «это лежит в текущей теме».

    Возвращает (id работы, id прошлой темы, id ученика).
    """
    course_id = (await db.execute(text(
        "INSERT INTO courses (title, access_level) VALUES ('tsk864 тема', 'auto_check') RETURNING id"
    ))).scalar_one()
    past_course_id = (await db.execute(text(
        "INSERT INTO courses (title, access_level) VALUES ('tsk864 прошлая', 'auto_check') RETURNING id"
    ))).scalar_one()
    task_id = (await db.execute(text(
        "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, course_id, difficulty_id) "
        "VALUES (:ext, 10, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, 1) RETURNING id"
    ), {
        "ext": f"tsk864-{random.randint(10**8, 10**10)}",
        "c": json.dumps({"type": "SA_COM", "stem": "напиши программу"}),
        "r": json.dumps({"max_score": 10}),
        "cid": course_id,
    })).scalar_one()

    for html in course_materials:
        await db.execute(text(
            "INSERT INTO materials (course_id, title, type, content) "
            "VALUES (:cid, 'материал темы', 'text', CAST(:c AS jsonb))"
        ), {"cid": course_id, "c": _material(html)})

    # Свой ученик, а не первый попавшийся: у существующих в базе уже есть свои
    # отметки о материалах, и на них тест считал бы не то, что задаёт.
    user_id = (await db.execute(text(
        "INSERT INTO users (email, full_name) VALUES (:e, 'tsk864 ученик') RETURNING id"
    ), {"e": f"tsk864-{random.randint(10**8, 10**10)}@example.test"})).scalar_one()
    for html in completed_materials:
        material_id = (await db.execute(text(
            "INSERT INTO materials (course_id, title, type, content) "
            "VALUES (:cid, 'пройденный материал', 'text', CAST(:c AS jsonb)) RETURNING id"
        ), {"cid": past_course_id, "c": _material(html)})).scalar_one()
        await db.execute(text(
            "INSERT INTO student_material_progress (student_id, material_id, status, completed_at, source) "
            "VALUES (:u, :m, 'completed', now(), 'system')"
        ), {"u": user_id, "m": material_id})

    now = datetime.now(timezone.utc)
    result_id = (await db.execute(text(
        "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, received_at, "
        " max_score, source_system, answer_json, code_review) "
        "VALUES (0, :u, :t, :now, 0, :now, 10, 'test', CAST(:a AS jsonb), CAST(:cr AS jsonb)) RETURNING id"
    ), {
        "u": user_id, "t": task_id, "now": now,
        "a": json.dumps({"type": "SA_COM", "response": {"value": code}}),
        "cr": json.dumps({"status": "pending"}),
    })).scalar_one()
    await db.commit()
    return result_id, past_course_id, user_id


async def _cleanup(db, result_id: int, past_course_id: int, user_id: int) -> None:
    await db.execute(text(
        "DELETE FROM student_material_progress WHERE student_id = :u"), {"u": user_id})
    await db.execute(text(
        "DELETE FROM courses WHERE id IN "
        "(SELECT course_id FROM tasks WHERE id IN (SELECT task_id FROM task_results WHERE id = :r))"
    ), {"r": result_id})
    await db.execute(text("DELETE FROM courses WHERE id = :p"), {"p": past_course_id})
    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    await db.commit()


async def _read_review(db, result_id: int) -> Dict[str, Any]:
    return (await db.execute(
        text("SELECT code_review FROM task_results WHERE id = :r"), {"r": result_id},
    )).scalar_one()


def _stub_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Модель замокана: признак к ней отношения не имеет и платить за неё незачем."""
    async def _fake_review(code, *, task_stem=None, student_id=None):
        return {
            "language": "Python",
            "code_quality": {"score": 8, "notes": []},
            "ai_authorship": {"verdict": "ambiguous", "reasoning": "коротко"},
            "model": "test-model",
        }

    monkeypatch.setattr(code_review_cron_service, "review_student_code", _fake_review)
    monkeypatch.setattr(code_review_cron_service, "analyze_student_code_quality", lambda code: None)


_FIRST_TOPIC = "<p>Функция <code>print()</code> выводит значение, <code>input()</code> читает строку.</p>"
_STRINGS_TOPIC = "<p>f-строка пишется с буквы f перед кавычками.</p>"
_FSTRING_CODE = 'name = input()\nprint(f"Привет, {name}!")\n'


async def test_unseen_construct_is_marked(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Ученик прошёл первую тему, а в решении f-строка — пометка появляется.

    Это ровно та ситуация, с которой началась задача: детектор ИИ-авторства
    называет конструкцию обычной, потому что не знает, что этот ученик её ещё
    не проходил.
    """
    result_id, past, user_id = await _seed(
        db, code=_FSTRING_CODE,
        course_materials=[_FIRST_TOPIC],
        completed_materials=[_FIRST_TOPIC],
    )
    _stub_model(monkeypatch)
    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        unseen = review["unseen_constructs"]
        assert [i["code"] for i in unseen["items"]] == ["fstring"]
        assert unseen["items"][0]["evidence"] == 'print(f"Привет, {name}!")'
        assert unseen["materials_seen"] == 1
        # Вердикт модели живёт своей жизнью: признак его не переписывает.
        assert review["ai_authorship"]["verdict"] == "ambiguous"
    finally:
        await _cleanup(db, result_id, past, user_id)


async def test_covered_construct_is_silent(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ту же f-строку ученику уже объясняли — пометки нет, но сверка была."""
    result_id, past, user_id = await _seed(
        db, code=_FSTRING_CODE,
        course_materials=[_FIRST_TOPIC],
        completed_materials=[_FIRST_TOPIC, _STRINGS_TOPIC],
    )
    _stub_model(monkeypatch)
    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        assert review["unseen_constructs"]["items"] == []
        assert review["unseen_constructs"]["materials_seen"] == 2
    finally:
        await _cleanup(db, result_id, past, user_id)


async def test_current_topic_counts_as_available(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Материал ТЕКУЩЕЙ темы гасит пометку, даже если ученик его ещё не отметил.

    Порядок «сначала решил, потом прочитал» — обычное дело, и наказывать за
    него нельзя: материал у ученика перед глазами.
    """
    result_id, past, user_id = await _seed(
        db, code=_FSTRING_CODE,
        course_materials=[_FIRST_TOPIC, _STRINGS_TOPIC],
        completed_materials=[_FIRST_TOPIC],
    )
    _stub_model(monkeypatch)
    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        assert review["unseen_constructs"]["items"] == []
    finally:
        await _cleanup(db, result_id, past, user_id)


async def test_no_material_marks_means_no_section(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Ученик не отметил ни одного материала — секции нет вовсе.

    Пустой список значил бы «сверили, всё пройдено», а это неправда: сверять
    было не с чем, и пометка сказала бы лишь то, что человек не нажимал кнопку.
    """
    result_id, past, user_id = await _seed(
        db, code=_FSTRING_CODE,
        course_materials=[_FIRST_TOPIC],
        completed_materials=[],
    )
    _stub_model(monkeypatch)
    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        assert "unseen_constructs" not in review
    finally:
        await _cleanup(db, result_id, past, user_id)


async def test_mark_survives_unavailable_model(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Модель недоступна — пометка всё равно на месте.

    Признак считается из данных и модели не требует; терять его вместе с
    вердиктом значило бы отдать преподавателю пустой отчёт там, где факт есть.
    """
    result_id, past, user_id = await _seed(
        db, code=_FSTRING_CODE,
        course_materials=[_FIRST_TOPIC],
        completed_materials=[_FIRST_TOPIC],
    )

    async def _dead_model(code, *, task_stem=None, student_id=None):
        return {"error": "LLMConfigError", "message": "нет ключа", "retryable": False}

    monkeypatch.setattr(code_review_cron_service, "review_student_code", _dead_model)
    monkeypatch.setattr(
        code_review_cron_service, "analyze_student_code_quality",
        lambda code: {"pylint": {"score": 7.5, "messages": []}},
    )
    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        assert review["degraded"] is True
        assert [i["code"] for i in review["unseen_constructs"]["items"]] == ["fstring"]
    finally:
        await _cleanup(db, result_id, past, user_id)


# ---------- Границы видимости ----------

def test_mark_is_not_declared_on_student_surfaces() -> None:
    """
    Ученические схемы ответа на сдачу не получают пометку ни под каким именем.

    Продолжение стража tsk-302: пометка живёт внутри `code_review`, но проверить
    отдельно стоит — она новая, и соблазн «показать ученику, чего он не знает»
    ровно тут и возникает.
    """
    from app.schemas.attempts import AttemptAnswerResult, AttemptAnswersResponse
    from app.schemas.checking import CheckResult

    for schema in (AttemptAnswersResponse, AttemptAnswerResult, CheckResult):
        for field_name in schema.model_fields:
            assert "unseen" not in field_name.lower(), (
                f"{schema.__name__}.{field_name} показывает пометку ученику"
            )


def test_badge_lights_its_own_flag_not_ai_suspicion() -> None:
    """
    В списках пометка поднимает СВОЙ флаг, а не «похоже на нейросеть» (tsk-864).

    Решение оператора 09.09 — показывать признак и в ленте с прогрессом. Слить
    его в `ai_suspected` было бы дёшево и неправильно: тот значок подписан
    «работа похожа на сделанную нейросетью», а здесь утверждается совсем другое.
    Ученик мог узнать конструкцию сам, и склейка превратила бы факт в обвинение
    ровно в том месте, ради которого задача и делалась.
    """
    from app.schemas.code_review import build_code_review_badge

    badge = build_code_review_badge({
        "status": "done",
        "code_quality": {"score": 8},
        "ai_authorship": {"verdict": "ambiguous"},
        "unseen_constructs": {
            "items": [{"code": "fstring", "label": "f-строка", "evidence": "print(f'')"}],
            "materials_seen": 31,
        },
    })
    assert badge is not None
    assert badge.has_unseen_constructs is True
    assert badge.ai_suspected is False


def test_badge_stays_dark_when_everything_is_covered() -> None:
    """Сверили, всё пройдено — значок не зажигается: пустой список не повод."""
    from app.schemas.code_review import build_code_review_badge

    badge = build_code_review_badge({
        "status": "done",
        "code_quality": {"score": 8},
        "unseen_constructs": {"items": [], "materials_seen": 50},
    })
    assert badge is not None and badge.has_unseen_constructs is False


def test_badge_survives_report_of_unexpected_shape() -> None:
    """
    Отчёт неожиданной формы не роняет значок.

    Значок строится для КАЖДОЙ строки ленты, а там их до сотни: одна кривая
    запись положила бы преподавателю весь список, а не одну работу.
    """
    from app.schemas.code_review import build_code_review_badge

    for junk in ([], "нет", 0, {"items": "нет"}):
        badge = build_code_review_badge({
            "status": "done", "code_quality": {"score": 8}, "unseen_constructs": junk,
        })
        assert badge is not None and badge.has_unseen_constructs is False, junk


def test_report_schema_accepts_the_mark() -> None:
    """Отчёт со всеми полями пометки разбирается схемой, а не проваливается в extra."""
    from app.schemas.code_review import CodeReviewReport

    report = CodeReviewReport.model_validate({
        "status": "done",
        "kind": "code",
        "unseen_constructs": {
            "items": [
                {"code": "fstring", "label": 'f-строка (`f"…"`)',
                 "evidence": 'print(f"Привет, {name}!")'},
            ],
            "materials_seen": 31,
        },
    })
    assert report.unseen_constructs is not None
    assert report.unseen_constructs.items is not None
    assert report.unseen_constructs.items[0].code == "fstring"
    assert report.unseen_constructs.materials_seen == 31
