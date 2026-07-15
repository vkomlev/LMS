"""tsk-210 (аудит, класс сливов №2 — раскрытие метода + вычислительный остаток).

Раскрытие метода: hint называет сам метод/ключевое слово, которое и есть ответ
(«dump — записать в файл», «text.lower()», «git init …»). Вычислительный остаток: те же
вычислительные сливы, что в fix_hint_computed_leak, но в курсах вне первой выборки.

Фикс — per-task переформулировка: для метода — ОПИСАТЬ назначение без имени метода (ученик
вспоминает сам); для вычисления — метод/формула без финального значения (methodist §3.4a).
Правит только всё ещё сливающие; verify «ответа нет в новой подсказке». LMS-прод.

Ложные срабатывания эвристики НЕ трогаем (8826 «10»⊂«1024», 6811 Байкал в стеме,
7792/7820 «ru»⊂example.ru).

Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import json
import os
import sys

import asyncpg
from dotenv import load_dotenv

NEW_HINTS: dict[int, str] = {
    # --- раскрытие метода: описать назначение, не называя метод ---
    6176: "Бот подтверждает нажатие inline-кнопки, чтобы у пользователя пропали «часики» — вспомни этот метод из урока про callback.",
    5842: "Бот подтверждает нажатие inline-кнопки, чтобы у пользователя пропали «часики» — вспомни этот метод из урока про callback.",
    6180: "Метод модуля json, который ЗАПИСЫВАЕТ объект в файл (парный ему load — читает).",
    5844: "Метод модуля json, который ЗАПИСЫВАЕТ объект в файл (парный ему load — читает).",
    6191: "Класс из модуля threading, который выполняет функцию ПОЗЖЕ, через заданное число секунд.",
    5850: "Класс из модуля threading, который выполняет функцию ПОЗЖЕ, через заданное число секунд.",
    6193: "У метода split есть параметр, ограничивающий число разрезов — чтобы разрезать строку только по первому пробелу.",
    5851: "У метода split есть параметр, ограничивающий число разрезов — чтобы разрезать строку только по первому пробелу.",
    5852: "Метод строки, который проверяет, что все её символы — цифры (возвращает True/False).",
    5855: "Парный к upper метод строки — приводит буквы к НИЖНЕМУ регистру.",
    5860: "Первая команда git — «инициализировать», создать новое пустое хранилище в папке проекта.",
    6928: "Сбалансированный по цене и качеству класс модели Claude — не самый мощный (Opus) и не самый быстрый (Haiku).",
    7099: "В отчёте нужны не сырые факты, а выводы-озарения, которые из этих фактов следуют.",
    # --- вычислительный остаток: метод без финального значения ---
    5553: "round(x, 2) оставляет два знака после запятой; раздели 10 на 3 и округли результат сам.",
    5611: "math.pi — число «пи»; round(…, 2) оставит два знака после запятой.",
    5626: "Оставь только числа, кратные 3, и среди них найди самое большое.",
    6472: "Раскрой НЕ(А ИЛИ Б) = (НЕ А) И (НЕ Б), найди границы X и возьми наибольшее целое.",
    6473: "Раскрой отрицание в первом условии, соедини со вторым (X>10) и возьми наибольшее целое из диапазона.",
    6700: "Перемножь число путей до промежуточной точки на число путей после неё.",
    6815: "Маска ищет файлы во ВСЕХ вложенных папках — сложи подходящие из каждой.",
    6816: "Из общего числа файлов вычти число .pdf.",
    6942: "Оставь числа, кратные 5, и сложи их.",
    6944: "Начни максимум с заведомо очень маленького числа — меньше любого возможного в последовательности.",
}


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    fixed = 0
    skipped = 0
    try:
        async with conn.transaction():
            for tid, new_hint in NEW_HINTS.items():
                row = await conn.fetchrow(
                    "SELECT task_content->'hints_text' AS hints, "
                    "solution_rules->'short_answer'->'accepted_answers'->0->>'value' AS ans "
                    "FROM tasks WHERE id=$1 AND is_active", tid
                )
                if row is None:
                    print(f"  SKIP {tid}: не найдено"); skipped += 1; continue
                hints = row["hints"]
                if isinstance(hints, str):
                    hints = json.loads(hints)
                ans = row["ans"]
                cur = " ".join(hints) if isinstance(hints, list) else ""
                if not ans or ans.lower() not in cur.lower():
                    print(f"  SKIP {tid}: ответ уже не в подсказке"); skipped += 1; continue
                if ans.lower() in new_hint.lower():
                    raise RuntimeError(f"{tid}: новая подсказка содержит ответ {ans!r} — стоп")
                await conn.execute(
                    "UPDATE tasks SET task_content = jsonb_set(task_content,'{hints_text}', $2::jsonb) WHERE id=$1",
                    tid, json.dumps([new_hint], ensure_ascii=False),
                )
                fixed += 1
                if fixed <= 6:
                    print(f"  OK {tid} (ответ {ans!r}): -> {new_hint}")
            print(f"\nИтог: переформулировано {fixed}, пропущено {skipped}")
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
