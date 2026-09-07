# -*- coding: utf-8 -*-
"""tsk-798: разметка приоритета номеров ЕГЭ в сокращённой программе.

Порядок утверждён оператором 05.09: сначала номера, дающие балл при наименьших
затратах, в конце — требующие сильного программирования. Из данных этот
порядок не выводится: сложность заданий внутри номеров одинаковая, `HARD` есть
только у двух подкурсов из 25.

Меньше — входит в сокращённую программу раньше. Подкурсы «Сложные задания»
(1378) и «Старые задания ЕГЭ» (1489) намеренно остаются NULL: первый вне
зачёта, второй пуст.

Запуск (после протокола `/db-check`):
    DBCHECK_OK=1 python scripts/tsk798_set_program_priority.py --apply
Без `--apply` — только показывает план.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

#: Порядок включения в сокращённую программу ЕГЭ: (метка, id подкурса).
#:
#: Python и номера ЕГЭ идут ОДНИМ списком, вперемешку — иначе не работает ни
#: одно из двух требований. Поставь Python целиком вперёд — он не выпадет
#: никогда, и при позднем старте ученик получит один язык без единого номера
#: (ровно это и увидел оператор 07.09). Поставь целиком назад — выпадут циклы,
#: без которых не решить номера 16, 17, 22, 24-27.
#:
#: Поэтому раскладка такая: сначала номера, где программирование не нужно
#: вовсе, вместе с базой языка; затем номера с простым кодом и темы, которые
#: им нужны; в конце — продвинутый Python (функции, словари, множества,
#: рекурсия) вместе с трудными номерами, которые без него всё равно не берутся.
ORDER: list[tuple[str, int]] = [
    ("py установка", 90),    # Как установить Python
    ("py первая", 106),      # Первая программа. Основные конструкции
    ("1", 140),              # Информационные модели
    ("2", 148),              # Таблицы истинности
    ("3", 138),              # Базы данных в Excel
    ("4", 155),              # Неравномерное кодирование, условие Фано
    ("py числа", 103),       # Числа и операции
    ("py условия", 111),     # Условные конструкции
    ("9", 160),              # Агрегатные функции Excel
    ("10", 139),             # Сети и адресация
    ("11", 162),             # Объём информации
    ("12", 163),             # Машина Тьюринга
    ("13", 150),             # Анализ хода исполнения алгоритма
    ("py циклы", 110),       # Циклы — дальше без них нельзя
    ("5", 156),              # Анализ алгоритмов для исполнителей
    ("6", 157),              # Исполнитель Черепаха
    ("7", 158),              # Кодирование информации
    ("8", 159),              # Комбинаторика
    ("14", 142),             # Позиционные системы счисления
    ("15", 143),             # Логические операции
    ("py строки", 108),      # Работа со строками
    ("py списки", 109),      # Списки (массивы)
    ("16", 144),             # Рекурсивные функции
    ("17", 145),             # Обработка числовых последовательностей
    ("22", 149),             # Параллельные процессы
    ("24", 151),             # Обработка текста
    ("25", 152),             # Обработка числовых данных
    ("py функции", 104),     # Создание собственных функций
    ("18", 146),             # Жадные алгоритмы
    ("19-21", 147),          # Теория игр
    ("py словари", 107),     # Работа со словарями
    ("23", 1490),            # Анализ графов
    ("py множества", 105),   # Использование множеств
    ("py рекурсия", 1451),   # Рекурсия
    ("26", 153),             # Обработка данных
    ("27", 154),             # Анализ данных
]

#: Порядок для программы ОГЭ (курсы 1080 + 1454). Приоритеты общие с ЕГЭ по
#: шкале, но это не мешает: резка идёт внутри дерева СВОЕЙ программы, а номера
#: ЕГЭ в дерево ОГЭ не входят. Общие подкурсы Python (установка, первая
#: программа, числа, строки, условия, циклы) уже размечены выше — второй раз
#: их трогать нельзя, иначе порядок разъедется у ЕГЭ.
ORDER_OGE: list[tuple[str, int]] = [
    ("огэ 1", 1111),   # Измерение информации и кодировки текста
    ("огэ 2", 1112),   # Кодирование и декодирование, условие Фано
    ("огэ 3", 1120),   # Значение логического выражения
    ("огэ 4", 1128),   # Кратчайший путь по таблице дорог
    ("огэ 7", 1152),   # Сетевые адреса
    ("огэ 8", 1153),   # Поисковые запросы, логика И/ИЛИ
    ("огэ 9", 1154),   # Пути в ориентированном графе
    ("огэ 10", 1162),  # Системы счисления
    ("огэ 11", 1163),  # Поиск файла по содержимому
    ("огэ 12", 1164),  # Поиск файлов по маске
    ("огэ 13", 1178),  # Презентация или текстовый документ
    ("огэ 14", 1179),  # Обработка данных в электронной таблице
    ("огэ 5", 1129),   # Формальный исполнитель
    ("огэ 6", 1130),   # Программа с условным оператором
    ("огэ 15", 1180),  # Короткий алгоритм: Робот в КуМир
    ("огэ 16", 1181),  # Программа на анализ последовательности чисел
]


def load_prod_dsn_asyncpg_style() -> str:
    """DSN прод-роли из `.mcp.json` в формате SQLAlchemy (секрет не печатаем)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    return (
        f"postgresql+asyncpg://{parts.username}:{unquote(parts.password)}"
        f"@{parts.hostname}:{parts.port}{parts.path}"
    )


async def main(apply: bool) -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # ОГЭ идёт следом за ЕГЭ по той же шкале: резка считается внутри дерева
    # своей программы, и номера одной в дерево другой не попадают.
    full_order = ORDER + ORDER_OGE

    async with factory() as db:
        titles = {
            int(r[0]): r[1]
            for r in (
                await db.execute(
                    text("SELECT id, title FROM courses WHERE id = ANY(:ids)"),
                    {"ids": [cid for _, cid in full_order]},
                )
            ).all()
        }
        missing = [cid for _, cid in full_order if cid not in titles]
        if missing:
            print(f"ОШИБКА: подкурсы не найдены: {missing}")
            await engine.dispose()
            return 1

        print(f"{'приоритет':>10}{'номер':>8}{'id':>7}  название")
        for priority, (number, course_id) in enumerate(full_order, start=1):
            print(
                f"{priority:>10}{number:>8}{course_id:>7}  {titles[course_id][:52]}"
            )

        if not apply:
            print("\nЭто предпросмотр. Записать: добавить --apply")
            await engine.dispose()
            return 0

        # Транзакция уже открыта первым SELECT — коммитим её явно, а не через
        # `db.begin()`: вложенный begin на той же сессии падает.
        for priority, (_, course_id) in enumerate(full_order, start=1):
            await db.execute(
                text("UPDATE courses SET program_priority = :p WHERE id = :c"),
                {"p": priority, "c": course_id},
            )
        await db.commit()

        rows = (
            await db.execute(
                text(
                    "SELECT id, program_priority FROM courses "
                    " WHERE program_priority IS NOT NULL ORDER BY program_priority"
                )
            )
        ).all()
        print(f"\nРазмечено подкурсов: {len(rows)} (ожидалось {len(full_order)})")
        if len(rows) != len(full_order):
            print("ОШИБКА: размечено не столько, сколько планировали")
            await engine.dispose()
            return 1
        expected = [(cid, i) for i, (_, cid) in enumerate(full_order, start=1)]
        if sorted(rows, key=lambda r: r[1]) != sorted(expected, key=lambda r: r[1]):
            print("ОШИБКА: порядок в базе не совпал с планом")
            await engine.dispose()
            return 1

    await engine.dispose()
    print("Готово: приоритеты проставлены и сверены с планом.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="записать в базу")
    sys.exit(asyncio.run(main(parser.parse_args().apply)))
