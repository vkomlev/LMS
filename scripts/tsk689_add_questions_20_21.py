# -*- coding: utf-8 -*-
"""tsk-689, этап 2: дописать вопросы 20 и 21 заданиям базового курса 147.

ЧТО ДЕЛАЕМ
Пять заданий блока 19-21 несут ОДИН вопрос вместо трёх. При этом сам вопрос у
них разный: у 3505, 3981 и 3949 это вопрос 19, у 2383 — вопрос 20, у 2385 —
вопрос 21. Поэтому имеющийся вопрос НЕ переписывается: он получает свой номер и
остаётся дословно, а недостающие два дописываются типовыми формулировками блока
(взяты у соседних заданий курса, напр. 3470 и 9518) — по аналогии, не выдумкой.

ОТКУДА ОТВЕТЫ
Посчитаны эталонным алгоритмом оператора (`scripts/tsk689_games_solver.py`).
Решатель прошёл калибровку: на 20 заданиях курса 147, где все три ответа уже
есть в базе, он воспроизвёл их без единого расхождения; на каждом из пяти
заданий ниже он, кроме того, воспроизвёл ИМЕЮЩИЙСЯ ответ — только после этого
брались новые. Соответствие вопроса и признака:
    19 (вид A) f(S) = -1   ·   20 f(S) = 2   ·   21 f(S) = -2.

СМЕНА ТИПА
Три вопроса = три части ответа, поэтому `SA_COM` → `TBL_COM` с `table.columns=1`
(так устроены все трёхвопросные задания блока). По коду проверки это «смена типа
без правки правил»: эталон и нормализация остаются в том же `short_answer`, а
разбор ответа только расширяется (`_check_table_answer`, инвариант tsk-366/383).
Прежние формы ответа сохраняются отдельными `accepted_answers`, ни одна не
удаляется.

ЧЕГО НЕ ДЕЛАЕМ
Не трогаем задание 2384: у него потеряно САМО описание игры («В игре, описанной
в задании 19...»), восстановить его подбором не удалось — значит, дописывать там
нечего, это вопрос к оператору. Отдельным шагом (`--park-2384`) оно выводится из
обязательных, чтобы не быть тупиком в учебном пути.

Запуск: dry-run по умолчанию; `--apply` — запись (нужен префикс DBCHECK_OK=1).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

def q19_std(first: str, first_gen: str, second: str) -> str:
    return (
        "<p><strong>Задание 19.</strong></p>"
        f"<p>Укажите минимальное значение S, при котором {first} не может выиграть за "
        f"один ход, но при любом ходе {first_gen} {second} может выиграть своим первым "
        "ходом.</p>"
    )


def q20_std(first: str, first_gen: str, second_gen: str) -> str:
    return (
        "<p><strong>Задание 20.</strong></p>"
        "<p>Для игры, описанной в задании 19, найдите два наименьших значения S, при "
        f"которых у {first_gen} есть выигрышная стратегия, причём одновременно "
        "выполняются два условия:</p>"
        f"<p>— {first} не может выиграть за один ход;</p>"
        f"<p>— {first} может выиграть своим вторым ходом независимо от того, как будет "
        f"ходить {second_gen}.</p>"
        "<p>Найденные значения запишите в ответе в порядке возрастания.</p>"
    )


def q21_std(first_gen: str, second_gen: str) -> str:
    return (
        "<p><strong>Задание 21.</strong></p>"
        "<p>Для игры, описанной в задании 19, найдите минимальное значение S, при "
        "котором одновременно выполняются два условия:</p>"
        f"<p>— у {second_gen} есть выигрышная стратегия, позволяющая ему выиграть первым "
        f"или вторым ходом при любой игре {first_gen};</p>"
        f"<p>— у {second_gen} нет стратегии, которая позволит ему гарантированно "
        "выиграть первым ходом.</p>"
    )


# Имена игроков в заданиях разные (Петя/Ваня, Алиса/Боб, Кузнец/Садовник) —
# формулировка подставляет их, а не навязывает Петю с Ваней чужой игре.
NAMES_DEFAULT = ("Петя", "Пети", "Ваня", "Вани")

Q19_STD = q19_std("Петя", "Пети", "Ваня")
Q20_STD = q20_std("Петя", "Пети", "Ваня")
Q21_STD = q21_std("Пети", "Вани")
HEADERS = {19: "<p><strong>Задание 19.</strong></p>",
           20: "<p><strong>Задание 20.</strong></p>",
           21: "<p><strong>Задание 21.</strong></p>"}

# id -> описание правки
PLAN: Dict[int, dict] = {
    3505: {
        "slot": 19,
        "marker": "Укажите минимальное значение $S$, при котором Петя не может выиграть",
        "answers": ["60\n62\n63\n64"],
        "note": "19 — имеющийся (min f=-1 = 60); 20 — два наименьших f=2 (62, 63); 21 — min f=-2 (64)",
    },
    3981: {
        "slot": 19,
        "marker": "Известно, что Ваня выиграл своим первым ходом после неудачного первого хода Пети.",
        "answers": ["8\n5\n7\n6"],
        "note": "19 — имеющийся (max по виду B = 8); 20 — f=2 (5, 7); 21 — min f=-2 (6)",
    },
    3949: {
        "slot": 19,
        "marker": "Найдите минимальное значение S, когда Петя мог выиграть первым ходом",
        "answers": ["8\n5\n6\n4"],
        "note": "19 — имеющийся (8); 20 — два наименьших f=2 (5, 6); 21 — min f=-2 (4)",
    },
    2383: {
        "slot": 20,
        "marker": "Найдите пять таких значений",
        "answers": [
            "22\n2324324445\n25",
            "22\n23 24 32 44 45\n25",
            "22\n23\n24\n32\n44\n45\n25",
        ],
        "note": "20 — имеющийся (пять значений f=2); 19 — min f=-1 (22); 21 — min f=-2 (25)",
    },
    2385: {
        "slot": 21,
        "marker": "Укажите два значения",
        "answers": [
            "14\n12\n13\n10 11",
            "14\n12\n13\n1011",
            "14\n12\n13\n10\n11",
        ],
        "note": "21 — имеющийся (10 и 11); 19 — min f=-1 (14); 20 — два наименьших f=2 (12, 13)",
    },
}

PARKED_TASK = 2384  # описание игры потеряно, дописывать нечего

# Курс 1397 «Сложные». Взяты ТОЛЬКО типовые игры с кучами камней, на которых
# решатель воспроизвёл имеющийся ответ. Не взяты: игры со словами, картами,
# графом, кубиком, тремя кучами и фишкой на плоскости (типовых формулировок 20 и
# 21 для них нет — пришлось бы выдумывать); задания с нестандартным вопросом
# (4160, 4222, 3879, 4007, 4013, 4077, 4278, 3554); 4036 и 4232 про конфеты —
# решатель НЕ воспроизвёл их имеющийся ответ, значит правила игры поняты неверно
# либо эталон неточен, и трогать их нельзя; 3380 — та же игра, что 2385 в
# базовом курсе, три вопроса сделали бы задания полными дублями.
PLAN_HARD: Dict[int, dict] = {
    3498: {
        "slot": 19,
        "marker": "Определите минимальное начальное количество камней",
        "names": ("Кузнец", "Кузнеца", "Садовник", "Садовника"),
        "answers": ["2427\n809\n2395\n2424"],
        "note": "19 — имеющийся (2427); 20 — два наименьших f=2 (809, 2395); 21 — min f=-2 (2424)",
    },
    3594: {
        "slot": 21,
        "marker": "Укажите такое значение S, при котором у Вани есть выигрышная стратегия",
        "names": NAMES_DEFAULT,
        "answers": ["20\n10\n18\n35"],
        "note": "21 — имеющийся (35); 19 — min f=-1 (20); 20 — два наименьших f=2 (10, 18)",
    },
    4187: {
        "slot": 19,
        "marker": "Известно, что Ваня выиграл своим первым ходом после неудачного первого хода Пети.",
        "names": NAMES_DEFAULT,
        "answers": ["28\n31\n33\n30"],
        "note": "19 — имеющийся (28); 20 — два наименьших f=2 (31, 33); 21 — min f=-2 (30)",
    },
    3518: {
        "slot": 19,
        "marker": "Необходимо определить максимальное значение",
        "names": ("Алиса", "Алисы", "Боб", "Боба"),
        "answers": ["18\n10\n11\n9"],
        "note": "19 — имеющийся (18); 20 — два наименьших f=2 (10, 11); 21 — min f=-2 (9)",
    },
    3571: {
        "slot": 19,
        "marker": "Известно, что Ваня выиграл своим первым ходом после неудачного хода Пети.",
        "names": NAMES_DEFAULT,
        "answers": ["26\n25\n39\n34"],
        "note": "19 — имеющийся (26); 20 — два наименьших f=2 (25, 39); 21 — min f=-2 (34)",
    },
    4262: {
        "slot": 19,
        "marker": "Укажите значение S, при котором Ваня выиграет первым ходом",
        "names": NAMES_DEFAULT,
        "answers": ["15\n17\n19\n21"],
        "note": "19 — имеющийся (15); 20 — два наименьших f=2 (17, 19); 21 — min f=-2 (21)",
    },
    3851: {
        "slot": 19,
        "marker": "Найдите максимальное значение S, когда Петя мог выиграть первым ходом",
        "names": NAMES_DEFAULT,
        "answers": ["4\n7\n8\n9"],
        "note": "19 — имеющийся (4); 20 — два наименьших f=2 (7, 8); 21 — min f=-2 (9)",
    },
}


def _dsn() -> str:
    """Прод-DSN learn: из окружения, иначе из `.mcp.json` (секрет не печатаем)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


def _split_at_question(stem: str, marker: str) -> tuple:
    """Делит стем на «описание игры» и «имеющийся вопрос».

    Маркер ищется с допуском на мягкий перенос (U+00AD) внутри слов и на любой
    пробельный символ между словами: часть заданий пришла с сайта-источника с
    расстановкой переносов внутри слов и неразрывными пробелами, и точное
    вхождение там не находится (проверено на 3505 — `position()` даёт 0).
    """
    words = [w for w in marker.split(" ") if w]
    pattern = r"[\s    ]+".join(
        "".join(re.escape(ch) + "­?" for ch in w) for w in words
    )
    m = re.search(pattern, stem)
    if not m:
        raise RuntimeError(f"Не нашёл начало вопроса по маркеру: {marker!r}")
    start = m.start()
    # Если вопрос начинается со СВОЕГО <p ...> — отрезаем вместе с ним. Условие
    # именно «между тегом и вопросом ничего нет»: иначе у задания, где весь стем
    # лежит в одном <p> (3505), в блок вопроса уехало бы описание игры целиком.
    tag = stem.rfind("<p", 0, start)
    if tag != -1 and re.fullmatch(r"<p[^>]*>\s*", stem[tag:start]):
        start = tag
    head, tail = stem[:start], stem[start:]
    return head, tail


def build_stem(stem: str, slot: int, marker: str, names=NAMES_DEFAULT) -> str:
    """Собирает стем из трёх заданий: имеющееся в своём слоте, два дописанных."""
    suffix = ""
    for closing in ("</body></html>", "</html>", "</body>"):
        if stem.rstrip().endswith(closing):
            stem = stem.rstrip()[: -len(closing)]
            suffix = closing
            break

    head, existing = _split_at_question(stem, marker)
    head = head.rstrip()
    if head and not head.endswith(">"):
        head += "</p>"

    # Вопрос, вырезанный из середины общего абзаца (3505), приходит без своего
    # открывающего тега, но с чужим закрывающим — открываем абзац сами.
    if not existing.lstrip().startswith("<"):
        existing = "<p>" + existing

    first, first_gen, second, second_gen = names
    blocks = {
        19: q19_std(first, first_gen, second),
        20: q20_std(first, first_gen, second_gen),
        21: q21_std(first_gen, second_gen),
    }
    blocks[slot] = HEADERS[slot] + existing
    return head + blocks[19] + blocks[20] + blocks[21] + suffix


async def main() -> int:
    parser = argparse.ArgumentParser(description="tsk-689: вопросы 20 и 21 в курсе 147")
    parser.add_argument("--apply", action="store_true", help="записать в прод-БД")
    parser.add_argument("--park-2384", action="store_true",
                        help="вывести 2384 из обязательных (описание игры потеряно)")
    parser.add_argument("--show", action="store_true", help="показать новый стем целиком")
    parser.add_argument("--batch", choices=("base", "hard"), default="base",
                        help="base — курс 147, hard — типовые задания курса 1397")
    args = parser.parse_args()

    plan = PLAN if args.batch == "base" else PLAN_HARD
    expected_course = 147 if args.batch == "base" else 1397

    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            "SELECT id, course_id, task_content, solution_rules FROM tasks "
            "WHERE id = ANY($1::int[])",
            list(plan),
        )
        by_id = {r["id"]: r for r in rows}
        if len(by_id) != len(plan):
            print("СТОП: нашлись не все задания из плана.")
            return 2

        updates: List[tuple] = []
        for task_id, spec in plan.items():
            row = by_id[task_id]
            if row["course_id"] != expected_course:
                print(f"СТОП: задание {task_id} не в курсе {expected_course} (сейчас {row['course_id']}).")
                return 2
            content = json.loads(row["task_content"])
            rules = json.loads(row["solution_rules"])
            if content.get("type") == "TBL_COM" and "Задание 20" in (content.get("stem") or ""):
                print(f"  {task_id}: уже с тремя вопросами, пропускаю")
                continue

            new_stem = build_stem(content["stem"], spec["slot"], spec["marker"],
                                  spec.get("names", NAMES_DEFAULT))
            for num in (19, 20, 21):
                if f"Задание {num}." not in new_stem:
                    print(f"СТОП: в новом стеме {task_id} нет блока «Задание {num}».")
                    return 2

            new_content = dict(content)
            new_content["stem"] = new_stem
            new_content["type"] = "TBL_COM"
            new_content["table"] = {"columns": 1}

            new_rules = dict(rules)
            sa = dict(new_rules.get("short_answer") or {})
            old_values = [a.get("value") for a in sa.get("accepted_answers") or []]
            sa["accepted_answers"] = [{"score": 1, "value": v} for v in spec["answers"]]
            new_rules["short_answer"] = sa

            print(f"\n  {task_id}: {spec['note']}")
            print(f"    было принято: {old_values}")
            print(f"    станет:       {spec['answers']}")
            if args.show:
                print("    новый стем:\n" + new_stem)
            updates.append((task_id, json.dumps(new_content, ensure_ascii=False),
                            json.dumps(new_rules, ensure_ascii=False)))

        if args.park_2384:
            lvl = await conn.fetchval(
                "SELECT requirement_level FROM tasks WHERE id = $1", PARKED_TASK
            )
            print(f"\n  {PARKED_TASK}: {lvl} → recommended (описание игры потеряно)")

        if not args.apply:
            print(f"\nDry-run: записи не было. К обновлению {len(updates)} заданий.")
            return 0

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.audit_actor', 'tsk-689', true)")
            for task_id, content_json, rules_json in updates:
                await conn.execute(
                    "UPDATE tasks SET task_content = $2::jsonb, solution_rules = $3::jsonb "
                    "WHERE id = $1",
                    task_id, content_json, rules_json,
                )
            if args.park_2384:
                await conn.execute(
                    "UPDATE tasks SET requirement_level = 'recommended' WHERE id = $1",
                    PARKED_TASK,
                )
        print(f"\nОбновлено заданий: {len(updates)}")

        print("\n=== ПОСЛЕ ===")
        check = await conn.fetch(
            "SELECT id, task_content->>'type' AS type, task_content->'table'->>'columns' AS cols, "
            "solution_rules->'short_answer'->'accepted_answers'->0->>'value' AS ans, "
            "(task_content->>'stem' LIKE '%Задание 20.%') AS has20, "
            "(task_content->>'stem' LIKE '%Задание 21.%') AS has21 "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
            list(plan),
        )
        ok = True
        for r in check:
            good = r["type"] == "TBL_COM" and r["cols"] == "1" and r["has20"] and r["has21"]
            ok = ok and good
            print(f"  {r['id']}: тип={r['type']} столбцов={r['cols']} "
                  f"вопрос20={r['has20']} вопрос21={r['has21']} ответ={r['ans']!r}")
        return 0 if ok else 3
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
