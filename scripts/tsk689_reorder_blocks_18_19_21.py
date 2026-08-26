# -*- coding: utf-8 -*-
"""tsk-689, этап 1: порядок блоков ЕГЭ 18 и 19-21 от простого к сложному.

ЧТО ДЕЛАЕТ
1. Заводит подкурс «Задание 19-21. Устное решение» под курсом 147 (первым ребёнком)
   и переносит туда материалы «Теория» / «устное решение» и три устных задания.
   Зачем узел, а не просто номера: движок внутри одного курса всегда выдаёт
   СНАЧАЛА все материалы и только потом задания (`resolve_next_item`), поэтому
   «устные задачи перед материалами про код» плоским `order_position` недостижимо.
   Обход дерева — post-order (`_collect_courses_in_order`, tsk-127): содержимое
   подкурса идёт ПЕРЕД содержимым курса-контейнера. Это и даёт нужный порядок.
2. Переносит задания между базовым курсом и «Сложными» по решению оператора
   (26.08): вверх — то, что по уровню базовое; вниз — уровень 8 блока 18.
3. Перенумеровывает `order_position` активных заданий и материалов четырёх
   курсов по уровням сложности из критериев оператора
   (разбор: docs/specs/2026-08-26-tsk689-razbor-urovney-18-21.md).

БЕЗОПАСНОСТЬ ЗАПИСИ
- `trg_set_task_order_position` / `trg_set_material_order_position` — BEFORE-триггеры
  со «вставкой со сдвигом»: массовая перенумерация через них каскадила бы. Внутри
  транзакции они глушатся тем же флагом, которым пользуются сами
  (`app.skip_task_order_trigger` / `app.skip_material_order_trigger`), и порядок
  выставляется ровно списком.
- `trg_task_audit_update` НЕ глушим: смена `course_id` обязана попасть в `task_audit`.
  Актёр помечается через `app.audit_actor = 'tsk-689'`.
- Ни одной строки не удаляем и не деактивируем. Неактивные задания и материалы
  не трогаем вовсе — движок их и так не видит (`is_active`), а лишний UPDATE
  засорил бы аудит.
- Всё одной транзакцией: либо весь порядок новый, либо ничего.

ОБРАТИМОСТЬ
`--snapshot-file` пишет прежние (course_id, order_position) всех затронутых
строк в JSON. Откат — тем же скриптом с `--rollback <файл>`.

Запуск: dry-run по умолчанию; `--apply` — запись (нужен префикс DBCHECK_OK=1).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

BASE_18 = 146
HARD_18 = 1396
BASE_1921 = 147
HARD_1921 = 1397

ORAL_UID = "lms:tsk689:oral:147"
ORAL_TITLE = "Задание 19-21. Устное решение"
ORAL_DESCR = (
    "Разбор алгоритма теории игр на самых простых задачах — одна куча, два хода. "
    "Решаются устно, чтобы прочувствовать и запомнить ход рассуждения, "
    "прежде чем писать программу."
)

# --- Целевой состав и порядок. Списки — источник истины, порядок = позиция. ---

# Курс 146: разминка на последовательностях, затем робот по ступеням 0-7.
TASKS_146: List[int] = [
    # ступень «разминка: последовательности»
    4212, 2314, 2078, 4207, 2312, 3968, 3963, 3964, 3966,
    # 0 — базовый
    4066, 2307,
    # 2 — простые стенки
    4283, 9562, 2191, 4284,
    # 3 — угловые стенки, несколько финальных клеток
    9489, 9504, 9517, 3170, 2192, 2193, 3628, 3627, 3569,
    # 4 — + мёртвые зоны
    2306, 2310,
    # 5 — диагонали
    2311,
    # 6 — сложные условия сбора
    2313,
    # 7 — ладья
    2305,
]

# Курс 1396: ступени 6-8.
TASKS_1396: List[int] = [
    # 6 — сложные условия сбора
    3544, 3551, 4231, 3486, 3494, 3972, 4076, 4035, 4135, 4254,
    4244, 4336, 3919, 3855, 4176, 3584,
    # 7 — фигурные ходы
    4285, 4286, 3969, 3938, 4378,
    # 8 — нестандартные вопросы и постановки
    2309, 2308, 4384, 4221, 4379, 4381, 4175, 3962, 3519, 4104,
    4131, 4079, 4048, 4351, 4335, 3850,
]

# Подкурс «Устное решение».
TASKS_ORAL: List[int] = [5133, 5134, 5135]
MATERIALS_ORAL: List[int] = [391, 596, 597]

# Курс 147: ступени 1-7.
TASKS_147: List[int] = [
    # 1 — одна куча, два хода
    3765, 3767, 3766,
    # 2 — одна куча, три хода (кэш)
    3470, 10028, 2203, 9518,
    # 3 — две кучи
    3949, 3472, 3896, 4067, 4580, 4261, 4260, 4538, 4206, 2384,
    # 4 — одна куча, уменьшение
    3981, 2204, 2202, 2997, 3505, 9505, 4579,
    # 5 — две кучи, уменьшение
    2383, 4539,
    # 6 — ходы с условиями
    2385,
    # 7 — два диапазона окончания
    2079, 3329,
]
MATERIALS_147: List[int] = [598, 599, 600, 601, 604, 602, 603]

# Курс 1397: ступени 2, 6, 7, 8.
TASKS_1397: List[int] = [
    3498,
    4160, 4222, 4187, 3594, 3380, 4036, 4232, 4262, 3851, 3879,
    3518, 4278, 3554, 4527,
    4535, 4533, 4534, 4536, 4537, 4077, 3571, 4013,
    4007, 3606, 4245, 4191, 3888, 4217, 3535, 3982, 3631, 4179,
    3990, 4255, 4033, 3828, 3831, 4081, 4082,
]

MATERIALS_146: List[int] = [389, 593, 594]


def _dsn() -> str:
    """Прод-DSN learn: из окружения, иначе из `.mcp.json` (секрет не печатаем)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError(
            "Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно."
        )
    return dsn


def _check_lists() -> None:
    """Списки не пересекаются и не содержат дублей — иначе задание пропадёт."""
    all_tasks = TASKS_146 + TASKS_1396 + TASKS_ORAL + TASKS_147 + TASKS_1397
    dupes = {t for t in all_tasks if all_tasks.count(t) > 1}
    if dupes:
        raise RuntimeError(f"Задание указано дважды: {sorted(dupes)}")
    all_mats = MATERIALS_146 + MATERIALS_ORAL + MATERIALS_147
    dupes_m = {m for m in all_mats if all_mats.count(m) > 1}
    if dupes_m:
        raise RuntimeError(f"Материал указан дважды: {sorted(dupes_m)}")


async def _current_state(conn: asyncpg.Connection) -> Tuple[Dict[int, Tuple[int, int]], Dict[int, Tuple[int, int]]]:
    """(course_id, order_position) активных заданий и материалов четырёх курсов."""
    course_ids = [BASE_18, HARD_18, BASE_1921, HARD_1921]
    oral = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", ORAL_UID)
    if oral:
        course_ids.append(oral)
    trows = await conn.fetch(
        "SELECT id, course_id, order_position FROM tasks "
        "WHERE course_id = ANY($1::int[]) AND is_active",
        course_ids,
    )
    mrows = await conn.fetch(
        "SELECT id, course_id, order_position FROM materials "
        "WHERE course_id = ANY($1::int[]) AND is_active",
        course_ids,
    )
    return (
        {r["id"]: (r["course_id"], r["order_position"]) for r in trows},
        {r["id"]: (r["course_id"], r["order_position"]) for r in mrows},
    )


def _plan(
    tasks_now: Dict[int, Tuple[int, int]],
    mats_now: Dict[int, Tuple[int, int]],
    oral_course_id: int,
) -> Tuple[List[Tuple[int, int, int]], List[Tuple[int, int, int]]]:
    """Список (id, целевой course_id, целевая позиция) для заданий и материалов."""
    task_plan: List[Tuple[int, int, int]] = []
    for course_id, ids in (
        (BASE_18, TASKS_146),
        (HARD_18, TASKS_1396),
        (oral_course_id, TASKS_ORAL),
        (BASE_1921, TASKS_147),
        (HARD_1921, TASKS_1397),
    ):
        for pos, task_id in enumerate(ids, start=1):
            task_plan.append((task_id, course_id, pos))

    mat_plan: List[Tuple[int, int, int]] = []
    for course_id, ids in (
        (BASE_18, MATERIALS_146),
        (oral_course_id, MATERIALS_ORAL),
        (BASE_1921, MATERIALS_147),
    ):
        for pos, mat_id in enumerate(ids, start=1):
            mat_plan.append((mat_id, course_id, pos))

    missing_t = [t for t, _, _ in task_plan if t not in tasks_now]
    if missing_t:
        raise RuntimeError(f"Заданий нет в активном составе четырёх курсов: {missing_t}")
    extra_t = [t for t in tasks_now if t not in {i for i, _, _ in task_plan}]
    if extra_t:
        raise RuntimeError(f"Активные задания не попали в план (потерялись бы): {extra_t}")

    missing_m = [m for m, _, _ in mat_plan if m not in mats_now]
    if missing_m:
        raise RuntimeError(f"Материалов нет в активном составе: {missing_m}")
    extra_m = [m for m in mats_now if m not in {i for i, _, _ in mat_plan}]
    if extra_m:
        raise RuntimeError(f"Активные материалы не попали в план: {extra_m}")

    return task_plan, mat_plan


async def _ensure_oral_course(conn: asyncpg.Connection, apply: bool) -> int:
    """id подкурса «Устное решение»; создаёт его и привязку к 147 при --apply."""
    existing = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", ORAL_UID)
    if existing:
        print(f"  подкурс «{ORAL_TITLE}» уже есть: id={existing}")
        return int(existing)
    if not apply:
        print(f"  подкурс «{ORAL_TITLE}» будет создан (course_uid={ORAL_UID})")
        return -1
    new_id = await conn.fetchval(
        """
        INSERT INTO courses (title, description, access_level, is_required, course_uid,
                             is_public_demo, created_at)
        SELECT $1, $2, access_level, false, $3, false, NOW()
        FROM courses WHERE id = $4
        RETURNING id
        """,
        ORAL_TITLE, ORAL_DESCR, ORAL_UID, BASE_1921,
    )
    # Первым ребёнком 147: у 147 других детей нет, order_number = 1.
    await conn.execute(
        "INSERT INTO course_parents (course_id, parent_course_id, order_number) VALUES ($1, $2, 1)",
        new_id, BASE_1921,
    )
    print(f"  создан подкурс id={new_id}, привязан к {BASE_1921} с order_number=1")
    return int(new_id)


async def _apply_plan(
    conn: asyncpg.Connection,
    task_plan: List[Tuple[int, int, int]],
    mat_plan: List[Tuple[int, int, int]],
) -> Tuple[int, int]:
    """Записывает план. Вызывать ВНУТРИ транзакции."""
    # Глушим триггеры порядка: они реализуют «вставку со сдвигом», а нам нужна
    # прямая перенумерация списком. Аудит смены курса не глушим намеренно.
    await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
    await conn.execute("SELECT set_config('app.skip_material_order_trigger', 'true', true)")
    await conn.execute("SELECT set_config('app.audit_actor', 'tsk-689', true)")

    t_ids = [t for t, _, _ in task_plan]
    t_courses = [c for _, c, _ in task_plan]
    t_pos = [p for _, _, p in task_plan]
    st = await conn.execute(
        """
        UPDATE tasks t SET course_id = v.course_id, order_position = v.pos
        FROM (SELECT unnest($1::int[]) AS id, unnest($2::int[]) AS course_id,
                     unnest($3::int[]) AS pos) v
        WHERE t.id = v.id
          AND (t.course_id IS DISTINCT FROM v.course_id
               OR t.order_position IS DISTINCT FROM v.pos)
        """,
        t_ids, t_courses, t_pos,
    )
    tasks_updated = int(st.rsplit(" ", 1)[-1] or 0)

    m_ids = [m for m, _, _ in mat_plan]
    m_courses = [c for _, c, _ in mat_plan]
    m_pos = [p for _, _, p in mat_plan]
    st = await conn.execute(
        """
        UPDATE materials m SET course_id = v.course_id, order_position = v.pos
        FROM (SELECT unnest($1::int[]) AS id, unnest($2::int[]) AS course_id,
                     unnest($3::int[]) AS pos) v
        WHERE m.id = v.id
          AND (m.course_id IS DISTINCT FROM v.course_id
               OR m.order_position IS DISTINCT FROM v.pos)
        """,
        m_ids, m_courses, m_pos,
    )
    mats_updated = int(st.rsplit(" ", 1)[-1] or 0)
    return tasks_updated, mats_updated


async def _verify(conn: asyncpg.Connection, oral_course_id: int) -> bool:
    """Сверка после записи: состав и порядок каждого курса совпали с планом."""
    ok = True
    for course_id, expected in (
        (BASE_18, TASKS_146),
        (HARD_18, TASKS_1396),
        (oral_course_id, TASKS_ORAL),
        (BASE_1921, TASKS_147),
        (HARD_1921, TASKS_1397),
    ):
        rows = await conn.fetch(
            "SELECT id FROM tasks WHERE course_id = $1 AND is_active "
            "ORDER BY order_position NULLS LAST, id",
            course_id,
        )
        got = [r["id"] for r in rows]
        if got != expected:
            ok = False
            print(f"  РАСХОЖДЕНИЕ, задания курса {course_id}:")
            print(f"    ожидалось: {expected}")
            print(f"    в базе:    {got}")
        else:
            print(f"  курс {course_id}: {len(got)} заданий в нужном порядке")

    for course_id, expected in (
        (BASE_18, MATERIALS_146),
        (oral_course_id, MATERIALS_ORAL),
        (BASE_1921, MATERIALS_147),
    ):
        rows = await conn.fetch(
            "SELECT id FROM materials WHERE course_id = $1 AND is_active "
            "ORDER BY order_position NULLS LAST, id",
            course_id,
        )
        got = [r["id"] for r in rows]
        if got != expected:
            ok = False
            print(f"  РАСХОЖДЕНИЕ, материалы курса {course_id}: ожидалось {expected}, в базе {got}")
        else:
            print(f"  курс {course_id}: {len(got)} материалов в нужном порядке")

    # Задания и материалы не потеряли ни одного результата/прогресса
    lost = await conn.fetchval(
        "SELECT count(*) FROM task_results tr LEFT JOIN tasks t ON t.id = tr.task_id "
        "WHERE t.id IS NULL"
    )
    if lost:
        ok = False
        print(f"  РАСХОЖДЕНИЕ: {lost} результатов ссылаются на несуществующие задания")
    return ok


async def _rollback(conn: asyncpg.Connection, snapshot: dict, apply: bool) -> None:
    """Возврат (course_id, order_position) из снимка."""
    tasks = snapshot["tasks"]
    mats = snapshot["materials"]
    print(f"Откат: {len(tasks)} заданий, {len(mats)} материалов")
    if not apply:
        print("Dry-run: записи не было.")
        return
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
        await conn.execute("SELECT set_config('app.skip_material_order_trigger', 'true', true)")
        await conn.execute("SELECT set_config('app.audit_actor', 'tsk-689-rollback', true)")
        for tid, (cid, pos) in tasks.items():
            await conn.execute(
                "UPDATE tasks SET course_id = $2, order_position = $3 WHERE id = $1",
                int(tid), cid, pos,
            )
        for mid, (cid, pos) in mats.items():
            await conn.execute(
                "UPDATE materials SET course_id = $2, order_position = $3 WHERE id = $1",
                int(mid), cid, pos,
            )
    print("Откат выполнен. Подкурс «Устное решение» скрипт не удаляет — снимите вручную.")


async def main() -> int:
    parser = argparse.ArgumentParser(description="tsk-689 этап 1: порядок блоков 18 и 19-21")
    parser.add_argument("--apply", action="store_true", help="записать в прод-БД")
    parser.add_argument("--snapshot-file", default="", help="куда сохранить снимок для отката")
    parser.add_argument("--rollback", default="", help="откатить по файлу снимка")
    args = parser.parse_args()

    _check_lists()
    conn = await asyncpg.connect(_dsn())
    try:
        if args.rollback:
            snapshot = json.loads(Path(args.rollback).read_text(encoding="utf-8"))
            await _rollback(conn, snapshot, args.apply)
            return 0

        tasks_now, mats_now = await _current_state(conn)
        print("=== ДО ===")
        print(f"  активных заданий в четырёх курсах: {len(tasks_now)}")
        print(f"  активных материалов:               {len(mats_now)}")

        if args.snapshot_file:
            Path(args.snapshot_file).write_text(
                json.dumps({"tasks": tasks_now, "materials": mats_now}, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"  снимок для отката: {args.snapshot_file}")

        print("\n=== ПЛАН ===")
        oral_id = await _ensure_oral_course(conn, apply=False)
        oral_probe = oral_id if oral_id > 0 else -1
        task_plan, mat_plan = _plan(tasks_now, mats_now, oral_probe)

        moves = [
            (t, tasks_now[t][0], c)
            for t, c, _ in task_plan
            if t in tasks_now and tasks_now[t][0] != c
        ]
        print(f"  заданий меняют курс: {len(moves)}")
        for tid, old_c, new_c in moves:
            print(f"    {tid}: {old_c} → {new_c}")
        mat_moves = [
            (m, mats_now[m][0], c) for m, c, _ in mat_plan
            if m in mats_now and mats_now[m][0] != c
        ]
        print(f"  материалов меняют курс: {len(mat_moves)}")
        for mid, old_c, new_c in mat_moves:
            print(f"    {mid}: {old_c} → {new_c}")
        reorders = sum(
            1 for t, c, p in task_plan
            if t in tasks_now and tasks_now[t] != (c, p)
        )
        print(f"  строк заданий к обновлению всего: {reorders}")

        if not args.apply:
            print("\nDry-run: записи не было.")
            return 0

        async with conn.transaction():
            oral_id = await _ensure_oral_course(conn, apply=True)
            task_plan, mat_plan = _plan(tasks_now, mats_now, oral_id)
            t_upd, m_upd = await _apply_plan(conn, task_plan, mat_plan)
            print(f"\nОбновлено: заданий {t_upd}, материалов {m_upd}")

        print("\n=== ПОСЛЕ ===")
        ok = await _verify(conn, oral_id)
        return 0 if ok else 3
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
