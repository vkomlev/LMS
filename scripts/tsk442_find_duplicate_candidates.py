"""tsk-442: список кандидатов на дубль-аккаунт (расширенный маппинг ФИО).

READ-ONLY — ничего не пишет в БД, безопасно запускать в любой момент (в т.ч.
регулярно вручную) на dev и на проде. Печатает пары пользователей с похожим
ФИО (без учёта порядка слов, опечаток, неполной фамилии; отчество в
сравнении не участвует) — для ручного разбора оператором/методистом. Если
пара похожа на "плавающий аккаунт (без identity_link) + только что
зарегистрировавшийся дубль" — решение о слиянии принимает человек, скрипт
только подсвечивает кандидатов (см. `app/services/users_dedup_service.py`).

Слияние — отдельный write-скрипт `scripts/merge_users.py` (по протоколу
/db-check), сюда не входит.

Запуск:
    venv/bin/python scripts/tsk442_find_duplicate_candidates.py [--threshold 0.72]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8-sig")

from app.db.session import async_session_factory  # noqa: E402
from app.services.users_dedup_service import (  # noqa: E402
    DEFAULT_MATCH_THRESHOLD,
    find_duplicate_candidates,
)


async def _run(threshold: float) -> None:
    async with async_session_factory() as db:
        candidates = await find_duplicate_candidates(db, threshold=threshold)

    if not candidates:
        print(f"Кандидатов на дубль не найдено (порог={threshold}).")
        return

    print(f"Найдено кандидатов: {len(candidates)} (порог={threshold})\n")
    for c in candidates:
        flag_a = "без входа" if not c.user_a_has_identity else "уже входил(а)"
        flag_b = "без входа" if not c.user_b_has_identity else "уже входил(а)"
        print(
            f"[{c.score:.2f}] id={c.user_a_id} «{c.user_a_name}» ({flag_a})  "
            f"<->  id={c.user_b_id} «{c.user_b_name}» ({flag_b})"
        )
    print(
        "\nЭто только список кандидатов — слияние делает оператор вручную "
        "через scripts/merge_users.py после проверки."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_MATCH_THRESHOLD,
        help=f"Порог похожести 0..1 (по умолчанию {DEFAULT_MATCH_THRESHOLD})",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.threshold))


if __name__ == "__main__":
    main()
