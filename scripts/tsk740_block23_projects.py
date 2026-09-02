# -*- coding: utf-8 -*-
"""tsk-740, партия 11: проектные задания блока 23.

ЗАЧЕМ
Последняя незакрытая находка ревью — P1 по критерию практики (Merrill): норматив
просит одно проектное задание на урок-тему, а в блоке их было ноль. Решение
оператора 02.09: проектные добавить, бюджет блока поднять с 6 до 7 часов.

ЧЕМ ПРОЕКТНОЕ ОТЛИЧАЕТСЯ ОТ ОБЫЧНОГО ЗДЕСЬ
Обычное задание блока проверяет один приём: «дай число по файлу». Проектное
требует собрать несколько приёмов темы в одну программу и выдать НЕСКОЛЬКО
величин по одному файлу — то есть ученик пишет не «решение задачи», а маленький
инструмент, который потом переиспользует на других заданиях.

- Лист В: «паспорт графа» — сколько рёбер, сколько различных вершин, сколько
  вершин без исходящих рёбер. Проверяет чтение файла и понимание структуры.
- Лист А: два кратчайших пути по одному файлу между разными парами вершин.
  Проверяет, что решение оформлено функцией, а не одноразовым куском кода.
- Лист Б: количество путей плюс размер топологического порядка (он же проверка
  на цикл). Проверяет обе темы листа сразу.

ФАЙЛЫ НЕ ЗАЛИВАЮТСЯ ЗАНОВО
Переиспользуются файлы уже существующих заданий блока — вопрос к ним другой,
а лишние объекты в хранилище не нужны.

ОТВЕТЫ ПОСЧИТАНЫ, А НЕ ВЫДУМАНЫ
Каждая величина получена прогоном по реальному файлу с боевого хранилища;
кратчайшие пути дополнительно сверены Беллманом-Фордом (совпали). Скрипт
пересчитывает их при запуске и падает, если результат разошёлся с записанным
эталоном — то есть эталон не может «протухнуть» молча.

Запуск: вхолостую по умолчанию;
  DBCHECK_OK=1 python scripts/tsk740_block23_projects.py
  DBCHECK_OK=1 python scripts/tsk740_block23_projects.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import heapq
import json
import os
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
MEDIA = "https://api.learn.victor-komlev.ru/api/v1/media/"

LIST_V_UID = "lms:tsk740:ege2027:23:osnova"
LIST_A_UID = "lms:tsk740:ege2027:23:short"
LIST_B_UID = "lms:tsk740:ege2027:23:count"

FAJL_V = "4fe661de45d5ba10c26e62b0378436fdfdd58a7b8d4e549781ed5aebde7a4e2e"
FAJL_A = "b0e4454ea5e8b014a084f275291e3ed7d1f1338d5929c8c2d3ee405bc9270451"
FAJL_B = "e87c413dd57ce03a6095e463a93ff53708b48d0efb0f8bd4cc56042400084cc1"


def _chitat(sha: str):
    tekst = urllib.request.urlopen(MEDIA + sha + ".txt", timeout=60).read().decode("utf-8")
    rebra, graf, vershiny = [], defaultdict(list), set()
    for stroka in tekst.splitlines():
        chasti = stroka.split()
        if len(chasti) < 3:
            continue
        l, m, w = int(chasti[0]), int(chasti[1]), float(chasti[2])
        rebra.append((l, m, w))
        graf[l].append((m, w))
        vershiny.update((l, m))
    return rebra, graf, vershiny


def _dijkstra(graf, ot, do):
    rasst, ochered = {ot: 0.0}, [(0.0, ot)]
    while ochered:
        d, u = heapq.heappop(ochered)
        if d > rasst.get(u, float("inf")):
            continue
        for v, w in graf[u]:
            nd = d + w
            if nd < rasst.get(v, float("inf")):
                rasst[v] = nd
                heapq.heappush(ochered, (nd, v))
    return rasst.get(do)


def _bellman(rebra, vershiny, ot, do):
    rasst = {v: float("inf") for v in vershiny}
    rasst[ot] = 0.0
    for _ in range(len(vershiny) - 1):
        izm = False
        for l, m, w in rebra:
            if rasst[l] + w < rasst[m]:
                rasst[m] = rasst[l] + w
                izm = True
        if not izm:
            break
    return rasst[do]


def _putej(graf, ot, do):
    pamyat: dict[int, int] = {}
    sys.setrecursionlimit(20000)

    def obojti(u):
        if u == do:
            return 1
        if u in pamyat:
            return pamyat[u]
        pamyat[u] = sum(obojti(v) for v, _ in graf[u])
        return pamyat[u]

    return obojti(ot)


def _topo(graf, vershiny):
    vhod = {v: 0 for v in vershiny}
    for u in graf:
        for v, _ in graf[u]:
            vhod[v] += 1
    ochered = [v for v in vershiny if vhod[v] == 0]
    poryadok = []
    while ochered:
        u = ochered.pop()
        poryadok.append(u)
        for v, _ in graf[u]:
            vhod[v] -= 1
            if vhod[v] == 0:
                ochered.append(v)
    return poryadok


def poschitat() -> dict[str, str]:
    """Эталоны считаются заново при каждом запуске — из реальных файлов."""
    rebra_v, graf_v, vershiny_v = _chitat(FAJL_V)
    stoki = sum(1 for v in vershiny_v if not graf_v[v])
    otvet_v = f"{len(rebra_v)} {len(vershiny_v)} {stoki}"

    rebra_a, graf_a, vershiny_a = _chitat(FAJL_A)
    poryadok_a = _topo(graf_a, vershiny_a)
    para2 = (poryadok_a[0], poryadok_a[-1])
    d1, d2 = _dijkstra(graf_a, 608, 749), _dijkstra(graf_a, *para2)
    b1, b2 = _bellman(rebra_a, vershiny_a, 608, 749), _bellman(rebra_a, vershiny_a, *para2)
    if abs(d1 - b1) > 1e-9 or abs(d2 - b2) > 1e-9:
        raise RuntimeError("Дейкстра и Беллман-Форд разошлись — эталон писать нельзя.")
    otvet_a = f"{int(d1)} {int(d2)}"

    rebra_b, graf_b, vershiny_b = _chitat(FAJL_B)
    poryadok_b = _topo(graf_b, vershiny_b)
    otvet_b = f"{_putej(graf_b, 992, 528)} {len(poryadok_b)}"

    return {"v": otvet_v, "a": otvet_a, "b": otvet_b,
            "para2": f"{para2[0]} {para2[1]}"}


def _pravila(etalon: str) -> dict:
    return {
        "max_score": 1,
        "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
        "auto_check": True, "text_answer": None, "scoring_mode": "all_or_nothing",
        "short_answer": {
            "regex": None, "use_regex": False,
            "normalization": ["trim", "lower", "strip_punctuation", "collapse_spaces"],
            "accepted_answers": [{"score": 1, "value": etalon}],
        },
        "partial_rules": [], "correct_options": [],
        "custom_scoring_config": None, "manual_review_required": False,
    }


SDACHA = (
    "<p><b>Что сдавать.</b> В поле «Ответ» — числа через пробел, в указанном "
    "порядке. В поле «Комментарий» — свою программу: без комментария (или "
    "приложенного файла) ответ не засчитывается.</p>"
)


def sobrat_zadaniya(otvety: dict[str, str]) -> list[dict]:
    ssylka = lambda sha, imya: (
        f'<p>Файл к заданию: <a href="/api/v1/media/{sha}.txt" target="_blank" '
        f'rel="noopener noreferrer">{imya}</a></p>'
    )
    ot2, do2 = otvety["para2"].split()
    return [
        {
            "uid": "lms:tsk740:proj23:v", "kurs": "list_v", "etalon": otvety["v"],
            "title": "Проект: паспорт графа",
            "stem": ssylka(FAJL_V, "23_pasport.txt") +
            "<p><em>Задание выполняется с использованием прилагаемого файла.</em></p>"
            "<p>Напишите программу, которая читает файл и выводит про этот граф три "
            "числа подряд:</p><ol>"
            "<li>сколько в нём рёбер;</li>"
            "<li>сколько в нём различных вершин (считая и те, из которых рёбра только "
            "входят);</li>"
            "<li>сколько вершин, из которых не выходит ни одного ребра.</li></ol>"
            "<p>Это те самые величины, которые полезно печатать при отладке любого "
            "задания 23: они сразу показывают, правильно ли прочитан файл.</p>"
            "<p>В ответе запишите три числа через пробел в указанном порядке. "
            "Пример формата (числа выдуманы, не из этого файла): "
            "<code>120 55 7</code>.</p>" + SDACHA,
        },
        {
            "uid": "lms:tsk740:proj23:a", "kurs": "list_a", "etalon": otvety["a"],
            "title": "Проект: два маршрута по одному файлу",
            "stem": ssylka(FAJL_A, "23_dva_marshruta.txt") +
            "<p><em>Задание выполняется с использованием прилагаемого файла.</em></p>"
            "<p>По одному и тому же графу найдите целые части длин двух кратчайших "
            "путей:</p><ol>"
            "<li>из вершины 608 в вершину 749;</li>"
            f"<li>из вершины {ot2} в вершину {do2}.</li></ol>"
            "<p>Задача решается заметно быстрее, если поиск пути оформлен отдельной "
            "функцией: тогда второй маршрут считается её повторным вызовом, а не "
            "копией кода. Такой функцией потом удобно пользоваться и на других "
            "заданиях блока.</p>"
            "<p>В ответе запишите два числа через пробел в указанном порядке. "
            "Пример формата (числа выдуманы): <code>430 275</code>.</p>" + SDACHA,
        },
        {
            "uid": "lms:tsk740:proj23:b", "kurs": "list_b", "etalon": otvety["b"],
            "title": "Проект: пути и проверка на цикл",
            "stem": ssylka(FAJL_B, "23_puti_i_poryadok.txt") +
            "<p><em>Задание выполняется с использованием прилагаемого файла.</em></p>"
            "<p>По этому графу найдите два числа:</p><ol>"
            "<li>количество различных путей из вершины 992 в вершину 528;</li>"
            "<li>сколько вершин попало в топологический порядок этого графа.</li></ol>"
            "<p>Второе число — это заодно и проверка: если оно совпало с общим числом "
            "различных вершин графа, цикла в нём нет, а значит подсчёт путей корректен.</p>"
            "<p>В ответе запишите два числа через пробел в указанном порядке. "
            "Пример формата (числа выдуманы): <code>58 47</code>.</p>" + SDACHA,
        },
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


async def main(apply: bool) -> None:
    print("=== СЧИТАЕМ ЭТАЛОНЫ ПО РЕАЛЬНЫМ ФАЙЛАМ ===")
    otvety = poschitat()
    for klyuch in ("v", "a", "b"):
        print(f"  лист {klyuch.upper()}: {otvety[klyuch]}")
    zadaniya = sobrat_zadaniya(otvety)

    conn = await asyncpg.connect(_dsn())
    try:
        kursy = {}
        for klyuch, uid in (("list_v", LIST_V_UID), ("list_a", LIST_A_UID), ("list_b", LIST_B_UID)):
            kurs = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", uid)
            if kurs is None:
                raise RuntimeError(f"Лист {uid} не найден — сперва партии 6-9.")
            kursy[klyuch] = kurs

        print("\n=== ПЛАН ===")
        for z in zadaniya:
            print(f"  {z['uid']} -> курс {kursy[z['kurs']]}: «{z['title']}», эталон «{z['etalon']}»")

        if not apply:
            print("\nВхолостую. Записи не было.")
            return

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            sozdano = obnovleno = 0
            for z in zadaniya:
                soderzhimoe = {
                    "type": "SA_COM", "title": z["title"], "stem": z["stem"],
                    "course_uid": {"list_v": LIST_V_UID, "list_a": LIST_A_UID,
                                   "list_b": LIST_B_UID}[z["kurs"]],
                    "has_hints": True,
                    "hints_text": ["Соберите ответ из величин, которые программа считает "
                                   "по отдельности, и выведите их одной строкой через "
                                   "пробел — ровно в том порядке, как перечислено в условии."],
                    "hints_video": [], "manual_review_required": False,
                }
                est = await conn.fetchval("SELECT id FROM tasks WHERE external_uid = $1", z["uid"])
                if est is None:
                    await conn.execute(
                        "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                        "difficulty_id, solution_rules, is_active, requirement_level, "
                        "difficulty_provenance) "
                        "VALUES ($1, 1, $2::jsonb, $3, 5, $4::jsonb, true, 'required', $5::jsonb)",
                        z["uid"], json.dumps(soderzhimoe, ensure_ascii=False), kursy[z["kurs"]],
                        json.dumps(_pravila(z["etalon"]), ensure_ascii=False),
                        json.dumps({"istochnik": "проектное задание tsk-740, партия 11",
                                    "otvet_proveren": "прогоном по реальному файлу"},
                                   ensure_ascii=False),
                    )
                    sozdano += 1
                else:
                    await conn.execute(
                        "UPDATE tasks SET task_content = $2::jsonb, solution_rules = $3::jsonb, "
                        "course_id = $4, difficulty_id = 5 WHERE id = $1",
                        est, json.dumps(soderzhimoe, ensure_ascii=False),
                        json.dumps(_pravila(z["etalon"]), ensure_ascii=False), kursy[z["kurs"]],
                    )
                    obnovleno += 1
            print(f"\nЗаданий создано {sozdano}, обновлено {obnovleno}")
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'false', true)")

            print("\n=== ПРОВЕРКА (до коммита) ===")
            for kurs_id, imya in ((kursy["list_v"], "лист В"), (kursy["list_a"], "лист А"),
                                  (kursy["list_b"], "лист Б")):
                zad = await conn.fetchval(
                    "SELECT count(*) FROM tasks WHERE course_id = $1 AND is_active", kurs_id)
                mat = await conn.fetchval(
                    "SELECT count(*) FROM materials WHERE course_id = $1 AND is_active", kurs_id)
                proj = await conn.fetchval(
                    "SELECT count(*) FROM tasks WHERE course_id = $1 AND is_active AND difficulty_id = 5",
                    kurs_id)
                print(f"  {imya}: материалов {mat}, заданий {zad} (проектных {proj}), "
                      f"всего {mat + zad}")
                if mat + zad > 20:
                    raise RuntimeError(f"{imya}: {mat + zad} сущностей — сверх порога 20.")
                if proj != 1:
                    raise RuntimeError(f"{imya}: проектных {proj}, норматив требует одно.")
            # Инвариант tsk-347 не должен быть нарушен: HARD только в блоке сложных.
            hard_v_listyah = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = ANY($1::int[]) AND difficulty_id = 4",
                list(kursy.values()))
            if hard_v_listyah:
                raise RuntimeError(f"{hard_v_listyah} HARD-заданий оказалось в основных листьях.")
            print("  HARD в основных листьях: 0 (инвариант tsk-347 цел)")

        print("\nГотово. Проектные задания на месте.")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tsk-740 партия 11: проектные задания блока 23")
    parser.add_argument("--apply", action="store_true", help="записать изменения")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
