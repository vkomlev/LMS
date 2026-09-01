# -*- coding: utf-8 -*-
"""tsk-761, шаг 2: переписать остаточные ссылки на файлы источников на наш /api/v1/media/.

Шаг 1 (`scripts/tsk761_verify_links.py`) для каждого задания скачал файл по битой
ссылке и сверил его sha256 с sha уже привязанного CAS-файла (имя файла в CAS —
это и есть sha256 содержимого). Сюда приходят ТОЛЬКО записи с `match: true`:
доказано, что за битой ссылкой лежит ровно тот же файл, что у нас в CAS, поэтому
ссылку можно переписать на нашу, ничего не докачивая.

Меняется только значение атрибута `href` у самого якоря: текст ссылки
(«Задание 26», «10-260.docx») и остальная разметка условия не трогаются — якорь
стоит внутри текста задания и его удаление меняло бы вид условия.

Протокол записи (/db-check): бэкап прежних `stem` в файл ДО правки, одна
транзакция, `FOR UPDATE`, построчная проверка внутри транзакции и после коммита,
dry-run по умолчанию.

Запуск:
  python scripts/tsk761_rewrite_links.py --plan out/tsk761_plan.json --tasks 3791
  DBCHECK_OK=1 python scripts/tsk761_rewrite_links.py --plan out/tsk761_plan.json --tasks all --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
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


def load_plan(path: Path) -> dict[int, list[dict]]:
    """task_id -> список доказанных пар (битая ссылка, наш CAS-url).

    Список, а не одна пара: у задания бывает несколько файлов-приложений («Файл A» и
    «Файл B» у №26), и каждая ссылка сверяется со своим файлом отдельно.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, list[dict]] = {}
    for item in data["plan"]:
        if item.get("match") is not True:
            continue
        if not item.get("cas_href") or not item.get("bad_href"):
            raise RuntimeError(f"задание {item['task_id']}: в плане нет пары ссылок")
        out.setdefault(int(item["task_id"]), []).append(item)
    if not out:
        raise RuntimeError("в плане нет ни одной доказанной пары (match=true)")
    return out


def transform_stem(stem: str, bad_href: str, cas_href: str) -> tuple[str, int]:
    """Заменяет href="<битая ссылка>" на href="<наш CAS-url>". Возвращает (stem, замен)."""
    needle = f'href="{bad_href}"'
    count = stem.count(needle)
    if count:
        stem = stem.replace(needle, f'href="{cas_href}"')
    return stem, count


async def main(plan_path: Path, task_filter: list[int] | None, apply: bool) -> None:
    plan = load_plan(plan_path)
    ids = sorted(plan) if task_filter is None else sorted(set(task_filter) & set(plan))
    if task_filter is not None and set(task_filter) - set(plan):
        raise RuntimeError(f"нет доказанной пары для заданий: {sorted(set(task_filter) - set(plan))}")
    print(f"Заданий с доказанным совпадением sha: {len(plan)}; к обработке сейчас: {len(ids)}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = project_root / "out" / f"tsk761_stem_backup_{stamp}.json"

    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id, course_id, is_active, task_content FROM tasks "
                "WHERE id = ANY($1::int[]) ORDER BY id FOR UPDATE",
                ids,
            )
            if len(rows) != len(ids):
                missing = sorted(set(ids) - {r["id"] for r in rows})
                raise AssertionError(f"не найдены задания: {missing}")

            backup: list[dict] = []
            updates: list[tuple[int, dict, int]] = []
            for row in rows:
                content = (json.loads(row["task_content"])
                           if isinstance(row["task_content"], str) else dict(row["task_content"]))
                stem = content.get("stem", "") or ""
                backup.append({"task_id": row["id"], "stem": stem})
                new_stem, replaced = stem, 0
                for item in plan[row["id"]]:
                    new_stem, n = transform_stem(new_stem, item["bad_href"], item["cas_href"])
                    if n == 0:
                        raise AssertionError(
                            f"id={row['id']}: якорь href=\"{item['bad_href']}\" не найден в stem"
                        )
                    replaced += n
                content = dict(content)
                content["stem"] = new_stem
                updates.append((row["id"], content, replaced))

            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text(
                json.dumps({"task": "tsk-761", "saved_at": stamp, "items": backup},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"Бэкап прежних условий: {backup_path} ({len(backup)} записей)")

            print(f"Замен всего: {sum(n for _, _, n in updates)}")
            for tid, content, n in updates[:3]:
                for item in plan[tid]:
                    print(f"  id={tid} замен={n}: {item['bad_href']} -> {item['cas_href']}")

            for tid, content, _ in updates:
                await conn.execute(
                    "UPDATE tasks SET task_content = $1::jsonb WHERE id = $2",
                    json.dumps(content, ensure_ascii=False), tid,
                )

            verify = await conn.fetch(
                "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY($1::int[])", ids,
            )
            for r in verify:
                stem = r["stem"] or ""
                for item in plan[r["id"]]:
                    if f'href="{item["bad_href"]}"' in stem:
                        raise AssertionError(f"id={r['id']}: битая ссылка осталась")
                    if item["cas_href"] not in stem:
                        raise AssertionError(f"id={r['id']}: нет ссылки на CAS")
            print(f"Проверка в транзакции: {len(verify)}/{len(ids)} — битых ссылок нет, CAS на месте.")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        after = await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY($1::int[])", ids,
        )
        bad_left = [
            r["id"] for r in after
            if any(f'href="{item["bad_href"]}"' in (r["stem"] or "") for item in plan[r["id"]])
        ]
        if bad_left:
            raise AssertionError(f"ПОСЛЕ КОММИТА битая ссылка осталась: {bad_left}")
        print(f"\nЗАПИСАНО И ЗАКОММИЧЕНО. Проверка после коммита: {len(after)}/{len(ids)} чисты.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--tasks", required=True, help="список id через запятую или 'all'")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    tasks = None if args.tasks.strip() == "all" else [int(x) for x in args.tasks.split(",")]
    try:
        asyncio.run(main(Path(args.plan), tasks, args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
    except AssertionError as exc:
        print(f"\nОШИБКА ПРОВЕРКИ: {exc}")
        sys.exit(1)
