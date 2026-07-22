# -*- coding: utf-8 -*-
"""tsk-369, добор: найти файл там, где источник в шапке не назван.

ДВА ПУТИ

1. **Перебор источников по ID из шапки.** У части заданий из Telegram в шапке есть номер
   («Задание 17_23757 Демоверсия 2026»), но нет имени источника, а ссылки в посте не
   осталось. Тот же ID у kompege, sdamgia и kpolyakov ведёт на разные задачи — совпадёт
   условие только у одного. Гейт сверки тот же, что в основном проходе; совпало у
   нескольких (маловероятно) — кандидат отбрасывается как неоднозначный.

2. **Близнец внутри LMS.** Задания по сборнику Крылова («Задание 9 Вариант 1 Крылова
   С.С.») дублируют партию `crylov:v<В>t<З>`, у которой файл уже привязан (tsk-317, файлы
   выверены по книге). Тогда ни скачивать, ни класть в CAS ничего не нужно: берётся
   готовый `sha_ext` близнеца. Условие близнеца сверяется с условием задания тем же
   дословным фрагментом — совпадение обязательно, иначе кандидат не берётся.

Ничего не пишет в БД. Выход — в формате шага 2 (`fetched_probe.json`), его подхватывает
`tsk369_build_plan.py`.

Запуск:  python scripts/tsk369_probe_unknown.py --items <items.json> --out-dir <dir>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import difflib
import time
import urllib.error
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tsk369_collect import dsn, strip_html  # noqa: E402
from tsk369_fetch_files import (  # noqa: E402
    GETTERS, ext_from_headers, expected_ext, fetch_bytes, middle_slice, prose, verdict_for,
)

_STEM_ID = re.compile(r"(?:Задани[ея]|задани[ея])[^_<]{0,16}_(\d+)")
# «Задание 9 Вариант 1 Крылова С.С.» / «Задание 22 Крылов С.С. вариант 1» /
# «Задание 26_v1 (Сборник Крылова С.С. 2026)» — номер задания и номер варианта
# стоят в шапке в любом порядке, поэтому ищутся двумя отдельными выражениями.
_KRYLOV = re.compile(r"крылов", re.I)
_TASK_NO = re.compile(r"задани[ея]\s*(\d{1,2})", re.I)
# «Вариант 1», «вариант Крылова С.С. 5», «26_v1» — номер варианта стоит и сразу после
# слова, и через имя автора, поэтому между ними допускается короткая вставка без цифр.
_VAR_NO = re.compile(r"(?:вариант\w*[^\d\n]{0,20}(\d{1,2})|_v(\d{1,2})\b)", re.I)

TWIN_SQL = """
SELECT id, external_uid,
       task_content->>'stem' AS stem,
       task_content->'attached_file_paths' AS paths,
       solution_rules#>>'{short_answer,accepted_answers,0,value}' AS answer
FROM tasks
WHERE is_active AND external_uid LIKE 'crylov:%'
"""

ANSWERS_SQL = """
SELECT id, solution_rules#>>'{short_answer,accepted_answers,0,value}' AS answer
FROM tasks WHERE id = ANY($1::int[])
"""

_SHA_EXT = re.compile(r"/api/v1/media/([0-9a-f]{64}\.[a-z0-9]+)")


def krylov_key(stem: str) -> tuple[int, int] | None:
    head = stem[:200]
    if not _KRYLOV.search(head):
        return None
    t, v = _TASK_NO.search(head), _VAR_NO.search(head)
    if not t or not v:
        return None
    return int(t.group(1)), int(v.group(1) or v.group(2))


async def load_krylov_twins() -> dict[tuple[int, int], dict]:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(TWIN_SQL)
    finally:
        await conn.close()
    twins: dict[tuple[int, int], dict] = {}
    for r in rows:
        m = re.fullmatch(r"crylov:v(\d+)t(\d+)", r["external_uid"] or "")
        if not m:
            continue
        shas = _SHA_EXT.findall(r["stem"] or "")
        for p in (json.loads(r["paths"]) if isinstance(r["paths"], str) else (r["paths"] or [])):
            hit = _SHA_EXT.search(str(p))
            if hit:
                shas.append(hit.group(1))
        if not shas:
            continue
        twins[(int(m.group(2)), int(m.group(1)))] = {
            "twin_id": r["id"], "external_uid": r["external_uid"],
            "sha_ext": sorted(set(shas)), "stem": strip_html(r["stem"]),
            "answer": r["answer"],
        }
    return twins


async def load_answers(ids: list[int]) -> dict[int, str | None]:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        return {r["id"]: r["answer"] for r in await conn.fetch(ANSWERS_SQL, ids)}
    finally:
        await conn.close()


def probe_sources(stem: str, sid: str) -> list[dict]:
    hits = []
    for src, getter in GETTERS.items():
        if src == "yandex":
            continue  # у yandex ID — UUID, перебор по числовому ID неприменим
        try:
            text, answer, files = getter(sid)
            time.sleep(0.5)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError):
            continue
        verdict, detail = verdict_for(stem, text)
        if verdict == "match":
            hits.append({"source": src, "source_id": sid, "text": text,
                         "answer": answer, "files": files, "detail": detail})
    return hits


def main(items_path: Path, out_dir: Path) -> None:
    items = json.loads(items_path.read_text(encoding="utf-8"))
    targets = [i for i in items if not i.get("source")]
    twins = asyncio.run(load_krylov_twins())
    answers = asyncio.run(load_answers([i["id"] for i in targets]))
    for i in targets:
        i["answer"] = answers.get(i["id"])
    files_dir = out_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for it in targets:
        rec = {"id": it["id"], "course_id": it["course_id"], "via": "probe",
               "phrase": it.get("phrase"), "files": []}

        key = krylov_key(it["stem"])
        twin = twins.get(key) if key else None
        if twin:
            # Пост в Telegram пересказывает условие своими словами и добавляет шапку
            # («Решаем через циклы», «Уровень средний»), поэтому дословный фрагмент из
            # СЕРЕДИНЫ съезжает и не находится. Второй признак — доля общего текста, и
            # третий, решающий, — совпадение верного ответа с ответом близнеца: у задач
            # одного типа ответы различаются, случайное совпадение неправдоподобно.
            frag = middle_slice(prose(it["stem"]))
            ratio = difflib.SequenceMatcher(None, prose(it["stem"]), prose(twin["stem"])).ratio()
            answers_agree = (
                it.get("answer") and twin.get("answer")
                and re.sub(r"\s+", " ", str(it["answer"]).strip())
                == re.sub(r"\s+", " ", str(twin["answer"]).strip())
            )
            # Задания ОДНОГО номера в разных вариантах Крылова написаны почти одним
            # текстом — различает их именно файл. Поэтому одной похожести мало: вариант
            # из шапки обязан быть лучшим среди всех вариантов этого номера, либо ответы
            # должны совпасть. Иначе можно привязать файл соседнего варианта.
            same_number = {v: difflib.SequenceMatcher(
                None, prose(it["stem"]), prose(t["stem"])).ratio()
                for (n, v), t in twins.items() if n == key[0]}
            best_var = max(same_number, key=lambda v: same_number[v]) if same_number else None
            variant_is_best = best_var == key[1]
            if answers_agree or (variant_is_best and (ratio >= 0.75 or (frag and frag in prose(twin["stem"])))):
                rec.update({
                    "source": "lms_twin", "source_id": twin["external_uid"],
                    "verdict": "match", "ext_ok": None,
                    "detail": {"twin_id": twin["twin_id"],
                               "prose_ok": bool(frag) and frag in prose(twin["stem"]),
                               "similarity": round(ratio, 2),
                               "variant_is_best": variant_is_best,
                               "similarity_by_variant": {str(v): round(s, 2)
                                                         for v, s in sorted(same_number.items())},
                               "answers_agree": bool(answers_agree),
                               "answer_lms": it.get("answer"), "answer_twin": twin.get("answer"),
                               "fragment": frag[:80], "nums_ok": None},
                    "src_text": twin["stem"][:6000],
                    "files": [{"sha_ext": s, "ext": s.rsplit(".", 1)[1], "size": None,
                               "name": "", "url": f"lms:{twin['twin_id']}", "reuse": True}
                              for s in twin["sha_ext"]],
                })
                rec["n_files"] = len(rec["files"])
                results.append(rec)
                print(f"  [twin     ] id={it['id']} ← {twin['external_uid']} "
                      f"(задание {key[0]}, вариант {key[1]}), файлов {len(rec['files'])}")
                continue
            print(f"  [twin-fail] id={it['id']} условие не совпало с {twin['external_uid']}")

        m = _STEM_ID.search(it["stem"])
        if not m:
            rec.update({"verdict": "no_source_id"})
            results.append(rec)
            print(f"  [нет ID   ] id={it['id']} — источник не определить")
            continue

        hits = probe_sources(it["stem"], m.group(1))
        if len(hits) != 1:
            rec.update({"verdict": "ambiguous" if hits else "no_match",
                        "source_id": m.group(1), "candidates": [h["source"] for h in hits]})
            results.append(rec)
            print(f"  [{rec['verdict']:9}] id={it['id']} ID={m.group(1)} "
                  f"кандидатов: {rec['candidates']}")
            continue

        hit = hits[0]
        rec.update({"source": hit["source"], "source_id": hit["source_id"],
                    "verdict": "match", "detail": hit["detail"],
                    "answer_src": hit["answer"], "src_text": hit["text"][:6000]})
        want = expected_ext(it["stem"])
        for n, f in enumerate(hit["files"]):
            try:
                data, headers = fetch_bytes(f["url"])
                time.sleep(0.3)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                rec["files"].append({"url": f["url"], "error": str(exc)})
                continue
            ext = ext_from_headers(headers, f["url"], f.get("name", ""))
            if not ext:
                rec["files"].append({"url": f["url"], "error": "не определил расширение"})
                continue
            import hashlib
            dest = files_dir / f"{it['id']}_{n}.{ext}"
            dest.write_bytes(data)
            rec["files"].append({"url": f["url"], "name": f.get("name") or "", "ext": ext,
                                 "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                 "path": str(dest),
                                 "ext_ok": (ext in want) if want else None})
        got = [f for f in rec["files"] if f.get("ext")]
        rec["n_files"] = len(got)
        rec["ext_ok"] = None if want is None else (all(f.get("ext_ok") for f in got) if got else None)
        if not got:
            rec["verdict"] = "match_no_files"
        results.append(rec)
        print(f"  [{rec['verdict']:9}] id={it['id']} {hit['source']}:{hit['source_id']} "
              f"файлов {len(got)}")

    out_path = out_dir / "fetched_probe.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    stats: dict[str, int] = {}
    for r in results:
        stats[r["verdict"]] = stats.get(r["verdict"], 0) + 1
    print(f"\nИтого: {stats}\nСохранено: {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    main(Path(a.items), Path(a.out_dir))
