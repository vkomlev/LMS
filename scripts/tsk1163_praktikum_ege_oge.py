"""tsk-1163: «Практикум ЕГЭ/ОГЭ» в главах курсов «Информатика» 7, 8, 9, 11 класс (принцип tsk-1161).

Готовые подборки tsk-1161 (`lms:tsk1161:bank:*`) подключаются ещё одним родителем; новые
собираются так же: подкурс в курсе банка + перенос `course_id`.

Решения оператора (29.09): задания — готовые из банка «Задание N ЕГЭ/ОГЭ», без копий.
Банк плоский, а задание принадлежит одному курсу, поэтому (решение оператора):
в курсе банка создаётся подкурс с отобранными 7–10 заданиями, и этот же подкурс вторым
родителем подключается к узлу «Практикум ЕГЭ/ОГЭ» главы (M:N `course_parents`, как tsk-388).
Задания переезжают UPDATE-ом `course_id` (PATCH не меняет course_id, tsk-1132); id и сдачи
учеников сохраняются, позиции уплотняет/назначает триггер `set_task_order_position` (tsk-802).
`requirement_level` не меняется (решение оператора: оставить обязательными).

Отбор — `tsk1163_selection.json` (сделан `tsk1163_select_tasks.py`, только чтение).

Запуск (из корня LMS):
  python scripts/tsk1163_praktikum_ege_oge.py            # dry-run: всё в транзакции, откат
  DBCHECK_OK=1 python scripts/tsk1163_praktikum_ege_oge.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk1132_move_foreign_tasks import prod_dsn  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk1163")

# класс-глава → id главы; порядок подборок в практикуме — как в списке
CHAPTERS: Dict[str, int] = {
    "7-1": 827, "7-2": 836, "7-3": 845, "7-6": 858,
    "8-1": 965, "8-2": 970, "8-3": 975, "8-4": 982,
    "9-1": 989, "9-2": 994, "9-3": 1000, "9-4": 1004,
    "11-1": 1041, "11-2": 1046, "11-3": 1052, "11-4": 1057,
}
# подборки, собранные раньше (tsk-1161 и эта задача), по главам: ключ → course_uid
REUSE: Dict[str, List[str]] = {
    "7-1": ["lms:tsk1161:bank:oge2"],
    "7-2": ["lms:tsk1161:bank:oge12", "lms:tsk1161:bank:oge11", "new:oge7"],
    "7-3": ["lms:tsk1161:bank:oge1"],
    "7-6": ["lms:tsk1161:bank:oge13"],
    "8-1": ["lms:tsk1161:bank:oge10", "lms:tsk1161:bank:ege14"],
    "8-2": ["lms:tsk1161:bank:oge3", "lms:tsk1161:bank:oge8", "lms:tsk1161:bank:ege2"],
    "8-3": ["new:oge5"],
    "8-4": ["new:oge6", "new:oge16"],
    "9-1": ["new:oge16"],
    "9-2": ["new:oge4", "new:oge9", "new:ege1"],
    "9-3": ["new:oge14", "new:ege9"],
    "9-4": ["new:oge7"],
    "11-1": ["new:ege9", "new:ege3"],
    "11-2": ["new:ege5", "new:ege12", "new:ege16", "new:ege17"],
    "11-3": ["new:ege1", "new:ege22"],
    "11-4": ["new:ege10n"],
}
PRAKTIKUM_TITLE = "Практикум ЕГЭ/ОГЭ"
PRAKTIKUM_DESC = "Настоящие задания ЕГЭ и ОГЭ по темам этой главы — от простых к средним."
SELECTION = json.loads(
    (Path(__file__).resolve().parent / "tsk1163_selection.json").read_text(encoding="utf-8")
)


def snapshot(cur: Any, task_ids: List[int], course_ids: List[int]) -> Dict[str, Any]:
    """Снимок: курс и уровень каждого задания, сдачи, целостность позиций по курсам."""
    cur.execute(
        "SELECT id, course_id, requirement_level, is_active FROM tasks WHERE id = ANY(%s)",
        (task_ids,),
    )
    tasks = {r["id"]: dict(r) for r in cur.fetchall()}
    cur.execute(
        "SELECT count(*) AS n FROM task_results WHERE task_id = ANY(%s)", (task_ids,)
    )
    results = cur.fetchone()["n"]
    cur.execute(
        "SELECT course_id, count(*) AS n, max(order_position) AS mx, "
        "count(DISTINCT order_position) AS d, min(order_position) AS mn "
        "FROM tasks WHERE course_id = ANY(%s) GROUP BY course_id",
        (course_ids,),
    )
    positions = {r["course_id"]: (r["n"], r["mx"], r["d"], r["mn"]) for r in cur.fetchall()}
    return {"tasks": tasks, "results": results, "positions": positions}


def ensure_course(cur: Any, uid: str, title: str, desc: str, access_level: str) -> int:
    """Создать курс по course_uid (идемпотентно), вернуть id."""
    cur.execute("SELECT id FROM courses WHERE course_uid = %s", (uid,))
    row = cur.fetchone()
    if row:
        return int(row["id"])
    cur.execute(
        "INSERT INTO courses (title, access_level, description, is_required, course_uid, "
        "is_public_demo) VALUES (%s, %s::access_level_type, %s, false, %s, false) RETURNING id",
        (title, access_level, desc, uid),
    )
    return int(cur.fetchone()["id"])


def link(cur: Any, child: int, parent: int) -> None:
    """Привязать узел к родителю в конец списка (номер ставит триггер)."""
    cur.execute(
        "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
        "VALUES (%s, %s, NULL) ON CONFLICT DO NOTHING",
        (child, parent),
    )


def access_of(cur: Any, course_id: int) -> str:
    """Уровень доступа курса как текст."""
    cur.execute("SELECT access_level::text AS a FROM courses WHERE id = %s", (course_id,))
    return str(cur.fetchone()["a"])


def main() -> int:
    """Собрать практикумы в одной транзакции с проверкой до и после."""
    parser = argparse.ArgumentParser(description="tsk-1163")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    all_tasks = [t for g in SELECTION for t in g["task_ids"]]
    banks = sorted({g["bank"] for g in SELECTION})
    if len(all_tasks) != len(set(all_tasks)):
        logger.error("Задание попало в две подборки — останов")
        return 1

    conn = psycopg2.connect(**prod_dsn())
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        before = snapshot(cur, all_tasks, banks)
        logger.info("ДО: заданий %s, сдач %s, курсы банка %s",
                    len(before["tasks"]), before["results"], before["positions"])
        bad = [g["key"] for g in SELECTION for t in g["task_ids"]
               if before["tasks"].get(t, {}).get("course_id") != g["bank"]
               or not before["tasks"][t]["is_active"]]
        if bad:
            logger.error("Состояние не совпадает с отбором (%s) — останов", bad)
            conn.rollback()
            return 1

        created: Dict[str, int] = {}
        by_key = {g["key"]: g for g in SELECTION}
        # 1) новые подборки: подкурс в курсе банка + перенос заданий
        for g in SELECTION:
            node = ensure_course(cur, f"lms:tsk1161:bank:{g['key']}", g["title"],
                                 "Подборка заданий банка для практикумов курсов «Информатика».",
                                 access_of(cur, g["bank"]))
            created[g["key"]] = node
            link(cur, node, g["bank"])
            for tid in g["task_ids"]:  # порядок отбора = по возрастанию сложности
                cur.execute(
                    "UPDATE tasks SET course_id = %s, order_position = NULL "
                    "WHERE id = %s AND course_id = %s",
                    (node, tid, g["bank"]),
                )
                if cur.rowcount != 1:
                    logger.error("Задание %s: обновлено %s строк — откат", tid, cur.rowcount)
                    conn.rollback()
                    return 1
        # 2) практикум в конце главы + подборки (новые и готовые) вторым родителем
        for ch, chapter_id in CHAPTERS.items():
            prak = ensure_course(cur, f"lms:tsk1163:praktikum:{ch}", PRAKTIKUM_TITLE,
                                 PRAKTIKUM_DESC, access_of(cur, chapter_id))
            link(cur, prak, chapter_id)
            created[f"praktikum:{ch}"] = prak
            for ref in REUSE[ch]:
                if ref.startswith("new:"):
                    node = created[by_key[ref[4:]]["key"]]
                else:
                    cur.execute("SELECT id FROM courses WHERE course_uid = %s", (ref,))
                    node = int(cur.fetchone()["id"])
                link(cur, node, prak)
        logger.info("Узлы: %s", created)

        node_ids = [created[g["key"]] for g in SELECTION]
        after = snapshot(cur, all_tasks, banks + node_ids)
        cur.execute(
            "SELECT cp.parent_course_id, cp.course_id, cp.order_number, "
            "(SELECT max(order_number) FROM course_parents x WHERE x.parent_course_id = cp.parent_course_id) AS mx "
            "FROM course_parents cp WHERE cp.course_id = ANY(%s)",
            ([created[f"praktikum:{c}"] for c in CHAPTERS],),
        )
        prak_last = all(r["order_number"] == r["mx"] for r in cur.fetchall())
        ok = (
            after["results"] == before["results"]
            and all(after["tasks"][t]["requirement_level"] == before["tasks"][t]["requirement_level"]
                    for t in all_tasks)
            and all(after["tasks"][t]["course_id"] == created[g["key"]]
                    for g in SELECTION for t in g["task_ids"])
            # новые подкурсы — строго 1..N; в банке ОГЭ пропуски позиций были и до (не наши),
            # проверяем только отсутствие дублей и точное число ушедших заданий
            and all(after["positions"][created[g["key"]]] == (len(g["task_ids"]),) * 3 + (1,)
                    for g in SELECTION)
            and all(after["positions"][b][0] == after["positions"][b][2]
                    and before["positions"][b][0] - after["positions"][b][0]
                    == sum(len(g["task_ids"]) for g in SELECTION if g["bank"] == b)
                    for b in banks)
            and prak_last
        )
        logger.info("ПОСЛЕ: сдач %s, позиции %s, практикум последним: %s",
                    after["results"], after["positions"], prak_last)
        if not ok:
            logger.error("Проверка не прошла — откат")
            conn.rollback()
            return 1
        if not args.apply:
            logger.info("DRY-RUN: проверка пройдена, изменения откачены.")
            conn.rollback()
            return 0
        conn.commit()
        logger.info("COMMIT")
        return 0
    except Exception:
        conn.rollback()
        logger.exception("ОШИБКА — транзакция откачена")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
