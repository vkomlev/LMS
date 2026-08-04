# -*- coding: utf-8 -*-
"""tsk-558: курс 147 (Теория игр 19-21) — блок из 3 вопросов у kompege жил как

один вопрос 19 без пары 20/21, у polyakov/tg — все 3 вопроса в стеме, но
ответ склеен в одну нечитаемую строку. Оператор нашёл живьём на проде
(kompege, спойлер «показать другие задания этого блока»): задание 19-21 —
ОДИН блок с общим условием и тремя разными вопросами (19 → 1 ответ, 20 → 2
ответа, 21 → 1 ответ), как уже верно устроено у Крылова (v1/v5/v11/v16 —
крылов остаётся нетронутым, он и так правильный).

ЧТО НАШЛОСЬ (read-only разведка через живой kompege API, 2026-08-04)
kompege API отдаёт задание 19 с полем `subTask[]`, где ЛЕЖАТ и вопрос 20, и
вопрос 21 — с текстом и ключом ответа (`key`). LMS ранее импортировала только
верхнеуровневый вопрос 19, subTask терялся целиком.

- id=2202 (ext:d4:kompege:20260602:20965, "М. Попков", Патрик/Валера):
  Q19 key=21, Q20 key="23 24", Q21 key=25.
- id=2203 (ext:d4:kompege:20260602:21714, Петя/Ваня +2/+5/x2):
  Q19 key=62, Q20 key="31 57", Q21 key=55.
- id=2204 (ext:d4:kompege:20260602:23203, Петя/Ваня -3/-7/:3):
  Q19 key=36, Q20 key="39 40", Q21 key=42.
- НОВЫЙ блок, kompege taskId=27425 (Петя/Ваня +3/+5/x3, порог 97) — в LMS
  отсутствовал полностью (сверено по числам S<=96/порог 97 + правилам хода —
  не совпадает ни с одним из 2202/2203/2204 и не совпадает с tg:ege:518,
  у которого те же ходы, но ДРУГОЕ условие победы — "не более 105" реверс
  победителя и вопрос на МАКСИМУМ, а не минимум S). Оператор в постановке
  назвал ID 27416/27417/27418 — эти ID при прямом запросе к kompege API
  ведут на СОВСЕМ другие задания (текстовый редактор, кодирование, машина
  Тьюринга), т.е. либо опечатка при переписывании со скриншота, либо kompege
  успел переиспользовать эти ID под другой контент. 27425 — единственный
  найденный вблизи блок теории игр с этим движением камней, отсутствующий в
  курсе 147; берём его как искомый новый блок.

- id=2079 (ext:d4:polyakov:20260602:4109) — уже содержит ВСЕ 3 вопроса ТЕКСТОМ
  в stem («Вопрос 1./Вопрос 2./Вопрос 3.»), но ответ — одна склеенная строка
  "1) 13 2) 24 47 3) 46". Разбирается на 4 отдельных значения: 13, 24, 47, 46.
- id=3307 (tg:ege:545) — ДОСЛОВНЫЙ дубль 2079 (тот же текст, тот же битый
  ответ, включая "1) 13 2) 24 47 3) 46") — критерий дедупа tsk-350 (текст +
  числа совпадают полностью). Деактивируется (is_active=false), не удаляется.

ФОРМАТ: TBL_COM, table.columns=1 (список полей, НЕ сетка N x M — решение
оператора). 4 поля на блок (1+2+1): по одному значению в каждой строке,
порядок — Q19, Q20 (по возрастанию — так требует само задание), Q21.
row_order_matters остаётся дефолтным True (порядок полей содержательно важен:
поле 1 — это именно ответ на 19, а не любое совпавшее число).
solution_rules.short_answer не переписывается сверх значения ответа — та же
логика, что в tsk-366/tsk-383 (смена ТИПА, не переписывание правил).

Крылов (v1/v5/v11/v16, 4 полных триплета) и sdamgia (2383/2384/2385) НЕ
трогаются — уже верны (см. tsk-558 recon в трекере).

ЖИВЫЕ ПОПЫТКИ: 0 task_results на всех 5 затрагиваемых id (2202, 2203, 2204,
2079, 3307) на момент постановки задачи (read-only прод-БД). Повторно
проверяется прямо перед записью, в этой же транзакции — если появилась хоть
одна попытка, скрипт останавливается без записи (см. блок ниже).

Запуск: dry-run по умолчанию;
  python scripts/tsk558_merge_theory_games_19_21.py
  DBCHECK_OK=1 python scripts/tsk558_merge_theory_games_19_21.py --apply
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

# ─── Q20/Q21 текст, взятый ДОСЛОВНО из kompege API (2026-08-04) ────────────

Q20_2202 = (
    '<p>Для игры, описанной в задании 19, найдите два наименьших значения S, '
    'при которых у Патрика есть выигрышная стратегия, причём одновременно '
    'выполняются два условия:</p>\n<p>&ndash; Патрик не может выиграть за один '
    'ход;</p>\n<p>&ndash; Патрик может выиграть своим вторым ходом независимо '
    'от того, как будет ходить Валера.</p>\n<p>Найденные значения запишите в '
    'ответе в порядке возрастания.</p>'
)
Q21_2202 = (
    '<p>Для игры, описанной в задании 19, найдите минимальное значение S, при '
    'котором одновременно выполняются два условия:</p>\n<p>&ndash; у Валеры '
    'есть выигрышная стратегия, позволяющая ему выиграть первым или вторым '
    'ходом при любой игре Патрика;</p>\n<p>&ndash; у Валеры нет стратегии, '
    'которая позволит ему гарантированно выиграть первым ходом.</p>'
)

Q20_2203 = (
    '<p>Для игры, описанной в задании 19, найдите два наименьших&nbsp;значения '
    'S, при которых у Пети есть выигрышная стратегия, причём&nbsp;одновременно '
    'выполняются два условия:<br>- Петя не может выиграть за один ход;<br>- '
    'Петя может выиграть своим вторым ходом независимо от того,&nbsp;как будет '
    'ходить Ваня.<br>Найденные значения запишите в ответе в порядке '
    'возрастания.</p>'
)
Q21_2203 = (
    '<p>Для игры, описанной в задании 19, найдите минимальное значение S, при '
    'котором одновременно выполняются два условия:<br>- у Вани есть '
    'выигрышная стратегия, позволяющая ему выиграть первым или вторым ходом '
    'при любой игре Пети;<br>- у Вани нет стратегии, которая позволит ему '
    'гарантированно выиграть первым ходом.</p>'
)

Q20_2204 = (
    '<p>Для игры, описанной в задании 19, найдите два наименьших&nbsp;значения '
    'S, при которых у Пети есть выигрышная стратегия, причём<br>одновременно '
    'выполняются два условия:<br>&minus; Петя не может выиграть за один '
    'ход;<br>&minus; Петя может выиграть своим вторым ходом независимо от '
    'того,&nbsp;как будет ходить Ваня.<br>Найденные значения запишите в ответе '
    'в порядке возрастания</p>'
)
Q21_2204 = (
    '<p>Для игры, описанной в задании 19, найдите минимальное значение S, при '
    'котором одновременно выполняются два&nbsp;условия:<br>&ndash; у Вани '
    'есть выигрышная стратегия, позволяющая ему выиграть первым или вторым '
    'ходом при любой игре Пети;<br>&ndash; у Вани нет стратегии, которая '
    'позволит ему гарантированно выиграть первым ходом.&nbsp;</p>'
)

# 27425: subTask 20/21 у источника повторяют условие целиком — обрезаем до
# самого вопроса и даём такую же краткую отсылку "Для игры, описанной в
# задании 19", как у 2202/2203/2204, чтобы не плодить тройной повтор правил.
STEM_27425 = (
    '<p>Два игрока, Петя и Ваня, играют в следующую игру. Перед игроками '
    'лежит куча камней. Игроки ходят по очереди, первый ход делает Петя. За '
    'один ход игрок может:</p>\n<ul>\n<li>добавить в кучу 3 камня;</li>\n'
    '<li>добавить в кучу 5 камней;</li>\n<li>увеличить количество камней в '
    'куче в 3 раза.</li>\n</ul>\n<p><em>Например</em>, из кучи в 20 камней за '
    'один ход можно получить кучу из 23, 25 или 60 камней.</p>\n<p>Чтобы '
    'делать ходы, у каждого игрока есть неограниченное количество камней. '
    'Игра завершается, когда количество камней в куче становится не менее '
    '97. Победителем считается игрок, сделавший последний ход, то есть '
    'первым получивший кучу из 97 или более камней.</p>\n<p>В начальный '
    'момент в куче было S камней, 1 ≤ S ≤ 96.</p>\n<p>Будем говорить, что '
    'игрок имеет выигрышную стратегию, если он может выиграть при любых '
    'ходах противника.</p>\n<p>Укажите <strong>минимальное</strong> значение '
    'S, при котором Петя не может выиграть за один ход, но при любом ходе '
    'Пети Ваня может выиграть своим первым ходом.</p>'
)
Q20_27425 = (
    '<p>Для игры, описанной в задании 19, найдите <strong>два наименьших'
    '</strong> значения S, при которых у Пети есть выигрышная стратегия, '
    'причём одновременно выполняются два условия:</p>\n<ul><li>Петя не может '
    'выиграть за один ход;</li><li>Петя может выиграть своим вторым ходом '
    'независимо от того, как будет ходить Ваня.</li></ul>\n<p>Найденные '
    'значения запишите в ответе в порядке возрастания.</p>'
)
Q21_27425 = (
    '<p>Для игры, описанной в задании 19, укажите <strong>минимальное'
    '</strong> значение S, при котором одновременно выполняются два условия:'
    '</p>\n<ul><li>у Вани есть выигрышная стратегия, позволяющая ему выиграть '
    'первым или вторым ходом при любой игре Пети;</li><li>у Вани нет '
    'стратегии, которая позволит ему гарантированно выиграть первым ходом.'
    '</li></ul>'
)


def _label(n: int) -> str:
    return f"<p><strong>Задание {n}.</strong></p>"


def _merge_stem(q19_stem: str, q20_text: str, q21_text: str) -> str:
    return _label(19) + q19_stem + _label(20) + q20_text + _label(21) + q21_text


# ─── План правок существующих заданий (UPDATE, id не меняется) ─────────────
# answer_lines — новый эталон columns=1: 4 строки, порядок Q19/Q20a/Q20b/Q21.

UPDATE_PLAN = {
    2202: {"q20": Q20_2202, "q21": Q21_2202, "answer_lines": ["21", "23", "24", "25"]},
    2203: {"q20": Q20_2203, "q21": Q21_2203, "answer_lines": ["62", "31", "57", "55"]},
    2204: {"q20": Q20_2204, "q21": Q21_2204, "answer_lines": ["39", "40", "36", "42"]},
    2079: {"answer_lines": ["13", "24", "47", "46"]},  # polyakov: stem уже полный
}
# 2204: порядок в answer_lines должен быть Q19,Q20a,Q20b,Q21 = 36,39,40,42
UPDATE_PLAN[2204]["answer_lines"] = ["36", "39", "40", "42"]

DUPLICATE_OF_2079 = 3307  # tg:ege:545

NEW_TASK = {
    "external_uid": "ext:d4:kompege:20260804:27425",
    "course_id": COURSE_ID,
    "difficulty_id": 2,  # как у братьев 2202/2203/2204 (kompege API difficulty=0)
    "stem": _merge_stem(STEM_27425, Q20_27425, Q21_27425),
    "answer_lines": ["30", "10", "25", "22"],
    "course_uid": "wp:zadanie-19-21-ege-po-informatike-teoriya-igr",
}

ALL_TOUCHED_IDS = list(UPDATE_PLAN.keys()) + [DUPLICATE_OF_2079]


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
        TaskContent.model_validate(content),
        SolutionRules.model_validate(rules),
        StudentAnswer(type=task_type, response=StudentResponse(value=otvet)),
    )
    return result.is_correct


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


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            # ─── Гейт: живые попытки — проверяем ПРЯМО ПЕРЕД записью ────────
            live = await conn.fetch(
                "SELECT task_id, count(*) FROM task_results "
                "WHERE task_id = ANY($1::int[]) GROUP BY task_id",
                ALL_TOUCHED_IDS,
            )
            if live:
                raise AssertionError(
                    f"на затрагиваемых заданиях появились попытки учеников: "
                    f"{[(r['task_id'], r['count']) for r in live]} — СТОП, не пишем"
                )
            print(f"Живых попыток на {ALL_TOUCHED_IDS}: 0 (подтверждено перед записью)")

            rows = await conn.fetch(
                "SELECT id, task_content, solution_rules FROM tasks "
                "WHERE id = ANY($1::int[]) AND course_id = $2 AND is_active",
                list(UPDATE_PLAN.keys()), COURSE_ID,
            )
            by_id = {int(r["id"]): r for r in rows}
            missing = sorted(set(UPDATE_PLAN) - set(by_id))
            if missing:
                raise AssertionError(f"не найдены/не активны в курсе {COURSE_ID}: {missing}")

            plan: list[tuple[int, dict, dict, str]] = []
            for task_id, spec in UPDATE_PLAN.items():
                content = json.loads(by_id[task_id]["task_content"])
                rules = json.loads(by_id[task_id]["solution_rules"])

                if content.get("type") != "SA_COM":
                    raise AssertionError(f"id={task_id}: ожидали SA_COM, тип {content.get('type')!r}")

                if "q20" in spec:
                    content["stem"] = _merge_stem(content["stem"], spec["q20"], spec["q21"])
                content["type"] = "TBL_COM"
                content["table"] = {"columns": 1}

                etalon = "\n".join(spec["answer_lines"])
                rules["short_answer"]["accepted_answers"] = [{"score": 1, "value": etalon}]

                plan.append((task_id, content, rules, etalon))

            # ─── Запись: UPDATE существующих ─────────────────────────────────
            for task_id, content, rules, _ in plan:
                await conn.execute(
                    "UPDATE tasks SET task_content = $2::jsonb, solution_rules = $3::jsonb WHERE id = $1",
                    task_id, json.dumps(content, ensure_ascii=False), json.dumps(rules, ensure_ascii=False),
                )
            print(f"Обновлено (SA_COM -> TBL_COM): {len(plan)} заданий: {sorted(UPDATE_PLAN)}")

            # ─── Запись: дубль 3307 -> is_active=false ───────────────────────
            dup_row = await conn.fetchrow(
                "SELECT task_content->>'stem' AS stem FROM tasks WHERE id = $1", DUPLICATE_OF_2079
            )
            twin_row = await conn.fetchrow(
                "SELECT task_content->>'answer_raw' AS answer_raw FROM tasks WHERE id = $1", 2079
            )
            # Критерий tsk-350: текст блока дословно совпадает (сверка по
            # длине и общей структуре — само сравнение делалось вручную при
            # разведке, здесь — защитный ассерт на регрессию плана).
            if dup_row is None or "Ваня выиграл своим первым ходом" not in (dup_row["stem"] or ""):
                raise AssertionError("3307 больше не похож на дубль 2079 текстово — не деактивирую вслепую")
            await conn.execute("UPDATE tasks SET is_active = false WHERE id = $1", DUPLICATE_OF_2079)
            print(f"Деактивирован дубль id={DUPLICATE_OF_2079} (tg:ege:545, дословный дубль 2079)")

            # ─── Запись: новый блок 27425 ─────────────────────────────────────
            new_content = {
                "code": None, "stem": NEW_TASK["stem"], "tags": None, "type": "TBL_COM",
                "media": None, "title": None, "prompt": None, "options": None,
                "has_hints": False, "course_uid": NEW_TASK["course_uid"],
                "hints_text": [], "hints_video": [], "difficulty_code": None,
                "table": {"columns": 1},
            }
            new_etalon = "\n".join(NEW_TASK["answer_lines"])
            new_rules = {
                "max_score": 1,
                "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
                "auto_check": True,
                "text_answer": None,
                "scoring_mode": "all_or_nothing",
                "short_answer": {
                    "regex": None, "use_regex": False,
                    "normalization": ["trim", "lower"],
                    "accepted_answers": [{"score": 1, "value": new_etalon}],
                },
                "partial_rules": [],
                "correct_options": [],
                "custom_scoring_config": None,
                "manual_review_required": False,
            }
            new_provenance = {
                "task": "tsk-558", "canon": 3, "source": "kompege",
                "evidence": "API difficulty=0 (базовая), как у 2202/2203/2204",
                "decided_at": "2026-08-04",
            }
            dup_check = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE external_uid = $1", NEW_TASK["external_uid"]
            )
            if dup_check:
                raise AssertionError(f"external_uid {NEW_TASK['external_uid']} уже существует")

            new_id = await conn.fetchval(
                "INSERT INTO tasks (external_uid, course_id, difficulty_id, max_score, "
                "task_content, solution_rules, is_active, requirement_level, difficulty_provenance) "
                "VALUES ($1, $2, $3, 1, $4::jsonb, $5::jsonb, true, 'required', $6::jsonb) RETURNING id",
                NEW_TASK["external_uid"], NEW_TASK["course_id"], NEW_TASK["difficulty_id"],
                json.dumps(new_content, ensure_ascii=False), json.dumps(new_rules, ensure_ascii=False),
                json.dumps(new_provenance, ensure_ascii=False),
            )
            print(f"Создано новое задание id={new_id} ({NEW_TASK['external_uid']})")
            plan.append((int(new_id), new_content, new_rules, new_etalon))

            # ─── Реордер курса (durable-логика TasksService._reorder_tasks_by_difficulty) ───
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            await conn.execute(REORDER_SQL, COURSE_ID)

            # ─── Верификация 1: самосогласованность (поштучно) ──────────────
            ids = [p[0] for p in plan]
            posle = {
                int(row["id"]): row
                for row in await conn.fetch(
                    "SELECT id, task_content, solution_rules, order_position FROM tasks WHERE id = ANY($1::int[])",
                    ids,
                )
            }
            oshibki: list[str] = []
            for task_id, _, _, etalon in plan:
                row = posle[task_id]
                content = json.loads(row["task_content"])
                rules = json.loads(row["solution_rules"])
                if content.get("type") != "TBL_COM" or (content.get("table") or {}).get("columns") != 1:
                    oshibki.append(f"id={task_id}: тип/columns не соответствуют после записи")
                    continue
                if _proverit(content, rules, "TBL_COM", etalon) is not True:
                    oshibki.append(f"id={task_id}: эталон не засчитывается после записи")
                    continue
                for мутация in (f"  {etalon}  ", etalon.upper(), etalon.replace("\n", "\n\n") + "\n"):
                    if _proverit(content, rules, "TBL_COM", мутация) is not True:
                        oshibki.append(f"id={task_id}: мутация {мутация[:40]!r} не засчитана")
                        break
                испорчено = "\n".join(reversed(etalon.split("\n")))
                if испорчено != etalon and _proverit(content, rules, "TBL_COM", испорчено) is True:
                    oshibki.append(f"id={task_id}: неверный порядок полей ошибочно засчитан")

            # ─── Верификация 2: order_position без коллизий и разрывов монотонности ─
            dupes = await conn.fetchval(
                "SELECT count(*) FROM (SELECT order_position FROM tasks WHERE course_id = $1 "
                "GROUP BY order_position HAVING count(*) > 1) x", COURSE_ID)
            if dupes:
                oshibki.append(f"коллизии order_position в курсе {COURSE_ID}: {dupes}")
            violations = await conn.fetchval("""
                SELECT count(*) FROM (
                    SELECT difficulty_id, LAG(difficulty_id) OVER (ORDER BY order_position) AS prev
                    FROM tasks WHERE course_id = $1 AND is_active
                ) x WHERE prev IS NOT NULL AND difficulty_id < prev""", COURSE_ID)
            if violations:
                oshibki.append(f"нарушен межгрупповой порядок в курсе {COURSE_ID}: {violations}")

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
