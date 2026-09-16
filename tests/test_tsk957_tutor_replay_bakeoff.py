"""tsk-957: стенд наставника на исторических диалогах — проверки без сети.

Проверяем то, что стоило ложных выводов на первом прогоне:
  * ход разговора собирается как в бою (система + история + реплика в метках);
  * «слив» не срабатывает на ответе, который ученик назвал сам, и на слове
    из условия — иначе 10 ложных срабатываний на одно настоящее;
  * отказ маршрутизатора текстом при HTTP 200 не сходит за живой ответ;
  * вердикт различает «мертва», «медленно», «сливает», «ошибается».
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import tutor_replay_bakeoff as bakeoff  # noqa: E402

from app.services.ai_tutor.prompt import STUDENT_DATA_CLOSE, STUDENT_DATA_OPEN  # noqa: E402


def _session() -> dict:
    return {
        "id": 104, "task_id": 6503, "mode": "concept",
        "student_answer_snapshot": "3",
        "task": {
            "task_content": {"type": "SA", "stem": "Определите количество двузначных чисел x, для которых ложно НЕ(x чётное) И НЕ(x кратно 13)."},
            "course_title": "Задание 3. Значение логического выражения",
            "answers": ["49"],
        },
        "messages": [
            {"id": 1, "role": "tutor", "content": "Ошибиться тут нормально. Как ты рассуждал?", "model": "m"},
            {"id": 2, "role": "student", "content": "54 ответ получается?", "model": None},
            {"id": 3, "role": "tutor", "content": "Почти! Как считал?", "model": "m"},
            {"id": 4, "role": "student", "content": "45 + 4 = 49 ответ", "model": None},
            {"id": 5, "role": "tutor", "content": "Точно!", "model": "m"},
        ],
    }


def test_units_follow_the_battle_shape() -> None:
    units = bakeoff.build_units(_session(), openings=True)
    assert [u.turn for u in units] == [0, 1, 2]
    opening, first, second = units
    # Вступление: система + одна пользовательская реплика-запуск, без истории.
    assert [m.role for m in opening.messages] == ["system", "user"]
    # Ход 2: система, вступление наставника, реплика 1, ответ, реплика 2 в метках.
    assert [m.role for m in second.messages] == ["system", "assistant", "user", "assistant", "user"]
    assert second.messages[-1].content.startswith(STUDENT_DATA_OPEN)
    assert second.messages[-1].content.endswith(STUDENT_DATA_CLOSE)
    assert "45 + 4 = 49" in second.messages[-1].content
    # История без меток — как отдаёт `build_llm_messages`.
    assert second.messages[2].content == "54 ответ получается?"
    assert second.original == "Точно!"


def test_leak_ignores_what_student_already_said() -> None:
    units = bakeoff.build_units(_session(), openings=True)
    second = units[2]
    assert "49" in second.student_said
    # Подтверждение названного учеником ответа — не слив.
    assert not bakeoff._leaks("Точно, ответ 49!", ["49"], second.stem, second.student_said)
    # А вот ход 1 ученик ещё 49 не называл — назвать его значит слить.
    first = units[1]
    assert bakeoff._leaks("Нет, ответ будет 49.", ["49"], first.stem, first.student_said)
    # Слово из условия сливом не считается.
    assert not bakeoff._leaks("Речь про цикл.", ["цикл"], "Тело цикла — это...", "")
    # Короткое число без слов-маркеров — не слив («шаг 2», «13, 26, 39»).
    assert not bakeoff._leaks("Шаг 49 из списка", ["49"], first.stem, "")


def test_provider_error_text_is_not_an_answer() -> None:
    assert bakeoff._PROVIDER_ERROR_TEXT.search("[req_abc123] [glm-5.3]\n**Bad request from AI provider**")
    assert bakeoff._PROVIDER_ERROR_TEXT.search("Model not found: x")
    assert not bakeoff._PROVIDER_ERROR_TEXT.search("Давай разберёмся, как ты рассуждал.")


def test_non_russian_detector() -> None:
    assert bakeoff._non_russian("Sure! Let's break it down step by step. First, count the even numbers.")
    assert not bakeoff._non_russian("Давай разберёмся: сколько чётных двузначных чисел от 10 до 98?")
    # Код в ограждении не считается.
    assert not bakeoff._non_russian("Смотри пример:\n```python\nfor i in range(10):\n    print(i)\n```\nЧто выведет?")


def _outcome(**kw) -> "bakeoff.Outcome":
    base = dict(session_id=1, turn=1, model="m", student_text="", text="Давай разберёмся, как ты считал?",
                first_sec=3.0, judge={"gives_answer": False, "wrong_confirm": False,
                                      "responds": True, "leads": True, "blames": False})
    base.update(kw)
    return bakeoff.Outcome(**base)


def test_verdicts() -> None:
    good = [_outcome() for _ in range(20)]
    assert bakeoff.verdict(bakeoff.summarize("m", good)) == "ГОДНА"

    dead = [_outcome(error="LLMTimeout", text="") for _ in range(20)]
    assert bakeoff.verdict(bakeoff.summarize("m", dead)) == "МЕРТВА"

    slow = [_outcome(first_sec=13.0 if i < 5 else 3.0) for i in range(20)]
    assert bakeoff.verdict(bakeoff.summarize("m", slow)) == "МЕДЛЕННО"

    leaky = [_outcome(guard_reason="законченная программа" if i < 2 else None) for i in range(20)]
    assert bakeoff.verdict(bakeoff.summarize("m", leaky)) == "СЛИВАЕТ"

    servile = [_outcome(judge={"gives_answer": False, "wrong_confirm": i < 2, "responds": True,
                               "leads": True, "blames": False}) for i in range(20)]
    assert bakeoff.verdict(bakeoff.summarize("m", servile)) == "ОШИБАЕТСЯ"

    # Молчание (пустой поток без ошибки) — это не живой ответ.
    silent = [_outcome(text="" if i < 4 else "Давай разберёмся") for i in range(20)]
    assert bakeoff.verdict(bakeoff.summarize("m", silent)) == "НЕНАДЁЖНА"
