"""tsk-442/455: слияние двух учётных записей (write, протокол /db-check).

CLI-обёртка над `app.services.user_merge_service` (там же — правила
переноса, список таблиц, докстринг с деталями по каждому классу таблиц;
tsk-455 добавил автоматический вызов той же логики сразу при регистрации
дубля с высокой уверенностью — `check_and_merge_duplicate_on_registration`).
Этот скрипт остаётся для РУЧНОГО разбора кандидатов, которые не прошли
порог автослияния.

Режимы:
- (по умолчанию) — dry-run: печатает обе учётки + количество строк в каждой
  затрагиваемой таблице у source. Ничего не пишет.
- `--apply` — выполняет перенос в ОДНОЙ транзакции + независимая
  read-only верификация после коммита.

Запуск (см. `.env` DATABASE_URL — сначала на dev, потом на проде):
    venv/bin/python scripts/merge_users.py --source-id 123 --target-id 456
    DBCHECK_OK=1 venv/bin/python scripts/merge_users.py --source-id 123 --target-id 456 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8-sig")

from app.db.session import async_session_factory  # noqa: E402
from app.services.user_merge_service import (  # noqa: E402
    CONFLICT_MOVES,
    DELETE_ON_MERGE,
    SIMPLE_MOVES,
    apply_merge as _apply,
    count_rows as _count,
    fetch_user as _fetch_user,
    verify_merge as _verify,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("merge_users")


async def _preflight(db, source, target) -> None:
    print(f"source: id={source.id} «{source.full_name}» email={source.email} "
          f"tg_id={source.tg_id} is_active={source.is_active}")
    print(f"target: id={target.id} «{target.full_name}» email={target.email} "
          f"tg_id={target.tg_id} is_active={target.is_active}\n")
    print("Строк у source по таблицам (будут перенесены/удалены):")
    total = 0
    for table, column in SIMPLE_MOVES:
        n = await _count(db, table, column, source.id)
        if n:
            print(f"  {table}.{column}: {n}")
            total += n
    for table, column, _other in CONFLICT_MOVES:
        n = await _count(db, table, column, source.id)
        if n:
            print(f"  {table}.{column}: {n} (с проверкой конфликтов у target)")
            total += n
    for table, column in DELETE_ON_MERGE:
        n = await _count(db, table, column, source.id)
        if n:
            print(f"  {table}.{column}: {n} (будет УДАЛЕНО, не перенесено)")
    print(f"\nВсего строк на перенос: {total}")


async def _run(
    source_id: int, target_id: int, apply: bool, *, session_factory=None,
) -> None:
    """`session_factory` — точка подмены источника сессий (тесты передают
    фабрику, привязанную к своему соединению/транзакции — см.
    `lesson_occurrence_generator_tick` docstring про ту же причину)."""
    factory = session_factory or async_session_factory
    if source_id == target_id:
        print("source-id и target-id совпадают — нечего сливать.")
        return

    async with factory() as db:
        source = await _fetch_user(db, source_id)
        target = await _fetch_user(db, target_id)
        if source is None or target is None:
            print(f"Не найден пользователь: source={source}, target={target}")
            return
        if not source.is_active:
            print(f"source id={source_id} уже деактивирован (merged_into_user_id="
                  f"{source.merged_into_user_id}) — повторное слияние не выполняется.")
            return
        if not target.is_active:
            print(f"target id={target_id} сам деактивирован — выбери другую цель.")
            return

        await _preflight(db, source, target)

        if not apply:
            print("\n[dry-run] Ничего не изменено. Запустите с --apply для переноса.")
            return

        await _apply(db, source_id, target_id)
        await db.commit()
        logger.info("merge_users: source=%d -> target=%d применено", source_id, target_id)

    async with factory() as verify_db:
        await _verify(verify_db, source_id, target_id)
        print(f"Верификация OK: source id={source_id} деактивирован, данных не осталось.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", type=int, required=True, help="Учётка, которая деактивируется")
    parser.add_argument("--target-id", type=int, required=True, help="Учётка-получатель данных")
    parser.add_argument("--apply", action="store_true", help="Выполнить перенос (по умолчанию — dry-run)")
    args = parser.parse_args()
    asyncio.run(_run(args.source_id, args.target_id, args.apply))


if __name__ == "__main__":
    main()
