"""tsk-770: переделка заданий, которые правкой одного эталона не чинятся.

Три класса, оставшихся после ``tsk770_fix_oge_*_etalons.py``:

* **условие не читается вообще** (6404, 6409) — закодированная строка не разбирается
  ни одним способом, верного ответа не существует. Строка восстанавливается из
  эталона: склеиваем коды его букв. Для 6404 дополнительно разводятся коды Ы и А —
  генератор выдал им один и тот же код, из-за чего ответ был бы неоднозначен;
* **условие допускает несколько ответов** (6410, 6419) — ученик отвечает верно и
  получает незачёт. Разводится код-двойник (6410) либо заменяется лишняя цепочка,
  нарушавшая обещанную условием единственность (6419);
* **вырожденное условие** (6568, 6569, 6570) — после пересчёта ответ оказался
  тривиальным (две дороги по километру) или задача решалась прямой дорогой в обход
  маршрута из заголовка. Вес одного ребра возвращается к значению, при котором
  сходятся и заголовок, и ИСХОДНЫЙ эталон партии, — то есть условие восстанавливается
  по эталону, а не выдумывается заново.

Каждая правка после применения проверяется решателем: ответ обязан быть единственным
и равным заявленному. Не сошлось — вся транзакция откатывается.

Запуск::

    python scripts/tsk770_rebuild_broken_tasks.py            # сухой прогон
    DBCHECK_OK=1 python scripts/tsk770_rebuild_broken_tasks.py --apply
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
from typing import Any, Callable

import psycopg2
import psycopg2.extras

logger = logging.getLogger("tsk770")

#: Кириллические двойники латиницы в именах пунктов.
LOOKALIKE = {"А": "A", "В": "B", "С": "C", "Е": "E", "М": "M", "К": "K",
             "Н": "H", "О": "O", "Р": "P", "Т": "T", "Х": "X", "У": "Y"}
EDGE_RE = re.compile(r"([A-Za-zА-Яа-яЁё])\s*-\s*([A-Za-zА-Яа-яЁё])\s*=\s*(\d+)")
TARGET_RE = re.compile(
    r"(?:между(?:\s+пунктами)?\s+([A-Za-zА-Яа-яЁё])\s+и\s+([A-Za-zА-Яа-яЁё])"
    r"|от\s+(?:пункта|станции)\s+([A-Za-zА-Яа-яЁё])[^.]*?до\s+(?:пункта|станции)"
    r"\s+([A-Za-zА-Яа-яЁё]))"
)
PAIR_RE = re.compile(r"([А-ЯЁA-Z])\s*=\s*([^\s,.;]+(?:\s+[^\s,.;А-ЯЁA-Z]+)*)")
CHAINS_RE = re.compile(r"(?:цепочки|шифровки)\s*:\s*([0-9,\s]+)")


# --------------------------------------------------------------------- решатели

def solve_graph(stem: str) -> list[str]:
    """Длина кратчайшего пути. Возвращает список — для единообразной проверки."""
    body = stem.split("Определите")[0].split("Найдите")[0]
    edges: dict[frozenset[str], int] = {}
    for a, b, weight in EDGE_RE.findall(body):
        edges[frozenset({LOOKALIKE.get(a, a), LOOKALIKE.get(b, b)})] = int(weight)
    pos = stem.find("Определите")
    if pos < 0:
        pos = stem.find("Найдите")
    match = TARGET_RE.search(stem[pos:])
    if not match:
        raise ValueError("не нашёлся вопрос «между X и Y»")
    nodes = [g for g in match.groups() if g]
    src = LOOKALIKE.get(nodes[0], nodes[0])
    dst = LOOKALIKE.get(nodes[1], nodes[1])

    adj: dict[str, list[tuple[str, int]]] = {}
    for pair, weight in edges.items():
        u, v = tuple(pair)
        adj.setdefault(u, []).append((v, weight))
        adj.setdefault(v, []).append((u, weight))
    lengths: list[int] = []

    def walk(node: str, path: list[str], length: int) -> None:
        if node == dst:
            lengths.append(length)
            return
        for nxt, weight in adj.get(node, []):
            if nxt not in path:
                path.append(nxt)
                walk(nxt, path, length + weight)
                path.pop()

    walk(src, [src], 0)
    return [str(min(lengths))] if lengths else []


def _decode_all(msg: str, table: dict[str, str], no_repeat: bool) -> list[str]:
    """Все разборы строки; no_repeat — оговорка «буквы не повторяются»."""
    out: list[str] = []

    def walk(pos: int, acc: list[str]) -> None:
        if len(out) >= 200:
            return
        if pos == len(msg):
            out.append("".join(acc))
            return
        for letter, code in table.items():
            if no_repeat and letter in acc:
                continue
            if code and msg.startswith(code, pos):
                acc.append(letter)
                walk(pos + len(code), acc)
                acc.pop()

    walk(0, [])
    return out


def solve_decode(stem: str) -> list[str]:
    """Все разборы закодированной строки по кодовой таблице."""
    table = {letter: code.replace(" ", "") for letter, code in PAIR_RE.findall(stem)}
    if not table:
        return []
    alphabet = "".join(sorted(set("".join(table.values()))))
    runs = re.findall("[" + re.escape(alphabet) + "]+", stem.replace(" ", ""))
    longest = max(len(code) for code in table.values())
    candidates = [run for run in runs if len(run) > longest]
    if not candidates:
        return []
    msg = max(candidates, key=len)
    return sorted(set(_decode_all(msg, table, "не повторяются" in stem)))


def solve_chains(stem: str) -> list[str]:
    """Слова из тех цепочек, что читаются единственным способом."""
    table = {letter: code for letter, code in PAIR_RE.findall(stem.split("Дан")[0])
             if code.isdigit()}
    chains = [c.strip() for c in CHAINS_RE.search(stem).group(1).split(",") if c.strip()]
    words = []
    for chain in chains:
        variants = _decode_all(chain, table, False)
        if len(variants) == 1:
            words.append(variants[0])
    return sorted(set(words))


SOLVERS: dict[str, Callable[[str], list[str]]] = {
    "graph": solve_graph,
    "decode": solve_decode,
    "chains": solve_chains,
}


# ----------------------------------------------------------------------- правки

@dataclass
class Rebuild:
    """Одна переделка: точечные замены в условии + ожидаемый единственный ответ."""

    task_id: int
    solver: str
    expected: str
    why: str
    stem_edits: list[tuple[str, str]] = field(default_factory=list)
    new_title: str | None = None


REBUILDS: list[Rebuild] = [
    # --- условие не читалось ни одним разбором -------------------------------
    Rebuild(
        task_id=6404, solver="decode", expected="БЫК",
        why="строка не разбиралась; восстановлена из эталона, коды Ы и А разведены",
        stem_edits=[("А=?€? .", "А=€€ ."), ("строке: ????€?€ .", "строке: ???€??€ .")],
    ),
    Rebuild(
        task_id=6409, solver="decode", expected="КОЛ",
        why="строка не разбиралась; восстановлена из эталона",
        stem_edits=[("строке !!???!?? .", "строке !!??!??? .")],
    ),
    # --- условие допускало несколько верных ответов --------------------------
    Rebuild(
        task_id=6410, solver="decode", expected="СЕД",
        why="код Р давал второй верный разбор СР; код Р разведён",
        stem_edits=[("Р=!!!?,", "Р=!!!!,")],
    ),
    Rebuild(
        task_id=6419, solver="chains", expected="ДАТА",
        why="однозначных цепочек было две (20335->ТВВД и 51201->ДАТА); лишняя заменена",
        stem_edits=[("шифровки: 20335, 21120, 31321, 51201",
                     "шифровки: 11213, 21120, 31321, 51201")],
        new_title="Шифровка с единственной расшифровкой",
    ),
    # --- вырожденное условие: вес ребра возвращён к исходному эталону --------
    Rebuild(
        task_id=6568, solver="graph", expected="6",
        why="задача решалась прямой дорогой A-F=5 в обход маршрута из заголовка",
        stem_edits=[("A-F=5,", "A-F=7,")],
    ),
    Rebuild(
        task_id=6569, solver="graph", expected="5",
        why="ответ был вырожден (A-D-F = 1+1); вес D-F возвращён к исходному эталону 5",
        stem_edits=[("D-F=1,", "D-F=4,")],
    ),
    Rebuild(
        task_id=6570, solver="graph", expected="6",
        why="ответ был вырожден (A-B-F = 1+1); вес B-F возвращён к исходному эталону 6",
        stem_edits=[("B-F=1,", "B-F=5,")],
    ),
]


def apply_edits(stem: str, edits: list[tuple[str, str]], task_id: int) -> str:
    """Точечные замены. Подстрока обязана встречаться ровно один раз."""
    for old, new in edits:
        found = stem.count(old)
        if found != 1:
            raise RuntimeError(
                f"[{task_id}] подстрока {old!r} встречается {found} раз, ожидался ровно 1"
            )
        stem = stem.replace(old, new)
    return stem


def dsn_from_mcp(alias: str = "learn_prod_db") -> str:
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

    conn = psycopg2.connect(dsn_from_mcp())
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute(
        "SELECT id, external_uid, task_content, solution_rules FROM tasks "
        "WHERE id = ANY(%s) ORDER BY id",
        ([r.task_id for r in REBUILDS],),
    )
    rows = {row["id"]: row for row in cur.fetchall()}

    backup: list[dict[str, Any]] = []
    plan: list[tuple[Rebuild, dict[str, Any]]] = []
    for rb in REBUILDS:
        row = rows.get(rb.task_id)
        if row is None:
            raise RuntimeError(f"[{rb.task_id}] задание не найдено")
        content = dict(row["task_content"])
        old_stem = content["stem"]
        new_stem = apply_edits(old_stem, rb.stem_edits, rb.task_id)

        answers = SOLVERS[rb.solver](new_stem)
        if answers != [rb.expected]:
            raise RuntimeError(
                f"[{rb.task_id}] после правки решатель даёт {answers}, а ожидался "
                f"единственный ответ {rb.expected!r} — правка отклонена"
            )
        before = SOLVERS[rb.solver](old_stem)
        current = (((row["solution_rules"] or {}).get("short_answer") or {})
                   .get("accepted_answers") or [{}])[0].get("value")

        content["stem"] = new_stem
        if rb.new_title:
            content["title"] = rb.new_title
        plan.append((rb, content))
        backup.append({"id": rb.task_id, "external_uid": row["external_uid"],
                       "task_content": row["task_content"],
                       "solution_rules": row["solution_rules"]})

        logger.info("[%s] %s", rb.task_id, rb.why)
        logger.info("      было:  решатель -> %s, эталон в базе %r",
                    before or "ответа нет", current)
        logger.info("      стало: решатель -> [%r] (единственный), эталон %r",
                    rb.expected, rb.expected)

    if not args.apply:
        logger.info("\nСухой прогон. Для записи: DBCHECK_OK=1 ... --apply")
        conn.close()
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    backup_path = Path(args.backup_dir) / f"{stamp}-tsk770-rebuilt-tasks-backup.json"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    logger.info("Снимок условий и правил до правки: %s", backup_path)

    try:
        for rb, content in plan:
            cur.execute(
                """
                UPDATE tasks
                SET task_content = %s,
                    solution_rules = jsonb_set(
                        solution_rules,
                        '{short_answer,accepted_answers,0,value}',
                        to_jsonb(%s::text), false)
                WHERE id = %s
                """,
                (psycopg2.extras.Json(content), rb.expected, rb.task_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"[{rb.task_id}] UPDATE затронул {cur.rowcount} строк")

        # Верификация поштучно и ИЗ БАЗЫ: перечитываем то, что реально записалось.
        cur.execute(
            "SELECT id, task_content->>'stem' AS stem, "
            "solution_rules#>>'{short_answer,accepted_answers,0,value}' AS v "
            "FROM tasks WHERE id = ANY(%s) ORDER BY id",
            ([rb.task_id for rb, _ in plan],),
        )
        actual = {r["id"]: r for r in cur.fetchall()}
        for rb, _content in plan:
            row = actual[rb.task_id]
            answers = SOLVERS[rb.solver](row["stem"])
            if answers != [rb.expected] or row["v"] != rb.expected:
                raise RuntimeError(
                    f"[{rb.task_id}] проверка из базы не сошлась: решатель {answers}, "
                    f"эталон {row['v']!r}"
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
