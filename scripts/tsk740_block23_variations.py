# -*- coding: utf-8 -*-
"""tsk-740, партия 12 (П2): лист «Вариации вопроса» блока 23 и 10 заданий к нему.

ЗАЧЕМ
Банк блока задаёт только два голых вопроса — «кратчайший путь A → B» и «сколько путей
A → B». Разбор Полякова (22.09.2026, kpolyakov.spb.ru, «23: алгоритмы на графах»)
показал вариации, которые ждут на экзамене: путь через вершину, в обход вершин,
только по «длинным» рёбрам, не больше K рёбер, сам путь. Решение оператора 30.09 —
сделать урок и задания на них СВОИМ генератором; задачи Полякова не переносим.

ЧЕМ ПРОВЕРЕН КАЖДЫЙ ОТВЕТ
Два независимых решателя на каждый вид, ответ принимается только при совпадении:
  A — рекурсия с запоминанием, ровно тот метод, которому учит урок;
  B — другой алгоритм: Дейкстра по состояниям «вершина + пройдена ли N»,
      Беллман-Форд по отфильтрованным рёбрам, послойная динамика по числу рёбер,
      Дейкстра с восстановлением пути, топологическая динамика с флагом.
Гейты сверх этого (скрипт падает и ничего не пишет):
  1. решатели воспроизводят ФИПИ (10971 и 7 — гейт генератора партии 5);
  2. на типовом примере ФИПИ дают числа, названные в уроке (10.5, 10.5, 12, 12, 7, 1, 2);
  3. на файле демоверсии дают ответы Полякова (16058, 11782, 16869, 4673, 23814, 20198) —
     его ответы перепроверены 30.09 третьим способом, это внешняя опора;
  4. вариация обязана МЕНЯТЬ ответ по сравнению с голым вопросом — иначе задание не
     проверяет приём; путь для «суммы вершин» обязан быть единственным;
  5. дробная часть ответа не ближе 1e-6 к целому — порядок сложения не сдвинет int().

Генерация детерминирована (зерно = номер задания), файлы идемпотентны по sha256.
Все задания — EASY/NORMAL: инвариант tsk-347 (HARD только в блоке 1378) не задет.

Запуск: вхолостую по умолчанию;
  DBCHECK_OK=1 python scripts/tsk740_block23_variations.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import heapq
import json
import logging
import math
import os
import random
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk740_gen23_graphs import (  # noqa: E402
    CB_ROOT, PREDEL_STROK, PREDEL_VERSHINY, PREDEL_VES, PRIMER_FIPI, _dsn, _proverit_fajl,
    _razobrat, bellman_ford, dejkstra, gejt_reshatelej, postroit, putej_topologicheski, uslovie,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

project_root = Path(__file__).resolve().parents[1]
VYVOD = project_root / "reviews" / "tsk740-var23"
DEMO = project_root / "reviews" / "2026-09-01-tsk740-fipi-demo23.txt"
UROK_HTML = Path(__file__).resolve().parent / "tsk740_block23_lessons" / "m_var.html"

GLAVA_ID = 1490
KURS_UID = "lms:tsk740:ege2027:23:var"
KURS_TITLE = "Вариации вопроса: через вершину, в обход, с ограничениями"
KURS_DESCR = ("Как меняется программа, когда в задании 23 добавляют условие: путь через "
              "вершину, в обход вершин, только по рёбрам нужной длины, не больше K рёбер, "
              "сам путь. Один шаблон — рекурсия с запоминанием — и три места для правки.")
UROK_UID = "lms:tsk740:m23:8"
UROK_TITLE = "Вариации вопроса: куда вписать ограничение"
PREDEL_PUTEJ = 10**9
INF = float("inf")
EPS = 1e-6

CHTO_SDAVAT = ("<p><b>Что сдавать.</b> В поле «Ответ» — только само число. В поле "
               "«Комментарий» — свою программу или ход решения: без комментария (или "
               "приложенного файла) ответ не засчитывается.</p>")

# (ключ, вид, сложность, рёбер, вершин, подсказка)
NABOR: list[tuple[str, str, int, int, int, str]] = [
    ("v1", "via", 2, 60, 32,
     "Путь через вершину складывается из двух участков: от старта до неё и от неё до финиша. "
     "Функцию переписывать не нужно — её достаточно вызвать дважды и сложить результаты."),
    ("v2", "via", 3, 110, 50,
     "Проверьте себя: путь через вершину не может быть короче обычного кратчайшего пути. "
     "Если у вас получилось меньше — участки сложены не те или перепутан порядок вершин в вызове."),
    ("v3", "avoid1", 2, 60, 32,
     "Запрещённую вершину проще всего выбросить сразу при чтении файла: пропускайте каждую "
     "строку, где она стоит в начале или в конце ребра. Дальше программа та же."),
    ("v4", "avoid2", 3, 110, 50,
     "Две запрещённые вершины удобно держать в множестве и проверять строку файла одним "
     "условием «ни начало, ни конец ребра не входят в множество»."),
    ("v5", "minw", 3, 110, 50,
     "Ограничение на длину ребра проверяется там же, где перебираются соседи: неподходящее "
     "ребро просто пропускается. Сравнивайте с порогом вещественное число, а не округлённое."),
    ("v6", "maxk", 3, 100, 46,
     "Функции нужен третий параметр — сколько рёбер ещё можно пройти. При каждом шаге он "
     "уменьшается на единицу, а при нуле, если финиш не достигнут, путь считается невозможным."),
    ("v7", "maxk", 3, 120, 55,
     "Если ответ совпал с обычным кратчайшим путём, скорее всего ограничение не сработало: "
     "проверьте, что счётчик рёбер передаётся в рекурсивный вызов уменьшенным, а не прежним."),
    ("v8", "pathsum", 3, 100, 46,
     "Пусть функция возвращает не одно число, а пару: длину и сам путь кортежем вершин. "
     "Сумму считайте только по промежуточным вершинам — без старта и финиша."),
    ("v9", "cvia", 2, 60, 30,
     "Количество путей через вершину — это произведение: сколько путей от старта до неё "
     "умножить на сколько путей от неё до финиша. Каждый путь первого участка продолжается "
     "каждым путём второго."),
    ("v10", "cavoid", 3, 100, 44,
     "Выбросьте при чтении файла все рёбра, которые входят в запрещённую вершину или выходят "
     "из неё, и посчитайте пути обычной функцией. Ответ обязан получиться меньше, чем без запрета."),
]


# ------------------------------------------------ решатель A: рекурсия (метод урока)

def graf_iz(rebra, zapret=frozenset(), min_ves: float | None = None) -> dict:
    """Словарь смежности; запрещённые вершины и лёгкие рёбра отбрасываются при чтении."""
    g: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for l, m, w in rebra:
        if l in zapret or m in zapret:
            continue
        if min_ves is not None and w < min_ves:
            continue
        g[l].append((m, w))
    return g


def rek_short(g, ot: int, do: int) -> float:
    """Кратчайший путь рекурсией с запоминанием (шаблон урока, min)."""
    @lru_cache(maxsize=None)
    def luchshij(v: int) -> float:
        if v == do:
            return 0.0
        return min((w + luchshij(u) for u, w in g.get(v, [])), default=INF)
    return luchshij(ot)


def rek_short_k(g, ot: int, do: int, k: int) -> float:
    """Кратчайший путь не более чем из k рёбер: третий параметр — сколько осталось."""
    @lru_cache(maxsize=None)
    def luchshij(v: int, ostalos: int) -> float:
        if v == do:
            return 0.0
        if ostalos == 0:
            return INF
        return min((w + luchshij(u, ostalos - 1) for u, w in g.get(v, [])), default=INF)
    return luchshij(ot, k)


def rek_short_put(g, ot: int, do: int) -> tuple[float, tuple[int, ...]]:
    """Кратчайший путь вместе с вершинами (пара: длина, кортеж вершин)."""
    @lru_cache(maxsize=None)
    def luchshij(v: int) -> tuple[float, tuple[int, ...]]:
        if v == do:
            return 0.0, (v,)
        otvet: tuple[float, tuple[int, ...]] = (INF, ())
        for u, w in g.get(v, []):
            dlina, put = luchshij(u)
            if w + dlina < otvet[0]:
                otvet = (w + dlina, (v,) + put)
        return otvet
    return luchshij(ot)


def rek_count(g, ot: int, do: int) -> int:
    """Количество путей рекурсией с запоминанием (тот же шаблон, sum)."""
    @lru_cache(maxsize=None)
    def putej(v: int) -> int:
        if v == do:
            return 1
        return sum(putej(u) for u, _ in g.get(v, []))
    return putej(ot)


# ------------------------------------------------ решатель B: независимые алгоритмы

def b_via(rebra, ot: int, do: int, n: int) -> float:
    """Через вершину n: Дейкстра по состояниям (вершина, пройдена ли n)."""
    g = defaultdict(list)
    for l, m, w in rebra:
        g[l].append((m, w))
    start = (ot, ot == n)
    dist = {start: 0.0}
    q = [(0.0, start)]
    while q:
        d, (v, f) = heapq.heappop(q)
        if d > dist.get((v, f), INF):
            continue
        for u, w in g[v]:
            s = (u, f or u == n)
            if d + w < dist.get(s, INF):
                dist[s] = d + w
                heapq.heappush(q, (d + w, s))
    return dist.get((do, True), INF)


def b_filtr_bf(rebra, ot: int, do: int, zapret=frozenset(), min_ves: float | None = None) -> float:
    """В обход вершин / по тяжёлым рёбрам: Беллман-Форд по отфильтрованному списку."""
    ost = [(l, m, w) for l, m, w in rebra
           if l not in zapret and m not in zapret and (min_ves is None or w >= min_ves)]
    if not any(m == do for _, m, _ in ost) or not any(l == ot for l, _, _ in ost):
        return INF
    r = bellman_ford(ost, ot, do)
    return INF if r is None else r


def b_sloi(rebra, ot: int, do: int, k: int) -> float:
    """Не более k рёбер: послойная динамика, каждый слой — ровно на одно ребро больше."""
    tek = {ot: 0.0}
    luchshee = 0.0 if ot == do else INF
    for _ in range(k):
        nov: dict[int, float] = {}
        for l, m, w in rebra:
            if l in tek and tek[l] + w < nov.get(m, INF):
                nov[m] = tek[l] + w
        tek = nov
        luchshee = min(luchshee, tek.get(do, INF))
    return luchshee


def b_put(rebra, ot: int, do: int) -> tuple[float, tuple[int, ...], int]:
    """Путь: Дейкстра с запоминанием предыдущей вершины + число кратчайших путей."""
    g = defaultdict(list)
    for l, m, w in rebra:
        g[l].append((m, w))
    dist, prev, q = {ot: 0.0}, {}, [(0.0, ot)]
    while q:
        d, v = heapq.heappop(q)
        if d > dist.get(v, INF):
            continue
        for u, w in g[v]:
            if d + w < dist.get(u, INF):
                dist[u], prev[u] = d + w, v
                heapq.heappush(q, (d + w, u))
    put, v = [do], do
    while v != ot:
        v = prev[v]
        put.append(v)
    # Сколько путей имеют ту же минимальную длину — по рёбрам «на кратчайшем».
    na_kratch = [(l, m) for l, m, w in rebra
                 if l in dist and m in dist and abs(dist[l] + w - dist[m]) < EPS]
    skolko: dict[int, int] = defaultdict(int)
    skolko[ot] = 1
    for v in sorted(dist, key=dist.get):
        for l, m in na_kratch:
            if l == v:
                skolko[m] += skolko[v]
    return dist[do], tuple(put[::-1]), skolko[do]


def b_cvia(rebra, ot: int, do: int, n: int) -> int:
    """Пути через n: топологическая динамика по состояниям (вершина, пройдена ли n)."""
    vse = {v for l, m, _ in rebra for v in (l, m)}
    vhod = {v: 0 for v in vse}
    g = defaultdict(list)
    for l, m, _ in rebra:
        g[l].append(m)
        vhod[m] += 1
    och = [v for v in vse if vhod[v] == 0]
    poryadok = []
    while och:
        v = och.pop()
        poryadok.append(v)
        for u in g[v]:
            vhod[u] -= 1
            if vhod[u] == 0:
                och.append(u)
    cnt = defaultdict(int)
    cnt[(ot, ot == n)] = 1
    for v in poryadok:
        for f in (False, True):
            c = cnt[(v, f)]
            if c:
                for u in g[v]:
                    cnt[(u, f or u == n)] += c
    return cnt[(do, True)]


# ------------------------------------------------ задания

def dostizhimo(g, ot: int) -> set[int]:
    """Вершины, достижимые из ot."""
    vidno, stek = {ot}, [ot]
    while stek:
        v = stek.pop()
        for u, _ in g.get(v, []):
            if u not in vidno:
                vidno.add(u)
                stek.append(u)
    return vidno


def sverit(a: float, b: float, chto: str) -> float:
    """Два решателя обязаны сойтись; дробная часть не у самого целого."""
    if a == INF or b == INF or abs(a - b) > EPS:
        raise RuntimeError(f"{chto}: решатели разошлись ({a} против {b})")
    if abs(a - round(a)) < EPS:
        raise ValueError("ответ почти целый — int() зависит от порядка сложения")
    return a


def podobrat(vid: str, rebra, ot: int, do: int, rnd: random.Random) -> tuple[str, int, str]:
    """Параметр вариации, ответ и текст вопроса. ValueError — граф не подошёл."""
    g = graf_iz(rebra)
    golyj = rek_short(g, ot, do)
    _, put0, _ = b_put(rebra, ot, do)
    vnutr = list(put0[1:-1])

    if vid == "via":
        iz = dostizhimo(g, ot)
        obr = defaultdict(list)
        for l, m, w in rebra:
            obr[m].append((l, w))
        k_do = dostizhimo(obr, do)
        kand = sorted(v for v in iz & k_do if v not in put0)
        rnd.shuffle(kand)
        for n in kand:
            a = rek_short(g, ot, n) + rek_short(g, n, do)
            otv = sverit(a, b_via(rebra, ot, do, n), "via")
            if int(otv) != int(golyj):
                return (f"через вершину {n}", int(otv),
                        f"Найдите и запишите в ответе целую часть длины кратчайшего пути из "
                        f"вершины с номером {ot} в вершину с номером {do}, проходящего через "
                        f"вершину с номером {n}. Существование хотя бы одного такого пути "
                        "гарантируется.")
        raise ValueError("нет подходящей вершины для «через»")

    if vid in ("avoid1", "avoid2"):
        rnd.shuffle(vnutr)
        for s1 in vnutr:
            zapret = {s1}
            if vid == "avoid2":
                _, put1, _ = b_put([r for r in rebra if s1 not in r[:2]], ot, do) \
                    if do in dostizhimo(graf_iz(rebra, {s1}), ot) else (0, (), 0)
                if len(put1) < 3:
                    continue
                zapret = {s1, rnd.choice(put1[1:-1])}
            gz = graf_iz(rebra, frozenset(zapret))
            if do not in dostizhimo(gz, ot):
                continue
            otv = sverit(rek_short(gz, ot, do), b_filtr_bf(rebra, ot, do, frozenset(zapret)), vid)
            if int(otv) == int(golyj):
                continue
            nomera = sorted(zapret)
            chem = (f"не проходящего через вершину с номером {nomera[0]}" if len(nomera) == 1 else
                    f"не проходящего через вершины с номерами {nomera[0]} и {nomera[1]}")
            zagolovok = (f"в обход вершины {nomera[0]}" if len(nomera) == 1 else
                         f"в обход вершин {nomera[0]} и {nomera[1]}")
            return (zagolovok, int(otv),
                    f"Найдите и запишите в ответе целую часть длины кратчайшего пути из вершины "
                    f"с номером {ot} в вершину с номером {do}, {chem}. Существование хотя бы "
                    "одного такого пути гарантируется.")
        raise ValueError("нет подходящих запретных вершин")

    if vid == "minw":
        for porog in range(100, int(PREDEL_VES / 12), 50):
            gz = graf_iz(rebra, min_ves=porog)
            if do not in dostizhimo(gz, ot):
                break
            otv = rek_short(gz, ot, do)
            if int(otv) == int(golyj):
                continue
            try:
                otv = sverit(otv, b_filtr_bf(rebra, ot, do, min_ves=porog), "minw")
            except ValueError:
                continue
            return (f"только рёбра весом от {porog}", int(otv),
                    f"Найдите и запишите в ответе целую часть длины кратчайшего пути из вершины "
                    f"с номером {ot} в вершину с номером {do}, составленного только из рёбер, "
                    f"вес каждого из которых не меньше {porog}. Существование хотя бы одного "
                    "такого пути гарантируется.")
        raise ValueError("нет подходящего порога веса")

    if vid == "maxk":
        rebr0 = len(put0) - 1
        for k in range(rebr0 - 1, 0, -1):
            a = rek_short_k(g, ot, do, k)
            if a == INF:
                break
            if int(a) == int(golyj):
                continue
            otv = sverit(a, b_sloi(rebra, ot, do, k), "maxk")
            return (f"не более {k} рёбер", int(otv),
                    f"Найдите и запишите в ответе целую часть длины кратчайшего пути из вершины "
                    f"с номером {ot} в вершину с номером {do}, состоящего не более чем из {k} "
                    "рёбер. Существование хотя бы одного такого пути гарантируется.")
        raise ValueError("нет подходящего ограничения числа рёбер")

    if vid == "pathsum":
        dl_a, put_a = rek_short_put(g, ot, do)
        dl_b, put_b, skolko = b_put(rebra, ot, do)
        sverit(dl_a, dl_b, "pathsum")
        if put_a != put_b:
            raise RuntimeError(f"pathsum: пути разошлись {put_a} / {put_b}")
        if skolko != 1 or len(put_a) < 4:
            raise ValueError("кратчайший путь не единственный или слишком короткий")
        return ("сумма вершин пути", sum(put_a[1:-1]),
                f"Найдите кратчайший путь из вершины с номером {ot} в вершину с номером {do} "
                "и запишите в ответе сумму номеров всех его промежуточных вершин (кроме "
                f"{ot} и {do}). Гарантируется, что кратчайший путь единственный.")

    vse_puti = rek_count(g, ot, do)
    if vid == "cvia":
        kand = sorted({v for l, m, _ in rebra for v in (l, m)} - {ot, do})
        rnd.shuffle(kand)
        for n in kand:
            a = rek_count(g, ot, n) * rek_count(g, n, do)
            if not 0 < a < vse_puti or vse_puti < 10:
                continue
            if a != b_cvia(rebra, ot, do, n):
                raise RuntimeError("cvia: решатели разошлись")
            return (f"через вершину {n}", a,
                    f"Найдите и запишите в ответе количество различных путей из вершины "
                    f"с номером {ot} в вершину с номером {do}, проходящих через вершину "
                    f"с номером {n}. Пути считаются различными, если они отличаются хотя бы "
                    "одним ребром.")
        raise ValueError("нет подходящей вершины для «через» в подсчёте путей")

    if vid == "cavoid":
        kand = sorted({v for l, m, _ in rebra for v in (l, m)} - {ot, do})
        rnd.shuffle(kand)
        for s in kand:
            gz = graf_iz(rebra, frozenset({s}))
            a = rek_count(gz, ot, do)
            if not 0 < a < vse_puti or vse_puti - a < 3:
                continue
            ost = [r for r in rebra if s not in r[:2]]
            if a != putej_topologicheski(ost, ot, do):
                raise RuntimeError("cavoid: решатели разошлись")
            return (f"в обход вершины {s}", a,
                    f"Найдите и запишите в ответе количество различных путей из вершины "
                    f"с номером {ot} в вершину с номером {do}, не проходящих через вершину "
                    f"с номером {s}. Пути считаются различными, если они отличаются хотя бы "
                    "одним ребром.")
        raise ValueError("нет подходящей вершины для «в обход» в подсчёте путей")

    raise KeyError(vid)


def uslovie_var(vid: str, ssylka: str, imya: str, ot: int, do: int, vopros: str) -> str:
    """Условие по образцу ФИПИ: общий текст генератора партии 5, наш вопрос вместо своего."""
    baza_vid = "count" if vid.startswith("c") else "short"
    tekst = uslovie(baza_vid, ssylka, imya, ot, do)
    nachalo = tekst.index("<p>Найдите")
    konec = tekst.index("</p>", nachalo) + len("</p>")
    return tekst[:nachalo] + f"<p>{vopros}</p>" + tekst[konec:] + "\n" + CHTO_SDAVAT


def pravila(etalon: str) -> dict:
    """Правила проверки — те же, что у соседних заданий блока после tsk-187."""
    return {
        "max_score": 1,
        "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
        "auto_check": True,
        "text_answer": None,
        "scoring_mode": "all_or_nothing",
        "short_answer": {
            "regex": None, "use_regex": False,
            "normalization": ["trim", "lower", "collapse_spaces", "strip_brackets_commas"],
            "accepted_answers": [{"score": 1, "value": etalon}],
        },
        "partial_rules": [],
        "correct_options": [],
        "custom_scoring_config": None,
        "manual_review_required": False,
    }


# ------------------------------------------------ гейты

def gejt_primer_i_polyakov() -> None:
    """Числа урока на примере ФИПИ и ответы Полякова на файле демоверсии."""
    r = _razobrat(PRIMER_FIPI)
    g = graf_iz(r)
    primer = {
        "через 4": rek_short(g, 1, 4) + rek_short(g, 4, 100),
        "через 4 (B)": b_via(r, 1, 100, 4),
        "в обход 7": rek_short(graf_iz(r, frozenset({7})), 1, 100),
        "рёбра >= 3": rek_short(graf_iz(r, min_ves=3), 1, 100),
        "не более 1 ребра": rek_short_k(g, 1, 100, 1),
        "сумма вершин пути": float(sum(rek_short_put(g, 1, 100)[1][1:-1])),
        "путей через 4": float(rek_count(g, 1, 4) * rek_count(g, 4, 100)),
        "путей в обход 7": float(rek_count(graf_iz(r, frozenset({7})), 1, 100)),
    }
    zhdem = {"через 4": 10.5, "через 4 (B)": 10.5, "в обход 7": 10.5, "рёбра >= 3": 12.0,
             "не более 1 ребра": 12.0, "сумма вершин пути": 7.0, "путей через 4": 1.0,
             "путей в обход 7": 2.0}
    log.info("=== ГЕЙТ: пример ФИПИ (числа из урока) ===")
    for k, v in primer.items():
        log.info("  %-18s %s (в уроке %s)", k, v, zhdem[k])
        if abs(v - zhdem[k]) > EPS:
            raise RuntimeError(f"Пример ФИПИ, «{k}»: {v} вместо {zhdem[k]} — урок разойдётся с кодом.")

    d = _razobrat(DEMO.read_text(encoding="utf-8"))
    gd = graf_iz(d)
    polyakov = {
        "через 4": (int(rek_short(gd, 1, 4) + rek_short(gd, 4, 100)), int(b_via(d, 1, 100, 4)), 16058),
        "в обход 633": (int(rek_short(graf_iz(d, frozenset({633})), 1, 100)),
                        int(b_filtr_bf(d, 1, 100, frozenset({633}))), 11782),
        "в обход 173, 633": (int(rek_short(graf_iz(d, frozenset({173, 633})), 1, 100)),
                             int(b_filtr_bf(d, 1, 100, frozenset({173, 633}))), 16869),
        "сумма вершин пути": (sum(rek_short_put(gd, 1, 100)[1][1:-1]),
                              sum(b_put(d, 1, 100)[1][1:-1]), 4673),
        "рёбра >= 1000": (int(rek_short(graf_iz(d, min_ves=1000), 1, 100)),
                          int(b_filtr_bf(d, 1, 100, min_ves=1000)), 23814),
        "не более 6 рёбер": (int(rek_short_k(gd, 1, 100, 6)), int(b_sloi(d, 1, 100, 6)), 20198),
    }
    log.info("=== ГЕЙТ: демоверсия, ответы Полякова ===")
    for k, (a, b, p) in polyakov.items():
        log.info("  %-18s A=%s B=%s Поляков=%s", k, a, b, p)
        if not a == b == p:
            raise RuntimeError(f"Демоверсия, «{k}»: A={a}, B={b}, Поляков {p} — не сошлись.")
    log.info("Все решатели сошлись между собой, с уроком и с Поляковым.\n")


def postroit_zadaniya() -> list[dict]:
    """Сгенерировать 10 заданий; каждое — свой граф и свой проверенный ответ."""
    VYVOD.mkdir(parents=True, exist_ok=True)
    zadaniya = []
    for nomer, (klyuch, vid, slozhnost, reber, vershin, podskazka) in enumerate(NABOR, start=1):
        for popytka in range(200):
            seed = 750_000 + nomer * 1000 + popytka
            tekst, ot, do = postroit(seed=seed, reber=reber, vershin=vershin)
            rebra = _razobrat(tekst)
            if len(rebra) > PREDEL_STROK or max(max(l, m) for l, m, _ in rebra) > PREDEL_VERSHINY \
                    or max(w for *_, w in rebra) > PREDEL_VES:
                raise RuntimeError(f"{klyuch}: граф вышел за пределы формата ЕГЭ.")
            if vid.startswith("c") and rek_count(graf_iz(rebra), ot, do) > PREDEL_PUTEJ:
                continue
            try:
                parametr, otvet, vopros = podobrat(vid, rebra, ot, do, random.Random(seed))
            except ValueError:
                continue
            break
        else:
            raise RuntimeError(f"{klyuch}: за 200 попыток не нашёлся подходящий граф.")
        imya = f"23_{klyuch}.txt"
        put = VYVOD / imya
        put.write_text(tekst, encoding="utf-8", newline="\n")
        sha = hashlib.sha256(put.read_bytes()).hexdigest()
        zadaniya.append({
            "klyuch": klyuch, "vid": vid, "slozhnost": slozhnost, "reber": len(rebra),
            "ot": ot, "do": do, "parametr": parametr, "etalon": str(otvet), "vopros": vopros,
            "podskazka": podskazka, "imya": imya, "put": put, "sha_ext": f"{sha}.txt",
            "uid": f"lms:tsk740:var23:{klyuch}", "seed": seed,
        })
    return zadaniya


def proverit_podskazki(zadaniya: list[dict]) -> None:
    """Эталон не встречается в подсказке; одинаковых подсказок нет (правило партии 9)."""
    if len({z["podskazka"] for z in zadaniya}) != len(zadaniya):
        raise RuntimeError("Есть одинаковые подсказки внутри листа.")
    for z in zadaniya:
        if z["etalon"] in z["podskazka"]:
            raise RuntimeError(f"{z['klyuch']}: эталон виден в подсказке.")


# ------------------------------------------------ запись

async def zapisat(zadaniya: list[dict]) -> None:
    """Файлы в хранилище, затем курс, урок и задания одной транзакцией с проверкой."""
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=CB_ROOT / ".env", encoding="utf-8-sig")
    sys.path.insert(0, str(CB_ROOT))
    from monolith.external_tasks.media.cas_downloader import store_bytes_to_cas  # noqa: E402

    cas_root = Path(os.environ.get("CAS_MEDIA_ROOT", str(CB_ROOT / "data" / "media_store")))
    log.info("=== ФАЙЛЫ В ХРАНИЛИЩЕ (до записи в базу) ===")
    for z in zadaniya:
        imya = await store_bytes_to_cas(z["put"].read_bytes(), "txt", cas_root)
        if imya != z["sha_ext"]:
            raise RuntimeError(f"{z['klyuch']}: CAS вернул «{imya}», ждали «{z['sha_ext']}».")
        dostupen, kak = _proverit_fajl(z["sha_ext"])
        log.info("  %4s | %s", z["klyuch"], kak)
        if not dostupen:
            raise RuntimeError(f"{z['klyuch']}: файл не читается с боевого адреса — в базу не пишем.")

    urok = UROK_HTML.read_text(encoding="utf-8")
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.audit_actor', 'tsk-740 партия 12', true)")
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")

            kurs = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", KURS_UID)
            if kurs is None:
                kurs = await conn.fetchval(
                    "INSERT INTO courses (title, access_level, description, is_required, "
                    "course_uid, is_public_demo) VALUES ($1, 'self_guided'::access_level_type, "
                    "$2, false, $3, false) RETURNING id",
                    KURS_TITLE, KURS_DESCR, KURS_UID)
                # Без номера — триггер поставит последним в главе, соседей не сдвинет.
                await conn.execute(
                    "INSERT INTO course_parents (course_id, parent_course_id) VALUES ($1, $2)",
                    kurs, GLAVA_ID)
                log.info("Лист создан: id=%s", kurs)
            else:
                await conn.execute("UPDATE courses SET title = $2, description = $3 WHERE id = $1",
                                   kurs, KURS_TITLE, KURS_DESCR)
                log.info("Лист уже был: id=%s", kurs)

            mat = await conn.fetchval("SELECT id FROM materials WHERE external_uid = $1", UROK_UID)
            soderzhimoe = json.dumps({"text": urok, "format": "html"}, ensure_ascii=False)
            if mat is None:
                await conn.execute(
                    "INSERT INTO materials (course_id, type, content, order_position, title, "
                    "is_active, external_uid, requirement_level) "
                    "VALUES ($1, 'text', $2::jsonb, 1, $3, true, $4, 'required')",
                    kurs, soderzhimoe, UROK_TITLE, UROK_UID)
            else:
                await conn.execute(
                    "UPDATE materials SET content = $2::jsonb, title = $3 WHERE id = $1",
                    mat, soderzhimoe, UROK_TITLE)

            for z in zadaniya:
                tc = {
                    "type": "SA_COM",
                    "title": ("Кратчайший путь, " if not z["vid"].startswith("c")
                              else "Количество путей, ") + z["parametr"],
                    "stem": uslovie_var(z["vid"], f"/api/v1/media/{z['sha_ext']}", z["imya"],
                                        z["ot"], z["do"], z["vopros"]),
                    "course_uid": KURS_UID,
                    "has_hints": True,
                    "hints_text": [z["podskazka"]],
                    "hints_video": [],
                    "manual_review_required": False,
                }
                prov = {"istochnik": "сгенерировано в tsk-740 (партия 12, вариации)",
                        "vid_voprosa": z["vid"], "parametr": z["parametr"], "reber": z["reber"],
                        "otvet_proveren": "рекурсией с запоминанием и независимым алгоритмом"}
                est = await conn.fetchval("SELECT id FROM tasks WHERE external_uid = $1", z["uid"])
                args = (json.dumps(tc, ensure_ascii=False), kurs, z["slozhnost"],
                        json.dumps(pravila(z["etalon"]), ensure_ascii=False),
                        json.dumps(prov, ensure_ascii=False))
                if est is None:
                    await conn.execute(
                        "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                        "difficulty_id, solution_rules, is_active, requirement_level, "
                        "difficulty_provenance) VALUES ($1, 1, $2::jsonb, $3, $4, $5::jsonb, "
                        "true, 'required', $6::jsonb)", z["uid"], *args)
                else:
                    await conn.execute(
                        "UPDATE tasks SET task_content = $2::jsonb, course_id = $3, "
                        "difficulty_id = $4, solution_rules = $5::jsonb, "
                        "difficulty_provenance = $6::jsonb WHERE id = $1", est, *args)

            # Порядок заданий листа — по номеру в наборе (v1..v10).
            for poz, z in enumerate(zadaniya, start=1):
                await conn.execute("UPDATE tasks SET order_position = $2 WHERE external_uid = $1",
                                   z["uid"], poz)
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'false', true)")

            # --- верификация до коммита
            svyaz = await conn.fetchrow(
                "SELECT parent_course_id, order_number FROM course_parents WHERE course_id = $1", kurs)
            deti = await conn.fetch(
                "SELECT course_id, order_number FROM course_parents WHERE parent_course_id = $1 "
                "ORDER BY order_number", GLAVA_ID)
            if svyaz["parent_course_id"] != GLAVA_ID or [d["course_id"] for d in deti][-1] != kurs:
                raise RuntimeError(f"Лист встал не последним в главе: {[tuple(d) for d in deti]}")
            if [d["order_number"] for d in deti] != list(range(1, len(deti) + 1)):
                raise RuntimeError(f"Порядок листов главы с дырой: {[tuple(d) for d in deti]}")
            proverka = await conn.fetch(
                "SELECT external_uid, course_id, difficulty_id, is_active, order_position, "
                "solution_rules#>>'{short_answer,accepted_answers,0,value}' AS etalon, "
                "task_content->>'stem' AS stem FROM tasks WHERE course_id = $1", kurs)
            po_uid = {z["uid"]: z for z in zadaniya}
            if len(proverka) != len(zadaniya):
                raise RuntimeError(f"В листе {len(proverka)} заданий вместо {len(zadaniya)}.")
            for r in proverka:
                z = po_uid[r["external_uid"]]
                if r["etalon"] != z["etalon"] or f"/api/v1/media/{z['sha_ext']}" not in r["stem"]:
                    raise RuntimeError(f"{z['klyuch']}: эталон или ссылка на файл не на месте.")
                if r["difficulty_id"] == 4 or not r["is_active"]:
                    raise RuntimeError(f"{z['klyuch']}: HARD или неактивно.")
            mats = await conn.fetchval(
                "SELECT count(*) FROM materials WHERE course_id = $1 AND is_active", kurs)
            if mats + len(proverka) > 20:
                raise RuntimeError("Лист больше 20 сущностей.")
            log.info("Проверено до коммита: лист %s последним в главе, урок %s, заданий %s, "
                     "эталоны и файлы на месте.", kurs, mats, len(proverka))
        log.info("Готово: лист вариаций записан.")
    finally:
        await conn.close()


async def main(apply: bool) -> None:
    gejt_reshatelej()
    gejt_primer_i_polyakov()
    zadaniya = postroit_zadaniya()
    proverit_podskazki(zadaniya)
    log.info("=== СГЕНЕРИРОВАНО ===")
    for z in zadaniya:
        log.info("  %4s | %-6s | сл.%s | рёбер %3s | %4s -> %4s | %-28s | ответ %s",
                 z["klyuch"], z["vid"], z["slozhnost"], z["reber"], z["ot"], z["do"],
                 z["parametr"], z["etalon"])
    if not apply:
        log.info("\nВхолостую: ни хранилище, ни база не тронуты. Файлы — %s", VYVOD)
        return
    if not UROK_HTML.exists():
        raise RuntimeError(f"Нет текста урока {UROK_HTML}.")
    await zapisat(zadaniya)


if __name__ == "__main__":
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="tsk-740 П2: лист вариаций задания 23")
    parser.add_argument("--apply", action="store_true", help="залить файлы и записать в боевую базу")
    asyncio.run(main(parser.parse_args().apply))
