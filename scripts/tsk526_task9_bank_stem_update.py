# -*- coding: utf-8 -*-
"""tsk-526 (пункт 4), шаг 2: вписать <img> схемы графа в stem банка задания 9 (курс 1154).

29 задач `oge:reshu:t9:<id>` обещают "схему по ссылке ниже", которой нет в LMS/SPW
(`task_content.media` не рендерится фронтендом нигде — playbook CB §6.5). Картинки уже
залиты в CAS/прод-S3 и проверены публично доступными (шаг 1,
D:/Work/ContentBackbone/scripts/tsk526_task9_bank_images.py,
D:/Work/ContentBackbone/reviews/tsk526-task9-bank/images_plan.json).

Этот скрипт заменяет маркер "(Смотрите схему по ссылке ниже.)" на <img src="/api/v1/media/<sha>">
(тот же durable-паттерн, что tsk-369/390/392 — SPW рендерит <img> из stem, не media-поле),
и оборачивает stem в <p> (текст был plain, после вставки <img> нужен HTML-режим рендера).
7 авторских заданий #q1-#q7 (текстовые описания графа) не затронуты — картинка им не нужна.

Запуск: dry-run по умолчанию;
  python scripts/tsk526_task9_bank_stem_update.py
  DBCHECK_OK=1 python scripts/tsk526_task9_bank_stem_update.py --apply
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
COURSE_ID = 1154
MARKER = "(Смотрите схему по ссылке ниже.)"
PLAN_PATH = Path(
    r"D:\Work\ContentBackbone\reviews\tsk526-task9-bank\images_plan.json"
)


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


def load_plan() -> dict[str, str]:
    """external_uid -> sha_ext, из плана шага 1 (уже проверен публично доступным)."""
    data = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for item in data["plan"]:
        if not item.get("public_ok"):
            raise RuntimeError(f"pid={item['problem_id']}: public_ok=False — не должно было дойти сюда")
        uid = f"oge:reshu:t9:{item['problem_id']}"
        out[uid] = item["sha_ext"]
    if len(out) != 29:
        raise RuntimeError(f"ожидал 29 записей в плане, нашёл {len(out)}")
    return out


def transform_stem(stem: str, sha_ext: str) -> str:
    if stem.count(MARKER) != 1:
        raise AssertionError(f"маркер встречается {stem.count(MARKER)} раз, ожидался 1")
    img_tag = f'<img src="/api/v1/media/{sha_ext}"/>'
    replaced = stem.replace(MARKER, f"<br>{img_tag}<br>", 1)
    if "\n\n" not in replaced:
        raise AssertionError("ожидал разделитель \\n\\n перед 'Источник:'")
    main, _, source = replaced.partition("\n\n")
    return f"<p>{main.strip()}</p><p>{source.strip()}</p>"


async def main(apply: bool) -> None:
    plan = load_plan()
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id, external_uid, task_content FROM tasks "
                "WHERE course_id = $1 AND external_uid = ANY($2::text[]) "
                "AND is_active = true FOR UPDATE",
                COURSE_ID, list(plan.keys()),
            )
            if len(rows) != 29:
                raise AssertionError(f"ожидал 29 активных задач, нашёл {len(rows)}")

            updates: list[tuple[int, str, str]] = []  # (id, external_uid, new_stem)
            for row in rows:
                content = json.loads(row["task_content"]) if isinstance(row["task_content"], str) else dict(row["task_content"])
                stem = content.get("stem", "")
                new_stem = transform_stem(stem, plan[row["external_uid"]])
                updates.append((row["id"], row["external_uid"], new_stem))

            print(f"Задач к обновлению: {len(updates)}")
            for tid, uid, new_stem in updates[:3]:
                print(f"  id={tid} {uid}\n    -> {new_stem[:160]}...")

            for tid, uid, new_stem in updates:
                row = next(r for r in rows if r["id"] == tid)
                content = json.loads(row["task_content"]) if isinstance(row["task_content"], str) else dict(row["task_content"])
                content["stem"] = new_stem
                await conn.execute(
                    "UPDATE tasks SET task_content = $1::jsonb WHERE id = $2",
                    json.dumps(content, ensure_ascii=False), tid,
                )

            if apply:
                verify = await conn.fetch(
                    "SELECT id, external_uid, task_content->>'stem' AS stem FROM tasks "
                    "WHERE course_id = $1 AND external_uid = ANY($2::text[])",
                    COURSE_ID, list(plan.keys()),
                )
                missing_img = [r["external_uid"] for r in verify if "/api/v1/media/" not in (r["stem"] or "")]
                if missing_img:
                    raise AssertionError(f"без <img> после записи: {missing_img}")
                still_has_marker = [r["external_uid"] for r in verify if MARKER in (r["stem"] or "")]
                if still_has_marker:
                    raise AssertionError(f"маркер не заменён: {still_has_marker}")
                print(f"\nПроверка после UPDATE: {len(verify)}/29 содержат <img>, маркер убран везде. OK")

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
