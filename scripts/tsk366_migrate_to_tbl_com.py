# -*- coding: utf-8 -*-
"""tsk-366: перевод заданий с табличным ответом в тип `TBL_COM`.

ЗАЧЕМ
Задания ЕГЭ №17/18/25/26/27 требуют ответа таблицей, но живут как `SA_COM` с
ОДНИМ полем ввода: ученик обязан угадать, чем разделять значения. Тип `TBL_COM`
даёт сетку ввода и поячеечное сравнение. Грунт помечен в данных флагом
`task_content.pending_tbl_com` (scripts/tsk366_mark_pending_tbl_com.py).

ЧТО ДЕЛАЕТ

Две корзины, и вторая не менее важна первой.

1. **Перевод в TBL_COM.** `task_content.type` → `TBL_COM`, добавляется раскладка
   `task_content.table.columns`. Правила проверки НЕ переписываются: эталон и так
   лежит в `short_answer.accepted_answers`, а движок `TBL_COM` читает тот же блок.
   Заданиям, у которых ответ сохранён впрок в `task_content.answer_raw` и стоит
   ручная проверка, правило собирается из `answer_raw`, а `manual_review_required`
   снимается — автопроверка включается.

2. **Снятие ложных пометок.** Разметка грунта шла по признаку «два и более числа
   через пробел» и захватила лишнее: задания, где ответ — ОДНА напечатанная
   программой строка («выведите чётные числа через пробел **в одну строку, не в
   столбик**» — там проверяется сам формат вывода), и задания, чей многозначный
   ответ позже исправили на одиночный (tsk-373). Сетка им противопоказана. Такие
   остаются `SA_COM`, помечаются `tbl_com_not_applicable=true` и перестают всплывать
   кандидатами в еженедельном чеке. Решение оператора 2026-07-23; принцип тот же,
   что в [[tsk-325]]/[[tsk-370]] — многозначное не переносить слепо.

РЕШЕНИЯ ОПЕРАТОРА (2026-07-23), зафиксированные дефолтами схемы, а не хардкодом:
  * режим оценки — `all_or_nothing` (как на ЕГЭ: №25 стоит 1 балл целиком);
  * порядок рядов важен (`solution_rules.table.row_order_matters`, дефолт True).
Оба — поля правила: меняются на отдельном задании без правки схемы. Скрипт их не
пишет, чтобы не плодить в данных значения, равные дефолту.

ЧИСЛО СТОЛБЦОВ
Раскрывать его безопасно — оно задано условием («выпишите число и результат
деления»). Число РЯДОВ не хранится намеренно: в №25 количество найденных чисел
само по себе часть ответа, и готовая сетка выдала бы его ученику.

ВЕРИФИКАЦИЯ (главное в этом скрипте)
210 заданий УЖЕ работают на автопроверке — миграция обязана их не сломать. Поэтому
после записи каждый переведённый ответ прогоняется через настоящий
`CheckingService`: эталон подаётся на вход как ответ ученика, и результат обязан
быть `is_correct=True`. Проверяется КАЖДОЕ задание, а не агрегат (урок tsk-317).
Дополнительно тот же эталон прогоняется с изменёнными разделителями (двойной
пробел, перевод строки) — это и есть выигрыш ученика, ради которого всё затевалось.

Запуск: dry-run по умолчанию;
  python scripts/tsk366_migrate_to_tbl_com.py
  DBCHECK_OK=1 python scripts/tsk366_migrate_to_tbl_com.py --apply
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

# ─── Классификация грунта ───────────────────────────────────────────────────

# Ответ — ОДНА напечатанная программой строка: условие требует «через пробел в
# одну строку (не в столбик)», и проверяется сам формат вывода. Сетка ввода
# противоречила бы заданию.
ODNA_STROKA_VYVODA = {212, 214, 215, 216, 230, 268, 269, 471}

# Ответ из одного значения: разметка захватила их, когда ответ был многозначным;
# позже он исправлен по источнику (tsk-373), а флаг остался.
ODNO_ZNACHENIE = {2273, 2291, 2299, 2352, 2354, 2367}

NE_TABLICA = ODNA_STROKA_VYVODA | ODNO_ZNACHENIE

# Курсы ЕГЭ с табличным ответом: №25, №18, №26, №17, №27, №19-21, №9, №14.
KURSY_EGE = {152, 146, 153, 145, 154, 147, 160, 1179, 1310}

# Задания, где число столбцов не выводится из числа значений и задано условием.
YAVNYE_STOLBCY = {
    120: 2,   # «6 чисел, по 2 на каждый запуск, в порядке запусков» → 3 ряда × 2
    565: 3,   # две даты по три числа (день, месяц, год) → 2 ряда × 3
}


def stolbcy(task_id: int, course_id: int | None, znacheniy: int) -> int:
    """
    Число столбцов в ряду для задания.

    Правило: явное значение из словаря → курс ЕГЭ с чётным числом значений
    (канон «число + частное», «величина A + величина B») → 2; пара значений в
    курсе Python → 2; всё остальное → 1 (вертикальный список).

    Единица — безопасный дефолт: список значений столбиком верен всегда, тогда
    как ошибочные 2 столбца разложили бы по рядам величины, не связанные попарно
    (например «месяцы, дни, часы, минуты» легли бы парами и запутали ученика).
    """
    if task_id in YAVNYE_STOLBCY:
        return YAVNYE_STOLBCY[task_id]
    if course_id in KURSY_EGE and znacheniy % 2 == 0:
        return 2
    if znacheniy == 2:
        return 2
    return 1


SELECT_TARGETS = """
SELECT id, course_id, max_score, task_content, solution_rules
FROM tasks
WHERE (task_content->>'pending_tbl_com')::bool IS TRUE
ORDER BY id
"""

UPDATE_TASK = """
UPDATE tasks
SET task_content = $2::jsonb, solution_rules = $3::jsonb
WHERE id = $1
"""


def _dsn() -> str:
    """Прод-DSN learn: из окружения либо из .mcp.json (как в разметке грунта)."""
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


def _etalon(content: dict, rules: dict) -> str | None:
    """Эталонный ответ: из правила проверки, иначе из сохранённого впрок answer_raw."""
    try:
        value = rules["short_answer"]["accepted_answers"][0]["value"]
        if isinstance(value, str) and value.strip():
            return value
    except (KeyError, IndexError, TypeError):
        pass
    raw = content.get("answer_raw")
    return raw if isinstance(raw, str) and raw.strip() else None


def _proverit(content: dict, rules: dict, otvet: str) -> bool:
    """Прогон ответа через настоящий движок проверки — как это сделает сервер."""
    result = checking.check_task(
        TaskContent.model_validate(content),
        SolutionRules.model_validate(rules),
        StudentAnswer(
            type=content["type"], response=StudentResponse(value=otvet)
        ),
    )
    return result.is_correct is True


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    perevedeno = 0
    snyato = 0
    try:
        async with conn.transaction():
            rows = await conn.fetch(SELECT_TARGETS)
            print(f"Помечено флагом pending_tbl_com: {len(rows)}")

            plan: list[tuple[int, dict, dict, str | None, bool]] = []
            bez_etalona: list[int] = []
            vklyuchena_avtoproverka: list[int] = []

            for r in rows:
                task_id = int(r["id"])
                content = json.loads(r["task_content"])
                rules = json.loads(r["solution_rules"]) if r["solution_rules"] else {}
                if not isinstance(rules, dict):
                    rules = {}
                etalon = _etalon(content, rules)

                content.pop("pending_tbl_com", None)

                if task_id in NE_TABLICA:
                    content["tbl_com_not_applicable"] = True
                    plan.append((task_id, content, rules, None, False))
                    continue

                if not etalon:
                    bez_etalona.append(task_id)
                    continue

                znacheniy = len(etalon.split())
                content["type"] = "TBL_COM"
                content["table"] = {"columns": stolbcy(task_id, r["course_id"], znacheniy)}

                # Правила не переписываем — движок TBL_COM читает тот же блок.
                # Собираем их только там, где эталон лежал впрок в answer_raw.
                # `short_answer` бывает JSON-null, а не отсутствующим ключом:
                # `.get(..., {})` такой случай не покрывает (tsk-361, форма 2).
                sa = rules.get("short_answer") or {}
                if not sa.get("accepted_answers"):
                    vklyuchena_avtoproverka.append(task_id)
                    max_score = int(rules.get("max_score") or r["max_score"] or 1)
                    rules["max_score"] = max_score
                    rules["short_answer"] = {
                        "normalization": ["trim", "lower"],
                        "accepted_answers": [{"value": etalon, "score": max_score}],
                    }
                    rules["auto_check"] = True
                    rules["manual_review_required"] = False

                plan.append((task_id, content, rules, etalon, True))

            v_tbl = [p for p in plan if p[4]]
            v_sa = [p for p in plan if not p[4]]
            print(f"  перевод в TBL_COM:        {len(v_tbl)}")
            print(f"  остаются SA_COM (не таблица): {len(v_sa)}")
            print(f"  включена автопроверка (ответ был впрок в answer_raw): "
                  f"{len(vklyuchena_avtoproverka)}")
            if bez_etalona:
                print(f"  ПРОПУЩЕНО (нет эталона):  {len(bez_etalona)} → {bez_etalona}")

            raskladka: dict[int, int] = {}
            for _, content, _, _, is_tbl in plan:
                if is_tbl:
                    cols = content["table"]["columns"]
                    raskladka[cols] = raskladka.get(cols, 0) + 1
            print(f"  по числу столбцов: {dict(sorted(raskladka.items()))}")

            print("\nПримеры перевода:")
            for task_id, content, _, etalon, is_tbl in v_tbl[:5]:
                assert etalon is not None
                print(
                    f"  id={task_id} столбцов={content['table']['columns']} "
                    f"эталон={etalon[:50]!r}"
                )

            # ─── Запись ─────────────────────────────────────────────────────
            for task_id, content, rules, _, is_tbl in plan:
                await conn.execute(
                    UPDATE_TASK, task_id, json.dumps(content, ensure_ascii=False),
                    json.dumps(rules, ensure_ascii=False),
                )
                if is_tbl:
                    perevedeno += 1
                else:
                    snyato += 1
            print(f"\nЗаписано: переведено {perevedeno}, снята пометка {snyato}")

            # ─── Верификация: КАЖДОЕ задание, а не агрегат ──────────────────
            ids = [p[0] for p in plan]
            posle = {
                int(row["id"]): row
                for row in await conn.fetch(
                    "SELECT id, task_content, solution_rules FROM tasks WHERE id = ANY($1::int[])",
                    ids,
                )
            }

            oshibki: list[str] = []
            ruchnye: list[int] = []
            for task_id, _, _, etalon, is_tbl in plan:
                row = posle[task_id]
                content = json.loads(row["task_content"])
                rules = json.loads(row["solution_rules"]) if row["solution_rules"] else {}

                if content.get("pending_tbl_com") is not None:
                    oshibki.append(f"id={task_id}: флаг pending_tbl_com не снят")

                if not is_tbl:
                    if content.get("type") != "SA_COM" and content.get("type") != "SA":
                        oshibki.append(f"id={task_id}: тип изменён, хотя не должен был")
                    if content.get("tbl_com_not_applicable") is not True:
                        oshibki.append(f"id={task_id}: не помечен tbl_com_not_applicable")
                    continue

                assert etalon is not None
                if content.get("type") != "TBL_COM":
                    oshibki.append(f"id={task_id}: тип не TBL_COM")
                    continue

                # Задание с обязательной ручной проверкой авто-вердикта не выносит
                # по замыслу (tsk-230). Скрипт этот флаг НЕ снимает — он выставлен
                # методистом и к формату ответа отношения не имеет. Проверяем то,
                # что и должно быть: движок уводит ответ в очередь преподавателя.
                if rules.get("manual_review_required") is True:
                    ruchnye.append(task_id)
                    result = checking.check_task(
                        TaskContent.model_validate(content),
                        SolutionRules.model_validate(rules),
                        StudentAnswer(type="TBL_COM", response=StudentResponse(value=etalon)),
                    )
                    if result.is_correct is not None:
                        oshibki.append(
                            f"id={task_id}: ручная проверка, а движок вынес авто-вердикт"
                        )
                    continue

                # Эталон обязан засчитываться движком — иначе задание сломано.
                if not _proverit(content, rules, etalon):
                    oshibki.append(f"id={task_id}: эталон НЕ засчитывается после перевода")
                    continue
                # И то, ради чего всё затевалось: разделитель больше не угадывают.
                for mutaciya in (
                    etalon.replace(" ", "  "),
                    etalon.replace(" ", "\n"),
                    f"  {etalon}  ",
                ):
                    if not _proverit(content, rules, mutaciya):
                        oshibki.append(
                            f"id={task_id}: ответ с другим разделителем не засчитан"
                        )
                        break

            ostalos = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE task_content ? 'pending_tbl_com'"
            )
            if ostalos:
                oshibki.append(f"флаг pending_tbl_com остался у {ostalos} заданий")

            if oshibki:
                for e in oshibki[:30]:
                    print(f"  ОШИБКА: {e}")
                raise AssertionError(f"верификация не пройдена: {len(oshibki)} проблем")

            if ruchnye:
                print(
                    f"  осталось на ручной проверке (флаг методиста не трогаем): "
                    f"{len(ruchnye)} → {ruchnye}"
                )
            print(f"OK: проверено поштучно {len(plan)} заданий, расхождений нет.")

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
