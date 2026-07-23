# -*- coding: utf-8 -*-
"""tsk-369, шаг 5: привязать файлы-приложения к заданиям на проде.

ЧТО МЕНЯЕТ (в одной транзакции, только по списку из плана)
  * `task_content.stem` — в НАЧАЛО условия добавляется абзац со ссылкой(ами) на файл.
    Именно он делает файл видимым ученику: SPW рисует условие как HTML и поля
    `attached_file_paths` не читает (проверено по клиенту);
  * `task_content.has_attached_file = true` и `task_content.attached_file_paths` —
    машинный учёт в том же формате, что ставит импорт ContentBackbone.

ЧЕГО НЕ ТРОГАЕТ: правила проверки, ответы, активность, порядок, любые другие поля.

ЗАЩИТЫ
  * файл привязывается, только если он подтверждён шагом 4 как доступный по
    `GET /api/v1/media/<sha_ext>` — иначе ученик получит ссылку в никуда;
  * повторный запуск невозможен: если в условии уже есть `/api/v1/media/`, задание
    пропускается (а не дублирует блок);
  * бэкап прежнего `stem` пишется на диск ДО записи;
  * dry-run по умолчанию: транзакция откатывается. `--apply` — только при DBCHECK_OK=1;
  * после COMMIT — независимая проверка ПОСТРОЧНО (не агрегатом): у каждого задания
    ссылка на месте и `attached_file_paths` совпадает с планом.

Запуск:
  python scripts/tsk369_apply.py --plan <plan.json> --stored <stored.json> --backup <файл> [--apply]
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk369_collect import dsn  # noqa: E402

MEDIA_BASE = "/api/v1/media"


async def main(plan_path: Path, stored_path: Path, backup_path: Path, apply: bool) -> None:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))["plan"]
    available = {s["sha_ext"] for s in json.loads(stored_path.read_text(encoding="utf-8"))["stored"]}
    # Файлы-близнецы уже лежали в CAS до этой задачи — они не проходили шаг 4.
    reused = {f["sha_ext"] for t in plan for f in t["files"] if f.get("reuse")}

    ready, skipped = [], []
    for task in plan:
        missing = [f["sha_ext"] for f in task["files"]
                   if f["sha_ext"] not in available and f["sha_ext"] not in reused]
        (skipped if missing else ready).append(
            task if not missing else {"id": task["id"], "missing": missing})

    print(f"Заданий в плане: {len(plan)}; готовы к записи: {len(ready)}; "
          f"без подтверждённого файла: {len(skipped)}")
    if skipped:
        print(f"  пропускаю: {[s['id'] for s in skipped]}")
    if not ready:
        return

    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        ids = [t["id"] for t in ready]
        rows = {r["id"]: r for r in await conn.fetch(
            "SELECT id, external_uid, task_content->>'stem' AS stem, "
            "       task_content->'attached_file_paths' AS paths "
            "FROM tasks WHERE id = ANY($1::int[]) AND is_active", ids)}

        missing_rows = sorted(set(ids) - set(rows))
        if missing_rows:
            raise RuntimeError(f"не нашёл активных заданий: {missing_rows}")

        # Идемпотентность — ПОФАЙЛОВО, а не по любой media-ссылке. У заданий 3 «базы данных»
        # в stem уже лежат PNG-картинки таблицы (это НЕ файл-данные): грубая проверка
        # «MEDIA_BASE in stem» зря пропускала бы их, оставив ученика без xlsx для расчёта
        # (tsk-390: 2131/2132/2134/2135). Пропускаем задание, только если ВСЕ его
        # планируемые файлы (по sha_ext) уже стоят в условии — тогда это реальный повтор.
        already = [t["id"] for t in ready
                   if all(f["sha_ext"] in (rows[t["id"]]["stem"] or "") for f in t["files"])]
        if already:
            print(f"  все файлы уже привязаны (пропускаю, повторный запуск?): {already}")
            ready = [t for t in ready if t["id"] not in already]
            ids = [t["id"] for t in ready]
        if not ready:
            print("Нечего записывать.")
            return

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            [{"id": i, "external_uid": rows[i]["external_uid"], "stem": rows[i]["stem"]}
             for i in ids], ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Бэкап прежних условий: {backup_path}")

        async with conn.transaction():
            for task in ready:
                paths = [f"{MEDIA_BASE}/{f['sha_ext']}" for f in task["files"]]
                new_stem = task["block"] + (rows[task["id"]]["stem"] or "")
                await conn.execute(
                    "UPDATE tasks SET task_content = "
                    "  jsonb_set("
                    "    jsonb_set("
                    "      jsonb_set(task_content, '{stem}', to_jsonb($2::text)),"
                    "      '{has_attached_file}', 'true'::jsonb),"
                    "    '{attached_file_paths}', $3::jsonb) "
                    "WHERE id = $1",
                    task["id"], new_stem, json.dumps(paths),
                )

            check = {r["id"]: r for r in await conn.fetch(
                "SELECT id, task_content->>'stem' AS stem, "
                "       task_content->'attached_file_paths' AS paths, "
                "       task_content->>'has_attached_file' AS flag "
                "FROM tasks WHERE id = ANY($1::int[])", ids)}
            bad = []
            for task in ready:
                r = check.get(task["id"])
                want = [f"{MEDIA_BASE}/{f['sha_ext']}" for f in task["files"]]
                got = json.loads(r["paths"]) if isinstance(r["paths"], str) else (r["paths"] or [])
                if not r or r["flag"] != "true" or list(got) != want:
                    bad.append((task["id"], "метаданные"))
                elif any(f["sha_ext"] not in (r["stem"] or "") for f in task["files"]):
                    bad.append((task["id"], "ссылка в условии"))
            if bad:
                raise AssertionError(f"проверка внутри транзакции не прошла: {bad[:10]}")

            print(f"Внутри транзакции: обновлено и проверено {len(ready)} заданий.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after = {r["id"]: r for r in await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem, "
            "       task_content->'attached_file_paths' AS paths "
            "FROM tasks WHERE id = ANY($1::int[])", ids)}
        problems = []
        for task in ready:
            r = after.get(task["id"])
            got = json.loads(r["paths"]) if isinstance(r["paths"], str) else (r["paths"] or [])
            want = [f"{MEDIA_BASE}/{f['sha_ext']}" for f in task["files"]]
            if list(got) != want or any(f["sha_ext"] not in (r["stem"] or "") for f in task["files"]):
                problems.append(task["id"])
        print(f"  проверено построчно: {len(ready)}; расхождений: {len(problems)}")
        if problems:
            print(f"  ПРОБЛЕМНЫЕ: {problems}")
            sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--stored", required=True)
    ap.add_argument("--backup", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    try:
        asyncio.run(main(Path(a.plan), Path(a.stored), Path(a.backup), a.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
