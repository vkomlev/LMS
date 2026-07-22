# -*- coding: utf-8 -*-
"""tsk-373, шаг 4: закрепить проверенный источник заданий и исправить ошибочные номера.

ЧТО И ПОЧЕМУ

Разбор 74 расхождений «ответ в LMS ≠ ключ kompege» показал, что неверных ОТВЕТОВ среди
них нет ни одного. Расхождения дали два других класса:

1. **Источник опознан неверно самим аудитом (17).** У партии `tg:ege` источник написан
   в шапке рядом с номером — «(Поляков)», «(Решу ЕГЭ)», — а сверка [[tsk-370]] считала
   заданием kompege всё, где есть «Задание NN_<id>». По ID из шапки спрашивали не тот
   сайт, и он отдавал постороннюю задачу. Проверка у РОДНОГО источника
   (`tsk373_verify_native.py`) подтвердила и задачу, и ответ — дефекта нет.
   Правка: записать проверенный источник в `task_content.source_kind` / `source_task_id`,
   чтобы следующий аудит шёл на нужный сайт, а не поднимал ту же ложную тревогу.

2. **Опечатка в номере задачи (5).** Номер в шапке указывает на чужую задачу; настоящая
   найдена перебором «соседей по опечатке» (`tsk373_find_true_id.py`) и совпала с условием
   LMS дословно, а её ключ — с ответом в LMS. То есть ответ был верен, ошибочен номер.
   Два случая — контрольные: 3058 (23746 → 23747) и 3177 (27360 → 23760) уже подтверждены
   оператором в [[tsk-369]], и перебор нашёл именно их.
   Правка: номер в видимой шапке условия и та же пара полей источника.

Задание 3309 намеренно не трогается: у него ответ `xzwy` (четыре переменные w, x, y, z)
не может относиться к его же условию про функцию от a, b, c, а таблицы истинности в
условии нет вовсе — решать нечего. Это решение оператора, не автоматическая правка.

ГЕЙТ ПЕРЕД ЗАПИСЬЮ (внутри транзакции, не по таблице ниже)
Каждая строка доказывается заново живым запросом к источнику: условие LMS сверяется с
условием задачи по планируемому ID двумя признаками (дословный фрагмент + значимые числа
или совпавший ответ), и ответ LMS сверяется с ключом источника. Не прошло — вся
транзакция откатывается. Таблица ниже — план, а не доказательство.

dry-run по умолчанию; `--apply` при DBCHECK_OK=1. Бэкап прежних значений пишется до
записи, после COMMIT — независимая построчная проверка.

Запуск: python scripts/tsk373_apply.py --backup <файл.json> [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk362_fetch_answers import GETTERS  # noqa: E402
from tsk370_scan import dsn  # noqa: E402
from tsk373_classify import answers_loose_equal, classify  # noqa: E402

PAUSE_SEC = 0.7

# id → (источник, проверенный ID, старый ID в шапке или None если шапка верна)
PLAN: dict[int, tuple[str, str, str | None]] = {
    # источник опознан по маркеру в шапке и подтверждён им же — номер верен
    3107: ("polyakov", "7048", None),
    3114: ("polyakov", "7048", None),
    3136: ("polyakov", "7442", None),
    3194: ("polyakov", "7613", None),   # маркера в шапке нет, источник опознан перебором
    3198: ("polyakov", "7442", None),
    3203: ("polyakov", "4406", None),
    3230: ("polyakov", "8064", None),
    3238: ("polyakov", "7857", None),
    3245: ("polyakov", "141", None),
    3277: ("polyakov", "7048", None),
    3302: ("polyakov", "6350", None),
    3303: ("polyakov", "5438", None),
    3305: ("polyakov", "4163", None),
    3308: ("polyakov", "2380", None),
    3310: ("polyakov", "5918", None),
    3343: ("polyakov", "7926", None),
    3418: ("sdamgia", "28550", None),
    # опечатка в номере: настоящая задача найдена и сверена, ответ LMS равен её ключу
    3036: ("kompege", "206", "216"),
    3056: ("kompege", "23751", "23749"),
    3058: ("kompege", "23747", "23746"),
    3177: ("kompege", "23760", "27360"),
    3241: ("kompege", "23562", "23532"),
}


def check_source(stem: str, answers: list[str], kind: str, sid: str) -> dict:
    """Живая сверка условия и ответа с задачей источника по планируемому ID."""
    answer, text = GETTERS[kind](sid)
    time.sleep(PAUSE_SEC)
    feats = classify(stem, text)
    same_ans = any(answers_loose_equal(v, answer) for v in answers) if answer else None
    ok_text = feats["verdict"] == "same" or (feats["verdict"] == "ambiguous" and same_ans)
    return {"ok": bool(ok_text and same_ans), "answer_src": answer,
            "same_answer": same_ans, **{k: feats[k] for k in
                                        ("verdict", "lcs", "jaccard", "numbers_src_cover")}}


async def main(backup_path: Path, apply: bool) -> None:
    ids = sorted(PLAN)
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = {r["id"]: r for r in await conn.fetch(
            "SELECT id, external_uid, is_active, task_content, "
            "       task_content->>'stem' AS stem, "
            "       solution_rules #> '{short_answer,accepted_answers}' AS answers "
            "FROM tasks WHERE id = ANY($1::int[])", ids)}
        missing = sorted(set(ids) - set(rows))
        if missing:
            raise RuntimeError(f"не нашёл заданий: {missing}")
        inactive = [i for i in ids if not rows[i]["is_active"]]
        if inactive:
            raise RuntimeError(f"задания неактивны, править нечего: {inactive}")

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            [{"id": i, "external_uid": rows[i]["external_uid"],
              "task_content": json.loads(rows[i]["task_content"])} for i in ids],
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Бэкап прежних значений task_content: {backup_path}\n")

        # ---- гейт: каждая строка доказывается живым запросом к источнику ----
        evidence, bad = {}, []
        for i in ids:
            kind, sid, old = PLAN[i]
            answers = [a["value"] for a in json.loads(rows[i]["answers"] or "[]")
                       if a.get("value") is not None]
            ev = check_source(rows[i]["stem"], answers, kind, sid)
            evidence[i] = ev
            mark = "OK " if ev["ok"] else "СТОП"
            print(f"  {mark} id={i} {kind}:{sid}"
                  + (f" (в шапке {old})" if old else "")
                  + f"  текст={ev['verdict']} lcs={ev['lcs']} чис={ev['numbers_src_cover']}"
                    f"  ответ LMS={answers[:1]} против {ev['answer_src']!r}")
            if not ev["ok"]:
                bad.append(i)
        if bad:
            raise RuntimeError(f"сверка с источником не прошла, ничего не пишу: {bad}")

        async with conn.transaction():
            for i in ids:
                kind, sid, old = PLAN[i]
                stem = rows[i]["stem"] or ""
                if old:
                    pattern = re.compile(r"(Задание\s*\d+[_ ])" + re.escape(old) + r"\b")
                    found = pattern.findall(stem)
                    if len(found) != 1:
                        raise RuntimeError(
                            f"id={i}: старый номер {old} встречается в шапке "
                            f"{len(found)} раз — правку не делаю")
                    stem = pattern.sub(lambda m: m.group(1) + sid, stem, count=1)
                await conn.execute(
                    "UPDATE tasks SET task_content = "
                    "  jsonb_set(jsonb_set(jsonb_set(task_content, "
                    "    '{source_kind}', to_jsonb($2::text)), "
                    "    '{source_task_id}', to_jsonb($3::text)), "
                    "    '{stem}', to_jsonb($4::text)) "
                    "WHERE id = $1", i, kind, sid, stem)

            check = {r["id"]: r for r in await conn.fetch(
                "SELECT id, task_content->>'source_kind' AS kind, "
                "       task_content->>'source_task_id' AS sid, "
                "       task_content->>'stem' AS stem "
                "FROM tasks WHERE id = ANY($1::int[])", ids)}
            problems = []
            for i in ids:
                kind, sid, old = PLAN[i]
                if check[i]["kind"] != kind or check[i]["sid"] != sid:
                    problems.append((i, "поля источника"))
                if old and re.search(r"Задание\s*\d+[_ ]" + re.escape(old) + r"\b",
                                     check[i]["stem"]):
                    problems.append((i, "старый номер остался в шапке"))
                if old and not re.search(r"Задание\s*\d+[_ ]" + re.escape(sid) + r"\b",
                                         check[i]["stem"]):
                    problems.append((i, "новый номер не появился в шапке"))
            if problems:
                raise AssertionError(f"проверка внутри транзакции не прошла: {problems}")
            print(f"\nВнутри транзакции: обновлено и проверено {len(ids)} заданий.")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after = {r["id"]: r for r in await conn.fetch(
            "SELECT id, task_content->>'source_kind' AS kind, "
            "       task_content->>'source_task_id' AS sid, "
            "       substring(regexp_replace(task_content->>'stem', '<[^>]+>', ' ', 'g') "
            "                 from 'Задание[^\\n]{0,30}') AS header "
            "FROM tasks WHERE id = ANY($1::int[])", ids)}
        for i in ids:
            r = after[i]
            print(f"  id={i}: {r['kind']}:{r['sid']}  шапка «{(r['header'] or '').strip()}»")
        wrong = [i for i in ids
                 if after[i]["kind"] != PLAN[i][0] or after[i]["sid"] != PLAN[i][1]]
        if wrong:
            print(f"  ПРОБЛЕМНЫЕ: {wrong}")
            sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    try:
        asyncio.run(main(Path(a.backup), a.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
