# -*- coding: utf-8 -*-
"""Курс 162 «Задание 11 ЕГЭ. Вычисление объёма информации» — серия заданий.

20 авто-проверяемых заданий (SC/SA_COM) + гашение материала 371 «Контрольные вопросы и мини-задания».
Существующие 82 задания сдвигаются +20. Итого: 102.

Серия: tsk-109 итерация 8 / external_uid prefix: lms:c162:vvod
Блок 1: Теория (01-10) — SC/SA_COM, THEORY/EASY
Блок 2: Мини-задания (11-20) — SA_COM, EASY/NORMAL
"""
import io, os, re, sys
import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID   = 162
COURSE_UID  = "wp:zadanie-11-ege-po-informatike-vychislenie-obema-informatsii"
MATERIAL_ID = 371
N           = 20

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

    # ── Блок 1: Теория (01–10) ────────────────────────────────────────────────

    # 01 — что означает K в формуле I = N × K (THEORY)
    (DIFF_THEORY, [], sc(
        "<p>В формуле <code>I&nbsp;=&nbsp;N&nbsp;×&nbsp;K</code>, "
        "где I — информационный объём сообщения, что обозначает символ K?</p>",
        [opt("A", "Число символов в тексте"),
         opt("B", "Число бит, необходимое для кодирования одного символа"),
         opt("C", "Мощность алфавита"),
         opt("D", "Объём сообщения в байтах")],
        ["B"],
    )),

    # 02 — байт в Кбайте (THEORY)
    (DIFF_THEORY, [], sa(
        "<p>Сколько байт содержится в 1&nbsp;Кбайте?</p>"
        "<p>Введите число.</p>",
        "1024",
    )),

    # 03 — минимальное число бит для 100 символов: 2^7=128 ≥ 100 → K=7 (EASY)
    (DIFF_EASY, [], sa(
        "<p>Какое минимальное количество бит необходимо для кодирования "
        "алфавита из 100 символов?</p>"
        "<p>Введите число.</p>",
        "7",
    )),

    # 04 — связь мощности алфавита N и числа бит K: N = 2^K (THEORY)
    (DIFF_THEORY, [], sc(
        "<p>Мощность алфавита N и количество бит K, необходимое для кодирования "
        "одного символа, связаны формулой:</p>",
        [opt("A", "N = K"),
         opt("B", "N = 2^K"),
         opt("C", "K = 2^N"),
         opt("D", "N = K × 8")],
        ["B"],
    )),

    # 05 — 1/8 Мбайт в биты: 1/8 × 1024 × 1024 × 8 = 1 048 576 (EASY)
    (DIFF_EASY, [], sa(
        "<p>Переведите 1/8&nbsp;Мбайта в биты.</p>"
        "<p>Введите число.</p>",
        "1048576",
    )),

    # 06 — формула Хартли: I = −log₂(p) (THEORY)
    (DIFF_THEORY, [], sc(
        "<p>Некоторое событие наступает с вероятностью <em>p</em>. "
        "Сколько бит информации несёт сообщение о его наступлении?</p>",
        [opt("A", "p × 8"),
         opt("B", "−log₂(p)"),
         opt("C", "log₁₀(1/p)"),
         opt("D", "1/p")],
        ["B"],
    )),

    # 07 — почему K=5 не хватает для 50 символов: 2⁵=32 < 50 (EASY)
    (DIFF_EASY, [], sc(
        "<p>Алфавит содержит 50 символов. Ученик решил взять K&nbsp;=&nbsp;5 бит "
        "на символ (2⁵&nbsp;=&nbsp;32). Почему это неверно?</p>",
        [opt("A", "2⁵ = 32 < 50 — вариантов кодов не хватит для 50 символов"),
         opt("B", "5 не является степенью двойки"),
         opt("C", "Нужно брать K кратным 8"),
         opt("D", "При K = 5 возникнет коллизия байтов")],
        ["A"],
    )),

    # 08 — удвоение числа символов в тексте → объём × 2 (EASY)
    (DIFF_EASY, [], sc(
        "<p>Текст состоит из N символов и занимает I бит. Текст увеличили "
        "в 2 раза — добавили ещё N символов того же алфавита. "
        "Новый объём в битах:</p>",
        [opt("A", "4 × I"),
         opt("B", "2 × I"),
         opt("C", "I + 1"),
         opt("D", "I (не изменился)")],
        ["B"],
    )),

    # 09 — 2⁷ < M ≤ 2⁸ → K = 8 (EASY)
    (DIFF_EASY, [], sc(
        "<p>Мощность алфавита M удовлетворяет условию "
        "2⁷&nbsp;&lt;&nbsp;M&nbsp;≤&nbsp;2⁸. Какое минимальное количество бит "
        "необходимо для кодирования одного символа такого алфавита?</p>",
        [opt("A", "7 бит"),
         opt("B", "8 бит"),
         opt("C", "15 бит"),
         opt("D", "Зависит от конкретного значения M")],
        ["B"],
    )),

    # 10 — типичная ошибка: ×100 вместо ×1024 (EASY)
    (DIFF_EASY, [], sc(
        "<p>При переводе 3&nbsp;Кбайт в байты ученик написал: "
        "3&nbsp;×&nbsp;100&nbsp;=&nbsp;300&nbsp;байт. В чём ошибка?</p>",
        [opt("A", "1 Кбайт = 1024 байт, а не 100 — надо умножать на 1024"),
         opt("B", "1 Кбайт = 1000 байт, надо умножать на 1000"),
         opt("C", "Надо делить, а не умножать"),
         opt("D", "Ошибки нет, ответ верен")],
        ["A"],
    )),

    # ── Блок 2: Мини-задания (11–20) ─────────────────────────────────────────

    # 11 — 32 символа → K=5 бит: 2⁵=32 (EASY)
    (DIFF_EASY, [], sa(
        "<p>В алфавите 32 символа. Сколько бит необходимо для кодирования "
        "одного символа?</p>"
        "<p>Введите число.</p>",
        "5",
    )),

    # 12 — 200 символов × 6 бит = 1200 бит = 150 байт (EASY)
    (DIFF_EASY, [], sa(
        "<p>Сообщение содержит 200 символов, каждый кодируется 6 битами. "
        "Найдите объём сообщения в байтах.</p>"
        "<p>Введите число.</p>",
        "150",
    )),

    # 13 — 8-символьный алфавит, слова длиной 4: 8⁴=4096 (EASY)
    (DIFF_EASY, [], sa(
        "<p>Алфавит состоит из 8 символов. Сколько различных слов длиной "
        "4 символа можно составить из букв этого алфавита "
        "(буквы могут повторяться)?</p>"
        "<p>Введите число.</p>",
        "4096",
    )),

    # 14 — 256 бит/с × 10 с = 2560 бит = 320 байт (EASY)
    (DIFF_EASY, [], sa(
        "<p>Данные передаются со скоростью 256&nbsp;бит/с. "
        "Сколько байт будет передано за 10 секунд?</p>"
        "<p>Введите число.</p>",
        "320",
    )),

    # 15 — 2³ = 8 символов (THEORY)
    (DIFF_THEORY, [], sa(
        "<p>Для кодирования символа алфавита используется 3 бита. "
        "Какова мощность этого алфавита (максимальное количество символов)?</p>"
        "<p>Введите число.</p>",
        "8",
    )),

    # 16 — 2048 символов × 8 бит = 16384 бит = 2048 байт = 2 Кбайт (EASY)
    (DIFF_EASY, [], sa(
        "<p>Текст содержит 2048 символов, каждый кодируется 8 битами. "
        "Каков объём текста в Кбайтах?</p>"
        "<p>Введите число.</p>",
        "2",
    )),

    # 17 — P=1/4 → I=log₂(4)=2 бит (EASY)
    (DIFF_EASY, [], sa(
        "<p>Вероятность некоторого события равна 1/4. "
        "Сколько бит информации несёт сообщение о его наступлении?</p>"
        "<p>Введите число.</p>",
        "2",
    )),

    # 18 — 4 Кбайт / 2 байт/символ = 2048 символов (NORMAL)
    (DIFF_NORMAL, [
        "https://vk.com/video-53400615_456239517",
        "https://vk.com/video-53400615_456239661",
    ], sa(
        "<p>Сообщение объёмом 4&nbsp;Кбайта содержит текст, "
        "где каждый символ кодируется 2 байтами. "
        "Определите количество символов в сообщении.</p>"
        "<p>Введите число.</p>",
        "2048",
    )),

    # 19 — 20-символьный алфавит, 10-символьный пароль → 7 байт (NORMAL)
    # K = ceil(log₂(20)) = 5; 10×5=50 бит; ceil(50/8)=7 байт
    (DIFF_NORMAL, [
        "https://vk.com/video-53400615_456239517",
        "https://vk.com/video-53400615_456239661",
    ], sa(
        "<p>Пароль составлен из 10 символов алфавита мощностью 20 символов. "
        "Найдите минимальное количество байт, необходимое для хранения пароля.</p>"
        "<p>Введите число.</p>",
        "7",
    )),

    # 20 — 4096 символов × 16 бит = 65536 бит = 8192 байт = 8 Кбайт (NORMAL)
    (DIFF_NORMAL, [
        "https://vk.com/video-53400615_456239517",
        "https://vk.com/video-53400615_456239661",
    ], sa(
        "<p>Текст содержит 4096 символов, каждый кодируется 16 битами. "
        "Каков объём текста в Кбайтах?</p>"
        "<p>Введите число.</p>",
        "8",
    )),
]

assert len(SERIES) == N, f"Ожидается {N} заданий, а в SERIES = {len(SERIES)}"


# ── сборка строк для вставки ──────────────────────────────────────────────────

def build_rows() -> list:
    rows = []
    for i, (diff_id, hints_video, (ttype, stem, options, extra)) in enumerate(
        SERIES, start=1
    ):
        ext_uid = f"lms:c162:vvod:{i:02d}"
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

        # ── 2. Вставить 20 заданий на позиции 1..N ───────────────────────────
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

        # ── 3. Погасить материал 371 ─────────────────────────────────────────
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
            "FROM tasks WHERE external_uid LIKE 'lms:c162:vvod:%' "
            "ORDER BY order_position",
        )
        new_rows = cur.fetchall()

        expected_after = before_cnt + N   # 82 + 20 = 102

        print(f"\n── состояние после ───────────────────────────────────────")
        print(f"заданий: {after_cnt}  (ожидается {expected_after})")
        print(f"order_position: min={pmin} max={pmax} count={pcnt} distinct={pdistinct}")
        print(f"материал {MATERIAL_ID} is_active после: {mat_after}")
        print("новые задания:")
        for pos, uid, ttype in new_rows:
            print(f"  pos={pos:3d}  {uid}  {ttype}")

        checks = {
            f"до было {before_cnt} заданий":         before_cnt == 82,
            f"итог {expected_after}":                 after_cnt == expected_after,
            f"позиции непрерывны 1..{expected_after}": (
                pmin == 1 and pmax == expected_after
                and pdistinct == expected_after
            ),
            "сдвинуто 82 задания":                   shifted == 82,
            f"вставлено {N} новых":                   len(new_rows) == N,
            f"новые на pos 1..{N}":                   (
                [r[0] for r in new_rows] == list(range(1, N + 1))
            ),
            f"материал {MATERIAL_ID} погашен":        not mat_after,
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
