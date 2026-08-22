# -*- coding: utf-8 -*-
"""tsk-636: задание без эталона не выносит авто-НЕЗАЧЁТ, а уходит к преподавателю.

Дефект. «Эталона нет» у SA/SA_COM определялось как `short_answer is None`. Но пустое
правило живёт в трёх формах (SQL NULL, JSON-null, объект-но-пустой), и третью это
условие не ловило: модель правил с пустым `accepted_answers` — обычный объект, то есть
истинный. Ответ проваливался в сравнение с пустым списком и получал `is_correct=False`.
Ученик отвечал верно, а система говорила «неверно», и узнать об этом было неоткуда —
ни ошибки, ни лога (тот же класс, что ловит `scripts/check_ungradable_tasks.py`, tsk-361).

Тот же вопрос «эталон есть?» уже задаёт предикат `SolutionRules.has_reference_answer()` —
им пользуются `_check_table_answer` (TBL_COM) и UX-сигнал клиенту (tsk-547). Правка
приводит SA/SA_COM к тому же предикату.

Тесты идут в ОБЕ стороны намеренно. Незаслуженный незачёт человек оспорит, а ложный
зачёт не заметит никто, поэтому мимо «эталон пуст → к преподавателю» здесь проверяется
и то, что на заданиях с эталоном ничего не смягчилось: неверный ответ остаётся неверным,
а балл в ручной ветке остаётся нулевым (иначе PASS-гейт движка зачёл бы задание).
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

from app.schemas.checking import StudentAnswer  # noqa: E402
from app.schemas.solution_rules import SolutionRules  # noqa: E402
from app.schemas.task_content import TaskContent  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402


SERVICE = CheckingService()

TASK_TYPES = ["SA", "SA_COM"]


def _task(task_type: str) -> TaskContent:
    return TaskContent.model_validate({"type": task_type, "stem": "Вопрос"})


def _answer(task_type: str, value: str) -> StudentAnswer:
    return StudentAnswer.model_validate({"type": task_type, "response": {"value": value}})


def _rules(**short_answer) -> SolutionRules:
    """Правило с блоком `short_answer` — именно та форма, что не ловилась."""
    payload = {
        "max_score": 1,
        "short_answer": {
            "normalization": ["trim", "lower"],
            "accepted_answers": [],
            "use_regex": False,
            "regex": None,
            **short_answer,
        },
    }
    return SolutionRules.model_validate(payload)


# ---------- Пустой эталон: авто-вердикта быть не должно ----------

@pytest.mark.parametrize("task_type", TASK_TYPES)
def test_пустой_список_эталонов_уводит_в_ручную_проверку(task_type: str):
    """Блок правил есть, ответов в нём нет — «сверять нечем», а не «неверно»."""
    result = SERVICE.check_task(_task(task_type), _rules(), _answer(task_type, "11110"))

    assert result.is_correct is None, "авто-незачёт на задании без эталона"
    assert result.score == 0, "балл в ручной ветке обязан быть нулевым"


@pytest.mark.parametrize("task_type", TASK_TYPES)
def test_regex_выключен_но_задан_это_тоже_отсутствие_эталона(task_type: str):
    """`regex` без `use_regex` не эталон: сравнивать по нему движок не будет."""
    result = SERVICE.check_task(
        _task(task_type),
        _rules(use_regex=False, regex=r"^\d+$"),
        _answer(task_type, "11110"),
    )

    assert result.is_correct is None
    assert result.score == 0


@pytest.mark.parametrize("task_type", TASK_TYPES)
def test_блока_правил_нет_вовсе_поведение_прежнее(task_type: str):
    """Форма «short_answer отсутствует» работала и раньше — не сломать её."""
    rules = SolutionRules.model_validate({"max_score": 1})

    result = SERVICE.check_task(_task(task_type), rules, _answer(task_type, "11110"))

    assert result.is_correct is None
    assert result.score == 0


def test_пустое_правило_не_даёт_прохождения_задания():
    """Ложного зачёта правка дать не может: PASS-гейт движка считает по баллу.

    `is_correct` меняется False → None, но `score` как был 0, так и остался, а
    прохождение задания движок гейтит по `score / max_score >= 0.5`.
    """
    result = SERVICE.check_task(_task("SA_COM"), _rules(), _answer("SA_COM", "что угодно"))

    assert result.score / result.max_score < 0.5


# ---------- Эталон есть: ничего не смягчилось ----------

@pytest.mark.parametrize("task_type", TASK_TYPES)
def test_верный_ответ_по_прежнему_зачёт(task_type: str):
    rules = _rules(accepted_answers=[{"value": "11110", "score": 1}])

    result = SERVICE.check_task(_task(task_type), rules, _answer(task_type, " 11110 "))

    assert result.is_correct is True
    assert result.score == 1


@pytest.mark.parametrize("task_type", TASK_TYPES)
def test_неверный_ответ_по_прежнему_незачёт(task_type: str):
    """Главная защита от ошибки в обратную сторону."""
    rules = _rules(accepted_answers=[{"value": "11110", "score": 1}])

    result = SERVICE.check_task(_task(task_type), rules, _answer(task_type, "111100"))

    assert result.is_correct is False
    assert result.score == 0


@pytest.mark.parametrize("task_type", TASK_TYPES)
def test_эталон_только_regex_проверяется_как_раньше(task_type: str):
    """`use_regex=true` + `regex` — полноценный эталон, ветка ручной проверки не при чём."""
    rules = _rules(use_regex=True, regex=r"^\d+$")

    good = SERVICE.check_task(_task(task_type), rules, _answer(task_type, "11110"))
    bad = SERVICE.check_task(_task(task_type), rules, _answer(task_type, "не число"))

    assert good.is_correct is True
    assert bad.is_correct is False


@pytest.mark.parametrize("task_type", TASK_TYPES)
def test_пустой_ответ_на_задании_с_эталоном_остаётся_незачётом(task_type: str):
    """Пустой ответ — это ответ ученика, а не отсутствие эталона: вердикт False."""
    rules = _rules(accepted_answers=[{"value": "11110", "score": 1}])

    result = SERVICE.check_task(_task(task_type), rules, _answer(task_type, "   "))

    assert result.is_correct is False


@pytest.mark.parametrize("task_type", TASK_TYPES)
def test_обязательная_ручная_проверка_имеет_приоритет(task_type: str):
    """`manual_review_required` разбирается ДО вопроса об эталоне — порядок не сдвинулся."""
    rules = SolutionRules.model_validate(
        {
            "max_score": 1,
            "manual_review_required": True,
            "short_answer": {
                "normalization": ["trim", "lower"],
                "accepted_answers": [{"value": "11110", "score": 1}],
            },
        }
    )

    result = SERVICE.check_task(_task(task_type), rules, _answer(task_type, "11110"))

    assert result.is_correct is None
    assert result.score == 0


def test_гибридный_режим_не_задет():
    """tsk-396: у `partial_auto_check` эталон обязателен схемой, ветка не меняется."""
    rules = SolutionRules.model_validate(
        {
            "max_score": 1,
            "manual_review_required": True,
            "partial_auto_check": True,
            "short_answer": {
                "normalization": ["trim", "lower"],
                "accepted_answers": [{"value": "11110", "score": 1}],
            },
        }
    )

    сошлось = SERVICE.check_task(_task("SA_COM"), rules, _answer("SA_COM", "11110"))
    не_сошлось = SERVICE.check_task(_task("SA_COM"), rules, _answer("SA_COM", "111100"))

    assert сошлось.is_correct is None and сошлось.score == 0
    assert не_сошлось.is_correct is False and не_сошлось.score == 0


# ---------- Работы из разбора tsk-636 ----------

@pytest.mark.parametrize(
    "answer, accepted, normalization, ожидание",
    [
        # res 15429, задание 9501 (crylov:v5t4) — точное совпадение
        ("11110", ["11110"], ["trim", "lower"], True),
        # res 15171, задание 5863 — регистр снимается шагом lower
        ("Алфавитом", ["алфавитом"], ["trim", "lower", "strip_punctuation", "collapse_spaces"], True),
        # res 2316, задание 6343 — точка в начале снимается strip_punctuation
        (".env", [".env"], ["trim", "lower", "strip_punctuation", "collapse_spaces"], True),
        # res 2012, задание 7401 — ответ входит в список синонимов
        ("диск", ["накопитель", "диск", "HDD"], ["trim", "lower", "strip_punctuation"], True),
        # обратная сторона: похожий, но другой ответ зачитываться не должен
        ("111100", ["11110"], ["trim", "lower"], False),
        ("алфавит", ["алфавитом"], ["trim", "lower", "strip_punctuation"], False),
    ],
)
def test_работы_из_разбора_нынешними_правилами_судятся_верно(
    answer: str, accepted: list, normalization: list, ожидание: bool
):
    """Закрепляет вывод разбора: сравнение с эталоном исправно.

    Эти десять работ получили «не зачёт» потому, что эталон задания правили уже
    ПОСЛЕ сдачи, — а не потому, что движок ошибся. Тест держит вторую половину
    утверждения: на нынешних правилах те же ответы судятся так, как ожидает человек.
    """
    rules = _rules(
        normalization=normalization,
        accepted_answers=[{"value": v, "score": 1} for v in accepted],
    )

    result = SERVICE.check_task(_task("SA_COM"), rules, _answer("SA_COM", answer))

    assert result.is_correct is ожидание
