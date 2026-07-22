# -*- coding: utf-8 -*-
"""tsk-373, шаг 1: разделить расхождения «ответ LMS ≠ ключ kompege» на два класса.

ЗАЧЕМ
Из 768 активных заданий со ссылкой на kompege у части ответ в LMS не совпадает с полем
`key` задачи источника по тому же ID. Симптом один, причины две и лечатся они
противоположно:

  * **та же задача** — текст условия LMS совпадает с условием источника, значит ID верен,
    а ответ в LMS чужой (как у 3177 в [[tsk-369]]: опечатка в номере принесла ответ другой
    задачи). Ученик получает «неверно» за верное решение → править надо ОТВЕТ;
  * **другая задача** — по этому ID у источника лежит совсем другое условие (пример: 3241,
    шапка обещает задание 16 про F(n), источник отдаёт задание 3 про базу данных). Условие
    и ответ в LMS при этом свои и, скорее всего, верные → править надо ПРИВЯЗКУ, а ответ
    не трогать.

Обжиг [[tsk-369]]: гейт по одному признаку («ответы совпали») привязал бы чужой файл. Здесь
зеркально — совпадает текст, расходится ответ. Поэтому решение принимается ПАРОЙ признаков:
дословный фрагмент условия + значимые числа.

ЧТО СЧИТАЕТСЯ
  * `lcs` — длина наибольшего общего дословного фрагмента (нормализованные символы);
  * `jaccard` — доля общих 4-словных сочетаний (устойчива к перестановкам абзацев);
  * `numbers_*` — совпадение значимых чисел условия (для задач одного типа с одинаковой
    преамбулой числа — единственное, чем задачи различаются);
  * `number_match` — номер задания ЕГЭ из шапки условия против `number` источника;
  * `answer_loose` — совпадают ли ответы с точностью до разделителей и порядка
    (у kompege варианты склеены через `&`, в LMS — пробелом или запятой): такие пары
    расхождением не считаются вовсе.

Ничего не пишет в БД. На выходе JSON с вердиктом по каждому заданию.

Запуск:  python scripts/tsk373_classify.py --cache <кэш.json> --out <файл.json>
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk370_scan import dsn, strip_html  # noqa: E402
from tsk370_verify_source import kompege_id, norm  # noqa: E402

# Пороги вердикта. Подобраны по разбросу на всей выборке 768 заданий (см. --stats):
# у пар «та же задача» дословный фрагмент идёт сотнями символов, у «другой задачи» —
# десятками (общие обороты «В ответе запишите число»).
SAME_LCS = 120
SAME_JACCARD = 0.45
DIFF_LCS = 60
DIFF_JACCARD = 0.15
SHINGLE = 4

HEADER_RE = re.compile(r"Задание\s+(\d+)[_ ](\d+)k?\b")


def strip_latex(text: str) -> str:
    """Формулы без разметки TeX: у источника условие набрано `\\( F(n) \\geq 10 \\)`.

    В LMS та же формула лежит обычным текстом. Без снятия команд `\\geq`, `\\lt`, `\\(`
    дословный фрагмент рвётся на куски и одна и та же задача выглядит чужой — так
    задание 3282 (та же функция F(n), тот же ответ) получило вердикт «другая задача».
    """
    t = re.sub(r"\\[a-zA-Z]+", " ", text or "")
    t = re.sub(r"[\\$(){}\[\]]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def body_text(stem: str) -> str:
    """Условие без служебной шапки: «Файл к заданию: …» и «Задание NN_id (КЕГЭ)»."""
    t = strip_html(stem or "")
    t = re.sub(r"^\s*Файл к заданию:.*?(?=Задание|\b[А-ЯЁ])", "", t, count=1)
    t = HEADER_RE.sub(" ", t)
    t = re.sub(r"\(КЕГЭ\)|\(ЕГЭ\)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def words(text: str) -> list[str]:
    """Слова нормализованного текста — основа для сочетаний."""
    t = strip_html(text).lower().replace("ё", "е")
    return re.findall(r"[0-9a-zа-я]+", t)


def jaccard(a: list[str], b: list[str], k: int = SHINGLE) -> float:
    """Доля общих k-словных сочетаний."""
    sa = {tuple(a[i:i + k]) for i in range(max(0, len(a) - k + 1))}
    sb = {tuple(b[i:i + k]) for i in range(max(0, len(b) - k + 1))}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def lcs_len(a: str, b: str) -> int:
    """Длина наибольшего общего дословного фрагмента нормализованных текстов."""
    if not a or not b:
        return 0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(
        0, len(a), 0, len(b)).size


def numbers(text: str) -> set[str]:
    """Значимые числа условия: от двух знаков — одиночные цифры шумят на «задание 3»."""
    return {n for n in re.findall(r"\d+", strip_html(text)) if len(n) >= 2}


def ans_tokens(s: str | None) -> list[str]:
    """Ответ как список значений: разделители не считаются частью значения.

    У kompege многострочный ответ хранится с ДВУХСИМВОЛЬНОЙ последовательностью `\\n`
    (обратная косая + буква n), а не переводом строки: в поле `key` задачи 21425 лежит
    `167990 73043\\n122627 29105`. Если этого не учесть, буква `n` прилипает к
    следующему числу (`n122627`), и ответ, совпадающий с LMS до последней цифры,
    выглядит расхождением — так все 31 многострочных ответа попали в «расхождения».
    """
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"\\+[nrt]", " ", s)
    return re.findall(r"[0-9a-zа-я]+", s)


def answers_loose_equal(a: str | None, b: str | None) -> bool:
    """Равны ли ответы с точностью до разделителей и порядка значений."""
    ta, tb = ans_tokens(a), ans_tokens(b)
    if not ta or not tb:
        return False
    return ta == tb or sorted(ta) == sorted(tb) or "".join(ta) == "".join(tb)


def classify(lms_stem: str, src_text: str) -> dict:
    """Признаки «та же задача / другая задача» по паре условий."""
    lms_body = strip_latex(body_text(lms_stem))
    src_body = strip_latex(strip_html(src_text or ""))
    ln, sn = norm(lms_body), norm(src_body)
    lw, sw = words(lms_body), words(src_body)
    lnum, snum = numbers(lms_body), numbers(src_body)
    common = lnum & snum
    lcs = lcs_len(ln, sn)
    jac = jaccard(lw, sw)
    if (lcs >= SAME_LCS or jac >= SAME_JACCARD) and (
            not snum or len(common) / len(snum) >= 0.5):
        verdict = "same"
    elif lcs < DIFF_LCS and jac < DIFF_JACCARD:
        verdict = "different"
    else:
        verdict = "ambiguous"
    return {
        "verdict": verdict,
        "lcs": lcs,
        "jaccard": round(jac, 3),
        "lms_len": len(lms_body),
        "src_len": len(src_body),
        "numbers_common": sorted(common),
        "numbers_lms_only": sorted(lnum - snum)[:10],
        "numbers_src_only": sorted(snum - lnum)[:10],
        "numbers_src_cover": round(len(common) / len(snum), 2) if snum else None,
        "lms_head": lms_body[:220],
        "src_head": src_body[:220],
    }


async def main(cache_path: Path, out: Path) -> None:
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(
            "SELECT id, external_uid, course_id, "
            "       task_content->>'stem' AS stem, "
            "       task_content->>'source_kind' AS source_kind, "
            "       task_content->>'source_task_id' AS source_task_id, "
            "       solution_rules "
            "FROM tasks WHERE is_active = true ORDER BY id")
    finally:
        await conn.close()

    mismatch, loose_ok, no_answer, missing_src, stats = [], [], [], [], []
    for r in rows:
        tid = kompege_id(r["external_uid"], r["source_kind"],
                         r["source_task_id"], r["stem"])
        if not tid:
            continue
        src = cache.get(tid)
        if not src or src.get("error") or not src.get("text"):
            missing_src.append({"id": r["id"], "kompege_id": tid})
            continue
        sr = json.loads(r["solution_rules"] or "{}")
        accepted = (sr.get("short_answer") or {}).get("accepted_answers") or []
        values = [a.get("value") for a in accepted if a.get("value") is not None]
        lms_ans = values[0] if values else None
        src_ans = src.get("key")
        feats = classify(r["stem"], src["text"])
        same_ans = any(answers_loose_equal(v, src_ans) for v in values)
        m0 = HEADER_RE.search(strip_html(r["stem"] or ""))
        stats.append({"id": r["id"], "kompege_id": tid, "lcs": feats["lcs"],
                      "jaccard": feats["jaccard"], "verdict": feats["verdict"],
                      "same_answer": same_ans, "has_answer": bool(values),
                      "answer_lms": lms_ans, "answer_src": src_ans,
                      "header_number": m0.group(1) if m0 else None,
                      "src_number": src.get("number"),
                      "lms_head": feats["lms_head"], "src_head": feats["src_head"],
                      "numbers_src_cover": feats["numbers_src_cover"]})
        if not values:
            no_answer.append({"id": r["id"], "kompege_id": tid, "src": src_ans})
            continue
        if any(answers_loose_equal(v, src_ans) for v in values):
            if str(lms_ans).strip().lower() != str(src_ans or "").strip().lower():
                loose_ok.append({"id": r["id"], "kompege_id": tid,
                                 "lms": lms_ans, "src": src_ans})
            continue
        m = HEADER_RE.search(strip_html(r["stem"] or ""))
        mismatch.append({
            "id": r["id"], "external_uid": r["external_uid"],
            "course_id": r["course_id"], "kompege_id": tid,
            "answer_lms": lms_ans, "answer_lms_all": values, "answer_src": src_ans,
            "header_number": m.group(1) if m else None,
            "header_id": m.group(2) if m else None,
            "src_number": src.get("number"),
            "number_match": (m.group(1) == str(src.get("number"))) if m else None,
            "src_files": len(json.loads(src.get("files") or "[]")) if isinstance(
                src.get("files"), str) else len(src.get("files") or []),
            **classify(r["stem"], src["text"]),
        })

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "checked": len(stats), "mismatch": mismatch, "loose_ok": loose_ok,
        "no_answer": no_answer, "missing_src": missing_src, "stats": stats,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    by = {}
    for m in mismatch:
        by[m["verdict"]] = by.get(m["verdict"], 0) + 1
    # Перекрёстная таблица «текст × ответ» — главный разрез задачи. Опасна не только
    # клетка «текст тот же, ответ другой», но и зеркальная: «текст ДРУГОЙ, ответ совпал»
    # — там ответ пришёл от чужой задачи по ошибочному ID (случай 3177 из [[tsk-369]]).
    cross: dict[tuple, int] = {}
    for s in stats:
        if not s["has_answer"]:
            continue
        cross[(s["verdict"], s["same_answer"])] = cross.get(
            (s["verdict"], s["same_answer"]), 0) + 1
    print("текст × ответ (все задания с ответом):")
    for (v, a), n in sorted(cross.items()):
        print(f"  текст={v:<9} ответ_совпал={str(a):<5} {n}")
    print(f"сверено заданий kompege: {len(stats)}")
    print(f"ответ разошёлся строго, но совпал по сути (разделители/порядок): {len(loose_ok)}")
    print(f"ответа в LMS нет вовсе (не наш класс): {len(no_answer)}")
    print(f"источник не отдал условие: {len(missing_src)}")
    print(f"настоящих расхождений: {len(mismatch)} → " +
          ", ".join(f"{k}={v}" for k, v in sorted(by.items())))
    print(f"Выгрузка: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    asyncio.run(main(args.cache, args.out))
