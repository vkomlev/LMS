# -*- coding: utf-8 -*-
"""
tsk-828: разбор неверного ответа у SA и SA_COM.

Откуда. tsk-822 научил табличные задания говорить «Совпало 3 значения из 4», а у
коротких ответов остался единственный текст «Ответ неверен. Попробуйте еще раз.» —
ученику не за что зацепиться.

Подсказки выведены из 845 незачётных работ прода, а не придуманы: ожидается число
(40 работ), эталон из нескольких чисел и прислано другое количество (39), ответ
совпадает с эталоном с точностью до записи (8). Остальным 89.7% сказать нечего —
ответ просто другой, и любая «подсказка» была бы разглашением; они получают
прежний текст.

Два правила, каждое из которых родилось на живых данных и стоило отдельной правки:

* **регистр не снимается** — иначе `print(I)` против эталона `print(i)` получал
  «дело в записи, а не в решении», хотя в Python это разные переменные (задание
  5500 прода);
* **набор значений — только числа** — иначе эталон-фраза «подключить модуль» давал
  «в ответе ожидается 2 значения» (задание 5468 прода).
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


def _content(task_type: str = "SA") -> TaskContent:
    return TaskContent.model_validate({"type": task_type, "stem": "Вопрос."})


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
    answer = StudentAnswer(type=task_type, response=StudentResponse(value=value))
    return service.check_task(_content(task_type), rules, answer)


def _general(result) -> str:
    assert result.feedback is not None
    return result.feedback.general or ""


# ─── подсказка «ожидается число» ────────────────────────────────────────────


@pytest.mark.parametrize("value", ["семь", "Я", "не знаю"])
def test_ожидается_число(value: str):
    result = _check(value, _rules(["7"]))
    assert result.is_correct is False
    assert "В ответе ожидается число" in _general(result)


def test_число_прислано_подсказки_про_число_нет():
    result = _check("8", _rules(["7"]))
    assert "ожидается число" not in _general(result)
    assert "Попробуйте еще раз" in _general(result)


# ─── подсказка «сколько значений» ───────────────────────────────────────────


def test_сколько_значений():
    """Эталон «10 164» — два числа; ученик прислал одно."""
    result = _check("10", _rules(["10 164"]))
    assert "ожидается 2 значения, а получено 1 значение" in _general(result)


def test_фраза_не_считается_набором_значений():
    """Живой случай 5468: эталон «подключить модуль» — фраза, а не два значения."""
    general = _general(_check("подключить модуль random", _rules(["подключить модуль"])))
    assert "значени" not in general
    assert "Попробуйте еще раз" in general


def test_код_не_считается_набором_значений():
    rules = _rules(['print(f"За год накопится: {za_god}")'], normalization=["trim"])
    general = _general(_check('print(f"ыв")', rules, task_type="SA_COM"))
    assert "значени" not in general


# ─── подсказка «дело в записи» ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "value, etalon",
    [
        ("123456789", "1 2 3 4 5 6 7 8 9"),  # задания 216, 230
        ("3.5", "3,5"),                      # разделитель дробной части
        ("небо скрёб", "небоскреб"),         # лишний пробел в слове + ё
    ],
)
def test_различие_только_в_записи(value: str, etalon: str):
    """Ученик решил верно и потерял балл на оформлении — говорим об этом прямо.

    Здесь остались только случаи, которые движок НЕ спасает. Часть прежних примеров
    («2 102 556 498» против «2102556498», «небоскрёб» против «небоскреб») с tsk-829
    просто засчитывается, и подсказка им больше не нужна — это не регресс теста, а
    исчезновение самой проблемы.
    """
    result = _check(value, _rules([etalon], normalization=["trim"]))
    assert result.is_correct is False  # вердикт не меняется
    assert "дело в записи ответа" in _general(result)


def test_регистр_не_объявляется_вопросом_записи():
    """Живой случай 5500: `print(I)` против `print(i)` — в Python это разные вещи.

    Если задание регистр не игнорирует, различие по регистру — настоящая ошибка,
    и списывать её на оформление нельзя.
    """
    general = _general(_check("print(I)", _rules(["print(i)"], normalization=["trim"]),
                              task_type="SA_COM"))
    assert "дело в записи" not in general
    assert "Попробуйте еще раз" in general


def test_подсказка_не_упоминает_регистр():
    """Мы регистр не снимаем — значит и советовать его проверить не должны."""
    general = _general(_check("123456789", _rules(["1 2 3 4 5 6 7 8 9"], normalization=["trim"])))
    assert "дело в записи ответа" in general
    assert "регистр" not in general


# ─── границы: ничего не разглашаем и не ломаем ──────────────────────────────


def test_эталон_не_утекает():
    result = _check("42", _rules(["сто двадцать три"]))
    assert "сто двадцать три" not in _general(result)


def test_верный_ответ_прежний_текст():
    result = _check("7", _rules(["7"]))
    assert result.is_correct is True
    assert _general(result) == "Отлично! Ваш ответ правильный."


def test_пустой_ответ_прежний_текст():
    result = _check("", _rules(["7"]))
    assert "Попробуйте еще раз" in _general(result)


def test_задание_без_эталона_не_падает():
    rules = SolutionRules.model_validate({"max_score": 1, "scoring_mode": "all_or_nothing"})
    result = _check("что-нибудь", rules)
    assert result.feedback is None or isinstance(_general(result), str)


@pytest.mark.parametrize(
    "value, etalon, correct",
    [
        ("7", "7", True),
        ("8", "7", False),
        ("семь", "7", False),
        ("небоскрёб", "небоскреб", True),  # с tsk-829 ё и е не различаются
    ],
)
def test_вердикт_и_балл_не_изменились(value: str, etalon: str, correct: bool):
    """Правка tsk-828 — только текст. Оценивание обязано остаться прежним."""
    result = _check(value, _rules([etalon], normalization=["trim"]))
    assert result.is_correct is correct
    assert result.score == (1 if correct else 0)
