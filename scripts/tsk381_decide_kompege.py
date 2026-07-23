"""tsk-381 — сведение канонов сложности для партии kompege (READ-ONLY).

Приоритет канонов (решение оператора 2026-07-23):
  1. авторская разметка «Уровень …» в ТГ-разборах канала @cyberguru_ege — главный;
  2. ручной вердикт оператора из прошлых задач — истина, не пересматривается;
  3. оценка внешнего сайта — здесь kompege, публичный API, поле ``difficulty``.
     Шкала подтверждена самим сайтом (селектор на kompege.ru): 0 = Базовый,
     1 = Средний, 2 = Сложный; 3 — четвёртая ступень («гроб»), к HARD.

Джойн ТГ-поста с заданием идёт по ЧИСЛОВОМУ id источника из шапки поста
(«Задание 13_12451 КЕГЭ. Уровень сложный»), а не по тексту — надёжный ключ,
подтверждён в tsk-354.

Скрипт ничего не пишет: печатает план правок с указанием, каким каноном
обосновано каждое значение, и отдельно — правки, требующие переноса между
базовым курсом и блоком «Сложные».

Запуск (после scripts/tsk381_collect_kompege_difficulty.py):
    python scripts/tsk381_decide_kompege.py
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Optional

import psycopg2

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk381.decide")

_LMS_ROOT = Path(__file__).resolve().parent.parent
_CB_ROOT = Path("D:/Work/ContentBackbone")

EASY, NORMAL, HARD = 2, 3, 4
DIFFICULTY_NAME = {1: "THEORY", 2: "EASY", 3: "NORMAL", 4: "HARD", 5: "PROJECT"}
LEVEL_TO_ID = {"простой": EASY, "лёгкий": EASY, "легкий": EASY, "средний": NORMAL, "сложный": HARD}
KOMPEGE_TO_ID = {0: EASY, 1: NORMAL, 2: HARD, 3: HARD}

BLOCK_MIN, BLOCK_MAX = 1379, 1403

# Канон 2: задания с ручным вердиктом оператора — не пересматривать.
# tsk-354 (изменённые + контрольные), tsk-361.
MANUAL_VERDICT: set[int] = {
    2059, 2116, 2262, 2352, 2386, 2720, 3792, 3796,  # tsk-354 применено
    3477, 3794, 3759,  # tsk-354/361 подтверждены без изменения
}


def _dsn(mcp_path: Path, server: str) -> str:
    """DSN сервера из .mcp.json. Секрет не логируется."""
    config = json.loads(mcp_path.read_text(encoding="utf-8"))
    for arg in config["mcpServers"][server]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://"):
            return arg.split("?")[0]
    raise RuntimeError(f"DSN для {server} не найден")


def load_tg_canon() -> dict[str, dict[str, Any]]:
    """Числовой id источника → уровень из ТГ-разбора (только КЕГЭ-посты)."""
    query = r"""
        WITH p AS (
          SELECT external_id,
            (regexp_match(body, '[Зз]адание\s*\d{1,2}[_\s]+(\d{3,7})'))[1] AS src_id,
            lower((regexp_match(body,
              'уровень[^а-яёa-z]{0,5}(простой|лёгкий|легкий|средний|сложный)', 'i'))[1]) AS lvl
          FROM content_hub.source_item
          WHERE source_id = '1701256430' AND body !~ '^\['
            AND body ~* '(КЕГЭ|КомЕГЭ|Ком\s*ЕГЭ)'
        )
        SELECT src_id, array_agg(DISTINCT lvl), array_agg(DISTINCT external_id)
        FROM p WHERE src_id IS NOT NULL AND lvl IS NOT NULL
        GROUP BY src_id
    """
    with psycopg2.connect(_dsn(_CB_ROOT / ".mcp.json", "content_backbone_prod_db")) as conn, conn.cursor() as cur:
        cur.execute(query)
        rows = cur.fetchall()

    canon: dict[str, dict[str, Any]] = {}
    for src_id, levels, posts in rows:
        ids = sorted({LEVEL_TO_ID[lv] for lv in levels if lv in LEVEL_TO_ID})
        canon[src_id] = {
            "levels": sorted(set(levels)),
            "difficulty_id": ids[0] if len(ids) == 1 else None,
            "posts": sorted(posts),
        }
    return canon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="tsk-381 сведение канонов kompege (read-only)")
    parser.add_argument("--kompege", default="out/tsk381_kompege.json")
    parser.add_argument("--out", default="out/tsk381_kompege_plan.json")
    args = parser.parse_args(argv)

    kompege_rows = json.loads(Path(args.kompege).read_text(encoding="utf-8"))["rows"]
    tg_canon = load_tg_canon()
    logger.info("ТГ-канон по числовому id (КЕГЭ): %d записей", len(tg_canon))

    plan: list[dict[str, Any]] = []
    stats = {
        "всего": len(kompege_rows), "ручной вердикт": 0, "канон ТГ": 0, "канон kompege": 0,
        "канона нет": 0, "конфликт внутри ТГ": 0, "правок": 0, "с переносом курса": 0,
    }

    for row in kompege_rows:
        task_id = row["id"]
        source_id = row["source_id"]
        current = row["difficulty_id"]
        tg = tg_canon.get(source_id or "")

        decided: Optional[int] = None
        canon = None
        evidence = None

        if task_id in MANUAL_VERDICT:
            stats["ручной вердикт"] += 1
            canon, evidence = "2 (ручной вердикт)", "не пересматривается"
        elif tg is not None and tg["difficulty_id"] is not None:
            decided, canon = tg["difficulty_id"], "1 (ТГ-разбор)"
            evidence = f"посты {','.join(tg['posts'])}: {'/'.join(tg['levels'])}"
            stats["канон ТГ"] += 1
        elif tg is not None:
            stats["конфликт внутри ТГ"] += 1
            canon = "1 (ТГ-разбор) — конфликт"
            evidence = f"посты {','.join(tg['posts'])}: {'/'.join(tg['levels'])}"
        elif row["kompege_difficulty"] is not None:
            decided = KOMPEGE_TO_ID[row["kompege_difficulty"]]
            canon = "3 (kompege)"
            evidence = f"difficulty={row['kompege_difficulty']} ({row['kompege_label']})"
            stats["канон kompege"] += 1
        else:
            stats["канона нет"] += 1

        in_block = BLOCK_MIN <= row["course_id"] <= BLOCK_MAX
        needs_move = decided is not None and ((decided == HARD) != in_block)
        entry = {
            **row, "canon": canon, "evidence": evidence, "decided_difficulty_id": decided,
            "changes": decided is not None and decided != current,
            "needs_course_move": needs_move,
        }
        if entry["changes"]:
            stats["правок"] += 1
            if needs_move:
                stats["с переносом курса"] += 1
        plan.append(entry)

    logger.info("")
    logger.info("=== Сводка по %d заданиям kompege ===", stats["всего"])
    for key, value in stats.items():
        logger.info("  %-22s %s", key, value)

    changes = [p for p in plan if p["changes"]]
    simple = [p for p in changes if not p["needs_course_move"]]
    moves = [p for p in changes if p["needs_course_move"]]

    logger.info("")
    logger.info("=== Правки без переноса курса (%d) ===", len(simple))
    for p in simple:
        logger.info(
            "  id=%-5s курс=%-5s %-6s -> %-6s  канон %s | %s",
            p["id"], p["course_id"], DIFFICULTY_NAME.get(p["difficulty_id"]),
            DIFFICULTY_NAME.get(p["decided_difficulty_id"]), p["canon"], p["evidence"],
        )

    logger.info("")
    logger.info("=== Правки С переносом между курсом и блоком «Сложные» (%d) ===", len(moves))
    for p in moves:
        logger.info(
            "  id=%-5s курс=%-5s %-6s -> %-6s  канон %s | %s",
            p["id"], p["course_id"], DIFFICULTY_NAME.get(p["difficulty_id"]),
            DIFFICULTY_NAME.get(p["decided_difficulty_id"]), p["canon"], p["evidence"],
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
