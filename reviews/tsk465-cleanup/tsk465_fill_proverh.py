"""tsk-465: дозаполнить блоки «Проверь себя», у которых потерялась подводка.

31 материал в курсах Arduino и Excel содержит только заголовок
`<h2 id="prover-sebya">Проверь себя</h2>` без поясняющего абзаца, тогда как
в 16 других курсах у того же блока подводка есть и одинакова у всех.
Подставляем ровно её — блок становится однородным по всем курсам.

Защита: обновляем только те строки, где текущий текст в точности равен
одинокому заголовку. Если хоть одна строка не такая или их не 31 — ROLLBACK.
Материалы с уже заполненной подводкой (16 шт.) не трогаются по определению
условия.

Запуск: только на прод-сервере под пользователем app.
Бэкап: D:/Work/LMS/reviews/tsk465-cleanup/backup-2026-07-29-proverh-sebya.json
"""
from __future__ import annotations

import asyncio
import sys

import asyncpg

EXPECTED = 31
APPLY = "--apply" in sys.argv

ONLY_HEADING = '<h2 id="prover-sebya">Проверь себя</h2>'
FULL_TEXT = (
    '<h2 id="prover-sebya">Проверь себя</h2>\n'
    "<p>Небольшая проверка по теме этого задания. Реши задачи ниже — они соберут "
    "то, что ты разобрал в теории. Ответы проверяются автоматически.</p>"
)


def _dsn() -> str:
    """Достать DSN из /opt/lms/.env (в вывод не печатаем)."""
    with open("/opt/lms/.env", encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                return raw.replace("postgresql+asyncpg://", "postgresql://")
    raise SystemExit("DATABASE_URL не найден в /opt/lms/.env")


async def main() -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, course_id, content->>'text' AS txt, content->>'format' AS fmt
                FROM materials
                WHERE TRIM(title) = 'Проверь себя' AND content->>'text' = $1
                ORDER BY id
                """,
                ONLY_HEADING,
            )
            print(f"Найдено пустых блоков: {len(rows)} (ожидалось {EXPECTED})")
            if len(rows) != EXPECTED:
                raise SystemExit("СТОП: количество не совпало — данные изменились")

            for r in rows:
                if r["fmt"] != "html":
                    raise SystemExit(f"СТОП: материал {r['id']} имеет формат {r['fmt']}, ожидался html")
            print("Формат сверен: у всех html")

            if not APPLY:
                print("\nDRY-RUN: изменения НЕ применены (нет --apply). Откатываю.")
                raise SystemExit(0)

            updated = await conn.fetch(
                """
                UPDATE materials
                SET content = jsonb_set(content::jsonb, '{text}', to_jsonb($2::text), true),
                    updated_at = NOW()
                WHERE TRIM(title) = 'Проверь себя' AND content->>'text' = $1
                RETURNING id
                """,
                ONLY_HEADING,
                FULL_TEXT,
            )
            print(f"Обновлено: {len(updated)}")
            if len(updated) != EXPECTED:
                raise SystemExit("СТОП: обновлено не 31 — откат")

            left = await conn.fetchval(
                "SELECT COUNT(*) FROM materials WHERE TRIM(title)='Проверь себя' AND content->>'text' = $1",
                ONLY_HEADING,
            )
            same = await conn.fetchval(
                "SELECT COUNT(*) FROM materials WHERE TRIM(title)='Проверь себя' AND content->>'text' = $1",
                FULL_TEXT,
            )
            print(f"Проверка внутри транзакции: пустых осталось {left}, с подводкой всего {same}")
            if left != 0 or same != 47:
                raise SystemExit("СТОП: итог не сходится (ждали 0 пустых и 47 с подводкой) — откат")
            print("COMMIT")
    finally:
        await conn.close()


asyncio.run(main())
