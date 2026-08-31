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


# ─── 5. tsk-383: columns=1 сохраняет фразу-в-строке ──────────────────────────
#
# Мини-тесты Python «Запустите программу N раз... вывод каждого запуска с
# новой строки» — каждый запуск может выводить ФРАЗУ («Первое число больше»),
# а не одно слово. columns=1 обязан различать границу МЕЖДУ запусками (перевод
# строки) от пробела ВНУТРИ фразы одного запуска.


def _tbl1(columns: int | None = 1) -> TaskContent:
    return _content("TBL_COM", columns=columns)


def test_многострочный_ответ_с_фразами_columns_1_не_дробится_по_словам():
    эталон = "Первое число больше\nВторое число больше\nЧисла равны"
    rules = _rules([эталон])
    result = _check(эталон, rules, content=_tbl1())
    assert result.is_correct is True


def test_фраза_в_строке_неверный_порядок_отклоняется():
    эталон = "Первое число больше\nВторое число больше\nЧисла равны"
    rules = _rules([эталон])
    испорченный = "Второе число больше\nПервое число больше\nЧисла равны"
    assert _check(испорченный, rules, content=_tbl1()).is_correct is False


def test_фраза_в_строке_неверное_значение_отклоняется():
    эталон = "Первое число больше\nВторое число больше\nЧисла равны"
    rules = _rules([эталон])
    испорченный = "Первое число больше\nВторое число больше\nЧисла НЕ равны"
    assert _check(испорченный, rules, content=_tbl1()).is_correct is False


def test_фраза_в_строке_хвостовые_пробелы_и_пустые_строки_не_мешают():
    эталон = "Первое число больше\nВторое число больше\nЧисла равны"
    rules = _rules([эталон])
    мутация = "  Первое число больше  \n\nВторое число больше\nЧисла равны\n"
    assert _check(мутация, rules, content=_tbl1()).is_correct is True


def test_однострочный_ответ_columns_1_режется_по_прежнему_по_словам():
    """Обратная совместимость: 200+ уже мигрированных tsk-366 заданий с
    columns=1 хранят ответ ОДНОЙ строкой через пробел (без \\n) — эта ветка
    не должна была сдвинуться ни на шаг."""
    эталон = "True False True False True"
    rules = _rules([эталон])
    for mutated in (
        эталон,
        эталон.replace(" ", "  "),
        f"  {эталон}  ",
    ):
        assert _check(mutated, rules, content=_tbl1()).is_correct is True


def test_однострочный_ответ_columns_1_с_переводом_строки_тоже_режется_по_словам():
    """Один запуск, одна строка вывода из нескольких токенов (например,
    результат одного print с несколькими числами) — перевод строки в конце
    ответа не должен переключать в фразовый режим (после trim остаётся ОДНА
    строка)."""
    эталон = "True False True False True"
    rules = _rules([эталон])
    assert _check(f"{эталон}\n", rules, content=_tbl1()).is_correct is True


def test_много_строк_но_каждая_один_токен_работает_как_раньше():
    """Числа/слова без внутренних пробелов — фразовый режим просто выдаёт те
    же ячейки, что и старый пословный разбор."""
    эталон = "1\n12\n123\n1234"
    rules = _rules([эталон])
    assert _check("1 12 123 1234", rules, content=_tbl1()).is_correct is True
    assert _check(эталон, rules, content=_tbl1()).is_correct is True


def test_фразовый_режим_не_влияет_на_columns_больше_1():
    """Регрессия: columns>1 (существующие таблицы ЕГЭ №25/26) продолжают резать
    ЛЮБОЙ пробельный символ как границу ячейки, перевод строки в фразовом
    режиме не участвует."""
    rules = _rules(["10 2786\n12 3140"])
    assert _check("10\n2786\n12\n3140", rules, content=_content(columns=2)).is_correct is True


# ─── 6. tsk-558: слитый блок ЕГЭ 19-21 (теория игр), 4 поля (1+2+1) ──────────
#
# Курс 147: у kompege задания 20/21 жили только в subTask источника и не
# импортировались вовсе (блок выглядел как один вопрос 19); у polyakov/tg —
# все 3 вопроса были в stem, а ответ — одна склеенная строка "1) 13 2) 24 47
# 3) 46". Оператор решил: один TBL_COM columns=1 на блок, 4 независимых поля
# (19 → 1 значение, 20 → 2 значения, 21 → 1 значение), порядок полей значим
# (поле 1 — это именно ответ на 19, а не любое совпавшее число).


def test_слитый_блок_19_21_эталон_засчитывается():
    эталон = "13\n24\n47\n46"
    rules = _rules([эталон])
    assert _check(эталон, rules, content=_tbl1()).is_correct is True


def test_слитый_блок_19_21_старый_склеенный_ответ_больше_не_единственный_формат():
    """Старый формат ответа (одна строка вида "1) 13 2) 24 47 3) 46") не эталон
    новых правил — после миграции проверяется по полям, а не по фразе
    целиком."""
    rules = _rules(["13\n24\n47\n46"])
    assert _check("1) 13 2) 24 47 3) 46", rules, content=_tbl1()).is_correct is False


def test_слитый_блок_19_21_перепутанный_порядок_полей_отклоняется():
    """Поле 1 обязано быть ответом именно на 19, а не любым совпавшим числом
    из блока — порядок полей содержательно важен, row_order_matters=True по
    умолчанию."""
    эталон = "13\n24\n47\n46"
    rules = _rules([эталон])
    assert _check("24\n47\n13\n46", rules, content=_tbl1()).is_correct is False


def test_слитый_блок_19_21_один_неверный_ответ_ломает_весь_блок():
    """all_or_nothing: 3 верных поля из 4 — блок целиком неверен (согласовано
    с существующей схемой оценивания kompege/polyakov-заданий, max_score=1)."""
    эталон = "13\n24\n47\n46"
    rules = _rules([эталон])
    assert _check("13\n24\n47\n99", rules, content=_tbl1()).is_correct is False


def test_слитый_блок_19_21_пробелы_и_регистр_не_мешают():
    эталон = "21\n23\n24\n25"
    rules = _rules([эталон])
    for мутация in ("  21  \n23\n24\n25", "21\n23\n24\n25\n", "21\n23\n24\n25".upper()):
        assert _check(мутация, rules, content=_tbl1()).is_correct is True


# ─── 6. tsk-752: разбивка строк не решает судьбу зачёта ─────────────────────
#
# Объединённое задание 19-21 ученик записывает по строке НА ВОПРОС (ответ на
# 20-е — два числа в одной строке), а эталон хранится по строке НА ЗНАЧЕНИЕ.
# Значения и их порядок те же — значит зачёт. Тот же ответ ОДНОЙ строкой
# засчитывался и раньше (резался по пробелам), поэтому расхождение было
# следствием формы записи, а не знаний ученика.
#
# Граница правила: оно включается ТОЛЬКО когда каждая ячейка эталона атомарна.
# Там, где ячейка — фраза с пробелами (мини-тесты Python, tsk-383), границу
# строки по-прежнему нельзя стирать, иначе фразы склеятся в общий поток слов.


def test_ответ_по_строке_на_вопрос_засчитывается_против_эталона_по_значению():
    """Живой случай tsk-751: 4579, «Куча камней с уменьшением»."""
    rules = _rules(["244\n247\n248\n252"])
    assert _check("244\n247 248\n252", rules, content=_tbl1()).is_correct is True


def test_ответ_по_строке_на_вопрос_второй_живой_случай():
    """9518, «Куча камней с умножением» — найден замером боевых сдач."""
    rules = _rules(["51\n46\n50\n45"])
    assert _check("51\n46 50\n45", rules, content=_tbl1()).is_correct is True


def test_разбивка_строк_свободна_но_порядок_значений_по_прежнему_важен():
    rules = _rules(["244\n247\n248\n252"])
    assert _check("244\n248 247\n252", rules, content=_tbl1()).is_correct is False


def test_свободная_разбивка_не_прощает_неверное_значение():
    rules = _rules(["244\n247\n248\n252"])
    assert _check("244\n247 248\n253", rules, content=_tbl1()).is_correct is False


def test_свободная_разбивка_не_склеивает_числа_в_одно():
    """«247248» — это другое число, а не два значения без пробела."""
    rules = _rules(["244\n247\n248\n252"])
    assert _check("244\n247248\n252", rules, content=_tbl1()).is_correct is False


def test_свободная_разбивка_не_прощает_лишнее_или_недостающее_значение():
    rules = _rules(["244\n247\n248\n252"])
    assert _check("244\n247 248 249\n252", rules, content=_tbl1()).is_correct is False
    assert _check("244\n247 248", rules, content=_tbl1()).is_correct is False


def test_фразовый_эталон_не_затронут_слова_между_строками_не_переставляются():
    """Инвариант tsk-383: у фразовых ячеек граница строки значима, и слова,
    перетасованные между строками, зачётом не становятся."""
    эталон = "Первое число больше\nВторое число больше\nЧисла равны"
    rules = _rules([эталон])
    перетасованный = "Первое число\nбольше Второе число\nбольше Числа равны"
    assert _check(перетасованный, rules, content=_tbl1()).is_correct is False


def test_фразовый_эталон_обычный_верный_ответ_по_прежнему_засчитывается():
    эталон = "Первое число больше\nВторое число больше\nЧисла равны"
    rules = _rules([эталон])
    assert _check(эталон, rules, content=_tbl1()).is_correct is True


def test_свободная_разбивка_работает_и_при_нескольких_эталонах():
    """Формы, добавленные вручную в tsk-751, остаются рабочими и не мешают."""
    rules = _rules(["244\n247\n248\n252", "244\n247 248\n252"])
    for форма in ("244\n247\n248\n252", "244\n247 248\n252", "244 247 248 252"):
        assert _check(форма, rules, content=_tbl1()).is_correct is True
