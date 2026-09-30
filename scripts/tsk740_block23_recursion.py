# -*- coding: utf-8 -*-
"""tsk-740, партия 12 (П1 + П3): рекурсия с запоминанием — основной метод блока 23.

ЗАЧЕМ
Разбор Полякова (30.09, docs/qa/2026-09-30-tsk740-polyakov-23-methods.md) показал, что
один шаблон — функция «от вершины до финиша» с @cache — закрывает оба вида вопроса
(min — кратчайший путь, сумма — количество путей) и опирается на знакомый ученикам
lru_cache из задания 16. Решение оператора 30.09: сделать его основным, Дейкстру
оставить вторым способом (граф с циклами, самопроверка).

ЧТО МЕНЯЕТСЯ
Материалы:
  - новый урок «Кратчайший путь: рекурсия с запоминанием» — первым в листе 1492;
    триггер позиции сам сдвигает Дейкстру на 2, Беллмана-Форда на 3;
  - 3902 Дейкстра — теперь «второй способ»; 3903 — ссылки на основной метод;
  - 3904 количество путей — тот же шаблон, sum вместо min; П3: глубина ≤ 201;
  - 3906 целая программа — на рекурсии; убрана ссылка вперёд «в прошлых уроках».
Курсы: описания листов 1492 и 1493.
Задания (только тексты, эталоны НЕ трогаются): подсказки 10262, 10263, 10266, 10271,
10274, 10284; условие 10291; условие и вариант C у 10293.

Тексты уроков — scripts/tsk740_block23_lessons/*.html; их код проверен
scripts/tsk740_block23_code_check.py (21 программа, пример ФИПИ, демо, боевой файл).

Запуск: вхолостую по умолчанию;
  DBCHECK_OK=1 python scripts/tsk740_block23_recursion.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk740_gen23_graphs import _dsn  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

UROKI = Path(__file__).resolve().parent / "tsk740_block23_lessons"
LIST_KRATCH = 1492

NOVYJ_UROK = {
    "uid": "lms:tsk740:m23:3r",
    "title": "Кратчайший путь: рекурсия с запоминанием",
    "fajl": "m_rek.html",
}
MATERIALY = {  # id -> (файл, новый заголовок или None)
    3902: ("m3902.html", "Второй способ: алгоритм Дейкстры"),
    3903: ("m3903.html", None),
    3904: ("m3904.html", None),
    3906: ("m3906.html", None),
}
KURSY = {
    1492: "Длина кратчайшего пути между вершинами: рекурсия с запоминанием, для графа "
          "с циклами и для самопроверки — Дейкстра и Беллман-Форд.",
    1493: "Подсчёт числа различных путей между вершинами: та же рекурсия с запоминанием, "
          "что и для кратчайшего пути, и проверка топологическим порядком.",
}
PODSKAZKI = {
    10262: "Из вершины 268 может не выходить ни одного ребра. Это не ошибка: для такой вершины "
           "цикл по соседям не выполнится ни разу, и функция вернёт бесконечность — тупик просто "
           "проиграет настоящим путям. Главное, чтобы первой строкой функции стояла проверка "
           "«вершина — это финиш, вернуть 0», иначе тупиком станет и сам финиш.",
    10263: "Если число получилось, но вы в нём не уверены, посчитайте то же самое вторым "
           "способом — Дейкстрой или проходами по списку рёбер (Беллман-Форд). Два разных "
           "алгоритма на одном файле обязаны дать одно число; разошлись — ошибка в чтении файла "
           "или в направлении рёбер.",
    10266: "Если программа остановилась с OverflowError про бесконечность, функция не нашла пути "
           "из 23 в 403. Проверьте порядок в вызове — сначала вершина, откуда идём, потом финиш — "
           "и что номера переписаны из условия без опечатки.",
    10271: "Обязательно поставьте @cache над функцией. Без него каждая вершина будет "
           "пересчитываться заново на каждом пути через неё — это тот же перебор, только "
           "замаскированный, и на пути от 111 до 828 он не закончится.",
    10274: "RecursionError здесь — не про глубину: в файле не больше 200 строк, и вложенных "
           "вызовов не больше 201. Значит, функция ходит по кругу. Проверьте строку, которая "
           "добавляет ребро в словарь: там должна быть пара (куда, вес), а не (откуда, вес) — "
           "такая опечатка делает из каждой вершины петлю на саму себя.",
    10284: "Обязательно поставьте @cache над функцией. Без него каждая вершина будет "
           "пересчитываться заново на каждом пути через неё — это тот же перебор, только "
           "замаскированный, и на пути от 238 до 904 он не закончится.",
}
ZAMENY_USLOVIJ = {  # id -> [(старое, новое)] в stem
    10291: [("print(расстояние[финиш])", "print(лучший(старт, финиш))")],
    10293: [("убрали словарь, в котором запоминались уже посчитанные вершины",
             "убрали строку <code>@cache</code> над функцией")],
}
ZAMENY_VARIANTOV = {  # id -> {вариант: новый текст}
    10293: {"C": "Ничего не изменится, <code>@cache</code> был лишним"},
}
VSE_ZADANIYA = sorted(set(PODSKAZKI) | set(ZAMENY_USLOVIJ) | set(ZAMENY_VARIANTOV))


def novoe_soderzhimoe(tc: dict, zid: int) -> dict:
    """Новый task_content задания: меняются только подсказка, условие и вариант."""
    nov = json.loads(json.dumps(tc))
    if zid in PODSKAZKI:
        nov["hints_text"] = [PODSKAZKI[zid]]
        nov["has_hints"] = True
    for staroe, novoe in ZAMENY_USLOVIJ.get(zid, []):
        if staroe not in nov["stem"]:
            raise RuntimeError(f"{zid}: в условии нет фрагмента «{staroe}».")
        nov["stem"] = nov["stem"].replace(staroe, novoe)
    for var, tekst in ZAMENY_VARIANTOV.get(zid, {}).items():
        hit = [o for o in nov["options"] if o["id"] == var]
        if len(hit) != 1:
            raise RuntimeError(f"{zid}: нет варианта {var}.")
        hit[0]["text"] = tekst
    return nov


async def main(apply: bool) -> None:
    teksty = {mid: (UROKI / f).read_text(encoding="utf-8") for mid, (f, _) in MATERIALY.items()}
    novyj_tekst = (UROKI / NOVYJ_UROK["fajl"]).read_text(encoding="utf-8")
    for t in [novyj_tekst, *teksty.values()]:
        if "heapq.heappop(очередь)" in t and "Дейкстр" not in t:
            raise RuntimeError("Код Дейкстры остался вне урока про Дейкстру.")

    conn = await asyncpg.connect(_dsn())
    try:
        zad = {r["id"]: r for r in await conn.fetch(
            "SELECT id, task_content::text AS tc, solution_rules::text AS sr FROM tasks "
            "WHERE id = ANY($1::int[])", VSE_ZADANIYA)}
        if len(zad) != len(VSE_ZADANIYA):
            raise RuntimeError(f"Нашлось {len(zad)} заданий из {len(VSE_ZADANIYA)}.")
        novye_tc = {zid: novoe_soderzhimoe(json.loads(r["tc"]), zid) for zid, r in zad.items()}
        # Подсказка не выдаёт эталон (правило партии 9).
        for zid, tc in novye_tc.items():
            sr = json.loads(zad[zid]["sr"])
            etalon = ((sr.get("short_answer") or {}).get("accepted_answers") or [{}])[0].get("value")
            if etalon and any(etalon in h for h in tc.get("hints_text", [])):
                raise RuntimeError(f"{zid}: эталон {etalon} виден в подсказке.")

        log.info("=== ПЛАН ===")
        log.info("новый урок в листе %s на позицию 1: «%s» (%d знаков)",
                 LIST_KRATCH, NOVYJ_UROK["title"], len(novyj_tekst))
        for mid, (f, zag) in MATERIALY.items():
            log.info("урок %s <- %s%s (%d знаков)", mid, f, f", заголовок «{zag}»" if zag else "",
                     len(teksty[mid]))
        for kid in KURSY:
            log.info("описание курса %s", kid)
        for zid in VSE_ZADANIYA:
            chto = [k for k, d in (("подсказка", PODSKAZKI), ("условие", ZAMENY_USLOVIJ),
                                   ("вариант", ZAMENY_VARIANTOV)) if zid in d]
            log.info("задание %s: %s", zid, ", ".join(chto))
        if not apply:
            log.info("\nВхолостую: база не тронута.")
            return

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.audit_actor', 'tsk-740 партия 12', true)")
            est = await conn.fetchval("SELECT id FROM materials WHERE external_uid = $1",
                                      NOVYJ_UROK["uid"])
            soderzh = json.dumps({"text": novyj_tekst, "format": "html"}, ensure_ascii=False)
            if est is None:
                await conn.execute(
                    "INSERT INTO materials (course_id, type, content, order_position, title, "
                    "is_active, external_uid, requirement_level) "
                    "VALUES ($1, 'text', $2::jsonb, 1, $3, true, $4, 'required')",
                    LIST_KRATCH, soderzh, NOVYJ_UROK["title"], NOVYJ_UROK["uid"])
            else:
                await conn.execute("UPDATE materials SET content = $2::jsonb, title = $3 WHERE id = $1",
                                   est, soderzh, NOVYJ_UROK["title"])
            for mid, (_, zag) in MATERIALY.items():
                await conn.execute(
                    "UPDATE materials SET content = jsonb_set(content, '{text}', to_jsonb($2::text)), "
                    "title = COALESCE($3, title) WHERE id = $1", mid, teksty[mid], zag)
            for kid, opis in KURSY.items():
                await conn.execute("UPDATE courses SET description = $2 WHERE id = $1", kid, opis)
            for zid, tc in novye_tc.items():
                await conn.execute("UPDATE tasks SET task_content = $2::jsonb WHERE id = $1",
                                   zid, json.dumps(tc, ensure_ascii=False))

            # --- верификация до коммита
            poryadok = await conn.fetch(
                "SELECT id, external_uid, order_position FROM materials "
                "WHERE course_id = $1 AND is_active ORDER BY order_position", LIST_KRATCH)
            uidy = [r["external_uid"] for r in poryadok]
            if uidy != [NOVYJ_UROK["uid"], "lms:tsk740:m23:3", "lms:tsk740:m23:4"] \
                    or [r["order_position"] for r in poryadok] != [1, 2, 3]:
                raise RuntimeError(f"Порядок уроков листа 1492: {[tuple(r) for r in poryadok]}")
            for mid in MATERIALY:
                t = await conn.fetchval("SELECT content->>'text' FROM materials WHERE id = $1", mid)
                if t != teksty[mid]:
                    raise RuntimeError(f"Урок {mid}: текст в базе не совпал с файлом.")
            posle = {r["id"]: r for r in await conn.fetch(
                "SELECT id, task_content::text AS tc, solution_rules::text AS sr FROM tasks "
                "WHERE id = ANY($1::int[])", VSE_ZADANIYA)}
            for zid in VSE_ZADANIYA:
                if json.loads(posle[zid]["sr"]) != json.loads(zad[zid]["sr"]):
                    raise RuntimeError(f"{zid}: правила проверки изменились — так нельзя.")
                if json.loads(posle[zid]["tc"]) != novye_tc[zid]:
                    raise RuntimeError(f"{zid}: содержимое в базе не совпало с планом.")
            vsego = await conn.fetchval(
                "SELECT count(*) FROM materials WHERE course_id = $1 AND is_active", LIST_KRATCH)
            zadanij = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = $1 AND is_active", LIST_KRATCH)
            if vsego + zadanij > 20:
                raise RuntimeError(f"Лист 1492: {vsego} + {zadanij} > 20 сущностей.")
            log.info("Проверено до коммита: порядок уроков 1492 %s, тексты совпали, эталоны "
                     "не тронуты, лист 1492 — %d сущностей.", [r["id"] for r in poryadok],
                     vsego + zadanij)
        log.info("Готово.")
    finally:
        await conn.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="tsk-740 П1+П3: рекурсия — основной метод блока 23")
    parser.add_argument("--apply", action="store_true", help="записать в боевую базу")
    asyncio.run(main(parser.parse_args().apply))
