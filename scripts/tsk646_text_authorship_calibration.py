# scripts/tsk646_text_authorship_calibration.py
"""
tsk-646: замер качества детектора ИИ-авторства НА ТЕКСТАХ (не на коде).

**Зачем замер вообще.** Детектор откалиброван на коде (tsk-302). У прозы и кода
разная природа: у кода признаки структурные (докстринги по конвенции, лишние
try/except), у прозы — стилистические и куда более спорные. Перенести рубрику
без замера значит выдать преподавателю вердикт неизвестного качества по вопросу,
где ошибка стоит отношений с ребёнком. Поэтому сначала цифры.

**Что берётся за истину.** Человеческой разметки «эта работа списана» в системе
нет и быть не может. Единственный доступный ярлык — слова преподавателя
(Коротких, 22.08) про ученицу 4538: «все задания с развёрнутым ответом у неё это
просто нейросеть». Это ярлык УРОВНЯ УЧЕНИКА и сам по себе тоже человеческая
догадка — так он и трактуется в отчёте, без вида на объективность.

Две выборки:

* **A — размеченная.** Все развёрнутые ответы (`TA`) реальных учеников.
  4538 — «по словам преподавателя ИИ» (положительный класс), остальные ученики —
  контроль. Контроль НЕ проверен: там тоже может быть списывание, поэтому
  сработка на контроле — верхняя граница ложных срабатываний, а не точная их доля.
* **B — неразмеченная, шумовая.** Длинные комментарии к заданиям `SA_COM`/
  `TBL_COM` (ход решения прозой, не код) у ВСЕХ учеников кроме 4538. Даёт не
  точность, а частоту сработки на потоке — сколько флагов преподаватель увидит.

Считаются ОБЕ оси детектора, раздельно: механические следы вставки (регулярки,
без сети) и стилистический вердикт модели. Раздельно — чтобы было видно, какая
ось что даёт: одна проверяема глазами, вторая нет.

Скрипт **строго read-only**: только SELECT из боевой базы, ни одной записи.
Тексты работ в репозиторий не попадают — они уходят в файл результата, который
кладётся вне репозитория (по умолчанию рядом с запуском).

Запуск:
    PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk646_text_authorship_calibration.py \
        --out C:/.../tsk646-calibration.json
    ... --model openai/gpt-5.4-mini   # прогнать конкретной моделью (по умолчанию — цепочка судей)
    ... --signals-only                # только регулярки, без обращения к модели (бесплатно)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg
from dotenv import load_dotenv

# До импорта модулей приложения: `app.core.config` читает окружение на импорте
# и падает без `DATABASE_URL`. Локальный `.env` даёт dev-адрес — он здесь и не
# нужен (боевая выборка идёт по DSN из `.mcp.json`), но без него не собрать
# сами модули сервиса, которые мы и меряем.
load_dotenv()

from app.services.code_review_service import looks_like_source_code  # noqa: E402
from app.services.text_authorship_service import (  # noqa: E402
    MIN_TEXT_CHARS,
    detect_paste_signals,
    review_student_text,
)

logger = logging.getLogger("tsk646.calibration")

#: Ученица, про которую преподаватель сказал «все развёрнутые — нейросеть».
LABELLED_AI_STUDENT = 4538

#: Аккаунты персонала: их сдачи — собственные прогоны, а не работы учеников.
#: 2 — оператор, 3 — Серебрякова (методист/преподаватель), 142 — тестовый
#: ученический аккаунт оператора, 4495 — Коротких.
STAFF_IDS = (2, 3, 142, 4495, 4496)

_TA_SQL = """
    SELECT tr.id, tr.user_id, tr.task_id, tr.is_correct, tr.checked_by,
           tr.answer_json->'response'->>'text' AS body,
           t.task_content->>'stem' AS stem
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id
    WHERE t.task_content->>'type' = 'TA'
      AND tr.user_id <> ALL($1::int[])
      AND coalesce(tr.answer_json->'response'->>'text', '') <> ''
    ORDER BY tr.user_id, tr.id
"""

_COMMENT_SQL = """
    SELECT tr.id, tr.user_id, tr.task_id, tr.is_correct, tr.checked_by,
           tr.answer_json->'response'->>'comment' AS body,
           t.task_content->>'stem' AS stem
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id
    WHERE t.task_content->>'type' IN ('SA_COM', 'TBL_COM')
      AND tr.user_id <> ALL($1::int[])
      AND tr.user_id <> $2
      AND length(coalesce(tr.answer_json->'response'->>'comment', '')) >= $3
    ORDER BY tr.user_id, tr.id
"""


def _prod_dsn() -> str:
    """Боевой DSN — из `.mcp.json`, а не из `.env`.

    В локальном `.env` лежит адрес dev-базы: прогон по нему измерил бы пустоту.
    Пароль нигде не печатается.
    """
    cfg = json.loads(Path(".mcp.json").read_text(encoding="utf-8"))
    dsn = cfg["mcpServers"]["learn_prod_db"]["args"][-1]
    # asyncpg не понимает параметр `options=-csearch_path%3D...` в строке.
    return dsn.split("?")[0]


async def _fetch_corpus(conn: asyncpg.Connection, kind: str) -> List[Dict[str, Any]]:
    """Выборка работ из боевой базы. Только чтение."""
    if kind == "ta":
        rows = await conn.fetch(_TA_SQL, list(STAFF_IDS))
    else:
        rows = await conn.fetch(
            _COMMENT_SQL, list(STAFF_IDS), LABELLED_AI_STUDENT, MIN_TEXT_CHARS
        )
    items = []
    for r in rows:
        body = (r["body"] or "").strip()
        if kind == "comments" and looks_like_source_code(body):
            # Комментарий с программой разбирает кодовая ветка детектора
            # (tsk-302). Здесь мерим прозу — иначе смешаем два предмета.
            continue
        items.append({
            "corpus": "A" if kind == "ta" else "B",
            "result_id": r["id"],
            "student_id": r["user_id"],
            "task_id": r["task_id"],
            "is_correct": r["is_correct"],
            "checked_by": r["checked_by"],
            "length": len(body),
            "below_threshold": len(body) < MIN_TEXT_CHARS,
            "label": (
                "teacher_says_ai" if r["user_id"] == LABELLED_AI_STUDENT else "control"
            ),
            "body": body,
            "stem": r["stem"],
        })
    return items


async def _judge(items: List[Dict[str, Any]], *, model: Optional[str], signals_only: bool) -> None:
    """Прогнать обе оси по каждой работе. Ось следов — всегда, модель — по флагу."""
    for i, item in enumerate(items, 1):
        item["signals"] = [s["code"] for s in detect_paste_signals(item["body"])]
        if signals_only or item["below_threshold"]:
            # Ниже порога живой детектор работу вообще не берёт — и замер обязан
            # считать ровно то, что будет в проде, а не «что было бы, если бы».
            item["verdict"] = None
            item["reasoning"] = None
            continue
        report = await review_student_text(
            item["body"], task_stem=item["stem"], student_id=None,
        )
        item["verdict"] = (report.get("ai_authorship") or {}).get("verdict")
        item["reasoning"] = (report.get("ai_authorship") or {}).get("reasoning")
        item["error"] = report.get("error")
        item["model"] = report.get("model")
        logger.info(
            "%s/%s res=%s ученик=%s следы=%s вердикт=%s",
            i, len(items), item["result_id"], item["student_id"],
            ",".join(item["signals"]) or "-", item["verdict"] or item.get("error"),
        )


def _summarize(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Сводка: сработки по осям, отдельно по размеченному классу и контролю."""

    def bucket(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        judged = [r for r in rows if r["verdict"] is not None]
        return {
            "работ": len(rows),
            "ниже_порога": sum(1 for r in rows if r["below_threshold"]),
            "разобрано_моделью": len(judged),
            "следы_вставки": sum(1 for r in rows if r["signals"]),
            "вердикт_ai_likely": sum(1 for r in judged if r["verdict"] == "ai_likely"),
            "вердикт_ambiguous": sum(1 for r in judged if r["verdict"] == "ambiguous"),
            "вердикт_student_likely": sum(
                1 for r in judged if r["verdict"] == "student_likely"
            ),
            "любая_ось_сработала": sum(
                1 for r in rows if r["signals"] or r["verdict"] == "ai_likely"
            ),
            "обе_оси_сработали": sum(
                1 for r in rows if r["signals"] and r["verdict"] == "ai_likely"
            ),
        }

    a = [r for r in items if r["corpus"] == "A"]
    return {
        "A_размеченная_ИИ": bucket([r for r in a if r["label"] == "teacher_says_ai"]),
        "A_контроль": bucket([r for r in a if r["label"] == "control"]),
        "B_шумовая": bucket([r for r in items if r["corpus"] == "B"]),
        "по_ученикам": _per_student(items),
    }


def _per_student(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Разрез по ученикам: у кого сколько работ и сколько сработок."""
    by: Dict[int, Dict[str, Any]] = {}
    for r in items:
        s = by.setdefault(r["student_id"], {
            "ученик": r["student_id"], "работ": 0, "следы": 0, "ai_likely": 0,
        })
        s["работ"] += 1
        s["следы"] += 1 if r["signals"] else 0
        s["ai_likely"] += 1 if r["verdict"] == "ai_likely" else 0
    return sorted(by.values(), key=lambda x: -x["работ"])


async def main() -> int:
    parser = argparse.ArgumentParser(description="Замер детектора ИИ-авторства на текстах")
    parser.add_argument("--out", required=True, help="Куда положить результат (json)")
    parser.add_argument("--model", default=None, help="Конкретная модель вместо цепочки судей")
    parser.add_argument("--signals-only", action="store_true", help="Только регулярки, без модели")
    parser.add_argument("--corpus", choices=("ta", "comments", "both"), default="both")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    # `load_dotenv` уже отработал на импорте — ключ провайдера в окружении.
    if args.model:
        # У сервиса намеренно нет параметра модели: в проде её выбирает цепочка
        # судей. Для замера подменяем саму цепочку — так измеряется ровно тот
        # путь, который поедет в прод, просто с одной моделью в списке.
        os.environ["LLM_JUDGE_MODELS"] = args.model

    conn = await asyncpg.connect(_prod_dsn())
    try:
        items: List[Dict[str, Any]] = []
        if args.corpus in ("ta", "both"):
            items += await _fetch_corpus(conn, "ta")
        if args.corpus in ("comments", "both"):
            items += await _fetch_corpus(conn, "comments")
    finally:
        await conn.close()

    logger.info("Взято работ: %s", len(items))
    await _judge(items, model=args.model, signals_only=args.signals_only)

    summary = _summarize(items)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"summary": summary, "items": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
