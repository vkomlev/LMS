# -*- coding: utf-8 -*-
"""tsk-558, пятый проход: слить Крылова (v1/v5/v11/v16) — оператор поймал

живьём (#9519, crylov:v11t20 — стоит отдельным заданием, а не частью
слитого блока), что первый проход ошибочно объявил Крылова "уже верным":
исходное решение оператора («брать подход kompege/polyakov/КРЫЛОВА — ОДНО
задание») читалось как «Крылов уже даёт образец правильного слияния», а на
деле в LMS Крылов лежит ТРЕМЯ отдельными заданиями (t19 SA_COM, t20 TBL_COM
columns=2 "сеткой", t21 SA_COM) — та же архитектура, что чинили у всех
остальных источников этого курса, тут просто не была применена вовсе.

ЖИВАЯ ПОПЫТКА — НЕ 0, В ОТЛИЧИЕ ОТ ВСЕХ ПРЕДЫДУЩИХ ПРОХОДОВ
Реальный ученик user_id=4512 сегодня (2026-08-05, 07:05-07:25) прошёл все 4
"задания 19" (crylov v1/v5/v11/v16 t19) правильными ответами — ДО того, как
блоки слиты. Оператор explicitly решил: "Делай мерж и вернём студента к
решению этой задачи заново" — т.е. НЕ грандфазерить эти 4 ответа в слитое
задание, а честно сбросить прогресс на них.

Смена content/type сама по себе НИЧЕГО не сбрасывает (read-only разведка
через агента, 2026-08-05): `compute_task_state`/`compute_course_state`/
"Попыток: N/3" читают ТОЛЬКО `task_results` по `task_id`, версии контента в
схеме не существует. Поэтому помимо слияния скрипт точечно удаляет 4 строки
`task_results` этого студента по этим 4 (уже смердженным) id — НЕ трогая
саму `attempts` (она course-level, общая на несколько заданий курса, снос
`attempts` каскадом задел бы результаты ДРУГИХ заданий той же попытки) — и
чистит кеш `student_course_state` (student_id=4512), чтобы дашборд не
показывал устаревший статус до следующего пересчёта.

СЛИЯНИЕ
Для каждого варианта (v1/v5/v11/v16): задание t19 (id ниже) становится
СЛИТЫМ TBL_COM columns=1 (4 поля: Q19, Q20a, Q20b, Q21) — та же архитектура,
что у kompege/sdamgia/polyakov/yandex-блоков этого курса. Тексты t20/t21 УЖЕ
были в правильном кратком формате "Для игры, описанной в задании 19, ..."
(не нужно вычленять хвост regex'ом, как для sdamgia/yandex — копируются
verbatim). Задания t20/t21 деактивируются (is_active=false, не удаляются) —
их содержимое теперь живёт внутри t19.

  v1  (primary=4579): Q19=244, Q20=(247,248) [9490], Q21=252 [9491]
  v5  (primary=9505): Q19=96,  Q20=(98,99)   [9506], Q21=100 [9507]
  v11 (primary=9518): Q19=51,  Q20=(46,50)   [9519], Q21=45  [9520]
  v16 (primary=4580): Q19=29,  Q20=(52,56)   [9563], Q21=51  [9531]

Запуск: dry-run по умолчанию;
  python scripts/tsk558_merge_crylov_19_21.py
  DBCHECK_OK=1 python scripts/tsk558_merge_crylov_19_21.py --apply
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
RESET_STUDENT_ID = 4512
RESET_COURSE_IDS = (112, 147)  # корень-навигатор + сам курс — оба кеша

# {primary_id (=t19): {"t20": id, "t21": id, "answer_lines": [...]}}
VARIANTS = {
    4579: {"t20": 9490, "t21": 9491, "answer_lines": ["244", "247", "248", "252"], "label": "v1"},
    9505: {"t20": 9506, "t21": 9507, "answer_lines": ["96", "98", "99", "100"], "label": "v5"},
    9518: {"t20": 9519, "t21": 9520, "answer_lines": ["51", "46", "50", "45"], "label": "v11"},
    4580: {"t20": 9563, "t21": 9531, "answer_lines": ["29", "52", "56", "51"], "label": "v16"},
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
            all_ids = list(VARIANTS) + [v["t20"] for v in VARIANTS.values()] + [v["t21"] for v in VARIANTS.values()]

            # ─── Гейт живых попыток: ожидаем РОВНО 4 строки студента 4512 на
            # primary-id и НИЧЕГО больше (другой студент/другой task_id —
            # СТОП, план не рассчитан на это) ──────────────────────────────
            live = await conn.fetch(
                "SELECT task_id, user_id, count(*) FROM task_results WHERE task_id = ANY($1::int[]) "
                "GROUP BY task_id, user_id", all_ids)
            unexpected = [
                r for r in live
                if not (int(r["user_id"]) == RESET_STUDENT_ID and int(r["task_id"]) in VARIANTS)
            ]
            if unexpected:
                raise AssertionError(f"неожиданные попытки (не студент {RESET_STUDENT_ID} на primary-id): "
                                      f"{[(r['task_id'], r['user_id'], r['count']) for r in unexpected]} — СТОП")
            found_students = {int(r["user_id"]) for r in live}
            if found_students - {RESET_STUDENT_ID}:
                raise AssertionError(f"попытки других студентов: {found_students - {RESET_STUDENT_ID}} — СТОП")
            print(f"Живые попытки: только студент {RESET_STUDENT_ID} на 4 primary-id "
                  f"(ожидаемо, сбрасываем ниже по явному решению оператора).")

            rows = await conn.fetch(
                "SELECT id, task_content, solution_rules FROM tasks WHERE id = ANY($1::int[]) AND course_id = $2 AND is_active",
                all_ids, COURSE_ID)
            by_id = {int(r["id"]): r for r in rows}
            missing = sorted(set(all_ids) - set(by_id))
            if missing:
                raise AssertionError(f"не найдены/не активны: {missing}")

            plan: list[tuple[int, dict, dict, str]] = []
            for primary_id, spec in VARIANTS.items():
                content = json.loads(by_id[primary_id]["task_content"])
                rules = json.loads(by_id[primary_id]["solution_rules"])
                if content.get("type") != "SA_COM":
                    raise AssertionError(f"id={primary_id}: ожидали SA_COM, тип {content.get('type')!r}")

                t20_content = json.loads(by_id[spec["t20"]]["task_content"])
                t21_content = json.loads(by_id[spec["t21"]]["task_content"])
                if t20_content.get("type") != "TBL_COM" or t21_content.get("type") != "SA_COM":
                    raise AssertionError(f"{spec['label']}: неожиданные типы t20/t21 — план устарел")

                content["stem"] = (
                    _label(19) + content["stem"]
                    + _label(20) + t20_content["stem"]
                    + _label(21) + t21_content["stem"]
                )
                content["type"] = "TBL_COM"
                content["table"] = {"columns": 1}
                etalon = "\n".join(spec["answer_lines"])
                rules["short_answer"]["accepted_answers"] = [{"score": 1, "value": etalon}]
                plan.append((primary_id, content, rules, etalon))

            for task_id, content, rules, _ in plan:
                await conn.execute(
                    "UPDATE tasks SET task_content = $2::jsonb, solution_rules = $3::jsonb WHERE id = $1",
                    task_id, json.dumps(content, ensure_ascii=False), json.dumps(rules, ensure_ascii=False))
            print(f"Слито (SA_COM -> TBL_COM): {len(plan)} заданий: {sorted(p[0] for p in plan)}")

            deactivate_ids = [v["t20"] for v in VARIANTS.values()] + [v["t21"] for v in VARIANTS.values()]
            await conn.execute(
                "UPDATE tasks SET is_active = false WHERE id = ANY($1::int[])", deactivate_ids)
            print(f"Деактивированы t20/t21 (содержимое перенесено в t19): {sorted(deactivate_ids)}")

            # ─── Сброс прогресса студента 4512 — явное решение оператора ────
            # Живой гейт выше уже подтвердил: ТОЛЬКО этот студент, ТОЛЬКО эти
            # 4 task_id (могут быть несколько попыток на один task_id —
            # 9518 их 3, 9505 их 2 — поэтому сверяем со счётчиком ДО удаления,
            # не с зашитым числом заданий).
            expected = await conn.fetchval(
                "SELECT count(*) FROM task_results WHERE user_id = $1 AND task_id = ANY($2::int[])",
                RESET_STUDENT_ID, list(VARIANTS))
            deleted = await conn.fetchval(
                "WITH d AS (DELETE FROM task_results WHERE user_id = $1 AND task_id = ANY($2::int[]) RETURNING 1) "
                "SELECT count(*) FROM d", RESET_STUDENT_ID, list(VARIANTS))
            if int(deleted) != int(expected):
                raise AssertionError(f"ожидали удалить {expected} строк task_results (как до удаления), удалено {deleted}")
            print(f"Удалено task_results студента {RESET_STUDENT_ID} на 4 primary-id: {deleted} строк "
                  f"(attempts НЕ тронуты — она course-level, общая на другие задания курса).")

            cache_deleted = await conn.fetchval(
                "WITH d AS (DELETE FROM student_course_state WHERE student_id = $1 AND course_id = ANY($2::int[]) RETURNING 1) "
                "SELECT count(*) FROM d", RESET_STUDENT_ID, list(RESET_COURSE_IDS))
            print(f"Очищен кеш student_course_state студента {RESET_STUDENT_ID} для курсов {RESET_COURSE_IDS}: "
                  f"{cache_deleted} строк (пересчитается лениво при следующем обращении).")

            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            await conn.execute(REORDER_SQL, COURSE_ID)

            # ─── Верификация ──────────────────────────────────────────────
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

            still_there = await conn.fetchval(
                "SELECT count(*) FROM task_results WHERE user_id = $1 AND task_id = ANY($2::int[])",
                RESET_STUDENT_ID, list(VARIANTS))
            if still_there:
                oshibki.append(f"после удаления всё ещё {still_there} task_results студента {RESET_STUDENT_ID}")

            active_check = await conn.fetch(
                "SELECT id, is_active FROM tasks WHERE id = ANY($1::int[])", deactivate_ids)
            still_active = [r["id"] for r in active_check if r["is_active"]]
            if still_active:
                oshibki.append(f"t20/t21 остались активными: {still_active}")

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

            print(f"OK: проверено поштучно {len(plan)} слитых заданий, 4/4 task_results снесены, "
                  "t20/t21 деактивированы, order_position без коллизий и разрывов.")
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
