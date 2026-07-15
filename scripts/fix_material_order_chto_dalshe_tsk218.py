"""tsk-218: «Что дальше» в конец раздела — разделы 10 и 11 курса Python-подростки (LMS prod).

Дефект (системный свип после раздела-3/4): итоговый материал «Что дальше» стоит НЕ
последним — за ним ещё содержательный материал.
  - Раздел 10 (892): «Что дальше» (1230, поз.4) → после него «Секрет углов» (1229, поз.5)
  - Раздел 11 (893): «Что дальше» (1234, поз.4) → после него «Случайные цвета» (1233, поз.5)

Фикс: сдвинуть «Что дальше» на последнюю позицию раздела. Триггер
set_material_order_position сдвинёт задний материал вверх.

Раздел 13 (895) — НЕ дефект: там весь раздел называется «Что дальше», его интро-материал
закономерно первый (проверено, исключён).

Запуск: dry-run по умолчанию; --apply для записи (нужен DBCHECK_OK=1 из-за хука).
"""
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

# (course_id, material_id «Что дальше», target_pos, ожидаемый порядок id)
MOVES: list[tuple[int, int, int, list[int]]] = [
    (892, 1230, 5, [1227, 1228, 1229, 1230]),
    (893, 1234, 5, [1231, 1232, 1233, 1234]),
]


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def _snapshot(conn: asyncpg.Connection, course_id: int) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT id, order_position, title FROM materials "
        "WHERE course_id=$1 AND is_active ORDER BY order_position",
        course_id,
    )


def _print(rows: list[asyncpg.Record], label: str) -> None:
    print(f"  {label}: " + " -> ".join(f"{r['id']}({r['order_position']})" for r in rows))


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            for course_id, mat_id, target, expected in MOVES:
                print(f"\nРаздел course_id={course_id}:")
                _print(await _snapshot(conn, course_id), "ДО   ")
                await conn.execute(
                    "UPDATE materials SET order_position=$1 WHERE id=$2 AND course_id=$3",
                    target, mat_id, course_id,
                )
                after = await _snapshot(conn, course_id)
                _print(after, "ПОСЛЕ")
                got = [r["id"] for r in after]
                if got != expected:
                    raise RuntimeError(
                        f"course {course_id}: порядок не совпал.\n"
                        f"  ожидали: {expected}\n  вышло:   {got}"
                    )
                print(f"  VERIFY OK: {got}")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply для записи)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО (2 раздела).")
    finally:
        await conn.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    try:
        asyncio.run(main(apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
