# -*- coding: utf-8 -*-
"""tsk-089, шаг 2: переписать голые imgsrc kpolyakov на CAS-URL в 19 заданиях HARD-блока.

19 активных заданий (id список ниже, курсы 1379/1380/1381/1396/1398) содержат в
task_content->>'stem' `<img src="NNNN.gif">` — голое имя файла без хоста, 404 в SPW
(сверено /db-check 2026-08-03). 9 уникальных файлов уже скачаны с kpolyakov.spb.ru
(правильный путь — `cms/images/`, НЕ `cms/files/` — тот для <a href>) и залиты в
CAS + прод-S3 шагом 1 (ContentBackbone/scripts/tsk089_kpolyakov_hard_images.py),
план — ContentBackbone/reviews/tsk089-hard-block-images/images_plan.json.

Заменяем только значение атрибута `src="NNNN.gif"` -> `src="/api/v1/media/<sha_ext>"`,
остальная разметка тега (`>` или `/>`) не трогается.

Запуск:
  python scripts/tsk089_hard_block_stem_update.py            # dry-run по умолчанию
  DBCHECK_OK=1 python scripts/tsk089_hard_block_stem_update.py --apply
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

project_root = Path(__file__).resolve().parents[1]

TASK_IDS: list[int] = [
    4299, 4337, 4342, 4344, 4345, 4351, 4378, 4384, 4419, 4456,
    4457, 4458, 4459, 4475, 4476, 4477, 4478, 4493, 4494,
]

PLAN_PATH = Path(
    r"D:\Work\ContentBackbone\reviews\tsk089-hard-block-images\images_plan.json"
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


def load_filename_to_sha() -> dict[str, str]:
    """filename ("5436.gif") -> sha_ext, из плана шага 1 (уже проверен публично доступным)."""
    data = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    if data.get("failed"):
        raise RuntimeError(f"план содержит ошибки: {data['failed']}")
    out: dict[str, str] = {}
    for item in data["plan"]:
        if not item.get("public_ok"):
            raise RuntimeError(f"{item['filename']}: public_ok=False — не должно было дойти сюда")
        out[item["filename"]] = item["sha_ext"]
    if len(out) != 9:
        raise RuntimeError(f"ожидал 9 записей в плане, нашёл {len(out)}")
    return out


def transform_stem(stem: str, filename_to_sha: dict[str, str]) -> tuple[str, int]:
    """Заменяет все `src="NNNN.gif"` на `src="/api/v1/media/<sha_ext>"`. Возвращает (stem, кол-во замен)."""
    count = 0

    def _sub(match: "re.Match[str]") -> str:
        nonlocal count
        filename = match.group(1)
        sha_ext = filename_to_sha.get(filename)
        if sha_ext is None:
            raise AssertionError(f"неизвестное имя файла в stem: {filename!r}")
        count += 1
        return f'src="/api/v1/media/{sha_ext}"'

    new_stem = re.sub(r'src="([0-9]+\.gif)"', _sub, stem)
    return new_stem, count


async def main(apply: bool) -> None:
    filename_to_sha = load_filename_to_sha()
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id, task_content FROM tasks "
                "WHERE id = ANY($1::int[]) AND is_active = true FOR UPDATE",
                TASK_IDS,
            )
            if len(rows) != len(TASK_IDS):
                found = {r["id"] for r in rows}
                missing = sorted(set(TASK_IDS) - found)
                raise AssertionError(f"ожидал {len(TASK_IDS)} активных задач, нашёл {len(rows)}; нет: {missing}")

            updates: list[tuple[int, dict, int]] = []  # (id, new_content, replacements)
            for row in rows:
                content = json.loads(row["task_content"]) if isinstance(row["task_content"], str) else dict(row["task_content"])
                stem = content.get("stem", "")
                new_stem, n = transform_stem(stem, filename_to_sha)
                if n == 0:
                    raise AssertionError(f"id={row['id']}: ни одной замены src=\"NNNN.gif\" не найдено")
                content = dict(content)
                content["stem"] = new_stem
                updates.append((row["id"], content, n))

            total_repl = sum(n for _, _, n in updates)
            print(f"Задач к обновлению: {len(updates)}, замен всего: {total_repl}")
            for tid, content, n in updates[:3]:
                print(f"  id={tid} replacements={n}\n    -> {content['stem'][:200]}...")

            for tid, content, _ in updates:
                await conn.execute(
                    "UPDATE tasks SET task_content = $1::jsonb WHERE id = $2",
                    json.dumps(content, ensure_ascii=False), tid,
                )

            if apply:
                verify = await conn.fetch(
                    "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY($1::int[])",
                    TASK_IDS,
                )
                missing_media = [r["id"] for r in verify if "/api/v1/media/" not in (r["stem"] or "")]
                if missing_media:
                    raise AssertionError(f"без /api/v1/media/ после записи: {missing_media}")
                still_bare = [
                    r["id"] for r in verify
                    if re.search(r'src="[0-9]+\.gif"', r["stem"] or "")
                ]
                if still_bare:
                    raise AssertionError(f"голый src=\"NNNN.gif\" не заменён: {still_bare}")
                print(f"\nПроверка после UPDATE: {len(verify)}/{len(TASK_IDS)} содержат /api/v1/media/, голых src не осталось. OK")

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
