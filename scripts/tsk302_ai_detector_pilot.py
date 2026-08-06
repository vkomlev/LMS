# scripts/tsk302_ai_detector_pilot.py
"""
Пилот детектора ИИ-авторства кода ученика (tsk-302, направление 2, калибровка 2026-08-06).

Read-only аналитический скрипт: НЕ пишет в БД, НЕ уведомляет учеников, НЕ встроен
в поток проверки ответов. Читает корпус реальных Python-сдач (JSON-файл, собран
заранее read-only через MCP + SSH-выгрузку вложений с прод-сервера — см.
reviews/2026-08-06-tsk302-dir2-ai-detector-pilot-report.md) и прогоняет каждую
через LLM-судью (CloseRouter, ContentBackbone — см. ADR-0046 в CB) по фиксированной
рубрике AI_LIKELY / AMBIGUOUS / STUDENT_LIKELY.

Конфигурация подключения к CloseRouter берётся из .env ContentBackbone (канон
CLOSEROUTER_*, легаси CB_CLAUDE_* — тот же приоритет, что и в cb_llm_client.providers).
Ключ нигде не печатается и не логируется.

Запуск:
    PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/tsk302_ai_detector_pilot.py <corpus.json> <output.json>
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import dotenv_values

logger = logging.getLogger(__name__)

_CB_ENV_PATH = Path(r"D:\Work\ContentBackbone\.env")

_JUDGE_SYSTEM_PROMPT = """\
Ты — ассистент методиста в онлайн-школе программирования для школьников/подростков
(уровень: только начал изучать Python — циклы, переменные, функции, условия, списки,
базовые методы строк). Тебе показывают ОДИН фрагмент кода, который ученик сдал как
ответ на учебное задание. Твоя задача — НЕ проверять правильность кода (это уже
сделано отдельно), а оценить, ПОХОЖ ЛИ стиль кода на код, скопированный у ИИ-модели
(ChatGPT и т.п.) вместо того, чтобы быть написанным самим учеником этого уровня.

Это ЭВРИСТИКА для методиста, не доказательство. Твой вердикт может быть неверным —
БУДЬ ОСТОРОЖЕН: ложное обвинение хуже, чем пропущенный случай. Если сомневаешься —
выбирай AMBIGUOUS, а не AI_LIKELY.

Признаки AI_LIKELY (типичны для сгенерированного ИИ решения на этом уровне):
- Docstring в конвенции (Google/NumPy style, секции Args/Returns) — школьник этого
  уровня почти никогда не пишет докстринги по формальной конвенции без явного задания.
- Построчные англоязычные комментарии, дублирующие очевидное действие следующей строки.
- Использование конструкций СИЛЬНО выше уровня задания (декораторы, type hints,
  list comprehension с несколькими условиями, `if __name__ == "__main__":`,
  try/except там, где задание этого не требует и не подразумевает) без педагогической
  необходимости.
- Излишне "причёсанный", единообразный стиль (пробелы точно по PEP8, docstring,
  снейк-кейс везде) — контраст с реальным кодом новичка, который обычно неровный.

Признаки STUDENT_LIKELY:
- Магические числа без объяснения, непоследовательное форматирование (то с
  пробелами, то без), опечатки в именах переменных, орфографические ошибки в
  русскоязычных строках/комментариях.
- Копипаста повторяющегося кода вместо функции там, где повторение очевидно.
- Минимальная либо отсутствующая документация, "сырой" стиль ручной итерации.
- Транслитерация имён переменных (vozrast, spisok, slovo) — типичный признак
  школьника, редко встречается в ИИ-генерации (ИИ обычно транслитерирует по-другому
  или использует английские имена).

НЕ ставь AI_LIKELY только на основании "код слишком хороший/правильный для новичка"
без КОНКРЕТНОГО стилистического маркера — оптимизм интерпретации в пользу ученика.
Очень короткий код (1-3 строки) почти всегда AMBIGUOUS — минимальное решение
выглядит одинаково независимо от автора, там просто нет сигнала.

Ответь СТРОГО одним JSON-объектом без markdown-обрамления:
{"verdict": "AI_LIKELY" | "AMBIGUOUS" | "STUDENT_LIKELY", "reasoning": "1-2 предложения с конкретной ссылкой на код"}
"""


def _resolve_closerouter_env() -> Dict[str, str]:
    """Канон CLOSEROUTER_* > легаси CB_CLAUDE_* — тот же приоритет, что cb_llm_client.providers."""
    env = dotenv_values(_CB_ENV_PATH)
    base_url = env.get("CLOSEROUTER_BASE_URL") or env.get("CB_CLAUDE_BASE_URL")
    api_key = env.get("CLOSEROUTER_API_KEY") or env.get("CB_CLAUDE_API_KEY")
    model = env.get("CLOSEROUTER_MODEL") or env.get("CB_SOLVER_TIER1_MODEL") or "openai/gpt-5.5"
    if not base_url or not api_key:
        raise RuntimeError(
            f"CloseRouter не настроен в {_CB_ENV_PATH} (нет CLOSEROUTER_*/CB_CLAUDE_* base_url/api_key)."
        )
    # Легаси CB_CLAUDE_BASE_URL уже включает суффикс /v1 (стиль openai-SDK base_url);
    # канон CLOSEROUTER_BASE_URL — без него (см. cb_llm_client._chat_url). Нормализуем
    # к единому виду БЕЗ /v1, чтобы не задвоить путь в судейском запросе.
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[: -len("/v1")]
    return {"base_url": base_url, "api_key": api_key, "model": model}


def judge_code(conn: Dict[str, str], code: str, *, timeout_sec: float = 30.0) -> Dict[str, Any]:
    """
    Один вызов CloseRouter (POST /v1/chat/completions, OpenAI-совместимый) —
    судейство одного фрагмента кода. Возвращает распарсенный вердикт либо
    {"verdict": "ERROR", "reasoning": <причина>} при сбое (не бросает исключение,
    чтобы один плохой ответ API не уронил весь батч).
    """
    body = {
        "model": conn["model"],
        "messages": [
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": f"Код ученика:\n```python\n{code}\n```"},
        ],
        "temperature": 0.0,
        "seed": 42,
        "max_tokens": 300,
    }
    headers = {"Authorization": f"Bearer {conn['api_key']}"}
    try:
        resp = httpx.post(
            f"{conn['base_url']}/v1/chat/completions",
            json=body, headers=headers, timeout=timeout_sec,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001 — сбой одного вызова не должен ронять батч
        logger.warning("tsk302 judge_code: сбой вызова CloseRouter: %s", exc)
        return {"verdict": "ERROR", "reasoning": f"{type(exc).__name__}: {exc}"}

    # Модель иногда оборачивает JSON в ```json ... ``` несмотря на инструкцию — снимаем.
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    try:
        parsed = json.loads(content)
        if parsed.get("verdict") not in {"AI_LIKELY", "AMBIGUOUS", "STUDENT_LIKELY"}:
            raise ValueError(f"неожиданный verdict: {parsed.get('verdict')}")
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("tsk302 judge_code: не удалось разобрать ответ модели: %s | raw=%s", exc, content[:200])
        return {"verdict": "ERROR", "reasoning": f"parse_error: {content[:200]}"}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) != 3:
        print("Использование: tsk302_ai_detector_pilot.py <corpus.json> <output.json>", file=sys.stderr)
        sys.exit(1)

    corpus_path, output_path = Path(sys.argv[1]), Path(sys.argv[2])
    corpus: List[Dict[str, Any]] = json.loads(corpus_path.read_text(encoding="utf-8"))
    conn = _resolve_closerouter_env()
    logger.info("Судья: модель=%s, корпус=%d сэмплов", conn["model"], len(corpus))

    results = []
    for i, sample in enumerate(corpus, 1):
        verdict = judge_code(conn, sample["code"])
        results.append({**sample, "verdict": verdict})
        logger.info(
            "[%d/%d] user=%s task=%s -> %s",
            i, len(corpus), sample["user_id"], sample["external_uid"], verdict.get("verdict"),
        )
        time.sleep(0.2)  # не долбить API без пауз

    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    counts: Dict[str, int] = {}
    for r in results:
        v = r["verdict"].get("verdict", "ERROR")
        counts[v] = counts.get(v, 0) + 1
    logger.info("Итог: %s", counts)


if __name__ == "__main__":
    main()
