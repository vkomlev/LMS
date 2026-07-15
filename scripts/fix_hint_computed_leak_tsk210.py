"""tsk-210 (аудит, класс сливов №2 — вычислительный): подсказка даёт финальный ответ.

Дефект: SA-подсказка ОГЭ считает до финального числа/значения, совпадающего с ответом
(«600+300−100=800», «10=8+2 значит 1010», «192.+168.5+.10=192.168.5.10»). Ровно анти-паттерн
wp-publish правила 1 / methodist §3.4a: hint даёт МЕТОД/формулу, не финальное значение.

Фикс — per-task method-form переформулировка (не механический срез: формат вычислений разный).
Скрипт правит ТОЛЬКО задания, всё ещё сливающие (ответ в текущей подсказке), и проверяет, что
новая подсказка ответа НЕ содержит. Правится только LMS-прод.

Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import json
import os
import sys

import asyncpg
from dotenv import load_dotenv

# id -> новая подсказка (метод/формула без финального значения)
NEW_HINTS: dict[int, str] = {
    # 1111 — Unicode/биты
    6307: "В одном байте 8 бит — раздели количество бит на 8.",
    6308: "8 бит = 1 байт на символ; раздели освободившееся число бит на размер одного символа.",
    6309: "16 бит = 2 байта на символ; раздели освободившиеся биты на 16 (или байты на 2).",
    6310: "16 бит = 2 байта; раздели биты на 16 — получишь число символов, затем вычти знаки препинания.",
    6312: "Раздели биты на 16 — получишь длину слова в символах; найди в списке слово именно такой длины.",
    6313: "Раздели биты на 16, вычти запятые — получишь длину слова; найди слово такой длины в списке.",
    # 1152 — восстановление IP/email
    6680: "Соедини фрагменты так, чтобы вышли 4 числа по правилам IP (каждое ≤ 255).",
    6681: "Соедини фрагменты в порядке: имя, затем @домен, затем зона (.ru).",
    6683: "Расставь фрагменты так, чтобы каждое из четырёх чисел было ≤ 255.",
    # 1153 — поисковые запросы (включение-исключение)
    6687: "Число страниц объединения = сумма двух запросов минус их пересечение.",
    6688: "Из формулы A|B = A + B − (A&B) вырази неизвестный запрос.",
    6689: "Сумма двух запросов минус их пересечение.",
    6690: "Из A|B = A + B − (A&B) вырази неизвестное слагаемое.",
    6691: "Из A|B = A + B − (A&B) вырази пересечение.",
    6693: "«Юг & (Восток|Запад)» = «Юг&Восток» + «Юг&Запад» − X — вырази X.",
    6694: "Из A|B = A + B − (A&B) вырази пересечение (третий запрос для этого вопроса не нужен).",
    # 1162 — системы счисления
    6802: "Где в двоичной записи стоит 1, прибавь соответствующую степень двойки (8, 4, 2, 1).",
    6803: "Разложи число на сумму степеней двойки (8, 4, 2, 1) и отметь занятые разряды.",
    6804: "Сложи степени двойки на позициях, где стоят единицы.",
    6805: "Разложи число на сумму степеней двойки (64, …, 1) и запиши занятые разряды.",
    6806: "Посчитай, сколько единиц в двоичной записи числа.",
    6808: "Переведи двоичное в десятичное (сумма степеней двойки), затем вычти 100.",
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
                    print(f"  SKIP {tid}: не найдено")
                    skipped += 1
                    continue
                hints = row["hints"]
                if isinstance(hints, str):
                    hints = json.loads(hints)
                ans = row["ans"]
                cur = " ".join(hints) if isinstance(hints, list) else ""
                if not ans or ans.lower() not in cur.lower():
                    print(f"  SKIP {tid}: ответ уже не в подсказке (не сливает) — не трогаю")
                    skipped += 1
                    continue
                if ans.lower() in new_hint.lower():
                    raise RuntimeError(f"{tid}: новая подсказка всё ещё содержит ответ {ans!r} — стоп")
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
