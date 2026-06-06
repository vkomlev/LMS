# -*- coding: utf-8 -*-
"""tsk-109 итерация 2: материал 401 «Контрольные вопросы» (подкурс 148,
«Задание 2 ЕГЭ. Таблицы истинности») -> 10 авто-проверяемых заданий SC/MC/SA_COM
+ гашение материала.

Факты для верных ответов взяты из материалов 393-400 того же подкурса.
Размещение: серия в начало (order_position 1..10), старые 61 задание сдвигаются +10.
Материал 401 -> is_active=false. Одна транзакция, самопроверка, DRY-RUN / --apply.
"""
import io
import os
import re
import sys

import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 148
MATERIAL_ID = 401
COURSE_UID = "wp:zadanie-2-ege-po-informatike-tablitsy-istinnosti"
N = 10


def load_dsn() -> str:
    dsn = os.environ.get("LMS_DB_DSN")
    if dsn:
        return dsn
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    url = None
    with open(env_path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("DATABASE_URL"):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not url:
        raise RuntimeError("DATABASE_URL не найден в .env")
    url = re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)
    return url


# HTML таблицы для задания 10 (из материала 400)
FRAG_TABLE = (
    "<table><thead><tr>"
    "<th>П1</th><th>П2</th><th>П3</th><th>F</th>"
    "</tr></thead><tbody>"
    "<tr><td></td><td>1</td><td>1</td><td>0</td></tr>"
    "<tr><td></td><td></td><td>1</td><td>0</td></tr>"
    "</tbody></table>"
)


def opt(oid: str, text: str) -> dict:
    return {"id": oid, "text": text, "is_active": True, "explanation": ""}


def sc(stem, options, correct):
    return ("SC", stem, options, {"correct_options": correct, "short_answer": None})


def mc(stem, options, correct):
    return ("MC", stem, options, {"correct_options": correct, "short_answer": None})


def sa(stem, value):
    short = {
        "regex": None,
        "use_regex": False,
        "normalization": ["trim", "lower"],
        "accepted_answers": [{"score": 1, "value": value}],
    }
    return ("SA_COM", stem, None, {"correct_options": [], "short_answer": short})


SERIES = [
    # (difficulty_id, builder)
    (1, sc(
        "<p>Что такое логическое выражение и какие значения оно принимает?</p>",
        [opt("A", "Утверждение или комбинация утверждений, которые принимают "
                  "значение 0 (ложь) или 1 (истина)"),
         opt("B", "Любое числовое выражение с арифметическими операциями"),
         opt("C", "Выражение, всегда равное 1"),
         opt("D", "Строка символов без значения истинности")],
        ["A"])),
    (1, sc(
        "<p>Конъюнкция (A AND B, обозначается ∧ или *) равна 1 только если...</p>",
        [opt("A", "Оба операнда равны 1"),
         opt("B", "Хотя бы один операнд равен 1"),
         opt("C", "Операнды различны"),
         opt("D", "Оба операнда равны 0")],
        ["A"])),
    (1, sc(
        "<p>Дизъюнкция (A OR B, обозначается ∨ или +) равна 0 только если...</p>",
        [opt("A", "Оба операнда равны 0"),
         opt("B", "Хотя бы один операнд равен 0"),
         opt("C", "Операнды различны"),
         opt("D", "Оба операнда равны 1")],
        ["A"])),
    (1, sa(
        "<p>Чему равно значение импликации A → B при A=1, B=0? "
        "В ответе запишите целое число.</p>",
        "0")),
    (1, sc(
        "<p>Когда тождественное равенство (эквиваленция, ≡) равно 1?</p>",
        [opt("A", "Когда оба операнда равны между собой (оба 0 или оба 1)"),
         opt("B", "Когда оба операнда равны 1"),
         opt("C", "Когда операнды различны"),
         opt("D", "Когда хотя бы один операнд равен 1")],
        ["A"])),
    (1, sc(
        "<p>В каком порядке выполняются логические операции в выражении "
        "<strong>без скобок</strong>?</p>",
        [opt("A", "Сначала НЕ (NOT), затем И (AND), затем ИЛИ (OR), "
                  "в конце импликация и эквиваленция"),
         opt("B", "Сначала ИЛИ (OR), затем И (AND), затем НЕ (NOT)"),
         opt("C", "Все операции с одинаковым приоритетом, выполняются слева направо"),
         opt("D", "Сначала импликация, затем НЕ (NOT), затем И (AND)")],
        ["A"])),
    (2, mc(
        "<p>Какие операторы Python используются для логических операций? "
        "Выберите все верные.</p>",
        [opt("A", "<code>and</code>"),
         opt("B", "<code>or</code>"),
         opt("C", "<code>not</code>"),
         opt("D", "<code>imp</code>")],
        ["A", "B", "C"])),
    (2, sa(
        "<p>Сколько строк содержит полная таблица истинности для выражения "
        "с 3 переменными? В ответе запишите целое число.</p>",
        "8")),
    (2, sc(
        "<p>Как надёжно реализовать импликацию A → B в Python?</p>",
        [opt("A", "<code>not A or B</code>"),
         opt("B", "<code>A and B</code>"),
         opt("C", "<code>A or not B</code>"),
         opt("D", "<code>not (A and B)</code>")],
        ["A"])),
    (3, sa(
        "<p>Логическая функция F задаётся выражением (x ≡ y) ∨ ((y ∨ z) → x).</p>"
        "<p>Дан фрагмент, содержащий неповторяющиеся строки таблицы истинности "
        "функции F, в которых F = 0:</p>"
        + FRAG_TABLE +
        "<p>Определите, какому столбцу соответствует каждая из переменных x, y, z. "
        "Напишите буквы x, y, z в том порядке, в котором идут соответствующие им "
        "столбцы (без разделителей).</p>",
        "xzy")),
]


def build_rows():
    rows = []
    for i, (difficulty_id, (ttype, stem, options, extra)) in enumerate(SERIES, start=1):
        ext_uid = f"lms:tsk109:c148:{i:02d}"
        task_content = {
            "code": None,
            "stem": stem,
            "tags": None,
            "type": ttype,
            "media": None,
            "title": None,
            "prompt": None,
            "options": options,
            "has_hints": False,
            "course_uid": COURSE_UID,
            "hints_text": [],
            "hints_video": [],
            "difficulty_code": None,
        }
        solution_rules = {
            "max_score": 1,
            "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
            "auto_check": True,
            "text_answer": None,
            "scoring_mode": "all_or_nothing",
            "short_answer": extra["short_answer"],
            "partial_rules": [],
            "correct_options": extra["correct_options"],
            "custom_scoring_config": None,
            "manual_review_required": False,
        }
        rows.append((ext_uid, difficulty_id, i, task_content, solution_rules))
    return rows


def main() -> None:
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")

        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s", (COURSE_ID,))
        before_cnt = cur.fetchone()[0]
        cur.execute("SELECT is_active FROM materials WHERE id=%s", (MATERIAL_ID,))
        mat_before = cur.fetchone()[0]
        print(f"заданий в курсе {COURSE_ID} до: {before_cnt}; материал {MATERIAL_ID} "
              f"is_active до: {mat_before}")

        cur.execute(
            "UPDATE tasks SET order_position = order_position + %s WHERE course_id=%s",
            (N, COURSE_ID))
        shifted = cur.rowcount

        rows = build_rows()
        for ext_uid, diff_id, pos, tc, sr in rows:
            cur.execute(
                "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                "difficulty_id, solution_rules, max_attempts, time_limit_sec, "
                "order_position) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ext_uid, 1, Json(tc), COURSE_ID, diff_id, Json(sr), None, None, pos))

        cur.execute("UPDATE materials SET is_active=false WHERE id=%s", (MATERIAL_ID,))
        mat_upd = cur.rowcount

        # --- Самопроверка ---
        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s", (COURSE_ID,))
        after_cnt = cur.fetchone()[0]
        cur.execute(
            "SELECT min(order_position), max(order_position), count(*), "
            "count(DISTINCT order_position) FROM tasks WHERE course_id=%s",
            (COURSE_ID,))
        pmin, pmax, pcnt, pdistinct = cur.fetchone()
        cur.execute(
            "SELECT count(*) FROM tasks WHERE course_id=%s "
            "AND external_uid LIKE 'lms:tsk109:c148:%%'", (COURSE_ID,))
        series_cnt = cur.fetchone()[0]
        cur.execute(
            "SELECT order_position, task_content->>'type', "
            "task_content->>'stem' FROM tasks "
            "WHERE external_uid LIKE 'lms:tsk109:c148:%%' ORDER BY order_position")
        preview = cur.fetchall()
        cur.execute("SELECT is_active FROM materials WHERE id=%s", (MATERIAL_ID,))
        mat_after = cur.fetchone()[0]

        print(f"сдвинуто заданий: {shifted}; вставлено серии: {series_cnt}")
        print(f"заданий в курсе после: {after_cnt}")
        print(f"order_position: min={pmin} max={pmax} count={pcnt} distinct={pdistinct}")
        print(f"материал {MATERIAL_ID} is_active после: {mat_after} (строк {mat_upd})")
        print("--- серия (позиция / тип / начало stem) ---")
        for pos, typ, stem in preview:
            print(f"  {pos:>2}  {typ:<7} {stem[:72]}")

        checks = {
            "вставлено ровно N": series_cnt == N,
            "итог = было + N": after_cnt == before_cnt + N,
            "позиции непрерывны 1..count": (pmin == 1 and pmax == pcnt
                                            and pdistinct == pcnt),
            "серия на позициях 1..N": [p[0] for p in preview] == list(range(1, N + 1)),
            "материал погашен": mat_after is False and mat_upd == 1,
            "сдвинуты все старые": shifted == before_cnt,
        }
        print("--- проверки ---")
        for name, ok in checks.items():
            print(f"  [{'OK' if ok else 'FAIL'}] {name}")

        if all(checks.values()) and apply:
            conn.commit()
            print("РЕЗУЛЬТАТ: проверки пройдены, COMMIT.")
        elif all(checks.values()):
            conn.rollback()
            print("РЕЗУЛЬТАТ: проверки пройдены (DRY-RUN), ROLLBACK. "
                  "Запусти с --apply для записи.")
        else:
            conn.rollback()
            print("РЕЗУЛЬТАТ: проверки НЕ пройдены, ROLLBACK.")
            sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"ОШИБКА: {exc!r}. ROLLBACK.")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
