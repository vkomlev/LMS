# -*- coding: utf-8 -*-
"""tsk-384, шаг 3: вписать <img> схемы хода фигуры в stem 5 заданий курса 111.

Привязка figure->task_id ЖЁСТКАЯ (см. FIGURES ниже, та же таблица, что породила
картинки в scripts/tsk384_build_visuals.py) — никакого текстового/fuzzy подбора,
прямая защита от прецедента путаницы фигур tsk-316 (там ладья случайно
сматчилась на пост про ферзя, т.к. эти 5 заданий делят общую преамбулу).

Исходный stem — markdown-подобный plain-text (обратные кавычки = inline code,
`\\n` = перенос строки). SPW рендерит его в HTML автоматически через
plainTextToHtml (components/task/TaskContentRenderer.tsx определяет режим по
наличию HTML-тега в тексте; lib/material/sanitize.ts:171 делает конвертацию).
Как только в тексте появляется `<img>`, режим переключается на HTML — и уже
НИКТО не конвертирует остальные backtick/`\\n`. Поэтому здесь backtick->`<code>`
и `\\n`-> `<br>` делаются вручную, ТЕМ ЖЕ алгоритмом, что plainTextToHtml
(протестировано на реальных 5 стемах — единственное отличие от исходного JS —
нет code-fence/URL-обработки, в этих 5 текстах их нет). Картинка вставляется
сразу после первого абзаца (описание правила хода фигуры) — до
"Программа считывает...".

Запуск:
  python scripts/tsk384_stem_update.py            # dry-run по умолчанию
  DBCHECK_OK=1 python scripts/tsk384_stem_update.py --apply
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
OUT_DIR = project_root / "reviews" / "tsk384-chess-visuals"

FIGURES = {
    182: dict(key="rook", alt="Ладья на шахматной доске 8x8: примеры разрешённых ходов "
                              "по вертикали и горизонтали и запрещённого хода по диагонали"),
    183: dict(key="bishop", alt="Слон на шахматной доске 8x8: примеры разрешённого хода "
                                 "по диагонали и запрещённого хода не по диагонали"),
    184: dict(key="king", alt="Король на шахматной доске 8x8: примеры разрешённых ходов "
                              "на одну клетку и запрещённого хода на две клетки"),
    185: dict(key="queen", alt="Ферзь на шахматной доске 8x8: примеры разрешённых ходов "
                               "по вертикали и диагонали и запрещённого хода"),
    186: dict(key="knight", alt="Конь на шахматной доске 8x8: примеры разрешённых ходов "
                                "буквой Г и запрещённого хода по диагонали"),
}


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


def load_stored() -> dict[str, str]:
    """key ("rook") -> sha_ext, из stored.json шага 2 (уже проверен публично доступным)."""
    data = json.loads((OUT_DIR / "stored.json").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for item in data["stored"]:
        out[item["key"]] = item["sha_ext"]
    if failed := data.get("failed"):
        raise RuntimeError(f"stored.json содержит неудачные загрузки: {failed}")
    if len(out) != 5:
        raise RuntimeError(f"ожидал 5 записей в stored.json, нашёл {len(out)}")
    return out


def _escape_html(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))


_INLINE_CODE_RE = re.compile(r"`([^`\n]+?)`")


def stem_plain_to_html(raw: str) -> str:
    """Зеркало lib/material/sanitize.ts::plainTextToHtml для plain-контента без
    code-fence/URL (в этих 5 стемах их нет — проверено по прод-данным)."""
    stash: list[str] = []

    def _stash(m: "re.Match[str]") -> str:
        stash.append(f"<code>{_escape_html(m.group(1))}</code>")
        return f"\x03INL_{len(stash) - 1}\x04"

    with_inline = _INLINE_CODE_RE.sub(_stash, raw)
    out = _escape_html(with_inline)
    out = out.replace("\n", "<br>")

    def _restore(m: "re.Match[str]") -> str:
        return stash[int(m.group(1))]

    return re.sub(r"\x03INL_(\d+)\x04", _restore, out)


def transform_stem(raw: str, sha_ext: str, alt: str) -> str:
    html_full = stem_plain_to_html(raw)
    if "<br><br>" not in html_full:
        raise AssertionError("не нашёл границу первого абзаца (<br><br>) в сконвертированном stem")
    idx = html_full.index("<br><br>")
    before, after = html_full[:idx], html_full[idx:]  # after начинается с "<br><br>"
    img_tag = f'<img src="/api/v1/media/{sha_ext}" alt="{_escape_html(alt)}"/>'
    return before + "<br><br>" + img_tag + after


async def main(apply: bool) -> None:
    stored = load_stored()
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            ids = list(FIGURES.keys())
            rows = await conn.fetch(
                "SELECT id, task_content FROM tasks "
                "WHERE id = ANY($1::int[]) AND course_id = 111 AND is_active = true FOR UPDATE",
                ids,
            )
            if len(rows) != len(ids):
                found = {r["id"] for r in rows}
                raise AssertionError(f"ожидал {len(ids)} активных задач курса 111, нашёл {len(rows)}; "
                                      f"нет: {sorted(set(ids) - found)}")

            updates: list[tuple[int, dict]] = []
            for row in rows:
                tid = row["id"]
                meta = FIGURES[tid]
                sha_ext = stored[meta["key"]]
                content = json.loads(row["task_content"]) if isinstance(row["task_content"], str) else dict(row["task_content"])
                stem = content.get("stem", "")
                if "/api/v1/media/" in stem:
                    raise AssertionError(f"id={tid}: в stem уже есть /api/v1/media/ — повторный запуск?")
                new_stem = transform_stem(stem, sha_ext, meta["alt"])
                content = dict(content)
                content["stem"] = new_stem
                updates.append((tid, content))

            print(f"Задач к обновлению: {len(updates)}")
            for tid, content in updates:
                print(f"  id={tid} ({FIGURES[tid]['key']})\n    -> {content['stem'][:220]}...")

            for tid, content in updates:
                await conn.execute(
                    "UPDATE tasks SET task_content = $1::jsonb WHERE id = $2",
                    json.dumps(content, ensure_ascii=False), tid,
                )

            verify = await conn.fetch(
                "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY($1::int[])",
                ids,
            )
            bad = []
            for r in verify:
                tid = r["id"]
                sha_ext = stored[FIGURES[tid]["key"]]
                stem = r["stem"] or ""
                if f"/api/v1/media/{sha_ext}" not in stem:
                    bad.append((tid, "нет своей картинки"))
                # перекрёстная проверка: чужая sha этого задания НЕ должна встречаться
                for other_tid, other_meta in FIGURES.items():
                    if other_tid == tid:
                        continue
                    other_sha = stored[other_meta["key"]]
                    if other_sha in stem:
                        bad.append((tid, f"нашёл чужую картинку {other_meta['key']} ({other_tid})"))
            if bad:
                raise AssertionError(f"проверка внутри транзакции не прошла: {bad}")
            print(f"\nВнутри транзакции: {len(verify)}/{len(ids)} — своя картинка на месте, "
                  f"чужих не найдено (перекрёстная проверка по всем 5 парам).")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after_rows = await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY($1::int[])",
            list(FIGURES.keys()),
        )
        problems = []
        for r in after_rows:
            tid = r["id"]
            sha_ext = stored[FIGURES[tid]["key"]]
            if f"/api/v1/media/{sha_ext}" not in (r["stem"] or ""):
                problems.append(tid)
        print(f"  проверено построчно: {len(after_rows)}; расхождений: {len(problems)}")
        if problems:
            print(f"  ПРОБЛЕМНЫЕ: {problems}")
            sys.exit(1)
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
