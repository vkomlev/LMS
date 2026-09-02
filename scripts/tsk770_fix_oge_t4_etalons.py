"""tsk-770: исправление эталонов партии ``oge:reshu:t4:*`` (курс 1128, ОГЭ задание 4).

Партия синтетическая: ключ ``external_uid`` содержит порядковый номер, а не ID задачи
на РешуОГЭ (у 10 из 13 партий ``oge:reshu:*`` там настоящие 5-значные ID). Условие и
ответ порождены языковой моделью; ответ она считала неполным перебором, теряя одно
ребро, поэтому у 9 заданий эталон равен длине ВТОРОГО по краткости пути, а у трёх не
равен длине вообще ни одного пути. Источник истины — стем: он самодостаточен,
математически корректен и именно его видит ученик.

Скрипт заново решает каждое задание перебором простых путей ПРЯМО ИЗ СТЕМА в БД и
приводит ``solution_rules.short_answer.accepted_answers[0].value`` к посчитанному
значению. Ничего, кроме этого поля, не трогает; вердикты прошлых сдач не пересчитывает
(решение по баллам — за оператором).

Запуск::

    python scripts/tsk770_fix_oge_t4_etalons.py            # сухой прогон
    DBCHECK_OK=1 python scripts/tsk770_fix_oge_t4_etalons.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

logger = logging.getLogger("tsk770")

UID_LIKE = "oge:reshu:t4:%"

#: Кириллические двойники латиницы — стемы смешивают алфавиты в именах пунктов.
LOOKALIKE = {"А": "A", "В": "B", "С": "C", "Е": "E", "М": "M", "К": "K",
             "Н": "H", "О": "O", "Р": "P", "Т": "T", "Х": "X", "У": "Y"}

EDGE_RE = re.compile(r"([A-Za-zА-Яа-яЁё])\s*-\s*([A-Za-zА-Яа-яЁё])\s*=\s*(\d+)")
TARGET_RE = re.compile(
    r"(?:между(?:\s+пунктами)?\s+([A-Za-zА-Яа-яЁё])\s+и\s+([A-Za-zА-Яа-яЁё])"
    r"|от\s+(?:пункта|станции)\s+([A-Za-zА-Яа-яЁё])[^.]*?до\s+(?:пункта|станции)"
    r"\s+([A-Za-zА-Яа-яЁё]))"
)


def norm_node(ch: str) -> str:
    """Привести кириллический двойник имени пункта к латинице."""
    return LOOKALIKE.get(ch, ch)


def parse_edges(stem: str) -> dict[frozenset[str], int]:
    """Достать список дорог из условия (часть текста до вопроса)."""
    body = stem.split("Определите")[0].split("Найдите")[0]
    edges: dict[frozenset[str], int] = {}
    for a, b, weight in EDGE_RE.findall(body):
        key = frozenset({norm_node(a), norm_node(b)})
        if len(key) != 2:
            raise ValueError(f"петля в списке дорог: {a}-{b}")
        edges[key] = int(weight)
    if not edges:
        raise ValueError("в условии не нашлось ни одной дороги")
    return edges


def parse_target(stem: str) -> tuple[str, str]:
    """Достать пару пунктов, между которыми ищется путь."""
    pos = stem.find("Определите")
    if pos < 0:
        pos = stem.find("Найдите")
    match = TARGET_RE.search(stem[pos:])
    if not match:
        raise ValueError("не нашёлся вопрос «между X и Y»")
    nodes = [g for g in match.groups() if g]
    return norm_node(nodes[0]), norm_node(nodes[1])


def all_simple_paths(edges: dict[frozenset[str], int], src: str,
                     dst: str) -> list[tuple[int, list[str]]]:
    """Полный перебор простых путей: (длина, маршрут), отсортировано по длине."""
    adj: dict[str, list[tuple[str, int]]] = {}
    for pair, weight in edges.items():
        u, v = tuple(pair)
        adj.setdefault(u, []).append((v, weight))
        adj.setdefault(v, []).append((u, weight))
    found: list[tuple[int, list[str]]] = []

    def walk(node: str, path: list[str], length: int) -> None:
        if node == dst:
            found.append((length, list(path)))
            return
        for nxt, weight in adj.get(node, []):
            if nxt in path:
                continue
            path.append(nxt)
            walk(nxt, path, length + weight)
            path.pop()

    if src not in adj or dst not in adj:
        raise ValueError(f"пункт {src} или {dst} отсутствует в списке дорог")
    walk(src, [src], 0)
    if not found:
        raise ValueError(f"нет ни одного пути {src} -> {dst}")
    return sorted(found)


def solve(stem: str) -> tuple[str, str]:
    """Посчитать ответ по условию. Возвращает (ответ, пояснение-маршрут)."""
    edges = parse_edges(stem)
    src, dst = parse_target(stem)
    paths = all_simple_paths(edges, src, dst)
    shortest = paths[0][0]

    longest_asked = "самого длинного" in stem
    shortest_asked = "самого короткого" in stem
    if longest_asked or shortest_asked:
        # Спрашивают длину одного участка НА кратчайшем пути, а не длину пути.
        picks: set[int] = set()
        for length, path in paths:
            if length != shortest:
                continue
            weights = [edges[frozenset({path[i], path[i + 1]})]
                       for i in range(len(path) - 1)]
            picks.add(max(weights) if longest_asked else min(weights))
        if len(picks) != 1:
            raise ValueError(f"кратчайшие пути дают разные участки: {sorted(picks)}")
        route = "; ".join("-".join(p) for ln, p in paths if ln == shortest)
        return str(picks.pop()), f"кратчайший путь {shortest}: {route}"

    best = [p for ln, p in paths if ln == shortest]
    return str(shortest), f"{shortest} = " + " / ".join("-".join(p) for p in best)


def dsn_from_mcp(alias: str = "learn_prod_db") -> str:
    """Взять строку подключения из .mcp.json проекта (в код её не хардкодим)."""
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".mcp.json")
                     .read_text(encoding="utf-8"))
    return cfg["mcpServers"][alias]["args"][-1].split("?")[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="записать изменения (без флага — только показать)")
    parser.add_argument("--backup-dir", default="reviews",
                        help="куда положить снимок solution_rules до правки")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        stream=sys.stdout)

    conn = psycopg2.connect(dsn_from_mcp())
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute(
        """
        SELECT id, external_uid, task_content->>'title' AS title,
               task_content->>'stem' AS stem, solution_rules
        FROM tasks
        WHERE external_uid LIKE %s AND is_active
        ORDER BY id
        """,
        (UID_LIKE,),
    )
    rows = cur.fetchall()
    logger.info("Активных заданий в партии: %d", len(rows))

    planned: list[tuple[int, str, str, str, str]] = []
    backup: list[dict[str, Any]] = []
    for row in rows:
        answer, explain = solve(row["stem"])
        rules = row["solution_rules"] or {}
        accepted = (rules.get("short_answer") or {}).get("accepted_answers") or []
        if len(accepted) != 1:
            raise RuntimeError(
                f"[{row['id']}] ожидался ровно один эталон, а их {len(accepted)}"
            )
        current = str(accepted[0].get("value"))
        if current == answer:
            continue
        planned.append((row["id"], row["external_uid"], current, answer, explain))
        backup.append({"id": row["id"], "external_uid": row["external_uid"],
                       "title": row["title"], "solution_rules": rules})

    logger.info("К правке: %d заданий", len(planned))
    for tid, uid, current, answer, explain in planned:
        logger.info("  [%s] %s: %s -> %s   (%s)", tid, uid, current, answer, explain)

    if not planned:
        conn.close()
        return 0

    if not args.apply:
        logger.info("\nСухой прогон. Для записи: DBCHECK_OK=1 ... --apply")
        conn.close()
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    backup_path = (Path(args.backup_dir)
                   / f"{stamp}-tsk770-oge-t4-solution-rules-backup.json")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    logger.info("Снимок solution_rules до правки: %s", backup_path)

    try:
        for tid, _uid, _current, answer, _explain in planned:
            cur.execute(
                """
                UPDATE tasks
                SET solution_rules = jsonb_set(
                        solution_rules,
                        '{short_answer,accepted_answers,0,value}',
                        to_jsonb(%s::text),
                        false)
                WHERE id = %s
                """,
                (answer, tid),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"[{tid}] UPDATE затронул {cur.rowcount} строк")

        # Верификация внутри той же транзакции — поштучно, не агрегатом.
        cur.execute(
            """
            SELECT id, solution_rules#>>'{short_answer,accepted_answers,0,value}' AS v
            FROM tasks WHERE id = ANY(%s) ORDER BY id
            """,
            ([p[0] for p in planned],),
        )
        actual = {r["id"]: r["v"] for r in cur.fetchall()}
        for tid, _uid, _current, answer, _explain in planned:
            if actual.get(tid) != answer:
                raise RuntimeError(
                    f"[{tid}] после UPDATE эталон {actual.get(tid)!r}, ждали {answer!r}"
                )
        conn.commit()
        logger.info("Записано и проверено: %d заданий", len(planned))
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
