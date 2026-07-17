"""tsk-261: четыре контентных пункта приёмки QA (B3, B5, B8, B9). Прод, через /db-check.

Сверено живьём (voir tsk-261), правится прицельно:

B3 — задача 5501 (`wp:python-podrostki-tema-6-cikly#q10`). Код — цикл `while x < 5`, а среди
     вариантов ответа стоит «range слишком большой» — ссылка на конструкцию, которой в коде нет.
     Меняем вариант b на правдоподобное заблуждение про while (не про range).

B5 — задача 5747 (`wp:python-podrostki-tema-9-turtle#q5`). Код рисует треугольник (`forward`/
     `right`), команды `penup` в нём НЕТ, а вопрос — «Зачем нужна команда t.penup()?». Ученик
     ищет её в коде и не находит. Развязываем вопрос от кода: делаем его понятийным (что
     `penup` вообще делает), убираем вводящий в заблуждение код-блок. Ответы не трогаем —
     вариант a «Поднять перо…» остаётся верным.

B8 — 16 материалов «Проверь себя» (по одному на Задание 1..16 ОГЭ, курсы 1111..1181), ВСЕ
     пустые: `<h2>Проверь себя</h2>` без тела. QA поймала один (2361), но пусто везде — класс,
     не экземпляр. Дописываем короткое интро под заголовок. Правим только реально пустые
     (len < 60), заголовок сохраняем как есть.

B9 — задача 6352 (`oge:reshu:t1:18031`). Формулировка «Ученик написал стихотворный отрывок…
     Найдите лишнее слово», а самого отрывка НЕТ — задание нерешаемо. Восстанавливаем стих по
     первоисточнику (РешуОГЭ 18031): «Скользя по утреннему снегу…». Ответ «скользя» (7 букв +
     пробел = 8 байт) от этого становится выводимым. Эталон не трогаем.

Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

# --- B3: вариант b (index 1) задачи 5501 ---
B3_ID = 5501
B3_OLD_B = "range слишком большой"
B3_NEW_B = "Цикл while работает без остановки, если внутри нет команды break"

# --- B5: понятийная формулировка без кода, задача 5747 ---
B5_ID = 5747
B5_NEW_STEM = (
    "<p>В черепашьей графике есть команда <code>t.penup()</code> — «поднять перо». "
    "Зачем она нужна?</p>"
)

# --- B8: интро для пустых «Проверь себя» ---
B8_INTRO = (
    "\n<p>Небольшая проверка по теме этого задания. Реши задачи ниже — они соберут то, что "
    "ты разобрал в теории. Ответы проверяются автоматически.</p>"
)

# --- B9: восстановленный стих, задача 6352 ---
B9_ID = 6352
B9_NEW_STEM = (
    "В кодировке Windows-1251 каждый символ кодируется 8 битами. "
    "Ученик написал стихотворный отрывок:\n\n"
    "Скользя по утреннему снегу,\n"
    "Друг милый, предадимся бегу\n"
    "Нетерпеливого коня\n"
    "И навестим поля пустые...\n\n"
    "Но одно слово он написал два раза подряд с одним пробелом между ними. Размер полученного "
    "текста оказался на 8 байт больше, чем размер нужного предложения. Найдите лишнее слово. "
    "Ответ запишите словом.\n\nИсточник: РешуОГЭ, задача 18031"
)


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        raise RuntimeError("DATABASE_URL не прод (5.42.107.253) — передай прод-DSN из .mcp.json")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            # ---- B3 ----
            b = await conn.fetchval(
                "SELECT task_content->'options'->1->>'text' FROM tasks WHERE id=$1", B3_ID)
            if b != B3_OLD_B:
                raise RuntimeError(f"B3 {B3_ID}: вариант b не {B3_OLD_B!r}, а {b!r} — стоп")
            await conn.execute(
                "UPDATE tasks SET task_content = jsonb_set(task_content,'{options,1,text}', to_jsonb($2::text)) WHERE id=$1",
                B3_ID, B3_NEW_B)
            chk = await conn.fetchval("SELECT task_content->'options'->1->>'text' FROM tasks WHERE id=$1", B3_ID)
            assert chk == B3_NEW_B and "range" not in chk, "B3 не применился"
            print(f"OK B3 {B3_ID}: вариант «range слишком большой» заменён на про while")

            # ---- B5 ----
            stem5 = await conn.fetchval("SELECT task_content->>'stem' FROM tasks WHERE id=$1", B5_ID)
            if "penup" not in stem5 or "turtle.Turtle" not in stem5:
                raise RuntimeError(f"B5 {B5_ID}: stem не тот, что ожидали — стоп")
            await conn.execute(
                "UPDATE tasks SET task_content = jsonb_set(task_content,'{stem}', to_jsonb($2::text)) WHERE id=$1",
                B5_ID, B5_NEW_STEM)
            chk = await conn.fetchval("SELECT task_content->>'stem' FROM tasks WHERE id=$1", B5_ID)
            assert chk == B5_NEW_STEM and "turtle.Turtle" not in chk and "penup" in chk, "B5 не применился"
            print(f"OK B5 {B5_ID}: вопрос про penup развязан от кода без penup")

            # ---- B8 (16 «Проверь себя») ----
            rows = await conn.fetch(
                "SELECT id, content->>'text' t FROM materials WHERE title='Проверь себя' AND length(content->>'text') < 60")
            for r in rows:
                if "Проверь себя" not in r["t"] or "<p>" in r["t"]:
                    raise RuntimeError(f"B8 {r['id']}: материал не пустой-как-ожидали ({r['t']!r})")
                await conn.execute(
                    "UPDATE materials SET content = jsonb_set(content,'{text}', to_jsonb($2::text)) WHERE id=$1",
                    r["id"], r["t"] + B8_INTRO)
            left = await conn.fetchval(
                "SELECT count(*) FROM materials WHERE title='Проверь себя' AND length(content->>'text') < 60")
            assert left == 0, f"B8: осталось пустых {left}"
            print(f"OK B8: интро дописано в {len(rows)} материалов «Проверь себя», пустых 0")

            # ---- B9 ----
            stem9 = await conn.fetchval("SELECT task_content->>'stem' FROM tasks WHERE id=$1", B9_ID)
            if "стихотворный отрывок" not in stem9 or "Скользя по утреннему" in stem9:
                raise RuntimeError(f"B9 {B9_ID}: stem не пустой-как-ожидали (уже со стихом?) — стоп")
            ans = await conn.fetchval(
                "SELECT solution_rules->'short_answer'->'accepted_answers'->0->>'value' FROM tasks WHERE id=$1", B9_ID)
            await conn.execute(
                "UPDATE tasks SET task_content = jsonb_set(task_content,'{stem}', to_jsonb($2::text)) WHERE id=$1",
                B9_ID, B9_NEW_STEM)
            chk = await conn.fetchval("SELECT task_content->>'stem' FROM tasks WHERE id=$1", B9_ID)
            ans2 = await conn.fetchval(
                "SELECT solution_rules->'short_answer'->'accepted_answers'->0->>'value' FROM tasks WHERE id=$1", B9_ID)
            assert "Скользя по утреннему" in chk, "B9 стих не вставлен"
            assert ans2 == ans, f"B9: эталон изменился {ans!r}→{ans2!r}"
            print(f"OK B9 {B9_ID}: стих восстановлен; эталон {ans!r} не тронут")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    try:
        asyncio.run(main(apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
