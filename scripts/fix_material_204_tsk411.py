# -*- coding: utf-8 -*-
"""tsk-411 (остаток): вручную вырезать оба вставленных блока заданий из материала id=204
«Числовые операции и операторы» (курс 103) — единственный материал курса 88, где хвостовые
блоки WP-парсинга вставлены в ДВА разных места документа (не одна точка среза, поэтому
общий скрипт trim_wp_artifact_tail_tsk411.py его пропустил как IRREGULAR).

Блок A (середина документа, внутри <ol> с практическими примерами): заголовок
«Задания 2-4 на числовые операции в Python» + 3 blockquote.check — вырезается, ЗАКРЫВАЮЩИЕ
теги </li></ol> самого списка теории остаются на месте (список практических примеров не
трогаем).
Блок B (хвост документа): заголовок «Задание 5 на деление в Python» + blockquote.check +
иллюстрация вывода — вырезается целиком до конца текста (обычный хвостовой паттерн).

Запуск: dry-run по умолчанию;
  python scripts/fix_material_204_tsk411.py
  DBCHECK_OK=1 python scripts/fix_material_204_tsk411.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
MATERIAL_ID = 204

BLOCK_A_START = '<h3><strong>Задания 2-4'
BLOCK_A_END = '<h3>Чем отличаются операции деления'
TAIL_MARKER = '<h3><strong>Задание 5 на деление в Python</strong></h3>'


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


def build_new_text(text: str) -> str:
    assert text.count(BLOCK_A_START) == 1, f"начало блока A встречается {text.count(BLOCK_A_START)} раз, ожидался 1"
    assert text.count(BLOCK_A_END) == 1, f"конец блока A встречается {text.count(BLOCK_A_END)} раз, ожидался 1"
    assert text.count(TAIL_MARKER) == 1, f"хвостовой маркер встречается {text.count(TAIL_MARKER)} раз, ожидался 1"

    a_start = text.index(BLOCK_A_START)
    a_end = text.index(BLOCK_A_END)
    assert a_start < a_end, "конец блока A встретился раньше начала"
    # заголовок «Задания 2-4» + 3 blockquote заменяются на закрывающие </li></ol> самого
    # списка теории (они были смещены внутрь удаляемого блока в исходном HTML)
    without_a = text[:a_start] + "</li>\n</ol>\n" + text[a_end:]

    tail_pos = without_a.index(TAIL_MARKER)
    kept = without_a[:tail_pos].rstrip()

    assert "Задание 2" not in kept
    assert "Задание 3" not in kept
    assert "Задание 4" not in kept
    assert "Задание 5" not in kept
    assert "blockquote class=\"check\"" not in kept
    assert "Чем отличаются операции деления" in kept  # реальная теория между блоками сохранена
    assert "Число 7 нечетное" in kept  # конец теории (последний пример перед хвостом) сохранён
    return kept


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, course_id, title, is_active, content FROM materials WHERE id = $1",
                MATERIAL_ID,
            )
            if row is None:
                raise AssertionError(f"материал {MATERIAL_ID} не найден")

            content = json.loads(row["content"]) if isinstance(row["content"], str) else dict(row["content"])
            text = content.get("text", "")
            new_text = build_new_text(text)

            print(f"ДО:    длина={len(text)}")
            print(f"ПОСЛЕ: длина={len(new_text)} (-{len(text) - len(new_text)})")
            print(f"\n...конец сохраняемого текста:\n...{new_text[-300:]}")

            if apply:
                new_content = dict(content)
                new_content["text"] = new_text
                await conn.execute(
                    "UPDATE materials SET content = $1::jsonb, updated_at = now() WHERE id = $2",
                    json.dumps(new_content, ensure_ascii=False), MATERIAL_ID,
                )
                after = await conn.fetchval(
                    "SELECT length(content->>'text') FROM materials WHERE id = $1", MATERIAL_ID
                )
                if after != len(new_text):
                    raise AssertionError(f"после UPDATE длина={after}, ожидалось {len(new_text)}")
                print(f"\nПроверка после UPDATE: длина={after} OK")

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
