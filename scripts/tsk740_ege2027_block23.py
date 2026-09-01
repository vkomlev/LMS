# -*- coding: utf-8 -*-
"""tsk-740, партия 4: новый блок «Задание 23» (графы) по демоверсии ЕГЭ-2027.

ЗАЧЕМ
С 2027 года задание 23 — новая тема: «Умение решать алгоритмические задачи,
связанные с анализом графов (задачи построения оптимального пути между вершинами
графа, определения количества различных путей между вершинами ориентированного
ациклического графа)». Уровень П, 1 балл, 12 минут, требуется программирование.
Прежнее содержимое позиции 23 уехало на позицию 13 (партия 1), место пустует.

ЧТО ДЕЛАЕТ СКРИПТ
1. Кладёт файл-приложение демоверсии (`demo_23.txt`, 200 рёбер) в CAS и прод-S3
   штатным помощником ContentBackbone `store_bytes_to_cas`, затем проверяет
   ЖИВЫМ HTTP-запросом к боевому media-эндпоинту, что файл читается. Порядок
   именно такой: сначала файл в хранилище и проверка, потом запись в БД —
   обратный порядок оставил бы в условии ссылку в никуда (урок tsk-369/384/526).
2. Создаёт курс «Задание 23 ЕГЭ по информатике. Анализ графов» подкурсом 112 на
   освободившуюся позицию 22.
3. Заводит первое задание банка — дословно из демоверсии ФИПИ, с эталоном.

ПОЧЕМУ ТОЛЬКО ОДНО ЗАДАНИЕ
Разведка 2026-09-01 показала: ни один из наших источников ещё не выпустил банк
заданий нового 23 — демоверсия вышла 28.08, прошло четыре дня.
- kpolyakov.spb.ru: новость о проекте демоверсии есть, но материалы прежние
  («13: IP-адреса и маски», «23: перебор вариантов, динамическое программирование»);
- inf-ege.sdamgia.ru: заголовок «ЕГЭ–2026», поиск по «ациклического»/«кратчайшего
  пути» — ноль совпадений;
- kompege.ru и education.yandex.ru: отдают SPA-каркас без JS, признаков 2027 нет.
Смежное, что есть у нас: курс 1154 «Задание 9. Пути в ориентированном графе» (ОГЭ,
36 заданий) и 1054 «Моделирование на графах» (10) — там нет весов рёбер и нет
файла, поэтому как замена они не годятся, только как разогрев.
Источники надо перепроверить после 30.09, когда ФИПИ закроет обсуждение проекта.

ЧЕМ ПРОВЕРЕН ЭТАЛОН (два независимых признака, § 9 плейбука)
- Дейкстра по приложенному файлу ФИПИ даёт 10971.
- Тот же код на «типовом примере» из условия даёт 7 — ровно то число, которое
  ФИПИ называет верным ответом для примера.
(В PDF демоверсии колонки таблицы ответов схлопнулись, и «10971» стоит в строке
задания 1; собственное решение снимает эту неоднозначность.)

Запуск: вхолостую по умолчанию;
  DBCHECK_OK=1 python scripts/tsk740_ege2027_block23.py
  DBCHECK_OK=1 python scripts/tsk740_ege2027_block23.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import heapq
import json
import os
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

ROOT_COURSE_ID = 112
NOVYJ_ORDER = 22
KURS_UID = "lms:tsk740:ege2027:23"
KURS_TITLE = "Задание 23 ЕГЭ по информатике. Анализ графов"
KURS_OPISANIE = (
    "Новая тема задания 23 с 2027 года: оптимальный путь между вершинами графа и "
    "количество различных путей в ориентированном ациклическом графе. Задание "
    "решается программой по данным из приложенного файла."
)

ZADANIE_UID = "fipi:demo2027:ege:inf:23"
ZADANIE_TITLE = "Кратчайший путь во взвешенном ациклическом графе (демоверсия ФИПИ 2027)"
ETALON = "10971"

# Путь к файлу-приложению из официального архива ФИПИ (см. reviews-отчёт партии).
FAJL = Path(
    os.environ.get("TSK740_DEMO23")
    or r"C:\Users\user\AppData\Local\Temp\claude\D--Work-LMS"
       r"\51e3b3bb-4573-43eb-a32b-b6b5e9c1bd2b\scratchpad\fipi2027\x\_demo_23.txt"
)

PRIMER = """100 12 1.0
6 7 7.0
6 1 1.0
1 7 5.5
7 100 2.0
4 100 8.0
1 100 12.0
1 4 2.5"""


def _kratchajshij_put(tekst: str, ot: int = 1, do: int = 100) -> float | None:
    """Дейкстра по описанию графа «L M W» в строках. Нужен для проверки эталона."""
    graf: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for stroka in tekst.splitlines():
        chasti = stroka.split()
        if len(chasti) < 3:
            continue
        graf[int(chasti[0])].append((int(chasti[1]), float(chasti[2])))
    rasstoyanie = {ot: 0.0}
    ochered: list[tuple[float, int]] = [(0.0, ot)]
    while ochered:
        d, u = heapq.heappop(ochered)
        if d > rasstoyanie.get(u, float("inf")):
            continue
        for v, w in graf[u]:
            nd = d + w
            if nd < rasstoyanie.get(v, float("inf")):
                rasstoyanie[v] = nd
                heapq.heappush(ochered, (nd, v))
    return rasstoyanie.get(do)


def _stem(ssylka_na_fajl: str) -> str:
    """Условие дословно по демоверсии ФИПИ 2027 + ссылка на приложенный файл."""
    return (
        f'<p>Файл к заданию: <a href="{ssylka_na_fajl}" target="_blank" '
        'rel="noopener noreferrer">demo_23.txt</a></p>\n'
        "<p><em>Задание выполняется с использованием прилагаемого файла.</em></p>\n"
        "<p>В текстовом файле содержится описание ациклического ориентированного "
        "взвешенного графа. В каждой строке файла записаны два натуральных числа "
        "(L, M) и одно положительное вещественное число (W). L и M — номера вершин "
        "графа, W — вес ребра, ведущего из вершины L в вершину M. Таким образом, "
        "количество строк в файле равно количеству рёбер в графе. Две вершины графа "
        "не могут быть соединены более чем одним ребром.</p>\n"
        "<p>Найдите и запишите в ответе целую часть длины кратчайшего пути из вершины "
        "с номером 1 в вершину с номером 100. Существование хотя бы одного такого пути "
        "гарантируется. Под длиной кратчайшего пути понимается минимальная сумма весов "
        "рёбер, составляющих путь.</p>\n"
        "<p>Для выполнения этого задания следует написать программу.</p>\n"
        # Знаки сравнения — символами Unicode, не LaTeX: голый \\le отрендерился бы
        # сырым (проверено живьём), а «<»/«>» санитайзер принял бы за начало тега
        # (§ 6.3 плейбука, класс D).
        "<p>Вершины графа могут быть пронумерованы не подряд. L ≤ 1000, M ≤ 1000; "
        "W ≤ 10 000. Количество строк в файле не превосходит 200. Числа в строках "
        "разделены произвольным ненулевым количеством пробелов и/или табуляций.</p>\n"
        "<p>Типовой пример организации данных во входном файле:</p>\n"
        f"<pre>{PRIMER}</pre>\n"
        "<p>Для приведённого примера верным ответом будет 7.</p>\n"
        "<p>Типовой пример имеет иллюстративный характер. Для выполнения задания "
        "используйте данные из прилагаемого файла.</p>"
    )


PRAVILA = {
    "max_score": 1,
    "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
    "auto_check": True,
    "text_answer": None,
    "scoring_mode": "all_or_nothing",
    "short_answer": {
        "regex": None,
        "use_regex": False,
        "normalization": ["trim", "lower", "collapse_spaces"],
        "accepted_answers": [{"score": 1, "value": ETALON}],
    },
    "partial_rules": [],
    "correct_options": [],
    "custom_scoring_config": None,
    "manual_review_required": False,
}


def _proverit_fajl(sha_ext: str) -> tuple[bool, str]:
    """Живой HTTP к боевому media-эндпоинту: 200 или 307 (редирект на S3) — норма."""
    req = urllib.request.Request(MEDIA_URL.format(sha_ext), headers={"User-Agent": "tsk740/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status == 200, (
                f"HTTP {resp.status}, Content-Type={resp.headers.get('Content-Type')}, "
                f"Content-Length={resp.headers.get('Content-Length')}"
            )
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return False, f"сеть: {exc}"


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
    dannye = FAJL.read_text(encoding="utf-8")
    sha = hashlib.sha256(FAJL.read_bytes()).hexdigest()
    sha_ext = f"{sha}.txt"

    # Гейт эталона: и боевой файл, и типовой пример из условия.
    po_fajlu = _kratchajshij_put(dannye)
    po_primeru = _kratchajshij_put(PRIMER)
    print("=== ПРОВЕРКА ЭТАЛОНА ===")
    print(f"Кратчайший путь по файлу ФИПИ : {po_fajlu} -> целая часть {int(po_fajlu)}")
    print(f"Кратчайший путь по примеру    : {po_primeru} -> целая часть {int(po_primeru)} "
          "(ФИПИ называет 7)")
    if int(po_primeru) != 7:
        raise RuntimeError("Решатель не воспроизводит пример из условия — эталону верить нельзя.")
    if str(int(po_fajlu)) != ETALON:
        raise RuntimeError(f"Эталон {ETALON} не совпал с решением {int(po_fajlu)}.")
    print(f"Эталон подтверждён: {ETALON}\n")

    print("=== ПЛАН ===")
    print(f"1. Файл {FAJL.name} ({FAJL.stat().st_size} байт, sha256 {sha[:16]}…) -> CAS + прод-S3")
    print(f"2. Курс «{KURS_TITLE}» (uid {KURS_UID}) -> подкурс {ROOT_COURSE_ID}, позиция {NOVYJ_ORDER}")
    print(f"3. Задание «{ZADANIE_TITLE}» (uid {ZADANIE_UID}), SA_COM, эталон {ETALON}")

    if not apply:
        print("\nВхолостую. Ни файл, ни база не тронуты.")
        return

    # Шаг 1: файл в хранилище и живая проверка ДО записи в базу.
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=CB_ROOT / ".env", encoding="utf-8-sig")
    sys.path.insert(0, str(CB_ROOT))
    from monolith.external_tasks.media.cas_downloader import store_bytes_to_cas  # noqa: E402

    cas_root = Path(os.environ.get("CAS_MEDIA_ROOT", str(CB_ROOT / "data" / "media_store")))
    imya = await store_bytes_to_cas(FAJL.read_bytes(), "txt", cas_root)
    if imya != sha_ext:
        raise RuntimeError(f"CAS вернул «{imya}», ожидалось «{sha_ext}».")
    dostupen, kak = _proverit_fajl(sha_ext)
    print(f"\nФайл на боевом эндпоинте: {kak}")
    if not dostupen:
        raise RuntimeError("Файл не читается с боевого эндпоинта — в базу не пишем.")

    ssylka = f"/api/v1/media/{sha_ext}"

    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            await conn.execute("SELECT set_config('app.skip_course_parent_order_trigger', 'true', true)")

            kurs_id = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", KURS_UID)
            if kurs_id is None:
                kurs_id = await conn.fetchval(
                    "INSERT INTO courses (title, access_level, description, is_required, "
                    "course_uid, is_public_demo) "
                    "VALUES ($1, 'self_guided'::access_level_type, $2, false, $3, false) RETURNING id",
                    KURS_TITLE, KURS_OPISANIE, KURS_UID,
                )
                await conn.execute(
                    "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
                    "VALUES ($1, $2, $3)",
                    kurs_id, ROOT_COURSE_ID, NOVYJ_ORDER,
                )
                print(f"Курс создан: id={kurs_id}, позиция {NOVYJ_ORDER}")
            else:
                print(f"Курс уже был: id={kurs_id}")

            soderzhimoe = {
                "type": "SA_COM",
                "title": ZADANIE_TITLE,
                "stem": _stem(ssylka),
                "course_uid": KURS_UID,
                "has_hints": False,
                "hints_text": [],
                "hints_video": [],
                "manual_review_required": False,
            }
            zadanie_id = await conn.fetchval(
                "SELECT id FROM tasks WHERE external_uid = $1", ZADANIE_UID
            )
            if zadanie_id is None:
                zadanie_id = await conn.fetchval(
                    "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                    "difficulty_id, solution_rules, is_active, requirement_level) "
                    "VALUES ($1, 1, $2::jsonb, $3, 3, $4::jsonb, true, 'required') RETURNING id",
                    ZADANIE_UID, json.dumps(soderzhimoe, ensure_ascii=False),
                    kurs_id, json.dumps(PRAVILA, ensure_ascii=False),
                )
                print(f"Задание создано: id={zadanie_id}")
            else:
                await conn.execute(
                    "UPDATE tasks SET task_content = $2::jsonb, solution_rules = $3::jsonb, "
                    "course_id = $4 WHERE id = $1",
                    zadanie_id, json.dumps(soderzhimoe, ensure_ascii=False),
                    json.dumps(PRAVILA, ensure_ascii=False), kurs_id,
                )
                print(f"Задание обновлено: id={zadanie_id}")

            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'false', true)")

            # Верификация до коммита.
            proverka = await conn.fetchrow(
                "SELECT t.id, t.course_id, t.is_active, t.requirement_level, "
                "t.solution_rules#>>'{short_answer,accepted_answers,0,value}' AS etalon, "
                "t.task_content->>'stem' AS stem, cp.order_number "
                "FROM tasks t JOIN course_parents cp ON cp.course_id = t.course_id "
                "WHERE t.id = $1 AND cp.parent_course_id = $2",
                zadanie_id, ROOT_COURSE_ID,
            )
            if proverka is None:
                raise RuntimeError("Задание не привязалось к дереву курса 112.")
            if proverka["etalon"] != ETALON:
                raise RuntimeError(f"Эталон записался неверно: {proverka['etalon']}")
            if ssylka not in (proverka["stem"] or ""):
                raise RuntimeError("В условии нет ссылки на файл-приложение.")
            if proverka["order_number"] != NOVYJ_ORDER:
                raise RuntimeError(f"Позиция курса {proverka['order_number']}, ждали {NOVYJ_ORDER}.")
            print(f"Проверено до коммита: задание {zadanie_id} в курсе {proverka['course_id']} "
                  f"на позиции {proverka['order_number']}, эталон {proverka['etalon']}, "
                  "ссылка на файл в условии есть.")

        print("\nГотово. Блок 23 заведён.")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tsk-740 партия 4: блок задания 23 (графы)")
    parser.add_argument("--apply", action="store_true", help="записать изменения")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
