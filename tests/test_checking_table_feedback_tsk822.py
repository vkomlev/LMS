# -*- coding: utf-8 -*-
"""
tsk-822: слепой `all_or_nothing` у TBL_COM — ученик видит ЧИСЛО совпавших рядов.

Откуда. Все 306 активных TBL_COM оцениваются целиком: три верных ряда из четырёх
дают ноль, и до tsk-822 ученик не знал, ошибся он в одном месте или во всех.
Улика — tsk-800: Глеб Анфалов с третьей попытки начал перебирать ряд, который
с самого начала был верным, потому что «неверно» накрывало всю таблицу.

Почему не частичный балл. `partial` при `max_score = 1` не даёт ничего:
`int(1 * 3/4) = 0`. А если поднять `max_score` до числа рядов, включается
`PASSED при score/max_score >= 0.5` (`learning_engine_service.PASS_THRESHOLD_RATIO`) —
то есть зачёт по заданию ЕГЭ за половину верных строк. Решение оператора 08.09:
оценивание не трогать, сказать числом.

Почему только число. Номер неверной строки при трёх попытках позволял бы добить
ответ перебором, не решая задачу. Здесь это зафиксировано тестом, а не намерением.
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


def _content(columns: int = 1) -> TaskContent:
    return TaskContent.model_validate(
        {"type": "TBL_COM", "stem": "Ответьте на вопросы.", "table": {"columns": columns}}
    )


def _rules(accepted: list[str], *, max_score: int = 1) -> SolutionRules:
    return SolutionRules.model_validate(
        {
            "max_score": max_score,
            "scoring_mode": "all_or_nothing",
            "short_answer": {
                "normalization": ["trim", "lower"],
                "accepted_answers": [{"value": v, "score": max_score} for v in accepted],
            },
        }
    )


def _check(value: str, rules: SolutionRules, columns: int = 1):
    answer = StudentAnswer(type="TBL_COM", response=StudentResponse(value=value))
    return service.check_task(_content(columns), rules, answer)


def _general(result) -> str:
    assert result.feedback is not None
    return result.feedback.general or ""


# ─── Случай, ради которого всё делалось ─────────────────────────────────────


def test_случай_tsk800_ученик_видит_что_ошибка_одна():
    """3472: эталон 17/9/8, ответ ученика 17/16/8 — две строки из трёх верны.

    Именно этого сообщения не хватало: увидев его, ученик не стал бы с третьей
    попытки перебирать третью строку, которая была правильной.
    """
    result = _check("17\n16\n8", _rules(["17\n9\n8"]))
    assert result.is_correct is False
    assert result.score == 0  # оценивание не изменилось
    assert "Совпало 2 значения из 3" in _general(result)


def test_ученику_не_называют_какая_строка_неверна():
    """Только количество: номер строки при трёх попытках = ответ перебором."""
    general = _general(_check("17\n16\n8", _rules(["17\n9\n8"])))
    for leak in ("строка 2", "вторая", "16", "9"):
        assert leak not in general


# ─── Что и когда сообщается ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value, expected_phrase",
    [
        ("20\n34\n38\n99", "Совпало 3 значения из 4"),
        ("20\n34\n99\n99", "Совпало 2 значения из 4"),
        ("20\n99\n99\n99", "Совпало 1 значение из 4"),
        ("99\n99\n99\n99", "Ни одно значение не совпало с эталоном"),
    ],
)
def test_число_совпавших_рядов(value: str, expected_phrase: str):
    """Склонение идёт вместе с числом: 1 значение, 2 значения, 5 значений."""
    result = _check(value, _rules(["20\n34\n38\n33"]))
    assert result.is_correct is False
    assert expected_phrase in _general(result)


def test_пять_совпавших_склоняются_правильно():
    rules = _rules(["1\n2\n3\n4\n5\n6"])
    assert "Совпало 5 значений из 6" in _general(_check("1\n2\n3\n4\n5\n9", rules))


def test_многостолбцовая_таблица_говорит_про_строки():
    """При двух столбцах ряд — это строка таблицы, а не одно значение."""
    result = _check("10 20\n30 99\n50 60", _rules(["10 20\n30 40\n50 60"]), columns=2)
    assert result.is_correct is False
    assert "Совпало 2 строки из 3" in _general(result)


def test_верный_ответ_не_получает_подсчёта():
    result = _check("17\n9\n8", _rules(["17\n9\n8"]))
    assert result.is_correct is True
    assert "Совпало" not in _general(result)


def test_однострочный_эталон_молчит():
    """«0 из 1» — то же самое, что «неверно»: лишний шум."""
    result = _check("42", _rules(["7"]))
    assert result.is_correct is False
    assert "Совпало" not in _general(result)
    assert "Ни одно значение" not in _general(result)


def test_пустой_ответ_ведёт_себя_как_прежде():
    result = _check("", _rules(["17\n9\n8"]))
    assert "пуст" in _general(result).lower()
    assert "Совпало" not in _general(result)


def test_ответ_одной_строкой_не_получает_ложного_нуля():
    """tsk-752/tsk-383: тот же ответ, набранный одной строкой, засчитывается —
    и подсчёт совпадений обязан идти по тому же разбору, а не по построчному."""
    result = _check("17 9 8", _rules(["17\n9\n8"]))
    assert result.is_correct is True
    assert "Ни одно значение" not in _general(result)


def test_лишние_ряды_не_дают_совпало_N_из_N():
    """Все ряды эталона на месте, но ответ неверен из-за лишнего ряда —
    фраза «совпало 3 из 3» тут сбивала бы с толку, поэтому её нет."""
    result = _check("17\n9\n8\n5", _rules(["17\n9\n8"]))
    assert result.is_correct is False
    assert "Совпало" not in _general(result)


def test_считается_ближайший_из_нескольких_эталонов():
    """Эталонов может быть несколько и разной длины — берём тот, к которому
    ответ ближе по ДОЛЕ совпавших рядов, а не по их числу."""
    rules = _rules(["1\n2\n3", "1\n2\n9\n9\n9\n9"])
    assert "Совпало 2 значения из 3" in _general(_check("1\n2\n7", rules))


# ─── Инвариант: оценивание не изменилось ────────────────────────────────────


@pytest.mark.parametrize(
    "value, correct, score",
    [
        ("17\n9\n8", True, 1),
        ("17\n16\n8", False, 0),
        ("99\n99\n99", False, 0),
    ],
)
def test_оценивание_осталось_прежним(value: str, correct: bool, score: int):
    """Правка tsk-822 — только текст. Балл и вердикт обязаны быть теми же:
    зачёт по-прежнему только за полный ответ (решение оператора 08.09)."""
    result = _check(value, _rules(["17\n9\n8"]))
    assert result.is_correct is correct
    assert result.score == score
