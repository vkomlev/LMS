# -*- coding: utf-8 -*-
"""tsk-731: вернуть трём заданиям потерянное содержание условия.

ЧТО ПОТЕРЯНО И ОТКУДА ВОЗВРАЩАЕМ

1. id=3025 «Соответствие столбцов логической функции» (курс 148, `tg:ege:956`).
   Фрагмент таблицы истинности приехал строкой чисел «1 0 0 0 / 1 0 0 0 / 1 0 0»:
   разметки таблицы нет, столбца F нет, а в третьей строке нет ещё одного
   значения. Задание нерешаемо. Источник — ТГ-пост @cyberguru_ege №956, но его
   тело УЖЕ плоское (класс A плейбука: «ниже по потоку не восстановить»). Зато
   пост называет первоисточник: Яндекс Учебник, задача
   `4b22ad8f-1eea-4f4f-9f2e-00d45f1a56d9`. Анонимный `get_task_by_id` отдаёт
   условие целиком, с настоящей `<table>` из пяти столбцов. В ней ЕСТЬ пустые
   ячейки — это часть задачи, а не пропуск: именно они и склеились в плоскую
   строку чисел. Ответ источника `zxwy` совпадает с эталоном в базе.

2. id=3470 (курс 147, `wp_nav:19:1d75c02b`) — оборваны условия заданий 20 и 21;
   id=3472 (курс 147, `wp_nav:19:8ab610f5`) — оборвано условие задания 21.
   Источник тот же — Яндекс, подборки `5a55834b-...` и `a97d888a-...`, задания
   №20/№21. В обоих случаях потерян маркированный список из двух пунктов.

ГДЕ РВАЛОСЬ (корень найден, не гипотеза)
`scripts/tsk558_yandex_wp_nav_19_21.py:147` — `re.findall(r"<p>.*?</p>", ...)`
БЕЗ флага `re.S`. Точка не совпадает с переводом строки, поэтому абзац, внутри
которого есть перевод строки, в выборку не попадает и молча пропадает. Перевод
строки внутри абзаца бывает ровно у одного вида содержимого — маркированного
списка (Яндекс отдаёт пункты как «* ...» через одиночный CRLF). Отсюда обе формы
дефекта: у 3470 хвост «Найденные значения...» уцелел, а список между ним и
двоеточием исчез; у 3472 список был последним — текст кончился на двоеточии.
Радиус поражения ровно два задания: этот скрипт трогал только 3470 и 3472,
других мест с этой идиомой в `scripts/` нет.

ПОЧЕМУ НЕ СОЧИНЯЕМ
Формулировки взяты дословно из ответа API (сохранён в артефакте ревью), а не по
шаблону соседних заданий. Проверка: решатель `tsk689_games_solver.py` считает
ответ ПО ВОССТАНОВЛЕННОЙ формулировке и сходится с эталоном, который уже лежит в
базе (3470 -> 22/18,21/17; 3472 -> 17/9/8). У 3472 задание 21 сформулировано НЕ
типовым для блока образом (про Петю, признак f=2, а не про Ваню, f=-2) — типовой
шаблон дал бы другой ответ, и совпадение с эталоном подтверждает, что взят
именно текст источника.

ЧТО НЕ ТРОГАЕМ
Эталоны, тип задания, порядок, уровень требования — ни одно поле, кроме
`task_content.stem`. Задания с целым условием не трогаем вовсе.

Запуск: dry-run по умолчанию;
  python scripts/tsk731_restore_lost_conditions.py
  python scripts/tsk731_restore_lost_conditions.py --show
  DBCHECK_OK=1 python scripts/tsk731_restore_lost_conditions.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

# --- Пункты списков, дословно из Яндекс-API (см. артефакт ревью) ------------

UL_PETYA_TWO_MOVES = (
    "<ul>"
    "<li>Петя не может выиграть за один ход;</li>"
    "<li>Петя может выиграть своим вторым ходом независимо от того, "
    "как будет ходить Ваня.</li>"
    "</ul>"
)
UL_VANYA_FIRST_OR_SECOND = (
    "<ul>"
    "<li>у Вани есть выигрышная стратегия, позволяющая ему выиграть первым "
    "или вторым ходом при любой игре Пети;</li>"
    "<li>у Вани нет стратегии, которая позволит ему гарантированно выиграть "
    "первым ходом.</li>"
    "</ul>"
)

# Вставки: (id, якорь — точная подстрока в текущем стеме, что дописать ПОСЛЕ неё).
# Якорь обязан встречаться ровно один раз, иначе скрипт останавливается.
INSERTS: list[tuple[int, str, str]] = [
    (
        3470,
        "найдите два таких <strong>минимальных</strong> значения $S$, при которых "
        "у Пети есть выигрышная стратегия, причём одновременно выполняются два условия:</p>",
        UL_PETYA_TWO_MOVES,
    ),
    (
        3470,
        "найдите <strong>минимальное</strong> значение $S$, при котором одновременно "
        "выполняются два условия:</p>",
        UL_VANYA_FIRST_OR_SECOND,
    ),
    (
        3472,
        "найдите наименьшее значение *S*, при котором у Пети есть выигрышная стратегия, "
        "причём одновременно выполняются два условия:</p>",
        UL_PETYA_TWO_MOVES,
    ),
]

# Полная замена стема 3025 — транскрипция условия из Яндекс-API. Формула и буквы
# оставлены обычным текстом (как в текущем стеме), чтобы не тащить в задание
# ещё один слой разметки; из источника добавлены: перечисление переменных,
# сам фрагмент таблицей и разбирающий формат ответа пример.
STEM_3025 = (
    "<p>Миша составлял таблицу истинности логической функции F = w∨(y∧¬x)∨¬z</p>"
    "<p>Но успел заполнить только фрагмент из трёх различных строк и даже не указал, "
    "какому столбцу таблицы соответствуют переменные w, x, y, z.</p>"
    "<table>"
    "<thead><tr><th></th><th></th><th></th><th></th><th>F</th></tr></thead>"
    "<tbody>"
    "<tr><td>1</td><td></td><td>0</td><td>0</td><td>0</td></tr>"
    "<tr><td>1</td><td></td><td>0</td><td>0</td><td>0</td></tr>"
    "<tr><td></td><td>1</td><td>0</td><td></td><td>0</td></tr>"
    "</tbody>"
    "</table>"
    "<p>Определите, какому столбцу таблицы они соответствуют.</p>"
    "<p>В ответе напишите буквы w, x, y, z в том порядке, в котором идут "
    "соответствующие им столбцы (сначала буква первого столбца, затем второго и т. д.). "
    "Буквы в ответе пишите подряд, никаких разделителей между ними ставить не нужно.</p>"
    "<p><em>Пример.</em> Функция F задана выражением ¬x∨y, которое зависит от двух "
    "переменных, а фрагмент таблицы выглядит так:</p>"
    "<table>"
    "<thead><tr><th></th><th></th><th>F</th></tr></thead>"
    "<tbody><tr><td>0</td><td>1</td><td>0</td></tr></tbody>"
    "</table>"
    "<p>В этом случае первому столбцу соответствует переменная y, а второму — x. "
    "В ответе следует написать: yx.</p>"
)

REPLACEMENTS: dict[int, str] = {3025: STEM_3025}

EXPECTED_COURSE = {3025: 148, 3470: 147, 3472: 147}


def _prod_dsn_from_mcp() -> str:
    """Строка подключения к прод-`learn` из `.mcp.json`. Единственный источник:
    ни хост, ни пароль в коде не хранятся (плейбук импорта, §10 «Гоча окружения»)."""
    cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
    servers = cfg.get("mcpServers", cfg)
    for arg in servers["learn_prod_db"]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://"):
            return arg
    raise RuntimeError("В .mcp.json нет строки подключения learn_prod_db.")


def _dsn() -> str:
    """Прод-DSN learn. Из окружения — только если это ТОТ ЖЕ хост и база, что в
    `.mcp.json`: иначе скрипт молча отработал бы по dev-базе (локальный `.env`
    указывает именно туда). Секрет никуда не печатается."""
    prod = _prod_dsn_from_mcp()
    prod_host = urlparse(prod).hostname or ""
    prod_db = urlparse(prod).path

    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    candidate = env.replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(candidate)
    if parsed.hostname == prod_host and parsed.path == prod_db:
        return candidate
    return prod


def build_new_stem(task_id: int, stem: str) -> str:
    """Собирает новый стем: либо полная замена, либо вставки по якорям."""
    if task_id in REPLACEMENTS:
        return REPLACEMENTS[task_id]
    out = stem
    for tid, anchor, addition in INSERTS:
        if tid != task_id:
            continue
        found = out.count(anchor)
        if found != 1:
            raise RuntimeError(
                f"id={task_id}: якорь встречается {found} раз (нужен ровно 1): {anchor[:70]!r}"
            )
        # Идемпотентность: якорь («…два условия:</p>») остаётся на месте и после
        # вставки, поэтому повторный прогон дописал бы список ВТОРОЙ раз. Пропускаем,
        # если он уже стоит сразу за якорем.
        if anchor + addition in out:
            continue
        out = out.replace(anchor, anchor + addition)
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description="tsk-731: вернуть потерянные условия")
    ap.add_argument("--apply", action="store_true", help="записать в прод-БД")
    ap.add_argument("--show", action="store_true", help="показать новый стем целиком")
    args = ap.parse_args()

    ids = sorted(EXPECTED_COURSE)
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            "SELECT id, course_id, is_active, task_content, solution_rules "
            "FROM tasks WHERE id = ANY($1::int[])",
            ids,
        )
        by_id = {int(r["id"]): r for r in rows}
        missing = sorted(set(ids) - set(by_id))
        if missing:
            print(f"СТОП: не найдены задания {missing}.")
            return 2

        updates: list[tuple[int, str]] = []
        for task_id in ids:
            row = by_id[task_id]
            if row["course_id"] != EXPECTED_COURSE[task_id]:
                print(f"СТОП: {task_id} не в курсе {EXPECTED_COURSE[task_id]} "
                      f"(сейчас {row['course_id']}).")
                return 2
            if not row["is_active"]:
                print(f"СТОП: {task_id} неактивно — правка не имеет смысла.")
                return 2

            content = json.loads(row["task_content"])
            old_stem = content.get("stem") or ""
            new_stem = build_new_stem(task_id, old_stem)
            if new_stem == old_stem:
                print(f"  {task_id}: стем уже целый, пропускаю")
                continue

            # Ничего не должно ПРОПАСТЬ: убрав из нового стема ровно то, что мы
            # дописали, обязаны получить старый байт в байт. Сравнивать вхождением
            # старого стема в новый нельзя — вставка идёт в середину и рвёт его
            # на части. При полной замене (3025) проверка неприменима.
            if task_id not in REPLACEMENTS:
                restored = new_stem
                for tid, _anchor, addition in INSERTS:
                    if tid == task_id:
                        restored = restored.replace(addition, "", 1)
                if restored != old_stem:
                    print(f"СТОП: {task_id} — вставка изменила старый текст.")
                    return 2
            # Условие не должно больше обрываться на двоеточии.
            if new_stem.rstrip().removesuffix("</p>").rstrip().endswith(":"):
                print(f"СТОП: {task_id} — новый стем всё ещё кончается двоеточием.")
                return 2

            new_content = dict(content)
            new_content["stem"] = new_stem
            print(f"\n  {task_id}: стем {len(old_stem)} → {len(new_stem)} символов")
            if args.show:
                print("    новый стем:\n" + new_stem)
            updates.append((task_id, json.dumps(new_content, ensure_ascii=False)))

        if not args.apply:
            print(f"\nDry-run: записи не было. К обновлению {len(updates)} заданий.")
            return 0

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.audit_actor', 'tsk-731', true)")
            for task_id, content_json in updates:
                await conn.execute(
                    "UPDATE tasks SET task_content = $2::jsonb WHERE id = $1",
                    task_id, content_json,
                )
        print(f"\nОбновлено заданий: {len(updates)}")

        print("\n=== ПОСЛЕ ===")
        check = await conn.fetch(
            "SELECT id, length(task_content->>'stem') AS len, "
            "(task_content->>'stem' LIKE '%<li>%') AS est_spisok, "
            "(task_content->>'stem' LIKE '%<table>%') AS est_tablitsa, "
            "(btrim(task_content->>'stem') LIKE '%условия:</p>') AS obryv, "
            "solution_rules->'short_answer'->'accepted_answers'->0->>'value' AS etalon "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
            ids,
        )
        ok = True
        for r in check:
            good = (not r["obryv"]) and (
                r["est_spisok"] if r["id"] in (3470, 3472) else r["est_tablitsa"]
            )
            ok = ok and good
            print(f"  {r['id']}: длина={r['len']} список={r['est_spisok']} "
                  f"таблица={r['est_tablitsa']} обрыв={r['obryv']} эталон={r['etalon']!r}")
        return 0 if ok else 3
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
