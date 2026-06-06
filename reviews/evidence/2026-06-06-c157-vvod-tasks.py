# -*- coding: utf-8 -*-
"""Курс 157 «Задание 6 ЕГЭ — Исполнитель Черепаха» — вводная серия заданий.

10 вводных/контрольных заданий (SC/MC/SA_COM) на позиции 1-10.
Существующие 97 заданий сдвигаются на +10.
Никакой материал не гасится (в курсе нет целевых материалов для гашения).

Серия: tsk-109 итерация 5 / external_uid prefix: lms:c157:vvod
"""
import io, os, re, sys
import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID  = 157
COURSE_UID = "wp:zadanie-6-ege-po-informatike-ispolnitel-cherepaha"
N          = 10   # количество вводных заданий

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


def mc(stem: str, options: list, correct: list):
    """Multiple-choice задание."""
    return ("MC", stem, options, {"correct_options": correct, "short_answer": None})


def sa(stem: str, value: str, extras: list = None):
    """Short-answer задание (plain accepted_answers)."""
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
    """Short-answer с регулярным выражением (task 3 — команда поднятия пера)."""
    short = {
        "regex": pattern,
        "use_regex": True,
        "normalization": ["trim", "lower"],
        "accepted_answers": [],
    }
    return ("SA_COM", stem, None, {"correct_options": [], "short_answer": short})


# ── содержание серии ──────────────────────────────────────────────────────────

SERIES = [

    # 1 — начальное состояние Черепахи (THEORY)
    (DIFF_THEORY, sc(
        "<p>Программа только что запустилась, ни одна команда Черепахи ещё не выполнена. "
        "Каково начальное состояние Исполнителя «Черепаха»?</p>",
        [opt("A", "Точка (0, 0), смотрит вправо, перо опущено"),
         opt("B", "Точка (0, 0), смотрит вверх, перо опущено"),
         opt("C", "Точка (0, 0), смотрит вверх, перо поднято"),
         opt("D", "Точка (1, 1), смотрит вверх, перо опущено")],
        ["B"])),

    # 2 — что делает right(45) (THEORY)
    (DIFF_THEORY, sc(
        "<p>Черепаха стоит на месте и смотрит <strong>вверх</strong>. "
        "Она выполняет команду <code>right(45)</code>.</p>"
        "<p>Выбери верное описание того, что произошло:</p>",
        [opt("A", "Черепаха сдвинулась вверх на 45 единиц"),
         opt("B", "Черепаха повернулась вправо на 45° — теперь смотрит вправо-вверх (45° от горизонтали)"),
         opt("C", "Черепаха повернулась влево на 45°"),
         opt("D", "Команда right() рисует дугу, а не поворачивает")],
        ["B"])),

    # 3 — команда поднять перо; regex-проверка (THEORY)
    (DIFF_THEORY, sa_regex(
        "<p>Черепаха движется по экрану и оставляет след. "
        "Нужно сделать так, чтобы она продолжала двигаться, но <strong>не рисовала</strong>.</p>"
        "<p>Введи команду Python, которая поднимает перо Черепахи "
        "(принимается любой верный вариант: <code>up</code>, <code>penup</code>, "
        "<code>up()</code>, <code>penup()</code> и другие).</p>",
        # принимаем: penup(), penup, up(), up, t.penup(), t.up()
        r"^(penup\(\)|penup|up\(\)|up|t\.penup\(\)|t\.up\(\))$",
    )),

    # 4 — MC: верные утверждения о goto(x, y) (EASY)
    (DIFF_EASY, mc(
        "<p>Команда <code>goto(x, y)</code> мгновенно переносит Черепаху в точку (x,&nbsp;y).</p>"
        "<p>Выбери <strong>все</strong> верные утверждения о команде <code>goto</code>:</p>",
        [opt("A", "Перо остаётся в том же положении — поднято или опущено"),
         opt("B", "Если перо опущено, goto нарисует отрезок из текущей точки до (x, y)"),
         opt("C", "goto автоматически поднимает перо перед перемещением"),
         opt("D", "goto работает только для точек с неотрицательными координатами")],
        ["A", "B"])),

    # 5 — right(90) forward(10) → координаты (EASY)
    (DIFF_EASY, sa(
        "<p>Черепаха стоит в точке (0,&nbsp;0) и смотрит <strong>вверх</strong>. "
        "Она выполняет две команды:</p>"
        "<ol><li><code>right(90)</code> — поворот вправо на 90°</li>"
        "<li><code>forward(10)</code> — движение вперёд на 10</li></ol>"
        "<p>В какой точке оказалась Черепаха? Введи координаты в формате "
        "<strong>x,y</strong>.</p>",
        "10,0",
    )),

    # 6 — forward(5) right(90) forward(3) → координаты (EASY)
    (DIFF_EASY, sa(
        "<p>Черепаха стоит в точке (0,&nbsp;0) и смотрит <strong>вверх</strong>. "
        "Она выполняет три шага подряд:</p>"
        "<ol>"
        "<li><code>forward(5)</code> — движение вперёд на 5</li>"
        "<li><code>right(90)</code> — поворот вправо на 90°</li>"
        "<li><code>forward(3)</code> — движение вперёд на 3</li>"
        "</ol>"
        "<p>Куда пришла Черепаха? Введи координаты в формате "
        "<strong>x,y</strong> (например: 2,7).</p>",
        "3,5",
    )),

    # 7 — суммарный угол поворота Повтори 4 (EASY)
    (DIFF_EASY, sa(
        "<p>Черепаха выполняет программу:</p>"
        "<pre>Повтори 4 [Направо 90]</pre>"
        "<p>На сколько градусов <strong>суммарно</strong> повернулась Черепаха "
        "за всю программу? Введи число.</p>",
        "360",
    )),

    # 8 — какую фигуру рисует Повтори 4 [Вперёд 10 Направо 90] (EASY)
    (DIFF_EASY, sc(
        "<p>Черепаха начинает в точке (0,&nbsp;0) и смотрит вверх. "
        "Она выполняет программу:</p>"
        "<pre>Повтори 4 [Вперёд 10 Направо 90]</pre>"
        "<p>Какую фигуру нарисует Черепаха?</p>",
        [opt("A", "Равносторонний треугольник"),
         opt("B", "Квадрат со стороной 10"),
         opt("C", "Круг радиуса 10"),
         opt("D", "Ромб")],
        ["B"])),

    # 9 — точки строго внутри равностороннего треугольника (NORMAL)
    # вершины: (0,0), (0,10), (5√3≈8.66, 5); interior count = 38
    (DIFF_NORMAL, sa(
        "<p>Черепаха начинает в точке (0,&nbsp;0) и смотрит <strong>вверх</strong>. "
        "Программа:</p>"
        "<pre>Повтори 3 [Вперёд 10 Направо 120]</pre>"
        "<p>Черепаха нарисует замкнутый равносторонний треугольник и вернётся "
        "в точку (0,&nbsp;0).</p>"
        "<p>Нарисованная область закрашивается. Сколько точек с "
        "<strong>целыми</strong> координатами (x,&nbsp;y) лежат "
        "<strong>строго внутри</strong> закрашенной области — "
        "без точек на сторонах треугольника?</p>"
        "<p><em>Подсказка: точка (0,&nbsp;5) лежит на стороне треугольника — "
        "не считается. Точка (1,&nbsp;3) — внутри — считается.</em></p>"
        "<p>Введи число.</p>",
        "38",
    )),

    # 10 — точки строго внутри прямоугольника 10×5 (NORMAL)
    # Программа рисует прямоугольник дважды; interior = 9×4 = 36
    (DIFF_NORMAL, sa(
        "<p>Черепаха начинает в точке (0,&nbsp;0) и смотрит <strong>вверх</strong>. "
        "Программа:</p>"
        "<pre>Повтори 4 [Вперёд 5 Направо 90 Вперёд 10 Направо 90]</pre>"
        "<p>Черепаха нарисует один и тот же прямоугольник дважды. "
        "Размеры прямоугольника: <strong>высота 5, ширина 10</strong>.</p>"
        "<p>Нарисованная область закрашивается. Сколько точек с "
        "<strong>целыми</strong> координатами (x,&nbsp;y) лежат "
        "<strong>строго внутри</strong> закрашенного прямоугольника — "
        "без точек на его сторонах?</p>"
        "<p>Введи число.</p>",
        "36",
    )),
]

assert len(SERIES) == N, f"Ожидается {N} заданий, а в SERIES = {len(SERIES)}"


# ── сборка строк для вставки ──────────────────────────────────────────────────

def build_rows() -> list:
    rows = []
    for i, (diff_id, (ttype, stem, options, extra)) in enumerate(SERIES, start=1):
        ext_uid = f"lms:c157:vvod:{i:02d}"
        task_content = {
            "code": None, "stem": stem, "tags": None,
            "type": ttype, "media": None, "title": None, "prompt": None,
            "options": options, "has_hints": False,
            "course_uid": COURSE_UID,
            "hints_text": [], "hints_video": [], "difficulty_code": None,
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
        print(f"заданий в курсе {COURSE_ID} до: {before_cnt}")

        # ── сдвиг существующих заданий +N ────────────────────────────────────
        cur.execute(
            "UPDATE tasks SET order_position = order_position + %s "
            "WHERE course_id=%s",
            (N, COURSE_ID),
        )
        shifted = cur.rowcount
        print(f"сдвинуто заданий на +{N}: {shifted}")

        # ── вставка 10 вводных заданий на позиции 1..N ────────────────────────
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
            "SELECT count(*) FROM tasks "
            "WHERE course_id=%s AND external_uid LIKE 'lms:c157:vvod:%%'",
            (COURSE_ID,),
        )
        series_cnt = cur.fetchone()[0]

        cur.execute(
            "SELECT order_position, task_content->>'type', "
            "left(task_content->>'stem', 70) "
            "FROM tasks "
            "WHERE external_uid LIKE 'lms:c157:vvod:%%' "
            "ORDER BY order_position",
        )
        preview = cur.fetchall()

        print(f"\nзаданий после: {after_cnt}  (было {before_cnt})")
        print(f"order_position: min={pmin} max={pmax} count={pcnt} distinct={pdistinct}")
        print("--- серия (pos / тип / начало stem) ---")
        for pos, typ, stem_head in preview:
            print(f"  {pos:>2}  {typ:<7}  {stem_head!r}")

        checks = {
            f"было {before_cnt} заданий":           before_cnt > 0,
            f"вставлено ровно {N}":                  series_cnt == N,
            f"итог = было + {N}":                    after_cnt == before_cnt + N,
            "позиции непрерывны 1..count":           (pmin == 1 and pmax == pcnt
                                                      and pdistinct == pcnt),
            f"серия на позициях 1..{N}":             [p[0] for p in preview] == list(range(1, N + 1)),
            f"сдвинуты все старые ({before_cnt})":  shifted == before_cnt,
        }

        print("\n--- проверки ---")
        for name, ok in checks.items():
            print(f"  [{'OK' if ok else 'FAIL'}] {name}")

        if all(checks.values()) and apply:
            conn.commit()
            print("\nРЕЗУЛЬТАТ: все проверки пройдены, COMMIT.")
        elif all(checks.values()):
            conn.rollback()
            print("\nРЕЗУЛЬТАТ: DRY-RUN пройден, ROLLBACK. Запусти с --apply для записи.")
        else:
            conn.rollback()
            print("\nРЕЗУЛЬТАТ: проверки НЕ пройдены, ROLLBACK.")
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
