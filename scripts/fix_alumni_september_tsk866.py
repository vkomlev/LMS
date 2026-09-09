"""tsk-866: погасить сентябрь двум выпускникам и закрыть их персональные цены.

**Что чиним.** Персональная цена пережила выпуск и начислила сентябрь без
единого занятия: Галимову Эмилю (4514) — 2 750 ₽, Мише Поскребышеву (4509) —
5 500 ₽. Оба переведены в «Выпускники» 09.09.2026, занятий в сентябре у них не
было ни по сетке, ни фактически. Правка кода (та же задача) считает такой месяц
нулём, но оба сентября уже `closed`, а закрытый месяц пересчёт не трогает —
это durable-инвариант контура. Поэтому адресно.

**Чем гасим.** `manual_minor = 0` — ручная сумма месяца. Не удалением строки:
удаление стёрло бы историю, а `calculated_minor` рядом остаётся видимым следом
(«расчёт дал 2 750, человек поставил 0»). Ровно так на этой базе уже погашены
август Галимова, август Омельченко и сентябрь Александровой с Гребневой. Ноль
совпадёт и с новым расчётом, поэтому будущий пересчёт не сдвинет дельту вперёд.

**Цены закрываем сроком** (`ends_on` = день выпуска), а не удаляем: запись —
след договорённости. Новый код делает это сам при выпуске; здесь тем же
способом приводятся в порядок трое уже выпущенных (4514, 4509 и Ястребцов 4536,
чья цена — такая же временная фиксация «перед выпуском»).

Действующих учеников с персональной ценой скрипт НЕ ТРОГАЕТ: у них цена
законна.

Протокол /db-check (режим записи):
  * dry-run по умолчанию — печатает текущее состояние и план;
  * перед записью каждая строка сверяется с ожидаемым состоянием (сумма, статус,
    отсутствие платежей и поправок, ноль фактических занятий) — разошлось,
    значит с момента разбора что-то изменилось, и вслепую не правим;
  * всё в одной транзакции, после записи — верификация выборкой, затем COMMIT.

Запуск (из корня LMS):
  python scripts/fix_alumni_september_tsk866.py
  DBCHECK_OK=1 python scripts/fix_alumni_september_tsk866.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk866.fix")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Месяц, который чиним. Константой, а не «текущим»: скрипт разовый и должен
#: делать ровно то, что вычитал человек, даже если запустить его в октябре.
PERIOD = date(2026, 9, 1)

#: Кого гасим: ученик → ожидаемое `calculated_minor` сентября. Сумма в ключе не
#: для красоты — по ней идёт сверка перед записью.
EXPECTED_CHARGES: Dict[int, int] = {
    4514: 275000,  # Галимов Эмиль Альбертович
    4509: 550000,  # Миша Поскребышев
}

#: Чьи персональные цены закрываем и каким днём — днём их выпуска.
CLOSE_OVERRIDES: Dict[int, date] = {
    4514: date(2026, 9, 9),
    4509: date(2026, 9, 9),
    # Елисей Ястребцов: примечание к цене говорило «перевод в выпускники 01.09»,
    # но подписка «Выпускник» у него началась 31.08 — берём факт из базы.
    4536: date(2026, 8, 31),
}


def prod_dsn() -> Dict[str, Any]:
    """Параметры подключения к боевой базе из `.mcp.json` (пароль не печатаем)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    parsed = urlparse(mcp["mcpServers"]["learn_prod_db"]["args"][-1])
    return dict(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=(parsed.path or "").lstrip("/"),
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
    )


def rub(minor: int) -> str:
    return f"{minor / 100:.2f} ₽"


def read_charges(cur) -> List[Dict[str, Any]]:
    """Строки сентября с полным разбором: сумма, оплаты, поправки, факт занятий."""
    cur.execute(
        """
        SELECT ch.id, ch.student_id, u.full_name, ch.group_id, ch.period, ch.status,
               ch.calculated_minor, ch.manual_minor, ch.frozen_total_minor,
               ch.expected_lessons,
               COALESCE((SELECT sum(a.amount_minor) FROM charge_adjustment a
                          WHERE a.student_id = ch.student_id AND a.group_id = ch.group_id
                            AND a.period = ch.period), 0) AS adjustments_minor,
               COALESCE((SELECT count(*) FROM student_payment p
                          WHERE p.student_id = ch.student_id AND p.group_id = ch.group_id
                            AND p.period = ch.period), 0) AS payments,
               (SELECT count(*) FROM lesson_occurrence_participant lop
                  JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
                 WHERE lop.student_id = ch.student_id
                   AND date_trunc('month', (lo.scheduled_at AT TIME ZONE 'Europe/Moscow'))::date
                       = ch.period) AS fact_lessons
          FROM student_monthly_charge ch
          JOIN users u ON u.id = ch.student_id
         WHERE ch.period = %(period)s AND ch.student_id = ANY(%(ids)s)
         ORDER BY ch.student_id
        """,
        {"period": PERIOD, "ids": list(EXPECTED_CHARGES)},
    )
    return [dict(r) for r in cur.fetchall()]


def read_overrides(cur) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT o.id, o.student_id, u.full_name, o.group_id, o.price_minor,
               o.ends_on, o.note
          FROM student_price_override o
          JOIN users u ON u.id = o.student_id
         WHERE o.student_id = ANY(%(ids)s)
         ORDER BY o.student_id
        """,
        {"ids": list(CLOSE_OVERRIDES)},
    )
    return [dict(r) for r in cur.fetchall()]


def check_charge(row: Dict[str, Any]) -> List[str]:
    """Расхождения строки с тем, что видел разбор. Пусто — можно править."""
    problems: List[str] = []
    expected = EXPECTED_CHARGES[row["student_id"]]
    if row["calculated_minor"] != expected:
        problems.append(
            f"расчёт {rub(row['calculated_minor'])} вместо ожидаемых {rub(expected)}"
        )
    if row["manual_minor"] is not None:
        problems.append(f"ручная сумма уже стоит: {rub(row['manual_minor'])}")
    if row["adjustments_minor"]:
        problems.append(f"есть поправки на {rub(row['adjustments_minor'])}")
    if row["payments"]:
        problems.append(f"есть платежи за месяц: {row['payments']} шт.")
    if row["fact_lessons"]:
        problems.append(
            f"занятия в месяце ВСЁ-ТАКИ были: {row['fact_lessons']} — это не наш случай"
        )
    if row["expected_lessons"]:
        problems.append(f"занятий по сетке {row['expected_lessons']}, а не ноль")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Погасить сентябрь выпускникам (tsk-866)")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    args = parser.parse_args()

    conn = psycopg2.connect(**prod_dsn())
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    charges = read_charges(cur)
    overrides = read_overrides(cur)

    logger.info("=== Начисления за %s ===", PERIOD)
    blocked = False
    for row in charges:
        problems = check_charge(row)
        logger.info(
            "  %s (%s): расчёт %s, ручная %s, поправки %s, платежей %s, занятий факт %s, "
            "по сетке %s, статус %s, снимок %s",
            row["full_name"], row["student_id"], rub(row["calculated_minor"]),
            "нет" if row["manual_minor"] is None else rub(row["manual_minor"]),
            rub(row["adjustments_minor"]), row["payments"], row["fact_lessons"],
            row["expected_lessons"], row["status"],
            "нет" if row["frozen_total_minor"] is None else rub(row["frozen_total_minor"]),
        )
        if problems:
            blocked = True
            for p in problems:
                logger.warning("     ! %s", p)
        else:
            logger.info("     → к оплате %s, станет 0.00 ₽", rub(row["calculated_minor"]))

    missing = set(EXPECTED_CHARGES) - {r["student_id"] for r in charges}
    if missing:
        blocked = True
        logger.warning("  ! не найдены строки сентября у: %s", sorted(missing))

    logger.info("=== Персональные цены ===")
    for row in overrides:
        target = CLOSE_OVERRIDES[row["student_id"]]
        state = "бессрочная" if row["ends_on"] is None else f"до {row['ends_on']}"
        action = (
            "уже закрыта не позже нужного"
            if row["ends_on"] is not None and row["ends_on"] <= target
            else f"закроем днём {target}"
        )
        logger.info(
            "  %s (%s): %s, %s → %s",
            row["full_name"], row["student_id"], rub(row["price_minor"]), state, action,
        )

    if blocked:
        logger.error(
            "СТОП: состояние базы разошлось с разбором. Ничего не записано — "
            "разберитесь с расхождением, потом запускайте снова."
        )
        conn.rollback()
        return 2

    if not args.apply:
        logger.info("Это dry-run. Записать: DBCHECK_OK=1 python %s --apply", Path(__file__).name)
        conn.rollback()
        return 0

    for row in charges:
        cur.execute(
            """
            UPDATE student_monthly_charge
               SET manual_minor = 0, updated_at = now()
             WHERE id = %(id)s AND manual_minor IS NULL
               AND calculated_minor = %(calc)s
            """,
            {"id": row["id"], "calc": row["calculated_minor"]},
        )
        if cur.rowcount != 1:
            logger.error("Строка %s не обновилась — откат", row["id"])
            conn.rollback()
            return 3
        # Снимок замороженного итога догоняет правку человека (tsk-756): иначе
        # страж сдвига сумм увидит расхождение там, где его нет. Строки без
        # снимка не трогаются — сентябрь текущий, снимок ему ещё не ставили.
        cur.execute(
            """
            UPDATE student_monthly_charge ch
               SET frozen_total_minor = COALESCE(ch.manual_minor, ch.calculated_minor)
                     + COALESCE((SELECT sum(a.amount_minor) FROM charge_adjustment a
                                  WHERE a.student_id = ch.student_id
                                    AND a.group_id = ch.group_id
                                    AND a.period = ch.period), 0),
                   frozen_at = now()
             WHERE ch.id = %(id)s AND ch.frozen_total_minor IS NOT NULL
            """,
            {"id": row["id"]},
        )

    for row in overrides:
        target = CLOSE_OVERRIDES[row["student_id"]]
        cur.execute(
            """
            UPDATE student_price_override
               SET ends_on = %(d)s, updated_at = now()
             WHERE id = %(id)s AND (ends_on IS NULL OR ends_on > %(d)s)
            """,
            {"id": row["id"], "d": target},
        )

    logger.info("=== Проверка после записи (до COMMIT) ===")
    for row in read_charges(cur):
        total = (row["manual_minor"] if row["manual_minor"] is not None
                 else row["calculated_minor"]) + row["adjustments_minor"]
        logger.info(
            "  %s (%s): ручная %s, к оплате %s",
            row["full_name"], row["student_id"],
            "нет" if row["manual_minor"] is None else rub(row["manual_minor"]),
            rub(total),
        )
        if total != 0:
            logger.error("Итог не ноль — откат")
            conn.rollback()
            return 4
    for row in read_overrides(cur):
        logger.info(
            "  цена %s (%s): %s, действует по %s",
            row["full_name"], row["student_id"], rub(row["price_minor"]), row["ends_on"],
        )
        if row["ends_on"] is None:
            logger.error("Цена осталась бессрочной — откат")
            conn.rollback()
            return 5

    conn.commit()
    logger.info("COMMIT выполнен.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
