# -*- coding: utf-8 -*-
"""
tsk-366: проверка табличного ответа TBL_COM + регрессия остальных типов.

Две группы тестов, и вторая важнее первой.

1. Поведение TBL_COM: разбор ответа по пробельным символам, режимы оценивания
   (all_or_nothing / partial), важность порядка рядов, ручная проверка,
   отсутствие эталона, пустой ответ.

2. **Инвариант «проверка не стала строже»**: `checking_service` — общий движок
   всей платформы, и 210 заданий уже работают на автопроверке как SA_COM с
   табличным ответом строкой. Перевод в TBL_COM обязан быть сменой ТИПА, а не
   переписыванием правил, поэтому здесь зафиксировано: всё, что засчитывал
   SA_COM на данном правиле, засчитывает и TBL_COM. Плюс прямые регрессионные
   проверки SC / MC / SA / SA_COM — они не должны измениться ни на шаг.
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


def _content(task_type: str = "TBL_COM", columns: int | None = 2) -> TaskContent:
    data: dict = {"type": task_type, "stem": "Найдите числа и результаты деления."}
    if columns is not None:
        data["table"] = {"columns": columns}
    return TaskContent.model_validate(data)


def _rules(
    accepted: list[str],
    *,
    max_score: int = 1,
    normalization: list[str] | None = None,
    scoring_mode: str = "all_or_nothing",
    row_order_matters: bool | None = None,
    manual: bool = False,
) -> SolutionRules:
    data: dict = {
        "max_score": max_score,
        "scoring_mode": scoring_mode,
        "manual_review_required": manual,
        "short_answer": {
            "normalization": normalization or ["trim", "lower"],
            "accepted_answers": [{"value": v, "score": max_score} for v in accepted],
        },
    }
    if row_order_matters is not None:
        data["table"] = {"row_order_matters": row_order_matters}
    return SolutionRules.model_validate(data)


def _answer(value: str, task_type: str = "TBL_COM") -> StudentAnswer:
    return StudentAnswer(type=task_type, response=StudentResponse(value=value))


def _check(value: str, rules: SolutionRules, content: TaskContent | None = None):
    return service.check_task(content or _content(), rules, _answer(value))


# ─── 1. Поведение TBL_COM ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "10 2786",           # ровно как эталон
        "10  2786",          # лишние пробелы между ячейками
        "  10 2786  ",       # пробелы по краям
        "10\n2786",          # ряды переводом строки
        "10\t2786",          # табуляция
        "10 \n 2786\n",      # смесь и хвостовой перевод строки
    ],
)
def test_разделители_не_надо_угадывать(value: str):
    """Ради этого тип и заведён: любой пробельный разделитель — один ответ."""
    result = _check(value, _rules(["10 2786"]))
    assert result.is_correct is True
    assert result.score == 1


def test_неверные_значения_не_засчитываются():
    result = _check("10 2787", _rules(["10 2786"]))
    assert result.is_correct is False
    assert result.score == 0


def test_лишняя_ячейка_не_засчитывается():
    result = _check("10 2786 5", _rules(["10 2786"]))
    assert result.is_correct is False


def test_многострочная_таблица():
    эталон = "13476875 21563\n13480625 21569\n13485625 21577"
    result = _check("13476875 21563 13480625 21569 13485625 21577", _rules([эталон]))
    assert result.is_correct is True


def test_порядок_рядов_важен_по_умолчанию():
    rules = _rules(["1 2\n3 4"])
    assert _check("3 4 1 2", rules).is_correct is False
    assert _check("1 2 3 4", rules).is_correct is True


def test_порядок_рядов_можно_выключить():
    rules = _rules(["1 2\n3 4"], row_order_matters=False)
    assert _check("3 4 1 2", rules).is_correct is True
    # Ячейки ВНУТРИ ряда остаются упорядоченными: столбцы разные по смыслу.
    assert _check("2 1 4 3", rules).is_correct is False


def test_порядок_рядов_выключен_но_кратность_повторов_значима():
    rules = _rules(["1 2\n1 2"], row_order_matters=False)
    assert _check("1 2 1 2", rules).is_correct is True
    assert _check("1 2", rules).is_correct is False


def test_частичный_балл_по_рядам():
    rules = _rules(["1 2\n3 4\n5 6"], max_score=3, scoring_mode="partial")
    result = _check("1 2 3 4 9 9", rules)
    assert result.score == 2
    assert result.is_correct is False


def test_частичный_балл_полное_совпадение_даёт_максимум():
    rules = _rules(["1 2\n3 4\n5 6"], max_score=3, scoring_mode="partial")
    result = _check("1 2 3 4 5 6", rules)
    assert result.score == 3
    assert result.is_correct is True


def test_all_or_nothing_не_даёт_частичного_балла():
    rules = _rules(["1 2\n3 4\n5 6"], max_score=3)
    assert _check("1 2 3 4 9 9", rules).score == 0


def test_обязательная_ручная_проверка_не_выносит_вердикт():
    """Паритет с SA_COM (tsk-230): вердикт ставит преподаватель."""
    result = _check("10 2786", _rules(["10 2786"], manual=True))
    assert result.is_correct is None
    assert result.score == 0


def test_без_эталона_уходит_в_ручную_проверку():
    rules = SolutionRules.model_validate({"max_score": 1})
    result = _check("10 2786", rules)
    assert result.is_correct is None


def test_пустой_ответ_неверен_и_объясняет_причину():
    result = _check("   ", _rules(["10 2786"]))
    assert result.is_correct is False
    assert result.feedback is not None
    assert "пуст" in (result.feedback.general or "").lower()


def test_подсказка_про_неполный_ряд():
    """Ученик должен отличать ошибку в счёте от ошибки ввода."""
    result = _check("10 2786 12", _rules(["10 2786\n12 3140"]))
    assert result.is_correct is False
    assert "ряды заполнены не до конца" in (result.feedback.general or "")


def test_совпавший_эталон_возвращается_для_показа_таблицей():
    result = _check("10 2786", _rules(["10 2786"]))
    assert result.details is not None
    assert result.details.matched_short_answer == "10 2786"


def test_несколько_эталонов_берётся_подходящий():
    rules = _rules(["1 2", "3 4"])
    assert _check("3 4", rules).is_correct is True


def test_раскладка_столбцов_по_умолчанию_если_блока_нет():
    """Отсутствие task_content.table не должно ломать проверку."""
    result = _check("10 2786", _rules(["10 2786"]), content=_content(columns=None))
    assert result.is_correct is True


def test_нормализация_применяется_к_каждой_ячейке():
    rules = _rules(["10 abc"], normalization=["trim", "lower"])
    assert _check("10 ABC", rules).is_correct is True


def test_пунктуация_между_ячейками_не_сдвигает_таблицу():
    """При strip_punctuation одинокая запятая дала бы пустую ячейку."""
    rules = _rules(["1 2"], normalization=["trim", "lower", "strip_punctuation"])
    assert _check("1 , 2", rules).is_correct is True


def test_несовпадение_типа_ответа_и_задачи_отклоняется():
    from app.utils.exceptions import DomainError

    with pytest.raises(DomainError):
        service.check_task(_content(), _rules(["1 2"]), _answer("1 2", task_type="SA"))


# ─── 2. Инвариант: TBL_COM не строже SA_COM на том же правиле ───────────────

# Реальные эталоны с прода (tsk-366, выборка из 263 помеченных заданий).
РЕАЛЬНЫЕ_ЭТАЛОНЫ = [
    "10 2786",
    "98 20",
    "30 4138",
    "416 1390",
    "1113840 1179360 1208844 1499400",
    "41818182 261959 5 271 57500001",
    "13476875 21563 13480625 21569 13485625 21577 13491875 21587 13493125 21589",
    "100000005 33333335 100000021 9090911 100000029 33333343",
]

ВАРИАНТЫ_ОТВЕТА = [
    lambda v: v,
    lambda v: f"  {v}  ",
    lambda v: v.upper(),
    lambda v: v.replace(" ", "  "),
    lambda v: v.replace(" ", "\n"),
]


@pytest.mark.parametrize("эталон", РЕАЛЬНЫЕ_ЭТАЛОНЫ)
@pytest.mark.parametrize("нормализация", [
    ["trim", "lower"],
    ["trim", "lower", "strip_punctuation", "collapse_spaces"],
])
def test_инвариант_tbl_com_засчитывает_всё_что_засчитывал_sa_com(
    эталон: str, нормализация: list[str]
):
    """
    Миграция 210 работающих заданий — смена типа без правки правил. Значит,
    ни один ответ, который засчитывался как SA_COM, не должен перестать
    засчитываться как TBL_COM.
    """
    rules = _rules([эталон], normalization=нормализация)
    sa_content = _content("SA_COM", columns=None)
    tbl_content = _content("TBL_COM")

    for мутация in ВАРИАНТЫ_ОТВЕТА:
        ответ = мутация(эталон)
        sa = service.check_task(sa_content, rules, _answer(ответ, "SA_COM"))
        tbl = service.check_task(tbl_content, rules, _answer(ответ, "TBL_COM"))
        if sa.is_correct is True:
            assert tbl.is_correct is True, (
                f"SA_COM засчитал {ответ!r}, а TBL_COM — нет (эталон {эталон!r})"
            )


@pytest.mark.parametrize("эталон", РЕАЛЬНЫЕ_ЭТАЛОНЫ)
def test_инвариант_неверный_ответ_остаётся_неверным(эталон: str):
    """Расширение не должно превратиться в «засчитываем что угодно»."""
    rules = _rules([эталон], normalization=["trim", "lower"])
    испорченный = эталон.replace(эталон.split()[0], "999999999", 1)
    assert _check(испорченный, rules).is_correct is False


# ─── 3. Регрессия остальных типов (движок общий для всей платформы) ─────────


def test_регрессия_sa_com_табличный_ответ_строкой_по_прежнему_верен():
    content = TaskContent.model_validate({"type": "SA_COM", "stem": "?"})
    rules = _rules(["10 2786"])
    result = service.check_task(content, rules, _answer("10 2786", "SA_COM"))
    assert result.is_correct is True
    assert result.score == 1


def test_регрессия_sa_com_лишний_пробел_по_прежнему_не_засчитан():
    """SA_COM остаётся строгим — именно эта строгость и была больно ученику."""
    content = TaskContent.model_validate({"type": "SA_COM", "stem": "?"})
    rules = _rules(["10 2786"], normalization=["trim", "lower"])
    result = service.check_task(content, rules, _answer("10  2786", "SA_COM"))
    assert result.is_correct is False


def test_регрессия_sa_короткий_ответ():
    content = TaskContent.model_validate({"type": "SA", "stem": "?"})
    rules = _rules(["42"])
    assert service.check_task(content, rules, _answer("42", "SA")).is_correct is True
    assert service.check_task(content, rules, _answer("43", "SA")).is_correct is False


def test_регрессия_sc():
    content = TaskContent.model_validate({
        "type": "SC",
        "stem": "?",
        "options": [{"id": "A", "text": "раз"}, {"id": "B", "text": "два"}],
    })
    rules = SolutionRules.model_validate({"max_score": 1, "correct_options": ["A"]})
    ok = StudentAnswer(type="SC", response=StudentResponse(selected_option_ids=["A"]))
    bad = StudentAnswer(type="SC", response=StudentResponse(selected_option_ids=["B"]))
    assert service.check_task(content, rules, ok).is_correct is True
    assert service.check_task(content, rules, bad).is_correct is False


def test_регрессия_mc_частичный_балл():
    content = TaskContent.model_validate({
        "type": "MC",
        "stem": "?",
        "options": [
            {"id": "A", "text": "раз"},
            {"id": "B", "text": "два"},
            {"id": "C", "text": "три"},
        ],
    })
    rules = SolutionRules.model_validate({
        "max_score": 10,
        "scoring_mode": "partial",
        "correct_options": ["A", "B"],
    })
    half = StudentAnswer(type="MC", response=StudentResponse(selected_option_ids=["A"]))
    full = StudentAnswer(
        type="MC", response=StudentResponse(selected_option_ids=["A", "B"])
    )
    assert service.check_task(content, rules, half).score == 5
    assert service.check_task(content, rules, full).is_correct is True


def test_регрессия_ta_уходит_в_ручную():
    content = TaskContent.model_validate({"type": "TA", "stem": "?"})
    rules = SolutionRules.model_validate({"max_score": 10})
    answer = StudentAnswer(type="TA", response=StudentResponse(text="ответ"))
    assert service.check_task(content, rules, answer).is_correct is None


# ─── 4. Схема ───────────────────────────────────────────────────────────────


def test_схема_подписи_столбцов_должны_совпадать_с_числом():
    with pytest.raises(ValueError):
        TaskContent.model_validate({
            "type": "TBL_COM",
            "stem": "?",
            "table": {"columns": 2, "column_titles": ["число"]},
        })


def test_схема_подписи_столбцов_валидны_при_совпадении():
    content = TaskContent.model_validate({
        "type": "TBL_COM",
        "stem": "?",
        "table": {"columns": 2, "column_titles": ["число", "частное"]},
    })
    assert content.table is not None
    assert content.table.column_titles == ["число", "частное"]
