# -*- coding: utf-8 -*-
"""tsk-414 (продолжение, решение оператора 2026-07-26): переместить теорию
"Форматирование строк" (материал 264) и видео "Форматирование строк" (материал 483)
курса 108 "Работа со строками" в конец занятия — непосредственно перед материалом
266 "Модульstring", как явно попросила QA: "Переместить теорию по форматированию
строк и видео по форматированию строк в конец занятия, до модуля string".

Видео "Строковые методы и функции" (482), которое сейчас стоит между 483 и 266,
сдвигается перед парой 264/483 (не было явно указано в письме куда девать, но
раз пара 264+483 должна стоять НЕПОСРЕДСТВЕННО перед 266 — 482 не может остаться
между ними).

НЕ выполнено в этом же заходе (сознательно, без гадания): ещё два пункта письма —
"Переместить блок с видео после видео «создание строк: одинарные/двойные/...»" и
повторное "Переместить блок с видео" — не называют, КАКОЙ блок видео перемещать;
письмо само отсылает к "определённой картине", которая была на скриншоте в
оригинальном письме и не сохранилась при извлечении текста. Без этого скриншота
исполнение — гадание, которое рискует сделать порядок хуже, а не лучше (урок
tsk-261 A8: отсутствие доказательства ≠ доказательство отсутствия, лучше спросить
скриншот, чем закрыть пункт наугад). Остаётся на решение оператора.

Запуск: dry-run по умолчанию;
  python scripts/tsk414_reorder_material_264_483_before_string_module.py
  DBCHECK_OK=1 python scripts/tsk414_reorder_material_264_483_before_string_module.py --apply
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
COURSE_ID = 108

# Новая последовательность (id -> new_position), 1..18, без пропусков.
NEW_ORDER = [
    259, 476, 260, 475, 261, 477, 263, 262, 478,
    479, 480, 474, 481, 482, 264, 483, 266, 473,
]


def _dsn() -> str:
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


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id, order_position, title FROM materials "
                "WHERE course_id = $1 AND is_active = true ORDER BY order_position",
                COURSE_ID,
            )
            current_ids = [r["id"] for r in rows]
            assert set(current_ids) == set(NEW_ORDER), (
                f"состав материалов курса {COURSE_ID} изменился с момента разведки: "
                f"было {sorted(NEW_ORDER)}, сейчас {sorted(current_ids)}"
            )
            by_id = {r["id"]: r for r in rows}

            plan = []
            for new_pos, mid in enumerate(NEW_ORDER, start=1):
                old_pos = by_id[mid]["order_position"]
                if old_pos != new_pos:
                    plan.append((mid, by_id[mid]["title"], old_pos, new_pos))

            print(f"--- курс {COURSE_ID}: {len(plan)} материалов меняют order_position ---")
            for mid, title, old, new in plan:
                print(f"  id={mid} ({title}): {old} -> {new}")

            if apply:
                await conn.execute("SELECT set_config('app.skip_material_order_trigger', 'true', true)")
                for mid, _title, _old, new in plan:
                    await conn.execute(
                        "UPDATE materials SET order_position = $1 WHERE id = $2",
                        new, mid,
                    )
                await conn.execute("SELECT set_config('app.skip_material_order_trigger', 'false', true)")

                dup = await conn.fetchval(
                    "SELECT count(*) FROM ("
                    "  SELECT order_position FROM materials WHERE course_id=$1 AND is_active=true"
                    "  GROUP BY order_position HAVING count(*) > 1"
                    ") d",
                    COURSE_ID,
                )
                if dup:
                    raise AssertionError(f"курс {COURSE_ID}: {dup} дублирующихся order_position после апдейта")
                for mid, _title, _old, new in plan:
                    actual = await conn.fetchval("SELECT order_position FROM materials WHERE id=$1", mid)
                    if actual != new:
                        raise AssertionError(f"id={mid}: после UPDATE order_position={actual}, ожидалось {new}")
                print(f"\nВерификация внутри транзакции: OK, {len(plan)} строк, дублей нет.")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
    except AssertionError as exc:
        print(f"\nОШИБКА ПРОВЕРКИ: {exc}")
        sys.exit(1)
