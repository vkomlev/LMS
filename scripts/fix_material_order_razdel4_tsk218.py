"""tsk-218: реордер материалов раздела 4 (Строки, course_id=835) курса Python-подростки.

Дефект (всплыл на верификации терминов): мат 924 «Меняем регистр и склеиваем» стоит на
позиции 11 — ПОСЛЕ итогового «Что дальше» (962, поз.7). Тот же класс, что раздел-3.

Фикс: сдвинуть 924 на позицию 7 (сразу после 961 «count/find/replace» — строковые методы
рядом). Триггер set_material_order_position сдвинет 962 в конец. Мат 961 (поз.6) остаётся
ПЕРВЫМ методом → вставленный туда ввод понятия «метод» не ломается.

Было:  920(1) 921(2) 922(3) 923(4) 961(6) 962(7) 924(11)
Станет: 920(1) 921(2) 922(3) 923(4) 961(6) 924(7) 962(8)  -> «Что дальше» последний

Запуск: dry-run по умолчанию; --apply для записи (нужен DBCHECK_OK=1 из-за хука).
"""
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

COURSE_ID = 835
MATERIAL_ID = 924          # «Меняем регистр и склеиваем»
TARGET_POSITION = 7

EXPECTED_ORDER = [920, 921, 922, 923, 961, 924, 962]


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def _snapshot(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT id, order_position, title FROM materials "
        "WHERE course_id=$1 AND is_active ORDER BY order_position",
        COURSE_ID,
    )


def _print(rows: list[asyncpg.Record], label: str) -> None:
    print(f"\n{label}:")
    for r in rows:
        print(f"  pos={r['order_position']:>2}  id={r['id']}  {r['title']}")


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        _print(await _snapshot(conn), "ДО")
        async with conn.transaction():
            await conn.execute(
                "UPDATE materials SET order_position=$1 WHERE id=$2 AND course_id=$3",
                TARGET_POSITION, MATERIAL_ID, COURSE_ID,
            )
            after = await _snapshot(conn)
            _print(after, "ПОСЛЕ (в транзакции)")
            got = [r["id"] for r in after]
            if got != EXPECTED_ORDER:
                raise RuntimeError(
                    f"Порядок не совпал.\n  ожидали: {EXPECTED_ORDER}\n  вышло:   {got}"
                )
            print(f"\nVERIFY OK: линейный порядок = {got}")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply для записи)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    try:
        asyncio.run(main(apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
