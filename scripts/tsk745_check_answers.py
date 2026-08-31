# scripts/tsk745_check_answers.py
"""
tsk-745: проверка приёма ответов онбординга реальной функцией сверки LMS.

Живой клик по варианту ответа `live-browse.mjs` не умеет (ищет только button /
link / tab, роль radio не поддерживает), поэтому пройти задания мышью нельзя.
Но главный риск не в клике: он в том, что верный ответ ученика будет отвергнут
из-за падежа, «ё» или регистра. Это проверяется прямо — тем же кодом, что судит
живую сдачу (`checking_service._matches_short_answer`), а не догадкой по SQL.

Скрипт read-only: ничего не пишет, попыток не создаёт.

Запуск на боевом сервере:
    sudo -u app /opt/lms/venv/bin/python /tmp/tsk745/tsk745_check_answers.py
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys

sys.path.insert(0, "/opt/lms")

from app.services.checking_service import CheckingService  # noqa: E402

#: Корень проверяемого курса. Аргументом, а не константой: курсов-онбордингов
#: уже два, и проверять надо оба одним и тем же кодом.
DEFAULT_ROOT_UID = "lms:onboarding:platforma"

#: Что реально напечатает ученик. Не «правильный ответ из плана», а варианты,
#: которые пишет живой человек: другой падеж, «е» вместо «ё», заглавная буква,
#: слово с точкой. Все они обязаны приниматься.
PROBES = {
    "platforma/kabinet-03-sa": ["сообщения", "Сообщения", "сообщение", " сообщения ", "СООБЩЕНИЯ"],
    "platforma/zadaniya-01-sa": ["отправить ответ", "Отправить ответ", "отправить", "«Отправить ответ»"],
    "platforma/zadaniya-02-sa": ["3", "три", " 3 "],
    "platforma/zanyatiya-01-sa": ["занятия", "Занятия", "мои занятия", "занятие"],
    "platforma/doma-02-sa": ["прогресс", "Прогресс", "мой прогресс", "прогресса"],
    "platforma/dengi-01-sa": ["оплата", "Оплата", "оплату", "оплаты"],
    # курс ЕГЭ: ответы числовые, ученик пишет и цифрой, и словом
    "ege/ekzamen-01-sa": ["27", " 27 ", "двадцать семь"],
    "ege/ekzamen-03-sa": ["29", "двадцать девять"],
    "ege/bally-01-sa": ["6", "шесть"],
    "ege/bally-02-sa": ["70", "семьдесят"],
    "ege/instrumenty-02-sa": ["18", "восемнадцать"],
    "ege/etapy-02-sa": ["4", "четыре"],
    # курс ОГЭ
    "oge/ekzamen-01-sa": ["16", "шестнадцать"],
    "oge/ekzamen-02-sa": ["21", "двадцать один"],
    "oge/ocenki-01-sa": ["5", "пять"],
    "oge/ocenki-02-sa": ["17", "семнадцать"],
    "oge/zadaniya-02-sa": ["14", "четырнадцать", "задание 14"],
    "oge/praktika-01-sa": ["9", "девять"],
}

#: Ответы, которые приниматься НЕ должны: иначе задание не различает знание.
NEGATIVE = {
    "platforma/kabinet-03-sa": ["курсы", "профиль"],
    "platforma/zadaniya-01-sa": ["отправить на проверку", "сдать"],
    "platforma/zadaniya-02-sa": ["1", "5"],
    "platforma/zanyatiya-01-sa": ["расписание", "курсы"],
    "platforma/doma-02-sa": ["история", "курсы"],
    "platforma/dengi-01-sa": ["тариф", "платежи"],
    "ege/ekzamen-01-sa": ["29", "25"],
    "ege/ekzamen-03-sa": ["27", "100"],
    "ege/bally-01-sa": ["40", "5"],
    "ege/bally-02-sa": ["17", "72"],
    "ege/instrumenty-02-sa": ["27", "8"],
    "ege/etapy-02-sa": ["3", "5"],
    "oge/ekzamen-01-sa": ["27", "21"],
    "oge/ekzamen-02-sa": ["16", "29"],
    "oge/ocenki-01-sa": ["11", "17"],
    "oge/ocenki-02-sa": ["11", "21"],
    "oge/zadaniya-02-sa": ["13", "16"],
    "oge/praktika-01-sa": ["12", "21"],
}


async def main() -> None:
    root_uid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROOT_UID
    print(f"курс: {root_uid}")
    dsn = ""
    for line in pathlib.Path("/opt/lms/.env").read_text(encoding="utf-8-sig").splitlines():
        if line.strip().startswith("DATABASE_URL="):
            dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://").replace("+psycopg2", "")

    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT t.external_uid, t.task_content, t.solution_rules
              FROM tasks t
              JOIN course_parents cp ON cp.course_id = t.course_id
              JOIN courses root ON root.id = cp.parent_course_id
             WHERE root.course_uid = $1 AND t.is_active
             ORDER BY t.external_uid
            """,
            root_uid,
        )
    finally:
        await conn.close()

    print(f"заданий в курсе: {len(rows)}")
    problems = []
    checked_sa = 0

    for r in rows:
        uid = r["external_uid"]
        tc = json.loads(r["task_content"]) if isinstance(r["task_content"], str) else r["task_content"]
        sr = json.loads(r["solution_rules"]) if isinstance(r["solution_rules"], str) else r["solution_rules"]
        short = sr.get("short_answer")
        key = f"{root_uid.rsplit(chr(58), 1)[-1]}/{uid.rsplit(chr(58), 1)[-1]}"

        if tc["type"] in ("SC", "MC"):
            option_ids = {o["id"] for o in (tc.get("options") or [])}
            correct = set(sr.get("correct_options") or [])
            if not correct:
                problems.append(f"{uid}: нет правильных вариантов")
            elif not correct <= option_ids:
                problems.append(f"{uid}: correct_options {correct - option_ids} нет среди вариантов")
            continue

        if tc["type"] != "SA":
            continue
        checked_sa += 1
        if not short or not short.get("accepted_answers"):
            problems.append(f"{uid}: у SA нет принимаемых ответов")
            continue
        steps = short.get("normalization") or []
        accepted = [a["value"] for a in short["accepted_answers"]]

        for probe in PROBES.get(key, []):
            ok = any(CheckingService._matches_short_answer(probe, a, steps) for a in accepted)
            if not ok:
                problems.append(f"{uid}: ОТВЕРГНУТ верный ответ ученика {probe!r}")
        for probe in NEGATIVE.get(key, []):
            ok = any(CheckingService._matches_short_answer(probe, a, steps) for a in accepted)
            if ok:
                problems.append(f"{uid}: ПРИНЯТ неверный ответ {probe!r}")

    print(f"проверено SA: {checked_sa}, из них с пробами: {len(PROBES)}")
    if problems:
        print(f"\nНАХОДКИ ({len(problems)}):")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("\nВсе задания принимают правдоподобные ответы ученика и отвергают неверные.")


if __name__ == "__main__":
    asyncio.run(main())
