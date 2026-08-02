# -*- coding: utf-8 -*-
"""tsk-526 (пункт 3): гиперссылка на прошлую тему в материале zadanie-14#m2.

Материал id=2677 (course_id=1179, external_uid=authored:oge-informatika:zadanie-14#m2,
"Что нужно знать") упоминает по имени курс «Информатика 5-11»: Табличный процессор и
Инструменты анализа данных, но без <a href> — ученик не может перейти кликом.

Источник контента ContentBackbone (content_hub.material global_uid=zadanie-14) для
этого же абзаца уже содержит рабочие ссылки, но ведущие на WP-страницы, а не на узлы
LMS-графа — их нельзя использовать напрямую в LMS-материале (другой домен навигации).

Узлы 1043 (wp:inf-11-g1-t2, "1.2. Редактирование и форматирование в таблицах"),
1044 (wp:inf-11-g1-t3), 1045 (wp:inf-11-g1-t4, "1.4. Инструменты анализа данных")
уже прикреплены как дочерние курса 1179 (course_parents, подтверждено /db-check).
Узел 1042 ("1.1. Табличный процессор") НЕ прикреплён — недостижим из графа 1179.
Решение оператора (tsk-426 интерактивный разбор): один deep-link на ПЕРВЫЙ из
прикреплённых узлов (1043, course_uid=wp:inf-11-g1-t2) — точка входа, откуда ученик
дальше идёт по порядку 1043→1044→1045 (все три уже в дереве курса).

SPW-формат deep-link на курс: /courses/${encodeURIComponent(course_uid)}
(app/(authed)/courses/page.tsx:73) → /courses/wp%3Ainf-11-g1-t2

Запуск: dry-run по умолчанию;
  python scripts/tsk526_link_zadanie14_m2.py
  DBCHECK_OK=1 python scripts/tsk526_link_zadanie14_m2.py --apply
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

MATERIAL_ID = 2677
OLD_PHRASE = "курс «Информатика 5-11»: Табличный процессор и Инструменты анализа данных"
NEW_PHRASE = (
    '<a href="/courses/wp%3Ainf-11-g1-t2">курс «Информатика 5-11»: '
    "Табличный процессор и Инструменты анализа данных</a>"
)


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


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, course_id, external_uid, is_active, content "
                "FROM materials WHERE id = $1",
                MATERIAL_ID,
            )
            if row is None:
                raise AssertionError(f"материал {MATERIAL_ID} не найден")
            if row["course_id"] != 1179:
                raise AssertionError(f"ожидал course_id=1179, нашёл {row['course_id']}")
            if not row["is_active"]:
                raise AssertionError("материал неактивен — не тот, что показывается ученику")

            # Узлы деревьев — те самые, что использует NEW_PHRASE. Проверяем перед записью,
            # чтобы deep-link не указывал в никуда, если граф изменится.
            attached = await conn.fetch(
                "SELECT course_id FROM course_parents WHERE parent_course_id = 1179 "
                "AND course_id IN (1043,1044,1045) ORDER BY order_number"
            )
            attached_ids = [r["course_id"] for r in attached]
            if attached_ids != [1043, 1044, 1045]:
                raise AssertionError(
                    f"ожидал узлы [1043,1044,1045] прикреплёнными к 1179 в этом порядке, "
                    f"нашёл {attached_ids}"
                )
            target_uid = await conn.fetchval("SELECT course_uid FROM courses WHERE id = 1043")
            if target_uid != "wp:inf-11-g1-t2":
                raise AssertionError(f"course_uid узла 1043 изменился: {target_uid!r}")

            content = json.loads(row["content"]) if isinstance(row["content"], str) else dict(row["content"])
            text = content.get("text", "")
            if text.count(OLD_PHRASE) != 1:
                raise AssertionError(
                    f"фраза встречается {text.count(OLD_PHRASE)} раз в material {MATERIAL_ID}, ожидался 1"
                )
            if "<a href" in text:
                raise AssertionError("в тексте уже есть <a href> — повторный запуск?")

            new_text = text.replace(OLD_PHRASE, NEW_PHRASE, 1)
            print(f"ДО:    {text!r}")
            print(f"ПОСЛЕ: {new_text!r}")

            if apply:
                new_content = dict(content)
                new_content["text"] = new_text
                await conn.execute(
                    "UPDATE materials SET content = $1::jsonb, updated_at = now() WHERE id = $2",
                    json.dumps(new_content, ensure_ascii=False), MATERIAL_ID,
                )
                after = await conn.fetchval(
                    "SELECT content->>'text' FROM materials WHERE id = $1", MATERIAL_ID
                )
                if after != new_text:
                    raise AssertionError("после UPDATE текст не совпал с ожидаемым")
                print("\nПроверка после UPDATE: OK")

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
