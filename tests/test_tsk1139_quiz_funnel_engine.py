"""tsk-1139: движок правил квиза-воронки — на копии реального контента.

`tests/fixtures/tsk1139_quiz_razvilka.json` — снимок `quiz-razvilka.lms.json`
контентной сессии на 26.09. Если контент поменяет формат, эти тесты покажут,
что движок его больше не понимает, раньше, чем это увидит посетитель.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import quiz_funnel_engine as eng

SPEC = json.loads(
    (Path(__file__).parent / "fixtures" / "tsk1139_quiz_razvilka.json").read_text(encoding="utf-8")
)


def _codes(w: eng.Walk) -> list[str]:
    return [s.code for s in w.steps]


# ─── условия ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "cond, expected",
    [
        ({"Q": ["a"]}, True),
        ({"Q": ["b"]}, False),
        ({"not": {"Q": ["b"]}}, True),
        ({"all": [{"Q": ["a"]}, {"role": ["parent"]}]}, True),
        ({"all": [{"Q": ["a"]}, {"role": ["teen"]}]}, False),
        ({"any": [{"Q": ["b"]}, {"X": ["S1"]}]}, True),
        ("else", True),
        ({"MISSING": ["a"]}, False),
        (["странное"], False),
    ],
)
def test_eval_condition(cond, expected):
    ctx = eng.Context(answers={"Q": ["a"]}, role="parent", derived={"X": "S1"})
    assert eng.eval_condition(cond, ctx) is expected


# ─── путь ───────────────────────────────────────────────────────────────────

def test_start_without_params_asks_who_first():
    w = eng.walk(SPEC, {}, {})
    assert _codes(w) == ["Q0"]
    assert w.is_complete is False


def test_parent_goes_to_ege_by_goal_and_keeps_parent_voice():
    answers = {"Q0": ["a_parent"], "P1": ["p_age_16_17"], "P2": ["p_goal_ege"]}
    w = eng.walk(SPEC, answers, {})
    assert w.branch == "ege"
    assert w.role == "parent"
    current = w.steps[-1]
    assert current.code == "E1"
    assert eng.stem_for(current.question, w.role) == "В каком классе ребёнок?"


def test_ege_check_question_only_for_teen():
    """E3 (мини-проверка кода) показывается только ученику, родителю — нет."""
    base = {"E1": ["e_grade_11"], "E2": ["e_py_1"]}
    teen = eng.walk(SPEC, base, {"branch": "ege", "role": "teen"})
    parent = eng.walk(SPEC, base, {"branch": "ege", "role": "parent"})
    assert teen.steps[-1].code == "E3"
    assert parent.steps[-1].code == "E4"


def test_link_param_skips_entry_question():
    w = eng.walk(SPEC, {}, {"branch": "adult"})
    assert "Q0" not in _codes(w)
    assert w.branch == "adult"


def test_dir_param_prefills_direction():
    w = eng.walk(SPEC, {}, {"branch": "adult", "dir": "qa"})
    assert w.prefilled == {"A1": ["a_dir_qa"]}
    assert "A1" not in _codes(w)


def test_unknown_params_are_dropped():
    assert eng.valid_params(SPEC, {"branch": "hacker", "role": "teen", "dir": None}) == {"role": "teen"}


# ─── итог ───────────────────────────────────────────────────────────────────

def _finish(params: dict, pick_first: bool = True) -> eng.Evaluation:
    answers: dict[str, list[str]] = {}
    for _ in range(50):
        w = eng.walk(SPEC, answers, params)
        if w.is_complete:
            break
        step = w.steps[-1]
        ctx = eng.Context(answers={**answers, **w.prefilled}, role=w.role)
        options = eng.visible_options(step.question, ctx)
        answers[step.code] = [options[0 if pick_first else -1]["id"]]
    return eng.evaluate(SPEC, answers, params)


@pytest.mark.parametrize(
    "params", [{"branch": "parent"}, {"branch": "teen"}, {"branch": "adult"},
               {"branch": "ege", "role": "teen"}, {"branch": "ege", "role": "parent"}],
)
def test_every_branch_finishes_with_own_outcome(params):
    ev = _finish(params)
    assert ev.walk.is_complete
    assert ev.outcome is not None
    # Итог принадлежит ветке, где человек закончил (tsk-933: чужих текстов не показываем).
    assert ev.outcome["branch"] == ev.walk.branch


def test_check_feedback_for_wrong_answer():
    text = eng.check_feedback(SPEC, "E3", ["e_chk_10"])
    assert text and "range" in text


def test_real_content_has_no_dead_ends():
    """Полнота правил контента: у каждого пути итог, каждый итог достижим."""
    cov = eng.coverage_check(SPEC, runs_per_params=300)
    assert cov.without_outcome == 0, cov.samples_without_outcome
    assert cov.unreached_outcomes == []


def test_coverage_catches_missing_else():
    """Если у ветки убрать запасной итог, проверка это находит."""
    broken = json.loads(json.dumps(SPEC))
    broken["outcomes"] = [o for o in broken["outcomes"] if o.get("if") != "else"]
    cov = eng.coverage_check(broken, runs_per_params=200)
    assert cov.without_outcome > 0


def test_goto_loop_does_not_hang():
    loop = {"branches": {"root": [{"code": "A", "task_content": {"options": [{"id": "x"}]},
                                   "goto": {"x": "b"}}],
                         "b": [{"code": "B", "task_content": {"options": [{"id": "y"}]},
                                "goto": {"y": "root"}}]}}
    w = eng.walk(loop, {"A": ["x"], "B": ["y"]}, {})
    assert w.is_complete
