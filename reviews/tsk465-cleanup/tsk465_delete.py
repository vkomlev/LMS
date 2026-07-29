"""tsk-465: удаление мусорных материалов из прод-БД learn.

Удаляет строго 19 строк по явному списку id:
  - 14 тестовых «Win»/«OK row» в курсе 1 (прогоны импорта 02.06–07.06);
  - 5 материалов-обёрток с текстом «.» в главах Информатики-7.

Защита: перед удалением каждая строка сверяется с ожидаемым признаком
(название Win/OK row ЛИБО текст-точка). Если хоть одна не совпала или
количество != 19 — ROLLBACK, ничего не удаляется.

Каскадом уходит student_material_progress (FK ON DELETE CASCADE) — ожидается
ровно 7 строк. Расхождение тоже приводит к ROLLBACK.

Запуск: только на прод-сервере под пользователем app.
Бэкап: D:/Work/LMS/reviews/tsk465-cleanup/backup-2026-07-29-materials.json
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

IDS = [737, 738, 740, 741, 749, 750, 760, 761, 771, 772, 792, 793, 801, 802,
       879, 925, 969, 1000, 1034]
EXPECTED_MATERIALS = 19
EXPECTED_PROGRESS = 7
APPLY = "--apply" in sys.argv


def _dsn() -> str:
    """Достать DSN из /opt/lms/.env (не печатать его в вывод)."""
    with open("/opt/lms/.env", encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                # asyncpg не понимает драйверный префикс SQLAlchemy
                return raw.replace("postgresql+asyncpg://", "postgresql://")
    raise SystemExit("DATABASE_URL не найден в /opt/lms/.env")


async def main() -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, course_id, title,
                       regexp_replace(COALESCE(content->>'text',''),
                                      '<[^>]*>|&nbsp;|\\s', '', 'g') AS plain
                FROM materials WHERE id = ANY($1::int[]) ORDER BY id
                """,
                IDS,
            )
            print(f"Найдено материалов: {len(rows)} (ожидалось {EXPECTED_MATERIALS})")
            if len(rows) != EXPECTED_MATERIALS:
                raise SystemExit("СТОП: количество не совпало, данные изменились")

            for r in rows:
                ok_trash = r["title"] in ("Win", "OK row") and r["course_id"] == 1
                ok_stub = r["plain"] == "." and r["course_id"] in (827, 836, 845, 853, 858)
                if not (ok_trash or ok_stub):
                    raise SystemExit(
                        f"СТОП: материал {r['id']} не похож на цель "
                        f"(курс {r['course_id']}, «{r['title']}»)"
                    )
            print("Признаки сверены: все 19 — целевые")

            prog = await conn.fetchval(
                "SELECT COUNT(*) FROM student_material_progress WHERE material_id = ANY($1::int[])",
                IDS,
            )
            print(f"Отметок прогресса уйдёт каскадом: {prog} (ожидалось {EXPECTED_PROGRESS})")
            if prog != EXPECTED_PROGRESS:
                raise SystemExit("СТОП: число отметок прогресса не совпало")

            if not APPLY:
                print("\nDRY-RUN: изменения НЕ применены (нет --apply). Откатываю.")
                raise SystemExit(0)

            deleted = await conn.fetch(
                "DELETE FROM materials WHERE id = ANY($1::int[]) RETURNING id", IDS
            )
            print(f"Удалено материалов: {len(deleted)}")
            if len(deleted) != EXPECTED_MATERIALS:
                raise SystemExit("СТОП: удалено не 19 строк — откат")

            left_m = await conn.fetchval(
                "SELECT COUNT(*) FROM materials WHERE id = ANY($1::int[])", IDS
            )
            left_p = await conn.fetchval(
                "SELECT COUNT(*) FROM student_material_progress WHERE material_id = ANY($1::int[])",
                IDS,
            )
            print(f"Проверка внутри транзакции: материалов осталось {left_m}, отметок {left_p}")
            if left_m or left_p:
                raise SystemExit("СТОП: остатки после удаления — откат")
            print("COMMIT")
    finally:
        await conn.close()


asyncio.run(main())
