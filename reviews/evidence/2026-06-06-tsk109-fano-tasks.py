# -*- coding: utf-8 -*-
"""tsk-109 итерация 4: материалы 418 и 419 (курс 155,
«Задание 4 ЕГЭ. Неравномерное кодирование и условие Фано») ->
10 авто-проверяемых заданий SC/MC/SA_COM + гашение обоих материалов.

Ответы основаны строго на материалах 415 (теория), 416 (П=100), 417 (14 кодов), 419 (задания).
"""
import io, os, re, sys
import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID     = 155
MATERIAL_IDS  = [418, 419]   # оба гасятся
COURSE_UID    = "wp:zadanie-4-ege-po-informatike-neravnomernoe-kodirovanie-i-uslovie-fano"
N             = 10


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
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)


def opt(oid, text):
    return {"id": oid, "text": text, "is_active": True, "explanation": ""}

def sc(stem, options, correct):
    return ("SC", stem, options, {"correct_options": correct, "short_answer": None})

def mc(stem, options, correct):
    return ("MC", stem, options, {"correct_options": correct, "short_answer": None})

def sa(stem, value, extras=None):
    accepted = [{"score": 1, "value": value}]
    for v in (extras or []):
        accepted.append({"score": 1, "value": v})
    short = {"regex": None, "use_regex": False,
             "normalization": ["trim", "lower"],
             "accepted_answers": accepted}
    return ("SA_COM", stem, None, {"correct_options": [], "short_answer": short})


SERIES = [
    # (difficulty_id, builder)

    # 1 — определение условия Фано (материал 415)
    (1, sc(
        "<p>Что означает <strong>условие Фано</strong> для двоичного кода?</p>",
        [opt("A", "Никакое кодовое слово не является началом (префиксом) другого кодового слова"),
         opt("B", "Все коды имеют одинаковую длину"),
         opt("C", "Код каждого символа содержит не более 4 бит"),
         opt("D", "Количество кодируемых символов равно степени двойки")],
        ["A"])),

    # 2 — достаточность, но не обязательность (материал 415)
    (1, sc(
        "<p>Условие Фано для однозначного декодирования является…</p>",
        [opt("A", "Достаточным, но не обязательным условием"),
         opt("B", "Необходимым и достаточным условием"),
         opt("C", "Только необходимым условием"),
         opt("D", "Условием, не связанным с однозначностью декодирования")],
        ["A"])),

    # 3 — А=1, Б=10: нарушение Фано (материал 419, вопрос 3)
    (2, sc(
        "<p>Даны коды двух символов: А&nbsp;=&nbsp;1, Б&nbsp;=&nbsp;10. "
        "Нарушается ли условие Фано?</p>",
        [opt("A", "Да, код А=1 является началом кода Б=10"),
         opt("B", "Нет, коды разной длины — это всегда допустимо"),
         opt("C", "Нет, нарушение возникает только при совпадении кодов"),
         opt("D", "Нет, условие Фано не применяется к алфавиту из двух символов")],
        ["A"])),

    # 4 — X=0, Y=10, Z=101: нарушение (материал 419, задание 7)
    (2, sc(
        "<p>Даны коды: X&nbsp;=&nbsp;0, Y&nbsp;=&nbsp;10, Z&nbsp;=&nbsp;101. "
        "Нарушается ли условие Фано и почему?</p>",
        [opt("A", "Да, код Y=10 является началом кода Z=101"),
         opt("B", "Нет, все коды различаются по длине"),
         opt("C", "Да, код X=0 является началом кода Z=101"),
         opt("D", "Нет, условие Фано проверяется только для кодов одинаковой длины")],
        ["A"])),

    # 5 — MC: выбрать все корректные наборы (материалы 415, 419)
    (2, mc(
        "<p>Выберите <strong>все</strong> наборы кодов, удовлетворяющие условию Фано:</p>",
        [opt("A", "{0, 10, 11}"),
         opt("B", "{0, 1, 10}"),
         opt("C", "{00, 01, 10, 11}"),
         opt("D", "{0, 01}")],
        ["A", "C"])),

    # 6 — расшифровка 0110110 при A=0,B=10,C=11 (материал 419, задание 8)
    (2, sa(
        "<p>Алфавит: A&nbsp;=&nbsp;0, B&nbsp;=&nbsp;10, C&nbsp;=&nbsp;11.<br>"
        "Расшифруйте сообщение: <code>0110110</code><br>"
        "Введите ответ заглавными латинскими буквами, без пробелов.</p>",
        "acaca", ["ACACA"])),

    # 7 — расшифровка 10110111 при А=0,Б=10,В=110,Г=111 (материал 419, задание 6)
    (2, sa(
        "<p>Алфавит: А&nbsp;=&nbsp;0, Б&nbsp;=&nbsp;10, В&nbsp;=&nbsp;110, Г&nbsp;=&nbsp;111.<br>"
        "Расшифруйте сообщение: <code>10110111</code><br>"
        "Введите ответ заглавными буквами, без пробелов.</p>",
        "бвг", ["БВГ"])),

    # 8 — расшифровка 0010110 при А=0,Б=10,В=110,Г=111 (материал 418)
    (2, sa(
        "<p>Алфавит: А&nbsp;=&nbsp;0, Б&nbsp;=&nbsp;10, В&nbsp;=&nbsp;110, Г&nbsp;=&nbsp;111.<br>"
        "Расшифруйте сообщение: <code>0010110</code><br>"
        "Введите ответ заглавными буквами, без пробелов.</p>",
        "аабв", ["ААБВ"])),

    # 9 — кратчайший код для П при Л=00, М=01, Н=11 (материал 416)
    (3, sa(
        "<p>Для кодирования букв Л, М, Н, П используется неравномерный двоичный код "
        "(условие Фано).<br>"
        "Известно: Л&nbsp;=&nbsp;00, М&nbsp;=&nbsp;01, Н&nbsp;=&nbsp;11.<br>"
        "Найдите <strong>кратчайший</strong> допустимый код для буквы <strong>П</strong>. "
        "Если вариантов несколько — выберите с наименьшим числовым значением.<br>"
        "Введите код цифрами, без пробелов.</p>",
        "100")),

    # 10 — 14 кодов (материал 417)
    (3, sa(
        "<p>Все заглавные буквы закодированы неравномерным двоичным кодом (условие Фано).<br>"
        "Уже заняты коды: И&nbsp;=&nbsp;110, Н&nbsp;=&nbsp;011, Ф&nbsp;=&nbsp;00, "
        "О&nbsp;=&nbsp;1111, Р&nbsp;=&nbsp;11100, М&nbsp;=&nbsp;11101, "
        "А&nbsp;=&nbsp;1001, Т&nbsp;=&nbsp;101, К&nbsp;=&nbsp;1000.<br>"
        "Сколько существует допустимых кодов длиной ≤&nbsp;6 бит для новой буквы <strong>Ю</strong>, "
        "чтобы условие Фано сохранялось? Введите целое число.</p>",
        "14")),
]


def build_rows():
    rows = []
    for i, (diff_id, (ttype, stem, options, extra)) in enumerate(SERIES, start=1):
        ext_uid = f"lms:tsk109:c155:{i:02d}"
        task_content = {
            "code": None, "stem": stem, "tags": None,
            "type": ttype, "media": None, "title": None, "prompt": None,
            "options": options, "has_hints": False, "course_uid": COURSE_UID,
            "hints_text": [], "hints_video": [], "difficulty_code": None,
        }
        solution_rules = {
            "max_score": 1,
            "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
            "auto_check": True, "text_answer": None,
            "scoring_mode": "all_or_nothing",
            "short_answer": extra["short_answer"],
            "partial_rules": [],
            "correct_options": extra["correct_options"],
            "custom_scoring_config": None, "manual_review_required": False,
        }
        rows.append((ext_uid, diff_id, i, task_content, solution_rules))
    return rows


def main():
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")

        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s", (COURSE_ID,))
        before_cnt = cur.fetchone()[0]
        cur.execute("SELECT id, is_active FROM materials WHERE id = ANY(%s)",
                    (MATERIAL_IDS,))
        mat_before = {r[0]: r[1] for r in cur.fetchall()}
        print(f"заданий в курсе {COURSE_ID} до: {before_cnt}")
        for mid, act in mat_before.items():
            print(f"  материал {mid} is_active до: {act}")

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

        cur.execute("UPDATE materials SET is_active=false WHERE id = ANY(%s)",
                    (MATERIAL_IDS,))
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
            "AND external_uid LIKE 'lms:tsk109:c155:%%'", (COURSE_ID,))
        series_cnt = cur.fetchone()[0]
        cur.execute(
            "SELECT order_position, task_content->>'type', task_content->>'stem' "
            "FROM tasks WHERE external_uid LIKE 'lms:tsk109:c155:%%' "
            "ORDER BY order_position")
        preview = cur.fetchall()
        cur.execute("SELECT id, is_active FROM materials WHERE id = ANY(%s)",
                    (MATERIAL_IDS,))
        mat_after = {r[0]: r[1] for r in cur.fetchall()}

        print(f"сдвинуто заданий: {shifted}; вставлено серии: {series_cnt}")
        print(f"заданий в курсе после: {after_cnt}")
        print(f"order_position: min={pmin} max={pmax} count={pcnt} distinct={pdistinct}")
        for mid, act in mat_after.items():
            print(f"  материал {mid} is_active после: {act} (обновлено строк: {mat_upd})")
        print("--- серия ---")
        for pos, typ, stem in preview:
            print(f"  {pos:>2}  {typ:<7} {stem[:68]}")

        checks = {
            "вставлено ровно N": series_cnt == N,
            "итог = было + N": after_cnt == before_cnt + N,
            "позиции непрерывны 1..count": pmin == 1 and pmax == pcnt and pdistinct == pcnt,
            "серия на позициях 1..N": [p[0] for p in preview] == list(range(1, N + 1)),
            "оба материала погашены": all(v is False for v in mat_after.values()) and mat_upd == 2,
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
            print("РЕЗУЛЬТАТ: DRY-RUN пройден, ROLLBACK. Запусти с --apply.")
        else:
            conn.rollback()
            print("РЕЗУЛЬТАТ: проверки НЕ пройдены, ROLLBACK.")
            sys.exit(1)
    except Exception as exc:
        conn.rollback()
        print(f"ОШИБКА: {exc!r}. ROLLBACK.")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
