# -*- coding: utf-8 -*-
"""Переформулировка трёх вспомогательных заданий курса 156 под авто-проверку (tsk-779).

Зачем. Еженедельный чек `check_ungradable_tasks.py` месяц показывал 13 «непроверяемых»
активных заданий. Разбор (tsk-779) свёл их к трём настоящим: 3240 (5_1), 3451 (5_6),
3450 (5_8) — курс 156 «Задание 5 ЕГЭ». У них тип `SA_COM` и НЕТ эталона, поэтому сдача
получала оптимистичный зачёт (`app/api/v1/attempts.py`, ветка 2.3d): засчитывался любой
ответ, включая пустой, а работа попадала лишь в необязательную очередь преподавателя.

Почему переформулировка, а не ручная проверка. Эталона у них не было не по недосмотру:
условие не задавало входные данные («Дано трёхзначное число N» — самого N нет), а у 5_1
ответ — 109 пар, в поле краткого ответа не вводится. Решение оператора — не вводить
ручную проверку, а дать заданиям однозначный короткий ответ. Образец взят у соседей по
той же серии: 5_2/5_3 (4818/4819) задают конкретные числа и имеют эталон.

Что меняется у каждого задания: текст условия (`task_content.stem`) и эталон
(`solution_rules.short_answer`). Тип `SA_COM`, сложность, баллы и порядок не трогаются —
ученик по-прежнему пишет программу, меняется только то, что он выводит в ответ.

Две ловушки, найденные двойной проверкой ДО записи (обе учтены в формулировках):
  * 5_1 — «сколько пар» без уточнения читается двояко: пар (M, N) 109, а уникальных
    значений N всего 45. В условии теперь явно «пар (M, N)».
  * 5_8 — если цифру исходного числа разрешить брать дважды, разность выходит 49
    вместо 45. В условии теперь явная оговорка про однократное использование.
Границы диапазонов у 5_1 неоднозначности НЕ дают: и при включительном чтении, и при
полуинтервале получается 109 пар — проверено перебором обоих вариантов.

`normalization` — `["trim", "lower"]`, как у остальных заданий этого курса с эталоном
(3106, 3289, 4555, 4556). `strip_punctuation` намеренно НЕ добавлен: ответ здесь —
одно число, и этот шаг превратил бы «10.9» в «109», то есть зачёл бы неверный ответ
(класс дефекта из tsk-772, где «очевидная» правка правила засчитала «2 5» за «2.5»).

Старые вердикты не пересчитываются: 42 строки `task_results` по этим заданиям остаются
как есть. Это сознательно — ученики отвечали на ПРЕЖНЮЮ формулировку, и снимать у них
зачёт задним числом было бы неверно.

Запуск из корня проекта (по умолчанию — только показать план, ничего не менять):
    python scripts/tsk779_restate_aux_tasks_156.py
    DBCHECK_OK=1 python scripts/tsk779_restate_aux_tasks_156.py --apply

Снимок «как было» пишется в `reviews/tsk779-restate-156-snapshot.json` ПЕРЕД записью и
повторным прогоном не затирается (урок tsk-772: второй прогон затёр снимок отката и
откатываться стало не к чему). Откат — `--rollback` из того же снимка.

Коды выхода: 0 — успех либо план показан; 1 — расхождение с ожидаемым состоянием; 2 — ошибка.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

SNAPSHOT = project_root / "reviews" / "tsk779-restate-156-snapshot.json"

# id → (новый stem, эталонный ответ, короткая пометка для отчёта)
CHANGES: dict[int, dict[str, str]] = {
    3240: {
        "stem": (
            "<p>Вспомогательное задание 5_1.</p>\n"
            "<p>Даны два диапазона чисел: M [50:100] и N [1000:10000]. "
            "Найдите все пары чисел, где N кратно M и N кратно 143.</p>\n"
            "<p>В ответе укажите, <strong>сколько всего пар (M, N)</strong> получилось.</p>"
        ),
        "answer": "109",
        "note": "было «выведите все пары» (109 пар — в поле не ввести) → количество пар",
    },
    3451: {
        "stem": (
            "<p>Вспомогательное задание 5_6</p>\n"
            "<p>Дано восьмибитное двоичное представление числа N. Замените цифры на "
            "противоположные (вместо 0 – 1, вместо 1 - 0). Выведите результат в "
            "десятичном виде.</p>\n"
            "<p><strong>Например.</strong><br>Дано число N&nbsp;=&nbsp;13.<br>"
            "Восьмибитная двоичная запись числа N: <code>00001101</code>.<br>"
            "Все цифры заменяются на противоположные, новая запись: <code>11110010</code>.<br>"
            "Десятичное значение полученного числа: <strong>242</strong>.</p>\n"
            "<p>Выполните преобразование для <strong>N&nbsp;=&nbsp;200</strong>. "
            "В ответе укажите полученное десятичное число.</p>"
        ),
        "answer": "55",
        "note": "было «дано число N» без самого N → задано N = 200 (пример N = 13 остался образцом)",
    },
    3450: {
        "stem": (
            "<p>Вспомогательное задание 5_8</p>\n"
            "<p>Дано трехзначное число N. Из цифр, образующих десятичную запись N, "
            "постройте наибольшее и наименьшее возможные двузначные числа. Каждую цифру "
            "исходного числа можно использовать не более одного раза; двузначное число "
            "не может начинаться с нуля.</p>\n"
            "<p>Выполните это для <strong>N&nbsp;=&nbsp;905</strong>. В ответе укажите "
            "<strong>разность</strong> наибольшего и наименьшего из полученных чисел.</p>"
        ),
        "answer": "45",
        "note": "было «дано число N» без самого N → задано N = 905, ответ одним числом (95 − 50)",
    },
}

SHORT_ANSWER_TEMPLATE: dict[str, Any] = {
    "regex": None,
    "use_regex": False,
    "normalization": ["trim", "lower"],
    "accepted_answers": [],
}

SQL_READ = """
SELECT t.id, t.course_id, t.is_active,
       t.task_content->>'type' AS task_type,
       t.task_content->>'stem' AS stem,
       t.solution_rules->'short_answer' AS short_answer
FROM tasks t WHERE t.id = ANY(:ids) ORDER BY t.id
"""

SQL_WRITE = """
UPDATE tasks
   SET task_content = jsonb_set(task_content, '{stem}', to_jsonb(CAST(:stem AS text)), true),
       solution_rules = jsonb_set(solution_rules, '{short_answer}', CAST(:short_answer AS jsonb), true)
 WHERE id = :task_id
"""


def _expected_short_answer(value: str) -> dict[str, Any]:
    """Блок `short_answer` с единственным принимаемым ответом.

    :param value: эталонный ответ (одно число строкой).
    :returns: готовый к записи объект правил короткого ответа.
    """
    rules = dict(SHORT_ANSWER_TEMPLATE)
    rules["accepted_answers"] = [{"score": 1, "value": value}]
    return rules


async def main(apply: bool, rollback: bool) -> int:
    from sqlalchemy import bindparam, text
    from sqlalchemy.ext.asyncio import create_async_engine

    sys.path.insert(0, str(project_root / "scripts"))
    from weekly_checks import prod_dsn  # noqa: E402 — общий источник боевого DSN

    ids = sorted(CHANGES)
    engine = create_async_engine(prod_dsn(), echo=False)
    read_stmt = text(SQL_READ).bindparams(bindparam("ids", expanding=False))

    try:
        async with engine.connect() as conn:
            where = (await conn.execute(text(
                "SELECT current_database() AS db, inet_server_addr()::text AS host"
            ))).mappings().first()
            print(f"База: {where['db']} на {where['host'] or 'localhost'}")
            before = (await conn.execute(read_stmt, {"ids": ids})).mappings().all()

        if len(before) != len(ids):
            found = {r["id"] for r in before}
            print(f"ОШИБКА: не найдены задания {sorted(set(ids) - found)}", file=sys.stderr)
            return 1

        # Гейт состояния: скрипт рассчитан на задания БЕЗ эталона. Если эталон уже
        # появился (кто-то завёл руками, прогон повторный) — не перезаписывать молча.
        already: list[int] = []
        for row in before:
            existing = row["short_answer"] or {}
            if isinstance(existing, dict) and existing.get("accepted_answers"):
                already.append(row["id"])
        if already and not rollback:
            print(
                f"\nВНИМАНИЕ: у заданий {already} эталон уже задан — правка не нужна "
                f"или уже применена. Ничего не меняю.",
                file=sys.stderr,
            )
            return 1

        if rollback:
            if not SNAPSHOT.exists():
                print(f"ОШИБКА: нет снимка {SNAPSHOT}", file=sys.stderr)
                return 2
            snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
            print(f"\nОТКАТ по снимку {SNAPSHOT} ({len(snapshot)} заданий)")
            async with engine.begin() as conn:
                for item in snapshot:
                    await conn.execute(text(SQL_WRITE), {
                        "task_id": item["id"],
                        "stem": item["stem"],
                        "short_answer": json.dumps(item["short_answer"], ensure_ascii=False),
                    })
            print("Откат применён.")
            return 0

        print(f"\nПЛАН ПРАВКИ ({len(ids)} заданий, курс 156)")
        for row in before:
            change = CHANGES[row["id"]]
            print(f"\n─── задание {row['id']} ({row['task_type']}, активно={row['is_active']})")
            print(f"    что меняем: {change['note']}")
            print(f"    БЫЛО эталон: {row['short_answer']}")
            print(f"    СТАЛО эталон: {_expected_short_answer(change['answer'])}")
            print(f"    БЫЛО условие: {row['stem'][:150]}…")
            print(f"    СТАЛО условие: {change['stem'][:150]}…")

        if not apply:
            print(
                "\nЭто только план (--apply не задан). Ничего не изменено.\n"
                "Применить: DBCHECK_OK=1 python scripts/tsk779_restate_aux_tasks_156.py --apply"
            )
            return 0

        # Снимок ПЕРЕД записью и только один раз (tsk-772: повторный прогон затирал откат).
        if SNAPSHOT.exists():
            print(f"\nСнимок {SNAPSHOT} уже существует — не перезаписываю (откат сохранён).")
        else:
            SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
            SNAPSHOT.write_text(
                json.dumps(
                    [{"id": r["id"], "stem": r["stem"], "short_answer": r["short_answer"]}
                     for r in before],
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
            print(f"\nСнимок для отката: {SNAPSHOT}")

        async with engine.begin() as conn:
            for task_id, change in CHANGES.items():
                await conn.execute(text(SQL_WRITE), {
                    "task_id": task_id,
                    "stem": change["stem"],
                    "short_answer": json.dumps(
                        _expected_short_answer(change["answer"]), ensure_ascii=False
                    ),
                })

        # Верификация ПОСЛЕ коммита, отдельным соединением и поштучно:
        # агрегат «обновлено 3 строки» о содержимом не говорит (урок tsk-602).
        ok = True
        async with engine.connect() as conn:
            after = (await conn.execute(read_stmt, {"ids": ids})).mappings().all()
        print("\nПРОВЕРКА ПОСЛЕ ЗАПИСИ")
        for row in after:
            change = CHANGES[row["id"]]
            stem_ok = row["stem"] == change["stem"]
            rules_ok = row["short_answer"] == _expected_short_answer(change["answer"])
            ok = ok and stem_ok and rules_ok
            mark = "OK " if (stem_ok and rules_ok) else "НЕТ"
            print(f"  {mark} задание {row['id']}: условие={'совпало' if stem_ok else 'РАСХОЖДЕНИЕ'}, "
                  f"эталон={'совпал' if rules_ok else 'РАСХОЖДЕНИЕ'} "
                  f"(ответ {row['short_answer'].get('accepted_answers') if row['short_answer'] else None})")
        return 0 if ok else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="применить правку (иначе только план)")
    ap.add_argument("--rollback", action="store_true", help="вернуть состояние из снимка")
    args = ap.parse_args()
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        os.system("chcp 65001 >nul 2>&1")
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(asyncio.run(main(apply=args.apply, rollback=args.rollback)))
    except Exception as exc:  # noqa: BLE001 — причина обязана попасть в вывод оператору
        print(f"ОШИБКА выполнения: {exc}", file=sys.stderr)
        sys.exit(2)
