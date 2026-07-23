# -*- coding: utf-8 -*-
"""tsk-392, шаг 2.5: доказать принадлежность файла заданию ОГЭ там, где текстовая сверка не работает.

ЗАЧЕМ ОТДЕЛЬНЫЙ ШАГ
Гейт tsk-369 сверяет дословный фрагмент условия и значимые числа источника. Для партии
ОГЭ он даёт `weak`/`mismatch` почти всем (5 `match` из 110) — и это не признак чужого
файла, а следствие того, что **автор курса переписал условия своими словами**:
  * «Скачай файл-таблицу задачи 10566 … Сколько учеников набрали более 600 баллов»
    вместо исходного условия с таблицей на пол-экрана;
  * задание ОГЭ-14 разбито на два подвопроса (`_1`, `_2`), у каждого свой кусок условия.
Ослаблять общий гейт ради этого нельзя — он защищает партии ЕГЭ. Поэтому здесь считаются
ДРУГИЕ признаки, независимые от формулировки, и результат кладётся в файл-доказательство,
который `tsk369_build_plan.py --evidence` принимает как основание привязки.

ПРИЗНАКИ (у каждого задания печатается, какой именно сработал)
  1. `twin_sha` — скачанный по ID файл **байт-в-байт равен файлу, который LMS уже отдаёт**
     другому заданию с тем же ID источника (курс 1179: `oge:reshu:t14:10566_1` и близнец
     `sdamgia:oge:14:10566`). Сильнейший признак: платформа уже раздаёт этот файл ученикам
     как приложение к этой же задаче источника, и sha256 совпал.
  2. `answer` — верный ответ в LMS совпал с ответом источника. У задач одного типа ответы
     разные («Овсяников», «Николай»), случайное совпадение неправдоподобно. Но ответ бывает
     и коротким числом («3», «17») — одного его мало, поэтому он идёт в паре с признаком 3.
  3. `tokens` — имена каталогов и характерные обороты из условия LMS («Проза», «DEMO-12»,
     «единственном сыне») присутствуют в тексте источника. Порог — доля 0.6, а не 1.0:
     источник местами опускает имя подкаталога в вёрстке («в подкаталоге каталога Проза»),
     хотя задача та же. Все задания с долей < 1.0 печатаются поимённо для ручного просмотра.

ПРАВИЛО ПОДТВЕРЖДЕНИЯ: `twin_sha` **либо** (`answer` И `tokens`). Один признак из 2-3 —
недостаточно; задание уходит в остаток, а не привязывается «на всякий случай».

Read-only: только SELECT с прод-DSN. Ничего не пишет в БД.

Запуск:
  python scripts/tsk392_evidence.py --items <items.json> --fetched <fetched.json> --out <evidence.json>
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk369_build_plan import _squash  # noqa: E402
from tsk369_collect import dsn  # noqa: E402

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

TOKEN_RATIO = 0.6

# Токены-«якоря» условия: имена каталогов в кавычках-ёлочках и коды вида DEMO-12.
_QUOTED = re.compile(r"«([^»]{2,40})»")
_CODE = re.compile(r"\b((?:DEMO|Task|demo|task)[-\w]*)\b")


def norm(s: str | None) -> str:
    """Только буквы и цифры в нижнем регистре: источник расставляет мягкие переносы."""
    return re.sub(r"[^0-9a-zA-Zа-яА-ЯёЁ]+", "", (s or "").replace("­", "")).lower()


def token_evidence(lms_stem: str, src_text: str | None) -> tuple[bool, dict]:
    tokens = {t for t in (set(_QUOTED.findall(lms_stem)) | set(_CODE.findall(lms_stem)))
              if len(norm(t)) > 2}
    if not tokens:
        return False, {"tokens": [], "found": [], "ratio": None}
    src = norm(src_text)
    found = sorted(t for t in tokens if norm(t) in src)
    ratio = len(found) / len(tokens)
    return ratio >= TOKEN_RATIO, {"tokens": sorted(tokens), "found": found,
                                  "ratio": round(ratio, 2)}


def answer_evidence(lms_answer: str | None, src_answer: str | None) -> tuple[bool, dict]:
    """Источник иногда даёт несколько допустимых ответов через `|`."""
    a_lms = _squash(lms_answer)
    variants = {_squash(x) for x in re.split(r"\|", src_answer or "") if x.strip()}
    variants.discard("")
    ok = bool(a_lms) and any(
        a_lms == v or v.startswith(a_lms) or a_lms.startswith(v) for v in variants)
    return ok, {"answer_lms": lms_answer, "answer_src": src_answer}


async def load_state(ids: list[int]) -> tuple[dict[int, str | None], dict[str, str]]:
    """Ответы кандидатов и sha256 файлов, уже привязанных к заданиям с тем же ID источника."""
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(
            "SELECT id, solution_rules#>>'{short_answer,accepted_answers,0,value}' AS answer "
            "FROM tasks WHERE id = ANY($1::int[])", ids)
        answers = {r["id"]: r["answer"] for r in rows}
        # Близнецы: активные задания, у которых в условии уже стоит ссылка на файл в CAS.
        # Ключ — ID источника из external_uid, значение — sha256 привязанного файла.
        twins_rows = await conn.fetch(r"""
            SELECT external_uid,
                   (regexp_match(task_content->>'stem',
                                 '/api/v1/media/([0-9a-f]{64})\.'))[1] AS sha
            FROM tasks
            WHERE is_active
              AND external_uid ~ '^(oge:reshu:t[0-9]+|sdamgia:oge:[0-9]+):[0-9]+(_[0-9]+)?$'
              AND (task_content->>'stem') ~ '/api/v1/media/'
        """)
        twins: dict[str, str] = {}
        for r in twins_rows:
            if r["sha"]:
                twins[r["external_uid"].split(":")[-1].split("_")[0]] = r["sha"]
        return answers, twins
    finally:
        await conn.close()


def main(items_path: Path, fetched_path: Path, out_path: Path) -> None:
    items = {i["id"]: i for i in json.loads(items_path.read_text(encoding="utf-8"))}
    recs = json.loads(fetched_path.read_text(encoding="utf-8"))
    answers, twins = asyncio.run(load_state(sorted(items)))

    evidence: dict[str, dict] = {}
    rejected: list[dict] = []
    partial_tokens: list[tuple] = []
    stats = {"twin_sha": 0, "answer+tokens": 0, "отклонено": 0}

    for rec in recs:
        tid = rec["id"]
        item = items.get(tid)
        if item is None:
            continue
        sha = next((f["sha256"] for f in rec.get("files", []) if f.get("sha256")), None)
        twin_sha = twins.get(str(item["source_id"]))
        twin_ok = bool(sha and twin_sha and sha == twin_sha)

        ans_ok, ans_detail = answer_evidence(answers.get(tid), rec.get("answer_src"))
        tok_ok, tok_detail = token_evidence(item["stem"], rec.get("src_text"))
        if tok_detail["ratio"] is not None and tok_detail["ratio"] < 1.0:
            partial_tokens.append((tid, item["source_id"], tok_detail["ratio"],
                                   sorted(set(tok_detail["tokens"]) - set(tok_detail["found"]))))

        kinds = []
        if twin_ok:
            kinds.append("twin_sha")
        if ans_ok and tok_ok:
            kinds.append("answer+tokens")
        if not kinds:
            rejected.append({"id": tid, "source_id": item["source_id"],
                             "verdict": rec.get("verdict"), "answer": ans_detail,
                             "tokens": tok_detail, "twin_sha": twin_sha, "sha": sha,
                             "stem": item["stem"][:180]})
            stats["отклонено"] += 1
            continue
        for k in kinds:
            stats[k] += 1
        evidence[str(tid)] = {
            "kinds": kinds, "source": rec.get("source"), "source_id": item["source_id"],
            "verdict_text_gate": rec.get("verdict"),
            "twin_sha": twin_sha if twin_ok else None,
            "answer": ans_detail if ans_ok else None,
            "tokens": tok_detail if tok_ok else None,
        }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"evidence": evidence, "rejected": rejected},
                                   ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"Подтверждено: {len(evidence)} из {len(recs)}")
    for k, n in stats.items():
        print(f"    {k}: {n}")
    if partial_tokens:
        print(f"\nСовпали не все якоря условия ({len(partial_tokens)}) — просмотреть глазами:")
        for row in partial_tokens:
            print(f"    id={row[0]} источник={row[1]} доля={row[2]} не найдено: {row[3]}")
    if rejected:
        print(f"\nНе подтверждено ({len(rejected)}) — уйдёт в остаток оператору:")
        for r in rejected[:20]:
            print(f"    id={r['id']} {r['source_id']} вердикт={r['verdict']} "
                  f"ответ={r['answer']['answer_lms']!r}/{r['answer']['answer_src']!r} "
                  f"якоря={r['tokens']['ratio']}")
    print(f"\nСохранено: {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True)
    ap.add_argument("--fetched", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    main(Path(a.items), Path(a.fetched), Path(a.out))
