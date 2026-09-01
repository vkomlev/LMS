# -*- coding: utf-8 -*-
"""tsk-759, шаг 2: переписать внешние ссылки на картинки в stem на наш /api/v1/media/.

37 активных заданий (курсы 140/153/1379/1381/1397) ссылались на картинки
kompege.ru / ege.sdamgia.ru / sun9-40.userapi.com. Файлы на источниках живы,
но CSP боевого сайта (`img-src 'self' data: api.learn... s3.twcstorage.ru
victor-komlev.ru`) их не пропускает — ученик видит пустую рамку. Живой случай:
задание 3992 (курс 140), где картинка несёт всё условие.

Шаг 1 (ContentBackbone/scripts/tsk759_external_images_to_cas.py) уже положил
файлы в CAS + прод-S3 и проверил каждый по `/api/v1/media/<sha>`: код 200,
Content-Type и магическая сигнатура (урок tsk-536 — 200 не значит «картинка»).

Здесь заменяется только значение атрибута `src="<внешний url>"` на
`src="/api/v1/media/<sha_ext>"`; остальная разметка тега (width/height,
`>` или `/>`) не трогается. Задания, где картинка уже наша, не затрагиваются.

Запуск:
  python scripts/tsk759_rewrite_external_images.py --plan <файл> --tasks 3992
  DBCHECK_OK=1 python scripts/tsk759_rewrite_external_images.py --plan <файл> --tasks 3992 --apply
  (--tasks all — все задания, чьи URL есть в плане)
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
PROD_HOST = "5.42.107.253"


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if PROD_HOST not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and PROD_HOST in arg:
                    dsn = arg
                    break
    if PROD_HOST not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn.")
    return dsn


def load_url_to_sha(plan_paths: list[Path]) -> dict[str, str]:
    """src_url (как он лежит в stem) -> sha_ext. Берём только проверенные записи."""
    out: dict[str, str] = {}
    for path in plan_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("failed"):
            raise RuntimeError(f"{path.name}: план содержит ошибки: {data['failed']}")
        if not data.get("apply"):
            raise RuntimeError(f"{path.name}: это dry-run-план, файлы не залиты")
        for item in data["plan"]:
            if not item.get("public_ok"):
                raise RuntimeError(f"{item['src_url']}: public_ok=False — сюда дойти не должно")
            out[item["src_url"]] = item["sha_ext"]
    if not out:
        raise RuntimeError("план пуст")
    return out


def transform_stem(stem: str, url_to_sha: dict[str, str]) -> tuple[str, int]:
    """Заменяет src="<внешний url>" на src="/api/v1/media/<sha>". Возвращает (stem, замен)."""
    count = 0
    for url, sha_ext in url_to_sha.items():
        needle = f'src="{url}"'
        if needle in stem:
            count += stem.count(needle)
            stem = stem.replace(needle, f'src="/api/v1/media/{sha_ext}"')
    return stem, count


async def main(plan_paths: list[Path], task_filter: list[int] | None, apply: bool) -> None:
    url_to_sha = load_url_to_sha(plan_paths)
    print(f"В плане проверенных файлов: {len(url_to_sha)}")

    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            if task_filter is not None:
                rows = await conn.fetch(
                    "SELECT id, course_id, is_active, task_content FROM tasks "
                    "WHERE id = ANY($1::int[]) ORDER BY id FOR UPDATE",
                    task_filter,
                )
                if len(rows) != len(task_filter):
                    missing = sorted(set(task_filter) - {r["id"] for r in rows})
                    raise AssertionError(f"не найдены задания: {missing}")
            else:
                like_any = [f'%src="{u}"%' for u in url_to_sha]
                rows = await conn.fetch(
                    "SELECT id, course_id, is_active, task_content FROM tasks "
                    "WHERE task_content->>'stem' LIKE ANY($1::text[]) ORDER BY id FOR UPDATE",
                    like_any,
                )

            updates: list[tuple[int, dict, int]] = []
            for row in rows:
                content = (json.loads(row["task_content"])
                           if isinstance(row["task_content"], str) else dict(row["task_content"]))
                stem = content.get("stem", "") or ""
                new_stem, n = transform_stem(stem, url_to_sha)
                if n == 0:
                    raise AssertionError(f"id={row['id']}: ни одной замены не найдено")
                content = dict(content)
                content["stem"] = new_stem
                updates.append((row["id"], content, n))

            total = sum(n for _, _, n in updates)
            print(f"Заданий к обновлению: {len(updates)}, замен всего: {total}")
            for tid, content, n in updates[:3]:
                print(f"  id={tid} замен={n}\n    -> ...{content['stem'][-180:]}")

            for tid, content, _ in updates:
                await conn.execute(
                    "UPDATE tasks SET task_content = $1::jsonb WHERE id = $2",
                    json.dumps(content, ensure_ascii=False), tid,
                )

            ids = [tid for tid, _, _ in updates]
            verify = await conn.fetch(
                "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY($1::int[])", ids,
            )
            no_media = [r["id"] for r in verify if "/api/v1/media/" not in (r["stem"] or "")]
            if no_media:
                raise AssertionError(f"после записи нет /api/v1/media/: {no_media}")
            still_ext = [r["id"] for r in verify
                         if any(f'src="{u}"' in (r["stem"] or "") for u in url_to_sha)]
            if still_ext:
                raise AssertionError(f"внешняя ссылка осталась: {still_ext}")
            print(f"Проверка в транзакции: {len(verify)}/{len(ids)} — все на /api/v1/media/, "
                  f"внешних ссылок не осталось.")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="append", required=True, help="JSON-план шага 1 (можно несколько)")
    ap.add_argument("--tasks", required=True, help="список id через запятую или 'all'")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    tasks = None if args.tasks.strip() == "all" else [int(x) for x in args.tasks.split(",")]
    try:
        asyncio.run(main([Path(p) for p in args.plan], tasks, args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
    except AssertionError as exc:
        print(f"\nОШИБКА ПРОВЕРКИ: {exc}")
        sys.exit(1)
