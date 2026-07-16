"""Регулярный чек: порядок разделов курса совпадает с их номерами (tsk-237).

Зачем. `course_parents.order_number` присваивается по мере публикации: INSERT без
явного order_number → триггер `set_course_parent_order_number` ставит max+1. Поэтому
раздел, опубликованный позже, встаёт в конец, и ученик видит разделы вразнобой.
Ловилось только глазами оператора по скриншоту (Трек 1: 1,2,3,5,6,4,8,9,10,7;
Трек 3: 1,2,3,4,6,5; чат-боты: 0,2,1,5,6,7,8,9,10,3,4).

Что делает. Для каждого курса-родителя, у детей которого в course_uid есть номер темы
(`tema-<N>`), сравнивает фактический порядок (по order_number) с порядком по N.
Read-only: ни одного UPDATE.

Границы (важно). Родитель проверяется, только если у ВСЕХ его детей номер есть и в
`course_uid`, и в названии, и они СОВПАДАЮТ. Иначе uid не отражает позицию в этом родителе
и сравнивать нечего. Так отсеиваются переиспользованные курсы: у `oge-z16` дети — курсы
Python-подростков, привязанные к заданию, и там `python-podrostki-tema-6-cikly` называется
«Раздел 7. Циклы» (6 ≠ 7).

Известный размен: тем же правилом пропускается и `python-podrostki-11-14` — у него та же
рассинхронизация uid и названия (сдвиг на единицу), хотя разделы там настоящие. Сознательно:
ложное обвинение хуже пропуска — оно толкает «чинить» порядок там, где он осмыслен.
Если нумерацию курса приведут в порядок, он начнёт проверяться сам.

Куда смотрит. В базу из `DATABASE_URL` — по умолчанию это dev (`localhost`), прод от
скриптов закрыт (tsk-246). Для проверки ПРОДА — запускать с явным override, например:
    DATABASE_URL=<прод-dsn> python scripts/check_section_order.py
Скрипт всегда печатает хост и базу, которую проверил: dev и прод расходятся, и перепутать
их легко (в dev у курса 1064 21 ребёнок, на проде — 10).

Запуск из корня проекта:
    python scripts/check_section_order.py            # отчёт
    python scripts/check_section_order.py --quiet    # только проблемы (для планировщика)

Коды выхода: 0 — порядок везде верный; 1 — найдены курсы с нарушенным порядком;
2 — ошибка выполнения. Чинит порядок отдельный скрипт (см. tsk-237), этот только сообщает.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

_NUM_RE = re.compile(r"tema-(\d+)")
# номер в названии ребёнка: «Раздел 3. …», «Тема 0. …», «Трек 1. Раздел 3. …»
_TITLE_NUM_RE = re.compile(r"(?:Раздел|Тема)\s+(\d+)")

_SQL = """
    SELECT cp.parent_course_id,
           p.course_uid  AS parent_uid,
           p.title       AS parent_title,
           c.course_uid  AS child_uid,
           c.title       AS child_title,
           cp.order_number
    FROM course_parents cp
    JOIN courses c ON c.id = cp.course_id
    JOIN courses p ON p.id = cp.parent_course_id
    ORDER BY cp.parent_course_id, cp.order_number
"""


async def main() -> int:
    ap = argparse.ArgumentParser(description="Чек порядка разделов курсов (read-only)")
    ap.add_argument("--quiet", action="store_true", help="печатать только нарушения")
    args = ap.parse_args()

    from sqlalchemy import text

    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        rows = (await session.execute(text(_SQL))).all()
        target = (await session.execute(
            text("SELECT current_setting('server_version'), inet_server_addr()::text, current_database()")
        )).first()

    host = target[1] or "localhost"
    print(f"База: {host} / {target[2]}  (dev и прод расходятся — сверяйся с хостом)\n")

    # группируем детей по родителю, сохраняя порядок по order_number
    parents: dict[int, dict] = {}
    for parent_id, parent_uid, parent_title, child_uid, child_title, _order in rows:
        p = parents.setdefault(
            parent_id, {"uid": parent_uid, "title": parent_title, "nums": [], "skip": False}
        )
        m = _NUM_RE.search(child_uid or "")
        if not m:
            # ребёнок без номера темы в uid → у родителя другая семантика
            p["skip"] = True
            continue
        n_uid = int(m.group(1))
        mt = _TITLE_NUM_RE.search(child_title or "")
        if mt and int(mt.group(1)) != n_uid:
            # uid говорит одно, название другое (python-podrostki-tema-6-cikly = «Раздел 7»)
            # → это переиспользованный курс, номер из uid не означает позицию здесь
            p["skip"] = True
            continue
        p["nums"].append(n_uid)

    checked = broken = skipped = 0
    problems: list[str] = []

    for parent_id, p in sorted(parents.items()):
        if p["skip"] or len(p["nums"]) < 2:
            skipped += 1
            continue
        checked += 1
        actual = p["nums"]
        if actual != sorted(actual):
            broken += 1
            problems.append(
                f"  [НАРУШЕН] {p['uid']} (id={parent_id})\n"
                f"      сейчас: {actual}\n"
                f"      нужно:  {sorted(actual)}\n"
                f"      {p['title']}"
            )
        elif not args.quiet:
            print(f"  [ок] {p['uid']}: {actual}")

    if problems:
        print("\nПорядок разделов нарушен — ученик видит разделы вразнобой:\n")
        print("\n".join(problems))
        print(
            f"\nИтог: проверено {checked}, нарушено {broken}, пропущено {skipped}"
            " (другая семантика или один раздел)."
        )
        print(
            "Причина обычно одна: раздел опубликован позже остальных и встал в конец "
            "(order_number = max+1). Как чинить — tsk-237 в трекере."
        )
        return 1

    if not args.quiet:
        print(f"\nИтог: проверено {checked}, нарушений нет, пропущено {skipped}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — вход в планировщик, нужен внятный код выхода
        print(f"ОШИБКА чека порядка разделов: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
