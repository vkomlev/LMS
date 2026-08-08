"""tsk-593 (хвост tsk-575): кто и что должен приложить заново.

Файлы, утраченные до починки tsk-575, не восстановятся: копий нет ни в
бэкапах, ни в других каталогах. Единственное, что можно сделать, —
дать преподавателю понятный список: ученик, задание, курс, когда сдавал.
Данные скрипт НЕ правит.

Два действия:

* **отчёт** (по умолчанию) — CSV и сводка по ученикам. Только чтение;
* `--seed-baseline` — записать найденные утраты в `attachment_missing_seen`
  как ИСХОДНЫЙ УРОВЕНЬ суточной проверки. Без этого шага первый же её прогон
  сообщит про все старые потери разом, и уведомление про новую потерю утонет
  в этом шуме. Это единственная запись, которую делает скрипт, и она
  добавляющая: существующие строки не трогаются (`ON CONFLICT DO NOTHING`).

Запуск (на сервере, под пользователем app):

    sudo -u app bash -lc 'cd /opt/lms && set -a && . ./.env && set +a && \\
        venv/bin/python scripts/tsk593_lost_attachments_report.py \\
        --out reviews/2026-08-08-tsk593-lost-attachments.csv'
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402
from app.services import attachment_storage  # noqa: E402
from app.services.attempt_attachments import collect_attachment_ids  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tsk593.lost")

_SQL = """
SELECT tr.id            AS task_result_id,
       tr.user_id       AS user_id,
       u.full_name      AS full_name,
       tr.task_id       AS task_id,
       t.external_uid   AS task_uid,
       t.task_content->>'title' AS task_title,
       tr.attempt_id    AS attempt_id,
       a.course_id      AS course_id,
       c.title          AS course_title,
       tr.submitted_at  AS submitted_at,
       tr.answer_json   AS answer_json
FROM task_results tr
LEFT JOIN users   u ON u.id = tr.user_id
LEFT JOIN tasks   t ON t.id = tr.task_id
LEFT JOIN attempts a ON a.id = tr.attempt_id
LEFT JOIN courses c ON c.id = a.course_id
WHERE jsonb_array_length(
          COALESCE(tr.answer_json->'response'->'meta'->'attachments', '[]'::jsonb)
      ) > 0
ORDER BY tr.user_id, tr.task_id, tr.submitted_at
"""


async def collect() -> List[Dict[str, Any]]:
    """Собрать строки «ссылка на вложение есть» и отметить, каких файлов нет."""
    async with async_session_factory() as db:
        rows = (await db.execute(text(_SQL))).mappings().fetchall()

    wanted: List[str] = []
    for row in rows:
        wanted.extend(collect_attachment_ids(row["answer_json"]))
    logger.info("работ со ссылкой на вложение: %s, имён файлов: %s", len(rows), len(set(wanted)))

    present = await attachment_storage.existing_names(attachment_storage.ATTEMPTS, wanted)
    logger.info("файлов найдено в хранилище: %s из %s", len(present), len(set(wanted)))

    lost: List[Dict[str, Any]] = []
    for row in rows:
        for name in collect_attachment_ids(row["answer_json"]):
            if name in present:
                continue
            lost.append({
                "user_id": row["user_id"],
                "full_name": row["full_name"],
                "course_id": row["course_id"],
                "course_title": row["course_title"],
                "task_id": row["task_id"],
                "task_uid": row["task_uid"],
                "task_title": row["task_title"],
                "attempt_id": row["attempt_id"],
                "task_result_id": row["task_result_id"],
                "submitted_at": row["submitted_at"],
                "attachment_id": name,
            })
    return lost


async def seed_baseline(lost: List[Dict[str, Any]]) -> int:
    """Записать утраты как исходный уровень суточной проверки. Возвращает число строк."""
    if not lost:
        return 0
    async with async_session_factory() as db:
        for item in lost:
            await db.execute(
                text(
                    "INSERT INTO attachment_missing_seen (space, name, owner_kind, owner_id) "
                    "VALUES ('attempts', :name, 'task_result', :oid) "
                    "ON CONFLICT (space, name) DO NOTHING"
                ),
                {"name": item["attachment_id"], "oid": item["task_result_id"]},
            )
        await db.commit()
        written = (
            await db.execute(text("SELECT count(*) FROM attachment_missing_seen"))
        ).scalar()
    return int(written or 0)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Список утраченных вложений (tsk-575/tsk-593)")
    parser.add_argument("--out", default=None, help="куда положить CSV")
    parser.add_argument(
        "--seed-baseline", action="store_true",
        help="записать найденное в attachment_missing_seen как исходный уровень проверки",
    )
    args = parser.parse_args()

    lost = await collect()
    logger.info("утрачено файлов: %s", len(lost))

    by_student: Dict[str, int] = {}
    for item in lost:
        key = f"{item['full_name']} (id {item['user_id']})"
        by_student[key] = by_student.get(key, 0) + 1

    print("\nКого просить приложить файл заново:")
    for who, count in sorted(by_student.items(), key=lambda kv: -kv[1]):
        print(f"  {who}: {count} файлов")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(lost[0].keys()) if lost else ["нет"],
                                    delimiter=";")
            writer.writeheader()
            writer.writerows(lost)
        logger.info("CSV: %s", out)

    if args.seed_baseline:
        total = await seed_baseline(lost)
        logger.info("исходный уровень записан, всего строк в памяти проверки: %s", total)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
