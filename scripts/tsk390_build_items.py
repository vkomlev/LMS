# -*- coding: utf-8 -*-
"""tsk-390, шаг 1: собрать задания ЕГЭ без файла-данных под доскачивание (расширение tsk-369).

ОТЛИЧИЕ ОТ tsk369_collect
tsk369_collect ловит кандидатов по ТЕКСТУ условия (FILE_GATE_RE «откройте файл…»).
Этого мало: у kompege/polyakov заданий типов 22/24/27 формулировка краткая и файла в тексте
не называет, хотя по типу задания файл нужен. Поэтому здесь критерий — «в задании нет
ссылки на файл-ДАННЫЕ» (`/api/v1/media/<64hex>.<data-ext>`, где data-ext ∈ xlsx|xls|ods|
csv|txt|odt|docx|doc|zip). PNG/JPG-картинки таблиц файлом не считаются. Нужен ли файл на
самом деле — рассудит источник на шаге 2 (есть у него files[] или нет).

ОБЛАСТЬ
Курсы `wp:zadanie-*`, блок «Сложные задания» `lms:tsk347:hard:*`, курсы 138/139.
Номер задания ЕГЭ — из `external_uid` (`wp_nav:<N>:`) либо `course_uid` (`zadanie-<N>`).
Типы с обязательным файлом (по решению оператора 2026-07-24): 3,9,10,17,18,22,24,26,27.
Задание 25 исключено (данные-диапазон в тексте, файл не нужен).

ИСТОЧНИК заданию присваивается так же, как в tsk369_collect:
  1. `source_kind` + `source_task_id` — явный;
  2. `external_uid` вида `ext:(d4|calib):<src>:<дата>:<id>` или `ext:<src>:...`;
  3. `lms:*` — локально-авторское (вероятно вшитые данные, файл может не понадобиться);
  4. иначе — unknown (в остаток оператору).

Схема items совпадает с tsk369_collect — дальше идут те же fetch_files/build_plan/apply.
Read-only: только SELECT с прод-DSN из .mcp.json.

Запуск:
  python scripts/tsk390_build_items.py --out <items.json> [--source kompege]
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
from tsk369_collect import (  # noqa: E402
    _STEM_ID, dsn, normalize_source_id, source_from_words, strip_html,
)

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

FILE_TYPES = ("3", "9", "10", "17", "18", "22", "24", "26", "27")

# «Нет файла-данных»: ни в stem, ни в attached_file_paths/media/code нет ссылки на
# /api/v1/media/<64hex>.<data-ext>. Картинки (png/jpg/…) не считаются.
TARGET_SQL = r"""
WITH scope AS (
  SELECT t.id, t.course_id, t.external_uid, t.max_score,
         c.course_uid,
         coalesce((regexp_match(t.external_uid,'wp_nav:([0-9]+):'))[1],
                  substring(c.course_uid from 'zadanie-([0-9]+)')) AS ege_num,
         t.task_content->>'type'          AS task_type,
         t.task_content->>'stem'          AS stem,
         t.task_content->>'source_kind'   AS source_kind,
         t.task_content->>'source_task_id' AS source_task_id,
         t.task_content->>'source_url'    AS source_url,
         ( ((t.task_content->>'stem')||' '||coalesce(t.task_content->>'attached_file_paths','')||' '
            ||coalesce(t.task_content->>'media','')||' '||coalesce(t.task_content->>'code',''))
            ~* '[0-9a-f]{64}\.(xlsx|xls|ods|csv|txt|odt|docx|doc|zip)'
           -- Файл бывает приложен ПРЯМОЙ внешней ссылкой (авторские задания курса ведут на
           -- victor-komlev.ru/wp-content/uploads/...). Ученику она работает так же. Без этой
           -- ветки 17 авторских заданий попали в кандидаты зря, и одной группе привязался
           -- ЧУЖОЙ файл (03.ods с sdamgia вместо авторского 3.ods) — откачено, tsk-390.
           OR (t.task_content->>'stem')
              ~* 'href="https?://[^"]+\.(xlsx|xls|ods|csv|txt|odt|docx|doc|zip)"'
         ) AS has_data_file
  FROM tasks t
  JOIN courses c ON c.id = t.course_id
  WHERE t.is_active
    AND (c.course_uid LIKE 'wp:zadanie-%' OR c.course_uid LIKE 'lms:tsk347:hard:%' OR c.id IN (138,139))
)
SELECT id, course_id, external_uid, max_score, course_uid, ege_num,
       task_type, stem, source_kind, source_task_id, source_url
FROM scope
WHERE NOT has_data_file AND ege_num = ANY($1::text[])
ORDER BY id
"""


def classify(uid: str, source_kind: str | None, source_task_id: str | None,
             stem: str = "") -> tuple[str | None, str | None, str | None]:
    """(source, source_id, via) — та же логика присвоения источника, что в tsk369_collect."""
    if source_kind and source_task_id:
        return source_kind, normalize_source_id(source_kind, source_task_id), "source_task_id"
    parts = (uid or "").split(":")
    src_tok = next((p for p in parts if p in ("kompege", "polyakov", "sdamgia", "yandex")), None)
    date_ix = next((i for i, p in enumerate(parts) if re.fullmatch(r"20\d{6}", p)), None)
    if uid.startswith("ext:") and src_tok and date_ix is not None:
        return src_tok, ":".join(parts[date_ix + 1:]), "external_uid"
    if uid.startswith("ext:") and src_tok:
        # ext:<src>:<...>:<id> без даты (пилотные партии)
        ix = parts.index(src_tok)
        return src_tok, ":".join(parts[ix + 1:]), "external_uid"
    if uid.startswith("lms:"):
        return "lms-authored", None, "lms"
    # Запасной путь (есть в tsk369_collect, здесь сперва отсутствовал): источник и ID
    # прямо в шапке ТГ-поста — «Задание 17_6757 (Поляков)», «27_21425 (Комп ЕГЭ)».
    # Без него вся партия `tg:ege:*` уходила в unknown, хотя восстановима (tsk-390).
    if stem:
        sid = _STEM_ID.search(stem)
        src = source_from_words(strip_html(stem)[:140])
        if src and sid:
            return src, sid.group(1), "stem"
    return None, None, None


async def main(out_path: Path, source_filter: str | None) -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(TARGET_SQL, list(FILE_TYPES))
    finally:
        await conn.close()

    items = []
    for r in rows:
        uid = r["external_uid"] or ""
        src, sid, via = classify(uid, r["source_kind"], r["source_task_id"], r["stem"] or "")
        if source_filter and src != source_filter:
            continue
        items.append({
            "id": r["id"], "course_id": r["course_id"], "external_uid": uid,
            "family": uid.split(":", 1)[0] if uid else "none",
            "ege_num": r["ege_num"], "task_type": r["task_type"], "max_score": r["max_score"],
            "source_url": r["source_url"], "phrase": None,
            "stem_html": r["stem"], "stem": strip_html(r["stem"]),
            "source": src, "source_id": sid, "via": via, "twins": [],
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")

    def tally(field: str) -> dict[str, int]:
        acc: dict[str, int] = {}
        for it in items:
            acc[str(it[field])] = acc.get(str(it[field]), 0) + 1
        return dict(sorted(acc.items(), key=lambda kv: -kv[1]))

    print(f"Кандидатов (нет файла-данных, типы {','.join(FILE_TYPES)}): {len(items)}")
    print(f"  источник: {tally('source')}")
    print(f"  номер ЕГЭ: {tally('ege_num')}")
    print(f"  как определён источник: {tally('via')}")
    print(f"Сохранено: {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--source", help="оставить только этот источник (kompege/polyakov/yandex/...)")
    a = ap.parse_args()
    asyncio.run(main(Path(a.out), a.source))
