"""tsk-772: исправить дефекты спарсенных партий ``oge:reshu`` по первоисточнику.

В отличие от синтетических партий (t2/t4/t7, см. tsk-770) здесь в ``external_uid``
стоит настоящий id задачи РешуОГЭ, ответ брался с сайта и потому верен. Испорчено
другое: условие проходило через пересказ языковой моделью. Сверка 269 активных
заданий с источником дала три класса дефектов.

**1. Потерян или искажён текст условия.**
``6373`` — список птиц пропал целиком, задача нерешаема (ученик 4510 открыл по ней
обращение 02.09). ``6349``, ``6366``, ``6371`` — список на месте, но пересказ переврал
названия («Марао» вместо «Маражо», «Волгда» вместо «Вологда», «бабиросса» вместо
«бабирусса»); ответ от этого не меняется, но текст ученику показывается ложный.

**2. Перевёрнут вопрос.** ``6486``: в источнике «наименьшее целое число x», у нас
«наибольшее». Числа те же, эталон верный, а задача в нашей формулировке решения не
имеет — наибольшего чётного числа, не меньшего 7, не существует.

**3. Эталон беднее источника.** Источник допускает несколько написаний
(«Долгопёр|долгопер|Долгопер», «доктор|врач»), а у нас записан один вариант. Нормализация
ответа — только ``trim`` и ``lower``: она НЕ приравнивает «ё» к «е», поэтому ученик,
написавший «Долгопёр» или «врач», получил бы незачёт за верный ответ.

Правки точечные, каждая проверяется после записи чтением из базы: условия — решателем,
эталоны — наличием всех вариантов.

Запуск::

    python scripts/tsk772_fix_parsed_oge_batches.py            # сухой прогон
    DBCHECK_OK=1 python scripts/tsk772_fix_parsed_oge_batches.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

logger = logging.getLogger("tsk772")


# --------------------------------------------------------------------- решатели

def solve_text_size(stem: str) -> list[str]:
    """Задание 1 ОГЭ: из списка вычеркнули слово, размер упал на N байт.

    Вместе со словом уходит разделитель «, » — два символа. Значит длина
    вычеркнутого слова = N / (бит на символ / 8) - 2.
    """
    bits = re.search(r"(\d+)\s*бит", stem)
    delta = re.search(r"на\s+(\d+)\s*байт", stem)
    quote = re.search(r"«([^»]+)»", stem)
    if not (bits and delta and quote):
        return []
    per_char = int(bits.group(1)) // 8
    if per_char <= 0:
        return []
    target_len = int(delta.group(1)) // per_char - 2
    # список — до тире, дальше идёт пояснение («— птицы», «относятся к…»)
    listing = re.split(r"\s+[—–-]\s+|\s+относятся\s+", quote.group(1))[0]
    words = [w.strip() for w in listing.split(",") if w.strip()]
    return sorted({w for w in words if len(w) == target_len})


def solve_logic(stem: str) -> list[str]:
    """Задание 3 ОГЭ: наибольшее/наименьшее целое X, для которого истинно."""
    body = stem.split("Ответ запишите")[0]
    expr = body.split("высказывание", 1)[1].lstrip(":").strip().rstrip(".?")
    text = expr.replace("≤", "<=").replace("≥", ">=")
    text = re.sub(r"нечётн\w*|нечетн\w*", "@ODD@", text)
    text = re.sub(r"чётн\w*|четн\w*", "@EVEN@", text)
    text = re.sub(r"\bX\b", "n", text, flags=re.I)
    text = re.sub(r"\bНЕ\b", " not ", text)
    text = re.sub(r"\bИЛИ\b", " or ", text)
    text = re.sub(r"\bИ\b", " and ", text)
    pred = text.replace("@EVEN@", "% 2 == 0").replace("@ODD@", "% 2 == 1")
    hits = [n for n in range(-500, 1001)
            if eval(pred, {"__builtins__": {}}, {"n": n})]  # noqa: S307
    if not hits:
        return []
    if "наибольш" in body:
        # у неограниченного сверху множества наибольшего нет — это и есть дефект
        return [] if max(hits) == 1000 else [str(max(hits))]
    if "наименьш" in body:
        return [] if min(hits) == -500 else [str(min(hits))]
    return [str(hits[0])] if len(hits) == 1 else []


SOLVERS = {"text_size": solve_text_size, "logic": solve_logic}


# ----------------------------------------------------------------------- правки

@dataclass
class Fix:
    task_id: int
    why: str
    stem_edits: list[tuple[str, str]] = field(default_factory=list)
    solver: str | None = None
    expected: str | None = None
    add_answers: list[str] = field(default_factory=list)


FIXES: list[Fix] = [
    # 1. Условие потеряно или искажено пересказом
    Fix(
        task_id=6373, solver="text_size", expected="грач",
        why="список птиц пропал целиком — задача была нерешаема",
        stem_edits=[(
            "Вова написал текст со списком птиц, разделённых запятыми и пробелами.",
            "Вова написал текст (в нём нет лишних пробелов): «Чиж, грач, стриж, "
            "гагара, пингвин, ласточка, жаворонок, свиристель, буревестник, "
            "вертиголовка — птицы».",
        )],
    ),
    Fix(
        task_id=6349, solver="text_size", expected="Суматра",
        why="пересказ переврал название острова: «Марао» вместо «Маражо»",
        stem_edits=[("Марао", "Маражо")],
    ),
    Fix(
        task_id=6366, solver="text_size", expected="Соликамск",
        why="пересказ переврал название города: «Волгда» вместо «Вологда»",
        stem_edits=[("Волгда", "Вологда")],
    ),
    Fix(
        task_id=6371, solver="text_size", expected="пекари",
        why="пересказ переврал название животного: «бабиросса» вместо «бабирусса»",
        stem_edits=[("бабиросса", "бабирусса")],
    ),
    # 2. Перевёрнутый вопрос
    Fix(
        task_id=6486, solver="logic", expected="8",
        why="в источнике «наименьшее целое число», у нас «наибольшее» — такого нет",
        stem_edits=[("наибольшее целое число X", "наименьшее целое число X")],
    ),
    # 3. Эталон беднее источника: добавляем написания, которые источник считает верными
    Fix(task_id=6350, why="источник допускает «Долгопёр»; нормализация не равняет ё и е",
        add_answers=["Долгопёр"]),
    Fix(task_id=6856, why="источник допускает «философом»", add_answers=["философом"]),
    Fix(task_id=6857, why="источник допускает «восемьсот»", add_answers=["восемьсот"]),
    Fix(task_id=6858, why="источник допускает «Семён»; нормализация не равняет ё и е",
        add_answers=["Семён"]),
    Fix(task_id=6875, why="источник допускает «врач»", add_answers=["врач"]),
    Fix(task_id=6880, why="источник допускает «в Чечне»", add_answers=["в Чечне"]),
]


def apply_edits(stem: str, edits: list[tuple[str, str]], task_id: int) -> str:
    """Точечные замены; подстрока обязана встречаться ровно один раз."""
    for old, new in edits:
        found = stem.count(old)
        if found != 1:
            raise RuntimeError(
                f"[{task_id}] подстрока {old!r} встречается {found} раз, ожидался 1"
            )
        stem = stem.replace(old, new)
    return stem


def dsn(alias: str = "learn_prod_db") -> str:
    """Строка подключения из .mcp.json проекта (в код её не хардкодим)."""
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".mcp.json")
                     .read_text(encoding="utf-8"))
    return cfg["mcpServers"][alias]["args"][-1].split("?")[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="записать изменения (без флага — только показать)")
    parser.add_argument("--backup-dir", default="reviews")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    conn = psycopg2.connect(dsn())
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute(
        "SELECT id, external_uid, task_content, solution_rules FROM tasks "
        "WHERE id = ANY(%s) ORDER BY id",
        ([f.task_id for f in FIXES],),
    )
    rows = {row["id"]: row for row in cur.fetchall()}

    backup: list[dict[str, Any]] = []
    plan: list[tuple[Fix, dict[str, Any] | None, list[dict[str, Any]] | None]] = []
    for fix in FIXES:
        row = rows.get(fix.task_id)
        if row is None:
            raise RuntimeError(f"[{fix.task_id}] задание не найдено")
        content: dict[str, Any] | None = None
        answers: list[dict[str, Any]] | None = None

        if fix.stem_edits:
            content = dict(row["task_content"])
            new_stem = apply_edits(content["stem"], fix.stem_edits, fix.task_id)
            solved = SOLVERS[fix.solver](new_stem) if fix.solver else []
            if solved != [fix.expected]:
                raise RuntimeError(
                    f"[{fix.task_id}] после правки решатель даёт {solved}, "
                    f"ожидался единственный ответ {fix.expected!r} — правка отклонена"
                )
            before = SOLVERS[fix.solver](content["stem"]) if fix.solver else []
            content["stem"] = new_stem
            logger.info("[%s] %s", fix.task_id, fix.why)
            logger.info("      было:  решатель -> %s", before or "ответа нет")
            logger.info("      стало: решатель -> %s", solved)

        if fix.add_answers:
            rules = row["solution_rules"] or {}
            answers = list((rules.get("short_answer") or {}).get("accepted_answers") or [])
            existing = {str(a.get("value", "")).strip().lower() for a in answers}
            score = answers[0].get("score", 1) if answers else 1
            added = []
            for value in fix.add_answers:
                if value.strip().lower() in existing:
                    continue
                answers.append({"score": score, "value": value})
                added.append(value)
            if not added:
                answers = None
            else:
                logger.info("[%s] %s", fix.task_id, fix.why)
                logger.info("      было:  %s",
                            [a.get("value") for a in answers if a.get("value") not in added])
                logger.info("      стало: %s", [a.get("value") for a in answers])

        if content is None and answers is None:
            continue
        plan.append((fix, content, answers))
        backup.append({"id": fix.task_id, "external_uid": row["external_uid"],
                       "task_content": row["task_content"],
                       "solution_rules": row["solution_rules"]})

    logger.info("\nК правке: %d заданий", len(plan))
    if not args.apply:
        logger.info("Сухой прогон. Для записи: DBCHECK_OK=1 ... --apply")
        conn.close()
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    backup_path = Path(args.backup_dir) / f"{stamp}-tsk772-parsed-batches-backup.json"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    logger.info("Снимок условий и правил до правки: %s", backup_path)

    try:
        for fix, content, answers in plan:
            if content is not None:
                cur.execute("UPDATE tasks SET task_content = %s WHERE id = %s",
                            (psycopg2.extras.Json(content), fix.task_id))
                if cur.rowcount != 1:
                    raise RuntimeError(f"[{fix.task_id}] UPDATE условия: {cur.rowcount} строк")
            if answers is not None:
                cur.execute(
                    """
                    UPDATE tasks
                    SET solution_rules = jsonb_set(
                            solution_rules, '{short_answer,accepted_answers}',
                            %s::jsonb, false)
                    WHERE id = %s
                    """,
                    (json.dumps(answers, ensure_ascii=False), fix.task_id),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"[{fix.task_id}] UPDATE правил: {cur.rowcount} строк")

        # Верификация поштучно и ИЗ БАЗЫ.
        cur.execute(
            "SELECT id, task_content->>'stem' AS stem, "
            "solution_rules#>'{short_answer,accepted_answers}' AS answers "
            "FROM tasks WHERE id = ANY(%s) ORDER BY id",
            ([f.task_id for f, _, _ in plan],),
        )
        actual = {row["id"]: row for row in cur.fetchall()}
        for fix, content, answers in plan:
            row = actual[fix.task_id]
            if fix.solver:
                solved = SOLVERS[fix.solver](row["stem"])
                if solved != [fix.expected]:
                    raise RuntimeError(
                        f"[{fix.task_id}] проверка из базы: решатель даёт {solved}"
                    )
            if answers is not None:
                values = {str(a.get("value", "")).lower() for a in (row["answers"] or [])}
                missing = [v for v in fix.add_answers if v.lower() not in values]
                if missing:
                    raise RuntimeError(
                        f"[{fix.task_id}] проверка из базы: не добавились {missing}"
                    )
        conn.commit()
        logger.info("Записано и проверено из базы: %d заданий", len(plan))
    except Exception:
        conn.rollback()
        logger.exception("Откат транзакции — изменения не применены")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
