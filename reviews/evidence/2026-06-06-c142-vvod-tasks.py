# -*- coding: utf-8 -*-
"""Курс 142 «Задание 14 ЕГЭ. Позиционные системы счисления» — серия заданий.

11 авто-проверяемых заданий (SC/SA_COM) + гашение материала 380
«Контрольные вопросы и мини-задания».
Существующие 152 задания сдвигаются +11. Итого: 163.

Серия: tsk-109 итерация 10 / external_uid prefix: lms:c142:vvod
Блок 1: Теория (01-03)         — SC, THEORY
Блок 2: Вычисления (04-11)     — SA_COM/SC, EASY/NORMAL
  (вопрос 9 материала разбит на 09 и 10 — два независимых ответа)
"""
import io, os, re, sys
import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID   = 142
COURSE_UID  = "wp:zadanie-14-ege-po-informatike-pozitsionnye-sistemy-schisleniya"
MATERIAL_ID = 380
N           = 11

DIFF_THEORY = 1
DIFF_EASY   = 2
DIFF_NORMAL = 3


# ── helpers ───────────────────────────────────────────────────────────────────

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


def opt(oid: str, text: str) -> dict:
    return {"id": oid, "text": text, "is_active": True, "explanation": ""}


def sc(stem: str, options: list, correct: list):
    """Single-choice задание."""
    return ("SC", stem, options, {"correct_options": correct, "short_answer": None})


def sa(stem: str, value: str, extras: list = None):
    """SA_COM с авто-чеком по списку ответов."""
    accepted = [{"score": 1, "value": value}]
    for v in (extras or []):
        accepted.append({"score": 1, "value": v})
    short = {
        "regex": None, "use_regex": False,
        "normalization": ["trim", "lower"],
        "accepted_answers": accepted,
    }
    return ("SA_COM", stem, None, {"correct_options": [], "short_answer": short})


# ── содержание серии ──────────────────────────────────────────────────────────
# Структура: (difficulty_id, hints_video, task_tuple)

SERIES = [

    # ── Блок 1: Теория (01–03) ────────────────────────────────────────────────

    # 01 — что такое позиционная система счисления (THEORY)
    (DIFF_THEORY, ["https://vk.com/video-53400615_456240218"], sc(
        "<p>Что такое позиционная система счисления?</p>",
        [opt("A", "Система, в которой все цифры имеют одинаковое значение независимо от их расположения"),
         opt("B", "Система, в которой значение каждой цифры зависит от её позиции в записи числа"),
         opt("C", "Система, использующая только цифры 0 и 1"),
         opt("D", "Система записи, в которой вместо цифр используются буквы")],
        ["B"],
    )),

    # 02 — перевод числа из системы N в десятичную (THEORY)
    (DIFF_THEORY, ["https://vk.com/video-53400615_456240219"], sc(
        "<p>Как перевести целое число из системы с основанием N в десятичную?</p>",
        [opt("A", "Разделить число на N и записать остатки от деления в обратном порядке"),
         opt("B", "Умножить каждую цифру на её порядковый номер (1, 2, 3, …) и сложить произведения"),
         opt("C", "Умножить каждую цифру на Nⁱ, где i — позиция цифры справа (начиная с 0), и сложить результаты"),
         opt("D", "Записать каждую цифру числа в двоичном коде и объединить")],
        ["C"],
    )),

    # 03 — почему int() принимает основание до 36 (THEORY)
    (DIFF_THEORY, ["https://vk.com/video-53400615_456239730"], sc(
        "<p>Почему функция Python <code>int(s, base)</code> принимает основание <code>base</code> "
        "не выше 36?</p>",
        [opt("A", "Потому что в стандарте Python ограничено 36 системами счисления"),
         opt("B", "Потому что числа с основанием больше 36 не встречаются в практических задачах"),
         opt("C", "Потому что доступно 10 цифр (0–9) и 26 букв латинского алфавита (a–z): "
                  "итого 36 возможных символов-цифр"),
         opt("D", "Потому что значения больше 36 не помещаются в 32-битное целое число")],
        ["C"],
    )),

    # ── Блок 2: Вычисления (04–11) ───────────────────────────────────────────

    # 04 — int('F', 16) = 15 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239577"], sa(
        "<p>Чему равен результат выражения <code>int('F', 16)</code>?</p>"
        "<p>Введите число.</p>",
        "15",
    )),

    # 05 — 16**3 = 4096 = 0x1000 → 3 значащих нуля (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239577"], sa(
        "<p>Вычислите <code>16**3</code>.</p>"
        "<p>Сколько значащих нулей у этого числа в шестнадцатеричной записи?</p>"
        "<p><em>Подсказка: значащие нули — нули, стоящие после первой ненулевой цифры.</em></p>"
        "<p>Введите число.</p>",
        "3",
    )),

    # 06 — 345₁₀ → 8 = 531₈ (EASY)
    # 345 = 5×64 + 3×8 + 1×1 → 531₈
    (DIFF_EASY, ["https://vk.com/video-53400615_456239731"], sa(
        "<p>Переведите число <strong>345</strong> (основание 10) в восьмеричную систему счисления.</p>"
        "<p>Запишите результат цифрами без приставки <code>0o</code>.</p>",
        "531",
    )),

    # 07 — 2ⁿ−1 в двоичной = n единиц подряд (THEORY)
    (DIFF_THEORY, [], sc(
        "<p>Как выглядит число <strong>2ⁿ − 1</strong> в двоичной системе счисления?</p>",
        [opt("A", "1 с n нулями (т.е. 10...0 из n+1 цифр) — это равно 2ⁿ, а не 2ⁿ − 1"),
         opt("B", "n единиц подряд (11...1 из n цифр)"),
         opt("C", "1 с (n − 1) нулями"),
         opt("D", "n нулей подряд")],
        ["B"],
    )),

    # 08 — наименьшее основание, в котором 30₁₀ трёхзначное (EASY)
    # p=3: 3³=27 ≤ 30 → 4 цифры (✗); p=4: 4²=16 ≤ 30 < 64=4³ → 3 цифры (✓)
    (DIFF_EASY, [], sa(
        "<p>Какое наименьшее основание системы счисления, "
        "в которой число <strong>30</strong> является <em>трёхзначным</em>?</p>"
        "<p>Введите число.</p>",
        "4",
    )),

    # 09 — 255 → hex = ff (EASY, вопрос 9 материала, часть 1)
    # 255 = 15×16 + 15 = 0xff
    (DIFF_EASY, ["https://vk.com/video-53400615_456239731"], sa(
        "<p>Запишите число <strong>255</strong> в шестнадцатеричной системе счисления.</p>"
        "<p>Используйте строчные буквы. Приставку <code>0x</code> не писать.</p>",
        "ff",
    )),

    # 10 — 255 → oct = 377 (EASY, вопрос 9 материала, часть 2)
    # 255 = 3×64 + 7×8 + 7 = 0o377
    (DIFF_EASY, ["https://vk.com/video-53400615_456239731"], sa(
        "<p>Запишите число <strong>255</strong> в восьмеричной системе счисления.</p>"
        "<p>Приставку <code>0o</code> не писать.</p>",
        "377",
    )),

    # 11 — Python: цифр 'A' в hex(100..200) = 18 (NORMAL)
    # hex(160)='0xa0'..hex(175)='0xaf': 16 чисел, из них hex(170)='0xaa'→2 буквы A
    # итого 160-175: 10×1 + 1×2 + 5×1 = 17; плюс hex(186)='0xba'→1; итого 18
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239467",
                   "https://vk.com/video-53400615_456239751"], sa(
        "<p>Напишите функцию, которая возвращает суммарное количество шестнадцатеричных "
        "цифр <strong>«A»</strong> (регистр не важен) во всех числах от 100 до 200 "
        "включительно.</p>"
        "<p>Пример кода:</p>"
        "<pre>def count_a():\n"
        "    return sum(hex(n).count('a') for n in range(100, 201))</pre>"
        "<p>Чему равно возвращаемое значение?</p>"
        "<p>Введите число.</p>",
        "18",
    )),
]

assert len(SERIES) == N, f"Ожидается {N} заданий, а в SERIES = {len(SERIES)}"


# ── сборка строк для вставки ──────────────────────────────────────────────────

def build_rows() -> list:
    rows = []
    for i, (diff_id, hints_video, (ttype, stem, options, extra)) in enumerate(
        SERIES, start=1
    ):
        ext_uid = f"lms:c142:vvod:{i:02d}"
        has_hints = bool(hints_video)
        task_content = {
            "code": None, "stem": stem, "tags": None,
            "type": ttype, "media": None, "title": None, "prompt": None,
            "options": options, "has_hints": has_hints,
            "course_uid": COURSE_UID,
            "hints_text": [], "hints_video": hints_video,
            "difficulty_code": None,
        }
        solution_rules = {
            "max_score": 1,
            "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
            "auto_check": True, "text_answer": None,
            "scoring_mode": "all_or_nothing",
            "short_answer": extra["short_answer"],
            "partial_rules": [], "correct_options": extra["correct_options"],
            "custom_scoring_config": None, "manual_review_required": False,
        }
        rows.append((ext_uid, diff_id, i, task_content, solution_rules))
    return rows


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    apply = "--apply" in sys.argv

    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()

    try:
        cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")

        # ── снимок до ────────────────────────────────────────────────────────
        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s", (COURSE_ID,))
        before_cnt = cur.fetchone()[0]
        cur.execute("SELECT is_active FROM materials WHERE id=%s", (MATERIAL_ID,))
        mat_before = cur.fetchone()
        print(f"заданий в курсе {COURSE_ID} до: {before_cnt}")
        print(f"материал {MATERIAL_ID} is_active до: {mat_before[0] if mat_before else 'НЕТ'}")

        # ── 1. Сдвинуть все задания +N ───────────────────────────────────────
        cur.execute(
            "UPDATE tasks SET order_position = order_position + %s "
            "WHERE course_id=%s",
            (N, COURSE_ID),
        )
        shifted = cur.rowcount
        print(f"сдвинуто заданий на +{N}: {shifted}")

        # ── 2. Вставить 11 заданий на позиции 1..N ───────────────────────────
        rows = build_rows()
        for ext_uid, diff_id, pos, tc, sr in rows:
            cur.execute(
                "INSERT INTO tasks "
                "(external_uid, max_score, task_content, course_id, difficulty_id, "
                "solution_rules, max_attempts, time_limit_sec, order_position) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ext_uid, 1, Json(tc), COURSE_ID, diff_id, Json(sr),
                 None, None, pos),
            )
        print(f"вставлено новых заданий: {len(rows)}")

        # ── 3. Погасить материал 380 ─────────────────────────────────────────
        cur.execute(
            "UPDATE materials SET is_active=false WHERE id=%s AND course_id=%s",
            (MATERIAL_ID, COURSE_ID),
        )
        mat_upd = cur.rowcount
        print(f"погашен материал {MATERIAL_ID}: {mat_upd} строк")

        # ── самопроверка ──────────────────────────────────────────────────────
        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s", (COURSE_ID,))
        after_cnt = cur.fetchone()[0]

        cur.execute(
            "SELECT min(order_position), max(order_position), count(*), "
            "count(DISTINCT order_position) FROM tasks WHERE course_id=%s",
            (COURSE_ID,),
        )
        pmin, pmax, pcnt, pdistinct = cur.fetchone()

        cur.execute("SELECT is_active FROM materials WHERE id=%s", (MATERIAL_ID,))
        mat_after = cur.fetchone()[0]

        cur.execute(
            "SELECT order_position, external_uid, task_content->>'type' AS ttype "
            "FROM tasks WHERE external_uid LIKE 'lms:c142:vvod:%' "
            "ORDER BY order_position",
        )
        new_rows = cur.fetchall()

        expected_after = before_cnt + N   # 152 + 11 = 163

        print(f"\n── состояние после ───────────────────────────────────────")
        print(f"заданий: {after_cnt}  (ожидается {expected_after})")
        print(f"order_position: min={pmin} max={pmax} count={pcnt} distinct={pdistinct}")
        print(f"материал {MATERIAL_ID} is_active после: {mat_after}")
        print("новые задания:")
        for pos, uid, ttype in new_rows:
            print(f"  pos={pos:3d}  {uid}  {ttype}")

        checks = {
            f"до было {before_cnt} заданий":          before_cnt == 152,
            f"итог {expected_after}":                  after_cnt == expected_after,
            f"позиции непрерывны 1..{expected_after}": (
                pmin == 1 and pmax == expected_after
                and pdistinct == expected_after
            ),
            "сдвинуто 152 задания":                    shifted == 152,
            f"вставлено {N} новых":                    len(new_rows) == N,
            f"новые на pos 1..{N}":                    (
                [r[0] for r in new_rows] == list(range(1, N + 1))
            ),
            f"материал {MATERIAL_ID} погашен":         not mat_after,
        }

        print(f"\n── проверки ─────────────────────────────────────────────")
        for name, ok in checks.items():
            print(f"  [{'OK' if ok else 'FAIL'}] {name}")

        if all(checks.values()) and apply:
            conn.commit()
            print("РЕЗУЛЬТАТ: все проверки пройдены, COMMIT.")
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
