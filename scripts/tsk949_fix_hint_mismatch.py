# -*- coding: utf-8 -*-
"""tsk-949: снять с заданий 4106 и 4108 (курс 157) видео ЧУЖОЙ задачи.

ЧТО БЫЛО НЕ ТАК
У задания id=4106 «Точки внутри области по квадрату» (kompege 4717) в
hints_video стояли два ролика, дословно совпадающие с видео задания id=2170
«Площадь фигуры из повторов» (kompege 4742) — подтверждено двумя независимыми
постами в Telegram-канале (t.me/cyberguru_ege/726 и /709, оба подписаны
«Задание 6_4742», алгоритм и вопрос про площадь совпадают дословно с 2170,
а не с 4106). У задания id=4108 «Положительные точки внутри шестиугольника»
(kompege 4752) стоял ролик задания id=2322 «Точки внутри и вне фигур
Черепахи» (sdamgia 75243) — подтверждено собственным описанием VK-видео
(«Разбираем задание №6 (6_75243)»), а условие 4108 (шестиугольник, повороты
на 60°) не совпадает с условием 2322 (два прямоугольника, повороты на 90°).

ПЕРВОПРИЧИНА
На странице-источнике victor-komlev.ru/zadanie-6-ege-po-informatike-
ispolnitel-cherepaha/ у kompege 4717 и 4752 в списке «Задания для
подготовки» ссылки «Смотреть разбор» НЕТ вовсе — автор видео для них не
записывал. Импортёр wp_nav_import.py — «экстрактор ссылок, не парсер
задачи» (см. ContentBackbone docs/ai/ege-import-playbook.md) — при разборе
списка сместился на соседние строки (4742 и 75243) и приписал их ссылки
задачам 4717/4752. Тот же класс дефекта, что и в tsk-811 (методология и
образец скрипта — scripts/tsk811_fix_hint_layout.py), только смещение
позиционное, а не блоковая раскладка.

ЧТО ДЕЛАЕТСЯ
Донора (2170, 2322) не трогаем. У 4106/4108 снимается весь hints_video —
родного видео нет НИГДЕ (проверено: поиск по TG-каналу, ContentBackbone,
сама страница-источник) — после снятия у обоих заданий не остаётся ни одной
подсказки (`allow_empty: true` в плане, решение согласовано с оператором
15.09 — тот же принцип, что и в tsk-811 Шаг 2: неверная подсказка хуже
отсутствия). `has_hints` пересчитывается по факту (video=[] и text=[] ->
false).

Запуск: dry-run по умолчанию;
        DBCHECK_OK=1 python scripts/tsk949_fix_hint_mismatch.py --apply
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
DEFAULT_PLAN = project_root / "reviews" / "tsk949-cleanup.json"

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
                print(f"  #{it['task_id']} {it['external_uid']} «{(it.get('title') or '')[:44]}»")
                for u in it["before"]:
                    print(f"        СНЯТЬ {u}")
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
