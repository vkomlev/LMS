# tests/test_unseen_constructs_tsk975.py
"""
tsk-975: `no_credit_courses` у `list_literal`/`split_join` адресован
конкретному курсу-нарушителю (108 «Строки»), а не любому курсу задания.

Живой случай: Земзюлин Дмитрий (id=4577), 17 отмеченных материалов курса
109 «Списки (массивы) в Python», получил пометку `list_literal` за задание
в этом же курсе 109 (id-10367, evidence `a = [[0] * 8 for _ in range(8)]`).
До tsk-975 льгота "курс мельком упомянул" (написанная для курса 108) не
разбирала, какой именно курс перед ней, и гасила автозачёт и личную отметку
для ЛЮБОГО курса задания — включая 109, целевой курс по самой теме.

Что закрываем:
- курс 109 и любой другой курс с настоящей практикой (не только 108) даёт
  зачёт `list_literal`/`split_join` как обычно (`no_credit_courses` пуст
  для этого курса) - и автоматически (тема пройдена целиком), и по личной
  отметке материала СВОЕГО ЖЕ курса;
- живой случай tsk-916/938 (курс 108, льгота НЕ действует) не сломан -
  покрыт существующими тестами `tests/test_unseen_constructs_tsk915.py`
  (`test_current_course_does_not_silence_split_join`,
  `test_own_course_material_marked_personally_does_not_silence_split_join`),
  теперь явно пришпиленными к настоящему id 108.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text as sqltext

from app.services import code_review_cron_service
from tests.test_unseen_constructs_tsk864 import _cleanup, _seed, _stub_model


async def _retarget_to_course(db, result_id: int, course_id: int) -> None:
    """
    Переводит "текущий" курс задания (случайный id от `_seed`) на настоящий
    курс тестовой БД (dev-копия прод-данных) - без него `no_credit_courses`
    и общая льгота tsk-864 не смогут сработать на реальном материале курса.
    """
    task_id, old_course_id = (await db.execute(sqltext(
        "SELECT t.id, t.course_id FROM task_results tr "
        "JOIN tasks t ON t.id = tr.task_id WHERE tr.id = :r"
    ), {"r": result_id})).one()
    await db.execute(sqltext(
        "UPDATE materials SET course_id = :cid WHERE course_id = :old"
    ), {"cid": course_id, "old": old_course_id})
    await db.execute(sqltext(
        "UPDATE tasks SET course_id = :cid WHERE id = :t"
    ), {"cid": course_id, "t": task_id})
    await db.commit()


async def _cleanup_retargeted(db, result_id: int, past_course_id: int, user_id: int) -> None:
    """
    Как `_cleanup`, но не удаляет курс, на который сделан retarget, - в
    тестовой БД это настоящий боевой курс (dev-копия), а не фикстура теста.
    Внешняя транзакция теста откатывается целиком в любом случае (см.
    докстринг `db_conn` в conftest) - это уборка для порядка внутри теста.
    """
    task_id = (await db.execute(sqltext(
        "SELECT task_id FROM task_results WHERE id = :r"
    ), {"r": result_id})).scalar_one()
    await db.execute(sqltext(
        "DELETE FROM student_material_progress WHERE student_id = :u"
    ), {"u": user_id})
    await db.execute(sqltext("DELETE FROM task_results WHERE id = :r"), {"r": result_id})
    await db.execute(sqltext("DELETE FROM tasks WHERE id = :t"), {"t": task_id})
    # Материал темы (`_seed`) после retarget лежит в реальном курсе 109 -
    # удаляем его точечно по заголовку. Нейтральный материал (если тест его
    # заводил) лежит в `past` и уйдёт каскадом вместе с курсом ниже.
    await db.execute(sqltext(
        "DELETE FROM materials WHERE title = 'материал темы' AND course_id = 109"
    ))
    await db.execute(sqltext("DELETE FROM courses WHERE id = :p"), {"p": past_course_id})
    await db.execute(sqltext("DELETE FROM users WHERE id = :u"), {"u": user_id})
    await db.commit()


async def test_list_literal_not_flagged_inside_its_own_course(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Живой случай Земзюлина: задание на списки СДАНО в курсе 109 «Списки», и
    ученик лично отметил материал ЭТОГО ЖЕ курса пройденным (как Земзюлин
    отметил 17 материалов курса 109 до сдачи). `list_literal` не должен
    появиться - курс 109 не входит в `no_credit_courses` этой конструкции,
    поэтому действует обычное правило tsk-864 (личная отметка засчитывается
    независимо от того, в каком курсе лежит материал).
    """
    result_id, past, user_id = await _seed(
        db,
        # tsk-975: код максимально близкий к живой сдаче id-10367.
        code="a = [[0] * 8 for _ in range(8)]\nprint(a)\n",
        course_materials=[
            "<h3>Создание списков</h3>"
            "<p>Список можно создать, перечислив его элементы в квадратных "
            "скобках через запятую: <code>my_list = [1, 2, 3]</code>.</p>"
        ],
        completed_materials=[],
    )
    await _retarget_to_course(db, result_id, course_id=109)

    # Личная отметка материала СВОЕГО ЖЕ курса (109) - как Земзюлин отмечал
    # материалы курса 109 по ходу обучения, до сдачи задания.
    material_id = (await db.execute(sqltext(
        """
        SELECT m.id FROM task_results tr
        JOIN tasks t ON t.id = tr.task_id
        JOIN materials m ON m.course_id = t.course_id
        WHERE tr.id = :r AND m.title = 'материал темы'
        """
    ), {"r": result_id})).scalar_one()
    await db.execute(sqltext(
        "INSERT INTO student_material_progress (student_id, material_id, status, completed_at, source) "
        "VALUES (:u, :m, 'completed', now(), 'system')"
    ), {"u": user_id, "m": material_id})
    await db.commit()

    _stub_model(monkeypatch)
    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = (await db.execute(sqltext(
            "SELECT code_review FROM task_results WHERE id = :r"
        ), {"r": result_id})).scalar_one()
        unseen = review["unseen_constructs"]
        codes = [i["code"] for i in unseen["items"]] if unseen else []
        assert "list_literal" not in codes
    finally:
        await _cleanup_retargeted(db, result_id, past, user_id)


async def test_list_literal_not_flagged_by_whole_course_credit_in_its_own_course(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Тот же курс 109, но БЕЗ личной отметки материалов - общее правило tsk-864
    ("текущая тема пройдена целиком") тоже должно работать как обычно для
    курса 109, раз он не в `no_credit_courses`. Ученику нужен хотя бы один
    отмеченный материал где-то ВООБЩЕ (иначе `materials_seen == 0` и сверки
    нет вовсе) - берём нейтральный материал из прошлой темы, как в
    tsk-915 `test_current_course_does_not_silence_split_join`.
    """
    result_id, past, user_id = await _seed(
        db,
        code="a = [[0] * 8 for _ in range(8)]\nprint(a)\n",
        course_materials=[
            "<h3>Создание списков</h3>"
            "<p>Список можно создать, перечислив его элементы в квадратных "
            "скобках через запятую: <code>my_list = [1, 2, 3]</code>.</p>"
        ],
        completed_materials=[],
    )
    await _retarget_to_course(db, result_id, course_id=109)

    neutral_material_id = (await db.execute(sqltext(
        "INSERT INTO materials (course_id, title, type, content) "
        "VALUES (:cid, 'пройденный материал', 'text', CAST(:c AS jsonb))"
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
        codes = [i["code"] for i in unseen["items"]]
        assert "list_literal" not in codes
    finally:
        await _cleanup_retargeted(db, result_id, past, user_id)
