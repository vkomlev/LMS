# tests/test_unseen_constructs_tsk965.py
"""
tsk-965: детектор непройденных конструкций не проверял условие ЭТОГО задания.

Живой случай: id-271 «Объединение трёх списков в один» (курс 109) даёт в
условии `list1 = [1, 2, 3]`, `list2 = [4, 5, 6]`, `list3 = [7, 8, 9]` готовым
текстом. Ученик копирует синтаксис списка оттуда, а не выучивает его заранее
— а признак называл литерал списка непройденной конструкцией. Разбор по
прод-БД (16.09): `list_literal` — 215 из 268 (80%) всех пометок за всё время,
минимум 51% уникальных заданий с этим флагом сами дают список в условии.

Что закрываем:
- конструкция, показанная в условии САМОГО задания (markdown-код в обратных
  кавычках, тройные блоки ``` ```, HTML `<pre>`/`<code>`), гасит пометку для
  этого задания — как для `list_literal`, так и для остальных конструкций
  каталога, механизм общий (не точечный фикс);
- обрывок прозы в обратных кавычках, который не парсится как Python
  (`with` без блока, `<товар>` как обозначение места в шаблоне), не роняет
  расчёт и не даёт ложного зачёта;
- конструкция, которой в условии нет, продолжает флагаться как обычно —
  признак не превратился в вечное молчание.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import text as sqltext

from app.services import code_review_cron_service
from app.services.unseen_constructs_service import codes_in_stem
from tests.test_unseen_constructs_tsk864 import _cleanup, _stub_model


# ---------- codes_in_stem: юнит-разбор, без БД ----------

def test_backtick_list_literal_is_recognized() -> None:
    """Живой стем id-271: три списка в одинарных обратных кавычках."""
    stem = (
        "Даны три фиксированных списка:\n"
        "`list1 = [1, 2, 3]`, `list2 = [4, 5, 6]`, `list3 = [7, 8, 9]`.\n"
        "Объедините их в один через оператор `+`."
    )
    assert "list_literal" in codes_in_stem(stem)


def test_fenced_block_list_literal_is_recognized() -> None:
    """Тройной блок ``` ``` — боевой формат матрицы (id=10364)."""
    stem = (
        "Дана матрица:\n\n```\nmatrix = [\n    [13, 47, 5, 88],\n]\n```\n"
        "Найдите максимум."
    )
    assert "list_literal" in codes_in_stem(stem)


def test_html_code_block_is_recognized() -> None:
    """Боевой формат заданий id=5622 и соседних: `<pre><code>` с сущностями."""
    stem = (
        "<p>Исходный код для этого задания:</p>"
        '<pre><code class="language-python">chisla = [12, 7, 20, 8]\n'
        "chetnyh = 0</code></pre>Допиши строку."
    )
    assert "list_literal" in codes_in_stem(stem)


def test_html_pre_with_trailing_code_span_is_not_confused() -> None:
    """
    Боевая форма id=5622 целиком: `<pre><code>…</code></pre>`, а СРАЗУ ПОСЛЕ —
    отдельный `<code>` с пояснением к плейсхолдеру. Закрывающий тег ищем свой
    для каждого открывающего — иначе `<pre>` мог бы обрезаться на чужом
    `</code>`, а хвостовой `<code>…</code>` потерялся бы вовсе.
    """
    stem = (
        "<p>Исходный код для этого задания:</p>"
        '<pre><code class="language-python">chisla = [12, 7, 20, 8]\n'
        "chetnyh = 0\n"
        "for x in chisla:\n"
        "    # &lt;допиши здесь строку кода&gt;\n"
        "        chetnyh = chetnyh + 1\n"
        "print(chetnyh)</code></pre>"
        "Допиши пропущенную строку кода вместо комментария "
        "<code># &lt;допиши здесь строку кода&gt;</code>. Нужно условие if."
    )
    hits = codes_in_stem(stem)
    assert "list_literal" in hits  # из `<pre>` - chisla = [12, 7, 20, 8]


def test_prose_in_backticks_does_not_crash_or_falsely_match() -> None:
    """
    `with` без блока, `<товар>` как обозначение в прозе — не парсится как
    Python, и это не повод падать или засчитывать что-то по ошибке.
    """
    stem = "Используйте `with` для файла и подставьте `<товар>` в шаблон."
    assert codes_in_stem(stem) == set()


def test_construct_absent_from_stem_is_not_reported_as_seen() -> None:
    """Условие без литерала списка не гасит list_literal просто по факту вызова."""
    stem = "Напишите функцию `is_even(x)`, которая проверяет чётность."
    assert "list_literal" not in codes_in_stem(stem)


def test_empty_stem_is_survived() -> None:
    assert codes_in_stem(None) == set()
    assert codes_in_stem("") == set()


# ---------- Через фоновый тик: живой случай id-271 ----------

_ID271_STEM = (
    "Даны три фиксированных списка:\n"
    "`list1 = [1, 2, 3]`, `list2 = [4, 5, 6]`, `list3 = [7, 8, 9]`.\n"
    "Объедините их в один через оператор `+`: Выведите результат через\n"
    "`print`.\n\nПоместите вывод в поле «Ответ».\n"
)
_ID271_CODE = (
    "list1 = [1, 2, 3]\nlist2 = [4, 5, 6]\nlist3 = [7, 8, 9]\n"
    "print(list1+list2+list3)\n"
)
_FIRST_TOPIC = (
    "<p>Функция <code>print()</code> выводит значение, "
    "<code>input()</code> читает строку.</p>"
)


async def _seed_with_stem(
    db, *, stem: str, code: str, completed_materials: list[str],
) -> tuple[int, int, int]:
    """
    Тот же снаряд, что `_seed` в tsk864, но со своим текстом условия задания
    (`task_content.stem`) — ровно то, что проверяет `codes_in_stem`.

    Материалы прошлой темы (`completed_materials`) отмечаются пройденными,
    материалы ТЕКУЩЕЙ темы не заводятся вовсе — льгота "текущая тема доступна
    целиком" тут не должна маскировать проверяемое поведение.

    Возвращает (id работы, id прошлой темы, id ученика).
    """
    course_id = (await db.execute(sqltext(
        "INSERT INTO courses (title, access_level) VALUES ('tsk965 тема', 'auto_check') RETURNING id"
    ))).scalar_one()
    past_course_id = (await db.execute(sqltext(
        "INSERT INTO courses (title, access_level) VALUES ('tsk965 прошлая', 'auto_check') RETURNING id"
    ))).scalar_one()
    task_id = (await db.execute(sqltext(
        "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, course_id, difficulty_id) "
        "VALUES (:ext, 10, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, 1) RETURNING id"
    ), {
        "ext": f"tsk965-{random.randint(10**8, 10**10)}",
        "c": json.dumps({"type": "SA_COM", "stem": stem}, ensure_ascii=False),
        "r": json.dumps({"max_score": 10}),
        "cid": course_id,
    })).scalar_one()

    user_id = (await db.execute(sqltext(
        "INSERT INTO users (email, full_name) VALUES (:e, 'tsk965 ученик') RETURNING id"
    ), {"e": f"tsk965-{random.randint(10**8, 10**10)}@example.test"})).scalar_one()
    for material_html in completed_materials:
        material_id = (await db.execute(sqltext(
            "INSERT INTO materials (course_id, title, type, content) "
            "VALUES (:cid, 'пройденный материал', 'text', CAST(:c AS jsonb)) RETURNING id"
        ), {
            "cid": past_course_id,
            "c": json.dumps({"text": material_html}, ensure_ascii=False),
        })).scalar_one()
        await db.execute(sqltext(
            "INSERT INTO student_material_progress (student_id, material_id, status, completed_at, source) "
            "VALUES (:u, :m, 'completed', now(), 'system')"
        ), {"u": user_id, "m": material_id})

    now = datetime.now(timezone.utc)
    result_id = (await db.execute(sqltext(
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


async def test_id271_case_stem_silences_list_literal(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Живой случай 16.09: список дан в условии, ученик его переписал и сдал —
    пометки быть не должно, хотя литерал списка вне пройденных материалов.
    """
    result_id, past, user_id = await _seed_with_stem(
        db, stem=_ID271_STEM, code=_ID271_CODE,
        completed_materials=[_FIRST_TOPIC],
    )
    _stub_model(monkeypatch)
    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = (await db.execute(sqltext(
            "SELECT code_review FROM task_results WHERE id = :r"
        ), {"r": result_id})).scalar_one()
        unseen = review["unseen_constructs"]
        assert unseen is not None, "сверять было с чем - материал отмечен"
        assert "list_literal" not in [i["code"] for i in unseen["items"]]
    finally:
        await _cleanup(db, result_id, past, user_id)


async def test_construct_not_shown_in_stem_still_flags(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Контрольный случай: то же задание, но ученик использует lambda, которой
    в условии нет вовсе — признак не должен превратиться в вечное молчание.
    """
    code = "square = lambda x: x * x\nprint(square(5))\n"
    result_id, past, user_id = await _seed_with_stem(
        db, stem=_ID271_STEM, code=code,
        completed_materials=[_FIRST_TOPIC],
    )
    _stub_model(monkeypatch)
    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = (await db.execute(sqltext(
            "SELECT code_review FROM task_results WHERE id = :r"
        ), {"r": result_id})).scalar_one()
        unseen = review["unseen_constructs"]
        assert unseen is not None
        assert "lambda_expr" in [i["code"] for i in unseen["items"]]
    finally:
        await _cleanup(db, result_id, past, user_id)
