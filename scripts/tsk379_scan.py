# -*- coding: utf-8 -*-
"""tsk-379, шаг 1: разбор служебной шапки импорта в условии заданий (read-only).

ЗАЧЕМ
В `task_content.stem` партий `crylov` и `tg:ege` осела шапка импорта вида
«Задание 24_24613 КЕГЭ. Уровень сложный.» (иногда «Решение задания 9_58322 Уровень
простой») перед самим условием. Утечки ответа нет — это выяснено в [[tsk-379]] отдельно, —
но ученик читает её как часть задачи, а «Решение задания N» прямо вводит в заблуждение:
дальше идёт условие, а не решение. Уровень сложности дублирует поле `difficulty_id`
(канон уже подтверждён по обеим партиям — [[tsk-381]], [[tsk-382]]).

ПОЧЕМУ ДВА РЕЖИМА СРЕЗА, А НЕ ОДИН
Шапка либо занимает ВЕСЬ первый содержательный `<p>…</p>` (тогда убирается целиком,
вместе с закрывающим тегом и последующим пробелом — иначе остаётся пустой параграф),
либо делит `<p>` с реальным текстом через `<br>` (частый случай live-данных: следом идёт
«Вариант …», комментарий оператора или сразу вопрос). Во втором случае режется только
сама шапка «Задание/Решение задания … Уровень X» плюс один соседний `<br>`, а всё
остальное в параграфе остаётся — резать «от маркера до конца» запрещает урок [[tsk-370]]
(обрыв условия снаружи не виден).

Границы среза: захват НЕ пересекает теги, кроме `<br>` — если между «Задание» и «Уровень»
встречается `<a>`, `<strong>` или граница параграфа, шапка на этой записи не считается
разобранной и попадает в `unmatched` для ручного разбора.

Ничего не пишет в БД. На выходе JSON для шага 2 (`tsk379_fix_stems.py`).

Запуск:  python scripts/tsk379_scan.py --out <файл.json>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk370_scan import ASK_RE, dsn, strip_html  # noqa: E402

LEVEL_WORD = r"(?:простой|лёгкий|легкий|средний|сложный)"
LEADER = r"(?:Решение\s+задания|Задание)"

# Шапка занимает ровно весь параграф — убирается вместе с тегами и хвостовым пробелом.
WHOLE_HEADER_P_RE = re.compile(
    rf"<p>\s*{LEADER}\s+(?:<br\s*/?>|[^<])*?Уровень\s+{LEVEL_WORD}\.?\s*</p>\s*",
    re.IGNORECASE,
)
# Шапка — начало параграфа, дальше в том же <p> реальный текст через <br>.
INLINE_HEADER_RE = re.compile(
    rf"{LEADER}\s+(?:<br\s*/?>|[^<])*?Уровень\s+{LEVEL_WORD}\.?\s*(?:<br\s*/?>\s*)?",
    re.IGNORECASE,
)
# Тем же способом ловится шапка без уровня — только у crylov, там номер/сборник есть,
# а строки «Уровень …» нет (примеры 9525/9528/9530 из tsk-382).
LEVELLESS_LEADER = r"Задание\s+\d+[\w_]*\s+Сборник\s+Крылова\s+С\.?С\.?\s+вариант\s+\d+\.?"
WHOLE_HEADER_NOLEVEL_P_RE = re.compile(
    rf"<p>\s*{LEVELLESS_LEADER}\s*</p>\s*", re.IGNORECASE)
INLINE_HEADER_NOLEVEL_RE = re.compile(
    rf"{LEVELLESS_LEADER}\s*(?:<br\s*/?>\s*)?", re.IGNORECASE)

# «Уровень …» где-то в тексте — сигнал кандидата на разбор (широкий, дальше фильтруется).
CANDIDATE_RE = re.compile(r"Уровень\s+" + LEVEL_WORD, re.IGNORECASE)
LEADER_ANY_RE = re.compile(LEADER, re.IGNORECASE)


def cut_header(stem: str) -> tuple[str, list[str]]:
    """Условие без шапки импорта. Возвращает (новое условие, что срезано)."""
    removed: list[str] = []
    out = stem

    for whole_re, inline_re in (
        (WHOLE_HEADER_P_RE, INLINE_HEADER_RE),
        (WHOLE_HEADER_NOLEVEL_P_RE, INLINE_HEADER_NOLEVEL_RE),
    ):
        hit = [m.group(0) for m in whole_re.finditer(out)]
        out = whole_re.sub("", out)
        hit2 = [m.group(0) for m in inline_re.finditer(out)]
        out = inline_re.sub("", out)
        removed += hit + hit2

    return out, removed


async def main(out_path: Path) -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(
            "SELECT id, external_uid, "
            "       task_content->>'stem' AS stem, "
            "       difficulty_id "
            "FROM tasks WHERE is_active AND ("
            "     split_part(external_uid, ':', 1) IN ('crylov', 'tg')"
            "  OR task_content->>'stem' ~* 'Решение\\s+задания\\s+\\d'"
            ") ORDER BY id")
    finally:
        await conn.close()

    report: dict[str, list[dict]] = {"fixed": [], "unmatched": [], "levelless": []}
    for r in rows:
        stem = r["stem"] or ""
        has_candidate = bool(CANDIDATE_RE.search(stem)) or bool(
            re.search(r"Решение\s+задания\s+\d", stem, re.IGNORECASE))
        has_levelless = bool(WHOLE_HEADER_NOLEVEL_P_RE.search(stem)) or bool(
            re.search(LEVELLESS_LEADER, stem, re.IGNORECASE) and not has_candidate)
        if not has_candidate and not has_levelless:
            continue

        item = {"id": r["id"], "external_uid": r["external_uid"],
                 "difficulty_id": r["difficulty_id"], "len_before": len(stem)}
        new, removed = cut_header(stem)
        left = strip_html(new)

        if not removed:
            report["unmatched"].append(item | {"stem_head": stem[:200]})
            continue

        entry = item | {
            "removed": removed,
            "len_after": len(new),
            "residual_leader": bool(LEADER_ANY_RE.search(strip_html(new))),
            "residual_uroven": bool(re.search(r"Уровень\s+" + LEVEL_WORD, new, re.IGNORECASE)),
            "has_ask": bool(ASK_RE.search(left)),
            "text_len_before": len(strip_html(stem)),
            "text_len_after": len(left),
            "head_after": left[:160],
        }
        key = "levelless" if not re.search(r"Уровень\s+" + LEVEL_WORD, "".join(removed), re.I) else "fixed"
        report[key].append(entry)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    total = len(report["fixed"]) + len(report["levelless"])
    print(f"Кандидатов разобрано: {total}, из них с шапкой без уровня: {len(report['levelless'])}")
    print(f"Не разобрано (unmatched, нужен ручной разбор): {len(report['unmatched'])}")
    for u in report["unmatched"]:
        print(f"  {u['id']} {u['external_uid']}: {u['stem_head'][:120]!r}")
    bad = [e for e in report["fixed"] + report["levelless"]
           if e["residual_leader"] or e["residual_uroven"] or not e["has_ask"]]
    print(f"Подозрительных после среза (остаток шапки / нет вопроса): {len(bad)}")
    for b in bad:
        print(f"  {b['id']} {b['external_uid']}: residual_leader={b['residual_leader']} "
              f"residual_uroven={b['residual_uroven']} has_ask={b['has_ask']}")
    print(f"\nОтчёт: {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    asyncio.run(main(ap.parse_args().out))
