# -*- coding: utf-8 -*-
"""tsk-210 добор: ASCII-контраст на две «сводные» страницы, различающие путаемые понятия.
3077 (тип данных / объект / модель) и 3140 (тип сети / часть адреса)."""
import asyncio
import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import asyncpg

PRE_OPEN = ('\n<pre class="cb-ascii" style="background:#f5f5f5;color:#1a1a1a;'
            'padding:12px;border-radius:6px;overflow:auto">')
PRE_CLOSE = "</pre>"

DATA = [
 (3077, "про какой из трёх уровней спрашивают.</p>", r"""Три слова про базы данных — три РАЗНЫХ уровня:

  ТИП ДАННЫХ    -> одна клетка: что лежит в поле
                   (Текстовый, Числовой, Логический)
  ОБЪЕКТ БД     -> часть базы Access:
                   таблица, запрос, форма, отчёт
  МОДЕЛЬ ДАННЫХ -> форма всей базы:
                   иерархическая, сетевая, реляционная

  клетка   <   часть базы   <   вся база"""),

 (3140, "про какой из двух наборов вопрос.</p>", r"""Два набора слов темы «Сети» — их путают:

  ТИП СЕТИ — масштаб (как далеко раскинута):
     персональная -> локальная -> региональная -> глобальная

  ЧАСТИ АДРЕСА — строение записи, по которой находят узел:
     протокол, доменное имя, домен верхнего уровня, IP-адрес

  Тип сети — про РАЗМЕР.   Часть адреса — про СТРОЕНИЕ."""),
]


def _dsn() -> str:
    cfg = json.loads(Path(".mcp.json").read_text(encoding="utf-8"))
    args = cfg["mcpServers"]["learn_prod_db"]["args"]
    return next(a for a in args if a.startswith("postgres")).replace("postgresql+asyncpg://", "postgresql://")


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            for mid, anchor, ascii_raw in DATA:
                text = await conn.fetchval("SELECT content->>'text' FROM materials WHERE id=$1", mid)
                if text is None or "cb-ascii" in text:
                    raise RuntimeError(f"мат {mid}: нет или уже с cb-ascii")
                if text.count(anchor) != 1:
                    raise RuntimeError(f"мат {mid}: якорь != 1")
                block = PRE_OPEN + escape(ascii_raw) + PRE_CLOSE
                await conn.execute(
                    "UPDATE materials SET content = jsonb_set(content,'{text}', to_jsonb($2::text)) WHERE id=$1",
                    mid, text.replace(anchor, anchor + block))
                chk = await conn.fetchval("SELECT content->>'text' FROM materials WHERE id=$1", mid)
                assert "cb-ascii" in chk
                print(f"OK мат {mid}: +{len(block)} симв. cb-ascii")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю")
        print("\nЗАПИСАНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main("--apply" in sys.argv))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
