# -*- coding: utf-8 -*-
"""tsk-389: переоценка сложности 225 заданий курса 88 «Python для ЕГЭ».

Отличие от tsk-381/382 (ЕГЭ/ОГЭ): внешнего канона сложности здесь НЕТ —
задания авторские, банка с готовой оценкой за ними нет, разметки уровней в
разборах нет. Поэтому шкала выработана в чипе вместе с оператором, а не
сверена с источником. Провенанс это фиксирует явно: source="калибровка",
canon=null.

ШКАЛА (согласована с оператором 2026-08-08)
Ведущая ось — сколько ученик должен придумать сам сверх того, что дано
в условии:
  EASY   — одна операция, показанная в теме; 1-3 строки; краевых случаев нет
  NORMAL — сборка 2-3 разобранных в теме конструкций; схема стандартная
  HARD   — (а) схемы в теме не было; (б) краевой случай, на котором наивное
           решение молча врёт; (в) >=4 взаимозависимых конструкций либо своя
           функция с нетривиальной логикой; (г) тема из другого модуля
Поправки (суммарный сдвиг не больше одного уровня):
  вниз  — метод/конструкция решения дословно названы В ТЕКСТЕ УСЛОВИЯ
  вверх — краевой случай; риск формата составного вывода; чужая тема
Подсказки hints НЕ учитываются: они писались под старую разметку (есть у
32 HARD из 32), поэтому поправка за них переносила бы старую метку, а не
оценивала задание.

Разбор по каждому заданию:
  reviews/2026-08-08-tsk389-python-difficulty-full-pass.md

ЧТО ДЕЛАЕТ СКРИПТ
1. Читает текущее состояние и сверяет его с ожидаемым (защита от дрейфа:
   если чужая сессия уже поменяла difficulty — скрипт падает, а не пишет).
2. Меняет difficulty_id у 81 задания.
3. Проставляет difficulty_provenance ВСЕМ 225 (в т.ч. неизменившимся —
   факт «оценка пересмотрена калибровкой» важен и для них).
4. Явно вызывает реордер по каждому затронутому подкурсу. Прямая запись
   идёт мимо TasksService.bulk_upsert, поэтому durable-хук tsk-345 сам не
   сработает. Триггер глушится ТОЛЬКО через session-var
   app.skip_task_order_trigger (is_local=true), НЕ через ALTER TABLE
   DISABLE TRIGGER — последнее берёт ACCESS EXCLUSIVE лок на всю таблицу
   tasks и вешает live-запросы студентов по ВСЕМ курсам (урок tsk-345/346).
5. Верифицирует поштучно: difficulty у всех 225, провенанс у всех 225,
   самосогласованность order_position внутри каждого подкурса.

THEORY (137 заданий) не трогается вовсе — она проставлена по надёжному
структурному признаку в tsk-346.

Запуск: dry-run по умолчанию;
  python scripts/tsk389_recalibrate_python_difficulty.py
  DBCHECK_OK=1 python scripts/tsk389_recalibrate_python_difficulty.py --apply
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

ROOT_COURSE = 88
SUBCOURSES = (90, 106, 103, 108, 111, 110, 109, 104, 1451, 105, 107)
THEORY = 1
EASY, NORMAL, HARD = 2, 3, 4
DECIDED_AT = "2026-08-08"

# id -> (было, стало, обоснование). Только те, у кого уровень меняется.
CHANGES: dict[int, tuple[int, int, str]] = {
    # ─── 106 «Первая программа» ───────────────────────────────────────
    120: (EASY, NORMAL, "минуты в часы и минуты: нужны // и %, инструмент не назван"),
    121: (NORMAL, HARD, "мотоциклы: округление вверх (n+1)//2 надо изобрести, if запрещён и ещё не пройден"),
    # ─── 103 «Числа» ──────────────────────────────────────────────────
    58: (EASY, NORMAL, "круги на стадионе: // и % не названы, это сборка а не подстановка"),
    59: (EASY, NORMAL, "сумма дробных частей: приём x - int(x) не назван"),
    62: (NORMAL, EASY, "ключ решения (480 минут в рабочем дне) выписан в условии, остаётся одно деление"),
    # ─── 108 «Строки» ─────────────────────────────────────────────────
    134: (NORMAL, EASY, "срез с шагом расписан словами в условии"),
    136: (NORMAL, EASY, "индекс и шаг среза расписаны в условии"),
    142: (HARD, EASY, "все значения заданы, все 4 формата выписаны дословно, f-строки названы"),
    145: (EASY, NORMAL, "требует if, а модуль «Условные конструкции» идёт после строк"),
    146: (EASY, NORMAL, "требует if, а модуль «Условные конструкции» идёт после строк"),
    # ─── 111 «Условные конструкции» ───────────────────────────────────
    177: (NORMAL, EASY, "точная формула високосности выписана в условии строкой кода"),
    180: (NORMAL, EASY, "формула перевода фут-км и все три ветки выписаны"),
    181: (NORMAL, EASY, "формулы площадей, math.pi и все ветки выписаны"),
    185: (NORMAL, EASY, "условие хода ферзя выписано целиком, считать нечего"),
    186: (NORMAL, EASY, "условие хода коня выписано целиком, считать нечего"),
    190: (HARD, NORMAL, "все 5 проверок выписаны формулами; трудность только в объёме и порядке 10 значений"),
    # ─── 110 «Циклы» ──────────────────────────────────────────────────
    217: (NORMAL, EASY, "весь сценарий while True с break выписан в условии"),
    218: (NORMAL, EASY, "while True и break названы; тот же класс, что 233 (EASY)"),
    226: (NORMAL, EASY, "среднее 20 чисел: цикл и деление, схема элементарна"),
    232: (EASY, NORMAL, "Хоббит: цикл с накоплением, счётчик дней и условие «впервые достигнет» — схему надо собрать самому"),
    # ─── 109 «Списки» ─────────────────────────────────────────────────
    255: (NORMAL, EASY, "квадраты чётных: то же семейство генераторов, что 252/254"),
    256: (NORMAL, EASY, "comprehension назван, условие с or тривиально"),
    258: (NORMAL, EASY, "import math и comprehension названы"),
    261: (NORMAL, EASY, "«срез с шагом» назван в условии"),
    262: (NORMAL, EASY, "условие само расшифровывает индексы, off-by-one снят"),
    266: (NORMAL, EASY, "индексы lst[1], lst[3], lst[5] выписаны в условии"),
    269: (HARD, NORMAL, "условие разжевало и приём, и правило совпадения длин, и что брать из b"),
    272: (HARD, NORMAL, "повтор элемента i+1 раз: enumerate + extend, схема стандартная"),
    276: (EASY, NORMAL, "удалить элементы > 5: инструмент не назван, скрытая ловушка удаления при итерации"),
    # ─── 104 «Функции» ────────────────────────────────────────────────
    77: (NORMAL, EASY, "формат вывода kwargs выписан, включая отдельное разъяснение как НЕ надо"),
    80: (NORMAL, EASY, "то же, что 78; всё выписано вплоть до строки вызова"),
    87: (NORMAL, EASY, "math.gcd назван, остаются два деления"),
    88: (NORMAL, EASY, "aeiouy, .lower(), isalpha выписаны — обе тонкости сняты условием"),
    92: (HARD, EASY, "все 4 функции тривиальны, словарь и строка вызова выписаны, вызывается одна"),
    93: (NORMAL, EASY, "строка вызова filter выписана целиком"),
    94: (NORMAL, EASY, "два «плагина» в словаре, цикл вызова выписан"),
    # ─── 1451 «Рекурсия» ──────────────────────────────────────────────
    10006: (EASY, NORMAL, "направление вывода зависит от A<B, схему надо собрать самому, ничего не выписано"),
    10004: (NORMAL, HARD, "ключ — print ПОСЛЕ рекурсивного вызова (раскрутка стека); наивный порядок молча даёт прямой вывод"),
    10007: (NORMAL, HARD, "сборка результата после раскрутки + обобщение на любое основание + краевой случай n=0"),
    # ─── 105 «Множества» ──────────────────────────────────────────────
    285: (NORMAL, EASY, "генератор множества назван"),
    287: (NORMAL, EASY, "трассировка discard+add: три шага без ловушек"),
    290: (NORMAL, EASY, "генератор с условием-фильтром назван"),
    292: (NORMAL, EASY, "трассировка фильтра-генератора"),
    294: (HARD, EASY, "оба генератора отработаны в 285/290, symmetric_difference назван"),
    295: (NORMAL, EASY, "intersection назван, множества готовые"),
    296: (NORMAL, EASY, "трассировка одного оператора ^"),
    297: (NORMAL, EASY, "difference_update назван"),
    298: (NORMAL, EASY, "len назван, все три ветки выписаны"),
    300: (NORMAL, EASY, "выбор правильного оператора из готовых вариантов"),
    301: (NORMAL, EASY, "union/update названы, дальше механический подсчёт"),
    303: (NORMAL, EASY, "union и .lower() названы"),
    305: (NORMAL, EASY, "выбор update вместо add из готовых вариантов"),
    306: (NORMAL, EASY, "issubset назван"),
    307: (NORMAL, EASY, "difference_update назван"),
    308: (NORMAL, EASY, "issuperset назван"),
    310: (NORMAL, EASY, "split и set названы, дальше подсчёт"),
    313: (NORMAL, EASY, "symmetric_difference назван"),
    315: (NORMAL, EASY, "difference_update назван"),
    316: (NORMAL, EASY, "два генератора + intersection (назван) — тот же состав, что 294"),
    317: (HARD, NORMAL, "самая длинная цепочка модуля, но условие подсказывает считать c целиком"),
    318: (HARD, EASY, "«используйте цикл и update» — и приём, и схема названы в условии"),
    320: (HARD, NORMAL, "способ построить 26 букв алфавита не назван, дальше три стандартных шага"),
    321: (NORMAL, EASY, "difference назван"),
    322: (HARD, EASY, "симметрическая разность двух готовых множеств, метод назван — одна операция"),
    323: (HARD, NORMAL, "split/set названы, но сам метод отбора — нет"),
    324: (HARD, EASY, "пересечение двух готовых множеств, intersection назван"),
    325: (NORMAL, EASY, "пересечение с гласными, приём назван"),
    326: (HARD, EASY, "выбор правильного направления <= из готовых вариантов"),
    # ─── 107 «Словари» ────────────────────────────────────────────────
    345: (HARD, EASY, "удалить ключ с проверкой in: оператор назван, три строки. Полный близнец 355"),
    346: (NORMAL, EASY, "zip и dict названы в условии"),
    353: (HARD, NORMAL, "своя функция здесь однострочная обёртка — признаком HARD не считается"),
    355: (NORMAL, EASY, "удалить ключ с проверкой in: близнец 345"),
    356: (HARD, NORMAL, "подвох ссылочной семантики реальный, но copy назван прямо в условии"),
    357: (NORMAL, EASY, "update назван — одна операция"),
    358: (NORMAL, EASY, "sum и len названы — одна формула"),
    359: (NORMAL, EASY, "min назван"),
    362: (HARD, NORMAL, "приём в условии не назван, но схема сортировки по ключам стандартная"),
    365: (HARD, EASY, "стандартный накопитель темы, get(key, 0) назван прямо в условии"),
    366: (HARD, NORMAL, "приём не назван, схема стандартная, формат вывода простой"),
    369: (HARD, NORMAL, "вложенные словари — новый приём, но схема прямая: цикл по items + внутренние ключи"),
    569: (NORMAL, HARD, "словарь-шифр: нужны ord/chr (тема модуля 108) и краевой z->a, где chr(ord(c)+1) даёт {"),
}

CONFIRMED_EVIDENCE = (
    "уровень подтверждён калибровкой, не изменился "
    "(разбор — reviews/2026-08-08-tsk389-python-difficulty-full-pass.md)"
)

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
    """Прод-DSN learn: из окружения либо из .mcp.json. Секрет не печатаем."""
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


def _provenance(evidence: str, changed: bool, was: int | None, now: int) -> str:
    payload = {
        "task": "tsk-389",
        "canon": None,
        "source": "калибровка",
        "method": "шкала tsk-389: сколько ученик должен придумать сам; поправка за метод, названный в условии",
        "evidence": evidence,
        "changed": changed,
        "decided_at": DECIDED_AT,
    }
    if changed:
        payload["was_difficulty_id"] = was
        payload["now_difficulty_id"] = now
    return json.dumps(payload, ensure_ascii=False)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="выполнить запись (по умолчанию dry-run)")
    args = ap.parse_args()

    conn = await asyncpg.connect(_dsn())
    try:
        # ─── ЧТЕНИЕ: снять фактическое состояние ───────────────────────
        rows = await conn.fetch(
            """
            SELECT id, course_id, difficulty_id, difficulty_provenance IS NOT NULL AS has_prov
            FROM tasks
            WHERE is_active AND course_id = ANY($1::int[]) AND difficulty_id <> $2
            ORDER BY course_id, id
            """,
            list(SUBCOURSES), THEORY,
        )
        current = {r["id"]: r["difficulty_id"] for r in rows}
        theory_cnt = await conn.fetchval(
            "SELECT count(*) FROM tasks WHERE is_active AND course_id = ANY($1::int[]) AND difficulty_id = $2",
            list(SUBCOURSES), THEORY,
        )

        print(f"Активных не-THEORY заданий в подкурсах курса {ROOT_COURSE}: {len(current)}")
        print(f"THEORY (не трогаем): {theory_cnt}")
        if len(current) != 225:
            print(f"  ВНИМАНИЕ: ожидалось 225, найдено {len(current)} — курс изменился с момента разбора.")

        # ─── ЗАЩИТА ОТ ДРЕЙФА: сверить «было» с фактом ─────────────────
        drift: list[str] = []
        missing: list[int] = []
        for tid, (was, now, _) in CHANGES.items():
            if tid not in current:
                missing.append(tid)
            elif current[tid] != was:
                drift.append(f"  id={tid}: в разборе было {was}, в БД сейчас {current[tid]}")
        if missing:
            print(f"ОШИБКА: заданий нет среди активных не-THEORY: {sorted(missing)}")
            return 2
        if drift:
            print("ОШИБКА: сложность изменилась после разбора (чужая правка?):")
            print("\n".join(drift))
            return 2

        # ─── ПЛАН ──────────────────────────────────────────────────────
        after = dict(current)
        for tid, (_, now, _r) in CHANGES.items():
            after[tid] = now
        def dist(d: dict[int, int]) -> str:
            return " / ".join(f"{n}:{sum(1 for v in d.values() if v == c)}"
                              for c, n in ((EASY, "EASY"), (NORMAL, "NORMAL"), (HARD, "HARD")))
        print(f"\nБыло : {dist(current)}")
        print(f"Стало: {dist(after)}")
        up = sum(1 for t, (w, n, _r) in CHANGES.items() if n > w)
        print(f"Меняется: {len(CHANGES)} заданий ({len(CHANGES) - up} вниз, {up} вверх)")

        touched = sorted({r["course_id"] for r in rows if r["id"] in CHANGES})
        print(f"Подкурсы под реордер: {touched}")

        if not args.apply:
            print("\nDRY-RUN. Первые 10 правок:")
            for tid in sorted(CHANGES)[:10]:
                w, n, why = CHANGES[tid]
                print(f"  id={tid}: {w} -> {n}  — {why}")
            print("\nЗапись не выполнялась. Для применения:")
            print("  DBCHECK_OK=1 python scripts/tsk389_recalibrate_python_difficulty.py --apply")
            return 0

        # ─── ЗАПИСЬ В ТРАНЗАКЦИИ ───────────────────────────────────────
        async with conn.transaction():
            # 1) сложность у изменившихся + провенанс
            for tid, (was, now, why) in CHANGES.items():
                await conn.execute(
                    "UPDATE tasks SET difficulty_id = $2, difficulty_provenance = $3::jsonb WHERE id = $1",
                    tid, now, _provenance(why, True, was, now),
                )
            # 2) провенанс у подтверждённых (уровень не менялся)
            confirmed = [tid for tid in current if tid not in CHANGES]
            for tid in confirmed:
                await conn.execute(
                    "UPDATE tasks SET difficulty_provenance = $2::jsonb WHERE id = $1",
                    tid, _provenance(CONFIRMED_EVIDENCE, False, None, current[tid]),
                )
            print(f"\nОбновлено: сложность у {len(CHANGES)}, провенанс у {len(current)} заданий.")

            # 3) явный реордер — durable-хук tsk-345 при прямой записи не срабатывает
            for cid in touched:
                await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
                await conn.execute(REORDER_SQL, cid)
            print(f"Реордер выполнен по подкурсам: {touched}")

        # ─── ВЕРИФИКАЦИЯ (после COMMIT, независимым чтением) ───────────
        bad_diff, bad_prov = [], []
        for tid, expected in after.items():
            row = await conn.fetchrow(
                "SELECT difficulty_id, difficulty_provenance->>'task' AS ptask FROM tasks WHERE id = $1", tid
            )
            if row["difficulty_id"] != expected:
                bad_diff.append(f"  id={tid}: ожидалось {expected}, в БД {row['difficulty_id']}")
            if row["ptask"] != "tsk-389":
                bad_prov.append(f"  id={tid}: провенанс не проставлен")
        if bad_diff:
            print(f"ВЕРИФИКАЦИЯ ПРОВАЛЕНА (сложность), {len(bad_diff)}:")
            print("\n".join(bad_diff[:20]))
            return 3
        if bad_prov:
            print(f"ВЕРИФИКАЦИЯ ПРОВАЛЕНА (провенанс), {len(bad_prov)}:")
            print("\n".join(bad_prov[:20]))
            return 3
        print(f"Верификация 1/3: сложность и провенанс верны поштучно у всех {len(after)}.")

        # THEORY не тронута
        theory_after = await conn.fetchval(
            "SELECT count(*) FROM tasks WHERE is_active AND course_id = ANY($1::int[]) AND difficulty_id = $2",
            list(SUBCOURSES), THEORY,
        )
        if theory_after != theory_cnt:
            print(f"ВЕРИФИКАЦИЯ ПРОВАЛЕНА: THEORY была {theory_cnt}, стала {theory_after}")
            return 3
        print(f"Верификация 2/3: THEORY не тронута ({theory_after}).")

        # order_position самосогласован — проверяем ТОЛЬКО реордеренные курсы.
        # Курс 90 сюда не входит: там одно задание с order_position=0 (так было
        # до tsk-389), сложность в нём не менялась и реордер по нему не звался —
        # ловить чужой пред-существующий перекос своей проверкой нечестно.
        order_bad = []
        for cid in touched:
            ops = await conn.fetch(
                "SELECT id, order_position FROM tasks WHERE course_id = $1 ORDER BY order_position", cid
            )
            seq = [r["order_position"] for r in ops]
            if seq != list(range(1, len(seq) + 1)):
                order_bad.append(f"  курс {cid}: order_position не 1..N (дубли или дыры)")
        if order_bad:
            print("ВЕРИФИКАЦИЯ ПРОВАЛЕНА (порядок):")
            print("\n".join(order_bad))
            return 3
        print(f"Верификация 3/3: order_position 1..N без дублей и дыр в подкурсах {touched}.")

        # Порядок действительно идёт по возрастанию сложности (то, ради чего реордер)
        mono_bad = []
        for cid in touched:
            diffs = [r["difficulty_id"] for r in await conn.fetch(
                "SELECT difficulty_id FROM tasks WHERE course_id = $1 ORDER BY order_position", cid
            )]
            if any(b < a for a, b in zip(diffs, diffs[1:])):
                mono_bad.append(f"  курс {cid}: difficulty_id не монотонен по order_position")
        if mono_bad:
            print("ВЕРИФИКАЦИЯ ПРОВАЛЕНА (сортировка по сложности):")
            print("\n".join(mono_bad))
            return 3
        print("Верификация 4/4: порядок заданий идёт по возрастанию сложности.")
        print("\nГотово.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
