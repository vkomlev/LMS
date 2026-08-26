# -*- coding: utf-8 -*-
"""tsk-690: убрать материалы-«Вопросы» (356, 371, 380), сохранив разборы из 356.

ЧТО ЭТО ЗА МАТЕРИАЛЫ
Артефакт импорта из WordPress: импортёр разобрал страницу урока на блоки, и
раздел «Вопросы» стал отдельным МАТЕРИАЛОМ (ключи `wp:mat:komlev:<адрес>:2|:3`).
В LMS вопросы оформляются заданиями, а материал ученик сдать не может по
устройству — отсюда вечный долг. Поиск по существу (доля пунктов списка,
оканчивающихся вопросительным знаком, по всем материалам базы) даёт ровно эти
три активных; пять таких же из того же импорта уже выключены раньше руками
(363, 401, 419, 437, 807) — и материал 357 в том же курсе 139.

ДУБЛИРОВАНИЕ ЗАДАНИЯМИ ПРОВЕРЕНО ПОШТУЧНО (read-only, прод, 2026-08-26)
- 380 (курс 142): 10 пунктов = задания 5033-5043 + 2085, позиции 1-11;
- 371 (курс 162): 20 пунктов = задания 4948-4967, позиции 1-20;
- 356 (курс 139): 27 пунктов = задания 4995-5021, позиции 1-27.
Сверх списка вопросов в 356 лежат ТРИ разбора задач ЕГЭ с готовыми решениями на
Python («Задание 4/5/6»), которых нет ни в заданиях, ни в соседнем материале 355.
Решение оператора: перенести их в 355, и только потом убирать.

ВЫКЛЮЧАЕМ, А НЕ УДАЛЯЕМ (решение оператора)
Удалённая строка ВЕРНЁТСЯ при следующем переиздании из WP: импорт не найдёт
external_uid и создаст материал заново — активным и обязательным (ветка CREATE в
`materials_service.bulk_upsert`). Выключенный не вернётся: `is_active`
перезаписывается только при явной передаче поля (tsk-377/378). Плюс DELETE
каскадом снёс бы `student_material_progress` (ручной зачёт Глеба) и
перенумеровал остальные материалы курса триггером `trg_reorder_materials_*`.
Из экрана ученика материал пропадает одинаково: и кабинет (`me_service`), и
движок (`learning_engine_service`) фильтруют по `is_active = true`.

ЗАЩИТА ПЕРЕНОСА ОТ ИМПОРТА
У материала 355 уже стоит `content_provenance = {"source": "manual_web",
"fields": ["content"]}` — импорт его содержимое не перезаписывает (tsk-433).
Скрипт обновляет только отметку о правке, оставляя защиту на месте.

ОБРАТИМОСТЬ
`UPDATE materials SET is_active = true WHERE id IN (356,371,380)` возвращает
материалы; текст 355 до правки скрипт сохраняет в файл рядом с собой.

Запуск: dry-run по умолчанию; `--apply` — запись (нужен DBCHECK_OK=1).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

SOURCE_ID = 356  # материал-«Вопросы» курса 139, откуда берём разборы
TARGET_ID = 355  # материал «Подсеть, адрес подсети и маска подсети» того же курса
DISABLE_IDS = [356, 371, 380]
SPLIT_MARKER = '<blockquote class="check">'
SECTION_HEADER = "\n<hr/>\n<h3>Разборы заданий 13 с решениями</h3>\n"
# Три разбора опознаём по хвостам условий — если хоть одного нет, состав
# материала изменился и вслепую переносить нельзя.
EXPECTED_FRAGMENTS = [
    "Сколько различных адресов компьютеров теоретически допускает эта маска",
    "наименьшее количество возможных адресов",
    "сумма единиц в двоичной записи IP-адреса чётна",
]


def _dsn() -> str:
    """Прод-DSN learn: из окружения, иначе из `.mcp.json` (секрет не печатаем)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError(
            "Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно."
        )
    return dsn


async def _debt_snapshot(conn: asyncpg.Connection, label: str) -> None:
    """Сколько обязательных материалов курса не закрыто у тех, кто дошёл до темы."""
    rows = await conn.fetch(
        """
        WITH courses_of_interest AS (SELECT unnest(ARRAY[139, 142, 162]) AS course_id),
        req AS (
            SELECT m.course_id, m.id FROM materials m
            JOIN courses_of_interest c ON c.course_id = m.course_id
            WHERE m.is_active AND m.requirement_level IN ('required', 'skippable')
        ),
        touched AS (
            SELECT DISTINCT s.student_id, m.course_id
            FROM student_material_progress s
            JOIN materials m ON m.id = s.material_id
            JOIN courses_of_interest c ON c.course_id = m.course_id
        )
        SELECT t.student_id, u.full_name, t.course_id,
               (SELECT count(*) FROM req r WHERE r.course_id = t.course_id) - (
                   SELECT count(*) FROM student_material_progress s
                   JOIN req r ON r.id = s.material_id AND r.course_id = t.course_id
                   WHERE s.student_id = t.student_id AND s.status = 'completed'
               ) AS left_open
        FROM touched t JOIN users u ON u.id = t.student_id
        ORDER BY t.student_id, t.course_id
        """
    )
    print(f"{label}: незакрытые обязательные материалы курсов 139/142/162")
    for r in rows:
        print(f"  {r['student_id']} {r['full_name']}, курс {r['course_id']}: {r['left_open']}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="tsk-690: убрать материалы-«Вопросы»")
    parser.add_argument("--apply", action="store_true", help="записать в прод-БД")
    args = parser.parse_args()

    conn = await asyncpg.connect(_dsn())
    try:
        src = await conn.fetchrow(
            "SELECT id, course_id, is_active, content->>'text' AS text FROM materials WHERE id = $1",
            SOURCE_ID,
        )
        dst = await conn.fetchrow(
            "SELECT id, course_id, is_active, content, content->>'text' AS text, "
            "content_provenance FROM materials WHERE id = $1",
            TARGET_ID,
        )
        if src is None or dst is None:
            print("СТОП: материал 355 или 356 не найден.")
            return 2
        if src["course_id"] != dst["course_id"]:
            print("СТОП: 355 и 356 в разных курсах — перенос не тот, что задумывался.")
            return 2

        idx = src["text"].find(SPLIT_MARKER)
        if idx < 0:
            print("СТОП: в 356 больше нет блока разборов — состав изменился.")
            return 2
        excerpt = src["text"][idx:]
        missing = [f for f in EXPECTED_FRAGMENTS if f not in excerpt]
        if missing:
            print(f"СТОП: в блоке разборов не нашлись условия: {missing}")
            return 2
        if any(f in (dst["text"] or "") for f in EXPECTED_FRAGMENTS):
            print("Разборы уже есть в 355 — перенос пропускаю (повторный запуск).")
            excerpt = ""

        print(f"Материал {SOURCE_ID}: {len(src['text'])} знаков, из них разборы — {len(excerpt)}")
        print(f"Материал {TARGET_ID}: {len(dst['text'] or '')} знаков до правки")
        print(f"Выключаем материалы: {DISABLE_IDS}")
        print()
        await _debt_snapshot(conn, "ДО")

        if not args.apply:
            print("\nDry-run: записи не было.")
            return 0

        backup = project_root / "reviews" / f"tsk690-material{TARGET_ID}-before.html"
        backup.write_text(dst["text"] or "", encoding="utf-8")
        print(f"\nТекст 355 до правки сохранён: {backup}")

        now = datetime.now(timezone.utc).isoformat()
        async with conn.transaction():
            if excerpt:
                content = json.loads(dst["content"]) if isinstance(dst["content"], str) else dst["content"]
                content = dict(content)
                content["text"] = (dst["text"] or "") + SECTION_HEADER + excerpt
                prov = dict(json.loads(dst["content_provenance"]) if isinstance(
                    dst["content_provenance"], str) else (dst["content_provenance"] or {}))
                prov.update(
                    {
                        "source": "manual_web",
                        "fields": sorted(set(prov.get("fields") or []) | {"content"}),
                        "edited_at": now,
                        "edited_by": "script:tsk690",
                    }
                )
                await conn.execute(
                    "UPDATE materials SET content = $2::jsonb, content_provenance = $3::jsonb "
                    "WHERE id = $1",
                    TARGET_ID,
                    json.dumps(content, ensure_ascii=False),
                    json.dumps(prov, ensure_ascii=False),
                )
            status = await conn.execute(
                "UPDATE materials SET is_active = false "
                "WHERE id = ANY($1::int[]) AND is_active IS TRUE",
                DISABLE_IDS,
            )
            disabled = int(status.rsplit(" ", 1)[-1] or 0)

        after = await conn.fetchrow(
            "SELECT length(content->>'text') AS len, "
            "content->>'text' AS text FROM materials WHERE id = $1",
            TARGET_ID,
        )
        still_missing = [f for f in EXPECTED_FRAGMENTS if f not in (after["text"] or "")]
        print(f"355 после правки: {after['len']} знаков, разборов не хватает: {still_missing or 'нет'}")
        print(f"Выключено материалов: {disabled}")
        left_active = await conn.fetch(
            "SELECT id, is_active FROM materials WHERE id = ANY($1::int[]) ORDER BY id",
            DISABLE_IDS,
        )
        print("Состояние: " + ", ".join(f"{r['id']}={r['is_active']}" for r in left_active))
        print()
        await _debt_snapshot(conn, "ПОСЛЕ")
        return 0 if not still_missing else 3
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
