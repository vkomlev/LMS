# -*- coding: utf-8 -*-
"""tsk-779: SQL еженедельного чека обязан совпадать с предикатом сервиса.

Зачем. `check_ungradable_tasks.py` ищет задания без эталона своим SQL, а движок
решает тот же вопрос методом `SolutionRules.has_reference_answer()`. Два описания
одного условия разъехались молча: сервис давно считал эталоном ещё и `turtle_sim`
(сверка трассы рисунка, tsk-412) и regex, а SQL знал только `accepted_answers`.
Итог — 10 заданий курса 165 месяц лежали в еженедельном отчёте как непроверяемые,
хотя проверялись симулятором и живые сдачи это подтверждали.

Ошибка не в том, что кто-то поленился: SQL физически не может позвать питоновский
предикат, поэтому зеркало неизбежно. Этот тест — то, чего не хватало: он гоняет
ОБА описания по одному набору форм правил и падает, как только они расходятся.

Стратегия — как в `test_tsk636_task_rules_audit.py`: временный курс и задания в
транзакции фикстуры `db`, откат после теста, в базе ничего не остаётся.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "scripts"))

from check_ungradable_tasks import SQL_HOLLOW_RULES  # noqa: E402

from app.schemas.solution_rules import SolutionRules  # noqa: E402

_TASK_CONTENT = '{"type": "SA_COM", "stem": "x"}'

_BASE: dict[str, Any] = {
    "max_score": 1,
    "scoring_mode": "all_or_nothing",
    "auto_check": True,
    "manual_review_required": False,
}

_TURTLE_SIM: dict[str, Any] = {
    "expected_trace": {
        "segments": [],
        "final_state": {"position": [0.0, 0.0], "heading": 0.0, "pen_down": True},
    },
    "tolerance_px": 1.0,
}


def _short_answer(
    accepted: list[str] | None = None,
    *,
    use_regex: bool = False,
    regex: str | None = None,
) -> dict[str, Any]:
    return {
        "normalization": ["trim", "lower"],
        "accepted_answers": [{"value": v, "score": 1} for v in (accepted or [])],
        "use_regex": use_regex,
        "regex": regex,
    }


# Каждый случай: имя → правила. Ожидание НЕ хардкодится: оно берётся у самого
# сервиса, иначе тест зафиксировал бы моё сегодняшнее понимание, а не поведение.
CASES: dict[str, dict[str, Any]] = {
    "правил короткого ответа нет вовсе": {**_BASE, "short_answer": None},
    "блок есть, но пустой": {**_BASE, "short_answer": _short_answer()},
    "эталон списком ответов": {**_BASE, "short_answer": _short_answer(["42"])},
    "эталон регулярным выражением": {
        **_BASE,
        "short_answer": _short_answer(use_regex=True, regex=r"\d+"),
    },
    "regex включён, но не задан": {
        **_BASE,
        "short_answer": _short_answer(use_regex=True, regex=None),
    },
    "regex задан, но выключен": {
        **_BASE,
        "short_answer": _short_answer(use_regex=False, regex=r"\d+"),
    },
    "эталон трассой черепахи": {
        **_BASE,
        "short_answer": _short_answer(),
        "turtle_sim": _TURTLE_SIM,
    },
    "turtle_sim пустой (JSON-null)": {
        **_BASE,
        "short_answer": _short_answer(),
        "turtle_sim": None,
    },
}


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


async def _insert_task(db, course_id: int, rules: dict[str, Any]) -> int:
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
            {
                "tc": _TASK_CONTENT,
                "cid": course_id,
                "sr": json.dumps(rules, ensure_ascii=False),
            },
        )
    ).first()
    await db.flush()
    return int(row.id)


@pytest.mark.asyncio
async def test_чек_и_сервис_одинаково_решают_есть_ли_эталон(db):
    """Ни одна форма правил не должна оцениваться чеком и движком по-разному."""
    course_id = await _new_course(db, "tsk-779 зеркало предиката")

    task_ids: dict[int, str] = {}
    expected_hollow: dict[str, bool] = {}
    for name, rules in CASES.items():
        model = SolutionRules.model_validate(rules)
        # В базу кладём правила в том виде, в каком их пишет сервис: со ВСЕМИ ключами
        # и дефолтами. SQL чека опирается на их наличие (`custom_scoring_config`,
        # `quiz`, `correct_options`), и на «сокращённом» словаре тест проверял бы
        # форму, которой на проде не бывает.
        task_id = await _insert_task(db, course_id, model.model_dump(mode="json"))
        task_ids[task_id] = name
        # Источник истины — сам движок: «эталона нет» = задание обязано попасть в находки.
        expected_hollow[name] = not model.has_reference_answer()

    found = {
        row.id
        for row in (await db.execute(text(SQL_HOLLOW_RULES))).all()
        if row.id in task_ids
    }

    расхождения = [
        f"«{name}»: сервис говорит эталон "
        f"{'ОТСУТСТВУЕТ' if expected_hollow[name] else 'ЕСТЬ'}, "
        f"а чек задание {'нашёл' if task_id in found else 'не нашёл'}"
        for task_id, name in task_ids.items()
        if (task_id in found) is not expected_hollow[name]
    ]
    assert not расхождения, (
        "SQL чека разошёлся с `SolutionRules.has_reference_answer()`:\n  "
        + "\n  ".join(расхождения)
        + "\nПравить `SQL_HOLLOW_RULES` в scripts/check_ungradable_tasks.py, "
          "а не ожидания теста."
    )


@pytest.mark.asyncio
async def test_набор_форм_покрывает_обе_ветки(db):
    """Страховка от вырождения: в наборе есть и «эталон есть», и «эталона нет».

    Без неё тест выше остался бы зелёным, даже если однажды все случаи съедут в одну
    сторону — и перестал бы что-либо проверять.
    """
    вердикты = {
        SolutionRules.model_validate(rules).has_reference_answer() for rules in CASES.values()
    }
    assert вердикты == {True, False}, "набор форм обязан покрывать оба исхода предиката"
