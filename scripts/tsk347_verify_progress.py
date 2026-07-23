# -*- coding: utf-8 -*-
"""tsk-347: поимённый снимок прогресса по HARD-заданиям дерева ЕГЭ (112).

ЗАЧЕМ
Перенос заданий в другой курс не должен обнулить прогресс и лимиты попыток
(tsk-264: попытки считаются по паре «корень + задание» через
`attempts.root_course_id`). Агрегатная сверка «было столько же, сколько стало»
уже один раз пропустила единичный сбой (урок tsk-317), поэтому снимок —
ПОСТРОЧНЫЙ: ученик × задание × состояние.

Состояние воспроизводит `learning_engine_service.compute_task_state` для корня
112: PASSED по последнему результату, BLOCKED_LIMIT по исчерпанному лимиту в
границах корня, иначе FAILED. Лимит — override -> tasks.max_attempts -> 3,
квизу принудительно 1.

Запуск (read-only, безопасно в любой момент):
  python scripts/tsk347_verify_progress.py --out reviews/evidence/tsk347-do.json
  python scripts/tsk347_verify_progress.py --out reviews/evidence/tsk347-posle.json
  python scripts/tsk347_verify_progress.py --sravnit reviews/evidence/tsk347-do.json \
      --out reviews/evidence/tsk347-posle.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]

ROOT_COURSE_ID = 112
HARD_DIFFICULTY_ID = 4
DEFAULT_MAX_ATTEMPTS = 3
PASS_THRESHOLD_RATIO = 0.5

# Задания берём по КЛАССИФИКАЦИИ (difficulty_id=4) в дереве корня 112, а не по
# списку курсов-источников: после переноса они лежат в других курсах того же
# дерева, и снимок обязан находить их в обоих состояниях мира.
SNAPSHOT_SQL = """
WITH RECURSIVE tree AS (
    SELECT $1::int AS course_id
    UNION
    SELECT cp.course_id FROM course_parents cp JOIN tree t ON cp.parent_course_id = t.course_id
),
hard AS (
    SELECT t.id, t.course_id, t.is_active, t.requirement_level, t.max_attempts,
           t.task_content->>'type' AS ttype
    FROM tasks t WHERE t.course_id IN (SELECT course_id FROM tree) AND t.difficulty_id = $2
),
posledniy AS (
    SELECT DISTINCT ON (tr.user_id, tr.task_id)
           tr.user_id, tr.task_id, tr.score, tr.max_score, tr.is_correct, tr.submitted_at
    FROM task_results tr
    JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
    WHERE tr.task_id IN (SELECT id FROM hard)
    ORDER BY tr.user_id, tr.task_id, tr.submitted_at DESC, tr.id DESC
),
popytki_kornya AS (
    SELECT tr.user_id, tr.task_id, count(*) AS used
    FROM task_results tr
    JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
    WHERE tr.task_id IN (SELECT id FROM hard) AND a.root_course_id = $1
    GROUP BY 1, 2
),
popytki_vse AS (
    SELECT tr.user_id, tr.task_id, count(*) AS used
    FROM task_results tr
    JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
    WHERE tr.task_id IN (SELECT id FROM hard)
    GROUP BY 1, 2
)
SELECT p.user_id, p.task_id, h.course_id, h.is_active, h.requirement_level,
       p.score, p.max_score, p.is_correct,
       COALESCE(pk.used, 0) AS used_root,
       pv.used AS used_all,
       CASE WHEN h.ttype IN ('SC_Qw', 'MC_Qw') THEN 1
            ELSE COALESCE(o.max_attempts_override, h.max_attempts, $3) END AS lim,
       (SELECT stp.status FROM student_task_progress stp
         WHERE stp.task_id = p.task_id AND stp.student_id = p.user_id) AS progress_status
FROM posledniy p
JOIN hard h ON h.id = p.task_id
LEFT JOIN popytki_kornya pk ON pk.user_id = p.user_id AND pk.task_id = p.task_id
LEFT JOIN popytki_vse pv ON pv.user_id = p.user_id AND pv.task_id = p.task_id
LEFT JOIN student_task_limit_override o ON o.task_id = p.task_id AND o.student_id = p.user_id
ORDER BY p.user_id, p.task_id
"""


def _dsn() -> str:
    """Прод-DSN learn: из окружения либо из .mcp.json."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                    dsn = arg
                    break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


def _sostoyanie(row: dict) -> str:
    """Состояние задания по правилам compute_task_state (корень 112)."""
    if row["progress_status"] == "skipped":
        return "SKIPPED"
    max_score = row["max_score"] or 0
    if max_score > 0 and (row["score"] or 0) / max_score >= PASS_THRESHOLD_RATIO:
        return "PASSED"
    if row["used_root"] >= row["lim"]:
        return "BLOCKED_LIMIT"
    return "FAILED"


async def main(out: str | None, sravnit: str | None) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(SNAPSHOT_SQL, ROOT_COURSE_ID, HARD_DIFFICULTY_ID, DEFAULT_MAX_ATTEMPTS)
    finally:
        await conn.close()

    snimok: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        kluch = f"{d['user_id']}:{d['task_id']}"
        snimok[kluch] = {
            "user_id": d["user_id"],
            "task_id": d["task_id"],
            "course_id": d["course_id"],
            "requirement_level": d["requirement_level"],
            "score": d["score"],
            "max_score": d["max_score"],
            "is_correct": d["is_correct"],
            "used_root": d["used_root"],
            "used_all": d["used_all"],
            "lim": d["lim"],
            "state": _sostoyanie(d),
        }

    po_uchenikam: dict[int, dict[str, int]] = {}
    for v in snimok.values():
        b = po_uchenikam.setdefault(v["user_id"], {})
        b[v["state"]] = b.get(v["state"], 0) + 1

    print(f"Записей ученик×задание: {len(snimok)}; учеников: {len(po_uchenikam)}")
    for uid in sorted(po_uchenikam):
        print(f"  ученик {uid}: " + ", ".join(f"{k}={v}" for k, v in sorted(po_uchenikam[uid].items())))

    if sravnit:
        bylo = json.loads(Path(sravnit).read_text(encoding="utf-8"))
        # Сверяем ВСЁ множество построчно: что пропало, что появилось, что
        # изменилось в состоянии/попытках/оценке. course_id меняться ОБЯЗАН.
        propalo = sorted(set(bylo) - set(snimok))
        poyavilos = sorted(set(snimok) - set(bylo))
        izmenilos: list[str] = []
        SLEDIM = ("state", "score", "max_score", "is_correct", "used_root", "used_all", "lim")
        for k in sorted(set(bylo) & set(snimok)):
            raznica = {p: (bylo[k][p], snimok[k][p]) for p in SLEDIM if bylo[k][p] != snimok[k][p]}
            if raznica:
                izmenilos.append(f"{k}: {raznica}")
        print("\nСверка с", sravnit)
        print(f"  пропало записей: {len(propalo)}" + (f" -> {propalo[:20]}" if propalo else ""))
        print(f"  появилось записей: {len(poyavilos)}" + (f" -> {poyavilos[:20]}" if poyavilos else ""))
        print(f"  изменилось по существу: {len(izmenilos)}")
        for s in izmenilos[:40]:
            print(f"    {s}")
        pereehalo = sum(1 for k in set(bylo) & set(snimok) if bylo[k]["course_id"] != snimok[k]["course_id"])
        print(f"  сменили курс (ожидаемо): {pereehalo}")
        itog = not propalo and not poyavilos and not izmenilos
        print(f"\n  ИТОГ: {'прогресс сохранён поимённо' if itog else 'ЕСТЬ РАСХОЖДЕНИЯ — разбирать'}")

    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(snimok, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nСнимок сохранён: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="tsk-347: поимённый снимок прогресса по HARD")
    ap.add_argument("--out", help="куда сохранить снимок JSON")
    ap.add_argument("--sravnit", help="снимок ДО для построчной сверки")
    args = ap.parse_args()
    asyncio.run(main(args.out, args.sravnit))
