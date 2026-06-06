# -*- coding: utf-8 -*-
"""Курс 159 «Задание 8 ЕГЭ. Комбинаторика» — вводная серия заданий.

13 вводных SA_COM-заданий + гашение материала 442 «Вводные задания».
Существующие 145 заданий сдвигаются +13. Итого: 158.

Серия: tsk-109 итерация 7 / external_uid prefix: lms:c159:vvod
"""
import io, os, re, sys
import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID   = 159
COURSE_UID  = "wp:zadanie-8-ege-po-informatike-kombinatorika"
MATERIAL_ID = 442   # «Вводные задания» → погасить
N           = 13    # количество вводных заданий

DIFF_EASY   = 2
DIFF_NORMAL = 3
DIFF_HARD   = 4


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


def sa(stem: str, value: str, extras: list = None):
    """SA_COM с авто-чеком по списку ответов."""
    accepted = [{"score": 1, "value": value}]
    for v in (extras or []):
        accepted.append({"score": 1, "value": v})
    short = {
        "regex": None,
        "use_regex": False,
        "normalization": ["trim", "lower"],
        "accepted_answers": accepted,
    }
    return ("SA_COM", stem, None, {"correct_options": [], "short_answer": short})


def sa_regex(stem: str, pattern: str):
    """SA_COM с авто-чеком по регулярному выражению."""
    short = {
        "regex": pattern,
        "use_regex": True,
        "normalization": ["trim", "lower"],
        "accepted_answers": [],
    }
    return ("SA_COM", stem, None, {"correct_options": [], "short_answer": short})


# ── содержание серии ──────────────────────────────────────────────────────────
# Структура каждого элемента: (difficulty_id, hints_video, task_tuple)

SERIES = [

    # 01 — генератор: список 1..100 (EASY) → длина = 100
    (DIFF_EASY, [], sa(
        "<p>С помощью генератора создайте список из чисел от 1 до 100.</p>"
        "<p>Введите длину полученного списка.</p>",
        "100",
    )),

    # 02 — генератор: квадратные корни 1..100 (EASY) → длина = 100
    (DIFF_EASY, [], sa(
        "<p>С помощью генератора создайте список из квадратных корней чисел от 1 до 100.</p>"
        "<p>Введите длину полученного списка.</p>",
        "100",
    )),

    # 03 — генератор: буквы алфавита через chr()/ord() (EASY) → 32
    # chr(ord('а')..ord('я')) даёт 32 буквы (ё = chr(1105) вне диапазона 1072-1103)
    (DIFF_EASY, [], sa_regex(
        "<p>С помощью генератора создайте список строчных букв русского алфавита, "
        "используя функции <code>chr()</code> и <code>ord()</code> "
        "без явного перечисления букв.</p>"
        "<p>Введите количество элементов в полученном списке.</p>",
        r"^32$",
    )),

    # 04 — генератор: перестановки Р,У,К,А,В (EASY) → 5! = 120
    (DIFF_EASY, [], sa(
        "<p>С помощью генератора создайте список из всех возможных перестановок букв "
        "Р,&nbsp;У,&nbsp;К,&nbsp;А,&nbsp;В.</p>"
        "<p>Введите длину полученного списка.</p>",
        "120",
    )),

    # 05 — список → строка с пробелом (EASY)
    (DIFF_EASY, [], sa(
        "<p>Дан список <code>['б', 'е', 'р', 'к', 'с', 'о', 'б', 'е', 'н']</code>.</p>"
        "<p>Преобразуйте его в строку с разделителем — пробелом.</p>"
        "<p>Введите получившуюся строку.</p>",
        "б е р к с о б е н",
    )),

    # 06 — список → строка обратная без разделителей (EASY) → «небоскреб»
    (DIFF_EASY, [], sa(
        "<p>Дан список <code>['б', 'е', 'р', 'к', 'с', 'о', 'б', 'е', 'н']</code>.</p>"
        "<p>Преобразуйте его в строку без разделителей в обратном порядке.</p>"
        "<p>Введите получившуюся строку.</p>",
        "небоскреб",
    )),

    # 07 — доработать задание 4: строки, count начинается на А, без циклов (NORMAL)
    # Ответ: 4! = 24  (первая буква А, остальные 4 из {Р,У,К,В})
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239218"], sa(
        "<p>Доработайте задание 4: генератор должен формировать элементы-строки "
        "(а не кортежи).</p>"
        "<p>Посчитайте количество слов в списке, которые начинаются на букву А. "
        "В задании запрещено использовать циклы.</p>"
        "<p>Введите количество таких слов.</p>",
        "24",
    )),

    # 08 — все слова длиной 4 из Б,О,К,С,Ё,Р (NORMAL)
    # P(6,4) = 6×5×4×3 = 360
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239263"], sa(
        "<p>Составьте все возможные слова длиной 4 из букв Б,&nbsp;О,&nbsp;К,&nbsp;С,&nbsp;Ё,&nbsp;Р "
        "(каждая буква используется не более одного раза).</p>"
        "<p>Введите количество таких слов.</p>",
        "360",
    )),

    # 09 — слова длиной 3 и 4 из Б,О,К,С,Ё,Р, 2-я буква гласная (NORMAL)
    # длина 3: 5×2×4 = 40; длина 4: 5×2×4×3 = 120; итого 160
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239580"], sa(
        "<p>Составьте все возможные слова длиной 3 и 4 из букв "
        "Б,&nbsp;О,&nbsp;К,&nbsp;С,&nbsp;Ё,&nbsp;Р (каждая буква используется не более одного раза). "
        "Вторая буква обязательно должна быть гласной.</p>"
        "<p>Введите общее количество таких слов.</p>",
        "160",
    )),

    # 10 — АККУРАТНО, без двух одинаковых подряд (NORMAL)
    # Всего: 9!/(2!×2!) = 90720; АА-подряд: 8!/2! = 20160; КК-подряд: 20160;
    # оба: 7! = 5040; плохих: 35280; хороших: 55440
    (DIFF_NORMAL, [
        "https://vk.com/video-53400615_456239581",
        "https://vk.com/video-53400615_456239185",
    ], sa(
        "<p>Составьте все возможные слова перестановкой букв в слове «АККУРАТНО».</p>"
        "<p>Исключите варианты, в которых две одинаковые буквы стоят рядом.</p>"
        "<p>Введите количество допустимых вариантов.</p>",
        "55440",
    )),

    # 11 — В,У,А,Л,Ь длиной 5, Ь не первым и не после гласных (NORMAL)
    # Ь на pos2-5: 4×12 = 48
    (DIFF_NORMAL, [], sa(
        "<p>Составьте все возможные слова длиной 5 из букв В,&nbsp;У,&nbsp;А,&nbsp;Л,&nbsp;Ь "
        "(каждая буква используется ровно один раз).</p>"
        "<p>Мягкий знак не должен стоять первым и не должен стоять после гласных букв "
        "(У,&nbsp;А).</p>"
        "<p>Введите количество допустимых слов.</p>",
        "48",
    )),

    # 12 — позиция «ТОНЕТ» в убывающем порядке (HARD)
    # Алфавит {А,Е,К,Н,О,Т,Ф}, слова длиной 5 с повторениями, 7^5 = 16807 всего
    # Ф...=2401; ТФ..+ТТ..=686; ТОФ+ТОТ+ТОО=147; ТОНФ..ТОНК=35; ТОНЕФ=1 → 3271
    (DIFF_HARD, ["https://vk.com/video-53400615_456239415"], sa(
        "<p>Все пятибуквенные слова, составленные из букв К,&nbsp;О,&nbsp;Н,&nbsp;Ф,&nbsp;Е,&nbsp;Т,&nbsp;А "
        "(буквы могут повторяться), отсортированы в алфавитном порядке по убыванию.</p>"
        "<p>На каком месте находится слово «ТОНЕТ»?</p>"
        "<p>Введите номер позиции.</p>",
        "3271",
    )),

    # 13 — позиция «БАГДА» среди слов из «АБВГДЕ» с ограничениями (HARD)
    # 4-буквенных valid=979, 5-буквенных valid=5705; БАГДА — 1102-я в 5-буквенных
    # итоговая позиция: 979 + 1102 = 2081
    (DIFF_HARD, [], sa(
        "<p>Все четырёх- и пятибуквенные слова из алфавита «АБВГДЕ» "
        "(буквы могут повторяться) отсортированы сначала по длине, затем по алфавиту "
        "в порядке возрастания.</p>"
        "<p>Из них исключены: слова с буквой «Е» на третьей позиции "
        "и слова, содержащие сочетание «ГВ» в любой позиции.</p>"
        "<p>На какой позиции расположено слово «БАГДА»?</p>"
        "<p>Введите номер позиции.</p>",
        "2081",
    )),
]

assert len(SERIES) == N, f"Ожидается {N} заданий, а в SERIES = {len(SERIES)}"


# ── сборка строк для вставки ──────────────────────────────────────────────────

def build_rows() -> list:
    rows = []
    for i, (diff_id, hints_video, (ttype, stem, options, extra)) in enumerate(
        SERIES, start=1
    ):
        ext_uid = f"lms:c159:vvod:{i:02d}"
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
        cur.execute(
            "SELECT is_active FROM materials WHERE id=%s", (MATERIAL_ID,)
        )
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

        # ── 2. Вставить 13 вводных заданий на позиции 1..N ───────────────────
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

        # ── 3. Погасить материал 442 ─────────────────────────────────────────
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

        cur.execute(
            "SELECT is_active FROM materials WHERE id=%s", (MATERIAL_ID,)
        )
        mat_after = cur.fetchone()[0]

        cur.execute(
            "SELECT order_position, external_uid, "
            "task_content->>'type' AS ttype, "
            "solution_rules->'short_answer'->>'use_regex' AS is_regex "
            "FROM tasks "
            "WHERE external_uid LIKE 'lms:c159:vvod:%' "
            "ORDER BY order_position",
        )
        new_rows = cur.fetchall()

        expected_after = before_cnt + N   # 145 + 13 = 158

        print(f"\n── состояние после ──────────────────────────────────────────")
        print(f"заданий: {after_cnt}  (ожидается {expected_after})")
        print(f"order_position: min={pmin} max={pmax} count={pcnt} distinct={pdistinct}")
        print(f"материал {MATERIAL_ID} is_active после: {mat_after}")
        print("новые задания:")
        for pos, uid, ttype, is_regex in new_rows:
            flag = " [regex]" if is_regex == "true" else ""
            print(f"  pos={pos:3d}  {uid}  {ttype}{flag}")

        checks = {
            f"до было {before_cnt} заданий":         before_cnt == 145,
            f"итог {expected_after}":                after_cnt == expected_after,
            f"позиции непрерывны 1..{expected_after}": (
                pmin == 1 and pmax == expected_after
                and pdistinct == expected_after
            ),
            "сдвинуто 145 заданий":                  shifted == 145,
            f"вставлено {N} новых":                   len(new_rows) == N,
            f"новые на pos 1..{N}":                   (
                [r[0] for r in new_rows] == list(range(1, N + 1))
            ),
            f"материал {MATERIAL_ID} погашен":        not mat_after,
        }

        print(f"\n── проверки ─────────────────────────────────────────────────")
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
