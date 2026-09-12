# tests/test_unseen_constructs_tsk915.py
"""
tsk-915: каталог непройденных конструкций не знал про списки.

Живой случай: задание на тему «Строки» (курс 108), ученик получает список
через `.split()` и собирает строку обратно через `.join()`, тему «Списки»
(курс 109) не открывал вообще. Пометки не было вовсе — в каталоге не было ни
одной конструкции про списки.

Что закрываем:
- `.split()`/`.join()` и литерал `[...]` находятся деревом, а не регуляркой;
- боевой текст материала «Строковые методы» курса «Строки» (объясняет
  `.split()`/`.join()` МЕЛЬКОМ, без практики со списками) не гасит пометку —
  льгота «текущая тема пройдена целиком» для этих двух кодов не действует
  (`course_covers=False`, решение оператора 12.09: практики метод не даёт,
  пометка важнее риска лишний раз показаться);
- при этом СОБСТВЕННЫЙ прогресс ученика (реально отмеченные материалы, из
  любого курса) по-прежнему гасит пометку как обычно — льгота не действует
  только на «бесплатный» проход от текущего курса;
- для остальных конструкций каталога (`course_covers=True` по умолчанию)
  правило tsk-864 не изменилось.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text as sqltext

from app.services import code_review_cron_service
from app.services.unseen_constructs_service import codes_in_material, detect_in_code
from tests.test_unseen_constructs_tsk864 import _cleanup, _seed, _stub_model


# ---------- Разбор кода ученика ----------

def test_undasynova_case_is_detected() -> None:
    """Живая сдача 10377: .split() и .join(), без явного литерала списка."""
    code = (
        'sentence="Каждый охотник желает знать где сидит фазан"\n'
        "words=sentence.split()\n"
        "print(len(words))\n"
        "words, words=words, words\n"
        'print(" ".join(words))\n'
    )
    found = dict(detect_in_code(code))
    assert "split_join" in found
    assert "list_literal" not in found  # литерала [...] в этом коде нет


def test_list_literal_is_detected() -> None:
    code = "fruits = ['apple', 'banana', 'orange']\nprint(fruits)\n"
    found = dict(detect_in_code(code))
    assert "list_literal" in found
    assert "['apple'" in found["list_literal"]


def test_list_literal_does_not_confuse_comprehension_or_slice() -> None:
    """`[x for x in y]` - генератор, `a[1:2]` - срез, не литерал списка."""
    code = "a = [1, 2, 3]\nb = [x * 2 for x in a]\nc = a[1:2]\nprint(b, c)\n"
    found = dict(detect_in_code(code))
    assert "list_literal" in found  # от `a = [1, 2, 3]`
    assert "comprehension" in found  # от генератора
    assert "slice_step" not in found  # срез без шага


def test_split_join_ignores_format_method() -> None:
    """`.format()` - отдельный код каталога, не должен зажигать `split_join`."""
    code = '"{}".format(5)\n'
    found = dict(detect_in_code(code))
    assert "split_join" not in found
    assert "format_method" in found


# ---------- Сверка с материалом ----------

_STRINGS_MATERIAL_MENTIONS_SPLIT_JOIN = (
    "<h3>Строковые методы</h3>"
    "<p>Для разделения строки по пробелу используем метод <code>split()</code>: "
    "text.split() возвращает список подстрок.</p>"
    "<p>Для объединения элементов списка обратно в строку - метод "
    "<code>join()</code>: ','.join(fruits).</p>"
)

_LISTS_MATERIAL_TEACHES_LITERAL = (
    "<h3>Создание списков</h3>"
    "<p>Список можно создать, перечислив его элементы в квадратных скобках "
    "через запятую: <code>my_list = [1, 2, 3]</code>.</p>"
)

_FORMAT_MATERIAL_USES_LIST_INCIDENTALLY = (
    "<p>Шаблон template и список values, содержащий значения для подстановки: "
    "<code>values = [\"hello\", 42]</code>, template.format(*values).</p>"
)


def test_material_mentioning_split_join_in_passing_counts_for_material_check() -> None:
    """
    `codes_in_material` сама по себе не различает «мельком»/«с практикой» -
    это не её работа (см. докстринг Construct.course_covers): она честно
    находит текстовое совпадение. Разделение живёт в `covered_codes`.
    """
    body = json.dumps({"text": _STRINGS_MATERIAL_MENTIONS_SPLIT_JOIN}, ensure_ascii=False)
    assert "split_join" in codes_in_material(body)


def test_material_with_incidental_list_syntax_does_not_count_as_teaching_it() -> None:
    """
    Материал курса «Строки» использует литерал списка как аргумент `.format()`
    мимоходом (боевой случай - материал 264 «Форматирование строк»), без слов
    об устройстве списков. `list_literal` не должен посчитать это объяснением.
    """
    body = json.dumps({"text": _FORMAT_MATERIAL_USES_LIST_INCIDENTALLY}, ensure_ascii=False)
    assert "list_literal" not in codes_in_material(body)


# ---------- Через фоновый тик: льгота "текущая тема" не действует ----------

async def test_current_course_does_not_silence_split_join(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Прямое воспроизведение боевого расхождения: материал ТЕКУЩЕГО курса
    («Строки») объясняет `.split()`/`.join()` мельком, но ученик его не
    отмечал пройденным - и «Списки» тоже не открывал. Пометка обязана
    появиться: льгота «текущая тема доступна целиком» на split_join и
    list_literal не действует (`course_covers=False`).
    """
    result_id, past, user_id = await _seed(
        db,
        code=(
            'text = "Каждый охотник желает знать где сидит фазан"\n'
            "words = text.split()\n"
            'print(" ".join(words))\n'
        ),
        course_materials=[_STRINGS_MATERIAL_MENTIONS_SPLIT_JOIN],
        completed_materials=[],
    )
    # У ученика нет ни одного отмеченного материала вообще - обычно это значит
    # "сверить не с чем" (materials_seen == 0), поэтому дополнительно отмечаем
    # нейтральный материал без конструкций каталога, чтобы сверка состоялась.
    neutral_material_id = (await db.execute(sqltext(
        "INSERT INTO materials (course_id, title, type, content) "
        "VALUES (:cid, 'нейтральный материал', 'text', CAST(:c AS jsonb))"
        " RETURNING id"
    ), {
        "cid": past,
        "c": json.dumps({"text": "<p>print() выводит значение на экран.</p>"}, ensure_ascii=False),
    })).scalar_one()
    await db.execute(sqltext(
        "INSERT INTO student_material_progress (student_id, material_id, status, completed_at, source) "
        "VALUES (:u, :m, 'completed', now(), 'system')"
    ), {"u": user_id, "m": neutral_material_id})
    await db.commit()

    _stub_model(monkeypatch)
    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = (await db.execute(sqltext(
            "SELECT code_review FROM task_results WHERE id = :r"
        ), {"r": result_id})).scalar_one()
        unseen = review["unseen_constructs"]
        assert unseen is not None, "сверять было с чем - нейтральный материал отмечен"
        assert "split_join" in [i["code"] for i in unseen["items"]]
    finally:
        await _cleanup(db, result_id, past, user_id)


async def test_students_own_progress_still_silences_split_join(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    `course_covers=False` отключает только бесплатный проход от ТЕКУЩЕГО курса.
    Если ученик САМ отметил материал, где списки объясняются по-настоящему
    (например, при прохождении курса «Списки»), пометка по-прежнему гаснет.
    """
    result_id, past, user_id = await _seed(
        db,
        code='words = "a b c".split()\nprint(" ".join(words))\n',
        course_materials=[_STRINGS_MATERIAL_MENTIONS_SPLIT_JOIN],
        completed_materials=[_LISTS_MATERIAL_TEACHES_LITERAL, _STRINGS_MATERIAL_MENTIONS_SPLIT_JOIN],
    )
    _stub_model(monkeypatch)
    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = (await db.execute(sqltext(
            "SELECT code_review FROM task_results WHERE id = :r"
        ), {"r": result_id})).scalar_one()
        assert review["unseen_constructs"]["items"] == []
    finally:
        await _cleanup(db, result_id, past, user_id)
