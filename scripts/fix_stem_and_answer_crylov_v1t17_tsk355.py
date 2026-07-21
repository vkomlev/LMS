"""tsk-355 (follow-up находка): исправление текста условия и ответа задания
9488 (crylov:v1t17, курс 145). Оператор подтвердил канонический текст по
сканам книги — исходное искажение попало при импорте с future-step.ru
(там условие тоже было неверным для этого задания, хотя вариант взят тот
же, первый).

Правки (сверено оператором с каноном):
  - "Уровень средний" -> "Уровень простой" (текст stem, отдельно от
    difficulty_id, который уже исправлен в раунде 4 tsk-355)
  - "не менее двух из трёх элементов" -> "не более двух из трёх элементов"
  - "сумма элементов тройки превосходит" -> "сумма элементов тройки
    не превосходит"
  - answer_raw: null -> "8 99191" (формат сверен с соседними SA_COM —
    строка, значения через пробел)

DSN — только через PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/fix_stem_and_answer_crylov_v1t17_tsk355.py            # dry-run
    PROD_DB_DSN=... python scripts/fix_stem_and_answer_crylov_v1t17_tsk355.py --apply     # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg

TASK_ID = 9488
EXTERNAL_UID = "crylov:v1t17"

OLD_STEM = (
    '<p><a href="/api/v1/media/29ac57ea222afbbd447defa454f46dcc50666a0c4827d01901975423eb1809d2.txt" '
    'rel="noopener noreferrer" target="_blank">Файл к заданию</a></p>\n'
    "<p>Задание 17 Сборник Крылова С.С. вариант 1 Уровень средний.<br>"
    "Задание выполняется с использованием прилагаемых файлов.<br>"
    "В файле содержится последовательность целых чисел. Её элементы могут принимать целые "
    "значения от −100 000 до 100 000 включительно. Определите количество троек элементов "
    "последовательности, в которых не менее двух из трёх элементов являются двузначными числами, "
    "а сумма элементов тройки превосходит сумму минимального двузначного и максимального "
    "двузначного элементов последовательности. В ответе запишите количество найденных троек "
    "чисел, затем максимальную из сумм элементов таких троек. В данной задаче под тройкой "
    "подразумевается три идущих подряд элемента последовательности.</p>"
)

NEW_STEM = (
    '<p><a href="/api/v1/media/29ac57ea222afbbd447defa454f46dcc50666a0c4827d01901975423eb1809d2.txt" '
    'rel="noopener noreferrer" target="_blank">Файл к заданию</a></p>\n'
    "<p>Задание 17 Сборник Крылова С.С. вариант 1 Уровень простой.<br>"
    "Задание выполняется с использованием прилагаемых файлов.<br>"
    "В файле содержится последовательность целых чисел. Её элементы могут принимать целые "
    "значения от −100 000 до 100 000 включительно. Определите количество троек элементов "
    "последовательности, в которых не более двух из трёх элементов являются двузначными числами, "
    "а сумма элементов тройки не превосходит сумму минимального двузначного и максимального "
    "двузначного элементов последовательности. В ответе запишите количество найденных троек "
    "чисел, затем максимальную из сумм элементов таких троек. В данной задаче под тройкой "
    "подразумевается три идущих подряд элемента последовательности.</p>"
)

NEW_ANSWER_RAW = "8 99191"


async def main(apply: bool) -> int:
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-355: фикс текста+ответа id={TASK_ID} ({EXTERNAL_UID}) — {mode} ===\n")

    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT id, external_uid, task_content->>'stem' AS stem, "
            "task_content->>'answer_raw' AS answer_raw, difficulty_id "
            "FROM tasks WHERE id = $1",
            TASK_ID,
        )
        if row is None:
            print(f"ОШИБКА: id={TASK_ID} не найдено")
            return 1
        print(f"BEFORE: external_uid={row['external_uid']} difficulty_id={row['difficulty_id']}")
        print(f"BEFORE stem == OLD_STEM: {row['stem'] == OLD_STEM}")
        print(f"BEFORE answer_raw: {row['answer_raw']!r}")

        if row["external_uid"] != EXTERNAL_UID:
            print(f"ОШИБКА: external_uid не совпадает (факт {row['external_uid']}) — СТОП")
            return 1
        if row["stem"] != OLD_STEM:
            print("ОШИБКА: текущий stem не совпадает с ожидаемым OLD_STEM — "
                  "кто-то изменил текст параллельно, СТОП")
            return 1
        if row["answer_raw"] is not None:
            print(f"ОШИБКА: answer_raw уже не null (факт {row['answer_raw']!r}) — СТОП")
            return 1

        tx = conn.transaction()
        await tx.start()
        try:
            result = await conn.execute(
                "UPDATE tasks SET task_content = "
                "jsonb_set(jsonb_set(task_content, '{stem}', $1::jsonb), "
                "'{answer_raw}', $2::jsonb) "
                "WHERE id = $3",
                json.dumps(NEW_STEM),
                json.dumps(NEW_ANSWER_RAW),
                TASK_ID,
            )
            print(f"UPDATE: {result}")

            after = await conn.fetchrow(
                "SELECT task_content->>'stem' AS stem, task_content->>'answer_raw' AS answer_raw "
                "FROM tasks WHERE id = $1",
                TASK_ID,
            )
            ok_stem = after["stem"] == NEW_STEM
            ok_answer = after["answer_raw"] == NEW_ANSWER_RAW
            print(f"AFTER stem == NEW_STEM: {ok_stem}")
            print(f"AFTER answer_raw: {after['answer_raw']!r} (ожидали {NEW_ANSWER_RAW!r})")

            if not (ok_stem and ok_answer):
                print("\nОШИБКА: верификация после UPDATE не сошлась — ROLLBACK")
                await tx.rollback()
                return 1

            if apply:
                await tx.commit()
                print("\nCOMMIT — изменения сохранены.")
            else:
                await tx.rollback()
                print("\nROLLBACK — dry-run, изменения откатаны.")
        except Exception:
            await tx.rollback()
            raise
    finally:
        await conn.close()

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить COMMIT.")
    args = ap.parse_args()
    exit_code = asyncio.run(main(apply=args.apply))
    raise SystemExit(exit_code)
