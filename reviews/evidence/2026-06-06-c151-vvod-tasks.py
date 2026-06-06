# -*- coding: utf-8 -*-
"""Курс 151 «Задание 24 ЕГЭ. Обработка текста» — серия вводных заданий.

19 авто-проверяемых заданий (SC/SA_COM). Материал НЕ деактивируется.
Существующие 127 заданий сдвигаются +19. Итого: 146.

Серия: tsk-109 итерация 12 / external_uid prefix: lms:c151:vvod
Блок 1 (01-09): вводные — обработка текста в цикле (списки a и b)
Блок 2 (10-19): тренировка регулярных выражений
"""
import io, os, re, sys
import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID   = 151
COURSE_UID  = "wp:zadanie-24-ege-po-informatike-obrabotka-teksta"
MATERIAL_ID = None   # деактивация не нужна
N           = 19

DIFF_THEORY = 1
DIFF_EASY   = 2
DIFF_NORMAL = 3

# Компактные данные для вставки в условия задач
A_STR = ("YZXYXXYYZXXXXZXZYXXZXXXZYXYXZYXZXXZZYZXYZYZZZYXYXYZXXYYZZZYXXYZXZZ"
         "XYYYXYXZXYZYXYYYZZZXXXYYXXZYYXZZYXZXYZYYYYYYYZXXZZYXXXZYYZZYYZXYXZZZ"
         "XZYYZYXXYYXZYYXZYXZZXXZZXXXZYZYYXZZZXXYXZYXYZZXXXYYYZYXZZYXXZZYYYXXZ"
         "YZZZZXXYZZXZYYXXZYYZYYYZYZXYYXYZXYXYYYYZXYZZYXXZYZXZXXYXYYXXYYZZZYYZ"
         "XXYYYZZXYZXYZZYZYYZYXXYYZZZXXX")
B_STR = ("XAUDIBROVWGNNRSGAOXORESJOQKWSISJOMKBWEUAASXPOCCIOQJLGQMEXNUBNITNLRD"
         "SGCVMPVNNHXWTVREEGDVBWGWEIHVBTBJLYDDKZAEHORPUUKUYLWBBXHGXXNMCILNJTK"
         "WHJZBAYPFFCMMTRZJVEJCVOZNBURIAJHXIRHKMHHMTQNWMWTISPVAKJOZYOSNQKYTXDO"
         "MFMBCFWTVIHZTLIWKZJTDFZIOJKNUFRQWPMSNDHMSRZFPJUKCDFOEPJKUZXUSFTWMVMK"
         "OFSGTVFEYWCDDZMAOKASAOYNSKUPUSIBXKXLLCLLYYEKDMUDTSQRUDKSKWNKGRIPDEIIP"
         "WGOHPOVSBIDOVFHEBOLCZIINQOQRJLDEKNOGUXTEEPLHNMNKDKHTXMJJUSXTYOZVEOEIQ"
         "KJJLQVMXJQMXIOTMXQYAHSETDFOVEINFCUEMDJIXISAGZWHAAVLTXNRIHBUEROFMLUNDP"
         "NCCJBPDRKWKSLIQDOMZPKAR")

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


def sc(stem: str, options: list, correct: list):
    return ("SC", stem, options, {"correct_options": correct, "short_answer": None})


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


# ── данные для блока 1 ────────────────────────────────────────────────────────
_A_CODE = f'<pre>a = list("{A_STR}")  # 300 символов из X, Y, Z</pre>'
_B_CODE = f'<pre>b = list("{B_STR}")  # 500 символов из A–Z</pre>'

# ── серия ─────────────────────────────────────────────────────────────────────

SERIES = [

    # ══ Блок 1: обработка текста (01–09) ═════════════════════════════════════

    # 01 — макс подряд Z в a = 4 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239649",
                 "https://vk.com/video-53400615_456239733"], sa(
        f"<p>Дан список из 300 символов (X, Y, Z):</p>{_A_CODE}"
        "<p>Найдите максимальное количество подряд идущих букв "
        "<code>Z</code> в этом списке.</p>"
        "<p>Введите число.</p>",
        "4",
    )),

    # 02 — макс подряд одинаковых в a = 7 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239649",
                 "https://vk.com/video-53400615_456239733"], sa(
        f"<p>Дан список:</p>{_A_CODE}"
        "<p>Найдите максимальное количество подряд идущих <em>одинаковых</em> "
        "символов (любого вида) в этом списке.</p>"
        "<p>Введите число.</p>",
        "7",
    )),

    # 03 — макс чередующихся (соседние различны) в a = 11 (NORMAL)
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239649",
                   "https://vk.com/video-53400615_456239733"], sa(
        f"<p>Дан список:</p>{_A_CODE}"
        "<p>Найдите максимальное количество подряд идущих символов, "
        "у которых <strong>каждые два соседних различны</strong> "
        "(т.е. ни один символ не стоит рядом с точно таким же).</p>"
        "<p>Введите число.</p>",
        "11",
    )),

    # 04 — цепочек длины 3 по условию в a = 34 (NORMAL)
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239649",
                   "https://vk.com/video-53400615_456239660"], sa(
        f"<p>Дан список:</p>{_A_CODE}"
        "<p>Найдите количество цепочек длины 3, удовлетворяющих условиям:</p>"
        "<ul>"
        "<li>1-й символ — один из <code>Z</code> или <code>X</code></li>"
        "<li>2-й символ — один из <code>X</code> или <code>Y</code>, "
        "не совпадает с первым</li>"
        "<li>3-й символ — один из <code>Y</code> или <code>Z</code>, "
        "не совпадает со вторым</li>"
        "</ul>"
        "<p>Введите число.</p>",
        "34",
    )),

    # 05 — макс цепочка X,Y в a = 10 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239649",
                 "https://vk.com/video-53400615_456239733"], sa(
        f"<p>Дан список:</p>{_A_CODE}"
        "<p>Найдите максимальную длину цепочки из символов "
        "<code>X</code> и <code>Y</code> в произвольном порядке "
        "(без символа <code>Z</code>).</p>"
        "<p>Введите число.</p>",
        "10",
    )),

    # 06 — макс цепочка XYZXYZ... в a = 6 (NORMAL)
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239649",
                   "https://vk.com/video-53400615_456239733"], sa(
        f"<p>Дан список:</p>{_A_CODE}"
        "<p>Найдите максимальную длину цепочки вида "
        "<code>XYZXYZXYZ…</code> (последний неполный фрагмент допустим). "
        "Например, <code>XYZXY</code> — допустимая цепочка длины 5.</p>"
        "<p>Введите число.</p>",
        "6",
    )),

    # 07 — символ чаще всего после E в b = I (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239649",
                 "https://vk.com/video-53400615_456239660"], sa(
        f"<p>Дан список из 500 символов (A–Z):</p>{_B_CODE}"
        "<p>Определите символ, который <strong>чаще всего встречается</strong> "
        "сразу после буквы <code>E</code>.</p>"
        "<p>Введите одну букву (любой регистр).</p>",
        "i",
        ["I"],
    )),

    # 08 — макс расстояние между одинаковыми в b = 497 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239649",
                 "https://vk.com/video-53400615_456239660"], sa(
        f"<p>Дан список:</p>{_B_CODE}"
        "<p>Определите максимальное расстояние (разность индексов) "
        "между двумя вхождениями одного и того же символа "
        "(учитываются <em>любые</em> два вхождения, не обязательно соседние).</p>"
        "<p>Введите число.</p>",
        "497",
    )),

    # 09 — макс подряд без XZZY в a = 103 (NORMAL)
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239649",
                   "https://vk.com/video-53400615_456239733"], sa(
        f"<p>Дан список:</p>{_A_CODE}"
        "<p>Определите максимальное количество подряд идущих символов, "
        "в которых <strong>не встречается подпоследовательность "
        "<code>XZZY</code></strong>.</p>"
        "<p>Введите число.</p>",
        "103",
    )),

    # ══ Блок 2: регулярные выражения (10–19) ══════════════════════════════════

    # 10 — re.search digit (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239237",
                 "https://vk.com/video-53400615_456239483"], sa(
        "<p>Выполните код:</p>"
        "<pre>import re\n"
        "result = re.search(r'\\d', 'Hello123')\n"
        "print(result.group())</pre>"
        "<p>Что выведет программа?</p>"
        "<p>Введите ответ.</p>",
        "1",
    )),

    # 11 — re.search word in quotes (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239237",
                 "https://vk.com/video-53400615_456239483"], sa(
        '<p>Выполните код:</p>'
        '<pre>import re\n'
        'result = re.search(r\'\"(\\w+)\"\', \'This is a "word" in quotes.\')\n'
        'print(result.group(1))</pre>'
        "<p>Что выведет программа?</p>"
        "<p>Введите ответ.</p>",
        "word",
    )),

    # 12 — email validation SC (THEORY)
    (DIFF_THEORY, ["https://vk.com/video-53400615_456239237"], sc(
        "<p>Дан паттерн для проверки email:</p>"
        "<pre>import re\n"
        "pattern = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+$'\n"
        "result = bool(re.match(pattern, 'user@example.com'))</pre>"
        "<p>Чему равно значение <code>result</code>?</p>",
        [opt("A", "True — строка соответствует паттерну"),
         opt("B", "False — строка не соответствует паттерну")],
        ["A"],
    )),

    # 13 — repeated words (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239237",
                 "https://vk.com/video-53400615_456239483"], sa(
        "<p>Выполните код:</p>"
        "<pre>import re\n"
        "result = re.search(r'\\b(\\w+)\\b\\s+\\1\\b', 'This is a test test.')\n"
        "print(result.group())</pre>"
        "<p>Что выведет программа?</p>"
        "<p>Введите ответ (с пробелом между словами).</p>",
        "test test",
    )),

    # 14 — phone extraction (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239237",
                 "https://vk.com/video-53400615_456239483"], sa(
        "<p>Выполните код:</p>"
        "<pre>import re\n"
        "pattern = r'\\(?\\d{3}\\)?[-.\\s]?\\d{3}[-.\\s]?\\d{4}'\n"
        "result = re.search(pattern, '123-456-7890')\n"
        "print(result.group())</pre>"
        "<p>Что выведет программа?</p>"
        "<p>Введите ответ.</p>",
        "123-456-7890",
    )),

    # 15 — count HTML tags = 4 (NORMAL)
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239237",
                   "https://vk.com/video-53400615_456239428"], sa(
        "<p>Выполните код:</p>"
        "<pre>import re\n"
        "result = re.findall(r'<[^>]+>', '<p>This is <b>bold</b> text.</p>')\n"
        "print(len(result))</pre>"
        "<p>Что выведет программа?</p>"
        "<p>Введите число.</p>",
        "4",
    )),

    # 16 — count dates = 2 (NORMAL)
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239237",
                   "https://vk.com/video-53400615_456239428"], sa(
        "<p>Выполните код:</p>"
        "<pre>import re\n"
        "text = 'Date: 01/15/2022, Meeting on 03/20/2022'\n"
        "result = re.findall(r'\\b\\d{2}/\\d{2}/\\d{4}\\b', text)\n"
        "print(len(result))</pre>"
        "<p>Что выведет программа?</p>"
        "<p>Введите число.</p>",
        "2",
    )),

    # 17 — count 5-letter words = 2 (NORMAL, исправлено: Python=6 букв)
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239237",
                   "https://vk.com/video-53400615_456239428"], sa(
        "<p>Выполните код:</p>"
        "<pre>import re\n"
        "text = 'Hello world, Python is amazing!'\n"
        "result = re.findall(r'\\b\\w{5}\\b', text)\n"
        "print(len(result))</pre>"
        "<p>Что выведет программа?</p>"
        "<p><em>Подсказка: посчитайте буквы в каждом слове.</em></p>"
        "<p>Введите число.</p>",
        "2",
    )),

    # 18 — URL protocol = https (NORMAL)
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239237",
                   "https://vk.com/video-53400615_456239428"], sa(
        "<p>Выполните код:</p>"
        "<pre>import re\n"
        "pattern = r'(?P&lt;protocol&gt;https?)://(?P&lt;domain&gt;[\\w.-]+)/(?P&lt;path&gt;[\\w/]*)'\n"
        "url = 'https://www.example.com/path/to/page'\n"
        "result = re.match(pattern, url)\n"
        "print(result.group('protocol'))</pre>"
        "<p>Что выведет программа?</p>"
        "<p>Введите ответ.</p>",
        "https",
    )),

    # 19 — IPv6 address (NORMAL)
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239237",
                   "https://vk.com/video-53400615_456239428"], sa(
        "<p>Выполните код:</p>"
        "<pre>import re\n"
        "pattern = r'[0-9a-fA-F]{1,4}(:[0-9a-fA-F]{1,4}){7}'\n"
        "ipv6 = '2001:0db8:85a3:0000:0000:8a2e:0370:7334'\n"
        "result = re.search(pattern, ipv6)\n"
        "print(result.group())</pre>"
        "<p>Что выведет программа?</p>"
        "<p>Введите ответ.</p>",
        "2001:0db8:85a3:0000:0000:8a2e:0370:7334",
    )),
]

assert len(SERIES) == N, f"Ожидается {N} заданий, SERIES={len(SERIES)}"


# ── сборка строк ──────────────────────────────────────────────────────────────

def build_rows() -> list:
    rows = []
    for i, (diff_id, hints_video, (ttype, stem, options, extra)) in enumerate(
        SERIES, start=1
    ):
        ext_uid = f"lms:c151:vvod:{i:02d}"
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

        # Сдвинуть всё +N
        cur.execute(
            "UPDATE tasks SET order_position = order_position + %s WHERE course_id=%s",
            (N, COURSE_ID),
        )
        shifted = cur.rowcount
        print(f"сдвинуто заданий на +{N}: {shifted}")

        # Вставить N заданий
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

        # Самопроверка
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
            "FROM tasks WHERE external_uid LIKE 'lms:c151:vvod:%' "
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
            f"до было {before_cnt} заданий":          before_cnt == 127,
            f"итог {expected_after}":                  after_cnt == expected_after,
            f"позиции непрерывны 1..{expected_after}": (
                pmin == 1 and pmax == expected_after
                and pdistinct == expected_after
            ),
            "сдвинуто 127 заданий":                    shifted == 127,
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
