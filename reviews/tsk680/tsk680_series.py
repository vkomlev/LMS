# -*- coding: utf-8 -*-
"""Ряд замеров первого куска по цепочке наставника в разные часы (tsk-680).

Главный вопрос задачи — УСТОЙЧИВОСТЬ, а не единичный замер: три прохода одного
вечера дали два разных ответа. Поэтому здесь один процесс, раунд раз в N минут,
и обе интересные формы вызова у головы цепочки:

  A — как её зовёт еженедельная проверка: голый короткий вопрос, потолок 32
  B — как её зовёт боевой наставник: системная инструкция ~5 тыс. знаков,
      реплика ученика, потолок 900

Остальные модели цепочки меряются формой A — они здесь для фона, а не для
разбирательства.

Поток обрывается СРАЗУ после первого куска: мерится только время до него, а
дописывать ответ до конца значит платить за токены, которые никто не прочтёт.
Плата за это — расход не попадает в `llm_usage_event` (клиент пишет его в конце
потока), поэтому источник истины здесь — свой JSONL, а не пульт.

Ничего не меняет: только читает начало потока и пишет строку в файл.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/opt/lms")

from app.services.ai_tutor.prompt import TutorTaskView, build_system_prompt  # noqa: E402
from app.services.llm import Budget, LLMError, LLMMessage, stream  # noqa: E402

PURPOSE = "tsk680_probe"
FIRST_TOKEN_BUDGET = 12.0

_VIEW = TutorTaskView(
    task_id=0,
    stem=(
        "Составь программу: пользователь вводит числа, пока не введёт 0. "
        "Выведи сумму введённых чисел и их количество."
    ),
    task_type="SA",
    course_title="Python для подростков",
)
_SYSTEM = build_system_prompt(_VIEW, "concept", student_answer="summa = 0")

FORMS = {
    "A": (None, "Ответь одним словом: готов", 32),
    "B": (_SYSTEM, "Я не понимаю, как считать количество чисел. С чего начать?", 900),
}

PLAN = (
    ("anthropic/claude-sonnet-4.6", "A"),
    ("anthropic/claude-sonnet-4.6", "B"),
    ("openai/gpt-5.5", "A"),
    ("x-ai/grok-4.5", "A"),
    ("openai/gpt-5.4-mini", "A"),
)


async def probe(model: str, form: str) -> dict:
    system, user, max_tokens = FORMS[form]
    messages = []
    if system:
        messages.append(LLMMessage(role="system", content=system))
    messages.append(LLMMessage(role="user", content=user))

    started = time.monotonic()
    first_at: float | None = None
    error: str | None = None
    try:
        async for chunk in stream(
            messages, model=model, purpose=PURPOSE, budget=Budget.BATCH, max_tokens=max_tokens
        ):
            if chunk.delta:
                first_at = time.monotonic() - started
                break
    except LLMError as exc:
        error = f"{type(exc).__name__}: {exc}"[:200]
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"[:200]
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "form": form,
        "first_at": None if first_at is None else round(first_at, 2),
        "error": error,
        "verdict": "ok" if (first_at is not None and first_at <= FIRST_TOKEN_BUDGET) else "bad",
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=36)
    parser.add_argument("--sleep", type=float, default=600.0)
    parser.add_argument("--out", default="/tmp/tsk680_series.jsonl")
    args = parser.parse_args()

    for round_no in range(1, args.rounds + 1):
        for model, form in PLAN:
            row = await probe(model, form)
            row["round"] = round_no
            with open(args.out, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps(row, ensure_ascii=False), flush=True)
        if round_no < args.rounds:
            await asyncio.sleep(args.sleep)


if __name__ == "__main__":
    asyncio.run(main())
