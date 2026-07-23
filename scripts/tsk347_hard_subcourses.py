# -*- coding: utf-8 -*-
"""tsk-347: вынести HARD-задания ЕГЭ в отдельный опциональный блок в конец программы.

ЗАЧЕМ
Курс 112 «ЕГЭ по информатике» разросся: 1942 активных задания в 25 подкурсах, из
них 1026 — HARD. Движок сложные уже не выдаёт (`requirement_level='recommended'`
стоит у 989 из них с tsk-112), но синтабус показывает ученику ВСЕ активные
задания курса без фильтра по уровню (`me_service._SYLLABUS_TASKS_SQL`), и
«Задание 8» открывается списком на 139 позиций, из которых 94 недостижимы в
основном потоке. Это и есть перегруз, на который жалуются живые ученики.

АРХИТЕКТУРА (вариант A, согласован с оператором 2026-07-23)
Контейнер «Сложные задания» — последним (27-м) подкурсом курса 112; внутри —
по подкурсу на каждый номер ЕГЭ («Задание 8. Сложные»). Почему не подвесить
HARD-узел прямо на номерной курс: `_collect_courses_in_order` — POST-order
(сначала дети, потом сам курс), поэтому ребёнок курса 159 обходился бы РАНЬШЕ
основного потока задания 8 — противоположно «в конец». Контейнер в конце корня
даёт нужный порядок и в обходе движка, и в pre-order списке разделов
(`me_service._collect_section_meta`), а имена узлов сохраняют привязку к номеру.

ЧТО ДЕЛАЕТ
1. Создаёт контейнер и по подкурсу на каждый исходный курс с HARD-заданиями.
2. Переносит ВСЕ HARD-задания (`difficulty_id=4`), включая неактивные: активные
   не должны разъехаться с неактивными по классификации, иначе реактивация
   вернёт задание не в тот блок.
3. Ставит всем перенесённым `requirement_level='recommended'` — вне обхода
   next-item и вне знаменателя завершения курса (`learning_engine_service`
   строки 559/585/963). 37 заданий сейчас `required` (доливки после tsk-112,
   в их числе перетегированные в tsk-354) — именно они и сидят в основном потоке.
4. Переупорядочивает исходные курсы и новые подкурсы. Прямая запись идёт мимо
   `TasksService.bulk_upsert`, поэтому durable-хук tsk-345 не сработает — реордер
   вызывается явно, тем же SQL, что и хук.

ТРИГГЕР
`trg_set_task_order_position` глушится session-variable
`app.skip_task_order_trigger` (is_local), НЕ через `ALTER TABLE ... DISABLE
TRIGGER`: последнее берёт ACCESS EXCLUSIVE лок на всю таблицу `tasks` и
останавливает живой трафик учеников по всем курсам (урок tsk-345/tsk-346).
Без глушения триггер на UPDATE со сменой `order_position` начал бы сдвигать
позиции соседей в курсе-приёмнике.

ПРОГРЕСС УЧЕНИКОВ
Не трогается ни одной строкой: `task_results`/`attempts` привязаны к заданию, а
не к курсу. Попытки считаются по паре «корень + задание» (tsk-264,
`attempts.root_course_id`), а корень остаётся 112 — новые подкурсы внутри того же
дерева. Проверка поимённая (урок tsk-317), скриптом `tsk347_verify_progress.py`
до и после.

Запуск: dry-run по умолчанию;
  python scripts/tsk347_hard_subcourses.py
  DBCHECK_OK=1 python scripts/tsk347_hard_subcourses.py --apply
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
HARD_DIFFICULTY_ID = 4
CONTAINER_UID = "lms:tsk347:hard:root"
CONTAINER_TITLE = "Сложные задания"
CONTAINER_DESCRIPTION = (
    "Необязательный блок повышенной сложности. Проходить его не нужно для "
    "завершения курса — беритесь, когда основная программа уже освоена."
)

# Номер задания ЕГЭ по исходному курсу. Заведён явно, а не разбором заголовка:
# заголовки разнородны («Задание 1 ЕГЭ…», «ЕГЭ… Задание №3…», «Решение заданий
# 13 ЕГЭ…», «Задание 19-21…»), и регулярка на них — источник тихих ошибок.
NOMER_PO_KURSU: dict[int, str] = {
    140: "1",
    148: "2",
    138: "3",
    155: "4",
    156: "5",
    157: "6",
    158: "7",
    159: "8",
    160: "9",
    141: "10",
    162: "11",
    163: "12",
    139: "13",
    142: "14",
    143: "15",
    144: "16",
    145: "17",
    146: "18",
    147: "19-21",
    149: "22",
    150: "23",
    151: "24",
    152: "25",
    153: "26",
    154: "27",
}

# Реордер по сложности — тот же SQL, что у durable-хука tsk-345
# (`TasksService._reorder_tasks_by_difficulty`). Держать в паритете: расхождение
# порядка между импортом и этой миграцией даст ученику разный вид списка.
REORDER_SQL = """
WITH new_order AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            ORDER BY
                difficulty_id ASC,
                CASE task_content->>'type'
                    WHEN 'SC' THEN 1
                    WHEN 'MC' THEN 1
                    WHEN 'TA' THEN 2
                    WHEN 'SA' THEN 2
                    WHEN 'SA_COM' THEN 3
                    ELSE 99
                END ASC,
                order_position ASC NULLS LAST,
                id ASC
        ) AS new_op
    FROM tasks
    WHERE course_id = $1
)
UPDATE tasks t
SET order_position = n.new_op
FROM new_order n
WHERE t.id = n.id
  AND t.course_id = $1
  AND (t.order_position IS DISTINCT FROM n.new_op)
"""


def _dsn() -> str:
    """Прод-DSN learn: из окружения либо из .mcp.json (паттерн tsk-362/366/373)."""
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


async def _sobrat_plan(conn: asyncpg.Connection) -> list[dict]:
    """План переноса: исходный курс -> его HARD-задания и будущий подкурс."""
    kids = await conn.fetch(
        "SELECT cp.course_id, cp.order_number, c.title, c.access_level::text AS access_level "
        "FROM course_parents cp JOIN courses c ON c.id = cp.course_id "
        "WHERE cp.parent_course_id = $1 "
        "ORDER BY cp.order_number NULLS LAST, cp.course_id",
        ROOT_COURSE_ID,
    )
    plan: list[dict] = []
    for kid in kids:
        src_id = kid["course_id"]
        hard = await conn.fetch(
            "SELECT id, is_active, requirement_level, order_position "
            "FROM tasks WHERE course_id = $1 AND difficulty_id = $2 "
            "ORDER BY order_position NULLS LAST, id",
            src_id,
            HARD_DIFFICULTY_ID,
        )
        if not hard:
            continue
        nomer = NOMER_PO_KURSU.get(src_id)
        if nomer is None:
            raise RuntimeError(
                f"Курс {src_id} «{kid['title']}» не в карте номеров — "
                "дерево 112 изменилось, карту надо обновить осознанно."
            )
        plan.append(
            {
                "src_id": src_id,
                "src_title": kid["title"],
                "order_number": kid["order_number"],
                "access_level": kid["access_level"],
                "novyj_title": f"Задание {nomer}. Сложные",
                "novyj_uid": f"lms:tsk347:hard:{src_id}",
                "task_ids": [r["id"] for r in hard],
                "aktivnyh": sum(1 for r in hard if r["is_active"]),
                "neaktivnyh": sum(1 for r in hard if not r["is_active"]),
                "trebuet_smeny_urovnya": sum(
                    1 for r in hard if r["requirement_level"] != "recommended"
                ),
            }
        )
    return plan


async def _sozdat_kurs(
    conn: asyncpg.Connection,
    *,
    title: str,
    uid: str,
    description: str | None,
    access_level: str,
    parent_id: int,
    order_number: int,
) -> tuple[int, bool]:
    """Курс + связь с родителем. Идемпотентно по course_uid.

    :returns: (course_id, создан_ли_сейчас)
    """
    existing = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", uid)
    sozdan = existing is None
    if sozdan:
        course_id = await conn.fetchval(
            "INSERT INTO courses (title, access_level, description, is_required, course_uid, is_public_demo) "
            "VALUES ($1, $2::access_level_type, $3, false, $4, false) RETURNING id",
            title,
            access_level,
            description,
            uid,
        )
    else:
        course_id = existing
    await conn.execute(
        "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
        "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
        course_id,
        parent_id,
        order_number,
    )
    return int(course_id), sozdan


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        plan = await _sobrat_plan(conn)

        vsego_zadanij = sum(len(p["task_ids"]) for p in plan)
        vsego_aktivnyh = sum(p["aktivnyh"] for p in plan)
        vsego_urovnya = sum(p["trebuet_smeny_urovnya"] for p in plan)

        print("=" * 78)
        print(f"tsk-347 · перенос HARD в блок «{CONTAINER_TITLE}» · "
              f"{'ПРИМЕНЕНИЕ' if apply else 'DRY-RUN'}")
        print("=" * 78)
        print(f"Подкурсов-источников с HARD: {len(plan)}")
        print(f"Заданий к переносу: {vsego_zadanij} "
              f"(активных {vsego_aktivnyh}, неактивных {vsego_zadanij - vsego_aktivnyh})")
        print(f"Из них сменят requirement_level на recommended: {vsego_urovnya}")
        print("-" * 78)
        for p in plan:
            print(
                f"  {p['src_id']:>4} «{p['src_title'][:44]:<44}» -> "
                f"«{p['novyj_title']:<22}» {len(p['task_ids']):>4} зад. "
                f"(req->rec: {p['trebuet_smeny_urovnya']})"
            )
        print("-" * 78)
        print("Выборка первых 10 заданий первого курса:", plan[0]["task_ids"][:10])

        if not apply:
            print("\nDRY-RUN: ничего не записано. Повтор с --apply.")
            return

        async with conn.transaction():
            # Триггер порядка глушим на всю транзакцию: UPDATE со сменой
            # course_id + order_position иначе сдвинет позиции соседей приёмника.
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")

            sled_order = await conn.fetchval(
                "SELECT COALESCE(MAX(order_number), 0) + 1 FROM course_parents WHERE parent_course_id = $1",
                ROOT_COURSE_ID,
            )
            container_id, sozdan = await _sozdat_kurs(
                conn,
                title=CONTAINER_TITLE,
                uid=CONTAINER_UID,
                description=CONTAINER_DESCRIPTION,
                access_level="self_guided",
                parent_id=ROOT_COURSE_ID,
                order_number=int(sled_order),
            )
            print(f"\nКонтейнер: id={container_id} "
                  f"({'создан' if sozdan else 'уже был'}), order_number={sled_order}")

            pereneseno = 0
            for p in plan:
                novyj_id, sozdan = await _sozdat_kurs(
                    conn,
                    title=p["novyj_title"],
                    uid=p["novyj_uid"],
                    description=(
                        f"Задания повышенной сложности из раздела «{p['src_title']}». "
                        "Блок необязательный."
                    ),
                    access_level=p["access_level"],
                    parent_id=container_id,
                    order_number=int(p["order_number"]),
                )
                res = await conn.execute(
                    "UPDATE tasks SET course_id = $1, requirement_level = 'recommended' "
                    "WHERE id = ANY($2::int[])",
                    novyj_id,
                    p["task_ids"],
                )
                n = int(res.split()[-1])
                pereneseno += n
                # Порядок в приёмнике и уплотнение в источнике — тем же правилом,
                # что и durable-хук tsk-345.
                await conn.execute(REORDER_SQL, novyj_id)
                await conn.execute(REORDER_SQL, p["src_id"])
                p["novyj_id"] = novyj_id
                print(f"  {p['src_id']:>4} -> {novyj_id:>4} «{p['novyj_title']:<22}» "
                      f"перенесено {n:>4} ({'курс создан' if sozdan else 'курс уже был'})")

            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'false', true)")

            # ── Верификация ДО COMMIT ──────────────────────────────────────
            print("\nВерификация в транзакции:")
            ostalos = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = ANY($1::int[]) AND difficulty_id = $2",
                [p["src_id"] for p in plan],
                HARD_DIFFICULTY_ID,
            )
            ne_rec = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = ANY($1::int[]) "
                "AND requirement_level <> 'recommended'",
                [p["novyj_id"] for p in plan],
            )
            v_novyh = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = ANY($1::int[])",
                [p["novyj_id"] for p in plan],
            )
            ne_hard = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = ANY($1::int[]) AND difficulty_id <> $2",
                [p["novyj_id"] for p in plan],
                HARD_DIFFICULTY_ID,
            )
            kollizii = await conn.fetchval(
                "SELECT COALESCE(sum(c), 0) FROM ("
                "  SELECT count(*) - 1 AS c FROM tasks "
                "  WHERE course_id = ANY($1::int[]) AND order_position IS NOT NULL "
                "  GROUP BY course_id, order_position HAVING count(*) > 1"
                ") x",
                [p["src_id"] for p in plan] + [p["novyj_id"] for p in plan],
            )
            print(f"  HARD, оставшихся в исходных курсах: {ostalos} (ожидание 0)")
            print(f"  перенесено всего: {pereneseno} (ожидание {vsego_zadanij})")
            print(f"  заданий в новых подкурсах: {v_novyh} (ожидание {vsego_zadanij})")
            print(f"  не-HARD в новых подкурсах: {ne_hard} (ожидание 0)")
            print(f"  не-recommended в новых подкурсах: {ne_rec} (ожидание 0)")
            print(f"  коллизий order_position: {kollizii} (ожидание 0)")

            if (ostalos, ne_rec, ne_hard, kollizii) != (0, 0, 0, 0) or pereneseno != vsego_zadanij:
                raise RuntimeError("Верификация не сошлась — ROLLBACK.")

        print("\nCOMMIT выполнен. Независимую проверку делать через MCP learn_prod_db.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="tsk-347: HARD ЕГЭ -> отдельный блок")
    ap.add_argument("--apply", action="store_true", help="выполнить запись (по умолчанию dry-run)")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
