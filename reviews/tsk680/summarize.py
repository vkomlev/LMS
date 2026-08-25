# -*- coding: utf-8 -*-
"""Свод ряда замеров первого куска (tsk-680): доля промахов по модели, форме и часу."""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict

BUDGET = 12.0


def load(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def stat(values: list[float]) -> str:
    if not values:
        return "—"
    values = sorted(values)
    p90 = values[min(len(values) - 1, int(round(0.9 * (len(values) - 1))))]
    return (f"медиана {statistics.median(values):.1f} c, p90 {p90:.1f} c, "
            f"худший {values[-1]:.1f} c")


def main() -> None:
    rows = load(sys.argv[1])
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        by_key[(row["model"], row.get("form", "A"))].append(row)

    print(f"Всего замеров: {len(rows)}; окно: {rows[0]['ts']} — {rows[-1]['ts']}\n")
    print(f"{'модель':32} {'форма':6} {'n':>4} {'промахов >12 c':>16}  распределение")
    for (model, form), items in sorted(by_key.items()):
        good = [r["first_at"] for r in items if r["first_at"] is not None]
        bad = [r for r in items if r["verdict"] == "bad"]
        share = f"{len(bad)}/{len(items)} ({100 * len(bad) / len(items):.0f}%)"
        print(f"{model:32} {form:6} {len(items):>4} {share:>16}  {stat(good)}")

    print("\nПо часам (UTC), голова цепочки:")
    by_hour: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["model"].endswith("claude-sonnet-4.6"):
            by_hour[row["ts"][11:13]].append(row)
    for hour, items in sorted(by_hour.items()):
        bad = [r for r in items if r["verdict"] == "bad"]
        vals = [r["first_at"] for r in items if r["first_at"] is not None]
        worst = f"худший {max(vals):.1f} c" if vals else "нет значений"
        print(f"  {hour}:00 — замеров {len(items):>3}, промахов {len(bad):>3}, {worst}")

    errors = [r for r in rows if r.get("error")]
    if errors:
        print(f"\nОшибок вызова: {len(errors)}")
        for row in errors[:10]:
            print(f"  {row['ts']} {row['model']} {row.get('form')}: {row['error'][:120]}")


if __name__ == "__main__":
    main()
