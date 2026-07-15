"""tsk-218: реордер материалов раздела 3 (course_id=834) курса Python-подростки.

Дефект (naive-review P1): «Числа: считаем» (int/арифметика) стоит ПОСЛЕ «Что дальше»
и после «Деление //,%,**», из-за чего продвинутые операции идут раньше базовых, а
итоговый материал «Что дальше» — не последний.

Фикс: сдвинуть материал 918 «Числа: считаем» на позицию 4 (после input, до деления).
Триггер set_material_order_position сам сдвинет 919 и 963 вниз.

Было:  916(1) 917(3) 919(4) 963(5) 918(6)
Станет: 916(1) 917(3) 918(4) 919(5) 963(6)  -> интро,input,числа,деление,что-дальше

Запуск: dry-run по умолчанию; --apply для записи (нужен DBCHECK_OK=1 из-за хука).
"""
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

COURSE_ID = 834
MATERIAL_ID = 918          # «Числа: считаем»
TARGET_POSITION = 4

# Ожидаемый линейный порядок id после фикса (по order_position ASC)
EXPECTED_ORDER = [916, 917, 918, 919, 963]


def _dsn() -> str:
    # DATABASE_URL прокинут снаружи (прод-DSN из .mcp.json); .env только дополняет
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    dsn = os.environ["DATABASE_URL"]
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


async def _snapshot(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT id, order_position, title FROM materials "
        "WHERE course_id=$1 ORDER BY order_position",
        COURSE_ID,
    )


def _print(rows: list[asyncpg.Record], label: str) -> None:
    print(f"\n{label}:")
    for r in rows:
        print(f"  pos={r['order_position']:>2}  id={r['id']}  {r['title']}")


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        before = await _snapshot(conn)
        _print(before, "ДО")

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
                    f"Порядок не совпал с ожидаемым.\n  ожидали: {EXPECTED_ORDER}\n  вышло:   {got}"
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
        # dry-run rollback — это не ошибка исполнения
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
