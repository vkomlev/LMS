"""Замена формул-картинок sdamgia (<img class="tex">) в условиях заданий текстом (tsk-1126).

sdamgia рисует даже короткие выражения («20√3», «F(n) = n») SVG-картинкой; в кабинете
она выпадает на отдельную строку и рвёт текст условия. Скрипт меняет каждую такую
картинку на эквивалентный текст по точной карте alt → HTML. Меняется только
task_content.stem; ответы (solution_rules) не трогаются.

Запуск на прод-сервере (.env с прод-DSN), под app:
    ./venv/bin/python scripts/fix_sdamgia_tex_img_tsk1126.py            # dry-run
    DBCHECK_OK=1 ./venv/bin/python scripts/fix_sdamgia_tex_img_tsk1126.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys

from sqlalchemy import text

from app.db.session import async_session_factory

logger = logging.getLogger("tsk1126")

IMG_RE = re.compile(r'<img\b[^>]*class="tex"[^>]*/?>')
ALT_RE = re.compile(r'alt="([^"]*)"')

SUB = "<sub>{}</sub>".format
# Ключ — alt без мягких переносов и крайних пробелов.
REPLACEMENTS: dict[str, str] = {
    r"1536 \times 1024": "1536 × 1024",
    "43 плюс 90 плюс 72 плюс 30 плюс 36 плюс 63 плюс 61 плюс 84 плюс 49 плюс 51 = 579.":
        "43 + 90 + 72 + 30 + 36 + 63 + 61 + 84 + 49 + 51 = 579.",
    "45 плюс левая круглая скобка 54–45 правая круглая скобка плюс левая круглая скобка 54–46 правая круглая скобка плюс левая круглая скобка 46–27 правая круглая скобка плюс":
        "45 + (54 – 45) + (54 – 46) + (46 – 27) + ",
    "плюс левая круглая скобка 71–27 правая круглая скобка плюс левая круглая скобка 82–71 правая круглая скобка плюс левая круглая скобка 82–48 правая круглая скобка = 170.":
        " + (71 – 27) + (82 – 71) + (82 – 48) = 170.",
    "45 плюс левая круглая скобка 54–45 правая круглая скобка плюс левая круглая скобка 54–46 правая круглая скобка плюс левая круглая скобка 46–27 правая круглая скобка плюс левая круглая скобка 71–27 правая круглая скобка плюс левая круглая скобка 82–71 правая круглая скобка плюс левая круглая скобка 82–48 правая круглая скобка = 170.":
        "45 + (54 – 45) + (54 – 46) + (46 – 27) + (71 – 27) + (82 – 71) + (82 – 48) = 170.",
    "20 корень из: начало аргумента: 3 конец аргумента": "20√3",
    "левая круглая скобка x плюс a, y плюс b правая круглая скобка":
        "(<i>x</i> + <i>a</i>, <i>y</i> + <i>b</i>)",
    "F левая круглая скобка n правая круглая скобка =F левая круглая скобка n — 1 правая круглая скобка плюс n.":
        "<i>F</i>(<i>n</i>) = <i>F</i>(<i>n</i> − 1) + <i>n</i>.",
    "F левая круглая скобка 0 правая круглая скобка = 0,": "<i>F</i>(0) = 0,",
    "F левая круглая скобка n правая круглая скобка = 121.": "<i>F</i>(<i>n</i>) = 121.",
    "F левая круглая скобка n правая круглая скобка = n,": "<i>F</i>(<i>n</i>) = <i>n</i>,",
    "n меньше 9,": "<i>n</i> &lt; 9,",
    "F левая круглая скобка n правая круглая скобка = F левая круглая скобка n mod 9 правая круглая скобка плюс F левая круглая скобка n div 9 правая круглая скобка ,":
        "<i>F</i>(<i>n</i>) = <i>F</i>(<i>n</i> mod 9) + <i>F</i>(<i>n</i> div 9),",
    "n больше = 9.": "<i>n</i> ≥ 9.",
    "d левая круглая скобка A, B правая круглая скобка = корень из: начало аргумента: левая круглая скобка левая круглая скобка x_2 минус x_1 правая круглая скобка в квадрате плюс левая круглая скобка y_2 минус y_1 правая круглая скобка в квадрате правая круглая скобка конец аргумента .":
        "<i>d</i>(<i>A</i>, <i>B</i>) = √((<i>x</i>" + SUB(2) + " − <i>x</i>" + SUB(1)
        + ")<sup>2</sup> + (<i>y</i>" + SUB(2) + " − <i>y</i>" + SUB(1) + ")<sup>2</sup>).",
    "левая круглая скобка N больше M правая круглая скобка .": "(<i>N</i> &gt; <i>M</i>).",
    "левая круглая скобка N меньше или равно 10 000 правая круглая скобка":
        "(<i>N</i> ≤ 10 000)",
}


def norm_alt(alt: str) -> str:
    """Убрать мягкие переносы и крайние пробелы из alt."""
    return alt.replace("­", "").strip()


def convert(stem: str) -> tuple[str, list[str]]:
    """Заменить все tex-картинки; вернуть новый stem и список нераспознанных alt."""
    unknown: list[str] = []

    def repl(m: re.Match[str]) -> str:
        alt_m = ALT_RE.search(m.group(0))
        key = norm_alt(alt_m.group(1)) if alt_m else ""
        if key not in REPLACEMENTS:
            unknown.append(key)
            return m.group(0)
        return REPLACEMENTS[key]

    return IMG_RE.sub(repl, stem), unknown


async def main(apply: bool) -> int:
    """Прочитать класс, показать план, при --apply записать в одной транзакции."""
    async with async_session_factory() as s:
        rows = (await s.execute(text(
            "SELECT id, task_content->>'stem' FROM tasks "
            "WHERE task_content->>'stem' LIKE '%class=\"tex\"%' ORDER BY id FOR UPDATE"
        ))).all()
        logger.info("Заданий в классе: %d", len(rows))
        plan: list[tuple[int, str]] = []
        for tid, stem in rows:
            new, unknown = convert(stem)
            if unknown:
                logger.error("id=%s: нераспознанные формулы %s — стоп", tid, unknown)
                return 2
            plan.append((tid, new))
            logger.info("id=%s: %d формул → текст", tid, len(IMG_RE.findall(stem)))
        if not apply:
            logger.info("dry-run: запись не выполнялась")
            return 0
        for tid, new in plan:
            await s.execute(text(
                "UPDATE tasks SET task_content = jsonb_set(task_content, '{stem}', "
                "CAST(:v AS jsonb)), updated_at = now() WHERE id = :id"
            ), {"v": json.dumps(new, ensure_ascii=False), "id": tid})
        left = (await s.execute(text(
            "SELECT count(*) FROM tasks WHERE task_content->>'stem' LIKE '%class=\"tex\"%'"
        ))).scalar_one()
        if left:
            await s.rollback()
            logger.error("После правки осталось %d — ROLLBACK", left)
            return 3
        await s.commit()
        logger.info("COMMIT: исправлено %d заданий", len(plan))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    sys.exit(asyncio.run(main(p.parse_args().apply)))
