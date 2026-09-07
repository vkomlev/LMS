# -*- coding: utf-8 -*-
"""tsk-811: снять с вводных заданий разбор ЧУЖОЙ задачи и поставить свой первым.

ЧТО БЫЛО НЕ ТАК
Видеоподсказки у авторских вводных заданий блоков 24 и 25 расставлены пачками:
один ролик повешен на ряд соседних заданий (`24_2` висел на vvod:01–09, `25_5`
на 05–09). Из-за этого ученик на задании «Максимальная цепочка по шаблону XYZ»
открывал первую подсказку и видел разбор ДРУГОЙ задачи — про серию букв Z.
Автор так не публиковал: каждый ролик выложен в канал с текстом ровно одного
задания, пачки появились при переносе подсказок.

ЧТО СНИМАЕТСЯ, А ЧТО ОСТАЁТСЯ
Снимается только «чужой авторский» разбор — ролик с авторским кодом того же
блока, чей номер не равен номеру этого задания. Остаются:
  * собственный разбор задания (ставится ПЕРВЫМ — ради этого всё и делается);
  * разборы настоящих заданий ЕГЭ (код пятизначный — id kompege/sdamgia) и
    урочные ролики без кода: они не притворяются разбором этой задачи, а у
    части заданий других подсказок нет вовсе.

ДВА ШАГА, ДВА ПЛАНА
`reviews/tsk811-cleanup-step1.json` — задания, у которых после снятия остаётся
хотя бы одна подсказка. Безопасно: ничего не пропадает совсем.
`reviews/tsk811-cleanup-step2.json` — задания, где чужой разбор единственный, и
после снятия подсказок не остаётся. Такой план обязан нести `allow_empty: true`
и применяется только по решению оператора (принято 07.09). Отбор туда — по
направлению авторской цепочки: разбор задания, которое идёт РАНЬШЕ, не может
содержать решения этого задания и снимается; разбор более ПОЗДНЕГО задания
остаётся (запись идёт по всей цепочке и по ходу показывает ранние шаги).

`has_hints` пересчитывается по факту: если после снятия не осталось ни видео,
ни текстовых подсказок, признак становится false. Иначе список заданий у
методиста показывал бы подсказку там, где её нет.

Соответствие «ролик -> задание»: код `N_M` -> `lms:c{курс}:vvod:{M}` (ключ
найден в tsk-808 и сверен по тексту), плюс две пары со сдвигом нумерации,
принятые оператором: `25_8` -> задание 6, `25_16` -> задание 17.

Запуск: dry-run по умолчанию;
        `DBCHECK_OK=1 python scripts/tsk811_fix_hint_layout.py --plan <файл> --apply`
"""
from __future__ import annotations

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
DEFAULT_PLAN = project_root / "reviews" / "tsk811-cleanup-step1.json"

SELECT_BEFORE = """
SELECT id, external_uid, is_active,
       COALESCE(task_content->'hints_video', '[]'::jsonb)::text AS hv,
       jsonb_array_length(COALESCE(task_content->'hints_text', '[]'::jsonb)) AS n_text,
       md5(COALESCE(task_content->>'stem', '')) AS stem_md5,
       md5(COALESCE(task_content->>'hints_text', '')) AS hints_text_md5,
       md5(COALESCE(solution_rules::text, '')) AS solrules_md5,
       order_position
FROM tasks WHERE id = ANY($1::int[])
"""

UPDATE_ONE = """
UPDATE tasks
SET task_content = task_content || jsonb_build_object('hints_video', $2::jsonb, 'has_hints', $3::bool)
WHERE id = $1
  AND is_active
  AND COALESCE(task_content->'hints_video', '[]'::jsonb)::text = $4
"""

SELECT_AFTER = """
SELECT id, COALESCE(task_content->'hints_video', '[]'::jsonb)::text AS hv,
       (task_content->>'has_hints')::bool AS has_hints,
       COALESCE(task_content->'hints_text', '[]'::jsonb)::text AS ht,
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
    doc = json.loads(plan_path.read_text(encoding="utf-8"))
    plan, allow_empty = doc["items"], bool(doc.get("allow_empty"))
    print(f"План: {plan_path.name}" + ("  (разрешено оставлять задания без подсказок)"
                                       if allow_empty else ""))
    for it in plan:
        if not it["after"] and not allow_empty:
            raise RuntimeError(
                f"#{it['task_id']}: план оставляет задание без подсказок, "
                "а allow_empty в файле не выставлен — см. докстринг")
        if set(it["after"]) | set(it["drop"]) != set(it["before"]):
            raise RuntimeError(f"#{it['task_id']}: after+drop != before — план несогласован")
    ids = [it["task_id"] for it in plan]
    print(f"План: {len(plan)} заданий, снимается {sum(len(it['drop']) for it in plan)} ссылок")

    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            before = {r["id"]: r for r in await conn.fetch(SELECT_BEFORE, ids)}
            todo = []
            for it in plan:
                row = before.get(it["task_id"])
                if row is None:
                    raise AssertionError(f"нет задания #{it['task_id']}")
                if row["external_uid"] != it["external_uid"]:
                    raise AssertionError(
                        f"#{it['task_id']}: uid {row['external_uid']!r} != {it['external_uid']!r}")
                if not row["is_active"]:
                    print(f"  пропуск #{it['task_id']}: задание неактивно")
                    continue
                current = json.loads(row["hv"])
                if current == it["after"]:
                    print(f"  пропуск #{it['task_id']}: уже в нужном виде")
                    continue
                if current != it["before"]:
                    raise AssertionError(
                        f"#{it['task_id']}: подсказки изменились с момента построения плана\n"
                        f"  в базе:  {current}\n  в плане: {it['before']}")
                todo.append((it, row))

            print(f"\nБудет изменено: {len(todo)} заданий")
            for it, row in todo:
                print(f"  #{it['task_id']} {it['external_uid']} «{(it['title'] or '')[:44]}»")
                first = it["after"][0] if it["after"] else None
                for u in it["before"]:
                    tag = "СНЯТЬ  " if u in it["drop"] else ("ПЕРВЫМ " if u == first else "оставить")
                    print(f"        {tag} {u}")
                print(f"        -> порядок: {it['after'] or 'ПУСТО (подсказок не остаётся)'}")
            if not todo:
                raise RuntimeError("DRY-RUN: нечего применять")

            updated = 0
            for it, row in todo:
                has_hints = bool(it["after"]) or row["n_text"] > 0
                res = await conn.execute(
                    UPDATE_ONE, it["task_id"], json.dumps(it["after"], ensure_ascii=False),
                    has_hints, row["hv"])
                updated += int(res.split()[-1])
            print(f"\nUPDATE затронул строк: {updated} (ожидали {len(todo)})")
            if updated != len(todo):
                raise AssertionError(
                    f"обновлено {updated} != {len(todo)} — подсказки менялись параллельно, откатываю")

            after_rows = {r["id"]: r for r in await conn.fetch(SELECT_AFTER, ids)}
            for it, row in todo:
                a = after_rows[it["task_id"]]
                got = json.loads(a["hv"])
                if got != it["after"]:
                    raise AssertionError(f"#{it['task_id']}: hints_video={got} != {it['after']}")
                if not got and not allow_empty:
                    raise AssertionError(f"#{it['task_id']}: подсказки опустели — недопустимо")
                expect_flag = bool(got) or json.loads(a["ht"]) != []
                if a["has_hints"] is not expect_flag:
                    raise AssertionError(
                        f"#{it['task_id']}: has_hints={a['has_hints']}, ожидали {expect_flag}")
                for field in ("stem_md5", "hints_text_md5", "solrules_md5"):
                    if a[field] != row[field]:
                        raise AssertionError(f"#{it['task_id']}: изменилось {field} — недопустимо")
                if a["order_position"] != row["order_position"]:
                    raise AssertionError(f"#{it['task_id']}: сдвинулся order_position — откатываю")
            print(f"Верификация: у всех {len(todo)} список подсказок совпал с планом, "
                  "признак has_hints пересчитан по факту, условие, текстовые подсказки, "
                  "правило проверки и позиция не изменились.")
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
