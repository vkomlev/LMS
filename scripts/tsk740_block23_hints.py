# -*- coding: utf-8 -*-
"""tsk-740, партия 8: подсказки к заданиям блока 23.

ЗАЧЕМ
Экспертное ревью 01.09 нашло P1 по критерию оценивания: подсказок нет ни у одного
из 27 заданий блока, обратная связь сводится к «верно/неверно» по числу. Ученик,
у которого ответ не сошёлся, не получает ни признака, ни хода рассуждения — а
ошибиться в задании 23 можно в пяти разных местах.

ПРАВИЛА, ПО КОТОРЫМ НАПИСАНЫ ПОДСКАЗКИ (assignment-rules § 3.4a п.2, К5 рубрики)
- Подсказка даёт ХОД рассуждения, а не ответ: числа-эталона в ней нет ни в каком
  виде. Проверяется программно перед записью.
- Подсказка не generic-заглушка: у каждой свой акцент на конкретную ошибку, и в
  текст подставлены номера вершин ЭТОГО задания.
- Одинаковых строк в пределах одного узла нет — проверяется программно.

ЧЕСТНОЕ ОГРАНИЧЕНИЕ
Задания блока однотипны по построению: меняются граф и вершины, вопрос один из
двух. Поэтому подсказки собраны из шести «ходов» на каждый вид вопроса и
параметризованы вершинами задания. Это лучше, чем один текст на всех, но полной
уникальности «своя мысль на каждую задачу» здесь быть не может — она была бы
выдумкой. Разнообразие форм практики закрывается отдельно, новыми заданиями.

Запуск: вхолостую по умолчанию;
  DBCHECK_OK=1 python scripts/tsk740_block23_hints.py
  DBCHECK_OK=1 python scripts/tsk740_block23_hints.py --apply
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

UIDY_BLOKA = ("lms:tsk740:ege2027:23", "lms:tsk740:ege2027:23:short",
              "lms:tsk740:ege2027:23:count", "lms:tsk347:hard:1490")

# Ходы для «кратчайшего пути». {ot} и {do} — вершины конкретного задания.
HODY_PUT = [
    "Начните с проверки чтения: напечатайте, сколько рёбер попало в граф, и сверьте "
    "с числом строк в файле. Если рёбер меньше — скорее всего строка режется не тем "
    "способом: между числами бывают и пробелы, и табуляции, поэтому нужен split() "
    "без аргументов. До поиска пути из {ot} в {do} дело тут даже не доходит.",

    "Следите за направлением: строка «L M W» означает ребро ИЗ L В M, обратного "
    "ребра нет. Если добавить в граф ещё и обратное, путь из {ot} в {do} может стать "
    "короче настоящего — а программа при этом не выдаст никакой ошибки.",

    "Первый найденный путь до {do} почти никогда не кратчайший. Убедитесь, что "
    "расстояние до вершины обновляется, если нашёлся более дешёвый путь, а не "
    "фиксируется при первом попадании.",

    "Целую часть берите один раз, у готового ответа. Если отбрасывать дробную часть "
    "у каждого ребра по дороге от {ot} к {do}, сумма разойдётся с верной на несколько "
    "единиц, и ошибку будет не видно.",

    "Из вершины {do} может не выходить ни одного ребра — тогда её просто нет среди "
    "ключей словаря смежности. Это нормально: нам нужен путь В неё, а не ИЗ неё. "
    "Ответ ищите в словаре расстояний, а не в словаре графа.",

    "Если число получилось, но вы в нём не уверены, посчитайте то же самое вторым "
    "способом — проходами по списку рёбер, как в теме про Беллмана-Форда. Два разных "
    "алгоритма на одном файле обязаны дать одно число; разошлись — ошибка в чтении "
    "файла или в направлении рёбер.",

    "Проверьте, что вес читается как вещественное число. Если написать int вместо "
    "float, программа либо остановится на первой же строке с дробным весом, либо "
    "молча отбросит дробные части — и путь из {ot} в {do} окажется короче настоящего.",

    "Последняя строка файла бывает пустой. Без проверки длины разобранной строки "
    "программа остановится на ней с ошибкой распаковки, хотя весь граф уже прочитан "
    "верно. Пропускайте строки, в которых меньше трёх чисел.",

    "Ответ ищите в словаре расстояний, а не в графе. Обращение к графу по вершине "
    "{do} даст список её исходящих рёбер, а нам нужно накопленное расстояние — это "
    "разные структуры, и путать их легко.",

    "Сверьте номера вершин с условием буквально: старт {ot}, финиш {do}. Перепутанные "
    "местами старт и финиш дают вполне правдоподобное число — граф ориентированный, "
    "путь в обратную сторону обычно тоже существует, но он другой.",

    "Если расстояние до {do} осталось бесконечным, значит из {ot} туда не добраться "
    "по направленным рёбрам. По условию задания путь гарантированно есть, поэтому "
    "ищите ошибку в чтении: скорее всего рёбра добавлены в обратную сторону.",
]

# Ходы для «количества путей».
HODY_KOL = [
    "Не пытайтесь перебрать сами пути от {ot} до {do} — их бывают миллионы, программа "
    "не дождётся конца. Считайте не пути, а их количество: число путей из вершины "
    "складывается из чисел путей у тех вершин, куда из неё ведут рёбра.",

    "Вес рёбер в этом вопросе не участвует вообще. Если он попал в расчёт, ответ "
    "будет похож на правдоподобный, но неверный — проверьте, что из тройки «L M W» "
    "используются только первые два числа.",

    "Обязательно запоминайте уже посчитанные вершины в словаре. Без этого каждая "
    "вершина будет пересчитываться заново на каждом пути через неё — это тот же "
    "перебор, только замаскированный, и на пути от {ot} до {do} он не закончится.",

    "Точка отсчёта — финиш: у вершины {do} количество путей равно единице (путь "
    "«стоять на месте»). Если начать с нуля у всех вершин, включая финиш, ответ "
    "получится нулевым при любом графе.",

    "Проверьте себя вторым способом из темы про топологическую сортировку: посчитайте "
    "«вперёд» от {ot}, передавая число путей соседям. Счёт назад от финиша и счёт "
    "вперёд от старта обязаны дать одно число.",

    "Если программа падает с RecursionError, дело не в логике: цепочка вложенных "
    "вызовов упёрлась в ограничение Python. Либо поднимите предел в начале программы, "
    "либо посчитайте без рекурсии — повторными проходами по списку рёбер.",

    "Направление важно и здесь: ребро «L M W» ведёт ИЗ L В M. Если добавить в граф "
    "обратные рёбра, путей из {ot} в {do} станет заметно больше настоящего, и никакой "
    "ошибки при этом не возникнет — число просто будет неверным.",

    "Ноль в ответе означает, что до {do} не дошли ни разу. Проверьте две вещи: не "
    "перепутаны ли старт с финишем и правильно ли задана начальная единица — она "
    "ставится ровно одной вершине.",

    "Последняя строка файла бывает пустой, и разбор на три числа на ней падает. "
    "Пропускайте строки, в которых меньше трёх значений: к самому подсчёту путей из "
    "{ot} это отношения не имеет, но программа до него не доживёт.",

    "Считайте в целых числах. Количество путей — это штуки, а не расстояние; если в "
    "расчёт случайно попал вес ребра, ответ станет дробным, и это первый признак, что "
    "в формулу затесалось лишнее.",
]


def _dsn() -> str:
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


def _vershiny(stem: str) -> tuple[str, str] | None:
    """Номера вершин из условия: «из вершины с номером A в вершину с номером B»."""
    m = re.search(r"вершины с номером\s*(\d+)\s*в вершину с номером\s*(\d+)", stem or "")
    return (m.group(1), m.group(2)) if m else None


def _vid(stem: str) -> str:
    return "count" if "количество различных путей" in (stem or "") else "short"


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        stroki = await conn.fetch(
            "SELECT t.id, t.external_uid, c.id AS kurs, c.title AS kurs_title, "
            "t.task_content->>'stem' AS stem, "
            "t.solution_rules#>>'{short_answer,accepted_answers,0,value}' AS etalon "
            "FROM tasks t JOIN courses c ON c.id = t.course_id "
            "WHERE c.course_uid = ANY($1::text[]) AND t.is_active ORDER BY c.id, t.id",
            list(UIDY_BLOKA),
        )
        if not stroki:
            raise RuntimeError("Задания блока 23 не найдены.")

        plan: list[dict] = []
        schetchik: dict[tuple[int, str], int] = {}
        for r in stroki:
            vid = _vid(r["stem"])
            pary = _vershiny(r["stem"])
            if pary is None:
                raise RuntimeError(f"{r['external_uid']}: в условии не нашлись вершины.")
            ot, do = pary
            klyuch = (r["kurs"], vid)
            nomer = schetchik.get(klyuch, 0)
            schetchik[klyuch] = nomer + 1
            hody = HODY_KOL if vid == "count" else HODY_PUT
            tekst = hody[nomer % len(hody)].format(ot=ot, do=do)
            plan.append({"id": r["id"], "uid": r["external_uid"], "kurs": r["kurs"],
                         "kurs_title": r["kurs_title"], "vid": vid,
                         "etalon": r["etalon"], "hint": tekst})

        # Гейт 1: эталон не должен встречаться в подсказке ни в каком виде.
        for p in plan:
            if p["etalon"] and p["etalon"] in p["hint"]:
                raise RuntimeError(f"{p['uid']}: подсказка содержит эталон — это слив ответа.")
        # Гейт 2: в пределах одного узла одинаковых подсказок нет.
        po_uzlam: dict[int, list[str]] = {}
        for p in plan:
            po_uzlam.setdefault(p["kurs"], []).append(p["hint"])
        for kurs, spisok in po_uzlam.items():
            dubli = len(spisok) - len(set(spisok))
            if dubli:
                raise RuntimeError(f"Курс {kurs}: {dubli} одинаковых подсказок в одном узле.")

        print("=== ПЛАН ===")
        for kurs, spisok in po_uzlam.items():
            nazvanie = next(p["kurs_title"] for p in plan if p["kurs"] == kurs)
            print(f"  {kurs} «{nazvanie}»: {len(spisok)} подсказок, "
                  f"различных {len(set(spisok))}")
        print(f"Всего заданий: {len(plan)}")
        print("\nПример (первое задание):")
        print(f"  {plan[0]['uid']}: {plan[0]['hint'][:150]}…")

        if not apply:
            print("\nВхолостую. Записи не было.")
            return

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            for p in plan:
                await conn.execute(
                    "UPDATE tasks SET task_content = jsonb_set("
                    "  jsonb_set(task_content, '{hints_text}', $2::jsonb),"
                    "  '{has_hints}', 'true'::jsonb) WHERE id = $1",
                    p["id"], json.dumps([p["hint"]], ensure_ascii=False),
                )
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'false', true)")

            proverka = await conn.fetch(
                "SELECT t.id, t.task_content->>'has_hints' AS flag, "
                "t.task_content->'hints_text' AS hints, "
                "t.solution_rules#>>'{short_answer,accepted_answers,0,value}' AS etalon "
                "FROM tasks t WHERE t.id = ANY($1::int[])",
                [p["id"] for p in plan],
            )
            bez_podskazki = [r["id"] for r in proverka
                             if r["flag"] != "true" or len(json.loads(r["hints"])) != 1]
            if bez_podskazki:
                raise RuntimeError(f"Без подсказки остались: {bez_podskazki}")
            for r in proverka:
                tekst = json.loads(r["hints"])[0]
                if r["etalon"] and r["etalon"] in tekst:
                    raise RuntimeError(f"{r['id']}: эталон попал в подсказку.")
            print(f"\nПроверено до коммита: {len(proverka)} заданий с подсказкой, "
                  "эталонов в подсказках нет.")

        print("Готово. Подсказки записаны.")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tsk-740 партия 8: подсказки блока 23")
    parser.add_argument("--apply", action="store_true", help="записать подсказки")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
