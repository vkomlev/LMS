"""tsk-853: выключить вторую ветку курса «Чат-боты» — ключи с возвратом каретки.

Зачем. Публикатор уроков ContentBackbone собирает `external_uid` как
`{база}#mN`/`{база}#qN`, а база приходила снаружи (параметр `--lms-uid-base`) и
не проверялась. Прогон 02.07.2026 передал её вместе с виндовым концом строки, и
символ возврата каретки уехал в ключ. Вместо обновления уроков курса LMS завела
вторую ветку: копии материалов и заданий сели не в листья графа, а в сами
узлы-темы. Ученики с тех пор читают один и тот же урок и решают одно и то же
задание дважды — в теме и в уроке.

Источник дефекта закрыт на стороне CB (`sanitize_uid`, коммит 934bfc9). Этот
скрипт разбирает последствие: **выключает** 42 задания и 101 материал ветки.
Строки не удаляются — на них висят 35 результатов учеников, 90 отметок
прочтения и 2 закрытых обращения помощи, и терять их нельзя.

Ученикам ничего решать заново не придётся: всё, что они решили на этой ветке,
у них уже зачтено на близнецах без возврата каретки (проверено 09.09 —
14 заданий у ученика 4522, 19 у 4548, расхождений ноль).

Отдельным шагом чиним домашку 92 (ученик 4548): два её пункта смотрят на
выключаемую ветку, а ту же работу ученик сдал на близнецах 17.08 и 19.08.
Пункты переставляются на близнецов, и домашка сразу оказывается выполненной.

Границы. Трогаются только строки, у которых в ключе есть возврат каретки, и
только в курсах `wp:chat-boty-tg-vk-max*` — двойное условие, чтобы правка не
расползлась. Попытки, отметки прочтения и обращения помощи не трогаются.

Протокол (db-check): по умолчанию читает и показывает план; запись — только с
`--apply`, одной транзакцией, с проверкой после.

Usage:
    python scripts/tsk853_disable_cr_branch.py
    DBCHECK_OK=1 python scripts/tsk853_disable_cr_branch.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import asyncpg

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: Курс, чью вторую ветку разбираем. Второе условие поверх «есть возврат
#: каретки»: одного признака мало, чтобы пускать правку по боевой базе.
_COURSE_UID_PREFIX = "wp:chat-boty-tg-vk-max"

#: Пункты домашки 92 → задания-близнецы без возврата каретки.
_HOMEWORK_ID = 92
_HOMEWORK_REMAP: dict[int, tuple[int, int]] = {
    412: (6169, 6195),  # sluchaynoe-chislo#q2
    413: (6171, 5838),  # bot-zapominaet#q1
}

def prod_dsn() -> dict[str, object]:
    """Параметры подключения к боевой базе из `.mcp.json` (пароль не печатаем).

    Локальный `.env` сюда не годится: его `DATABASE_URL` смотрит на dev-копию
    (localhost/Learn), а правка нужна на боевой базе.
    """
    mcp = json.loads((_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    parsed = urlparse(mcp["mcpServers"]["learn_prod_db"]["args"][-1])
    return dict(
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=(parsed.path or "").lstrip("/"),
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
    )


_TASKS_SQL = """
    SELECT t.id, t.course_id, t.external_uid, t.is_active
    FROM public.tasks t
    JOIN public.courses c ON c.id = t.course_id
    WHERE t.external_uid ~ E'\\r' AND c.course_uid LIKE $1
    ORDER BY t.id
"""

_MATERIALS_SQL = """
    SELECT m.id, m.course_id, m.external_uid, m.is_active
    FROM public.materials m
    JOIN public.courses c ON c.id = m.course_id
    WHERE m.external_uid ~ E'\\r' AND c.course_uid LIKE $1
    ORDER BY m.id
"""


async def read_state(conn: asyncpg.Connection) -> dict[str, list[asyncpg.Record]]:
    """Текущее состояние ветки и домашки — до любой записи."""
    like = f"{_COURSE_UID_PREFIX}%"
    tasks = await conn.fetch(_TASKS_SQL, like)
    materials = await conn.fetch(_MATERIALS_SQL, like)
    homework = await conn.fetch(
        """
        SELECT hi.id, hi.homework_id, hi.task_id, hi.position,
               t.external_uid, t.is_active,
               EXISTS (
                   SELECT 1 FROM public.task_results r
                   JOIN public.homework_assignment ha ON ha.id = hi.homework_id
                   WHERE r.task_id = hi.task_id AND r.user_id = ha.student_id AND r.is_correct
               ) AS solved
        FROM public.homework_item hi
        JOIN public.tasks t ON t.id = hi.task_id
        WHERE hi.homework_id = $1
        ORDER BY hi.position
        """,
        _HOMEWORK_ID,
    )
    return {"tasks": tasks, "materials": materials, "homework": homework}


def print_plan(state: dict[str, list[asyncpg.Record]]) -> None:
    """План правки: что и сколько будет затронуто."""
    tasks = [r for r in state["tasks"] if r["is_active"]]
    materials = [r for r in state["materials"] if r["is_active"]]
    print(f"Курс: {_COURSE_UID_PREFIX}*")
    print(f"Заданий к выключению:  {len(tasks)} (всего с возвратом каретки: {len(state['tasks'])})")
    print(f"Материалов к выключению: {len(materials)} (всего: {len(state['materials'])})")
    for row in tasks[:5]:
        print(f"  задание {row['id']} курс {row['course_id']} {row['external_uid']!r}")
    if len(tasks) > 5:
        print(f"  … ещё {len(tasks) - 5}")
    print(f"\nДомашка {_HOMEWORK_ID}: пунктов {len(state['homework'])}")
    for row in state["homework"]:
        target = _HOMEWORK_REMAP.get(row["id"])
        move = f" → задание {target[1]}" if target else " (не трогаем)"
        print(f"  пункт {row['id']}: задание {row['task_id']}{move}, сдано={row['solved']}")


async def apply_changes(conn: asyncpg.Connection) -> dict[str, int]:
    """Одна транзакция: выключение ветки + перестановка пунктов домашки."""
    like = f"{_COURSE_UID_PREFIX}%"
    counters: dict[str, int] = {}
    async with conn.transaction():
        tasks_off = await conn.execute(
            """
            UPDATE public.tasks t SET is_active = false
            FROM public.courses c
            WHERE c.id = t.course_id
              AND t.external_uid ~ E'\\r' AND c.course_uid LIKE $1 AND t.is_active
            """,
            like,
        )
        materials_off = await conn.execute(
            """
            UPDATE public.materials m SET is_active = false
            FROM public.courses c
            WHERE c.id = m.course_id
              AND m.external_uid ~ E'\\r' AND c.course_uid LIKE $1 AND m.is_active
            """,
            like,
        )
        counters["tasks"] = int(tasks_off.split()[-1])
        counters["materials"] = int(materials_off.split()[-1])

        moved = 0
        for item_id, (old_task, new_task) in _HOMEWORK_REMAP.items():
            res = await conn.execute(
                """
                UPDATE public.homework_item
                SET task_id = $3
                WHERE id = $1 AND homework_id = $4 AND task_id = $2
                """,
                item_id,
                old_task,
                new_task,
                _HOMEWORK_ID,
            )
            moved += int(res.split()[-1])
        counters["homework_items"] = moved
    return counters


async def verify(conn: asyncpg.Connection) -> None:
    """Проверка после записи: ветка выключена, домашка смотрит на близнецов."""
    like = f"{_COURSE_UID_PREFIX}%"
    left_tasks = await conn.fetchval(
        """
        SELECT count(*) FROM public.tasks t JOIN public.courses c ON c.id = t.course_id
        WHERE t.external_uid ~ E'\\r' AND c.course_uid LIKE $1 AND t.is_active
        """,
        like,
    )
    left_materials = await conn.fetchval(
        """
        SELECT count(*) FROM public.materials m JOIN public.courses c ON c.id = m.course_id
        WHERE m.external_uid ~ E'\\r' AND c.course_uid LIKE $1 AND m.is_active
        """,
        like,
    )
    results_kept = await conn.fetchval(
        "SELECT count(*) FROM public.task_results r JOIN public.tasks t ON t.id = r.task_id"
        " WHERE t.external_uid ~ E'\\r'"
    )
    progress_kept = await conn.fetchval(
        "SELECT count(*) FROM public.student_material_progress p"
        " JOIN public.materials m ON m.id = p.material_id WHERE m.external_uid ~ E'\\r'"
    )
    homework = await conn.fetch(
        """
        SELECT hi.id, hi.task_id, t.external_uid, t.is_active,
               EXISTS (
                   SELECT 1 FROM public.task_results r
                   JOIN public.homework_assignment ha ON ha.id = hi.homework_id
                   WHERE r.task_id = hi.task_id AND r.user_id = ha.student_id AND r.is_correct
               ) AS solved
        FROM public.homework_item hi JOIN public.tasks t ON t.id = hi.task_id
        WHERE hi.homework_id = $1 ORDER BY hi.position
        """,
        _HOMEWORK_ID,
    )
    print("\nПроверка после записи:")
    print(f"  активных заданий ветки осталось:   {left_tasks} (ожидаем 0)")
    print(f"  активных материалов ветки осталось: {left_materials} (ожидаем 0)")
    print(f"  результатов учеников сохранено:     {results_kept} (ожидаем 35)")
    print(f"  отметок прочтения сохранено:        {progress_kept} (ожидаем 90)")
    for row in homework:
        print(
            f"  домашка {_HOMEWORK_ID} пункт {row['id']}: задание {row['task_id']} "
            f"{row['external_uid']!r} активно={row['is_active']} сдано={row['solved']}"
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="выполнить запись (по умолчанию — только план)")
    args = parser.parse_args()

    params = prod_dsn()
    print(f"База: {params['user']}@{params['host']}:{params['port']}/{params['database']}\n")
    conn = await asyncpg.connect(**params)
    try:
        state = await read_state(conn)
        print_plan(state)
        if not args.apply:
            print("\nСухой прогон. Запись — с флагом --apply.")
            return 0
        counters = await apply_changes(conn)
        print(
            f"\nЗаписано: заданий выключено {counters['tasks']}, "
            f"материалов выключено {counters['materials']}, "
            f"пунктов домашки переставлено {counters['homework_items']}"
        )
        await verify(conn)
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
