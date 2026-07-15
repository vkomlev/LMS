"""tsk-218 (хвост DEFER): двоеточие обязательно в SA-заданиях-заголовках Python-подростки.

Дефект (QA «засчитывается без двоеточия»): 6 SA-заданий просят написать строку-ЗАГОЛОВОК
(`for/def/while … :`), но нормализация `strip_punctuation` стирает двоеточие → ответ без
`:` засчитывается (а это SyntaxError). Двоеточие — сама суть этих заданий.

Фикс: убрать `strip_punctuation` из short_answer.normalization у ЭТИХ 6 заданий →
двоеточие и скобки становятся обязательными (нормализация применяется одинаково к
эталону и ответу — checking_service._normalize_text). Остаётся trim+lower+collapse_spaces.

НЕ трогаем print(...)-задания: там снятие strip_punctuation дало бы ложные отказы на
вариантах пробелов/запятых. Только чистые заголовки с детерминированной формой.

Задания: 5499 (for range10), 5527 (for in spisok), 5633 (def privet), 5634 (def udar),
5757 (for range36), 5768 (while True).

Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import json
import os
import sys

import asyncpg
from dotenv import load_dotenv

TASK_IDS = [5499, 5527, 5633, 5634, 5757, 5768]
NEW_NORM = ["trim", "lower", "collapse_spaces"]


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            for tid in TASK_IDS:
                row = await conn.fetchval(
                    "SELECT solution_rules FROM tasks WHERE id=$1", tid
                )
                rules = json.loads(row) if isinstance(row, str) else row
                sa = (rules or {}).get("short_answer") or {}
                norm = sa.get("normalization") or []
                answer = (sa.get("accepted_answers") or [{}])[0].get("value")
                if "strip_punctuation" not in norm:
                    print(f"  SKIP {tid}: strip_punctuation уже нет (norm={norm})")
                    continue
                if not (isinstance(answer, str) and answer.rstrip().endswith(":")):
                    raise RuntimeError(
                        f"задание {tid}: ответ {answer!r} не заканчивается на ':' — "
                        f"не заголовок, прерываю (проверь список)"
                    )
                # Обновляем normalization -> без strip_punctuation
                await conn.execute(
                    "UPDATE tasks SET solution_rules = "
                    "jsonb_set(solution_rules, '{short_answer,normalization}', $2::jsonb) "
                    "WHERE id=$1",
                    tid, json.dumps(NEW_NORM),
                )
                check = await conn.fetchval(
                    "SELECT solution_rules->'short_answer'->'normalization' FROM tasks WHERE id=$1",
                    tid,
                )
                check_list = json.loads(check) if isinstance(check, str) else check
                assert "strip_punctuation" not in check_list, f"задание {tid}: не применилось"
                print(f"  OK {tid}: ответ={answer!r}  norm: -strip_punctuation → {check_list}")
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
