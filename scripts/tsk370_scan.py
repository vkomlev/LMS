# -*- coding: utf-8 -*-
"""tsk-370, шаг 1: сплошной разбор условий активных заданий на два дефекта импорта.

ЗАЧЕМ
На задании 3409 ([[tsk-369]]) вскрылось, что импорт может положить в `task_content.stem`
только преамбулу задачи, потеряв сам вопрос («что найти»), — а преамбулу при этом
вставить дважды подряд. Ученик читает описание входных данных и не понимает, что от него
хотят. Наружу это не всплывает ничем: и текст есть, и правило проверки на месте.

ЧТО ИЩЕТСЯ

A. ОБРЫВ БЕЗ ВОПРОСА — в условии нигде нет постановки задачи: ни глагола требования
   («определите», «найдите», «укажите», «сколько», «в ответе запишите»), ни знака вопроса.
   Дополнительно помечается более мягкий случай: постановка в тексте есть, но не в
   последних предложениях — условие обрывается описанием данных.

B. ДУБЛЬ ФРАГМЕНТА — один и тот же кусок текста длиной от MIN_DUP_LEN символов встречается
   внутри одного stem дважды. Ищется наибольший повтор (суффиксным сравнением по словам),
   отдельно отмечается «подряд» (второе вхождение сразу за первым) — характерная подпись
   импорта, склеившего преамбулу саму с собой.

Ничего не пишет в БД. На выходе JSON для ручного разбора и шага 2.

Запуск:  python scripts/tsk370_scan.py --out <файл.json>
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

# Глаголы и обороты постановки задачи. Ищутся по нормализованному тексту в нижнем регистре.
# Корни даны без окончаний: задания встречаются и на «вы» («определите»), и на «ты»
# («определи», «впиши»), и безлично («требуется найти»).
ASK_RE = re.compile(
    r"(определ|найд|укаж|назов|выбер|выпиш|запиш|впиш|вычисл|посчит|подсчит|"
    r"постро|состав|дополн|заполн|отмет|провер|объясн|опиш|сравн|реши|решен|"
    r"введи|введите|вывед|добав|измен|поставь|созда|сдела|соедин|сопостав|расположи|"
    r"расстав|перечисл|соотнес|проанализир|докаж|обоснуй|привед|преобразуй|переведи|"
    r"перевод|изобраз|нарисуй|разработ|реализуй|напиш|сформулируй|выполн|вставь|исправь|"
    r"расскаж|выясни|раздели|сгруппируй|классифицируй|оцени|прочит|заверши|закончи|"
    r"сколько|каков|какое|какой|какая|какие|чему равн|что нужно|что будет|что выведет|"
    r"что делает|что произойд|что вернёт|что вернет|что означает|что такое|зачем|почему|"
    r"верно ли|правда ли|в ответе|в качестве ответа|ответом|требуется|необходимо найти|"
    r"нужно найти|надо найти|засчитывается|принимается ответ|"
    # формы, которых нет среди корней выше: инфинитив «найти», «замените», «получите»…
    r"найти|замен|преврат|получ|сформир|отсортир|расшифр|зашифр|закодир|раскод|дешифр|"
    r"подбер|подстав|восстанов|наименьш|наибольш|максимальн|минимальн|"
    r"называется одним словом|в поле «ответ»|в поле ответ)",
    re.IGNORECASE,
)

MIN_DUP_LEN = 120  # символов нормализованного текста — ниже порога повтор не считается дефектом
TAIL_SENTENCES = 2  # сколько последних предложений считаются «концовкой» условия
PREFIX_LEN = 150    # длина отпечатка преамбулы для поиска близнецов по партии


def dsn(server: str) -> str:
    """DSN прод-сервера из .mcp.json. Значение не печатаем."""
    for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
        if not candidate.exists():
            continue
        cfg = json.loads(candidate.read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers[server]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://"):
                return arg
    raise RuntimeError(f"не нашёл DSN для {server} в .mcp.json")


def strip_html(s: str) -> str:
    """Текст условия без разметки и мягких переносов.

    Встроенные картинки (`data:image/...;base64,...`) снимаются ПЕРВЫМИ: у нескольких
    заданий такая строка обрезана посередине, тег `<img` остаётся незакрытым, и обычное
    снятие разметки оставляет килобайты base64 в тексте. Внутри них попадаются и «?», и
    любые буквосочетания — детектор постановки задачи начинает срабатывать на мусоре.
    """
    s = re.sub(r"data:image/[^\s\"'>]*", " ", s or "")
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", " ", s)
    s = re.sub(r"(?i)</p>|</div>|</li>", ". ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("­", "").replace("​", "").replace("﻿", "")
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", s).strip()


def sentences(text: str) -> list[str]:
    """Грубое деление на предложения — достаточно для поиска концовки условия."""
    parts = re.split(r"(?<=[.!?:])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def longest_repeat(text: str) -> tuple[str, bool]:
    """Наибольший повторяющийся фрагмент текста и признак «второе вхождение сразу за первым».

    Работает по словам: строятся все позиции слов, для каждой пары одинаковых слов
    наращивается общий префикс. Тексты условий короткие (единицы килобайт), квадратичной
    сложности здесь достаточно, а результат легко объяснить глазами.
    """
    words = text.split()
    n = len(words)
    if n < 8:
        return "", False
    positions: dict[str, list[int]] = {}
    for i, w in enumerate(words):
        positions.setdefault(w, []).append(i)
    best_len = 0
    best: tuple[int, int] = (0, 0)  # (начало первого вхождения, длина в словах)
    best_adjacent = False
    for occ in positions.values():
        if len(occ) < 2:
            continue
        for a_idx in range(len(occ)):
            for b_idx in range(a_idx + 1, len(occ)):
                i, j = occ[a_idx], occ[b_idx]
                if j - i <= best_len:
                    continue
                k = 0
                while j + k < n and words[i + k] == words[j + k] and i + k < j:
                    k += 1
                if k > best_len:
                    best_len = k
                    best = (i, k)
                    best_adjacent = (j == i + k)
    if best_len == 0:
        return "", False
    frag = " ".join(words[best[0]:best[0] + best[1]])
    return frag, best_adjacent


def analyse(stem_html: str) -> dict:
    """Признаки дефекта по одному условию."""
    text = strip_html(stem_html)
    # Служебная шапка «Файл к заданию: …» и «Задание NN_xxx (источник)» — не часть условия.
    body = re.sub(r"^\s*Файл к заданию:.*?(?=Задание|\b[А-ЯЁ])", "", text, count=1)
    sents = sentences(body)
    has_ask = bool(ASK_RE.search(body)) or "?" in body
    tail = " ".join(sents[-TAIL_SENTENCES:]) if sents else ""
    ask_in_tail = bool(ASK_RE.search(tail)) or "?" in tail
    frag, adjacent = longest_repeat(body)
    return {
        "prefix": re.sub(r"\d+", "#", body[:PREFIX_LEN].lower()),
        "text_len": len(body),
        "has_ask": has_ask,
        "ask_in_tail": ask_in_tail,
        "tail": tail[-300:],
        "dup_len": len(frag) if len(frag) >= MIN_DUP_LEN else 0,
        "dup_adjacent": adjacent if len(frag) >= MIN_DUP_LEN else False,
        "dup_fragment": frag[:400] if len(frag) >= MIN_DUP_LEN else "",
    }


def find_twins(items: list[dict]) -> list[dict]:
    """Задания, у которых преамбула та же, что у соседей по партии, а постановки в конце нет.

    Задачи одного типа ЕГЭ приходят пачками с дословно одинаковой преамбулой (различие —
    в приложенном файле и числах). Если у части пачки условие кончается вопросом, а у
    одного-двух — описанием данных, это прямой след потери вопроса при импорте:
    сравнивать есть с чем, и «так задумано» тут не объясняет.
    """
    groups: dict[str, list[dict]] = {}
    for it in items:
        if it["text_len"] >= 80:  # совсем короткие условия сравнивать не с чем
            groups.setdefault(it["prefix"], []).append(it)
    out: list[dict] = []
    for prefix, grp in groups.items():
        if len(grp) < 2:
            continue
        full = [g for g in grp if g["ask_in_tail"]]
        broken = [g for g in grp if not g["ask_in_tail"]]
        if not full or not broken:
            continue
        median_len = sorted(g["text_len"] for g in full)[len(full) // 2]
        for b in broken:
            out.append({
                **{k: b[k] for k in ("id", "external_uid", "type", "source_kind",
                                     "source_task_id", "text_len", "tail")},
                "twin_ids": [g["id"] for g in full][:5],
                "twin_median_len": median_len,
                "shorter_by": median_len - b["text_len"],
            })
    return sorted(out, key=lambda x: -x["shorter_by"])


async def main(out: Path) -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(
            "SELECT id, external_uid, course_id, "
            "       task_content->>'type'        AS type, "
            "       task_content->>'stem'        AS stem, "
            "       task_content->>'source_kind' AS source_kind, "
            "       task_content->>'source_task_id' AS source_task_id, "
            "       task_content->>'course_uid'  AS course_uid, "
            "       solution_rules "
            "FROM tasks WHERE is_active = true ORDER BY id"
        )
    finally:
        await conn.close()

    no_ask: list[dict] = []
    tail_only: list[dict] = []
    dups: list[dict] = []
    items: list[dict] = []
    for r in rows:
        a = analyse(r["stem"] or "")
        item = {
            "id": r["id"],
            "external_uid": r["external_uid"],
            "course_id": r["course_id"],
            "course_uid": r["course_uid"],
            "type": r["type"],
            "source_kind": r["source_kind"],
            "source_task_id": r["source_task_id"],
            **a,
        }
        items.append(item)
        if not a["has_ask"]:
            no_ask.append(item)
        elif not a["ask_in_tail"]:
            tail_only.append(item)
        if a["dup_len"]:
            dups.append(item)

    twins = find_twins(items)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"scanned": len(rows), "no_ask": no_ask, "tail_only": tail_only,
         "dups": dups, "twins": twins},
        ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"Разобрано активных заданий: {len(rows)}")
    print(f"A1. Нет постановки вообще:            {len(no_ask)}")
    print(f"A2. Постановка есть, но не в конце:   {len(tail_only)}")
    print(f"B.  Дубль фрагмента >= {MIN_DUP_LEN} симв.:    {len(dups)} "
          f"(из них подряд: {sum(1 for d in dups if d['dup_adjacent'])})")
    print(f"C.  Короче близнецов с той же преамбулой: {len(twins)}")
    print(f"Выгрузка: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    asyncio.run(main(args.out))
