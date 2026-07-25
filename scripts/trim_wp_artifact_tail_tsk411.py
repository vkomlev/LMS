# -*- coding: utf-8 -*-
"""tsk-411: срезать хвост-артефакт WP-парсинга («Вопросы» + «Задания для закрепления»
со спойлерами) из материалов курса 88 «Python для ЕГЭ» и его подтем (103-111).

ПОЧЕМУ
В LMS задание — отдельная сущность `tasks` с автопроверкой, а не текст в материале.
Материалы унаследовали от старого WP-курса хвостовой блок: заголовок «Вопросы» и/или
«Задания для закрепления/на закрепление/для тренировки» + список <blockquote class="check">
(иногда со спрятанным решением [spoiler]...[/spoiler]). Для каждого курса 103-111
подтверждено наличие полноценных `tasks` (26-57 штук на курс) с современными
переформулированными условиями и автопроверкой — блок в материале дублирует их по сути,
не текстуально.

ГАРАНТИИ (не режем вслепую)
Скрипт классифицирует материал на CLEAN (единственный хвостовой блок в конце документа,
безопасно срезать) и IRREGULAR (хвостовые блоки чередуются с настоящей теорией дальше по
документу — как в материале 204, где после первого блока «Задания» снова идёт теория, а
задания встречаются ещё раз в конце). IRREGULAR не трогаем, только перечисляем в отчёте —
для них нужна ручная разметка, не авто-срез.

Алгоритм cut_pos для CLEAN:
  1. Найти все заголовки h2/h3/h4 (текст очищен от вложенных тегов).
  2. tail-заголовок — текст матчит /Вопрос|Задани|Задач/i.
  3. non_tail = все остальные заголовки. Если non_tail есть — cut_pos = начало ПЕРВОГО
     tail-заголовка, идущего сразу после ПОСЛЕДНЕГО non_tail-заголовка. Если после этого
     non_tail-заголовка встречается ещё один non_tail-заголовок (теория после хвоста) —
     материал IRREGULAR.
  4. Если non_tail заголовков нет вообще — cut_pos = позиция первого
     <blockquote class="check" (единственный случай без заголовка над списком заданий).
  5. Санити: kept-текст (то что останется) должен быть длиннее 150 символов и короче
     исходного — иначе IRREGULAR (не режем).

Запуск: dry-run по умолчанию (ничего не пишет, всегда ROLLBACK);
  python scripts/trim_wp_artifact_tail_tsk411.py
  DBCHECK_OK=1 python scripts/trim_wp_artifact_tail_tsk411.py --apply
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

# курс 88 «Python для ЕГЭ» -> подтемы 103-111 (курс 88 сам материалов не содержит)
CANDIDATE_IDS = [
    204, 206, 208,
    214, 215, 221, 222,
    226, 227, 229,
    239, 240, 241,
    248, 250, 251, 253, 254,
    260, 261, 262, 263, 264,
    269, 270, 271, 272, 273, 274, 275, 276, 278, 279,
    286, 287, 288, 289,
    297, 298, 299, 300,
]

HEADING_RE = re.compile(r"<h([234])[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
TAIL_WORD_RE = re.compile(r"(Вопрос|Задани|Задач)", re.IGNORECASE)
BLOCKQUOTE_CHECK_RE = re.compile(r'<blockquote\s+class="check"', re.IGNORECASE)


def _dsn() -> str:
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


def classify(text: str) -> dict:
    headings = []
    for m in HEADING_RE.finditer(text):
        plain = TAG_RE.sub("", m.group(2)).strip()
        headings.append({"start": m.start(), "text": plain, "tail": bool(TAIL_WORD_RE.search(plain))})

    non_tail = [h for h in headings if not h["tail"]]
    bq_matches = list(BLOCKQUOTE_CHECK_RE.finditer(text))

    if not bq_matches and not any(h["tail"] for h in headings):
        return {"status": "SKIP_NO_ARTIFACT", "cut_pos": None}

    boundary = non_tail[-1]["start"] if non_tail else -1
    after = [h for h in headings if h["start"] > boundary]

    if after:
        if not after[0]["tail"]:
            return {"status": "IRREGULAR", "reason": "после последней теории снова нетематический заголовок",
                     "cut_pos": None}
        # ирегулярно, если среди хвостовых заголовков затесался ещё один теоретический
        if any(not h["tail"] for h in after):
            return {"status": "IRREGULAR", "reason": "теория чередуется с хвостом заданий (как id=204)",
                     "cut_pos": None}
        cut_pos = after[0]["start"]
    elif bq_matches:
        cut_pos = bq_matches[0].start()
    else:
        return {"status": "IRREGULAR", "reason": "нет ни tail-заголовков после теории, ни blockquote check",
                 "cut_pos": None}

    kept = text[:cut_pos].rstrip()
    if len(kept) < 150 or len(kept) >= len(text):
        return {"status": "IRREGULAR", "reason": f"kept слишком короткий/некорректный ({len(kept)} симв.)",
                 "cut_pos": None}

    # доп. проверка: внутри оставляемого текста не должно быть СВОЕГО tail-заголовка —
    # иначе это материал с несколькими вставками заданий по документу (как id=204),
    # одного cut_pos недостаточно, одну "точку среза" находить некорректно.
    earlier_tail = [h for h in headings if h["tail"] and h["start"] < cut_pos]
    if earlier_tail:
        return {"status": "IRREGULAR",
                 "reason": f"есть более ранний tail-заголовок «{earlier_tail[0]['text'][:40]}» "
                           f"внутри сохраняемой части — несколько вставок заданий по документу",
                 "cut_pos": None}
    if BLOCKQUOTE_CHECK_RE.search(kept):
        return {"status": "IRREGULAR", "reason": "blockquote check остался внутри сохраняемой части",
                 "cut_pos": None}

    return {"status": "CLEAN", "cut_pos": cut_pos, "kept": kept}


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id, course_id, title, is_active, content FROM materials "
                "WHERE id = ANY($1::int[]) ORDER BY course_id, id",
                CANDIDATE_IDS,
            )
            if len(rows) != len(CANDIDATE_IDS):
                raise AssertionError(f"нашлось {len(rows)} из {len(CANDIDATE_IDS)}")

            clean, irregular, skip = [], [], []
            for r in rows:
                content = json.loads(r["content"]) if isinstance(r["content"], str) else dict(r["content"])
                text = content.get("text", "")
                result = classify(text)
                result.update(id=r["id"], course_id=r["course_id"], title=r["title"],
                               is_active=r["is_active"], content=content, orig_len=len(text))
                if result["status"] == "CLEAN":
                    clean.append(result)
                elif result["status"] == "IRREGULAR":
                    irregular.append(result)
                else:
                    skip.append(result)

            print(f"=== CLEAN ({len(clean)}) — безопасно срезать хвост ===")
            for r in clean:
                removed = r["orig_len"] - len(r["kept"])
                tail_preview = r["content"]["text"][r["cut_pos"]:r["cut_pos"] + 120].replace("\n", " ")
                kept_tail_preview = r["kept"][-120:].replace("\n", " ")
                print(f"id={r['id']:>4} курс={r['course_id']:>3} «{r['title'][:40]}» "
                      f"было={r['orig_len']} станет={len(r['kept'])} (-{removed})")
                print(f"    ...конец оставляемого: ...{kept_tail_preview}")
                print(f"    срезаемое начинается:   {tail_preview}...")

            print(f"\n=== IRREGULAR ({len(irregular)}) — НЕ трогаем, нужен ручной разбор ===")
            for r in irregular:
                print(f"id={r['id']:>4} курс={r['course_id']:>3} «{r['title'][:40]}»: {r['reason']}")

            print(f"\n=== SKIP_NO_ARTIFACT ({len(skip)}) — паттерн не найден при повторной проверке ===")
            for r in skip:
                print(f"id={r['id']:>4} курс={r['course_id']:>3} «{r['title'][:40]}»")

            if apply:
                for r in clean:
                    new_content = dict(r["content"])
                    new_content["text"] = r["kept"]
                    await conn.execute(
                        "UPDATE materials SET content = $1::jsonb, updated_at = now() WHERE id = $2",
                        json.dumps(new_content, ensure_ascii=False), r["id"],
                    )
                after = await conn.fetch(
                    "SELECT id, length(content->>'text') AS len FROM materials WHERE id = ANY($1::int[])",
                    [r["id"] for r in clean],
                )
                after_map = {a["id"]: a["len"] for a in after}
                print(f"\n=== ПРОВЕРКА ПОСЛЕ UPDATE ===")
                bad = 0
                for r in clean:
                    ok = after_map[r["id"]] == len(r["kept"])
                    if not ok:
                        bad += 1
                    print(f"id={r['id']:>4} ожидалось={len(r['kept'])} факт={after_map[r['id']]} "
                          f"{'OK' if ok else 'MISMATCH'}")
                if bad:
                    raise AssertionError(f"{bad} материалов не совпали после UPDATE — откатываю всё")
                print(f"\nОбновлено материалов: {len(clean)}")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
    except AssertionError as exc:
        print(f"\nОШИБКА ПРОВЕРКИ: {exc}")
        sys.exit(1)
