"""Разовый прод-бэкфилл: закрыть стухшие заявки blocked_limit (tsk-339).

Найдено живым прогоном tsk-335/336: 9 заявок help_requests(request_type=
'blocked_limit', status='open') на проде оказались устаревшими — последний
реальный ответ ученика по каждому заданию уже `is_correct=true` (ученик решил
задание сам, без выдачи лимита учителем). Подтверждено read-only сверкой
(см. tsk-339 в D:\\Work\\Root\\tasks). Список ID финальный, добавлять новые
сюда не нужно — дальше это делает автозакрытие в attempts.py (2.4c).

Режим: без --apply — только проверка (сверяет текущее состояние с ожидаемым,
ничего не пишет). Пишет ТОЛЬКО при --apply.

DSN берётся ТОЛЬКО из переменной окружения LEARN_PROD_DSN (не хардкодится и не
пишется в файл): LEARN_PROD_DSN=... python scripts/backfill_close_stale_blocked_limit_tsk339.py
"""
from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

DSN = os.environ.get("LEARN_PROD_DSN")
if not DSN:
    raise RuntimeError(
        "Не задана переменная окружения LEARN_PROD_DSN "
        "(прод-DSN роли lms_prod). Секрет в коде не хардкодится."
    )

# (help_request_id, student_id, task_id) — подтверждено read-only сверкой 2026-07-21.
STALE_REQUESTS: list[tuple[int, int, int]] = [
    (57, 4497, 7430),
    (58, 4497, 7431),
    (59, 4496, 6342),
    (60, 4497, 7468),
    (61, 4497, 7469),
    (62, 4497, 7472),
    (63, 4497, 7491),
    (64, 4497, 7493),
    (65, 4497, 7494),
]

RESOLUTION_COMMENT = "Задание решено учеником самостоятельно (бэкфилл tsk-339)"


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(DSN)
    try:
        ids = [r[0] for r in STALE_REQUESTS]
        rows = await conn.fetch(
            "SELECT id, status, request_type, student_id, task_id "
            "FROM help_requests WHERE id = ANY($1::int[]) ORDER BY id",
            ids,
        )
        by_id = {r["id"]: r for r in rows}
        print(f"Найдено {len(rows)} из {len(ids)} ожидаемых заявок.")

        mismatches = []
        for hid, sid, tid in STALE_REQUESTS:
            row = by_id.get(hid)
            if row is None:
                mismatches.append(f"  id={hid}: заявка не найдена")
                continue
            if row["status"] != "open" or row["request_type"] != "blocked_limit":
                mismatches.append(
                    f"  id={hid}: status={row['status']} type={row['request_type']} "
                    f"(ожидали open/blocked_limit — уже не актуально, пропускаю)"
                )
            if row["student_id"] != sid or row["task_id"] != tid:
                mismatches.append(
                    f"  id={hid}: student_id={row['student_id']} task_id={row['task_id']} "
                    f"не совпадает с ожидаемым ({sid}, {tid}) — ОСТАНОВКА"
                )
                print("\n".join(mismatches))
                return

        if mismatches:
            print("Расхождения с ожидаемым состоянием (не блокирующие, просто пропуск):")
            print("\n".join(mismatches))

        to_close = [
            hid for hid, _, _ in STALE_REQUESTS
            if by_id.get(hid) is not None
            and by_id[hid]["status"] == "open"
            and by_id[hid]["request_type"] == "blocked_limit"
        ]
        print(f"К закрытию: {to_close}")

        if not apply:
            print("Dry-run: ничего не записано. Запустите с --apply для реальной записи.")
            return

        async with conn.transaction():
            for hid in to_close:
                await conn.execute(
                    """
                    UPDATE help_requests
                    SET status = 'closed', closed_at = now(), closed_by = NULL,
                        resolution_comment = $2, updated_at = now()
                    WHERE id = $1 AND status = 'open'
                    """,
                    hid, RESOLUTION_COMMENT,
                )
            print(f"OK: закрыто {len(to_close)} заявок.")

        verify = await conn.fetch(
            "SELECT id, status, closed_by, resolution_comment "
            "FROM help_requests WHERE id = ANY($1::int[]) ORDER BY id",
            to_close,
        )
        for r in verify:
            print(dict(r))
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
