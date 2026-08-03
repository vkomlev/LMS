# -*- coding: utf-8 -*-
"""tsk-525: явные цели-действия («конверт урока») в начале 11 разделов курса 88.

ЗАЧЕМ
tsk-424 (экспертное ревью курса 88 «Python для ЕГЭ») нашло: ни один из разделов
курса не открывается явной формулировкой цели («после этой темы сможешь...»).
Само содержание сильное (задания 1:1 покрывают темы) — дефект чисто в
отсутствии явной формулировки в начале раздела.

Задача формулировалась под «10 разделов», но между записью задачи и работой
над ней в tsk-524 добавили подкурс «Рекурсия» (course_id=1451, между
«Функциями» и «Множествами») — сейчас в графе курса 88 фактически 11
разделов. Это предложение покрывает все 11.

Формулировки составлены по реальному содержанию каждого раздела (материалы +
банк заданий, прочитаны целиком через MCP read-only) — не шаблонной заглушкой.
Полный текст формулировок и обоснование — reviews/2026-08-03-tsk525-course88-
lesson-envelopes-proposal.md (утверждено оператором 2026-08-03).

КУДА ВСТАВЛЯЕТСЯ
Ни у одного из 11 разделов нет отдельного вводного материала — первый по
order_position материал раздела сразу начинается с содержания. Формулировка
вставляется первым абзацем в content.text ЭТОГО материала (не создаём новый
материал, по аналогии с точечными правками содержания в tsk-523). Разметка —
устоявшаяся в LMS конвенция «конверта урока» CreateCourses:
  <p><b>После этой темы сможешь:</b> ...</p>

ИДЕМПОТЕНТНОСТЬ
Перед вставкой проверяем, что content.text ещё не содержит "После этой темы
сможешь" — если уже есть, пропускаем (повторный запуск безопасен).

Запуск: dry-run по умолчанию;
  python scripts/tsk525_lesson_envelopes.py
  DBCHECK_OK=1 python scripts/tsk525_lesson_envelopes.py --apply
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

COURSE_88_CHILDREN = [90, 106, 103, 108, 111, 110, 109, 104, 1451, 105, 107]

# material_id -> формулировка (без обёртки <p><b>...</b> ...</p> — добавляется в коде)
ENVELOPES: dict[int, str] = {
    196: (
        "определить версию и разрядность своей Windows, скачать и установить "
        "Python, проверить, что установка прошла успешно"
    ),
    236: (
        "написать и запустить свою первую программу в IDLE, объявлять "
        "переменные и константы разных типов, использовать <code>print()</code>/"
        "<code>input()</code> для вывода и ввода данных, читать сообщения об "
        "ошибках Python"
    ),
    204: (
        "выполнять арифметические и сравнительные операции над числами, "
        "различать деление <code>/</code>, <code>//</code> и остаток <code>%</code>, "
        "применять стандартные функции и модуль <code>math</code> для вычислений"
    ),
    259: (
        "создавать и форматировать строки (включая f-строки), извлекать части "
        "строки через индексы и срезы, применять строковые методы "
        "<code>find()</code>, <code>count()</code>, <code>replace()</code> и другие"
    ),
    294: (
        "писать ветвление <code>if</code>/<code>elif</code>/<code>else</code>, "
        "комбинировать условия через <code>and</code>/<code>or</code>/<code>not</code>, "
        "<code>in</code> и <code>is</code>, применять тернарный оператор, "
        "<code>match-case</code> и моржовый оператор <code>:=</code>"
    ),
    284: (
        "перебирать данные циклами <code>for</code> и <code>while</code>, "
        "управлять их работой через <code>break</code>/<code>continue</code>/"
        "<code>else</code>, строить вложенные циклы для перебора сложных "
        "последовательностей"
    ),
    269: (
        "создавать и изменять списки (включая генераторы списков), искать и "
        "вырезать элементы срезами, применять методы списков и функции "
        "<code>min</code>/<code>max</code>/<code>sum</code>, работать с кортежами "
        "и двумерными массивами"
    ),
    212: (
        "писать свои функции с параметрами и значениями по умолчанию, "
        "использовать <code>*args</code>/<code>**kwargs</code>, различать "
        "локальные и глобальные переменные, применять <code>lambda</code> как "
        "ключ сортировки в <code>sorted()</code>/<code>min()</code>/<code>max()</code>"
    ),
    222: (
        "написать свою рекурсивную функцию с корректным базовым случаем, "
        "объяснить работу стека вызовов, применить мемоизацию (кэш), чтобы "
        "избежать повторных вычислений"
    ),
    228: (
        "создавать множества и очищать данные от дубликатов, применять "
        "операции объединения/пересечения/разности, объяснить неизменяемость "
        "<code>frozenset</code>"
    ),
    247: (
        "создавать и изменять словари, перебирать и сортировать их по ключам/"
        "значениям, различать изменяемые и неизменяемые типы, поверхностное и "
        "глубокое копирование, распаковывать словари"
    ),
}

MARKER = "После этой темы сможешь"


def _dsn() -> str:
    """Прод-DSN learn: из окружения либо из .mcp.json (паттерн tsk-362/366/373/524)."""
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
        # ── Разведка ДО любых изменений ─────────────────────────────────
        children = await conn.fetch(
            "SELECT cp.course_id, cp.order_number, c.title "
            "FROM course_parents cp JOIN courses c ON c.id = cp.course_id "
            "WHERE cp.parent_course_id = 88 ORDER BY cp.order_number",
        )
        child_ids = {r["course_id"] for r in children}
        if child_ids != set(COURSE_88_CHILDREN):
            raise RuntimeError(
                f"Граф курса 88 изменился с момента разведки: "
                f"ожидали {sorted(COURSE_88_CHILDREN)}, нашли {sorted(child_ids)}"
            )

        first_materials = await conn.fetch(
            "SELECT DISTINCT ON (course_id) course_id, id, title, content, is_active "
            "FROM materials WHERE course_id = ANY($1::int[]) "
            "ORDER BY course_id, order_position",
            COURSE_88_CHILDREN,
        )
        by_course = {r["course_id"]: r for r in first_materials}

        print("=" * 78)
        print(f"tsk-525 · конверты уроков курса 88 · {'ПРИМЕНЕНИЕ' if apply else 'DRY-RUN'}")
        print("=" * 78)

        plan: list[tuple[int, int, str, str]] = []  # (course_id, material_id, old_text, new_text)
        for r in children:
            course_id = r["course_id"]
            m = by_course.get(course_id)
            if m is None:
                raise RuntimeError(f"У курса {course_id} нет материалов — проверить вручную")
            if m["id"] not in ENVELOPES:
                raise RuntimeError(
                    f"Первый материал курса {course_id} сейчас id={m['id']} "
                    f"({m['title']!r}), а формулировка готовилась под другой id — "
                    f"порядок материалов изменился, план устарел."
                )
            content = json.loads(m["content"]) if isinstance(m["content"], str) else m["content"]
            old_text = content.get("text") or ""
            already_done = MARKER in old_text
            envelope_html = f"<p><b>{MARKER}:</b> {ENVELOPES[m['id']]}.</p>"
            new_text = old_text if already_done else envelope_html + old_text
            plan.append((course_id, m["id"], old_text, new_text))
            status = "уже применено (пропуск)" if already_done else "к вставке"
            print(f"  order={r['order_number']:>2}  курс={course_id:>4}  "
                  f"материал={m['id']:>4}  is_active={m['is_active']}  {status}  «{r['title']}»")

        pending = [p for p in plan if p[2] != p[3]]
        print(f"\nВсего разделов: {len(plan)}. К вставке: {len(pending)}. "
              f"Уже применено: {len(plan) - len(pending)}.")

        if not apply:
            print("\nDRY-RUN: ничего не записано. Повтор с --apply.")
            return

        async with conn.transaction():
            for course_id, material_id, old_text, new_text in pending:
                await conn.execute(
                    "UPDATE materials SET content = jsonb_set(content, '{text}', $1::jsonb) "
                    "WHERE id = $2",
                    json.dumps(new_text),
                    material_id,
                )
            print(f"\nОбновлено материалов: {len(pending)}")

            # ── Верификация ДО COMMIT ────────────────────────────────────
            print("Верификация в транзакции:")
            all_ok = True
            for course_id, material_id, _old_text, expected_text in plan:
                row = await conn.fetchrow(
                    "SELECT content->>'text' AS text FROM materials WHERE id = $1",
                    material_id,
                )
                ok = row["text"] == expected_text and MARKER in row["text"]
                all_ok = all_ok and ok
                print(f"  курс={course_id:>4} материал={material_id:>4}: "
                      f"{'OK' if ok else 'МИСМАТЧ'}")
            if not all_ok:
                raise RuntimeError("Верификация не сошлась — ROLLBACK.")

        print("\nCOMMIT выполнен. Независимую проверку — через MCP learn_prod_db "
              "(отдельным read-only соединением) и живым рендером в браузере.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="tsk-525: конверты уроков курса 88")
    ap.add_argument("--apply", action="store_true", help="выполнить запись (по умолчанию dry-run)")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
