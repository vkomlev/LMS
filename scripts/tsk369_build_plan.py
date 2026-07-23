# -*- coding: utf-8 -*-
"""tsk-369, шаг 3: собрать план привязки файлов и отсеять всё, что не прошло гейт.

ЧТО ДЕЛАЕТ
Сводит результаты шага 2 (`fetched_*.json`) в один план: какие файлы к каким заданиям
привязываем, каким текстом ссылка встанет в условие, и что остаётся оператору.

ПРАВИЛА ОТБОРА (жёсткие — лучше оставить задание в остатке, чем привязать чужой файл)
  * `verdict == match` — сошлись и дословный фрагмент условия, и значимые числа;
    ЛИБО `verdict == weak`, но сработал независимый признак (см. ниже);
  * `ext_ok is not False` — тип файла не противоречит формулировке условия;
  * файл(ы) реально скачаны.
Всё остальное уходит в `manual` с причиной: это список для оператора, а не отказ молча.

ВТОРОЙ ГЕЙТ ДЛЯ `weak`
Вердикт `weak` (сошёлся только один признак из двух) в этой партии почти всегда означал
не чужую задачу, а разницу разметки: импорт в LMS обрезал у условия таблицу-пример, и
её числа «пропадали»; либо наоборот, KaTeX рвал текст и не находился дословный фрагмент.
Поэтому у `weak` проверяются два независимых признака, любой из которых достаточен:
  * **верный ответ в LMS совпадает с ответом источника** — у задач одного типа ответы
    разные, случайное совпадение неправдоподобно (сильнее обоих исходных признаков);
  * **доля общего текста ≥ 0.75** (difflib по буквам) — то есть условие совпадает целиком,
    а не только фрагментом.
Ответы читаются с прода read-only; ничего не пишется.

ФОРМА ССЫЛКИ
Ученику файл виден только ссылкой в `stem` (SPW `attached_file_paths` не читает), поэтому
в начало условия добавляется отдельный абзац со ссылками — тот же приём, что у партии
Крылова (tsk-317): не требует поиска якоря в тексте и устойчив к разнице форматирования.
Метаданные `has_attached_file` / `attached_file_paths` ставятся заодно — в них формат,
который использует импорт ContentBackbone.

Запуск:
  python scripts/tsk369_build_plan.py --items <items.json> --fetched <dir> --out <plan.json>
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import re
import sys
import os
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk369_collect import dsn  # noqa: E402
from tsk369_fetch_files import prose  # noqa: E402

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

MEDIA_BASE = "/api/v1/media"


# Подписи для безымянных файлов, когда их несколько: у ЕГЭ-27 это «файл A» (короткий,
# для отладки) и «файл Б» (полный), и ученик обязан их различать.
_LETTERS = "АБВГДЕЖЗ"


def link_label(f: dict, n_files: int, ix: int) -> str:
    """Подпись ссылки: имя файла из источника, иначе «Файл к заданию» / «Файл А, Б…»."""
    name = (f.get("name") or "").strip()
    if name:
        return name
    if n_files == 1:
        return "Файл к заданию"
    return f"Файл {_LETTERS[ix]}" if ix < len(_LETTERS) else f"Файл {ix + 1}"


def build_block(files: list[dict]) -> str:
    links = " &middot; ".join(
        f'<a href="{MEDIA_BASE}/{f["sha_ext"]}" target="_blank" rel="noopener noreferrer">'
        f'{link_label(f, len(files), ix)}</a>'
        for ix, f in enumerate(files)
    )
    return f"<p><strong>Файл к заданию:</strong> {links}</p>\n"


async def load_answers(ids: list[int]) -> dict[int, str | None]:
    """Верные ответы заданий с прода — read-only, для второго гейта у `weak`."""
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(
            "SELECT id, solution_rules#>>'{short_answer,accepted_answers,0,value}' AS answer "
            "FROM tasks WHERE id = ANY($1::int[])", ids)
        return {r["id"]: r["answer"] for r in rows}
    finally:
        await conn.close()


def _squash(a: str | None) -> str:
    """Ответ без разделителей: источники пишут «2; 6; 2; 3», «229&83», LMS — «2623»."""
    return re.sub(r"[^0-9a-zа-яё]+", "", (a or "").lower())


def weak_is_confirmed(rec: dict, item: dict, lms_answer: str | None) -> tuple[bool, dict]:
    src_answer = rec.get("answer_src")
    a_lms, a_src = _squash(lms_answer), _squash(src_answer)
    answers_agree = bool(a_lms and a_src and (
        a_lms == a_src or a_lms.startswith(a_src) or a_src.startswith(a_lms)))
    ratio = difflib.SequenceMatcher(
        None, prose(item["stem"]), prose(rec.get("src_text") or "")).ratio()
    evidence = {"answers_agree": answers_agree, "answer_lms": lms_answer,
                "answer_src": src_answer, "similarity": round(ratio, 2)}
    return (answers_agree or ratio >= 0.75), evidence


def main(items_path: Path, fetched_dir: Path, out_path: Path,
         confirmed_path: Path | None = None, evidence_path: Path | None = None) -> None:
    # Ручное подтверждение оператора: принимается, только если рядом записано, ЧЕМ именно
    # проверена принадлежность файла (сверка данных, пересчёт ответа). Файл лежит в
    # reviews/ и коммитится вместе с отчётом — это аудит, а не молчаливый обход гейта.
    confirmed: dict[int, dict] = {}
    if confirmed_path and confirmed_path.exists():
        raw = json.loads(confirmed_path.read_text(encoding="utf-8"))
        confirmed = {int(k): v for k, v in raw.items() if k.isdigit()}

    # Машинное доказательство принадлежности вместо текстовой сверки. Нужно там, где
    # условие переписано автором курса и дословный фрагмент не может совпасть в принципе
    # (партия ОГЭ, tsk-392): признаки считает scripts/tsk392_evidence.py, здесь они только
    # принимаются. Отличие от `--confirmed`: там основание — слово оператора, тут —
    # вычисленный и записанный в артефакт признак (sha файла, верный ответ, якоря условия).
    evidence: dict[int, dict] = {}
    if evidence_path and evidence_path.exists():
        raw = json.loads(evidence_path.read_text(encoding="utf-8")).get("evidence", {})
        evidence = {int(k): v for k, v in raw.items()}

    items = {i["id"]: i for i in json.loads(items_path.read_text(encoding="utf-8"))}
    # Порядок чтения = приоритет: первая запись про задание побеждает. Ручной ключ
    # оператора и локальный архив идут раньше автоматических источников — иначе прежний,
    # ошибочный ID из шапки условия так и остался бы победителем (обжиг: у 3177 в шапке
    # значился 27360 вместо 23760, и автоматическая запись перекрывала исправленную).
    priority = ["fetched_operator.json", "fetched_archive.json", "fetched_probe.json"]
    paths = [fetched_dir / n for n in priority if (fetched_dir / n).exists()]
    paths += [p for p in sorted(fetched_dir.glob("fetched_*.json")) if p.name not in priority]
    records: list[dict] = []
    for path in paths:
        records.extend(json.loads(path.read_text(encoding="utf-8")))
    answers = asyncio.run(load_answers(sorted(items)))

    plan, manual = [], []
    seen: set[int] = set()
    for rec in records:
        tid = rec["id"]
        if tid in seen:
            continue
        seen.add(tid)
        item = items.get(tid)
        if item is None:
            continue

        # Пустой файл (0 байт) источник иногда отдаёт вместо приложения — привязывать
        # его бессмысленно: ученик скачает пустоту и решит, что сломана платформа.
        files = [f for f in rec.get("files", []) if f.get("sha256") and f.get("ext")
                 and (f.get("size") or 0) > 0]
        # Близнец внутри LMS: файл уже в CAS, скачивать нечего — размера и sha256 нет.
        files += [f for f in rec.get("files", []) if f.get("reuse") and f.get("sha_ext")]
        reason = None
        evidence_rec: dict = {}
        if tid in evidence and files:
            evidence_rec = {"machine_evidence": evidence[tid]}
        elif tid in confirmed and files:
            evidence_rec = {"operator_confirmed": confirmed[tid].get("reason")}
        elif rec.get("verdict") == "weak" and files:
            ok, evidence_rec = weak_is_confirmed(rec, item, answers.get(tid))
            if not ok:
                reason = ("сверка: weak, второй признак не сработал "
                          f"(ответы={evidence_rec['answers_agree']}, "
                          f"сходство текста={evidence_rec['similarity']})")
        elif rec.get("verdict") != "match":
            reason = f"сверка: {rec.get('verdict')}"
        if not reason and not files:
            reason = "источник не отдал файл"
        elif rec.get("ext_ok") is False:
            reason = "тип файла не совпал с формулировкой условия"

        if reason:
            manual.append({"id": tid, "course_id": rec.get("course_id"),
                           "source": rec.get("source"), "source_id": rec.get("source_id"),
                           "reason": reason, "detail": rec.get("detail"),
                           "n_files": len(files), "stem": item["stem"][:200]})
            continue

        for f in files:
            f.setdefault("sha_ext", f"{f.get('sha256')}.{f['ext']}")
        plan.append({
            "id": tid, "course_id": rec["course_id"], "external_uid": item["external_uid"],
            "source": rec["source"], "source_id": rec["source_id"], "via": item["via"],
            "answer_src": rec.get("answer_src"), "verdict": rec.get("verdict"),
            "evidence": evidence_rec or rec.get("detail"),
            "files": [{"sha_ext": f["sha_ext"], "ext": f["ext"], "size": f.get("size"),
                       "name": f.get("name") or "", "path": f.get("path"),
                       "url": f.get("url"), "reuse": bool(f.get("reuse"))}
                      for f in files],
            "block": build_block(files),
        })

    # Задания, до которых шаг 2 вообще не дошёл (нет источника) — тоже в остаток.
    for tid, item in items.items():
        if tid in seen:
            continue
        manual.append({"id": tid, "course_id": item["course_id"], "source": item["source"],
                       "source_id": item["source_id"], "reason": "источник не определён",
                       "external_uid": item["external_uid"], "stem": item["stem"][:200]})

    out = {"plan": plan, "manual": manual}
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    uniq_files = {f["sha_ext"] for p in plan for f in p["files"]}
    total_bytes = sum(f["size"] or 0 for p in plan for f in p["files"])
    by_reason: dict[str, int] = {}
    for m in manual:
        by_reason[m["reason"]] = by_reason.get(m["reason"], 0) + 1

    print(f"К привязке: {len(plan)} заданий, файлов {sum(len(p['files']) for p in plan)} "
          f"(уникальных {len(uniq_files)}, суммарно {total_bytes/1e6:.1f} МБ)")
    print(f"В остаток оператору: {len(manual)}")
    for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        print(f"    {reason}: {n}")
    print(f"\nСохранено: {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True)
    ap.add_argument("--fetched", required=True, help="каталог с fetched_*.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--confirmed", help="JSON с подтверждениями оператора (reviews/...)")
    ap.add_argument("--evidence", help="JSON машинных доказательств (scripts/tsk392_evidence.py)")
    a = ap.parse_args()
    main(Path(a.items), Path(a.fetched), Path(a.out),
         Path(a.confirmed) if a.confirmed else None,
         Path(a.evidence) if a.evidence else None)
