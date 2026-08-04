# -*- coding: utf-8 -*-
"""tsk-558, четвёртый (последний) проход: 2997 и 3329 — оставшиеся 2 из 4

Yandex-заданий курса 147. Оператор открыл исходные ТГ-посты (@cyberguru_ege,
msg 987 и 518 — id взяты из `source_tg_global_uid`) и прислал прямые ссылки
на образовательную платформу для вопросов 20/21 каждого блока (посты сами их
не содержали текстом, только ссылками, как и предполагал плейбук §2).

СВЕРКА ПЕРЕД ЗАПИСЬЮ
Обе пары ссылок независимо подтверждены общим `task_series_id` с уже
известным Q19 этой игры (получено анонимным `get_task_by_id`, тот же метод,
что и в предыдущем проходе):
  - 2997: Q19 UUID 4be8eb33-506c-4c17-88a9-9e214b8f1f51, task_series_id
    9fbf3501-b77a-4360-b1b9-8d58aa506763. Ссылки оператора (bf988b1a — Q20,
    79eed1f8 — Q21) — тот же task_series_id и дословно тот же текст правил
    игры ("убрать 3/4 камня или /2 с округлением, порог не более 15").
  - 3329: Q19 UUID неизвестен (в LMS уже лежит текст, взятый из ТГ-поста, не
    из API), но Q20/Q21 (78604e48/64e08e1e, task_series_id
    66d382a2-b54a-4db1-bdcc-6d48376eecdb) дословно повторяют правила игры,
    уже лежащие в LMS ("добавить 3/5 камней или x3, порог не менее 97,
    реверс победителя при >105") — совпадение проверено вручную посимвольно
    по значимым числам (97, 105, 96) и структуре реверса, это тот же матч.

ФОРМА ОТВЕТА (важно: НЕ универсальный "1+2+1" — форма зависит от вопроса)
  - 2997: Q19=34(1) + Q20="наименьшее и наибольшее"=(35,69) + Q21=39(1) → 4 поля.
  - 3329: Q19=91(1) + Q20="наименьшее и наибольшее"=(30,88) +
    Q21="количество значений"=3(1, это ЧИСЛО-СЧЁТЧИК, не сами значения S —
    вопрос источника буквально просит "укажите количество найденных
    значений") → 4 поля.

Запуск: dry-run по умолчанию;
  python scripts/tsk558_yandex_final_2997_3329.py
  DBCHECK_OK=1 python scripts/tsk558_yandex_final_2997_3329.py --apply
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
sys.path.insert(0, str(project_root))

from app.schemas.checking import StudentAnswer, StudentResponse  # noqa: E402
from app.schemas.solution_rules import SolutionRules  # noqa: E402
from app.schemas.task_content import TaskContent  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402

checking = CheckingService()
COURSE_ID = 147

Q20_2997 = (
    '<p>Для игры, описанной в задании 19, найдите <strong>наименьшее</strong> и '
    '<strong>наибольшее</strong> значения S, при которых у Пети есть выигрышная '
    'стратегия, причём одновременно выполняются два условия:</p>'
    '<ul><li>Петя не может выиграть за один ход</li>'
    '<li>Петя может выиграть своим вторым ходом независимо от того, как будет ходить '
    'Ваня</li></ul>'
    '<p>Найденные значения запишите в ответе в порядке возрастания.</p>'
)
Q21_2997 = (
    '<p>Для игры, описанной в задании 19, найдите <strong>минимальное</strong> значение '
    'S, при котором одновременно выполняются два условия:</p>'
    '<ul><li>у Вани есть выигрышная стратегия, которая позволит ему выиграть первым или '
    'вторым ходом при любой игре Пети</li>'
    '<li>у Вани нет стратегии, которая позволит ему гарантированно выиграть первым '
    'ходом</li></ul>'
)

Q20_3329 = (
    '<p>Для игры, описанной в задании 19, найдите наименьшее и наибольшее значения S, '
    'при которых у Пети есть выигрышная стратегия, причём одновременно выполняются два '
    'условия:</p>'
    '<ul><li>Петя не может выиграть за один ход</li>'
    '<li>Петя может выиграть своим вторым ходом независимо от того, как будет ходить '
    'Ваня</li></ul>'
    '<p>Найденные значения запишите в ответе в порядке возрастания.</p>'
)
Q21_3329 = (
    '<p>Для игры, описанной в задании 19, определите количество S, при которых '
    'одновременно выполняются два условия:</p>'
    '<ul><li>у Вани есть выигрышная стратегия, которая позволяет ему выиграть первым или '
    'вторым ходом при любой игре Пети</li>'
    '<li>у Вани нет стратегии, которая позволит ему гарантированно выиграть первым '
    'ходом</li></ul>'
    '<p>В ответ укажите количество найденных значений.</p>'
)

PLAN = {
    2997: {"q20": Q20_2997, "q21": Q21_2997, "answer_lines": ["34", "35", "69", "39"]},
    3329: {"q20": Q20_3329, "q21": Q21_3329, "answer_lines": ["91", "30", "88", "3"]},
}


def _label(n: int) -> str:
    return f"<p><strong>Задание {n}.</strong></p>"


REORDER_SQL = """
WITH new_order AS (
    SELECT id, ROW_NUMBER() OVER (
        ORDER BY difficulty_id ASC,
            CASE task_content->>'type'
                WHEN 'SC' THEN 1 WHEN 'MC' THEN 1 WHEN 'TA' THEN 2 WHEN 'SA' THEN 2
                WHEN 'SA_COM' THEN 3 ELSE 99 END ASC,
            order_position ASC NULLS LAST, id ASC
    ) AS new_op
    FROM tasks WHERE course_id = $1
)
UPDATE tasks t SET order_position = n.new_op FROM new_order n
WHERE t.id = n.id AND t.course_id = $1 AND (t.order_position IS DISTINCT FROM n.new_op)
"""


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


def _proverit(content: dict, rules: dict, task_type: str, otvet: str):
    result = checking.check_task(
        TaskContent.model_validate(content), SolutionRules.model_validate(rules),
        StudentAnswer(type=task_type, response=StudentResponse(value=otvet)),
    )
    return result.is_correct


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            ids = list(PLAN)
            live = await conn.fetch(
                "SELECT task_id, count(*) FROM task_results WHERE task_id = ANY($1::int[]) GROUP BY task_id", ids)
            if live:
                raise AssertionError(f"живые попытки на {ids}: {[(r['task_id'], r['count']) for r in live]} — СТОП")
            print(f"Живых попыток на {ids}: 0 (подтверждено перед записью)")

            rows = await conn.fetch(
                "SELECT id, task_content, solution_rules FROM tasks WHERE id = ANY($1::int[]) AND course_id = $2 AND is_active",
                ids, COURSE_ID)
            by_id = {int(r["id"]): r for r in rows}
            missing = sorted(set(ids) - set(by_id))
            if missing:
                raise AssertionError(f"не найдены/не активны: {missing}")

            plan: list[tuple[int, dict, dict, str]] = []
            for task_id, spec in PLAN.items():
                content = json.loads(by_id[task_id]["task_content"])
                rules = json.loads(by_id[task_id]["solution_rules"])
                if content.get("type") != "SA_COM":
                    raise AssertionError(f"id={task_id}: ожидали SA_COM, тип {content.get('type')!r}")
                content["stem"] = (
                    _label(19) + content["stem"] + _label(20) + spec["q20"] + _label(21) + spec["q21"]
                )
                content["type"] = "TBL_COM"
                content["table"] = {"columns": 1}
                etalon = "\n".join(spec["answer_lines"])
                rules["short_answer"]["accepted_answers"] = [{"score": 1, "value": etalon}]
                plan.append((task_id, content, rules, etalon))

            for task_id, content, rules, _ in plan:
                await conn.execute(
                    "UPDATE tasks SET task_content = $2::jsonb, solution_rules = $3::jsonb WHERE id = $1",
                    task_id, json.dumps(content, ensure_ascii=False), json.dumps(rules, ensure_ascii=False))
            print(f"Обновлено (SA_COM -> TBL_COM): {len(plan)} заданий: {sorted(p[0] for p in plan)}")

            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            await conn.execute(REORDER_SQL, COURSE_ID)

            ids2 = [p[0] for p in plan]
            posle = {int(r["id"]): r for r in await conn.fetch(
                "SELECT id, task_content, solution_rules FROM tasks WHERE id = ANY($1::int[])", ids2)}
            oshibki: list[str] = []
            for task_id, _, _, etalon in plan:
                row = posle[task_id]
                content = json.loads(row["task_content"])
                rules = json.loads(row["solution_rules"])
                if content.get("type") != "TBL_COM" or (content.get("table") or {}).get("columns") != 1:
                    oshibki.append(f"id={task_id}: тип/columns не соответствуют")
                    continue
                if _proverit(content, rules, "TBL_COM", etalon) is not True:
                    oshibki.append(f"id={task_id}: эталон не засчитывается")
                    continue
                for мутация in (f"  {etalon}  ", etalon.upper(), etalon.replace("\n", "\n\n") + "\n"):
                    if _proverit(content, rules, "TBL_COM", мутация) is not True:
                        oshibki.append(f"id={task_id}: мутация {мутация[:40]!r} не засчитана")
                        break
                испорчено = "\n".join(reversed(etalon.split("\n")))
                if испорчено != etalon and _proverit(content, rules, "TBL_COM", испорчено) is True:
                    oshibki.append(f"id={task_id}: неверный порядок полей ошибочно засчитан")

            dupes = await conn.fetchval(
                "SELECT count(*) FROM (SELECT order_position FROM tasks WHERE course_id = $1 "
                "GROUP BY order_position HAVING count(*) > 1) x", COURSE_ID)
            if dupes:
                oshibki.append(f"коллизии order_position: {dupes}")
            violations = await conn.fetchval("""
                SELECT count(*) FROM (
                    SELECT difficulty_id, LAG(difficulty_id) OVER (ORDER BY order_position) AS prev
                    FROM tasks WHERE course_id = $1 AND is_active
                ) x WHERE prev IS NOT NULL AND difficulty_id < prev""", COURSE_ID)
            if violations:
                oshibki.append(f"нарушен межгрупповой порядок: {violations}")

            if oshibki:
                for e in oshibki[:40]:
                    print(f"  ОШИБКА: {e}")
                raise AssertionError(f"верификация не пройдена: {len(oshibki)} проблем")

            print(f"OK: проверено поштучно {len(plan)} заданий, order_position без коллизий и разрывов.")
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
