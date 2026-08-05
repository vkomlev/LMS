# -*- coding: utf-8 -*-
"""tsk-412: срезать хвост-текст заданий из материалов 314/316/317 курса 165
после переноса тех же 18 заданий в `tasks` (см. tsk412_import_turtle_tasks.py).

ПОЧЕМУ НЕ ПЕРЕИСПОЛЬЗУЕТСЯ trim_wp_artifact_tail_tsk411.py
Тот скрипт классифицирует материал по регэкспу заголовков /Вопрос|Задани|Задач/i
и требует хотя бы одного НЕ-хвостового заголовка перед хвостом (иначе — другая
ветка алгоритма). Для этих трёх материалов это ломается на конкретных данных:
  - материал 314 не имеет заголовков h2/h3/h4 в теории вообще (она оформлена
    как <ol><li><strong>...</strong>) — тестовый прогон подтвердил, что общий
    алгоритм это переживает, НО:
  - материал 316 содержит легитимные теоретические заголовки «Задание клавиш:»
    и «Задание клавиши по коду:» (про ИМЕНА клавиш для onkey(), не про
    упражнения) — оба матчат /Задани/i, что тот скрипт корректно ловит как
    IRREGULAR (earlier_tail внутри сохраняемой части) и просто отказывается
    резать. Это безопасный отказ, не порча данных, но и не готовое решение.

Поэтому — точечный скрипт с ЯВНО заданной строкой-маркером хвоста для каждого
из 3 материалов (не общий классификатор): маркер найден и сверен вручную по
реальному содержимому (см. tsk-412), проверяется на ЕДИНСТВЕННОСТЬ вхождения
перед резкой — если маркер задвоился или пропал (контент поменялся другим
чипом) — IRREGULAR, ничего не режется.

Запуск: dry-run по умолчанию (ничего не пишет, всегда ROLLBACK);
  PYTHONPATH=. python scripts/tsk412_trim_material_tail.py
  PYTHONPATH=. DBCHECK_OK=1 python scripts/tsk412_trim_material_tail.py --apply
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

# (material_id, маркер начала хвоста — уникальная подстрока заголовка)
MATERIAL_TAIL_MARKERS = [
    (314, "<h3>Задания на закрепление темы</h3>"),
    (316, "<h3>Задания для тренировки</h3>"),
    (317, "<h3>Задания на создание анимации</h3>"),
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


def classify(text: str, marker: str) -> dict:
    count = text.count(marker)
    if count != 1:
        return {"status": "IRREGULAR", "reason": f"маркер встречается {count} раз(а), ожидался 1"}

    cut_pos = text.index(marker)
    kept = text[:cut_pos].rstrip()

    if len(kept) < 150 or len(kept) >= len(text):
        return {"status": "IRREGULAR", "reason": f"kept слишком короткий/некорректный ({len(kept)} симв.)"}

    return {"status": "CLEAN", "cut_pos": cut_pos, "kept": kept}


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            ids = [m[0] for m in MATERIAL_TAIL_MARKERS]
            rows = await conn.fetch(
                "SELECT id, course_id, title, is_active, content FROM materials "
                "WHERE id = ANY($1::int[]) ORDER BY id",
                ids,
            )
            if len(rows) != len(ids):
                raise AssertionError(f"нашлось {len(rows)} из {len(ids)}")
            by_id = {r["id"]: r for r in rows}

            print("=" * 78)
            print(f"tsk-412 · срез хвоста материалов 314/316/317 курса 165 · "
                  f"{'ПРИМЕНЕНИЕ' if apply else 'DRY-RUN'}")
            print("=" * 78)

            clean = []
            for material_id, marker in MATERIAL_TAIL_MARKERS:
                r = by_id[material_id]
                content = json.loads(r["content"]) if isinstance(r["content"], str) else dict(r["content"])
                text = content.get("text", "")
                result = classify(text, marker)
                result.update(id=material_id, title=r["title"], is_active=r["is_active"],
                               content=content, orig_len=len(text))
                status = result["status"]
                if status == "CLEAN":
                    removed = result["orig_len"] - len(result["kept"])
                    print(f"id={material_id:>4} «{r['title'][:40]}» CLEAN "
                          f"было={result['orig_len']} станет={len(result['kept'])} (-{removed})")
                    clean.append(result)
                else:
                    print(f"id={material_id:>4} «{r['title'][:40]}» IRREGULAR: {result['reason']}")

            if len(clean) != len(MATERIAL_TAIL_MARKERS):
                raise RuntimeError(
                    f"{len(MATERIAL_TAIL_MARKERS) - len(clean)} материал(ов) IRREGULAR — "
                    "резать нечего, проверьте вручную."
                )

            if not apply:
                print("\nDRY-RUN: ничего не записано. Повтор с --apply.")
                return

            for r in clean:
                new_content = dict(r["content"])
                new_content["text"] = r["kept"]
                await conn.execute(
                    "UPDATE materials SET content = $1::jsonb, updated_at = now() WHERE id = $2",
                    json.dumps(new_content, ensure_ascii=False), r["id"],
                )

            after = await conn.fetch(
                "SELECT id, length(content->>'text') AS len FROM materials WHERE id = ANY($1::int[])",
                [r["id"] for r in clean],
            )
            after_map = {a["id"]: a["len"] for a in after}
            print("\n=== ПРОВЕРКА ПОСЛЕ UPDATE ===")
            bad = 0
            for r in clean:
                ok = after_map[r["id"]] == len(r["kept"])
                if not ok:
                    bad += 1
                print(f"id={r['id']:>4} ожидалось={len(r['kept'])} факт={after_map[r['id']]} "
                      f"{'OK' if ok else 'MISMATCH'}")
            if bad:
                raise AssertionError(f"{bad} материалов не совпали после UPDATE — откатываю всё")

            print(f"\nОбновлено материалов: {len(clean)}")
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
