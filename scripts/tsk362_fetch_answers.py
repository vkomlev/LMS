# -*- coding: utf-8 -*-
"""tsk-362, шаг 2: забрать верные ответы из открытых источников и сверить их с условием.

ЧТО ДЕЛАЕТ
Для каждого задания из шага 1 (`tsk362_collect_sources.py`), у которого определён
источник и числовой ID, забирает верный ответ **с сайта-источника** и сверяет, что по
этому ID лежит именно та задача, что в LMS:

  * `kompege`  — `GET https://kompege.ru/api/v1/task/<id>` → поля `key` (ответ) и `text`;
  * `sdamgia`  — `GET https://inf-ege.sdamgia.ru/problem?id=<id>` → `div.answer` («Ответ: X»)
                 и блок `prob_maindiv` (условие);
  * `polyakov` — `GET https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=<id>`
                 → `div.hidedata` со скриптом `changeImageFilePath('X')` (ответ).

`yandex` здесь не обрабатывается: его ответы берутся авторизованным API из браузера
(метод [[tsk-100]]/[[tsk-361]]), это отдельный шаг.

СВЕРКА (главный гейт, без неё ответ не записывается)
ID из поста Telegram — сильный ключ, но пост мог быть про «аналог задания», а разметка
условия в LMS переписана. Поэтому по каждой паре сверяются два независимых признака:
  1. **дословный фрагмент** 60 букв из середины условия LMS есть в тексте источника
     (сравниваются только буквы: KaTeX в LMS дублирует формулу — и отрисованную, и
     LaTeX-исходник, поэтому посимвольно тексты не совпадают никогда);
  2. **значимые числа источника** (3+ цифр) все присутствуют в условии LMS — именно они
     различают задачи одного типа, у которых преамбула общая дословно.
Совпало и то, и другое → `verdict = "match"`. Что-то одно → `"weak"`. Ничего → `"mismatch"`.
Записывать в БД можно только `match`.

Ничего не пишет в БД: на выходе JSON для шага 3.

Запуск:  python scripts/tsk362_fetch_answers.py --items <items.json> --out <answers.json>
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0 Safari/537.36"
TIMEOUT = 25
PAUSE_SEC = 0.7  # вежливая пауза между запросами к одному источнику


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
    for enc in ("utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    # sdamgia расставляет мягкие переносы внутри слов («иг­ро­ка») — без их
    # снятия текст источника не сравнится ни с чем.
    s = s.replace("­", "").replace("​", "").replace("﻿", "")
    return re.sub(r"\s+", " ", s).strip()


def numbers(s):
    """Все числа условия множеством: KaTeX в LMS дублирует формулу (отрисованную и
    LaTeX-исходник), поэтому порядок и разбиение токенов не совпадают, а состав — да."""
    return set(re.findall(r"\d+", s or ""))


def prose(s):
    """Только буквы в нижнем регистре — текстовая часть без формул и разметки."""
    s = (s or "").lower().replace("ё", "е")
    return re.sub(r"[^а-яa-z]+", " ", s).strip()


def middle_slice(s, size=60):
    """Дословный фрагмент из середины условия — отпечаток именно этой задачи."""
    if len(s) <= size:
        return s
    start = max(0, len(s) // 2 - size // 2)
    return s[start:start + size]



def get_kompege(task_id: str) -> tuple[str | None, str]:
    data = json.loads(fetch(f"https://kompege.ru/api/v1/task/{task_id}"))
    answer = (data.get("key") or "").strip()
    text = strip_html(data.get("text") or "")
    for sub in data.get("subTask") or []:
        text += " " + strip_html(sub.get("text") or "")
    return (answer or None), text


def get_sdamgia(task_id: str) -> tuple[str | None, str]:
    h = fetch(f"https://inf-ege.sdamgia.ru/problem?id={task_id}")
    answer = None
    m = re.search(r'<div class="answer"[^>]*>(.{0,300}?)</div>', h, re.S)
    if m:
        a = strip_html(m.group(1))
        m2 = re.search(r"Ответ:?\s*(.+)", a)
        if m2:
            answer = m2.group(1).strip().rstrip(".")
    # Захват начинается ПОСЛЕ закрывающей скобки тега: внутри самого тега сидит
    # id="maindiv973140", и его число попадало в «значимые числа условия», ломая сверку.
    m = re.search(r'class="prob_maindiv"[^>]*>(.{0,60000}?)<div class="answer"', h, re.S)
    text = strip_html(m.group(1)) if m else strip_html(h)
    # Внутри блока идёт ещё и разбор с числами самого решения — если его не отрезать,
    # «значимые числа источника» пополняются тем, чего в условии нет и быть не может.
    cut = re.search(r"\bРешение\b", text)
    if cut and cut.start() > 100:
        text = text[:cut.start()]
    return answer, text


def get_polyakov(task_id: str) -> tuple[str | None, str]:
    h = fetch(f"https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId={task_id}")
    answer = None
    m = re.search(
        r'<div class="hidedata" id="%s">\s*<script>\s*document\.write\(\s*changeImageFilePath\(\s*\'(.*?)\'\s*\)'
        % re.escape(task_id), h, re.S)
    if m:
        answer = strip_html(m.group(1)).strip()
    # Условие лежит в ячейке class="topicview", но НЕ как разметка: оно передано
    # аргументом в document.write(changeImageFilePath('…')). Снимать теги здесь нельзя —
    # strip_html вырезает <script> целиком и оставляет пустую строку.
    m = re.search(r'(?s)class="topicview"[^>]*>(.*?)<td class="answer"', h)
    block = m.group(1) if m else h
    chunks = re.findall(r"changeImageFilePath\(\s*'(.*?)'\s*\)", block, re.S)
    text = strip_html(" ".join(chunks)) if chunks else strip_html(block)
    return answer, text


GETTERS = {"kompege": get_kompege, "sdamgia": get_sdamgia, "polyakov": get_polyakov}


def verdict_for(lms_stem: str, src_text: str) -> tuple[str, dict]:
    """Два независимых признака, оба обязательны для вердикта «match».

    Текст — потому что по ID можно уехать на другую задачу. Числа — потому что задачи
    одного типа делят преамбулу дословно («Значение арифметического выражения …»), и
    один текст их не различает (обжиг [[tsk-354]]/[[tsk-316]]): различает именно
    начинка — основание системы счисления, пороги, размеры.
    """
    lms_p, src_p = prose(lms_stem), prose(src_text)
    frag = middle_slice(lms_p)
    prose_ok = bool(frag) and frag in src_p

    # У sdamgia в том же блоке идёт хвост разбора с числами решения (включая сам ответ).
    # Условие всегда в начале, поэтому числа берём только из головы текста, соразмерной
    # условию в LMS; иначе «непришедшие» числа решения ломают сверку у каждой второй задачи.
    head_len = max(500, int(len(strip_html(lms_stem)) * 1.4))
    src_head = src_text[:head_len]

    lms_n, src_n = numbers(lms_stem), numbers(src_head)
    # Значимые числа берём со стороны ИСТОЧНИКА: его разметка чистая, а в LMS то же
    # число может быть разорвано KaTeX'ем — но полный набор чисел LMS их содержит.
    key_src = {n for n in src_n if len(n) >= 3}
    missing = sorted(key_src - lms_n)
    nums_ok = (len(missing) == 0) if key_src else (bool(lms_n & src_n) or not src_n)

    detail = {"fragment": frag[:80], "prose_ok": prose_ok,
              "key_src_numbers": sorted(key_src)[:15], "missing_in_lms": missing[:10],
              "nums_ok": nums_ok}
    if prose_ok and nums_ok:
        return "match", detail
    if prose_ok or nums_ok:
        return "weak", detail
    return "mismatch", detail


def main(items_path: Path, out_path: Path, only: str | None) -> None:
    items = json.loads(items_path.read_text(encoding="utf-8"))
    results = []
    stats = {"match": 0, "weak": 0, "mismatch": 0, "no_answer": 0, "error": 0, "skipped": 0}

    for it in items:
        src, sid = it.get("source"), it.get("source_id")
        if src not in GETTERS or not sid or not re.fullmatch(r"\d+", str(sid)):
            stats["skipped"] += 1
            continue
        if only and src != only:
            stats["skipped"] += 1
            continue
        rec = {"id": it["id"], "course_id": it["course_id"], "source": src, "source_id": sid,
               "via": it["via"], "max_score": it["max_score"]}
        try:
            answer, text = GETTERS[src](str(sid))
            time.sleep(PAUSE_SEC)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as exc:
            rec.update({"verdict": "error", "error": f"{type(exc).__name__}: {exc}"})
            stats["error"] += 1
            results.append(rec)
            print(f"  [error ] id={it['id']} {src}:{sid} — {exc}")
            continue

        verdict, detail = verdict_for(it["stem"], text)
        # Полный текст источника сохраняем, чтобы сверку можно было пересчитать
        # офлайн, не дёргая сайты заново.
        rec.update({"answer": answer, "verdict": verdict, "detail": detail,
                    "src_text": text[:6000]})
        if not answer:
            rec["verdict"] = "no_answer"
        stats[rec["verdict"]] = stats.get(rec["verdict"], 0) + 1
        results.append(rec)
        print(f"  [{rec['verdict']:8}] id={it['id']} {src}:{sid} → {str(answer)[:40]!r}")

    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nИтого: {stats}")
    print(f"Сохранено: {out_path}")


def reverify(items_path: Path, files: list[str]) -> None:
    """Пересчитать вердикты по уже сохранённым текстам источника — без обращения к сайтам.

    Нужно, когда меняется правило сверки: перезагружать 150 страниц ради этого незачем,
    полный текст источника сохранён в файлах шага 2.
    """
    items = {i["id"]: i for i in json.loads(items_path.read_text(encoding="utf-8"))}
    for f in files:
        path = Path(f)
        recs = json.loads(path.read_text(encoding="utf-8"))
        stats = {}
        for rec in recs:
            if not rec.get("src_text"):
                stats[rec.get("verdict", "?")] = stats.get(rec.get("verdict", "?"), 0) + 1
                continue
            verdict, detail = verdict_for(items[rec["id"]]["stem"], rec["src_text"])
            if not rec.get("answer"):
                verdict = "no_answer"
            rec["verdict"], rec["detail"] = verdict, detail
            stats[verdict] = stats.get(verdict, 0) + 1
        path.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{path.name}: {stats}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True)
    ap.add_argument("--out")
    ap.add_argument("--only", help="ограничить одним источником (kompege/sdamgia/polyakov)")
    ap.add_argument("--reverify", nargs="+", help="пересчитать вердикты в готовых файлах")
    args = ap.parse_args()
    if args.reverify:
        reverify(Path(args.items), args.reverify)
    else:
        main(Path(args.items), Path(args.out), args.only)
