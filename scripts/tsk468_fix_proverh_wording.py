"""tsk-468: заменить обещание автопроверки в блоках «Проверь себя».

Шаблонный абзац одинаков во всех 47 материалах «Проверь себя» (проверено
COUNT(DISTINCT text) = 1 в /db-check). Последнее предложение «Ответы
проверяются автоматически.» неверно для 124 заданий из 930 в этих курсах —
они требуют ручной проверки преподавателем (SA/SA_COM/TBL_COM). Оператор
выбрал вариант 2: заменить одно предложение простой универсальной
формулировкой без условной логики по составу заданий узла (tsk-468).

Защита: обновляем только строки, где текущий текст в точности равен OLD_TEXT.
Если найдено не 47 строк — STOP. Если после UPDATE не все 47 совпадают с
NEW_TEXT и 0 совпадают со старым — ROLLBACK.

Запуск: только на прод-сервере под пользователем app.
Бэкап: D:/Work/LMS/reviews/tsk468-cleanup/backup-2026-08-05-proverh-sebya.json
"""
from __future__ import annotations

import asyncio
import sys

import asyncpg

EXPECTED = 47
APPLY = "--apply" in sys.argv

OLD_TEXT = (
    '<h2 id="prover-sebya">Проверь себя</h2>\n'
    "<p>Небольшая проверка по теме этого задания. Реши задачи ниже — они соберут "
    "то, что ты разобрал в теории. Ответы проверяются автоматически.</p>"
)
NEW_TEXT = (
    '<h2 id="prover-sebya">Проверь себя</h2>\n'
    "<p>Небольшая проверка по теме этого задания. Реши задачи ниже — они соберут "
    "то, что ты разобрал в теории. Часть ответов проверяется сразу, часть — "
    "преподавателем.</p>"
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
                OLD_TEXT,
            )
            print(f"Найдено блоков со старым текстом: {len(rows)} (ожидалось {EXPECTED})")
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
                OLD_TEXT,
                NEW_TEXT,
            )
            print(f"Обновлено: {len(updated)}")
            if len(updated) != EXPECTED:
                raise SystemExit("СТОП: обновлено не 47 — откат")

            left_old = await conn.fetchval(
                "SELECT COUNT(*) FROM materials WHERE TRIM(title)='Проверь себя' AND content->>'text' = $1",
                OLD_TEXT,
            )
            same_new = await conn.fetchval(
                "SELECT COUNT(*) FROM materials WHERE TRIM(title)='Проверь себя' AND content->>'text' = $1",
                NEW_TEXT,
            )
            print(f"Проверка внутри транзакции: старых осталось {left_old}, новых всего {same_new}")
            if left_old != 0 or same_new != EXPECTED:
                raise SystemExit("СТОП: итог не сходится (ждали 0 старых и 47 новых) — откат")
            print("COMMIT")
    finally:
        await conn.close()


asyncio.run(main())
