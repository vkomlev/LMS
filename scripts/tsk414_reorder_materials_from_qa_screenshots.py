# -*- coding: utf-8 -*-
"""tsk-414 (доделка): полный порядок материалов курсов 108 "Работа со строками" и
111 "Условные конструкции в Python" — по картинкам из писем QA, которые не
сохранились при первом извлечении текста (sharing.mail.ru), но были подняты
повторно из исходных писем (скриншоты навигатора с ручными пометками порядка).

Курс 108: скриншот-эталон (ЧАСТЬ 1, без цифр — просто финальный вид списка).
Курс 111: скриншот с рукописными номерами у каждого пункта (1..25) — целевой
порядок = сортировка по этим номерам.

ВАЖНО: это ПОЛНЫЙ пересчёт order_position по найденному эталону, а не дельта —
заменяет собой более ранний частичный фикс курса 108
(tsk414_reorder_material_264_483_before_string_module.py), который был сделан
по неполному текстовому описанию письма (без картинки) и оказался НЕ полным
порядком, который имела в виду QA.

Запуск: dry-run по умолчанию;
  python scripts/tsk414_reorder_materials_from_qa_screenshots.py
  DBCHECK_OK=1 python scripts/tsk414_reorder_materials_from_qa_screenshots.py --apply
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

# Курс 108 "Работа со строками" — целевой порядок по скриншоту письма ЧАСТЬ 1.
COURSE_108_ORDER = [
    259,  # Запись строк (text)
    476,  # Создание строк: одинарные/двойные/тройные кавычки (video)
    477,  # Многострочные строки в переменной (video)
    260,  # Строковые операторы (text)
    475,  # Операции со строками (video)
    261,  # Срезы и индексы (text)
    474,  # Срезы в Python (video)
    478,  # Индексы. Извлечение символов (video)
    262,  # Как посчитать количество символов в строке (text)
    263,  # Строковые методы (text)
    479,  # Методы find(), count() и срезы (video)
    480,  # Метод replace() (video)
    481,  # Прочие строковые методы (video)
    482,  # Строковые методы и функции (video)
    264,  # Форматирование строк (text)
    483,  # Форматирование строк (video)
    266,  # Модульstring (text)
    473,  # Сводный видеоурок по строкам (video)
]

# Курс 111 "Условные конструкции в Python" — целевой порядок по рукописным
# номерам на скриншоте письма ЧАСТЬ 2 (номер у пункта = позиция в списке).
COURSE_111_ORDER = [
    294,  # 1. Тип данных bool, значение True и False (text)
    293,  # 2. Оператор if (text)
    485,  # 3. Оператор if (video)
    295,  # 4. Операторы сравнения (text)
    484,  # 5. Условия в Python (video)
    297,  # 6. Использование else (text)
    486,  # 7. Инструкция else (video)
    487,  # 8. Отступы. Конструкция else (video)
    298,  # 9. Конструкция elif (text)
    488,  # 10. Логические операторы и elif (video)
    489,  # 11. Инструкция elif (video)
    299,  # 12. Логические операторы and, or, not (text)
    490,  # 13. and, or, not (video)
    300,  # 14. Вложенные инструкции if (text)
    491,  # 15. Вложенные if (video)
    492,  # 16. Вложенные if (часть 2) (video)
    296,  # 17. Несколько примеров использования if и операторов сравнения (text)
    304,  # 18. Оператор in (text)
    303,  # 19. Оператор is (text)
    305,  # 20. Заглушки кода. Ключевое слово pass (text)
    301,  # 21. Цепочки сравнений (text)
    302,  # 22. Тернарный оператор (text)
    307,  # 23. Конструкция match-case (text)
    308,  # 24. Моржовый оператор (text)
    493,  # 25. Условные конструкции в Python (обзор) (video)
]

COURSES = {108: COURSE_108_ORDER, 111: COURSE_111_ORDER}


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
            full_plan: list[tuple[int, int, int, int]] = []  # course_id, id, old, new
            for course_id, target_order in COURSES.items():
                rows = await conn.fetch(
                    "SELECT id, order_position, title FROM materials "
                    "WHERE course_id = $1 AND is_active = true ORDER BY order_position",
                    course_id,
                )
                current_ids = [r["id"] for r in rows]
                assert set(current_ids) == set(target_order), (
                    f"курс {course_id}: состав материалов изменился с момента разведки — "
                    f"было {sorted(target_order)}, сейчас {sorted(current_ids)}"
                )
                by_id = {r["id"]: r for r in rows}
                plan = []
                for new_pos, mid in enumerate(target_order, start=1):
                    old_pos = by_id[mid]["order_position"]
                    if old_pos != new_pos:
                        plan.append((mid, by_id[mid]["title"], old_pos, new_pos))
                print(f"--- курс {course_id}: {len(plan)} материалов меняют order_position ---")
                for mid, title, old, new in plan:
                    print(f"  id={mid} ({title}): {old} -> {new}")
                    full_plan.append((course_id, mid, old, new))

            if apply:
                await conn.execute("SELECT set_config('app.skip_material_order_trigger', 'true', true)")
                for _course_id, mid, _old, new in full_plan:
                    await conn.execute(
                        "UPDATE materials SET order_position = $1 WHERE id = $2",
                        new, mid,
                    )
                await conn.execute("SELECT set_config('app.skip_material_order_trigger', 'false', true)")

                for course_id in COURSES:
                    dup = await conn.fetchval(
                        "SELECT count(*) FROM ("
                        "  SELECT order_position FROM materials WHERE course_id=$1 AND is_active=true"
                        "  GROUP BY order_position HAVING count(*) > 1"
                        ") d",
                        course_id,
                    )
                    if dup:
                        raise AssertionError(f"курс {course_id}: {dup} дублирующихся order_position после апдейта")
                for _course_id, mid, _old, new in full_plan:
                    actual = await conn.fetchval("SELECT order_position FROM materials WHERE id=$1", mid)
                    if actual != new:
                        raise AssertionError(f"id={mid}: после UPDATE order_position={actual}, ожидалось {new}")
                print(f"\nВерификация внутри транзакции: OK, {len(full_plan)} строк, дублей нет.")

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
