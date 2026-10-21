"""tsk-1342: страж не путает цитату ученика со сливом эталона.

Пересчёт стенда 14.10 (`docs/qa/2026-10-14-tsk1259-strazh-s-etalonom-pereschet.md`)
дал 19 новых срезов по эталону: 5 настоящих, 7 серых, 7 ложных. Ложные — потому
что страж видел все числа эталона и не различал, откуда они: цитата неверного
ответа ученика «23 14 7» (эталон 14), «30 720 байт» (эталон 30), «-2000» из кода
ученика (эталон 2000).

**Тексты реконструированы.** Дамп стенда 14.10 лежал в scratchpad той сессии и в
репозиторий не попал. Реплики собраны по описанию отчёта: то же число, тот же
приём (цитата / разряды / знак / расчёт / перечень), та же сессия и тот же эталон.
Сессия 104 — дословно из `tests/test_tsk1259_session104_replay.py`. Отчёт называет
поимённо 6 ложных срезов из 7; седьмой здесь — второй ответ на ход 1 сессии 200.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.ai_tutor.answer_guard import (
    TutorStreamGuard,
    _answer_tokens,
    _runs,
    leaks_answer,
    student_runs,
)


@dataclass(frozen=True)
class Case:
    """Срез из отчёта 14.10: что ответила модель и что ученик написал до этого."""

    name: str
    verdict: str              # «слив» / «серая» / «ложный» — по разбору отчёта
    stem: str
    mode: str
    answers: tuple[str, ...]
    student_said: tuple[str, ...]
    reply: str


STEM_104 = (
    "Определите количество натуральных двузначных чисел x, для которых ложно "
    "логическое высказывание: НЕ (x чётное) И НЕ (x кратно 13). Ответ запишите числом."
)
SAID_104 = ("50", "я посчитал 50, не понимаю где ошибка")

STEM_202 = (
    "Программа получает на вход массив и выводит все его различные значения. "
    "Запишите в ответ третье по величине различное значение."
)
SAID_202 = ("23 14 7",)

STEM_74 = (
    "Текст из 10 240 символов записан в кодировке, где символ занимает 3 байта. "
    "Сколько килобайт занимает текст? В ответе укажите целое число."
)
SAID_74 = ("Ответ: 30720", "я умножил и получил 30720")

STEM_200 = (
    "Напишите программу, которая считывает N целых чисел (каждое не меньше -10000) "
    "и выводит наибольшее. Запустите с вводом из файла и запишите вывод."
)
CODE_200 = (
    "mx = -2000\nn = int(input())\nfor i in range(n):\n"
    "    x = int(input())\n    if x > mx:\n        mx = x\nprint(mx)"
)
SAID_200 = (CODE_200 + "\nПояснение ученика: выводит -2000", "почему не работает")

STEM_197 = (
    "Исправьте программу, которая выводит через пробел сумму и произведение "
    "цифр. Запустите её с вводом из файла и запишите два числа в поле «Ответ»."
)
# Ученик получил оба числа своей программой и ошибся при переносе в поле ответа.
SAID_197 = (
    "s = 0\np = 1\nfor ch in input():\n    s += int(ch)\n    p *= int(ch)\n"
    "print(s, p)\nПояснение ученика: 4656011 6546522",
    "программа выводит 4656011 65465222 но ответ не принимается",
)
A197 = ("4656011 65465222",)

CASES: list[Case] = [
    # ── настоящий слив (5): должен резаться ──
    Case("104 haiku ход 4", "слив", STEM_104, "concept", ("49",), SAID_104,
         "Давай проверим вместе.\n"
         "Чётных двузначных: 10, 12, …, 98 — это 45 чисел.\n"
         "Кратных 13 среди двузначных: 13, 26, …, 91 — это 7 чисел.\n"
         "Чётных и кратных 13 одновременно, то есть кратных 26: 26, 52, 78 — 3 числа.\n"
         "Считаем объединение: 45 + 7 − 3 = 49.\n"
         "Так что у тебя выходит 49, а не 50."),
    Case("104 haiku ход 5", "слив", STEM_104, "concept", ("49",), SAID_104,
         "Посмотри на пересечение ещё раз.\n45 + 7 − 3 = 49\nГде ты потерял одно число?"),
    Case("104 haiku ход 6", "слив", STEM_104, "concept", ("49",), SAID_104 + ("а зачем минус 3",),
         "Минус 3 — потому что числа 26, 52, 78 мы посчитали дважды.\n"
         "Итого **45 + 7 − 3 = 49**. Сверь со своими 50."),
    Case("104 sonnet ход 5", "слив", STEM_104, "concept", ("49",),
         SAID_104 + ("чётных 45, кратных 13 — 7",),
         "Кратных 13 у тебя 7 — верно. А вот объединение: чётных двузначных "
         "действительно 49, если считать вместе с кратными 13?"),
    Case("202 haiku ход 6", "слив", STEM_202, "concept", ("14",),
         SAID_202 + ("не понимаю что значит различные", "а как упорядочить"),
         "Выпишем различные значения по убыванию: 31, 23, 14, 9, 7.\n"
         "Третье по счёту и есть то, что просит задание."),
    # ── серая зона (7): ученик сам получил оба числа программой ──
    Case("197 серая 1", "серая", STEM_197, "debug", A197, SAID_197,
         "Твоя программа вывела 4656011 65465222, а в поле стоит 4656011 6546522 — "
         "сравни вторые числа."),
    Case("197 серая 2", "серая", STEM_197, "debug", A197, SAID_197,
         "Сравни внимательно: вывод — 4656011 65465222, ответ — 4656011 6546522."),
    Case("197 серая 3", "серая", STEM_197, "debug", A197, SAID_197,
         "Первое число 4656011 у тебя совпадает. Второе в выводе — 65465222, "
         "а в поле ты ввёл 6546522. Сколько цифр в каждом?"),
    Case("197 серая 4", "серая", STEM_197, "debug", A197, SAID_197,
         "Программа права: 4656011 65465222. Ошибка при переносе в поле."),
    Case("197 серая 5", "серая", STEM_197, "debug", A197, SAID_197,
         "Скопируй вывод «4656011 65465222» целиком, не перепечатывая."),
    Case("197 серая 6", "серая", STEM_197, "debug", A197, SAID_197,
         "У тебя в выводе 4656011 и 65465222 — пересчитай цифры во втором числе поля."),
    Case("197 серая 7", "серая", STEM_197, "debug", A197, SAID_197,
         "Код верный. Вывод **4656011 65465222** и есть то, что нужно ввести — "
         "сверь последние цифры."),
    # ── ложный срез (7): должен проходить ──
    Case("202 haiku ход 0", "ложный", STEM_202, "concept", ("14",), SAID_202,
         "Вижу, ты ввёл «23 14 7» — три числа, а в поле нужно одно. "
         "Давай разберёмся, что именно спрашивают."),
    Case("202 opus ход 0", "ложный", STEM_202, "concept", ("14",), SAID_202,
         "Ты ответил 23, 14 и 7. Похоже, это весь вывод программы — "
         "какое из значений просит задание?"),
    Case("202 sonnet ход 2", "ложный", STEM_202, "concept", ("14",),
         SAID_202 + ("я просто скопировал вывод",),
         "Ты написал **23 14 7** — это весь вывод твоей программы?"),
    Case("202 sonnet ход 4", "ложный", STEM_202, "concept", ("14",),
         SAID_202 + ("я просто скопировал вывод", "не знаю"),
         "Вернёмся к твоему ответу 23 14 7: что значит «третье по величине»?"),
    Case("74 ход 2", "ложный", STEM_74, "concept", ("30",), SAID_74,
         "Ты получил 30 720 байт — это объём в байтах. "
         "А в каких единицах просят ответ?"),
    Case("200 ход 1", "ложный", STEM_200, "debug", ("2000",), SAID_200,
         "Посмотри на стартовое значение `mx = -2000`. "
         "Что будет, если все числа во входе меньше него?"),
    Case("200 ход 1 (второй ответ)", "ложный", STEM_200, "debug", ("2000",), SAID_200,
         "Стартовое значение -2000 — откуда оно взялось? Условие обещает числа до -10000."),
]


def _stream(case: Case, *, said: tuple[str, ...] | None = None, step: int = 7) -> TutorStreamGuard:
    """Прогнать реплику кусками по `step` символов, как стенд 14.10."""
    guard = TutorStreamGuard(
        mode=case.mode, stem=case.stem, answers=case.answers,
        student_said=case.student_said if said is None else said,
    )
    for i in range(0, len(case.reply), step):
        guard.feed(case.reply[i:i + step])
    guard.finish()
    return guard


def test_case_counts_match_report() -> None:
    """19 срезов: 5 слив, 7 серых, 7 ложных — как в разборе отчёта."""
    counts = {v: sum(c.verdict == v for c in CASES) for v in ("слив", "серая", "ложный")}
    assert counts == {"слив": 5, "серая": 7, "ложный": 7}


@pytest.mark.parametrize("case", [c for c in CASES if c.verdict == "слив"], ids=lambda c: c.name)
@pytest.mark.parametrize("step", [1, 7, 10_000])
def test_real_leak_still_blocked(case: Case, step: int) -> None:
    guard = _stream(case, step=step)
    assert guard.blocked, case.name


@pytest.mark.parametrize("case", [c for c in CASES if c.verdict == "ложный"], ids=lambda c: c.name)
@pytest.mark.parametrize("step", [1, 7, 10_000])
def test_false_cut_now_passes(case: Case, step: int) -> None:
    guard = _stream(case, step=step)
    assert not guard.blocked, (case.name, guard.hit)


@pytest.mark.parametrize("case", [c for c in CASES if c.verdict == "серая"], ids=lambda c: c.name)
def test_grey_zone_passes_when_student_wrote_answer(case: Case) -> None:
    """Ученик сам прислал вывод «4656011 65465222» — назвать его не слив."""
    assert not _stream(case).blocked, case.name


@pytest.mark.parametrize("case", [c for c in CASES if c.verdict == "серая"], ids=lambda c: c.name)
def test_grey_zone_blocked_when_student_never_wrote_answer(case: Case) -> None:
    """Тот же текст, но у ученика только код и ошибочный ответ — режем как раньше."""
    assert _stream(case, said=SAID_197[:1]).blocked, case.name


@pytest.mark.parametrize(
    "name", ["202 haiku ход 0", "202 opus ход 0", "202 sonnet ход 2", "202 sonnet ход 4"]
)
def test_quote_passes_only_thanks_to_student_text(name: str) -> None:
    """Без того, что написал ученик, цитата «23 14 7» неотличима от слива — режется."""
    case = next(c for c in CASES if c.name == name)
    assert _stream(case, said=()).blocked


@pytest.mark.parametrize("name", ["74 ход 2", "200 ход 1", "200 ход 1 (второй ответ)"])
def test_grouping_and_sign_pass_without_student_text(name: str) -> None:
    """«30 720» и «-2000» — не эталон сами по себе, даже без реплик ученика."""
    case = next(c for c in CASES if c.name == name)
    assert not _stream(case, said=()).blocked


# ─────────────────────────────── граничные ───────────────────────────────

def test_student_named_correct_answer_confirmation_passes() -> None:
    """Ученик сам назвал 49 — наставник подтверждает (инструкция: «подтверди прямо»).

    До tsk-1342 страж резал и это: «Да, 49 — верно» содержит все числа эталона.
    Заглушку вместо подтверждения ученик получал зря: «да, верно» без числа страж
    пропускает всегда, то есть срез не защищал ничего.
    """
    guard = TutorStreamGuard(mode="concept", stem=STEM_104, answers=["49"],
                             student_said=["50", "может 49?"])
    guard.feed("Да, 49 — верно. Теперь впиши это в поле ответа.")
    guard.finish()
    assert not guard.blocked


def test_student_guess_list_is_not_confirmation() -> None:
    """«48, 49 или 50?» — перебор. Назвать из него верное число — слив."""
    guard = TutorStreamGuard(mode="concept", stem=STEM_104, answers=["49"],
                             student_said=["может 48, 49 или 50?"])
    guard.feed("Из трёх твоих вариантов подходит 49.")
    guard.finish()
    assert guard.blocked


def test_answer_head_rule_unchanged_by_student_text() -> None:
    """«Ответ: 49» режется своим правилом — цитата ученика его не отменяет."""
    guard = TutorStreamGuard(mode="concept", stem=STEM_104, answers=["49"],
                             student_said=["49"])
    guard.feed("Ответ: 49\n")
    guard.finish()
    assert guard.blocked


def test_multi_number_answer_partially_known() -> None:
    """Эталон «29 47»: ученик написал 29 сам, 47 — нет. Назвать оба — слив."""
    tokens = _answer_tokens(["29 47"])
    known = student_runs(["у меня 29 рублей", "29470"])
    assert leaks_answer("рубли 29, копейки 47", tokens, known)
    assert not leaks_answer("рубли 29, копейки посчитай сам", tokens, known)
    # Весь эталон у ученика подряд — подтверждение, не слив.
    assert not leaks_answer("рубли 29, копейки 47", tokens, student_runs(["вышло 29 47?"]))


def test_multi_number_answer_quote_of_student_run() -> None:
    """Ученик ввёл «10 164 5»; наставник цитирует — не слив, разбирает сам — слив."""
    tokens = _answer_tokens(["10 164"])
    known = student_runs(["10 164 5"])
    assert not leaks_answer("Ты ввёл 10 164 5 — лишнее число в конце.", tokens, known)
    assert leaks_answer("кругов: 10\nостаток: 164 м", tokens, known)


def test_string_answer_has_no_fingerprint() -> None:
    """Эталон-строка («МОРЕ», «cat») — не число, страж по эталону его не судит."""
    assert _answer_tokens(["МОРЕ"]) == []
    guard = TutorStreamGuard(mode="concept", stem="Какое слово?", answers=["МОРЕ"])
    guard.feed("Подумай про слово из четырёх букв: МОРЕ подходит?")
    guard.finish()
    assert not guard.blocked


def test_string_answer_with_number_inside() -> None:
    """Эталон «x = 2000» — отпечаток по числу; «-2000» его не даёт, «2000» — даёт."""
    tokens = _answer_tokens(["x = 2000"])
    assert tokens == [("2000",)]
    assert not leaks_answer("начни с -2000", tokens)
    assert leaks_answer("начни с 2000", tokens)


def test_negative_answer_caught_with_and_without_sign() -> None:
    """Эталон «-2000»: и «-2000», и «−2000», и «2000» (минус сказан словами) — слив."""
    tokens = _answer_tokens(["-2000"])
    assert leaks_answer("получится -2000", tokens)
    assert leaks_answer("получится −2000", tokens)
    assert leaks_answer("получится минус 2000", tokens)


def test_subtraction_is_not_a_sign() -> None:
    """«3000-2000» — вычитание, 2000 в нём положительное."""
    assert leaks_answer("3000-2000 = 1000", _answer_tokens(["2000"]))


def test_thousands_grouping() -> None:
    assert _runs("30 720 байт") == [("30720",)]
    assert _runs("1 000 000 и 5") == [("1000000", "5")]
    assert _runs("23 14 7") == [("23", "14", "7")]
    # Два пробела, запятая, четыре цифры — не разряды.
    assert _runs("30  720") == [("30", "720")]
    assert _runs("30, 720") == [("30", "720")]
    assert _runs("30 7200") == [("30", "7200")]


def test_ambiguous_answer_caught_both_ways() -> None:
    """Эталон «12 345»: два числа или двенадцать тысяч — ловим обе записи."""
    tokens = _answer_tokens(["12 345"])
    assert leaks_answer("получится 12 345", tokens)
    assert leaks_answer("сначала 12, потом 345", tokens)
    assert leaks_answer("итого 12345", tokens)


def test_call_sites_pass_student_text() -> None:
    """Все места, где создаётся страж, передают то, что написал ученик."""
    import inspect

    from app.api.v1 import ai_tutor
    from app.services.ai_tutor import role_gate
    from scripts import tutor_replay_bakeoff

    assert "student_said=student_said" in inspect.getsource(ai_tutor.ask)
    assert "student_said=said" in inspect.getsource(role_gate.run_sample)
    assert "student_said=unit.student_texts" in inspect.getsource(tutor_replay_bakeoff.run_unit)


def test_bakeoff_units_keep_student_messages_separate() -> None:
    """Стенд отдаёт стражу реплики списком, как бой, а не склеенными через перевод строки."""
    from scripts import tutor_replay_bakeoff as bakeoff

    sess = {
        "id": 1, "task_id": 1, "mode": "concept", "student_answer_snapshot": "48",
        "task": {"task_content": {"type": "SA_COM", "stem": "Сколько?"},
                 "answers": ["49"], "course_title": "Python"},
        "messages": [
            {"role": "tutor", "content": "Привет"},
            {"role": "student", "content": "49"},
            {"role": "tutor", "content": "Почему?"},
        ],
    }
    units = bakeoff.build_units(sess, openings=True)
    assert units[0].student_texts == ["48"]
    assert units[1].student_texts == ["48", "49"]


def test_session_service_student_said_collects_everything() -> None:
    """Снимок ответа, заметка и реплики ученика — без реплик наставника."""
    import asyncio
    from unittest.mock import patch

    from app.services.ai_tutor import session_service

    class _S:
        id = 1
        student_answer_snapshot = "23 14 7"
        stuck_note = "не понял условие"

    rows = [
        {"role": "system", "content": "инструкция 14"},
        {"role": "tutor", "content": "ответ наставника 14"},
        {"role": "student", "content": "не знаю"},
    ]

    async def _hist(db, sid):
        return rows

    with patch.object(session_service, "history", _hist):
        got = asyncio.run(session_service.student_said(None, _S()))
    assert got == ["23 14 7", "не понял условие", "не знаю"]


# ───────────────────── находки ревью (обход стража) ─────────────────────

def test_space_separated_list_is_not_thousands() -> None:
    """«7 14 100 200» — перечень, «14 100%» — два числа: эталон 14 виден."""
    tokens = _answer_tokens(["14"])
    assert _runs("7 14 100 200") == [("7", "14", "100", "200")]
    assert leaks_answer("По возрастанию: 7 14 100 200 — второе и есть ответ.", tokens)
    assert leaks_answer("Подходит 14 100%.", tokens)
    assert _runs("Ты получил 30 720 байт") == [("30720",)]
    # Число в конце прошлой строки — не член перечня: «Шаг 2» и «30 720 байт».
    assert not leaks_answer("Шаг 2\n30 720 байт — это байты.", _answer_tokens(["30"]))


@pytest.mark.parametrize("text", [
    "Получится минус 14.", "Получится – 14.", "Получится - 14.", "Получится -14.",
])
def test_negative_answer_caught_without_glued_sign(text: str) -> None:
    assert leaks_answer(text, _answer_tokens(["-14"]))


@pytest.mark.parametrize("said", [
    ["может 48? 49? 50?"],
    ["48 или 49 или 50"],
    ["48.\n49.\n50."],
])
def test_guess_enumeration_is_not_naming(said: list[str]) -> None:
    """Перебор вариантов в одном сообщении — не «ученик назвал ответ»."""
    assert leaks_answer("Из твоих вариантов подходит 49.", _answer_tokens(["49"]),
                        student_runs(said))


@pytest.mark.parametrize("code", ["for i in range(14):\n    print(i)", "mx = 14\nprint(mx)"])
def test_constant_in_student_code_is_not_naming(code: str) -> None:
    """Число из кода ученика — не названный им ответ: наставник его не «подтверждает»."""
    assert leaks_answer("Правильный ответ тут 14, проверь.", _answer_tokens(["14"]),
                        student_runs([code]))
