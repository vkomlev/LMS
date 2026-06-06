# -*- coding: utf-8 -*-
"""Курс 147 «Задание 19-21 ЕГЭ. Теория игр» — серия вводных заданий.

3 авто-проверяемых задания SA_COM. Материал НЕ деактивируется.
Существующие 140 заданий сдвигаются +3. Итого: 143.

Серия: tsk-109 итерация 14 / external_uid prefix: lms:c147:vvod
Тема: одна куча, устный способ (найти мин S, при котором Ваня выигрывает первым ходом)

Задания:
  01) +1 или ×2, предел ≥52  → ответ 13
  02) +1 или ×3, предел ≥48  → ответ 6
  03) +1 или ×4, предел ≥100 → ответ 7

Устный метод (общий):
  1. Найти мин. позицию P, откуда Ваня побеждает ×k: P = ceil(T/k)
  2. Найти мин. S, откуда Петя попадает на P ходом ×k: S = ceil(P/k) = ceil(T/k²)
"""
import io, os, re, sys
import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID   = 147
COURSE_UID  = "wp:zadanie-19-21-ege-po-informatike-teoriya-igr"
MATERIAL_ID = None
N           = 3

DIFF_EASY   = 2

HINTS = [
    "https://vk.com/video-53400615_456240704",
    "https://vk.com/video-53400615_456240705",
]


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


def sa(stem: str, value: str):
    """Сборщик SA_COM с одним точным ответом (нормализация: trim + lower)."""
    short = {
        "regex": None, "use_regex": False,
        "normalization": ["trim", "lower"],
        "accepted_answers": [{"score": 1, "value": value}],
    }
    return ("SA_COM", stem, None, {"correct_options": [], "short_answer": short})


# ── тексты заданий ────────────────────────────────────────────────────────────

_BASE = (
    "<p>Два игрока, Петя и Ваня, играют в следующую игру. Перед игроками лежит "
    "куча камней. Игроки ходят по очереди, первый ход делает Петя. За один ход "
    "игрок может:</p>"
    "<ul>"
    "<li>добавить в кучу один камень;</li>"
    "<li>{mult_text}.</li>"
    "</ul>"
    "<p>Игра завершается в тот момент, когда количество камней в куче становится "
    "не менее {limit}. Победителем считается игрок, сделавший последний ход.</p>"
    "<p>В начальный момент в куче было S камней (1&nbsp;≤&nbsp;S&nbsp;≤&nbsp;{limit_m1}).</p>"
    "<p><strong>Найдите наименьшее значение S, при котором Ваня мог выиграть "
    "своим первым ходом.</strong></p>"
    "<p>Введите число.</p>"
)


def game_stem(mult_k: int, limit: int) -> str:
    """Строит текст задания для игры '+1 или ×mult_k, предел limit'."""
    mult_words = {
        2: "увеличить количество камней в куче в два раза",
        3: "увеличить количество камней в куче в три раза",
        4: "увеличить количество камней в куче в четыре раза",
    }
    return _BASE.format(
        mult_text=mult_words[mult_k],
        limit=limit,
        limit_m1=limit - 1,
    )


# ── серия ─────────────────────────────────────────────────────────────────────
#
# Устная проверка ответов:
#   01: Pmin = ceil(52/2)=26; Smin = ceil(26/2)=13
#       S=13 → Петя ×2→26 → Ваня ×2→52 ✓ | S=12: 12×2=24, из 24: ×2=48<52 ✗
#   02: Pmin = ceil(48/3)=16; Smin = ceil(16/3)=6
#       S=6  → Петя ×3→18 → Ваня ×3→54 ✓ | S=5:  5×3=15, из 15: ×3=45<48 ✗
#   03: Pmin = ceil(100/4)=25; Smin = ceil(25/4)=7
#       S=7  → Петя ×4→28 → Ваня ×4→112 ✓| S=6:  6×4=24, из 24: ×4=96<100 ✗

SERIES = [
    # 01 — +1/×2, предел 52, ответ 13
    (DIFF_EASY, HINTS, sa(game_stem(2, 52), "13")),

    # 02 — +1/×3, предел 48, ответ 6
    (DIFF_EASY, HINTS, sa(game_stem(3, 48), "6")),

    # 03 — +1/×4, предел 100, ответ 7
    (DIFF_EASY, HINTS, sa(game_stem(4, 100), "7")),
]

assert len(SERIES) == N, f"Ожидается {N} заданий, SERIES={len(SERIES)}"


# ── сборка строк ──────────────────────────────────────────────────────────────

def build_rows() -> list:
    rows = []
    for i, (diff_id, hints_video, (ttype, stem, options, extra)) in enumerate(
        SERIES, start=1
    ):
        ext_uid = f"lms:c147:vvod:{i:02d}"
        task_content = {
            "code": None, "stem": stem, "tags": None,
            "type": ttype, "media": None, "title": None, "prompt": None,
            "options": options, "has_hints": bool(hints_video),
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
            "FROM tasks WHERE external_uid LIKE 'lms:c147:vvod:%' "
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
            f"до было {before_cnt} заданий":           before_cnt == 140,
            f"итог {expected_after}":                   after_cnt == expected_after,
            f"позиции непрерывны 1..{expected_after}":  (
                pmin == 1 and pmax == expected_after
                and pdistinct == expected_after
            ),
            "сдвинуто 140 заданий":                    shifted == 140,
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
