# -*- coding: utf-8 -*-
"""tsk-636: журнал правок эталона задания (`solution_rules`) в `task_audit`.

Зачем. Разбор десяти незаслуженных незачётов (побочная находка tsk-590) упёрся в то,
что истории правил в базе нет: `task_audit` (tsk-114) писал только `course_id` и
`is_active`, а `content_provenance` заполняет один лишь веб-редактор — из десяти
случаев один. Отличать «сбой сравнения» от «эталон дополнили после сдачи» приходилось
косвенными уликами. Теперь правка эталона оставляет след.

Стратегия — как в `test_tsk114_task_audit.py`: временный курс и задания в транзакции
фикстуры `db`, откат после теста, в базе ничего не остаётся.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

_TASK_CONTENT = '{"type": "SA_COM", "stem": "x"}'


def _rules(accepted: list[str], *, normalization: list[str] | None = None) -> str:
    return json.dumps(
        {
            "max_score": 1,
            "scoring_mode": "all_or_nothing",
            "auto_check": True,
            "manual_review_required": False,
            "short_answer": {
                "normalization": normalization or ["trim", "lower"],
                "accepted_answers": [{"value": v, "score": 1} for v in accepted],
                "use_regex": False,
                "regex": None,
            },
        },
        ensure_ascii=False,
    )


async def _new_course(db, title: str) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO courses (title, description, access_level, is_required)
                VALUES (:title, 'test', 'self_guided', false)
                RETURNING id
                """
            ),
            {"title": title},
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _insert_task(db, course_id: int, rules: str) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO tasks (task_content, course_id, difficulty_id, solution_rules,
                                   max_score, is_active)
                VALUES (CAST(:tc AS jsonb), :cid, 1, CAST(:sr AS jsonb), 1, true)
                RETURNING id
                """
            ),
            {"tc": _TASK_CONTENT, "cid": course_id, "sr": rules},
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _audit_rows(db, task_id: int) -> list[dict]:
    rows = (
        await db.execute(
            text(
                """
                SELECT action, old_answer_key, new_answer_key,
                       old_content_key, new_content_key,
                       old_course_id, new_course_id, changed_by
                FROM task_audit
                WHERE task_id = :tid
                ORDER BY id
                """
            ),
            {"tid": task_id},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _accepted(answer_key: dict | None) -> list[str]:
    """Список эталонных ответов из выжимки — то, ради чего журнал и заведён."""
    if not answer_key:
        return []
    return [a["value"] for a in answer_key["short_answer"]["accepted_answers"]]


@pytest.mark.asyncio
async def test_дополнение_эталона_попадает_в_журнал(db):
    """Главный случай разбора: список принимаемых ответов расширили после сдачи."""
    course = await _new_course(db, "tsk636_b1")
    task_id = await _insert_task(db, course, _rules(["накопитель"]))

    await db.execute(text("SELECT set_config('app.audit_actor', 'test_actor', true)"))
    await db.execute(
        text("UPDATE tasks SET solution_rules = CAST(:sr AS jsonb) WHERE id = :tid"),
        {"sr": _rules(["накопитель", "диск", "HDD"]), "tid": task_id},
    )
    await db.flush()

    rows = await _audit_rows(db, task_id)
    assert len(rows) == 1
    assert rows[0]["action"] == "UPDATE"
    assert _accepted(rows[0]["old_answer_key"]) == ["накопитель"]
    assert _accepted(rows[0]["new_answer_key"]) == ["накопитель", "диск", "HDD"]
    assert rows[0]["changed_by"] == "test_actor"


@pytest.mark.asyncio
async def test_смена_шагов_нормализации_тоже_меняет_вердикт_и_тоже_в_журнале(db):
    """Сужение нормализации отвергает ответы, которые вчера засчитывались.

    Ровно это случилось с тремя заданиями курса Python 13-16 июля: тот же ответ
    сначала зачли, через три дня — нет. Без журнала это выглядело как сбой движка.
    """
    course = await _new_course(db, "tsk636_b2")
    task_id = await _insert_task(
        db, course, _rules(["def privet():"], normalization=["trim", "lower", "strip_punctuation"])
    )

    await db.execute(
        text("UPDATE tasks SET solution_rules = CAST(:sr AS jsonb) WHERE id = :tid"),
        {"sr": _rules(["def privet():"], normalization=["trim", "collapse_spaces"]), "tid": task_id},
    )
    await db.flush()

    rows = await _audit_rows(db, task_id)
    assert len(rows) == 1
    assert rows[0]["old_answer_key"]["short_answer"]["normalization"] == [
        "trim", "lower", "strip_punctuation",
    ]
    assert rows[0]["new_answer_key"]["short_answer"]["normalization"] == ["trim", "collapse_spaces"]


@pytest.mark.asyncio
async def test_повторная_запись_тех_же_правил_журнал_не_засоряет(db):
    """Повторный импорт с теми же правилами — не изменение.

    Сравнение идёт по значению jsonb, а не по тексту, поэтому иной порядок ключей
    и иные пробелы в JSON строки не создают.
    """
    course = await _new_course(db, "tsk636_b3")
    rules = _rules(["11110"])
    task_id = await _insert_task(db, course, rules)

    await db.execute(
        text("UPDATE tasks SET solution_rules = CAST(:sr AS jsonb) WHERE id = :tid"),
        {"sr": rules, "tid": task_id},
    )
    await db.flush()

    assert await _audit_rows(db, task_id) == []


@pytest.mark.asyncio
async def test_правка_текста_задания_пишет_отпечаток_условия(db):
    """tsk-760: правка формулировки теперь попадает в журнал — отпечатком.

    В редакции tsk-636 `task_content` в WHEN не входил, и правку условия
    журнал не видел: именно поэтому нельзя было отличить ручную правку
    формулировки от импорта. Теперь строка пишется, но хранит не текст, а
    sha256 условия до и после — журнал отвечает на «правили ли и когда», а не
    хранит вторую копию контента. Выжимка правила при этом пустая: правило не
    менялось.
    """
    course = await _new_course(db, "tsk636_b4")
    task_id = await _insert_task(db, course, _rules(["11110"]))

    await db.execute(
        text("UPDATE tasks SET task_content = CAST(:tc AS jsonb) WHERE id = :tid"),
        {"tc": '{"type": "SA_COM", "stem": "новая формулировка"}', "tid": task_id},
    )
    await db.flush()

    rows = await _audit_rows(db, task_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["old_content_key"] and row["new_content_key"]
    assert row["old_content_key"] != row["new_content_key"]
    assert row["old_answer_key"] is None and row["new_answer_key"] is None


@pytest.mark.asyncio
async def test_смена_курса_без_правки_правил_оставляет_выжимку_пустой(db):
    """Поведение tsk-114 сохранено, и колонки эталона при этом не заполняются.

    Иначе журнал переставал бы отвечать на свой вопрос: непустой `new_answer_key`
    обязан значить «правило меняли», а не «строка вообще есть».
    """
    course_a = await _new_course(db, "tsk636_b5_a")
    course_b = await _new_course(db, "tsk636_b5_b")
    task_id = await _insert_task(db, course_a, _rules(["11110"]))

    await db.execute(
        text("UPDATE tasks SET course_id = :cid WHERE id = :tid"),
        {"cid": course_b, "tid": task_id},
    )
    await db.flush()

    rows = await _audit_rows(db, task_id)
    assert len(rows) == 1
    assert rows[0]["old_course_id"] == course_a and rows[0]["new_course_id"] == course_b
    assert rows[0]["old_answer_key"] is None
    assert rows[0]["new_answer_key"] is None


@pytest.mark.asyncio
async def test_удаление_задания_сохраняет_прежний_эталон(db):
    """У DELETE «стало» нет, но «было» обязано остаться — иначе правило исчезнет вместе с заданием."""
    course = await _new_course(db, "tsk636_b6")
    task_id = await _insert_task(db, course, _rules(["11110"]))

    await db.execute(text("DELETE FROM tasks WHERE id = :tid"), {"tid": task_id})
    await db.flush()

    rows = await _audit_rows(db, task_id)
    assert len(rows) == 1
    assert rows[0]["action"] == "DELETE"
    assert _accepted(rows[0]["old_answer_key"]) == ["11110"]
    assert rows[0]["new_answer_key"] is None


@pytest.mark.asyncio
async def test_выжимка_не_тащит_эталонную_трассу_черепахи(db):
    """`turtle_sim.expected_trace` — тысячи чисел; в журнал она не копируется.

    Факт правки при этом виден: остальные поля блока в выжимке остаются.
    """
    course = await _new_course(db, "tsk636_b7")
    trace = {
        "segments": [
            {"kind": "line", "start": [0, 0], "end": [i, 0], "color_rgb": [0, 0, 0]}
            for i in range(50)
        ],
        "final_state": {"position": [0, 0], "heading": 0.0, "pen_down": True},
    }
    before = json.dumps(
        {"max_score": 1, "turtle_sim": {"expected_trace": trace, "tolerance_px": 0.75}},
        ensure_ascii=False,
    )
    after = json.dumps(
        {"max_score": 1, "turtle_sim": {"expected_trace": trace, "tolerance_px": 2.0}},
        ensure_ascii=False,
    )
    task_id = await _insert_task(db, course, before)

    await db.execute(
        text("UPDATE tasks SET solution_rules = CAST(:sr AS jsonb) WHERE id = :tid"),
        {"sr": after, "tid": task_id},
    )
    await db.flush()

    rows = await _audit_rows(db, task_id)
    assert len(rows) == 1
    assert "expected_trace" not in rows[0]["old_answer_key"]["turtle_sim"]
    assert "expected_trace" not in rows[0]["new_answer_key"]["turtle_sim"]
    assert rows[0]["old_answer_key"]["turtle_sim"]["tolerance_px"] == 0.75
    assert rows[0]["new_answer_key"]["turtle_sim"]["tolerance_px"] == 2.0


@pytest.mark.asyncio
async def test_выжимка_пустого_правила_это_sql_null(db):
    """JSON-null в `solution_rules` — не объект: выжимка обязана быть SQL NULL, а не '{}'.

    Пустое правило живёт в трёх формах, и `IS NULL` по jsonb-null врёт (плейбук ЕГЭ §6.1).
    Здесь проверяется, что функция выжимки на этой форме не падает и не выдумывает объект.
    """
    course = await _new_course(db, "tsk636_b8")
    task_id = await _insert_task(db, course, "null")

    await db.execute(
        text("UPDATE tasks SET solution_rules = CAST(:sr AS jsonb) WHERE id = :tid"),
        {"sr": _rules(["11110"]), "tid": task_id},
    )
    await db.flush()

    rows = await _audit_rows(db, task_id)
    assert len(rows) == 1
    assert rows[0]["old_answer_key"] is None
    assert _accepted(rows[0]["new_answer_key"]) == ["11110"]


@pytest.mark.asyncio
async def test_журнал_остаётся_неизменяемым(db):
    """Append-only (tsk-114) правка не ослабила: стереть свой след по-прежнему нельзя."""
    course = await _new_course(db, "tsk636_b9")
    task_id = await _insert_task(db, course, _rules(["11110"]))
    await db.execute(
        text("UPDATE tasks SET solution_rules = CAST(:sr AS jsonb) WHERE id = :tid"),
        {"sr": _rules(["11110", "11111"]), "tid": task_id},
    )
    await db.flush()

    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(
            text("UPDATE task_audit SET new_answer_key = NULL WHERE task_id = :tid"),
            {"tid": task_id},
        )
        await db.flush()
