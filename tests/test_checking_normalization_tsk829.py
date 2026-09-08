# -*- coding: utf-8 -*-
"""
tsk-829: два послабления нормализации, каждое — только там, где оно безопасно.

Откуда. Разбор [[tsk-828]] нашёл 12 работ, где ученик решил ВЕРНО, а балл не
получил: расхождение было только в записи ответа. Два класса чинятся движком:

* **буква «ё»** — «небоскрёб» против эталона «небоскреб». Ученик не обязан
  различать ё и е на письме;
* **разрядные пробелы в числе** — «2 102 556 498» против «2102556498» (в живой
  работе там неразрывные пробелы: так копируется из калькулятора и Excel).

Третий класс НЕ чинится и не должен: когда эталон записан через пробелы
(«1 2 3 4 5»), а ответ слитный («12345»), пробел разделяет РАЗНЫЕ значения. Таких
эталонов-наборов в базе 311, и снятие пробелов засчитало бы «37» за ответ «3 7».
Поэтому послабление односторонее — это и делает его безопасным.

Оба послабления — дополнительный путь к зачёту, как `code_ast`: ужесточить
проверку они не могут, только спасти верный ответ.
"""
import os
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.schemas.checking import StudentAnswer, StudentResponse  # noqa: E402
from app.schemas.solution_rules import SolutionRules  # noqa: E402
from app.schemas.task_content import TaskContent  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402

service = CheckingService()

NBSP = " "
NARROW_NBSP = " "


def _rules(accepted: list[str], normalization: list[str] | None = None) -> SolutionRules:
    return SolutionRules.model_validate(
        {
            "max_score": 1,
            "scoring_mode": "all_or_nothing",
            "short_answer": {
                "normalization": normalization if normalization is not None else ["trim", "lower"],
                "accepted_answers": [{"value": v, "score": 1} for v in accepted],
            },
        }
    )


def _check(value: str, rules: SolutionRules, task_type: str = "SA"):
    content = TaskContent.model_validate({"type": task_type, "stem": "Вопрос."})
    answer = StudentAnswer(type=task_type, response=StudentResponse(value=value))
    return service.check_task(content, rules, answer)


# ─── буква ё ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value, etalon",
    [
        ("небоскрёб", "небоскреб"),   # живая работа 11434, задание 4920
        ("небоскреб", "небоскрёб"),   # и обратно — послабление симметрично
        ("ЁЖ", "еж"),
        ("времён", "времен"),
    ],
)
def test_ё_и_е_не_различаются(value: str, etalon: str):
    result = _check(value, _rules([etalon], normalization=["trim", "lower"]))
    assert result.is_correct is True
    assert result.score == 1


def test_ё_работает_и_без_lower():
    """Замена идёт до шагов и от них не зависит."""
    result = _check("Ёж", _rules(["Еж"], normalization=["trim"]))
    assert result.is_correct is True


# ─── разрядные пробелы в числе ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "2 102 556 498",
        f"2{NBSP}102{NBSP}556{NBSP}498",        # живая работа 4202, задание 119
        f"2{NARROW_NBSP}102{NARROW_NBSP}556{NARROW_NBSP}498",
        " 2 102 556 498 ",
    ],
)
def test_разрядные_пробелы_в_числе_засчитываются(value: str):
    result = _check(value, _rules(["2102556498"], normalization=["trim"]))
    assert result.is_correct is True
    assert result.score == 1


def test_число_без_пробелов_как_и_раньше():
    assert _check("2102556498", _rules(["2102556498"], normalization=["trim"])).is_correct is True


# ─── чего послабление делать НЕ должно ──────────────────────────────────────


def test_слитный_ответ_не_засчитывается_за_набор_значений():
    """Ключевая граница: «3 7» — это два значения, а «37» — одно число."""
    result = _check("37", _rules(["3 7"], normalization=["trim"]))
    assert result.is_correct is False


def test_слитные_цифры_не_засчитываются_за_перечисление():
    """Живой случай заданий 216 и 230 — их чинить нормализацией нельзя."""
    result = _check("123456789", _rules(["1 2 3 4 5 6 7 8 9"], normalization=["trim"]))
    assert result.is_correct is False


def test_другое_число_с_пробелами_не_засчитывается():
    result = _check("2 102 556 499", _rules(["2102556498"], normalization=["trim"]))
    assert result.is_correct is False


def test_текст_с_пробелами_не_подгоняется_под_число():
    result = _check("два миллиарда", _rules(["2102556498"], normalization=["trim"]))
    assert result.is_correct is False


def test_дробное_с_пробелами_не_засчитывается():
    """Послабление про целое число; «1 000,5» против «1000.5» — не наш случай."""
    result = _check("1 000,5", _rules(["1000.5"], normalization=["trim"]))
    assert result.is_correct is False


# ─── инвариант: проверка не стала строже ────────────────────────────────────


@pytest.mark.parametrize(
    "value, etalon, correct",
    [
        ("7", "7", True),
        ("8", "7", False),
        ("Привет", "привет", True),          # lower работает как раньше
        ("print(i)", "print(i)", True),
        ("print(I)", "print(i)", False),     # регистр в коде значим (tsk-828)
    ],
)
def test_прежнее_поведение_сохранено(value: str, etalon: str, correct: bool):
    norm = ["trim", "lower"] if etalon == "привет" else ["trim"]
    result = _check(value, _rules([etalon], normalization=norm))
    assert result.is_correct is correct
