"""tsk-210 (класс B, вуз-курс 1248): убрать ответ из формулировки задания 7422.

Дефект (QA): «Современные процессоры и системы почти все 64-битные. Сколько это бит?»
— ответ (64) дословно в вопросе → задание ничего не проверяет.

Источник (материал 3023): разрядность подаётся так — «у старых машин 32 бита, у
64-битного ковш ВДВОЕ шире». Переформулировка теста без числа 64: дать 32 у старых +
«вдвое больше» → ученик получает 64 сам (recall/мини-счёт), опираясь на материал.

accepted_answers остаётся [64]. Меняется только stem.
Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

TASK_ID = 7422
OLD_STEM = "Современные процессоры и системы почти все 64-битные. Сколько это бит? Впиши число."
NEW_STEM = (
    "У старых компьютеров разрядность 32 бита, а у современных систем — вдвое больше. "
    "Сколько бит у современных систем? Впиши число."
)


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            stem = await conn.fetchval(
                "SELECT task_content->>'stem' FROM tasks WHERE id=$1", TASK_ID
            )
            if stem is None:
                raise RuntimeError(f"задание {TASK_ID}: не найдено")
            if "64" not in stem:
                raise RuntimeError(f"задание {TASK_ID}: stem уже без «64» — возможно уже правлено")
            if stem.strip() != OLD_STEM:
                raise RuntimeError(
                    f"задание {TASK_ID}: текущий stem не совпал с ожидаемым.\n  есть: {stem!r}"
                )
            if "64" in NEW_STEM:
                raise RuntimeError("новый stem всё ещё содержит «64» — стоп")
            await conn.execute(
                "UPDATE tasks SET task_content = jsonb_set(task_content,'{stem}', to_jsonb($2::text)) WHERE id=$1",
                TASK_ID, NEW_STEM,
            )
            check = await conn.fetchval(
                "SELECT task_content->>'stem' FROM tasks WHERE id=$1", TASK_ID
            )
            ans = await conn.fetchval(
                "SELECT solution_rules->'short_answer'->'accepted_answers'->0->>'value' FROM tasks WHERE id=$1",
                TASK_ID,
            )
            assert check == NEW_STEM and "64" not in check, "stem не применился"
            assert ans == "64", f"ответ изменился неожиданно: {ans!r}"
            print(f"OK задание {TASK_ID}: stem обновлён, «64» нет; ответ по-прежнему {ans!r}")
            print(f"  новый stem: {check}")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply)")
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
