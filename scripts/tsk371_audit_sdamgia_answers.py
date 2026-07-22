# -*- coding: utf-8 -*-
"""tsk-371, шаг 1: сверить ответы заданий sdamgia с ответами источника (read-only).

ЗАЧЕМ
У «Решу ЕГЭ» составной ответ пишется через собственный разделитель: `4229&23` — это два
числа, а не одно значение. При импорте такой ответ ломался: у задания 3791 в LMS оказалось
`ы` — ни первого числа, ни второго. Один случай нашёлся при разборе tsk-369 случайно;
здесь класс проверяется целиком.

ЧТО ДЕЛАЕТ
Берёт с прода активные задания с источником sdamgia и непустым `accepted_answers`, тянет
ответ и условие со страницы задачи и сравнивает. Сравнение нормализующее: `&`, `;`, `,`
и переводы строк считаются пробелом (форма записи различается, а состав должен совпасть).

СВЯЗКИ 19-21
`problem?id=N` у «Решу ЕГЭ» нередко отдаёт не одну задачу, а связку из трёх (19, 20, 21) —
у каждой свой блок «Ответ». ID в LMS при этом указывает на ПЕРВУЮ задачу связки, а само
задание в LMS бывает вторым или третьим. Поэтому нужный блок выбирается по хвосту условия
LMS (по самому вопросу — середина у связки общая), см. `sdamgia_block`.

КАТЕГОРИИ
  * `совпал`            — с точностью до разделителей;
  * `часть_ответа`      — в LMS только первое число составного ответа (тоже дефект: ученик
                          с полным ответом получит «неверно»);
  * `разошлись`         — состав другой; **сначала смотреть, та ли это задача**: сверка
                          условия идёт рядом (`prose_ok`), потому что по ID можно уехать
                          на чужую задачу (обжиг tsk-369: ID 27360 давал «правильный» ответ
                          от совсем другого задания);
  * `нет ответа у источника` / `ошибка сети`.

Ничего не пишет. На выходе JSON для шага 2.

Запуск: python scripts/tsk371_audit_sdamgia_answers.py --out <файл.json> [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
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
from tsk369_fetch_files import src_sdamgia, verdict_for  # noqa: E402

TASKS_SQL = """
SELECT t.id, t.course_id, t.external_uid,
       t.task_content->>'stem' AS stem,
       t.task_content->>'answer_raw' AS answer_raw,
       -- Партия ОГЭ имеет вид `sdamgia:oge:<номер задания>:<id>` и живёт на отдельном
       -- домене того же движка, поэтому ID берётся последним числовым сегментом.
       coalesce(t.task_content->>'source_task_id',
                (regexp_match(t.external_uid, 'sdamgia:(?:oge:)?(?:\\d+:)?(\\d+)$'))[1]) AS source_id,
       (t.external_uid ~ 'sdamgia:oge') AS is_oge,
       t.solution_rules#>>'{short_answer,accepted_answers,0,value}' AS answer,
       coalesce(jsonb_array_length(t.solution_rules#>'{short_answer,accepted_answers}'), 0) AS n_answers
FROM tasks t
WHERE t.is_active
  AND (t.task_content->>'source_kind' = 'sdamgia' OR t.external_uid ~ 'sdamgia')
  AND coalesce(jsonb_array_length(t.solution_rules#>'{short_answer,accepted_answers}'), 0) > 0
ORDER BY t.id
"""


def tokens(value: str | None) -> list[str]:
    """Состав ответа: разделители источника (`&`, `;`, `,`) и пробелы равнозначны."""
    return [t for t in re.split(r"[^0-9A-Za-zА-Яа-яЁё]+", (value or "").strip()) if t]


def classify(lms: str | None, src: str | None) -> str:
    a, b = tokens(lms), tokens(src)
    if not b:
        return "нет ответа у источника"
    if a == b:
        return "совпал"
    if a and b[:len(a)] == a and len(a) < len(b):
        return "часть_ответа"
    if a and len(a) > len(b) and a[:len(b)] == b:
        return "лишнее_в_lms"
    return "разошлись"


async def load_tasks(limit: int | None) -> list[dict]:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(TASKS_SQL)
    finally:
        await conn.close()
    out = [dict(r) for r in rows]
    return out[:limit] if limit else out


def main(out_path: Path, limit: int | None) -> None:
    tasks = asyncio.run(load_tasks(limit))
    print(f"Заданий sdamgia с ответом: {len(tasks)}")
    results, stats = [], {}
    for n, t in enumerate(tasks, 1):
        sid = (t["source_id"] or "").strip()
        rec = {"id": t["id"], "course_id": t["course_id"], "external_uid": t["external_uid"],
               "source_id": sid, "answer_lms": t["answer"], "answer_raw": t["answer_raw"],
               "n_answers": t["n_answers"]}
        if not re.fullmatch(r"\d+", sid):
            rec["status"] = "нет ID источника"
            results.append(rec)
            stats[rec["status"]] = stats.get(rec["status"], 0) + 1
            continue
        try:
            # Условие обязательно: страница `problem?id=N` часто отдаёт связку 19-21, и
            # без текстовой привязки ответом считался ответ соседней задачи.
            text, answer, _files = src_sdamgia(sid, strip_html(t["stem"]), bool(t.get("is_oge")))
            time.sleep(0.7)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as exc:
            rec.update({"status": "ошибка сети", "error": str(exc)})
            results.append(rec)
            stats["ошибка сети"] = stats.get("ошибка сети", 0) + 1
            continue

        status = classify(t["answer"], answer)
        verdict, detail = verdict_for(strip_html(t["stem"]), text)
        rec.update({"status": status, "answer_src": answer,
                    "same_task": verdict, "prose_ok": detail["prose_ok"],
                    "src_text": text[:2000]})
        results.append(rec)
        stats[status] = stats.get(status, 0) + 1
        if status != "совпал":
            print(f"  [{status:22}] id={t['id']} sdamgia:{sid} "
                  f"LMS={t['answer']!r} источник={answer!r} задача_та_же={verdict}")
        if n % 50 == 0:
            print(f"  … обработано {n}/{len(tasks)}")

    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nИтого: {stats}\nСохранено: {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    main(Path(a.out), a.limit)
