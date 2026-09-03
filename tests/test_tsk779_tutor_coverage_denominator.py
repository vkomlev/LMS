# -*- coding: utf-8 -*-
"""tsk-779: охват наставника считается по тем, кто ЗАСТРЯЛ.

Зачем. Повод для наставника — пара «ученик + задание» с неверной сдачей. Но замер
на боевых данных 03.09 показал: из 254 поводов 182 (72%) — ученик ошибся один раз
и тут же сдал верно сам. Наставник там не нужен, звать его никто не станет, а в
знаменателе он топил долю втрое: чек показывал 6% при пороге 20% и звал разбирать
провал там, где среди застрявших охват 19%.

Раздел задаёт знаменатель, а знаменатель — вердикт чека и продуктовое решение по
наставнику. Ошибка здесь тихая, поэтому проверяется тестом.
"""
from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "scripts"))

from check_tutor_outcomes import split_by_struggle  # noqa: E402


def _повод(user_id: int, task_id: int, wrong_tries: int) -> dict[str, int]:
    return {"user_id": user_id, "task_id": task_id, "wrong_tries": wrong_tries}


def test_одна_ошибка_не_повод_для_наставника():
    """Ученик промахнулся один раз — в знаменатель охвата он не идёт."""
    struggled, one_off, _ = split_by_struggle([_повод(1, 10, 1)], set())
    assert struggled == []
    assert len(one_off) == 1


def test_две_и_больше_ошибок_это_застревание():
    struggled, one_off, _ = split_by_struggle([_повод(1, 10, 2)], set())
    assert len(struggled) == 1
    assert one_off == []


def test_в_охват_идут_только_застрявшие():
    """Разговор по поводу с одной ошибкой в числитель охвата не попадает.

    Иначе знаменатель сузили бы, а числитель оставили полным — доля выросла бы
    сама собой, без единого нового разговора.
    """
    gated = [_повод(1, 10, 1), _повод(2, 20, 3)]
    covered = {(1, 10), (2, 20)}
    struggled, _, covered_struggled = split_by_struggle(gated, covered)
    assert len(struggled) == 1
    assert covered_struggled == {(2, 20)}


def test_боевой_срез_03_09_воспроизводится():
    """Числа с прода: 72 застрявших из 251, из них 14 дошли до наставника."""
    gated = (
        [_повод(i, 1, 3) for i in range(72)]
        + [_повод(i, 2, 1) for i in range(179)]
    )
    covered = {(i, 1) for i in range(14)} | {(0, 2)}
    struggled, one_off, covered_struggled = split_by_struggle(gated, covered)
    assert (len(struggled), len(one_off)) == (72, 179)
    assert len(covered_struggled) == 14
    # 19%, а не 6% по всем поводам — ровно та разница, ради которой правка.
    assert round(100 * len(covered_struggled) / len(struggled)) == 19
    assert round(100 * len(covered) / len(gated)) == 6
