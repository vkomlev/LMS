# -*- coding: utf-8 -*-
"""tsk-350: выбор канонического задания в группе и план деактивации.

Критерий канона (по убыванию веса):
  1. Прогресс учеников — канон обязан быть тем экземпляром, который зачтён
     наибольшему числу учеников. Иначе ученик теряет видимую отметку.
  2. Основной курс важнее подкурса «Сложные»: там requirement_level=required,
     задание идёт в зачёт курса, а в «Сложных» — recommended (вне зачёта).
  3. Чистота условия по источнику: каталожный импорт (ext:d4) > авторская
     страница сайта (wp_nav) > повторный калибровочный прогон (ext:calib)
     > пост-разбор в Telegram (tg:ege, crylov) — у последнего в условии
     живёт преамбула разбора («уровень средний», «решаем через регулярки»).
  4. Есть провенанс сложности (tsk-381/382) — задание откалибровано.
  5. Есть приложенные файлы.
  6. Меньший id — заведено раньше.
"""
from __future__ import annotations

import json
from pathlib import Path

from detect import find_groups

HERE = Path(__file__).parent
PASS_FILE = HERE / "passes.json"   # {task_id: [user_id, ...]} — зачтённые


def source_rank(row: dict) -> int:
    uid = row["external_uid"] or ""
    if uid.startswith("ext:d4:"):
        return 0
    if uid.startswith("wp_nav:"):
        return 1
    if uid.startswith("ext:calib:"):
        return 2
    if uid.startswith("tg:ege:") or uid.startswith("crylov:"):
        return 3
    return 4


def is_hard(row: dict) -> bool:
    return (row["course_uid"] or "").startswith("lms:tsk347:hard:")


def build_plan() -> dict:
    passes: dict[str, list[int]] = json.loads(PASS_FILE.read_text(encoding="utf-8"))
    groups, weak = find_groups()

    plan = []
    conflicts = []
    for g in groups:
        for r in g["members"]:
            r["passers"] = set(passes.get(str(r["id"]), []))
        best = sorted(
            g["members"],
            key=lambda r: (
                -len(r["passers"]),
                is_hard(r),
                source_rank(r),
                0 if r.get("difficulty_provenance") else 1,
                0 if r["files"] else 1,
                r["id"],
            ),
        )[0]
        losers = [r for r in g["members"] if r["id"] != best["id"]]
        # кто теряет отметку: зачтено по скрываемому, но не по канону
        lost = sorted(
            {u for r in losers for u in r["passers"]} - best["passers"]
        )
        rec = {
            "theme": g["theme"],
            "keep": best["id"],
            "keep_uid": best["external_uid"],
            "keep_course": best["course_id"],
            "hide": [r["id"] for r in losers],
            "hide_uids": [r["external_uid"] for r in losers],
            "lost_students": lost,
        }
        plan.append(rec)
        if lost:
            conflicts.append(rec)
    return {"plan": plan, "conflicts": conflicts, "weak": weak}


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    res = build_plan()
    hide = [i for r in res["plan"] for i in r["hide"]]
    print(f"групп: {len(res['plan'])}, к скрытию заданий: {len(hide)}")
    print(f"групп с потерей отметки у ученика: {len(res['conflicts'])}")
    for c in res["conflicts"][:20]:
        print("  ", c["keep"], "<-", c["hide"], "теряют:", c["lost_students"])
    (HERE / "plan.json").write_text(
        json.dumps(res["plan"], ensure_ascii=False, indent=1), encoding="utf-8"
    )
