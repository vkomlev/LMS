"""tsk-1259: сессия 104, задание 6503 — полное решение «45 + 7 − 3 = 49» прошло стража.

Стенд 14.10 (scripts/tutor_replay_bakeoff.py) создавал стража без эталонов, а
расчёт идёт на производных числах (45, 7, 3), которых нет в условии. Поймать
такую реплику можно только по эталону — значит, стенд обязан его передавать.
"""
from __future__ import annotations

import inspect

from app.services.ai_tutor.answer_guard import TutorStreamGuard

STEM = (
    "Определите количество натуральных двузначных чисел x, для которых ложно "
    "логическое высказывание: НЕ (x чётное) И НЕ (x кратно 13). Ответ запишите числом."
)
REPLY = (
    "Давай проверим вместе.\n"
    "Чётных двузначных: 10, 12, …, 98 — это 45 чисел.\n"
    "Кратных 13 среди двузначных: 13, 26, …, 91 — это 7 чисел.\n"
    "Чётных и кратных 13 одновременно, то есть кратных 26: 26, 52, 78 — 3 числа.\n"
    "Считаем объединение: 45 + 7 − 3 = 49.\n"
    "Так что у тебя выходит 49, а не 50."
)


def _run(guard: TutorStreamGuard, text: str, step: int = 3) -> str:
    shown = "".join(guard.feed(text[i:i + step]) for i in range(0, len(text), step))
    return shown + guard.finish()


def test_session104_reply_blocked_with_answer() -> None:
    guard = TutorStreamGuard(mode="concept", stem=STEM, answers=["49"])
    shown = _run(guard, REPLY)
    assert guard.blocked and guard.hit is not None
    assert "49" not in shown.replace("Давай", "")


def test_bakeoff_passes_answers_to_guard() -> None:
    from scripts import tutor_replay_bakeoff

    src = inspect.getsource(tutor_replay_bakeoff.run_unit)
    assert "answers=unit.answers" in src
