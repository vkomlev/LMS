# -*- coding: utf-8 -*-
"""tsk-740, партия 5: собственный банк заданий 23 (графы) по формату ЕГЭ-2027.

ЗАЧЕМ
Решение оператора 01.09 — вариант «б»: чужой банк не переносим, генерируем свой.
Задание 23 параметрическое: условие одно, меняются граф и вопрос. Это снимает
вопрос авторства и не привязывает нас к чужому сайту.

ДВА ВОПРОСА ИЗ СПЕЦИФИКАЦИИ
Кодификатор ЕГЭ-2027 называет для задания 23 ровно два вида:
  «задачи построения оптимального пути между вершинами графа» и
  «определения количества различных путей между вершинами ориентированного
   ациклического графа».
Генератор делает оба: `short` (целая часть длины кратчайшего пути) и
`count` (число различных путей).

ФОРМАТ ФАЙЛА — как у ФИПИ
Строка «L M W»: два натуральных номера вершин и положительный вещественный вес.
Ограничения демоверсии соблюдены: L ≤ 1000, M ≤ 1000, W ≤ 10 000, строк ≤ 200,
две вершины соединены не более чем одним ребром, граф ациклический
ориентированный, номера вершин идут НЕ подряд, разделитель — произвольное
количество пробелов и табуляций.

ЧЕМ ПРОВЕРЕН КАЖДЫЙ ОТВЕТ (§ 9 плейбука — два независимых признака)
Каждая задача решается ДВУМЯ независимыми алгоритмами, и ответ принимается
только при совпадении:
  - кратчайший путь: Дейкстра И Беллман-Форд (второй — из разбора Полякова,
    посты @kp_spb 2157-2159);
  - количество путей: динамика по топологическому порядку И обход в глубину
    с запоминанием.
Плюс общий гейт: те же функции обязаны воспроизвести ответ демоверсии ФИПИ
(10971) и её же типовой пример из условия (7). Не воспроизвели — скрипт падает
и ничего не пишет.

ГЕНЕРАЦИЯ ДЕТЕРМИНИРОВАННА
Зерно случайности задаётся номером задания, поэтому повторный запуск даёт те же
графы и те же файлы (sha256 совпадёт, CAS идемпотентен по содержимому).
`random.Random(seed)` — свой генератор, глобальный `random` не трогаем.

СЛОЖНОСТЬ И КУДА КЛАДЁТСЯ
EASY и NORMAL — в курс 1490 «Задание 23 ЕГЭ по информатике. Анализ графов».
HARD — в отдельный подкурс «Задание 23. Сложные» блока 1378: инвариант tsk-347
требует `difficulty_id=4` ⟺ курс внутри 1379–1403, иначе следующая доливка
вернёт задание в основной поток.

Запуск: вхолостую по умолчанию;
  DBCHECK_OK=1 python scripts/tsk740_gen23_graphs.py
  DBCHECK_OK=1 python scripts/tsk740_gen23_graphs.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import heapq
import json
import os
import random
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
CB_ROOT = Path(r"D:\Work\ContentBackbone")
MEDIA_URL = "https://api.learn.victor-komlev.ru/api/v1/media/{}"
VYVOD = project_root / "reviews" / "tsk740-gen23"

ROOT_COURSE_ID = 112
HARD_CONTAINER_ID = 1378
KURS_UID = "lms:tsk740:ege2027:23"
HARD_KURS_UID = "lms:tsk347:hard:1490"
HARD_KURS_TITLE = "Задание 23. Сложные"
HARD_ORDER = 22

PREDEL_VERSHINY = 1000
PREDEL_VES = 10000
PREDEL_STROK = 200

# (ключ, вид вопроса, сложность, рёбер, вершин)
NABOR: list[tuple[str, str, int, int, int]] = [
    *[(f"s{i}", "short", 2, 45 + i * 4, 26 + i * 2) for i in range(1, 6)],
    *[(f"s{i}", "short", 3, 95 + (i - 6) * 8, 45 + (i - 6) * 4) for i in range(6, 11)],
    *[(f"c{i}", "count", 2, 40 + i * 4, 24 + i * 2) for i in range(1, 6)],
    *[(f"c{i}", "count", 3, 90 + (i - 6) * 8, 42 + (i - 6) * 4) for i in range(6, 11)],
    *[(f"h{i}", "short", 4, 168 + i * 6, 78 + i * 6) for i in range(1, 4)],
    *[(f"h{i}", "count", 4, 150 + (i - 3) * 8, 96 + (i - 3) * 8) for i in range(4, 7)],
]

# Число путей в плотном графе растёт лавиной: 120 вершин при 190 рёбрах дают
# ответ в двадцать знаков, который ученику не набрать. Держим ответ в разумных
# рамках, подбирая зерно — детерминированность при этом сохраняется.
PREDEL_OTVETA_PUTEJ = 10**9


# ---------------------------------------------------------------- решатели

def _razobrat(tekst: str) -> list[tuple[int, int, float]]:
    """Рёбра из описания «L M W». Пустые и короткие строки пропускаются."""
    rebra = []
    for stroka in tekst.splitlines():
        chasti = stroka.split()
        if len(chasti) < 3:
            continue
        rebra.append((int(chasti[0]), int(chasti[1]), float(chasti[2])))
    return rebra


def dejkstra(rebra, ot: int, do: int) -> float | None:
    """Кратчайший путь. Первый из двух независимых способов."""
    graf = defaultdict(list)
    for l, m, w in rebra:
        graf[l].append((m, w))
    rasst = {ot: 0.0}
    ochered = [(0.0, ot)]
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


def bellman_ford(rebra, ot: int, do: int) -> float | None:
    """Кратчайший путь. Второй способ — из разбора Полячкова (@kp_spb 2159)."""
    vershiny = {v for l, m, _ in rebra for v in (l, m)}
    rasst = {v: float("inf") for v in vershiny}
    rasst[ot] = 0.0
    for _ in range(len(vershiny) - 1):
        izmenilos = False
        for l, m, w in rebra:
            if rasst[l] + w < rasst[m]:
                rasst[m] = rasst[l] + w
                izmenilos = True
        if not izmenilos:
            break
    znachenie = rasst.get(do, float("inf"))
    return None if znachenie == float("inf") else znachenie


def putej_topologicheski(rebra, ot: int, do: int) -> int:
    """Число различных путей. Первый способ — динамика по топологическому порядку."""
    graf = defaultdict(list)
    vhod = defaultdict(int)
    vershiny = set()
    for l, m, _ in rebra:
        graf[l].append(m)
        vhod[m] += 1
        vershiny.update((l, m))
    ochered = [v for v in vershiny if vhod[v] == 0]
    poryadok = []
    while ochered:
        u = ochered.pop()
        poryadok.append(u)
        for v in graf[u]:
            vhod[v] -= 1
            if vhod[v] == 0:
                ochered.append(v)
    if len(poryadok) != len(vershiny):
        raise RuntimeError("В графе найден цикл — генератор обязан строить ациклический.")
    skolko = defaultdict(int)
    skolko[ot] = 1
    for u in poryadok:
        if not skolko[u]:
            continue
        for v in graf[u]:
            skolko[v] += skolko[u]
    return skolko[do]


def putej_pamyatyu(rebra, ot: int, do: int) -> int:
    """Число различных путей. Второй способ — обход в глубину с запоминанием."""
    graf = defaultdict(list)
    for l, m, _ in rebra:
        graf[l].append(m)
    pamyat: dict[int, int] = {}

    def obojti(u: int) -> int:
        if u == do:
            return 1
        if u in pamyat:
            return pamyat[u]
        pamyat[u] = sum(obojti(v) for v in graf[u])
        return pamyat[u]

    sys.setrecursionlimit(10000)
    return obojti(ot)


def reshit(rebra, vid: str, ot: int, do: int) -> int:
    """Ответ, подтверждённый двумя независимыми способами. Иначе исключение."""
    if vid == "short":
        a, b = dejkstra(rebra, ot, do), bellman_ford(rebra, ot, do)
        if a is None or b is None or abs(a - b) > 1e-9:
            raise RuntimeError(f"Кратчайший путь: Дейкстра {a}, Беллман-Форд {b} — не сошлись.")
        return int(a)
    a, b = putej_topologicheski(rebra, ot, do), putej_pamyatyu(rebra, ot, do)
    if a != b or a == 0:
        raise RuntimeError(f"Число путей: топологически {a}, обходом {b} — не сошлись или ноль.")
    return a


# ---------------------------------------------------------------- генератор

def postroit(seed: int, reber: int, vershin: int) -> tuple[str, int, int]:
    """Ациклический орграф. Возвращает (текст файла, начальная, конечная вершина)."""
    rnd = random.Random(seed)
    if reber > PREDEL_STROK:
        raise ValueError(f"Рёбер {reber} — сверх предела ЕГЭ ({PREDEL_STROK} строк).")
    # Номера вершин НЕ подряд — как оговаривает условие ФИПИ.
    nomera = rnd.sample(range(1, PREDEL_VERSHINY + 1), vershin)
    # Топологический порядок задаётся перестановкой: рёбра идут только вперёд,
    # поэтому цикл невозможен по построению, а не по проверке постфактум.
    poryadok = nomera[:]
    rnd.shuffle(poryadok)
    ot, do = poryadok[0], poryadok[-1]

    rebra: dict[tuple[int, int], float] = {}
    ves = lambda: round(rnd.uniform(0.5, PREDEL_VES / 12), 1)
    # Хребет от начала до конца — гарантия, что путь существует.
    hrebet = sorted(rnd.sample(range(1, vershin - 1), min(6, vershin - 2)))
    tochki = [0] + hrebet + [vershin - 1]
    for a, b in zip(tochki, tochki[1:]):
        rebra[(poryadok[a], poryadok[b])] = ves()
    # Остальные рёбра — вперёд по порядку, без повторов пары вершин.
    popytok = 0
    while len(rebra) < reber and popytok < reber * 40:
        popytok += 1
        i = rnd.randrange(0, vershin - 1)
        j = rnd.randrange(i + 1, vershin)
        para = (poryadok[i], poryadok[j])
        if para in rebra:
            continue
        rebra[para] = ves()

    # Порядок строк в файле перемешан — как в файле ФИПИ, где первая строка
    # «100 12 1.0» ведёт из конечной вершины примера.
    stroki = list(rebra.items())
    rnd.shuffle(stroki)
    tekst = "\n".join(
        f"{l}{' ' * rnd.randint(1, 3)}{m}{chr(9) if rnd.random() < 0.25 else ' '}{w}"
        for (l, m), w in stroki
    )
    return tekst + "\n", ot, do


def uslovie(vid: str, ssylka: str, imya: str, ot: int, do: int) -> str:
    """Условие по образцу демоверсии ФИПИ 2027, с нашим вопросом."""
    if vid == "short":
        vopros = (
            f"<p>Найдите и запишите в ответе целую часть длины кратчайшего пути из вершины "
            f"с номером {ot} в вершину с номером {do}. Существование хотя бы одного такого "
            "пути гарантируется. Под длиной кратчайшего пути понимается минимальная сумма "
            "весов рёбер, составляющих путь.</p>"
        )
    else:
        vopros = (
            f"<p>Найдите и запишите в ответе количество различных путей из вершины "
            f"с номером {ot} в вершину с номером {do}. Существование хотя бы одного такого "
            "пути гарантируется. Пути считаются различными, если они отличаются хотя бы "
            "одним ребром.</p>"
        )
    return (
        f'<p>Файл к заданию: <a href="{ssylka}" target="_blank" '
        f'rel="noopener noreferrer">{imya}</a></p>\n'
        "<p><em>Задание выполняется с использованием прилагаемого файла.</em></p>\n"
        "<p>В текстовом файле содержится описание ациклического ориентированного "
        "взвешенного графа. В каждой строке файла записаны два натуральных числа (L, M) "
        "и одно положительное вещественное число (W). L и M — номера вершин графа, "
        "W — вес ребра, ведущего из вершины L в вершину M. Таким образом, количество строк "
        "в файле равно количеству рёбер в графе. Две вершины графа не могут быть соединены "
        "более чем одним ребром.</p>\n"
        f"{vopros}\n"
        "<p>Для выполнения этого задания следует написать программу.</p>\n"
        "<p>Вершины графа могут быть пронумерованы не подряд. L ≤ 1000, M ≤ 1000; "
        "W ≤ 10 000. Количество строк в файле не превосходит 200. Числа в строках "
        "разделены произвольным ненулевым количеством пробелов и/или табуляций.</p>"
    )


PRIMER_FIPI = """100 12 1.0
6 7 7.0
6 1 1.0
1 7 5.5
7 100 2.0
4 100 8.0
1 100 12.0
1 4 2.5"""


def pravila(etalon: str) -> dict:
    return {
        "max_score": 1,
        "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
        "auto_check": True,
        "text_answer": None,
        "scoring_mode": "all_or_nothing",
        "short_answer": {
            "regex": None,
            "use_regex": False,
            "normalization": ["trim", "lower", "collapse_spaces"],
            "accepted_answers": [{"score": 1, "value": etalon}],
        },
        "partial_rules": [],
        "correct_options": [],
        "custom_scoring_config": None,
        "manual_review_required": False,
    }


def _proverit_fajl(sha_ext: str) -> tuple[bool, str]:
    req = urllib.request.Request(MEDIA_URL.format(sha_ext), headers={"User-Agent": "tsk740/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status == 200, f"HTTP {resp.status}, {resp.headers.get('Content-Length')} байт"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return False, f"сеть: {exc}"


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


def gejt_reshatelej() -> None:
    """Решатели обязаны воспроизвести ФИПИ до того, как считать своё."""
    demo = project_root / "reviews" / "2026-09-01-tsk740-fipi-demo23.txt"
    rebra_demo = _razobrat(demo.read_text(encoding="utf-8"))
    otvet_demo = reshit(rebra_demo, "short", 1, 100)
    rebra_primer = _razobrat(PRIMER_FIPI)
    otvet_primer = reshit(rebra_primer, "short", 1, 100)
    print("=== ГЕЙТ РЕШАТЕЛЕЙ ===")
    print(f"файл демоверсии ФИПИ : {otvet_demo} (ключ ФИПИ 10971)")
    print(f"типовой пример       : {otvet_primer} (ФИПИ называет 7)")
    if otvet_demo != 10971 or otvet_primer != 7:
        raise RuntimeError("Решатели не воспроизводят ФИПИ — считать своё нельзя.")
    print("Оба способа сошлись и совпали с ФИПИ.\n")


async def main(apply: bool) -> None:
    gejt_reshatelej()
    VYVOD.mkdir(parents=True, exist_ok=True)

    zadaniya = []
    for nomer, (klyuch, vid, slozhnost, reber, vershin) in enumerate(NABOR, start=1):
        # Подбор зерна: для «числа путей» отбрасываем графы с неподъёмным ответом.
        for popytka in range(60):
            tekst, ot, do = postroit(seed=740_000 + nomer * 100 + popytka,
                                     reber=reber, vershin=vershin)
            rebra = _razobrat(tekst)
            if len(rebra) > PREDEL_STROK:
                raise RuntimeError(f"{klyuch}: строк {len(rebra)} — сверх предела ЕГЭ.")
            if max(max(l, m) for l, m, _ in rebra) > PREDEL_VERSHINY:
                raise RuntimeError(f"{klyuch}: номер вершины сверх 1000.")
            if max(w for _, _, w in rebra) > PREDEL_VES:
                raise RuntimeError(f"{klyuch}: вес сверх 10 000.")
            znachenie = reshit(rebra, vid, ot, do)
            if vid == "count" and znachenie > PREDEL_OTVETA_PUTEJ:
                continue
            break
        else:
            raise RuntimeError(f"{klyuch}: за 60 попыток не нашёлся граф с ответом "
                               f"до {PREDEL_OTVETA_PUTEJ}. Уменьшить плотность в наборе.")
        etalon = str(znachenie)
        imya = f"23_{klyuch}.txt"
        put = VYVOD / imya
        put.write_text(tekst, encoding="utf-8", newline="\n")
        sha = hashlib.sha256(put.read_bytes()).hexdigest()
        zadaniya.append({
            "klyuch": klyuch, "vid": vid, "slozhnost": slozhnost,
            "reber": len(rebra), "vershin": vershin, "ot": ot, "do": do,
            "etalon": etalon, "imya": imya, "sha_ext": f"{sha}.txt", "put": put,
            "uid": f"lms:tsk740:gen23:{klyuch}",
        })

    print("=== СГЕНЕРИРОВАНО ===")
    for z in zadaniya:
        vid_ru = "кратчайший путь" if z["vid"] == "short" else "число путей"
        sl = {2: "лёгкое", 3: "среднее", 4: "сложное"}[z["slozhnost"]]
        print(f"  {z['klyuch']:>3} | {vid_ru:>15} | {sl:>8} | рёбер {z['reber']:>3} | "
              f"{z['ot']:>4} -> {z['do']:>4} | ответ {z['etalon']}")
    print(f"\nВсего {len(zadaniya)}: файлы в {VYVOD}")
    print(f"Из них сложных (пойдут в подкурс «{HARD_KURS_TITLE}»): "
          f"{sum(1 for z in zadaniya if z['slozhnost'] == 4)}")

    if not apply:
        print("\nВхолостую. Ни файлы в хранилище, ни база не тронуты.")
        return

    from dotenv import load_dotenv

    load_dotenv(dotenv_path=CB_ROOT / ".env", encoding="utf-8-sig")
    sys.path.insert(0, str(CB_ROOT))
    from monolith.external_tasks.media.cas_downloader import store_bytes_to_cas  # noqa: E402

    cas_root = Path(os.environ.get("CAS_MEDIA_ROOT", str(CB_ROOT / "data" / "media_store")))
    print("\n=== ФАЙЛЫ В ХРАНИЛИЩЕ (до записи в базу) ===")
    for z in zadaniya:
        imya = await store_bytes_to_cas(z["put"].read_bytes(), "txt", cas_root)
        if imya != z["sha_ext"]:
            raise RuntimeError(f"{z['klyuch']}: CAS вернул «{imya}», ждали «{z['sha_ext']}».")
        dostupen, kak = _proverit_fajl(z["sha_ext"])
        print(f"  {z['klyuch']:>3} | {kak}")
        if not dostupen:
            raise RuntimeError(f"{z['klyuch']}: файл не читается с боевого эндпоинта — в базу не пишем.")

    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            await conn.execute("SELECT set_config('app.skip_course_parent_order_trigger', 'true', true)")

            osnovnoj = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", KURS_UID)
            if osnovnoj is None:
                raise RuntimeError(f"Курс {KURS_UID} не найден — сперва партия 4.")

            hard_id = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", HARD_KURS_UID)
            if hard_id is None:
                hard_id = await conn.fetchval(
                    "INSERT INTO courses (title, access_level, description, is_required, "
                    "course_uid, is_public_demo) "
                    "VALUES ($1, 'self_guided'::access_level_type, $2, false, $3, false) RETURNING id",
                    HARD_KURS_TITLE,
                    "Задания повышенной сложности из раздела «Задание 23 ЕГЭ по информатике. "
                    "Анализ графов». Блок необязательный.",
                    HARD_KURS_UID,
                )
                await conn.execute(
                    "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
                    "VALUES ($1, $2, $3)",
                    hard_id, HARD_CONTAINER_ID, HARD_ORDER,
                )
                print(f"\nПодкурс сложных создан: id={hard_id}, позиция {HARD_ORDER}")
            else:
                print(f"\nПодкурс сложных уже был: id={hard_id}")

            sozdano = obnovleno = 0
            for z in zadaniya:
                kurs = hard_id if z["slozhnost"] == 4 else osnovnoj
                uroven = "recommended" if z["slozhnost"] == 4 else "required"
                soderzhimoe = {
                    "type": "SA_COM",
                    "title": (
                        f"{'Кратчайший путь' if z['vid'] == 'short' else 'Количество путей'} "
                        f"из вершины {z['ot']} в вершину {z['do']}"
                    ),
                    "stem": uslovie(z["vid"], f"/api/v1/media/{z['sha_ext']}", z["imya"], z["ot"], z["do"]),
                    "course_uid": KURS_UID if kurs == osnovnoj else HARD_KURS_UID,
                    "has_hints": False,
                    "hints_text": [],
                    "hints_video": [],
                    "manual_review_required": False,
                }
                provenans = {
                    "istochnik": "сгенерировано в tsk-740 по формату демоверсии ФИПИ 2027",
                    "vid_voprosa": z["vid"],
                    "reber": z["reber"],
                    "vershin": z["vershin"],
                    "otvet_proveren": "двумя независимыми алгоритмами",
                }
                est = await conn.fetchval("SELECT id FROM tasks WHERE external_uid = $1", z["uid"])
                if est is None:
                    await conn.execute(
                        "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                        "difficulty_id, solution_rules, is_active, requirement_level, "
                        "difficulty_provenance) "
                        "VALUES ($1, 1, $2::jsonb, $3, $4, $5::jsonb, true, $6, $7::jsonb)",
                        z["uid"], json.dumps(soderzhimoe, ensure_ascii=False), kurs,
                        z["slozhnost"], json.dumps(pravila(z["etalon"]), ensure_ascii=False),
                        uroven, json.dumps(provenans, ensure_ascii=False),
                    )
                    sozdano += 1
                else:
                    await conn.execute(
                        "UPDATE tasks SET task_content = $2::jsonb, solution_rules = $3::jsonb, "
                        "course_id = $4, difficulty_id = $5, requirement_level = $6, "
                        "difficulty_provenance = $7::jsonb WHERE id = $1",
                        est, json.dumps(soderzhimoe, ensure_ascii=False),
                        json.dumps(pravila(z["etalon"]), ensure_ascii=False), kurs,
                        z["slozhnost"], uroven, json.dumps(provenans, ensure_ascii=False),
                    )
                    obnovleno += 1
            print(f"Заданий создано {sozdano}, обновлено {obnovleno}")

            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'false', true)")

            # Верификация до коммита.
            proverka = await conn.fetch(
                "SELECT t.external_uid, t.course_id, t.difficulty_id, t.is_active, "
                "t.solution_rules#>>'{short_answer,accepted_answers,0,value}' AS etalon, "
                "t.task_content->>'stem' AS stem "
                "FROM tasks t WHERE t.external_uid = ANY($1::text[])",
                [z["uid"] for z in zadaniya],
            )
            po_uid = {z["uid"]: z for z in zadaniya}
            if len(proverka) != len(zadaniya):
                raise RuntimeError(f"В базе {len(proverka)} заданий из {len(zadaniya)}.")
            for r in proverka:
                z = po_uid[r["external_uid"]]
                if r["etalon"] != z["etalon"]:
                    raise RuntimeError(f"{z['klyuch']}: эталон {r['etalon']} вместо {z['etalon']}.")
                if f"/api/v1/media/{z['sha_ext']}" not in (r["stem"] or ""):
                    raise RuntimeError(f"{z['klyuch']}: в условии нет ссылки на свой файл.")
                nuzhen = hard_id if z["slozhnost"] == 4 else osnovnoj
                if r["course_id"] != nuzhen:
                    raise RuntimeError(f"{z['klyuch']}: курс {r['course_id']} вместо {nuzhen}.")
            # Инвариант tsk-347: HARD живёт только в блоке сложных.
            narushenie = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = $1 AND difficulty_id = 4", osnovnoj
            )
            if narushenie:
                raise RuntimeError(f"{narushenie} HARD-заданий осталось в основном курсе.")
            print("Проверено до коммита: эталоны на месте, файлы привязаны, "
                  "HARD только в блоке сложных.")

        print("\nГотово. Банк заданий 23 записан.")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tsk-740 партия 5: свой банк заданий 23 (графы)")
    parser.add_argument("--apply", action="store_true", help="залить файлы и записать задания")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
