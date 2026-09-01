# -*- coding: utf-8 -*-
"""tsk-740, партия 3: задание 27 — новая форма записи ответа (ЕГЭ-2027).

ЧТО ИЗМЕНИЛОСЬ В КИМ
§ 11 спецификации ЕГЭ-2027: «Изменена структура записи ответа задания 27.
В 2027 г. участнику экзамена нужно будет записать в ответе строку из двух чисел
вместо двух строк по два числа».

КАК ЭТО ВЫГЛЯДИТ В НАШИХ ЗАДАНИЯХ
Старый формат существует в двух видах, и оба дают четыре числа:

1. «два файла»: «в первой строке … для файла А, во второй строке — аналогичные
   данные для файла Б» (2207, 2208, 2386, 3338, 3356);
2. «две пары величин по одному файлу»: «в первой строке — Px, Py; во второй
   строке — Q1, Q2» (3004, 3015, 9497, 9512, 9523, 9533).

В обоих случаях новый ответ — ВТОРАЯ пара. Демоверсия-2027 подтверждает: там
осталась ровно вторая пара («сначала целую часть произведения Q1 × 10 000, затем
целую часть произведения Q2 × 10 000»), а первая (средние арифметические центров
кластеров) убрана.

ЧЕМ ДОКАЗАНО, ЧТО БЕРЁМ ИМЕННО ВТОРУЮ ПАРУ (два независимых признака, § 9 плейбука)
- Дословный текст условия каждого задания называет порядок пар явно.
- Структура эталона совпадает: у 3015 и 3356 четыре числа записаны ДВУМЯ
  строками через `\\n`, и вторая строка — ровно та пара, которую называет текст.

НЕ ВХОДИТ В ПАРТИЮ
- 3333 и 3355: у них эталон из ДВУХ чисел, но это «по одному числу на каждый из
  двух файлов» — ни старый формат 27, ни новый. Класс отдельный, решение за
  оператором. Скрипт их не трогает и называет в отчёте.
- Баллы: `max_score` остаётся 1. В КИМ-2027 задание 27 стоит 2 балла с частичным
  зачётом, но менять баллы задним числом оператор запретил — это отдельное решение.
- Файлы-приложения: у двухфайловых заданий оба файла остаются на месте. Файл А
  превращается в тренировочный, ответ спрашивается по файлу Б.

Запуск: вхолостую по умолчанию;
  DBCHECK_OK=1 python scripts/tsk740_ege2027_task27.py
  DBCHECK_OK=1 python scripts/tsk740_ege2027_task27.py --apply
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

project_root = Path(__file__).resolve().parents[1]

FAJL_B = (
    "В ответе запишите два числа для файла Б: сначала целую часть произведения "
    "Px × 10 000, затем целую часть произведения Py × 10 000."
)
PARA_Q = (
    "В ответе запишите два числа: сначала целую часть произведения Q1 × 10 000, "
    "затем целую часть произведения Q2 × 10 000."
)

# id -> (новая формулировка ответа, новый эталон).
# Эталон — вторая пара из старых четырёх чисел, сверена с текстом условия поштучно.
KARTA: dict[int, tuple[str, str]] = {
    2207: (FAJL_B, "122627 29105"),
    2208: (FAJL_B, "144062 61170"),
    2386: (FAJL_B, "37522 51277"),
    3338: (FAJL_B, "144062 61170"),
    3356: (FAJL_B, "122627 29105"),
    3004: (PARA_Q, "75241 98298"),
    3015: (PARA_Q, "80045 243309"),
    9497: (PARA_Q, "8580 9126"),
    9512: (PARA_Q, "646 614"),
    9533: (PARA_Q, "149088 25324"),
    9523: (
        "В ответе запишите два числа: сначала число Q1, затем число Q2.",
        "88 399",
    ),
}

NE_TRONUTY = {
    3333: "эталон из двух чисел — по одному на файл; ни старый формат 27, ни новый",
    3355: "эталон из двух чисел — по одному на файл; ни старый формат 27, ни новый",
}

NACHALO = "В ответе запишите четыре числа"
# Мягкий перенос и нулевая ширина прячут фразу от прямого поиска (§ 10 плейбука).
NEVIDIMKI = "\u00ad\u200b"


def _gibkij_shablon(fraza: str) -> re.Pattern[str]:
    """Шаблон фразы, терпимый к мягким переносам, тегам и разным пробелам."""
    kuski = []
    for simvol in fraza:
        if simvol == " ":
            kuski.append(r"[\s\u00ad\u200b]+")
        else:
            kuski.append(re.escape(simvol) + r"[\u00ad\u200b]*")
    return re.compile("".join(kuski), re.IGNORECASE)


def _granicy_frazy(stem: str) -> tuple[int, int] | None:
    """Начало и конец предложения «В ответе запишите четыре числа: …»."""
    m = _gibkij_shablon(NACHALO).search(stem)
    if not m:
        return None
    nachalo = m.start()
    # Конец — первая точка, за которой пробел/тег и заглавная буква либо конец текста.
    konec = re.compile(r"\.(?=(?:[\s\u00ad\u200b]|<[^>]*>)*(?:[А-ЯA-Z]|$))")
    k = konec.search(stem, m.end())
    if not k:
        return None
    return nachalo, k.end()


def _dsn() -> str:
    """Прод-DSN learn: из окружения либо из .mcp.json (паттерн tsk-347/362/366)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                    dsn = arg
                    break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        stroki = await conn.fetch(
            "SELECT id, course_id, task_content->>'type' AS ttype, "
            "task_content->>'stem' AS stem, solution_rules AS pravila "
            "FROM tasks WHERE course_id IN (154, 1403) AND is_active ORDER BY id"
        )
        snimok = project_root / "reviews" / "2026-09-01-tsk740-task27-before.json"
        snimok.write_text(
            json.dumps([dict(r) for r in stroki], ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Снимок «до» записан: {snimok}\n")

        plan: list[dict] = []
        print("=== ПЛАН ===")
        for r in stroki:
            if r["id"] in NE_TRONUTY:
                print(f"  {r['id']}: НЕ ТРОГАЕМ — {NE_TRONUTY[r['id']]}")
                continue
            if r["id"] not in KARTA:
                print(f"  {r['id']}: НЕ В КАРТЕ — задание появилось после разбора, "
                      "разобрать отдельно")
                continue
            novaya_fraza, novyj_etalon = KARTA[r["id"]]
            granicy = _granicy_frazy(r["stem"] or "")
            if granicy is None:
                print(f"  {r['id']}: фраза «{NACHALO}…» не найдена — пропуск, разобрать вручную")
                continue
            a, b = granicy
            staraya = re.sub(r"<[^>]*>", " ", r["stem"][a:b])
            staraya = re.sub(r"[\s\u00ad\u200b]+", " ", staraya).strip()

            pravila = json.loads(r["pravila"]) if isinstance(r["pravila"], str) else r["pravila"]
            staryj_etalon = (pravila or {}).get("short_answer", {}).get("accepted_answers", [])
            staryj = staryj_etalon[0]["value"] if staryj_etalon else None
            # Контроль: новый эталон обязан быть хвостом старого — иначе взяли не ту пару.
            chisla_staryh = (staryj or "").split()
            if chisla_staryh[-2:] != novyj_etalon.split():
                raise RuntimeError(
                    f"{r['id']}: новый эталон «{novyj_etalon}» не совпадает с последней парой "
                    f"старого «{staryj}». Карта разошлась с базой — разбираться вручную."
                )

            plan.append({"id": r["id"], "a": a, "b": b, "fraza": novaya_fraza,
                         "etalon": novyj_etalon, "staryj_etalon": staryj})
            print(f"  {r['id']} ({r['ttype']}):")
            print(f"      было : {staraya}")
            print(f"      стало: {novaya_fraza}")
            print(f"      эталон: «{staryj}» -> «{novyj_etalon}»")

        print(f"\nК правке: {len(plan)} заданий из {len(stroki)} активных")

        if not apply:
            print("\nВхолостую. Записи не было.")
            return

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            for p in plan:
                stem = next(r["stem"] for r in stroki if r["id"] == p["id"])
                novyj_stem = stem[: p["a"]] + p["fraza"] + stem[p["b"]:]
                await conn.execute(
                    "UPDATE tasks SET "
                    "task_content = jsonb_set(task_content, '{stem}', to_jsonb($2::text)), "
                    "solution_rules = jsonb_set(solution_rules, "
                    "  '{short_answer,accepted_answers}', "
                    "  jsonb_build_array(jsonb_build_object('score', 1, 'value', $3::text))) "
                    "WHERE id = $1",
                    p["id"], novyj_stem, p["etalon"],
                )
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'false', true)")

            # Верификация до коммита.
            proverka = await conn.fetch(
                "SELECT id, task_content->>'stem' AS stem, "
                "solution_rules#>>'{short_answer,accepted_answers,0,value}' AS etalon "
                "FROM tasks WHERE id = ANY($1::int[])",
                [p["id"] for p in plan],
            )
            po_id = {p["id"]: p for p in plan}
            for r in proverka:
                ozhidaem = po_id[r["id"]]
                if r["etalon"] != ozhidaem["etalon"]:
                    raise RuntimeError(f"{r['id']}: эталон не записался ({r['etalon']}).")
                if len((r["etalon"] or "").split()) != 2:
                    raise RuntimeError(f"{r['id']}: эталон не из двух чисел — «{r['etalon']}».")
                if "четыре числа" in (r["stem"] or ""):
                    raise RuntimeError(f"{r['id']}: в условии осталось «четыре числа».")
                if len(r["stem"] or "") < 300:
                    raise RuntimeError(f"{r['id']}: условие подозрительно короткое — откат.")
            print(f"\nПроверено до коммита: {len(proverka)} заданий, эталоны из двух чисел, "
                  "«четыре числа» в условиях не осталось.")

        print("Готово. Правки записаны.")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tsk-740 партия 3: форма ответа задания 27")
    parser.add_argument("--apply", action="store_true", help="записать изменения")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
