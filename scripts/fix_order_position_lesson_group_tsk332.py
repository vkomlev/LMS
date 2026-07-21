# -*- coding: utf-8 -*-
"""tsk-332: пересчитать order_position курсов 917 и 1080 (группировка по уроку).

ЧТО ДЕЛАЕТ
Триггер `trg_set_task_order_position` должен держать `order_position` как
чистую последовательность 1..N без дублей на курс. Для курсов 917
(«Создание чат-ботов», 58 активных заданий) и 1080 («ОГЭ по информатике»,
18 активных заданий) инвариант нарушен: `order_position` — это номер вопроса
ВНУТРИ урока (`external_uid` = `authored:<course>:<lesson>#q<N>`), а не
курс-сквозная позиция (диагностика — reviews/2026-07-20-tsk332-order-position-diagnosis.md).

Наивный ROW_NUMBER() BY id ASC закрыл бы инвариант БД, но оставил бы баг по
сути: вторая партия вопросов (#q2) создавалась отдельным проходом НЕ в
порядке уроков, и её id-порядок не совпадает с программой курса — traversal
всё равно шёл бы "все #q1, потом все #q2" вместо "урок1.q1, урок1.q2, урок2.q1...".

Пересчёт группирует по `lesson_slug` (часть external_uid между вторым ':' и
'#'), ранжирует уроки по MIN(id) их вопросов (== id вопроса #q1, т.к. он
всегда создавался раньше #q2 для того же урока — подтверждено по данным), и
внутри урока сортирует по номеру вопроса. Даёт урок-за-уроком порядок,
совпадающий с программой курса (оператор подтвердил вариант "группировка по
уроку" явно, см. review-артефакт).

ОБЛАСТЬ ДЕЙСТВИЯ
Только dev (`learn.public`, localhost) — на проде у обоих курсов 0 строк в
tasks (контент ещё не синхронизирован), писать там нечего.

ИДЕМПОТЕНТНОСТЬ / BLAST-RADIUS
UPDATE трогает ровно 76 строк (58 + 18), только course_id IN (917, 1080).
order_position пересчитывается детерминированно из текущих данных — повторный
запуск даст тот же результат (идемпотентно). Обратимо: исходные значения
видны в reviews/2026-07-20-tsk332-order-position-diagnosis.md.

Запуск: dry-run по умолчанию (транзакция откатывается); --apply — запись.
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(dotenv_path=os.path.join(project_root, ".env"), encoding="utf-8-sig")

COURSE_IDS = (917, 1080)

SELECT_PREVIEW = """
WITH parsed AS (
    SELECT id, course_id, external_uid,
           split_part(split_part(external_uid, ':', 3), '#', 1) AS lesson_slug,
           (regexp_match(external_uid, '#q(\\d+)$'))[1]::int AS qnum
    FROM tasks
    WHERE course_id = ANY($1::int[])
),
lesson_rank AS (
    SELECT course_id, lesson_slug, MIN(id) AS lesson_min_id
    FROM parsed
    GROUP BY course_id, lesson_slug
)
SELECT p.id, p.course_id, p.external_uid,
       ROW_NUMBER() OVER (
           PARTITION BY p.course_id
           ORDER BY lr.lesson_min_id ASC, p.qnum ASC, p.id ASC
       ) AS new_pos
FROM parsed p
JOIN lesson_rank lr USING (course_id, lesson_slug)
ORDER BY p.course_id, new_pos
"""

UPDATE_ONE = """
UPDATE tasks SET order_position = $2 WHERE id = $1
"""


def _dsn() -> str:
    """Dev-DSN (localhost). Явно проверяем, что это НЕ прод-хост."""
    env = os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" in dsn:
        raise RuntimeError(
            "DATABASE_URL указывает на прод-хост — эта задача только для dev "
            "(на проде у курсов 917/1080 нет строк в tasks)."
        )
    if not dsn:
        raise RuntimeError("DATABASE_URL не задан в окружении")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            # Триггер должен молчать на время пересчёта — иначе explicit
            # order_position спровоцирует каскадные сдвиги соседей поверх
            # наших же ещё не применённых значений.
            await conn.execute(
                "SELECT set_config('app.skip_task_order_trigger', 'true', true)"
            )

            rows = await conn.fetch(SELECT_PREVIEW, list(COURSE_IDS))
            print(f"Целевых строк (курсы {COURSE_IDS}): {len(rows)}")
            if len(rows) == 0:
                raise RuntimeError("кандидатов нет")

            print("\nПревью нового порядка (первые/последние 5 на курс):")
            by_course: dict[int, list] = {}
            for r in rows:
                by_course.setdefault(r["course_id"], []).append(r)
            for cid, items in by_course.items():
                print(f"  course_id={cid}, всего={len(items)}")
                for r in items[:5]:
                    print(f"    {r['new_pos']:>3}  {r['external_uid']}")
                print("    ...")
                for r in items[-5:]:
                    print(f"    {r['new_pos']:>3}  {r['external_uid']}")

            updated = 0
            for r in rows:
                res = await conn.execute(UPDATE_ONE, r["id"], r["new_pos"])
                updated += int(res.split()[-1])

            if updated != len(rows):
                raise AssertionError(f"ожидали обновить {len(rows)}, обновлено {updated}")

            # ---- Верификация внутри транзакции ----
            collisions = await conn.fetch(
                """
                SELECT course_id, order_position, COUNT(*) AS cnt
                FROM tasks WHERE course_id = ANY($1::int[])
                GROUP BY course_id, order_position
                HAVING COUNT(*) > 1
                """,
                list(COURSE_IDS),
            )
            if collisions:
                raise AssertionError(f"остались коллизии order_position: {collisions}")

            for cid in COURSE_IDS:
                seq = await conn.fetch(
                    "SELECT order_position FROM tasks WHERE course_id = $1 ORDER BY order_position",
                    cid,
                )
                positions = [row["order_position"] for row in seq]
                expected = list(range(1, len(positions) + 1))
                if positions != expected:
                    raise AssertionError(
                        f"course_id={cid}: order_position не 1..N без пропусков: {positions[:10]}..."
                    )
                print(f"course_id={cid}: order_position = 1..{len(positions)} без пропусков и дублей — OK")

            print("\nOK: пересчёт применён, инвариант БД восстановлен, коллатералей нет.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply для записи)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main("--apply" in sys.argv))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
