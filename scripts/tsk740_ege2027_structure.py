# -*- coding: utf-8 -*-
"""tsk-740, партия 1: перестройка дерева курса 112 под демоверсию ЕГЭ-2027.

ЗАЧЕМ
Спецификация КИМ ЕГЭ 2027 (§ 11) сдвигает три темы по цепочке и снимает одну:

  10 «Информационный поиск средствами текстового процессора» -> снято с экзамена
  13 «Умение использовать маску подсети»                     -> становится 10
  23 «Умение анализировать ход исполнения алгоритма»          -> становится 13
  23                                                          -> новая тема, графы

Здесь делается только структурная часть: снятая тема уезжает в отдельный раздел
«Старые задания ЕГЭ», а два оставшихся блока переименовываются и встают на новые
позиции. Тексты материалов и условий — партия 2, формат задания 27 — партия 3,
новый блок 23 — партия 4.

РЕШЕНИЯ ОПЕРАТОРА (2026-09-01)
- Снятую тему не удалять и не деактивировать: отдельный раздел по образцу
  «Сложных заданий», прогресс прошедших сохраняется.
- Номера переставлять переименованием существующих курсов, а не переносом
  заданий в новые: сохраняются id, прогресс, попытки и ссылки из бота и с сайта.
- Пересдачу не требовать.

ПОЧЕМУ РАЗДЕЛ — ПОСЛЕДНИМ ПОДКУРСОМ
`_collect_courses_in_order` обходит дерево post-order (дети раньше родителя),
поэтому узел, подвешенный к номерному курсу, попал бы в обход РАНЬШЕ основного
потока. Контейнер в конце корня даёт «в конец» и в движке, и в pre-order списке
разделов. Тот же приём, что в tsk-347 для «Сложных заданий».

ПРОГРЕСС УЧЕНИКОВ
Не трогается ни одной строкой: `task_results` и `attempts` привязаны к заданию,
а не к курсу; корень (112) у всех перемещаемых узлов остаётся прежним, поэтому
пара «корень + задание» из tsk-264 не меняется. Заданий между курсами скрипт
не переносит вовсе — двигаются только связи `course_parents` и заголовки.

УРОВЕНЬ ОБЯЗАТЕЛЬНОСТИ
Заданиям снятой темы ставится `requirement_level='recommended'` — вне обхода
next-item и вне знаменателя завершения курса. Ставится и неактивным тоже: иначе
реактивация вернёт задание в основной поток (урок tsk-347).

Запуск: вхолостую по умолчанию;
  python scripts/tsk740_ege2027_structure.py
  DBCHECK_OK=1 python scripts/tsk740_ege2027_structure.py --apply
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

ROOT_COURSE_ID = 112
HARD_CONTAINER_ID = 1378

LEGACY_UID = "lms:tsk740:legacy:root"
LEGACY_TITLE = "Старые задания ЕГЭ"
LEGACY_DESCRIPTION = (
    "Темы, которые были в ЕГЭ до 2027 года и теперь из экзамена сняты. "
    "Для завершения курса они не нужны — блок оставлен, чтобы навык и уже "
    "пройденное никуда не делись."
)

# Курсы, уезжающие в «Старые задания ЕГЭ»: (id, откуда, новый заголовок, порядок).
V_STARYE: list[tuple[int, int, str, int]] = [
    (141, ROOT_COURSE_ID, "Поиск информации в документах (в ЕГЭ до 2027 — задание 10)", 1),
    (1388, HARD_CONTAINER_ID, "Поиск информации в документах. Сложные", 2),
]

# Переезд номеров: (id курса, родитель, новый заголовок, новый order_number).
PEREEZD: list[tuple[int, int, str, int]] = [
    (139, ROOT_COURSE_ID, "Задание 10 ЕГЭ по информатике. Организация компьютерных сетей и адресация", 11),
    (1391, HARD_CONTAINER_ID, "Задание 10. Сложные", 11),
    (150, ROOT_COURSE_ID, "Задание 13 ЕГЭ по информатике. Анализ хода исполнения алгоритма", 14),
    (1399, HARD_CONTAINER_ID, "Задание 13. Сложные", 14),
]

# Заданиям снятой темы — «рекомендуемое», включая неактивные.
KURSY_SNYATOJ_TEMY = [141, 1388]


def _dsn() -> str:
    """Прод-DSN learn: из окружения либо из .mcp.json (паттерн tsk-347/362/366)."""
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


async def _snimok(conn: asyncpg.Connection) -> dict:
    """Состояние до правки — для отката и для сверки после."""
    derevo = await conn.fetch(
        "SELECT cp.parent_course_id, cp.order_number, c.id, c.title "
        "FROM course_parents cp JOIN courses c ON c.id = cp.course_id "
        "WHERE cp.parent_course_id = ANY($1::int[]) "
        "ORDER BY cp.parent_course_id, cp.order_number NULLS LAST, c.id",
        [ROOT_COURSE_ID, HARD_CONTAINER_ID],
    )
    urovni = await conn.fetch(
        "SELECT course_id, requirement_level, is_active, count(*) AS n "
        "FROM tasks WHERE course_id = ANY($1::int[]) "
        "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
        KURSY_SNYATOJ_TEMY,
    )
    rezultaty = await conn.fetchval(
        "SELECT count(*) FROM task_results tr JOIN tasks t ON t.id = tr.task_id "
        "WHERE t.course_id = ANY($1::int[])",
        [141, 1388, 139, 1391, 150, 1399],
    )
    return {
        "derevo": [dict(r) for r in derevo],
        "urovni": [dict(r) for r in urovni],
        "rezultatov_v_zatronutyh": int(rezultaty),
    }


async def _sozdat_razdel(conn: asyncpg.Connection, apply: bool) -> tuple[int | None, bool]:
    """Контейнер «Старые задания ЕГЭ». Идемпотентно по course_uid.

    Вызывать только под заглушённым `app.skip_course_parent_order_trigger`:
    иначе BEFORE-триггер `set_course_parent_order_number` при явном
    `order_number` сдвинет вправо всех соседей.
    """
    sushchestvuet = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", LEGACY_UID)
    if sushchestvuet is not None:
        return int(sushchestvuet), False
    if not apply:
        return None, True
    course_id = await conn.fetchval(
        "INSERT INTO courses (title, access_level, description, is_required, course_uid, is_public_demo) "
        "VALUES ($1, 'self_guided'::access_level_type, $2, false, $3, false) RETURNING id",
        LEGACY_TITLE,
        LEGACY_DESCRIPTION,
        LEGACY_UID,
    )
    sled = await conn.fetchval(
        "SELECT COALESCE(MAX(order_number), 0) + 1 FROM course_parents WHERE parent_course_id = $1",
        ROOT_COURSE_ID,
    )
    await conn.execute(
        "INSERT INTO course_parents (course_id, parent_course_id, order_number) VALUES ($1, $2, $3)",
        course_id,
        ROOT_COURSE_ID,
        int(sled),
    )
    return int(course_id), True


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        do = await _snimok(conn)
        put_snimka = project_root / "reviews" / "2026-09-01-tsk740-structure-before.json"
        put_snimka.write_text(json.dumps(do, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"Снимок «до» записан: {put_snimka}")
        print(f"Строк результатов в затронутых курсах до правки: {do['rezultatov_v_zatronutyh']}")

        print("\n=== ПЛАН ===")
        print(f"1. Раздел «{LEGACY_TITLE}» (uid {LEGACY_UID}) — подкурсом курса {ROOT_COURSE_ID}, последним.")
        for cid, otkuda, zagolovok, poryadok in V_STARYE:
            print(f"2. Курс {cid}: {otkuda} -> раздел, порядок {poryadok}, заголовок «{zagolovok}»")
        for cid, roditel, zagolovok, poryadok in PEREEZD:
            print(f"3. Курс {cid}: остаётся у {roditel}, порядок -> {poryadok}, заголовок «{zagolovok}»")
        skolko = await conn.fetchval(
            "SELECT count(*) FROM tasks WHERE course_id = ANY($1::int[]) "
            "AND requirement_level IS DISTINCT FROM 'recommended'",
            KURSY_SNYATOJ_TEMY,
        )
        print(f"4. requirement_level -> recommended: {skolko} заданий (включая неактивные)")

        if not apply:
            print("\nВхолостую. Записи не было. Для правки: DBCHECK_OK=1 python "
                  "scripts/tsk740_ege2027_structure.py --apply")
            return

        async with conn.transaction():
            # Триггер order_position глушим session-var, не ALTER TABLE:
            # последнее берёт ACCESS EXCLUSIVE лок на всю tasks (урок tsk-345).
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            # На course_parents свой BEFORE-триггер: при явном order_number он
            # сдвигает вправо ВСЕХ соседей, то есть ломает нумерацию по номерам
            # заданий. Глушим на всю транзакцию — тогда тело триггера не
            # выполнится ни разу и не сбросит флаг обратно в 'false'.
            await conn.execute("SELECT set_config('app.skip_course_parent_order_trigger', 'true', true)")

            razdel_id, sozdan = await _sozdat_razdel(conn, apply=True)
            print(f"\nРаздел: id={razdel_id} ({'создан' if sozdan else 'уже был'})")

            # Перевешивание — UPDATE связи, а не DELETE+INSERT: на DELETE висит
            # AFTER-триггер `reorder_course_parents_after_delete` БЕЗ заглушки,
            # он сдвинул бы влево все курсы правее удалённого.
            for cid, otkuda, zagolovok, poryadok in V_STARYE:
                perevesheno = await conn.execute(
                    "UPDATE course_parents SET parent_course_id = $3, order_number = $4 "
                    "WHERE course_id = $1 AND parent_course_id = $2",
                    cid, otkuda, razdel_id, poryadok,
                )
                if perevesheno != "UPDATE 1":
                    raise RuntimeError(
                        f"Курс {cid}: связь с родителем {otkuda} не найдена ({perevesheno}). "
                        "Дерево изменилось — разбираться вручную."
                    )
                await conn.execute("UPDATE courses SET title = $2 WHERE id = $1", cid, zagolovok)
                print(f"  курс {cid}: {otkuda} -> раздел {razdel_id}, порядок {poryadok}, переименован")

            for cid, roditel, zagolovok, poryadok in PEREEZD:
                obnovleno = await conn.execute(
                    "UPDATE course_parents SET order_number = $3 "
                    "WHERE course_id = $1 AND parent_course_id = $2",
                    cid, roditel, poryadok,
                )
                if obnovleno != "UPDATE 1":
                    raise RuntimeError(f"Курс {cid}: связь с {roditel} не найдена ({obnovleno}).")
                await conn.execute("UPDATE courses SET title = $2 WHERE id = $1", cid, zagolovok)
                print(f"  курс {cid}: порядок {poryadok}, переименован")

            izmeneno = await conn.execute(
                "UPDATE tasks SET requirement_level = 'recommended' "
                "WHERE course_id = ANY($1::int[]) AND requirement_level IS DISTINCT FROM 'recommended'",
                KURSY_SNYATOJ_TEMY,
            )
            print(f"  уровень обязательности: {izmeneno}")

            # Верификация — ВНУТРИ транзакции: любой сбой инварианта поднимает
            # исключение и откатывает всё, а не оставляет полуправку на проде.
            posle = await _snimok(conn)
            print("\n=== ПРОВЕРКА (до коммита) ===")
            print(f"Строк результатов в затронутых курсах: {posle['rezultatov_v_zatronutyh']} "
                  f"(было {do['rezultatov_v_zatronutyh']})")
            if posle["rezultatov_v_zatronutyh"] != do["rezultatov_v_zatronutyh"]:
                raise RuntimeError("Число строк результатов изменилось — недопустимо.")

            ostalos = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = ANY($1::int[]) "
                "AND requirement_level IS DISTINCT FROM 'recommended'",
                KURSY_SNYATOJ_TEMY,
            )
            print(f"Заданий снятой темы вне уровня recommended: {ostalos} (должно быть 0)")
            if ostalos:
                raise RuntimeError("Остались задания снятой темы вне recommended.")

            # Соседей трогать было нельзя: сверяем порядок всех, кого план не называл.
            tronutye = {cid for cid, *_ in V_STARYE} | {cid for cid, *_ in PEREEZD}
            bylo = {(r["parent_course_id"], r["id"]): r["order_number"] for r in do["derevo"]}
            stalo = {(r["parent_course_id"], r["id"]): r["order_number"] for r in posle["derevo"]}
            sdvinulos = [
                (rod, cid, bylo[(rod, cid)], stalo.get((rod, cid)))
                for (rod, cid) in bylo
                if cid not in tronutye and stalo.get((rod, cid)) != bylo[(rod, cid)]
            ]
            if sdvinulos:
                for rod, cid, b, s in sdvinulos:
                    print(f"  СДВИНУЛОСЬ: родитель {rod}, курс {cid}: {b} -> {s}")
                raise RuntimeError("Порядок соседних курсов поехал — сработал триггер.")
            print(f"Соседних курсов сдвинуто: 0 (проверено {len(bylo) - len(tronutye)} связей)")

            dubli = await conn.fetch(
                "SELECT parent_course_id, order_number, count(*) AS n FROM course_parents "
                "WHERE parent_course_id = ANY($1::int[]) AND order_number IS NOT NULL "
                "GROUP BY 1, 2 HAVING count(*) > 1",
                [ROOT_COURSE_ID, HARD_CONTAINER_ID, razdel_id],
            )
            if dubli:
                for d in dubli:
                    print(f"  ДУБЛЬ порядка: родитель {d['parent_course_id']}, "
                          f"order_number {d['order_number']}, курсов {d['n']}")
                raise RuntimeError("Два курса на одной позиции — разбираться вручную.")
            print("Дублей порядка внутри родителя: 0")

            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'false', true)")

        print("\n=== ДЕРЕВО ПОСЛЕ КОММИТА ===")
        derevo = await conn.fetch(
            "SELECT cp.parent_course_id AS roditel, cp.order_number AS ord, c.id, c.title "
            "FROM course_parents cp JOIN courses c ON c.id = cp.course_id "
            "WHERE cp.parent_course_id = ANY($1::int[]) "
            "ORDER BY cp.parent_course_id, cp.order_number NULLS LAST, c.id",
            [ROOT_COURSE_ID, HARD_CONTAINER_ID, razdel_id],
        )
        for r in derevo:
            print(f"  {r['roditel']:>5} | {str(r['ord']):>3} | {r['id']:>5} | {r['title']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tsk-740 партия 1: структура дерева курса 112 под ЕГЭ-2027")
    parser.add_argument("--apply", action="store_true", help="записать изменения (иначе вхолостую)")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
