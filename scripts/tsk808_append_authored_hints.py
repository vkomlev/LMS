# -*- coding: utf-8 -*-
"""tsk-808 (вторая волна): довесить авторские видеоразборы на «свои» задания.

ЧЕМ ОТЛИЧАЕТСЯ ОТ tsk808_apply_video_hints.py
Тот пишет только там, где `hints_video` ПУСТ. Здесь наоборот: у всех целей
подсказки уже есть — просто чужие. Раскладка в блоках 24 и 25 оказалась
блоковой: один ролик повешен на пачку соседних заданий (`24_2` висит на
vvod:01–09), поэтому у задания «Максимальная цепочка по шаблону XYZ» стоит
разбор «серии букв Z», а собственный разбор `24_6` не прикреплён нигде.

Поэтому режим строго ДОБАВЛЯЮЩИЙ: ссылка дописывается в конец, существующие
сохраняются, порядок не меняется. Снятие чужих разборов — отдельная задача
(решение оператора 07.09): это не добавление, и цена ошибки другая.

КЛЮЧ СОПОСТАВЛЕНИЯ — НЕ ТЕКСТ
Авторские вспомогательные задания живут под `lms:c{курс}:vvod:{NN}`, и NN
совпадает со вторым числом кода ролика (`24_6` -> `vvod:06`). Текст служит
проверкой пары, а не способом её найти: сопоставление по одному тексту в этом
классе задач ненадёжно (замер в tsk808_build_video_hint_plan.py). Три пары
приняты со сдвигом номера после ручной сверки с оператором — они помечены в
`reviews/tsk808-authored-hints.json`.

Запуск: dry-run по умолчанию;
        `DBCHECK_OK=1 python scripts/tsk808_append_authored_hints.py [--plan <файл>] --apply`
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = project_root / "reviews" / "tsk808-authored-hints.json"

SELECT_BEFORE = """
SELECT id, external_uid, is_active,
       COALESCE(task_content->'hints_video', '[]'::jsonb)::text AS hv,
       md5(COALESCE(task_content->>'stem', '')) AS stem_md5,
       md5(COALESCE(task_content->>'hints_text', '')) AS hints_text_md5,
       md5(COALESCE(solution_rules::text, '')) AS solrules_md5,
       order_position
FROM tasks WHERE id = ANY($1::int[])
"""

UPDATE_ONE = """
UPDATE tasks
SET task_content = task_content || jsonb_build_object('hints_video', $2::jsonb, 'has_hints', true)
WHERE id = $1
  AND is_active
  AND COALESCE(task_content->'hints_video', '[]'::jsonb)::text = $3
"""

SELECT_AFTER = """
SELECT id, COALESCE(task_content->'hints_video', '[]'::jsonb)::text AS hv,
       (task_content->>'has_hints')::bool AS has_hints,
       md5(COALESCE(task_content->>'stem', '')) AS stem_md5,
       md5(COALESCE(task_content->>'hints_text', '')) AS hints_text_md5,
       md5(COALESCE(solution_rules::text, '')) AS solrules_md5,
       order_position
FROM tasks WHERE id = ANY($1::int[])
"""


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        for arg in cfg["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно.")
    return dsn


async def main(apply: bool, plan_path: Path) -> None:
    print(f"План: {plan_path.name}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))["items"]
    # у одного задания может быть два разбора (25_6 и 25_8 — оба про задание 6)
    per_task: dict[int, list[dict]] = defaultdict(list)
    for it in plan:
        per_task[it["task_id"]].append(it)
    ids = sorted(per_task)
    print(f"План: {len(plan)} ссылок на {len(ids)} заданий")

    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            before = {r["id"]: r for r in await conn.fetch(SELECT_BEFORE, ids)}
            missing = [i for i in ids if i not in before]
            if missing:
                raise AssertionError(f"нет таких заданий: {missing}")

            todo = []
            for tid in ids:
                row = before[tid]
                if not row["is_active"]:
                    print(f"  пропуск #{tid}: задание неактивно")
                    continue
                if row["external_uid"] != per_task[tid][0]["external_uid"]:
                    raise AssertionError(
                        f"#{tid}: uid в базе {row['external_uid']!r} != {per_task[tid][0]['external_uid']!r} "
                        "— план построен на другой версии данных")
                current = json.loads(row["hv"])
                add = [it["video"] for it in per_task[tid] if it["video"] not in current]
                if not add:
                    print(f"  пропуск #{tid} ({row['external_uid']}): все ссылки уже стоят")
                    continue
                todo.append((tid, row, current, current + add))

            print(f"\nБудет дописано: {len(todo)} заданий")
            for tid, row, current, after in todo:
                codes = ", ".join(it["code"] for it in per_task[tid])
                print(f"  #{tid} {row['external_uid']} ({codes}): было {len(current)} -> станет {len(after)}")
                for u in current:
                    print(f"        (было)      {u}")
                for u in after[len(current):]:
                    print(f"        + дописать  {u}")
            if not todo:
                raise RuntimeError("DRY-RUN: нечего применять")

            updated = 0
            for tid, row, _current, after in todo:
                res = await conn.execute(
                    UPDATE_ONE, tid, json.dumps(after, ensure_ascii=False), row["hv"])
                updated += int(res.split()[-1])
            print(f"\nUPDATE затронул строк: {updated} (ожидали {len(todo)})")
            if updated != len(todo):
                raise AssertionError(
                    f"обновлено {updated} != {len(todo)} — подсказки менялись параллельно, откатываю")

            after_rows = {r["id"]: r for r in await conn.fetch(SELECT_AFTER, ids)}
            for tid, row, current, expected in todo:
                a = after_rows[tid]
                got = json.loads(a["hv"])
                if got != expected:
                    raise AssertionError(f"#{tid}: hints_video={got} != {expected}")
                if got[:len(current)] != current:
                    raise AssertionError(f"#{tid}: прежние ссылки сдвинулись или пропали")
                if a["has_hints"] is not True:
                    raise AssertionError(f"#{tid}: has_hints={a['has_hints']}")
                for field in ("stem_md5", "hints_text_md5", "solrules_md5"):
                    if a[field] != row[field]:
                        raise AssertionError(f"#{tid}: изменилось {field} — недопустимо")
                if a["order_position"] != row["order_position"]:
                    raise AssertionError(f"#{tid}: сдвинулся order_position — откатываю")
            print(f"Верификация: у всех {len(todo)} прежние ссылки на месте и в том же порядке, "
                  "новые дописаны в конец; условие, текстовые подсказки, правило проверки "
                  "и позиция не изменились.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    argv = sys.argv[1:]
    path = DEFAULT_PLAN
    if "--plan" in argv:
        path = Path(argv[argv.index("--plan") + 1])
        if not path.is_absolute():
            path = project_root / path
    try:
        asyncio.run(main("--apply" in argv, path))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
