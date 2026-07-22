# -*- coding: utf-8 -*-
"""tsk-371: убрать из условия затащенное решение с готовым ответом (3765, 3766, 3767).

ЧТО БЫЛО
У трёх заданий в `task_content.stem` лежал сырой кусок страницы «Решу ЕГЭ»: служебная шапка
(«Тип 20 № 28088 Раздел кодификатора ФИПИ …»), само условие, а следом — блок «Решение» с
программой на Python и строкой «Ответ: 1325», плюс «Аналоги к заданию» и кнопки
«Спрятать/Показать решение». Ученик видел верный ответ прямо в условии — задание не работало
как задание. Нашлось при разборе tsk-371 (сверка ответов sdamgia); в проде таких ровно три.

ЧТО ДЕЛАЕТ
Берёт со страницы источника блок ИМЕННО этой задачи (`sdamgia_block` — привязка по хвосту
условия, см. tsk-369/tsk-371) и оставляет из него только условие: всё от начала блока до
`<div class="answer">`. Дополнительно вырезает служебную шапку `prob_nums` («Тип N № …»)
и строку раздела кодификатора — ученику они не нужны.

Разметка условия (таблицы, картинки, формулы) сохраняется как есть: переписывается только
граница, а не сам текст.

dry-run по умолчанию: печатает «было/стало» по каждому заданию. `--apply` при DBCHECK_OK=1,
бэкап прежних условий до записи, построчная проверка после COMMIT.

Запуск: python scripts/tsk371_strip_solution_stem.py --backup <файл.json> [--apply]
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk369_collect import dsn  # noqa: E402
from tsk369_fetch_files import fetch, sdamgia_block, strip_html  # noqa: E402

# id задания в LMS → id страницы источника
TARGETS: dict[int, str] = {3765: "28087", 3766: "28093", 3767: "28099"}

from html.parser import HTMLParser  # noqa: E402

# Классы узлов страницы источника, которые целиком выбрасываются.
_DROP_CLASSES = ("prob_nums", "align-left", "solution", "answer", "minor", "expand")
_VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "source"}


class _ConditionExtractor(HTMLParser):
    """Оставляет из блока задачи только условие.

    Резать регуляркой здесь нельзя по двум причинам. Во-первых, «Ответ: 1325» встречается
    не только в `<div class="answer">`, но и внутри разбора (`class="solution"`), который
    идёт ДО него. Во-вторых, раздел кодификатора спрятан через `style="display:none"` —
    на странице источника он не виден, но санитайзер SPW вырезает `style`, и в LMS этот
    текст стал бы видимым. Поэтому узлы отбрасываются по дереву: и служебные классы,
    и всё скрытое.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self._skip_depth = 0
        self._depth_stack: list[str] = []

    def _is_junk(self, attrs: list[tuple[str, str | None]]) -> bool:
        a = {k: (v or "") for k, v in attrs}
        if any(c in a.get("class", "") for c in _DROP_CLASSES):
            return True
        if "display:none" in a.get("style", "").replace(" ", ""):
            return True
        # Интерактив самого сайта-источника: кнопки «решить», «сообщить об ошибке»,
        # значки-подсказки. В LMS они бессмысленны, а после санитайзера превращаются
        # в осколки разметки посреди условия.
        if "onclick" in a:
            return True
        return a.get("src", "").startswith("/img/")

    def handle_starttag(self, tag, attrs):
        if self._skip_depth:
            if tag not in _VOID_TAGS:
                self._skip_depth += 1
            return
        if self._is_junk(attrs):
            if tag not in _VOID_TAGS:
                self._skip_depth = 1
            return
        if tag not in _VOID_TAGS:
            self._depth_stack.append(tag)
        attr_html = "".join(f' {k}="{v}"' for k, v in attrs if v is not None)
        self.out.append(f"<{tag}{attr_html}>")

    def handle_endtag(self, tag):
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if self._depth_stack and tag in self._depth_stack:
            while self._depth_stack:
                if self._depth_stack.pop() == tag:
                    break
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._skip_depth:
            self.out.append(data)

    def handle_entityref(self, name):
        if not self._skip_depth:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self._skip_depth:
            self.out.append(f"&#{name};")


def clean_condition(block_html: str) -> str:
    """Условие задачи без разбора, ответа и служебной шапки источника."""
    p = _ConditionExtractor()
    p.feed(block_html)
    p.close()
    html = "".join(p.out)
    html = re.sub(r"(?is)<style[^>]*>.*?</style>", "", html)  # css источника
    html = re.sub(r"^\s*(?:</[a-z]+>\s*)+", "", html)         # осколки после выброшенных узлов
    html = re.sub(r"(?:\s*<div[^>]*>\s*)+$", "", html)        # пустые обёртки в хвосте
    html = re.sub(r"(?:\s*</div>\s*)+$", "", html)
    html = re.sub(r"^\s*(?:<div[^>]*>\s*)+", "", html)
    return html.strip()


async def main(backup_path: Path, apply: bool) -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = {r["id"]: r for r in await conn.fetch(
            "SELECT id, external_uid, is_active, task_content->>'stem' AS stem "
            "FROM tasks WHERE id = ANY($1::int[])", list(TARGETS))}
        missing = sorted(set(TARGETS) - set(rows))
        if missing:
            raise RuntimeError(f"не нашёл заданий: {missing}")

        new_stems: dict[int, str] = {}
        for tid, sid in TARGETS.items():
            page = fetch(f"https://inf-ege.sdamgia.ru/problem?id={sid}")
            block = sdamgia_block(page, sid, strip_html(rows[tid]["stem"]))
            condition = clean_condition(block)
            plain = strip_html(condition)
            if len(plain) < 300:
                raise RuntimeError(f"id={tid}: условие вышло подозрительно коротким ({len(plain)})")
            for bad in ("Ответ:", "Аналоги к заданию", "Спрятать решение"):
                if bad in plain:
                    raise RuntimeError(f"id={tid}: в очищенном условии остался «{bad}»")
            new_stems[tid] = condition
            was = strip_html(rows[tid]["stem"])
            print(f"\n=== id={tid} (sdamgia:{sid})")
            print(f"  было {len(was)} знаков → стало {len(plain)}")
            print(f"  БЫЛО, хвост: …{was[-160:]}")
            print(f"  СТАЛО, хвост: …{plain[-160:]}")

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            [{"id": i, "external_uid": rows[i]["external_uid"], "stem": rows[i]["stem"]}
             for i in sorted(TARGETS)], ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nБэкап прежних условий: {backup_path}")

        async with conn.transaction():
            for tid, stem in new_stems.items():
                await conn.execute(
                    "UPDATE tasks SET task_content = "
                    "  jsonb_set(task_content, '{stem}', to_jsonb($2::text)) WHERE id = $1",
                    tid, stem)
            check = {r["id"]: r["stem"] for r in await conn.fetch(
                "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY($1::int[])",
                list(TARGETS))}
            bad = [i for i in TARGETS if "Ответ:" in strip_html(check[i] or "")]
            if bad:
                raise AssertionError(f"в условии остался ответ: {bad}")
            print(f"Внутри транзакции: обновлено и проверено {len(TARGETS)} заданий.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after = await conn.fetch(
            "SELECT id, length(task_content->>'stem') AS len, "
            "       (task_content->>'stem') LIKE '%Аналоги к заданию%' AS has_analogs "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id", list(TARGETS))
        for r in after:
            print(f"  id={r['id']}: длина условия {r['len']}, остатки страницы источника: "
                  f"{'ЕСТЬ' if r['has_analogs'] else 'нет'}")
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
