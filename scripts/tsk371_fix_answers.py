# -*- coding: utf-8 -*-
"""tsk-371, шаг 2: записать верные ответы там, где импорт из sdamgia их испортил.

ЧТО ЧИНИТ
Составной ответ «Решу ЕГЭ» пишется через собственный разделитель (`4229&23` — два числа).
При импорте такой ответ местами превращался в мусор (у 3791 в LMS осталось `ы`) или терял
вторую часть. Скрипт берёт ответ источника из отчёта шага 1
(`tsk371_audit_sdamgia_answers.py`) и записывает его в принятой в LMS форме — числа через
пробел.

ЗАЩИТЫ
  * чинятся только явно перечисленные `--ids` — список составляется человеком по отчёту,
    а не «всё, что не совпало»: расхождение ответа бывает и признаком того, что по ID лежит
    ЧУЖАЯ задача (обжиг tsk-369: ID 27360 давал «правильный» ответ от другого задания);
  * для каждого id проверяется, что сверка условия с источником прошла (`prose_ok`), иначе
    запись отклоняется;
  * прежние значения сохраняются в бэкап до записи;
  * dry-run по умолчанию, `--apply` при DBCHECK_OK=1, построчная проверка после COMMIT.

Запуск:
  python scripts/tsk371_fix_answers.py --audit <audit.json> --ids 3791,... --backup <файл> [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk369_collect import dsn  # noqa: E402

SHORT_ANSWER_TEMPLATE = {"regex": None, "use_regex": False,
                         "normalization": ["trim", "lower"]}


def normalize_answer(src: str) -> str:
    """Ответ источника в форму LMS: разделители источника (`&`, `;`, `,`) → пробел."""
    return " ".join(t for t in re.split(r"[^0-9A-Za-zА-Яа-яЁё]+", (src or "").strip()) if t)


async def main(audit_path: Path, ids: list[int], backup_path: Path, apply: bool) -> None:
    audit = {r["id"]: r for r in json.loads(audit_path.read_text(encoding="utf-8"))}
    plan = {}
    for tid in ids:
        rec = audit.get(tid)
        if rec is None:
            raise RuntimeError(f"id={tid} нет в отчёте шага 1")
        if not rec.get("prose_ok"):
            raise RuntimeError(
                f"id={tid}: условие не сошлось с источником — сначала убедиться, что по "
                f"ID {rec.get('source_id')} лежит та же задача")
        value = normalize_answer(rec.get("answer_src"))
        if not value:
            raise RuntimeError(f"id={tid}: у источника нет ответа")
        plan[tid] = value

    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = {r["id"]: r for r in await conn.fetch(
            "SELECT id, external_uid, is_active, solution_rules, "
            "       task_content->>'answer_raw' AS answer_raw "
            "FROM tasks WHERE id = ANY($1::int[])", list(plan))}
        missing = sorted(set(plan) - set(rows))
        if missing:
            raise RuntimeError(f"не нашёл заданий: {missing}")
        inactive = [i for i in plan if not rows[i]["is_active"]]
        if inactive:
            raise RuntimeError(f"задания неактивны: {inactive}")

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            [{"id": i, "external_uid": rows[i]["external_uid"],
              "solution_rules": json.loads(rows[i]["solution_rules"] or "null"),
              "answer_raw": rows[i]["answer_raw"]} for i in sorted(plan)],
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Бэкап прежних правил: {backup_path}")

        for i in sorted(plan):
            sr = json.loads(rows[i]["solution_rules"] or "{}")
            old = ((sr.get("short_answer") or {}).get("accepted_answers") or [{}])[0].get("value")
            print(f"  id={i} {rows[i]['external_uid']} sdamgia:{audit[i]['source_id']}: "
                  f"{old!r} → {plan[i]!r} (у источника {audit[i]['answer_src']!r})")

        async with conn.transaction():
            for i, value in plan.items():
                sr = json.loads(rows[i]["solution_rules"] or "{}")
                sr["short_answer"] = {**SHORT_ANSWER_TEMPLATE,
                                      "accepted_answers": [{"score": sr.get("max_score", 1),
                                                            "value": value}]}
                sr["manual_review_required"] = False
                sr["auto_check"] = True
                await conn.execute(
                    "UPDATE tasks SET solution_rules = $2::jsonb, "
                    "  task_content = jsonb_set(task_content, '{answer_raw}', to_jsonb($3::text)) "
                    "WHERE id = $1",
                    i, json.dumps(sr, ensure_ascii=False), value)

            check = {r["id"]: r["ans"] for r in await conn.fetch(
                "SELECT id, solution_rules#>>'{short_answer,accepted_answers,0,value}' AS ans "
                "FROM tasks WHERE id = ANY($1::int[])", list(plan))}
            bad = [i for i, v in plan.items() if check.get(i) != v]
            if bad:
                raise AssertionError(f"проверка внутри транзакции не прошла: {bad}")
            print(f"Внутри транзакции: обновлено и проверено {len(plan)} заданий.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after = await conn.fetch(
            "SELECT id, solution_rules#>>'{short_answer,accepted_answers,0,value}' AS ans, "
            "       (solution_rules->>'manual_review_required')::bool AS manual, "
            "       task_content->>'answer_raw' AS raw "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id", list(plan))
        for r in after:
            print(f"  id={r['id']}: ответ={r['ans']!r} answer_raw={r['raw']!r} "
                  f"ручная_проверка={r['manual']}")
        problems = [r["id"] for r in after if r["ans"] != plan[r["id"]]]
        if problems:
            print(f"  ПРОБЛЕМНЫЕ: {problems}")
            sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", required=True)
    ap.add_argument("--ids", required=True, help="id заданий через запятую")
    ap.add_argument("--backup", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    try:
        asyncio.run(main(Path(a.audit), [int(x) for x in a.ids.split(",")],
                         Path(a.backup), a.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
