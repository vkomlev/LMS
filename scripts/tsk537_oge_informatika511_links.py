# -*- coding: utf-8 -*-
"""tsk-537: чистка ссылок на расформированный курс «Информатика 5-11» в 12 материалах
раздела «Что нужно знать» курсов ОГЭ-заданий (zadanie-1..14, курсы 1111-1179).

Контекст (Root/tasks/tsk-537-*.md): курс 825 «Информатика 5-11» расформирован
2026-07-08 (tsk-129) на 7 независимых курсов (871 wp:inf-5 ... 1040 wp:inf-11).
Материалы ОГЭ хранят в тексте упоминание «курс «Информатика 5-11»: <тема>» — у
zadanie-14 (id=2677) ссылка была, но на один узел вместо двух заявленных в тексте
понятий; у остальных 10 (zadanie-1..13 без 5/6/14/15/16) ссылки не было вовсе.

Root cause (найдено при работе над tsk-537, подтверждено экспериментально и по
git-истории): НЕ баг активного кода конвейера ContentBackbone (blocks_renderer /
blocks_to_lms передают <a href> дословно, без изменений). Причина —
`scripts/unwrap_broken_rel_links_tsk261.py` (коммит 9cf80a5, tsk-261, применён
2026-07-17 — совпадает с materials.updated_at десяти материалов): скрипт разворачивал
"битые" относительные ссылки (`href="/[a-z]..."`, 404 в SPW, т.к. это WP-only пути) в
голый текст батчем по ВСЕМ материалам без разбора семантики цели. Собственный
self-check скрипта прямо называет material 2355 примером результата.

Источник ContentBackbone (content_hub.material) сознательно НЕ трогаем: там ссылки
корректны ДЛЯ WP-рендера того же документа (target="_blank" на старый WP-навигатор,
который оператор решил оставить как legacy-справочник, п.3 tsk-537). LMS/SPW и WP —
разные домены навигации, поэтому для материала, показываемого в LMS/SPW, нужен
LMS-internal deep-link `/courses/{quote(course_uid, safe="")}` (паттерн из
app/(authed)/courses/page.tsx, использован в tsk526_link_zadanie14_m2.py) — единой
функции-резолвера "старый WP-путь -> новый course_uid" в коде нет, мэппинг сделан
вручную по значениям courses.course_uid + course_parents (сверено /db-check,
2026-08-05, live: mcp__learn_prod_db__query).

Durable-часть фикса (не разовая правка): при записи проставляем
materials.content_provenance = {"source":"manual_web", "fields":["content"]} —
это существующий защитный контракт tsk-433 (app/services/materials_service.py
_manually_edited_fields): при следующем импорте/переиздании из ContentBackbone
LMS-bulk-upsert обязан пропускать поле content для этих 12 материалов, а не
затирать deep-link устаревшим WP-href из source. Без этой пометки правка была бы
съедена ближайшим переизданием (ровно то, от чего просил защититься оператор).

Запуск: dry-run по умолчанию;
  python scripts/tsk537_oge_informatika511_links.py
  DBCHECK_OK=1 python scripts/tsk537_oge_informatika511_links.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]


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


def _link(course_uid: str, text: str, *, blank: bool = False) -> str:
    href = f"/courses/{quote(course_uid, safe='')}"
    attrs = f' target="_blank" rel="noopener"' if blank else ""
    return f'<a href="{href}"{attrs}>{text}</a>'


# material_id -> (course_id, [(old_phrase, new_phrase), ...])
# new_phrase строится через _link(course_uid, anchor_text, blank=...).
FIXES: dict[int, tuple[int, list[tuple[str, str]]]] = {
    2355: (1111, [
        ("Измерение информации: бит, байт, формула I = K · i",
         _link("wp:inf-7-g1-t5", "Измерение информации: бит, байт, формула I = K · i", blank=True)),
        ("Кодирование текста: КОИ-8, Unicode, сколько бит на символ",
         _link("wp:inf-10-g3-t5", "Кодирование текста: КОИ-8, Unicode, сколько бит на символ", blank=True)),
        ("Измерение информации (глубже, старшие классы)",
         _link("wp:inf-10-g1-t2", "Измерение информации (глубже, старшие классы)", blank=True)),
    ]),
    2364: (1112, [
        ("Кодирование информации", _link("wp:inf-5-t07", "Кодирование информации")),
    ]),
    2400: (1120, [
        ("Алгебра логики", _link("wp:inf-10-g4-t2", "Алгебра логики")),
        ("Таблицы истинности", _link("wp:inf-10-g4-t3", "Таблицы истинности")),
    ]),
    2439: (1128, [
        ("модели и графы", _link("wp:inf-11-g3-t2", "модели и графы")),
    ]),
    2546: (1152, [
        ("компьютерные сети и адресация", _link("wp:inf-11-g4-t1", "компьютерные сети и адресация")),
    ]),
    2555: (1153, [
        # Литеральный href CB-источника (informatika-10-18-algebra-logiki) — "Алгебра
        # логики", НЕ course_dependencies-узел 1030 "Теория множеств" (та мапа для
        # course_dependencies строилась независимо, под другую цель — prerequisite-гейт,
        # не дословный href текста; расхождение зафиксировано в отчёте tsk-537).
        ("Алгебра логики", _link("wp:inf-10-g4-t2", "Алгебра логики")),
    ]),
    2563: (1154, [
        ("модели, графы, схемы", _link("wp:inf-11-g3-t2", "модели, графы, схемы")),
    ]),
    2597: (1162, [
        ("Системы счисления", _link("wp:inf-10-g3-t1", "Системы счисления")),
        ("Перевод чисел", _link("wp:inf-10-g3-t2", "Перевод чисел")),
    ]),
    2606: (1163, [
        # Источник CB даёт один <a> на весь составной оборот — сохраняем структуру
        # (не расщепляем на 2 ссылки, в отличие от 2677, где сам оператор явно
        # разделил два ПОНЯТИЯ; здесь один оборот "файлы и файловая система").
        ("файлы и файловая система", _link("wp:inf-10-g2-t4", "файлы и файловая система")),
    ]),
    2614: (1164, [
        ("Файловая система компьютера", _link("wp:inf-10-g2-t4", "Файловая система компьютера")),
        ("Файлы и каталоги", _link("wp:inf-7-g2-t3", "Файлы и каталоги")),
    ]),
    2670: (1178, [
        ("Форматирование текста", _link("wp:inf-7-g3-t3", "Форматирование текста")),
        ("Компьютерные презентации", _link("wp:inf-7-g5-t2", "Компьютерные презентации")),
        ("Текстовые документы", _link("wp:inf-10-g5-t1", "Текстовые документы")),
    ]),
}

# material 2677 (zadanie-14): не точечная фраза, а перестройка одной ссылки в две
# (решение оператора 2026-08-05, tsk-537 п.1) — обрабатывается отдельно от FIXES.
MATERIAL_2677_ID = 2677
MATERIAL_2677_COURSE_ID = 1179
MATERIAL_2677_OLD = (
    '<a href="/courses/wp%3Ainf-11-g1-t2">курс «Информатика 5-11»: '
    "Табличный процессор и Инструменты анализа данных</a>"
)
MATERIAL_2677_NEW = (
    "курс «Информатика 5-11»: "
    + _link("wp:inf-11-g1-t1", "Табличный процессор")
    + " и "
    + _link("wp:inf-11-g1-t4", "Инструменты анализа данных")
)

# Все узлы нового дерева, на которые ссылаемся, — id -> ожидаемый course_uid.
# Сверяется живьём внутри транзакции: deep-link не должен указывать в никуда,
# если граф курсов изменится между инвентаризацией и записью.
EXPECTED_NODES: dict[int, str] = {
    832: "wp:inf-7-g1-t5", 839: "wp:inf-7-g2-t3", 848: "wp:inf-7-g3-t3",
    860: "wp:inf-7-g5-t2", 880: "wp:inf-5-t07", 1012: "wp:inf-10-g1-t2",
    1020: "wp:inf-10-g2-t4", 1022: "wp:inf-10-g3-t1", 1023: "wp:inf-10-g3-t2",
    1026: "wp:inf-10-g3-t5", 1031: "wp:inf-10-g4-t2", 1032: "wp:inf-10-g4-t3",
    1037: "wp:inf-10-g5-t1", 1042: "wp:inf-11-g1-t1", 1045: "wp:inf-11-g1-t4",
    1054: "wp:inf-11-g3-t2", 1058: "wp:inf-11-g4-t1",
}


def _provenance(fields: list[str]) -> str:
    return json.dumps({
        "source": "manual_web",
        "edited_at": datetime.now(timezone.utc).isoformat(),
        "edited_by": "script:tsk537",
        "fields": fields,
    }, ensure_ascii=False)


async def _apply_one(
    conn: asyncpg.Connection, material_id: int, course_id: int,
    replacements: list[tuple[str, str]], *, apply: bool,
) -> None:
    row = await conn.fetchrow(
        "SELECT id, course_id, title, is_active, content, content_provenance "
        "FROM materials WHERE id = $1",
        material_id,
    )
    if row is None:
        raise AssertionError(f"материал {material_id} не найден")
    if row["course_id"] != course_id:
        raise AssertionError(f"material {material_id}: ожидал course_id={course_id}, нашёл {row['course_id']}")
    if not row["is_active"]:
        raise AssertionError(f"material {material_id}: неактивен — не тот, что показывается ученику")

    content = json.loads(row["content"]) if isinstance(row["content"], str) else dict(row["content"])
    text = content.get("text", "")
    if "<a href" in text:
        raise AssertionError(f"material {material_id}: в тексте уже есть <a href> — повторный запуск?")

    new_text = text
    for old, new in replacements:
        count = new_text.count(old)
        if count != 1:
            raise AssertionError(
                f"material {material_id}: фраза {old!r} встречается {count} раз, ожидался 1"
            )
        new_text = new_text.replace(old, new, 1)

    print(f"--- material {material_id} ({row['title']}) course={course_id} ---")
    print(f"    ДО:    {text!r}")
    print(f"    ПОСЛЕ: {new_text!r}")

    if not apply:
        return

    new_content = dict(content)
    new_content["text"] = new_text
    prev = row["content_provenance"]
    if isinstance(prev, str):
        prev = json.loads(prev)
    prev_fields = prev.get("fields") if isinstance(prev, dict) and prev.get("source") == "manual_web" else []
    merged_fields = sorted(set((prev_fields or []) + ["content"]))

    await conn.execute(
        "UPDATE materials SET content = $1::jsonb, content_provenance = $2::jsonb, "
        "updated_at = now() WHERE id = $3",
        json.dumps(new_content, ensure_ascii=False),
        _provenance(merged_fields),
        material_id,
    )
    after_text = await conn.fetchval("SELECT content->>'text' FROM materials WHERE id = $1", material_id)
    after_prov = await conn.fetchval("SELECT content_provenance->>'source' FROM materials WHERE id = $1", material_id)
    if after_text != new_text:
        raise AssertionError(f"material {material_id}: после UPDATE текст не совпал с ожидаемым")
    if after_prov != "manual_web":
        raise AssertionError(f"material {material_id}: content_provenance не проставлен")


async def _apply_2677(conn: asyncpg.Connection, *, apply: bool) -> None:
    row = await conn.fetchrow(
        "SELECT id, course_id, title, is_active, content, content_provenance "
        "FROM materials WHERE id = $1",
        MATERIAL_2677_ID,
    )
    if row is None:
        raise AssertionError(f"материал {MATERIAL_2677_ID} не найден")
    if row["course_id"] != MATERIAL_2677_COURSE_ID:
        raise AssertionError(f"material {MATERIAL_2677_ID}: ожидал course_id={MATERIAL_2677_COURSE_ID}")
    if not row["is_active"]:
        raise AssertionError(f"material {MATERIAL_2677_ID}: неактивен")

    content = json.loads(row["content"]) if isinstance(row["content"], str) else dict(row["content"])
    text = content.get("text", "")
    if text.count(MATERIAL_2677_OLD) != 1:
        raise AssertionError(
            f"material {MATERIAL_2677_ID}: старая фраза встречается "
            f"{text.count(MATERIAL_2677_OLD)} раз(а), ожидался 1 — узел 1043 (tsk-526) уже не тот?"
        )
    new_text = text.replace(MATERIAL_2677_OLD, MATERIAL_2677_NEW, 1)

    print(f"--- material {MATERIAL_2677_ID} (zadanie-14, две ссылки вместо одной) ---")
    print(f"    ДО:    {text!r}")
    print(f"    ПОСЛЕ: {new_text!r}")

    if not apply:
        return

    new_content = dict(content)
    new_content["text"] = new_text
    prev = row["content_provenance"]
    if isinstance(prev, str):
        prev = json.loads(prev)
    prev_fields = prev.get("fields") if isinstance(prev, dict) and prev.get("source") == "manual_web" else []
    merged_fields = sorted(set((prev_fields or []) + ["content"]))

    await conn.execute(
        "UPDATE materials SET content = $1::jsonb, content_provenance = $2::jsonb, "
        "updated_at = now() WHERE id = $3",
        json.dumps(new_content, ensure_ascii=False),
        _provenance(merged_fields),
        MATERIAL_2677_ID,
    )
    after_text = await conn.fetchval("SELECT content->>'text' FROM materials WHERE id = $1", MATERIAL_2677_ID)
    if after_text != new_text:
        raise AssertionError(f"material {MATERIAL_2677_ID}: после UPDATE текст не совпал с ожидаемым")


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            print("=" * 78)
            print(f"tsk-537 · 12 материалов ОГЭ-информатика · {'ПРИМЕНЕНИЕ' if apply else 'DRY-RUN'}")
            print("=" * 78)

            # Свежесть графа курсов — сверяем ВСЕ целевые узлы одним запросом.
            rows = await conn.fetch(
                "SELECT id, course_uid FROM courses WHERE id = ANY($1::int[])",
                list(EXPECTED_NODES.keys()),
            )
            found = {r["id"]: r["course_uid"] for r in rows}
            for node_id, expected_uid in EXPECTED_NODES.items():
                actual = found.get(node_id)
                if actual != expected_uid:
                    raise AssertionError(
                        f"узел {node_id}: ожидал course_uid={expected_uid!r}, нашёл {actual!r} — граф курсов изменился"
                    )
            print(f"Сверено узлов дерева: {len(EXPECTED_NODES)} (все course_uid совпали)")
            print()

            for material_id, (course_id, replacements) in FIXES.items():
                await _apply_one(conn, material_id, course_id, replacements, apply=apply)
                print()

            await _apply_2677(conn, apply=apply)

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО (11 + 1 материалов).")
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
