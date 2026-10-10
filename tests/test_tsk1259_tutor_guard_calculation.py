"""tsk-1259: страж не пускает пошаговый расчёт до итогового ответа.

Образцы — НАСТОЯЩИЕ реплики с боевой базы, 10.10, ученик 4628, отвечала
`anthropic/claude-sonnet-4.6` (запасная модель цепочки). Кода в них нет: модель
решила задание словами и арифметикой, и страж tsk-748, судивший только код,
пропустил ответ. Сессия 219 — задание 63 (эталон «29 47»), сессия 218 —
задание 58 (эталон «10 164»).
"""
from __future__ import annotations

import pytest

from app.services.ai_tutor.answer_guard import (
    BLOCKED_NOTICE,
    TutorStreamGuard,
    judge_text_line,
    leaks_answer,
    _answer_tokens,
    _stem_numbers,
)

STEM_63 = (
    "Напишите программу, которая запрашивает у пользователя цену за\n"
    "килограмм фруктов в рублях (дробное число) и вес покупки в граммах\n"
    "(целое). Посчитайте, сколько стоит покупка, и выведите стоимость в\n"
    "виде двух целых чисел через пробел: рубли и копейки.\n\n"
    "Запустите программу с вводом:\n```\n84.20\n350\n```\n"
    "Введите два числа через пробел в поле «Ответ»."
)

STEM_58 = (
    "Напишите программу, которая запрашивает у пользователя скорость `v`\n"
    "(м/с, целое) и время `t` (секунды, целое), считает пройденную дистанцию\n"
    "и выводит через пробел количество полных кругов и расстояние от старта\n"
    "на дорожке стадиона длиной 400 м.\n\n"
    "Запустите программу с вводом:\n```\n12\n347\n```\n"
    "Введите два числа из вывода через пробел в поле «Ответ»."
)

# Реплика 1031 сессии 219 (до места, где страж tsk-748 вырезал программу).
MSG_1031 = """Отлично, теперь всё ясно! Давай разберём задачу по шагам.

## Что нужно сделать:

1. **Цена**: 84.20 руб/кг
2. **Вес**: 350 грамм
3. **Проблема**: цена за **килограмм**, а вес в **граммах** — нужно привести к одним единицам
4. **Результат**: вывести рубли и копейки отдельно

## Решение:

**Шаг 1:** Переводим граммы в килограммы
- 350 грамм = 350 / 1000 = 0.35 кг

**Шаг 2:** Умножаем цену на вес
- 84.20 × 0.35 = 29.47 рублей

**Шаг 3:** Разделяем на рубли и копейки
- Рубли: 29
- Копейки: 47

## Ответ:
```
29 47
```
"""

# Реплика 1025 сессии 218.
MSG_1025 = """Вижу проблему! Ты правильно понял логику, но есть важная ошибка в расчёте **расстояния от старта**.

**Разберём по шагам:**

1. **Расстояние:** s = v × t = 12 × 347 = **4164 м** ✅
2. **Количество полных кругов:** 4164 ÷ 400 = 10 (целая часть) ✅
3. **Расстояние от старта:** Здесь ошибка! ❌

**Как найти остаток:**
- **Вариант 1:** 4164 - (10 × 400) = 4164 - 4000 = **164 м**
- **Вариант 2:** Остаток от деления: 4164 % 400 = **164 м** (оператор `%` в программировании)

**Правильный ответ:**
"""

# Реплика 1023 сессии 218: допустимая (вопросы, без расчёта). Не должна резаться.
MSG_1023 = """Отлично, ты движешься в правильном направлении! Давай проверим твою логику пошагово:

**Чтобы я мог помочь точнее, уточни:**

- **Какую формулу ты использовал для расстояния?** (Например, S = v × t или другую?)
- **Как ты нашёл количество кругов?** (Делил расстояние на длину одного круга?)

Жду твоих расчётов!"""


def _stream(guard: TutorStreamGuard, text: str, step: int) -> str:
    """Прогнать реплику кусками по `step` символов, как её режет сеть."""
    out = "".join(guard.feed(text[i:i + step]) for i in range(0, len(text), step))
    return out + guard.finish()


@pytest.mark.parametrize("step", [1, 3, 7, 40, 10_000])
@pytest.mark.parametrize("answers", [["29 47"], []])
def test_session_219_answer_never_reaches_student(step: int, answers: list[str]) -> None:
    """Задание 63: ни «29.47», ни «29 47» не уходят — с эталоном и без него."""
    guard = TutorStreamGuard(mode="debug", stem=STEM_63, answers=answers)
    shown = _stream(guard, MSG_1031, step)
    assert guard.blocked
    assert "29.47" not in shown and "29 47" not in shown
    assert "Копейки: 47" not in shown
    assert shown.endswith(BLOCKED_NOTICE)
    # Начало разговора не съедено: страж режет с места расчёта.
    assert shown.startswith("Отлично, теперь всё ясно!")


@pytest.mark.parametrize("step", [1, 5, 40, 10_000])
@pytest.mark.parametrize("answers", [["10 164"], []])
def test_session_218_answer_never_reaches_student(step: int, answers: list[str]) -> None:
    """Задание 58: «164» (остаток) не уходит ученику ни в каком виде."""
    guard = TutorStreamGuard(mode="debug", stem=STEM_58, answers=answers)
    shown = _stream(guard, MSG_1025, step)
    assert guard.blocked
    assert "164" not in shown.replace("4164", "")
    assert "4164" not in shown
    assert shown.startswith("Вижу проблему!")


@pytest.mark.parametrize("step", [1, 7, 10_000])
def test_questions_without_calculation_pass_untouched(step: int) -> None:
    """Наводящие вопросы с формулой без чисел задания — законны."""
    guard = TutorStreamGuard(mode="debug", stem=STEM_58, answers=["10 164"])
    assert _stream(guard, MSG_1023, step) == MSG_1023
    assert not guard.blocked


def test_calculation_on_foreign_numbers_is_allowed() -> None:
    """Пример на посторонних числах (методика разрешает) не режется."""
    nums = _stem_numbers(STEM_63)
    assert judge_text_line("- 50 × 3 = 150 руб.", stem_numbers=nums) is None
    assert judge_text_line("2 + 2 = 4", stem_numbers=nums) is None


def test_calculation_on_stem_numbers_is_blocked() -> None:
    nums = _stem_numbers(STEM_63)
    assert judge_text_line("- 84.20 × 0.35 = 29.47 рублей", stem_numbers=nums)
    assert judge_text_line("350 / 1000 = 0.35", stem_numbers=nums)


def test_answer_heading_is_blocked_but_quote_of_student_is_not() -> None:
    assert judge_text_line("## Ответ:", stem_numbers=set())
    assert judge_text_line("**Правильный ответ:** 29 47", stem_numbers=set())
    assert judge_text_line("Твой ответ: 29470 0 — давай разберём", stem_numbers=set()) is None
    assert judge_text_line("Ответь мне: что делает %?", stem_numbers=set()) is None


def test_answer_numbers_split_across_lines_are_caught() -> None:
    """Эталон «10 164», разнесённый по разным строкам разбора, — всё равно ответ."""
    tokens = _answer_tokens(["10 164"])
    assert leaks_answer("кругов: 10\n…\nостаток: **164** м", tokens)
    assert not leaks_answer("кругов: 10, а остаток посчитай сам", tokens)
    assert not leaks_answer("1164 и 2010", tokens)


def test_single_digit_answer_is_not_a_fingerprint() -> None:
    """Ответ «5» встречается в любом объяснении — отпечатком его не считаем."""
    assert _answer_tokens(["5"]) == []
