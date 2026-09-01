# -*- coding: utf-8 -*-
"""tsk-740, партия 2: номера заданий в текстах после переезда блоков ЕГЭ-2027.

ЗАЧЕМ
Партия 1 переставила блоки: тема «маска подсети» стала заданием 10, тема «анализ
хода исполнения алгоритма» — заданием 13. Заголовки курсов исправлены, а внутри
номер остался старым: «Термины и теория задания 13. IP адрес» лежит в блоке
«Задание 10», «Разбор заданий 23» — в блоке «Задание 13». Ученик видит разнобой.

ЧТО ПРАВИТСЯ
- Названия материалов: 13 штук в курсе 139 («13» -> «10»), 6 в курсе 150
  («23» -> «13», включая «Урок 23_1» и «Урок 23_2»).
- Тела материалов: 1 место в курсе 139 (материал 355).
- Условия заданий: 5 служебных шапок в курсах 139/1391 («Задание 13 (Яндекс).
  Уровень средний.», «Задание 13 Сборник Крылова…»). В курсах 150/1399 таких нет.
- Описания подкурсов «Сложные» (1391, 1399) — там процитирован старый заголовок.

ПОЧЕМУ НЕ ГЛОБАЛЬНАЯ ЗАМЕНА «13» -> «10»
Числа 13 и 23 живут в условиях как данные: IP-адреса, префиксы `/13`, годы,
номера задач на сайте-источнике («Задание 27_35485»). Меняется только связка
«слово-задание + номер»: шаблон `задан\\w* [№ ]NN`, где сразу за номером не идёт
цифра, точка-с-цифрой или подчёркивание. Каждое место сверено глазами до правки
(выборка в отчёте партии), список — закрытый, не «всё, что нашла регулярка».

ГРАНИЦА
Правится только служебная шапка нашего происхождения, а не текст источника.
Формулировки самих задач не трогаются: сдвиг номера в КИМ не меняет условие.

ВАЖНО ПРО ИМПОРТ
`task_content` целиком перезаписывается пакетным импортом из ContentBackbone
(урок tsk-377 и § 11.6 плейбука), поэтому правка стемов переживёт только до
следующей синхронизации. Синхронизация сейчас запрещена (tsk-734); когда её
разморозят, эти пять шапок надо будет поправить в источнике.

Запуск: вхолостую по умолчанию;
  DBCHECK_OK=1 python scripts/tsk740_ege2027_texts.py
  DBCHECK_OK=1 python scripts/tsk740_ege2027_texts.py --apply
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

# (курсы, старый номер, новый номер)
PEREEZDY: list[tuple[list[int], str, str]] = [
    ([139, 1391], "13", "10"),
    ([150, 1399], "23", "13"),
]

# Описания подкурсов «Сложные» цитируют старый заголовок родителя.
OPISANIYA: dict[int, str] = {
    1391: (
        "Задания повышенной сложности из раздела «Задание 10 ЕГЭ по информатике. "
        "Организация компьютерных сетей и адресация». Блок необязательный."
    ),
    1399: (
        "Задания повышенной сложности из раздела «Задание 13 ЕГЭ по информатике. "
        "Анализ хода исполнения алгоритма». Блок необязательный."
    ),
    1388: (
        "Задания повышенной сложности из раздела «Поиск информации в документах». "
        "Тема снята с ЕГЭ с 2027 года, блок необязательный."
    ),
}


def _shablon(nomer: str) -> re.Pattern[str]:
    """«задание/заданий/заданию + [№] NN», где NN не часть большего числа или id."""
    return re.compile(
        rf"(задан\w*)(\s*)(№\s*)?{nomer}(?![\d_])(?!\.\d)",
        re.IGNORECASE,
    )


def _zamenit(tekst: str, staryj: str, novyj: str) -> tuple[str, int]:
    """Возвращает (новый текст, сколько мест заменено)."""
    shablon = _shablon(staryj)
    skolko = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal skolko
        skolko += 1
        return f"{m.group(1)}{m.group(2)}{m.group(3) or ''}{novyj}"

    novyj_tekst = shablon.sub(_sub, tekst)
    # «Урок 23_1» / «Урок 23_2» — своя форма, подчёркивание закрыто основным шаблоном.
    urok = re.compile(rf"(Урок\s*){staryj}(_\d)", re.IGNORECASE)
    novyj_tekst, n2 = urok.subn(rf"\g<1>{novyj}\g<2>", novyj_tekst)
    return novyj_tekst, skolko + n2


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


async def _sobrat(conn: asyncpg.Connection) -> dict:
    """План правок: что, где, было -> станет. Ничего не пишет."""
    plan: dict = {"materialy_zagolovki": [], "materialy_tela": [], "stemy": []}
    for kursy, staryj, novyj in PEREEZDY:
        materialy = await conn.fetch(
            "SELECT id, course_id, title, content::text AS content "
            "FROM materials WHERE course_id = ANY($1::int[]) ORDER BY id",
            kursy,
        )
        for m in materialy:
            novyj_title, n = _zamenit(m["title"], staryj, novyj)
            if n:
                plan["materialy_zagolovki"].append(
                    {"id": m["id"], "course_id": m["course_id"], "bylo": m["title"],
                     "stanet": novyj_title, "mest": n}
                )
            novoe_telo, n2 = _zamenit(m["content"] or "", staryj, novyj)
            if n2:
                plan["materialy_tela"].append(
                    {"id": m["id"], "course_id": m["course_id"], "mest": n2,
                     "novoe": novoe_telo}
                )
        zadaniya = await conn.fetch(
            "SELECT id, course_id, task_content->>'stem' AS stem "
            "FROM tasks WHERE course_id = ANY($1::int[]) ORDER BY id",
            kursy,
        )
        for z in zadaniya:
            if not z["stem"]:
                continue
            novyj_stem, n = _zamenit(z["stem"], staryj, novyj)
            if n:
                plan["stemy"].append(
                    {"id": z["id"], "course_id": z["course_id"], "mest": n,
                     "bylo_frag": z["stem"][:110], "stanet_frag": novyj_stem[:110],
                     "novoe": novyj_stem}
                )
    return plan


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        plan = await _sobrat(conn)

        print("=== ПЛАН ПРАВОК ===")
        print(f"Названия материалов: {len(plan['materialy_zagolovki'])}")
        for p in plan["materialy_zagolovki"]:
            print(f"  {p['course_id']}/{p['id']}: «{p['bylo']}»\n"
                  f"      -> «{p['stanet']}»")
        print(f"\nТела материалов: {len(plan['materialy_tela'])}")
        for p in plan["materialy_tela"]:
            print(f"  {p['course_id']}/{p['id']}: мест {p['mest']}")
        print(f"\nУсловия заданий: {len(plan['stemy'])}")
        for p in plan["stemy"]:
            print(f"  {p['course_id']}/{p['id']}: «{p['bylo_frag'].strip()}»\n"
                  f"      -> «{p['stanet_frag'].strip()}»")
        print(f"\nОписания подкурсов: {len(OPISANIYA)}")

        snimok = project_root / "reviews" / "2026-09-01-tsk740-texts-before.json"
        do = await conn.fetch(
            "SELECT 'material' AS vid, id, title, content::text AS telo FROM materials "
            "WHERE course_id = ANY($1::int[])",
            [139, 150],
        )
        do_zadaniy = await conn.fetch(
            "SELECT 'task' AS vid, id, task_content->>'stem' AS stem FROM tasks "
            "WHERE id = ANY($1::int[])",
            [p["id"] for p in plan["stemy"]] or [0],
        )
        do_kursov = await conn.fetch(
            "SELECT id, description FROM courses WHERE id = ANY($1::int[])",
            list(OPISANIYA),
        )
        snimok.write_text(
            json.dumps(
                {"materialy": [dict(r) for r in do],
                 "zadaniya": [dict(r) for r in do_zadaniy],
                 "kursy": [dict(r) for r in do_kursov]},
                ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\nСнимок «до» записан: {snimok}")

        if not apply:
            print("\nВхолостую. Записи не было.")
            return

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")

            for p in plan["materialy_zagolovki"]:
                await conn.execute(
                    "UPDATE materials SET title = $2 WHERE id = $1", p["id"], p["stanet"]
                )
            for p in plan["materialy_tela"]:
                await conn.execute(
                    "UPDATE materials SET content = $2::jsonb WHERE id = $1",
                    p["id"], p["novoe"],
                )
            for p in plan["stemy"]:
                await conn.execute(
                    "UPDATE tasks SET task_content = jsonb_set(task_content, '{stem}', to_jsonb($2::text)) "
                    "WHERE id = $1",
                    p["id"], p["novoe"],
                )
            for cid, opisanie in OPISANIYA.items():
                await conn.execute("UPDATE courses SET description = $2 WHERE id = $1", cid, opisanie)

            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'false', true)")

            # Верификация до коммита: старых номеров в связке со словом «задание»
            # не осталось, а число материалов и заданий не изменилось.
            for kursy, staryj, _novyj in PEREEZDY:
                ostalos_m = await conn.fetchval(
                    "SELECT count(*) FROM materials WHERE course_id = ANY($1::int[]) "
                    rf"AND (title ~* 'задан\w*\s*(№\s*)?{staryj}\M' "
                    rf"OR content::text ~* 'задан\w*\s*(№\s*)?{staryj}\M')",
                    kursy,
                )
                ostalos_t = await conn.fetchval(
                    "SELECT count(*) FROM tasks WHERE course_id = ANY($1::int[]) "
                    rf"AND (task_content->>'stem') ~* 'задан\w*\s*(№\s*)?{staryj}\M'",
                    kursy,
                )
                print(f"  курсы {kursy}: осталось со старым номером {staryj} — "
                      f"материалов {ostalos_m}, заданий {ostalos_t}")
                if ostalos_m or ostalos_t:
                    raise RuntimeError(f"В курсах {kursy} остался старый номер {staryj}.")

            bitye = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND (task_content->>'stem' IS NULL OR length(task_content->>'stem') < 20)",
                [p["id"] for p in plan["stemy"]] or [0],
            )
            if bitye:
                raise RuntimeError(f"{bitye} условий стали пустыми — откат.")
            print("  условий, ставших пустыми: 0")

        print("\nГотово. Правки записаны.")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tsk-740 партия 2: номера заданий в текстах")
    parser.add_argument("--apply", action="store_true", help="записать изменения")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
