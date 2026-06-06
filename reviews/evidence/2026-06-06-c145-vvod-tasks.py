# -*- coding: utf-8 -*-
"""Курс 145 «Задание 17 ЕГЭ. Обработка числовых последовательностей» — серия вводных заданий.

7 авто-проверяемых заданий SA_COM. Материал НЕ деактивируется.
Существующие 62 задания сдвигаются +7. Итого: 69.

Серия: tsk-109 итерация 11 / external_uid prefix: lms:c145:vvod
Файл данных: https://victor-komlev.ru/wp-content/uploads/2025/09/17_1970.zip
  17_1970.txt: 5000 целых чисел от -1000 до 1000.

Вычисленные ответы:
  01) пар подряд                      = 4999
  02) макс сумма пары подряд          = 1990  (1000+990, двух подряд 1000 нет)
  03) всех комбинаций пар C(5000,2)   = 12497500
  04) макс сумма всех пар             = 2000  (1000+1000, 5 вхождений 1000)
  05) кратных 3                       = 1679
  06) оканчивается на 3 (|x|%10==3)   = 496
  07) элементов > ср.арифм. чётных    = 2456  (avg_even ≈ -3.10)
"""
import io, os, re, sys
import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID   = 145
COURSE_UID  = "wp:zadanie-17-ege-po-informatike-obrabotka-chislovyh-posledovatelnostej"
MATERIAL_ID = None
N           = 7

DIFF_EASY   = 2
DIFF_NORMAL = 3

_FILE_LINK = (
    '<p>Скачайте архив с файлом данных: '
    '<a href="https://victor-komlev.ru/wp-content/uploads/2025/09/17_1970.zip">'
    '17_1970.zip</a> — файл <code>17_1970.txt</code> содержит 5000 целых чисел '
    'от −1000 до 1000, по одному в строке.</p>'
    '<pre>with open("17_1970.txt") as f:\n'
    '    nums = [int(x) for x in f.read().split()]</pre>'
)


# helpers ─────────────────────────────────────────────────────────────────────

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


def sa(stem: str, value: str, extras: list = None):
    accepted = [{"score": 1, "value": value}]
    for v in (extras or []):
        accepted.append({"score": 1, "value": v})
    short = {
        "regex": None, "use_regex": False,
        "normalization": ["trim", "lower"],
        "accepted_answers": accepted,
    }
    return ("SA_COM", stem, None, {"correct_options": [], "short_answer": short})


# ── серия ─────────────────────────────────────────────────────────────────────

SERIES = [

    # 01 — количество пар из идущих подряд элементов = 4999 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239697",
                 "https://vk.com/video-53400615_456239762"], sa(
        _FILE_LINK
        + "<p><strong>Задание 1а.</strong> Пара — два <em>идущих подряд</em> элемента "
          "(элемент с индексом i и элемент с индексом i+1).</p>"
          "<p>Посчитайте <strong>количество</strong> таких пар.</p>"
          "<p>Введите число.</p>",
        "4999",
    )),

    # 02 — макс сумма пары подряд = 1990 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239697",
                 "https://vk.com/video-53400615_456239762"], sa(
        _FILE_LINK
        + "<p><strong>Задание 1б.</strong> Пара — два идущих подряд элемента.</p>"
          "<p>Найдите <strong>максимальное значение суммы</strong> среди всех таких пар.</p>"
          "<p>Введите число.</p>",
        "1990",
    )),

    # 03 — количество всех комбинаций пар C(5000,2) = 12497500 (NORMAL)
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239697",
                   "https://vk.com/video-53400615_456239762"], sa(
        _FILE_LINK
        + "<p><strong>Задание 2а.</strong> Пара — все возможные комбинации двух элементов "
          "файла (без учёта порядка, без повторений).</p>"
          "<p>Посчитайте <strong>количество</strong> таких пар.</p>"
          "<p><em>Подсказка: если элементов N, то пар = N×(N−1)/2.</em></p>"
          "<p>Введите число.</p>",
        "12497500",
    )),

    # 04 — макс сумма всех пар = 2000 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239697",
                 "https://vk.com/video-53400615_456239762"], sa(
        _FILE_LINK
        + "<p><strong>Задание 2б.</strong> Пара — все возможные комбинации двух элементов.</p>"
          "<p>Найдите <strong>максимальное значение суммы</strong> среди всех таких пар.</p>"
          "<p>Введите число.</p>",
        "2000",
    )),

    # 05 — кратных 3 = 1679 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239762"], sa(
        _FILE_LINK
        + "<p><strong>Задание 3.</strong> Посчитайте количество элементов файла, "
          "<strong>кратных 3</strong>.</p>"
          "<p>Введите число.</p>",
        "1679",
    )),

    # 06 — оканчивается на 3 = 496 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239762"], sa(
        _FILE_LINK
        + "<p><strong>Задание 4.</strong> Посчитайте количество элементов файла, "
          "<strong>оканчивающихся на цифру 3</strong> "
          "(для отрицательных чисел смотрим на последнюю цифру модуля, "
          "т.е. <code>abs(x) % 10 == 3</code>).</p>"
          "<p>Введите число.</p>",
        "496",
    )),

    # 07 — > среднего чётных = 2456 (NORMAL)
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239762"], sa(
        _FILE_LINK
        + "<p><strong>Задание 5.</strong> Посчитайте количество элементов файла, "
          "которые <strong>больше среднего арифметического всех чётных значений</strong> "
          "исходной последовательности.</p>"
          "<p>Введите число.</p>",
        "2456",
    )),
]

assert len(SERIES) == N, f"Ожидается {N} заданий, SERIES={len(SERIES)}"


# ── сборка ────────────────────────────────────────────────────────────────────

def build_rows() -> list:
    rows = []
    for i, (diff_id, hints_video, (ttype, stem, options, extra)) in enumerate(
        SERIES, start=1
    ):
        ext_uid = f"lms:c145:vvod:{i:02d}"
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

        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s", (COURSE_ID,))
        before_cnt = cur.fetchone()[0]
        print(f"заданий в курсе {COURSE_ID} до: {before_cnt}")

        cur.execute(
            "UPDATE tasks SET order_position = order_position + %s WHERE course_id=%s",
            (N, COURSE_ID),
        )
        shifted = cur.rowcount
        print(f"сдвинуто заданий на +{N}: {shifted}")

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

        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s", (COURSE_ID,))
        after_cnt = cur.fetchone()[0]

        cur.execute(
            "SELECT min(order_position), max(order_position), count(*), "
            "count(DISTINCT order_position) FROM tasks WHERE course_id=%s",
            (COURSE_ID,),
        )
        pmin, pmax, pcnt, pdistinct = cur.fetchone()

        cur.execute(
            "SELECT order_position, external_uid, task_content->>'type' AS ttype "
            "FROM tasks WHERE external_uid LIKE 'lms:c145:vvod:%' "
            "ORDER BY order_position",
        )
        new_rows = cur.fetchall()

        expected_after = before_cnt + N

        print(f"\n── состояние после ───────────────────────────────────────")
        print(f"заданий: {after_cnt}  (ожидается {expected_after})")
        print(f"order_position: min={pmin} max={pmax} count={pcnt} distinct={pdistinct}")
        print("новые задания:")
        for pos, uid, ttype in new_rows:
            print(f"  pos={pos:3d}  {uid}  {ttype}")

        checks = {
            f"до было {before_cnt} заданий":          before_cnt == 62,
            f"итог {expected_after}":                  after_cnt == expected_after,
            f"позиции непрерывны 1..{expected_after}": (
                pmin == 1 and pmax == expected_after
                and pdistinct == expected_after
            ),
            "сдвинуто 62 задания":                     shifted == 62,
            f"вставлено {N} новых":                    len(new_rows) == N,
            f"новые на pos 1..{N}":                    (
                [r[0] for r in new_rows] == list(range(1, N + 1))
            ),
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
