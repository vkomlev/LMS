"""tsk-381 — канон сложности из ТГ-разборов для партий sdamgia / Поляков / Яндекс (READ-ONLY).

Канон 1 — авторская разметка «Уровень …» в постах канала @cyberguru_ege. Джойн с
заданием LMS идёт по ПАРЕ (вид источника, числовой id) из шапки поста
(«Задание 16_48437 РешуЕГЭ. Уровень сложный»), а не по тексту: числовой id —
надёжный ключ (подтверждён в tsk-354), а вид источника обязателен, потому что
один и тот же номер у разных сайтов означает разные задачи.

У этих трёх партий канон 3 недоступен или дорог: у «Решу ЕГЭ» градации сложности
нет вовсе, у Полякова это отметка `*` на странице, у Яндекса — только через
авторизованный API. Поэтому здесь берём то, что есть: канон 1.

Скрипт ничего не пишет. Запуск:
    python scripts/tsk381_decide_tg_canon.py
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Optional

import psycopg2

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk381.tg")

_LMS_ROOT = Path(__file__).resolve().parent.parent
_CB_ROOT = Path("D:/Work/ContentBackbone")

EASY, NORMAL, HARD = 2, 3, 4
DIFFICULTY_NAME = {1: "THEORY", 2: "EASY", 3: "NORMAL", 4: "HARD", 5: "PROJECT"}
LEVEL_TO_ID = {"простой": EASY, "лёгкий": EASY, "легкий": EASY, "средний": NORMAL, "сложный": HARD}
BLOCK_MIN, BLOCK_MAX = 1379, 1403

KINDS = ("sdamgia", "polyakov", "yandex")

# Канон 2: ручной вердикт оператора — не пересматривать (tsk-354, tsk-361).
MANUAL_VERDICT: set[int] = {
    2059, 2116, 2262, 2352, 2386, 2720, 3792, 3796, 3477, 3794, 3759,
}


def _dsn(mcp_path: Path, server: str) -> str:
    """DSN сервера из .mcp.json. Секрет не логируется."""
    config = json.loads(mcp_path.read_text(encoding="utf-8"))
    for arg in config["mcpServers"][server]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://"):
            return arg.split("?")[0]
    raise RuntimeError(f"DSN для {server} не найден")


def load_tg_canon() -> dict[tuple[str, str], dict[str, Any]]:
    """(вид источника, числовой id) → уровень из ТГ-разбора."""
    query = r"""
        WITH p AS (
          SELECT external_id,
            CASE WHEN body ~* 'Решу\s*ЕГЭ|РешуЕГЭ' THEN 'sdamgia'
                 WHEN body ~* 'КЕГЭ|КомЕГЭ|Ком\s*ЕГЭ' THEN 'kompege'
                 WHEN body ~* 'Поляков' THEN 'polyakov'
                 WHEN body ~* 'Яндекс' THEN 'yandex' END AS kind,
            (regexp_match(body, '[Зз]адание\s*\d{1,2}[_\s]+(\d{3,7})'))[1] AS src_id,
            lower((regexp_match(body,
              'уровень[^а-яёa-z]{0,5}(простой|лёгкий|легкий|средний|сложный)', 'i'))[1]) AS lvl
          FROM content_hub.source_item
          WHERE source_id = '1701256430' AND body !~ '^\['
        )
        SELECT kind, src_id, array_agg(DISTINCT lvl), array_agg(DISTINCT external_id)
        FROM p
        WHERE kind IS NOT NULL AND src_id IS NOT NULL AND lvl IS NOT NULL
        GROUP BY kind, src_id
    """
    with psycopg2.connect(_dsn(_CB_ROOT / ".mcp.json", "content_backbone_prod_db")) as conn, conn.cursor() as cur:
        cur.execute(query)
        rows = cur.fetchall()

    canon: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, src_id, levels, posts in rows:
        ids = sorted({LEVEL_TO_ID[lv] for lv in levels if lv in LEVEL_TO_ID})
        canon[(kind, src_id)] = {
            "levels": sorted(set(levels)),
            "difficulty_id": ids[0] if len(ids) == 1 else None,
            "posts": sorted(posts),
        }
    return canon


def load_tasks() -> list[dict[str, Any]]:
    """Активные задания LMS трёх партий с числовым id источника."""
    query = r"""
        SELECT id, external_uid, course_id, difficulty_id,
               CASE WHEN external_uid ILIKE '%sdamgia%' THEN 'sdamgia'
                    WHEN external_uid ILIKE '%polyakov%' THEN 'polyakov'
                    WHEN external_uid ILIKE '%yandex%' THEN 'yandex' END AS kind,
               (regexp_match(external_uid, '(\d+)$'))[1] AS source_id
        FROM tasks
        WHERE is_active AND (external_uid ILIKE '%sdamgia%'
                             OR external_uid ILIKE '%polyakov%'
                             OR external_uid ILIKE '%yandex%')
        ORDER BY id
    """
    with psycopg2.connect(_dsn(_LMS_ROOT / ".mcp.json", "learn_prod_db")) as conn, conn.cursor() as cur:
        cur.execute(query)
        return [
            {"id": r[0], "external_uid": r[1], "course_id": r[2],
             "difficulty_id": r[3], "kind": r[4], "source_id": r[5]}
            for r in cur.fetchall()
        ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="tsk-381 канон ТГ для sdamgia/Поляков/Яндекс")
    parser.add_argument("--out", default="out/tsk381_tg_canon_plan.json")
    args = parser.parse_args(argv)

    canon = load_tg_canon()
    tasks = load_tasks()
    logger.info("ТГ-канон по (вид, id): %d записей", len(canon))
    logger.info("активных заданий трёх партий: %d", len(tasks))

    plan: list[dict[str, Any]] = []
    stats = {k: 0 for k in ("ручной вердикт", "канон ТГ есть", "канона нет", "конфликт", "правок", "с переносом курса")}

    for task in tasks:
        key = (task["kind"], task["source_id"] or "")
        found = canon.get(key)
        decided: Optional[int] = None
        evidence = None

        if task["id"] in MANUAL_VERDICT:
            stats["ручной вердикт"] += 1
            evidence = "вердикт оператора, не пересматривается"
        elif found is None:
            stats["канона нет"] += 1
        elif found["difficulty_id"] is None:
            stats["конфликт"] += 1
            evidence = f"посты {','.join(found['posts'])}: {'/'.join(found['levels'])} — спор"
        else:
            stats["канон ТГ есть"] += 1
            decided = found["difficulty_id"]
            evidence = f"посты {','.join(found['posts'])}: {'/'.join(found['levels'])}"

        in_block = BLOCK_MIN <= task["course_id"] <= BLOCK_MAX
        needs_move = decided is not None and ((decided == HARD) != in_block)
        changes = decided is not None and decided != task["difficulty_id"]
        if changes:
            stats["правок"] += 1
            if needs_move:
                stats["с переносом курса"] += 1
        plan.append({
            **task, "decided_difficulty_id": decided, "evidence": evidence,
            "current_level": DIFFICULTY_NAME.get(task["difficulty_id"]),
            "changes": changes, "needs_course_move": needs_move,
        })

    logger.info("")
    for key, value in stats.items():
        logger.info("  %-20s %s", key, value)

    for label, subset in (
        ("Правки без переноса курса", [p for p in plan if p["changes"] and not p["needs_course_move"]]),
        ("Правки С переносом курса", [p for p in plan if p["changes"] and p["needs_course_move"]]),
    ):
        logger.info("")
        logger.info("=== %s (%d) ===", label, len(subset))
        for p in subset:
            logger.info(
                "  id=%-5s %-10s курс=%-5s %-6s -> %-6s | %s",
                p["id"], p["kind"], p["course_id"], p["current_level"],
                DIFFICULTY_NAME.get(p["decided_difficulty_id"]), p["evidence"],
            )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps({"task": "tsk-381", "stats": stats, "plan": plan},
                   ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("")
    logger.info("план: %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
