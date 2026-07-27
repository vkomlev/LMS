"""tsk-442: автослияние дублей с высокой уверенностью (write, протокол /db-check).

По итогам первого реального прогона `tsk442_find_duplicate_candidates.py`
на проде (2026-07-27) оператор попросил автослияние для пар с высокой
уверенностью — остальное остаётся на ручной разбор, как и раньше.

Автослияние применяется ТОЛЬКО к парам, прошедшим обязательную защиту
`users_dedup_service.select_auto_merge_pairs` (не опция — без неё первый же
прогон слил бы два РЕАЛЬНЫХ разных аккаунта оператора, см. докстринг
сервиса): score >= порога, ровно одна сторона с identity_link, пара
единственная в обе стороны. Всё остальное печатается как обычный
кандидат-список (см. `tsk442_find_duplicate_candidates.py`) — ничего не
сливается автоматически.

Режимы:
- (по умолчанию) — dry-run: печатает план автослияния + ручной список.
  Ничего не пишет.
- `--apply` — сливает каждую auto-пару (переиспользует `merge_users._apply`/
  `_verify`), печатает результат по каждой. Одна пара падает — печатает
  ошибку и переходит к следующей (не блокирует остальные).

Запуск:
    venv/bin/python scripts/tsk442_auto_merge_duplicates.py
    DBCHECK_OK=1 venv/bin/python scripts/tsk442_auto_merge_duplicates.py --apply
    venv/bin/python scripts/tsk442_auto_merge_duplicates.py --auto-threshold 0.85
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8-sig")

from app.db.session import async_session_factory  # noqa: E402
from app.services.users_dedup_service import (  # noqa: E402
    DEFAULT_AUTO_MERGE_THRESHOLD,
    DEFAULT_MATCH_THRESHOLD,
    find_duplicate_candidates,
    select_auto_merge_pairs,
)

import merge_users  # noqa: E402


def _print_manual(candidates) -> None:
    if not candidates:
        return
    print(f"\nНа ручной разбор: {len(candidates)}")
    for c in candidates:
        flag_a = "без входа" if not c.user_a_has_identity else "уже входил(а)"
        flag_b = "без входа" if not c.user_b_has_identity else "уже входил(а)"
        print(
            f"  [{c.score:.2f}] id={c.user_a_id} «{c.user_a_name}» ({flag_a})  "
            f"<->  id={c.user_b_id} «{c.user_b_name}» ({flag_b})"
        )


async def _run(detect_threshold: float, auto_threshold: float, apply: bool) -> None:
    async with async_session_factory() as db:
        candidates = await find_duplicate_candidates(db, threshold=detect_threshold)
    auto_pairs, manual = select_auto_merge_pairs(candidates, auto_threshold=auto_threshold)

    if not auto_pairs:
        print(f"Пар для автослияния не найдено (порог автослияния={auto_threshold}).")
        _print_manual(manual)
        return

    print(f"Автослияние: {len(auto_pairs)} пар(а) (порог={auto_threshold})\n")
    for p in auto_pairs:
        print(
            f"  [{p.score:.2f}] source id={p.source_id} «{p.source_name}» (без входа) "
            f"-> target id={p.target_id} «{p.target_name}» (уже входил(а))"
        )

    if not apply:
        print("\n[dry-run] Ничего не слито. Запустите с --apply для реального слияния.")
        _print_manual(manual)
        return

    print()
    for p in auto_pairs:
        try:
            await merge_users._run(p.source_id, p.target_id, apply=True)
        except Exception as exc:  # noqa: BLE001 — одна упавшая пара не должна блокировать остальные
            print(f"  ОШИБКА при слиянии {p.source_id} -> {p.target_id}: {exc}")

    _print_manual(manual)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--detect-threshold", type=float, default=DEFAULT_MATCH_THRESHOLD,
        help=f"Порог для попадания в кандидаты вообще (по умолчанию {DEFAULT_MATCH_THRESHOLD})",
    )
    parser.add_argument(
        "--auto-threshold", type=float, default=DEFAULT_AUTO_MERGE_THRESHOLD,
        help=f"Порог автослияния (по умолчанию {DEFAULT_AUTO_MERGE_THRESHOLD}; "
             f"оператор обсуждал диапазон 0.85-0.9)",
    )
    parser.add_argument("--apply", action="store_true", help="Выполнить слияние (по умолчанию — dry-run)")
    args = parser.parse_args()
    asyncio.run(_run(args.detect_threshold, args.auto_threshold, args.apply))


if __name__ == "__main__":
    main()
