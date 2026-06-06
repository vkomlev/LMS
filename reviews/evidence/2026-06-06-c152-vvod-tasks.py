# -*- coding: utf-8 -*-
"""Курс 152 «Задание 25 ЕГЭ по информатике. Обработка числовых данных» — серия вводных заданий.

17 авто-проверяемых заданий SA_COM. Материал НЕ деактивируется.
Существующие 107 заданий сдвигаются +17. Итого: 124.

Серия: tsk-109 итерация 13 / external_uid prefix: lms:c152:vvod
Тема: функция get_div, делители, генераторы, массивы, M(N), diff_multi, маски.

Примечание: в курсе уже есть 2 «вводных» задания TG-формата (pos 31, 33),
без правильных ответов/SA_COM — новая серия создаётся независимо от них.

Вычисленные ответы:
  01) len(get_div(360))                              = 22
  02) кол-во простых [2..500]                        = 95
  03) кол-во чисел [2..500] с 2 делителями           = 149
  04) кол-во чисел [2..500] с 3 чётными дел.         = 77
  05) сумм. размер 2D массива (77 × 3)               = 231
  06) первое число после сортировки по prod          = 16
  07) макс. кол-во делителей [2..500]                = 22
  08) первое число после сортировки по убыванию      = 480
  09) первое число после сортировки по макс.дел.     = 360
  10) наименьшее из 5 чисел > 500000 с дел. на 8     = 500002
  11) M(18) = 9+6                                    = 15
  12) наименьшее > 10M с 0 < M(N) < 10000            = 10000043
  13) len(diff_multi(18))                            = 2
  14) кол-во чисел [0..500] с ≥3 разностями ≤11      = 3
  15) кол-во N=[200M;400M] = 2^m*3^n (m чётн, n неч) = 4
  16) кол-во кратных 2023 в [2023; 2000000]          = 988
  17) наименьшее 7-значн. число по маске 1?2139*4    = 1021394
"""
import io, os, re, sys
import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID   = 152
COURSE_UID  = "wp:zadanie-25-ege-po-informatike-obrabotka-chislovyh-dannyh"
MATERIAL_ID = None
N           = 17

DIFF_EASY   = 2
DIFF_NORMAL = 3
DIFF_HARD   = 4

V_INTRO  = "https://vk.com/video-53400615_456239171"
V_ARRAY  = "https://vk.com/video-53400615_456239445"
V_SEARCH = "https://vk.com/video-53400615_456239312"
V_MFUNC  = "https://vk.com/video-53400615_456239212"
V_LARGE  = "https://vk.com/video-53400615_456239211"
V_PAIR   = "https://vk.com/video-53400615_456239173"
V_POW    = "https://vk.com/video-53400615_456239318"
V_MASK1  = "https://vk.com/video-53400615_456239542"
V_MASK2  = "https://vk.com/video-53400615_456239217"


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

    # 01 — get_div: len(get_div(360)) = 22 (EASY)
    (DIFF_EASY, [V_INTRO], sa(
        "<p><strong>Задание 1.</strong> Напишите функцию <code>get_div(x)</code>. "
        "На вход функции подаётся целое число. Результат — последовательность всех "
        "возможных натуральных делителей числа x, <strong>за исключением числа 1 и "
        "самого числа x</strong>. Для создания последовательности используйте генератор.</p>"
        "<p>Например: <code>list(get_div(18)) = [2, 3, 6, 9]</code></p>"
        "<p>Используя написанную функцию, найдите <strong>количество делителей числа 360</strong> "
        "(не считая 1 и само число).</p>"
        "<p>Введите число.</p>",
        "22",
    )),

    # 02 — простые числа [2..500] = 95 (EASY)
    (DIFF_EASY, [V_INTRO], sa(
        "<p><strong>Задание 2.</strong> Используя функцию <code>get_div</code> из предыдущего "
        "задания, найдите все простые числа в диапазоне от 2 до 500 включительно. Напомним: "
        "простые числа делятся нацело только на 1 и на само себя — то есть "
        "<code>list(get_div(n))</code> возвращает пустой список.</p>"
        "<p>Поместите простые числа в список, используя генератор.</p>"
        "<p>Найдите <strong>количество</strong> простых чисел в этом диапазоне.</p>"
        "<p>Введите число.</p>",
        "95",
    )),

    # 03 — числа с ровно 2 делителями [2..500] = 149 (EASY)
    (DIFF_EASY, [V_INTRO], sa(
        "<p><strong>Задание 3.</strong> Используя функцию <code>get_div</code>, найдите все числа "
        "в диапазоне от 2 до 500, имеющие <strong>ровно два натуральных делителя</strong> "
        "(не считая 1 и само число). Поместите числа в список с помощью генератора.</p>"
        "<p>Найдите <strong>количество</strong> таких чисел.</p>"
        "<p>Введите число.</p>",
        "149",
    )),

    # 04 — числа с ровно 3 чётными делителями [2..500] = 77 (NORMAL)
    (DIFF_NORMAL, [V_INTRO], sa(
        "<p><strong>Задание 4.</strong> Модифицируйте функцию <code>get_div</code>: пусть она "
        "возвращает только <strong>чётные</strong> натуральные делители (нечётные не учитывать). "
        "Найдите все числа в диапазоне от 2 до 500, имеющие <strong>ровно три чётных "
        "натуральных делителя</strong>. Поместите их в список с помощью генератора.</p>"
        "<p>Найдите <strong>количество</strong> таких чисел.</p>"
        "<p>Введите число.</p>",
        "77",
    )),

    # 05 — суммарный размер 2D массива чётных делителей = 231 (NORMAL)
    (DIFF_NORMAL, [V_ARRAY], sa(
        "<p><strong>Задание 5.</strong> Для каждого числа из списка задания 4 получите список "
        "его чётных натуральных делителей (исключая 1 и само число) и поместите в <strong>двумерный "
        "массив</strong> с помощью генератора (одна строка — делители одного числа).</p>"
        "<p>Найдите <strong>суммарное количество элементов</strong> во всём двумерном массиве "
        "(сумму длин всех строк).</p>"
        "<p>Введите число.</p>",
        "231",
    )),

    # 06 — первое число после сортировки по возрастанию произведения = 16 (NORMAL)
    (DIFF_NORMAL, [V_ARRAY], sa(
        "<p><strong>Задание 6.</strong> Отсортируйте двумерный массив из задания 5 "
        "<strong>в порядке возрастания произведения всех натуральных делителей числа</strong> "
        "(исключая 1 и само число; используйте стандартный <code>get_div</code> без ограничения "
        "на чётность).</p>"
        "<p>Какое число окажется <strong>первым</strong> в отсортированном массиве?</p>"
        "<p>Введите число.</p>",
        "16",
    )),

    # 07 — максимальное кол-во делителей [2..500] = 22 (EASY)
    (DIFF_EASY, [V_ARRAY], sa(
        "<p><strong>Задание 7.</strong> Используя функцию <code>get_div</code>, найдите "
        "<strong>максимальное количество натуральных делителей</strong> (исключая 1 и само число) "
        "среди всех чисел от 2 до 500.</p>"
        "<p>Введите это максимальное количество.</p>",
        "22",
    )),

    # 08 — первое число после сортировки по убыванию = 480 (EASY)
    (DIFF_EASY, [V_ARRAY], sa(
        "<p><strong>Задание 8.</strong> Найдите все числа от 2 до 500, у которых количество "
        "натуральных делителей максимально (используйте результат предыдущего задания). "
        "Для каждого такого числа создайте строку: само число + все его натуральные делители. "
        "Поместите строки в двумерный массив.</p>"
        "<p>Отсортируйте массив <strong>в порядке убывания</strong> самих чисел.</p>"
        "<p>Какое число окажется <strong>первым</strong> в отсортированном массиве?</p>"
        "<p>Введите число.</p>",
        "480",
    )),

    # 09 — первое число после сортировки по возрастанию макс.делителя = 360 (NORMAL)
    (DIFF_NORMAL, [V_ARRAY], sa(
        "<p><strong>Задание 9.</strong> Используя массив из задания 8, отсортируйте его "
        "<strong>в порядке возрастания максимального делителя</strong> каждого числа.</p>"
        "<p>Какое число окажется <strong>первым</strong> в отсортированном массиве?</p>"
        "<p>Введите число.</p>",
        "360",
    )),

    # 10 — наименьшее из 5 чисел > 500000 с дел. оканч. на 8 = 500002 (HARD)
    (DIFF_HARD, [V_SEARCH], sa(
        "<p><strong>Задание 10.</strong> Используя функцию <code>get_div</code>, найдите "
        "5 наименьших чисел, <strong>больших 500 000</strong>, таких что среди их натуральных "
        "делителей есть число, оканчивающееся на <strong>цифру 8</strong>, при этом этот "
        "делитель <strong>не равен 8</strong> и <strong>не равен самому числу</strong>.</p>"
        "<p>Укажите <strong>наименьшее</strong> из 5 найденных чисел.</p>"
        "<p>Введите число.</p>",
        "500002",
    )),

    # 11 — M(18) = 15 (EASY)
    (DIFF_EASY, [V_MFUNC], sa(
        "<p><strong>Задание 11.</strong> Напишите функцию <code>M(N)</code> — "
        "<strong>сумма двух наибольших различных натуральных делителей</strong> числа N, "
        "не считая самого числа (то есть из результата <code>get_div(N)</code>). "
        "Если у числа N меньше двух таких делителей, то <code>M(N) = 0</code>.</p>"
        "<p>Проверьте: чему равно <code>M(18)</code>?</p>"
        "<p><em>Подсказка: <code>get_div(18) = [2, 3, 6, 9]</code>; два наибольших — 9 и 6.</em></p>"
        "<p>Введите число.</p>",
        "15",
    )),

    # 12 — наименьшее > 10M с 0<M<10000 = 10000043 (HARD)
    (DIFF_HARD, [V_LARGE], sa(
        "<p><strong>Задание 12.</strong> Используя функцию <code>M(N)</code> из предыдущего "
        "задания, найдите 5 наименьших натуральных чисел, <strong>превышающих 10 000 000</strong>, "
        "для которых <code>0 &lt; M(N) &lt; 10 000</code>.</p>"
        "<p>Укажите <strong>наименьшее</strong> из 5 найденных чисел.</p>"
        "<p>Введите число.</p>",
        "10000043",
    )),

    # 13 — len(diff_multi(18)) = 2 (EASY)
    (DIFF_EASY, [V_PAIR], sa(
        "<p><strong>Задание 13.</strong> На основе функции <code>get_div</code> напишите "
        "новую функцию <code>diff_multi(x)</code>:</p>"
        "<ul>"
        "<li>Находим все возможные пары сомножителей <code>(a, b)</code> из "
        "<code>get_div(x)</code>, где <code>a &lt; b</code> и <code>a * b == x</code>.</li>"
        "<li>Для каждой пары вычисляем разность <code>b − a</code> и добавляем в список.</li>"
        "</ul>"
        "<p>Проверьте: сколько элементов содержит <code>diff_multi(18)</code>?</p>"
        "<p><em>Подсказка: <code>get_div(18) = [2, 3, 6, 9]</code>. "
        "Пары: (2,&nbsp;9)&nbsp;→&nbsp;7, (3,&nbsp;6)&nbsp;→&nbsp;3. Итого 2 элемента.</em></p>"
        "<p>Введите число.</p>",
        "2",
    )),

    # 14 — кол-во чисел [0..500] с ≥3 разностями ≤11 = 3 (HARD)
    (DIFF_HARD, [V_PAIR], sa(
        "<p><strong>Задание 14.</strong> Используя функцию <code>diff_multi(x)</code>, "
        "посчитайте количество чисел от 0 до 500, у которых <strong>не менее трёх "
        "элементов</strong> в списке разностей <strong>не превышают 11</strong>.</p>"
        "<p>Введите количество таких чисел.</p>",
        "3",
    )),

    # 15 — кол-во N=2^m*3^n в [200M;400M] (m чётн, n неч) = 4 (NORMAL)
    (DIFF_NORMAL, [V_POW], sa(
        "<p><strong>Задание 15.</strong> Найдите все натуральные числа N, принадлежащие "
        "отрезку [200&nbsp;000&nbsp;000;&nbsp;400&nbsp;000&nbsp;000], которые можно представить "
        "в виде <code>N = 2<sup>m</sup> · 3<sup>n</sup></code>, где <strong>m — чётное</strong>, "
        "<strong>n — нечётное</strong>. Запишите их в порядке возрастания.</p>"
        "<p><em>Подсказка: перебирайте m = 0, 2, 4, … и n = 1, 3, 5, …</em></p>"
        "<p>Сколько таких чисел существует? Введите количество.</p>",
        "4",
    )),

    # 16 — кол-во кратных 2023 в [2023; 2000000] = 988 (EASY)
    (DIFF_EASY, [V_INTRO], sa(
        "<p><strong>Задание 16.</strong> Сформируйте список чисел из диапазона "
        "[2023;&nbsp;2&nbsp;000&nbsp;000], <strong>кратных числу 2023</strong> "
        "(используйте <code>range</code> или генератор).</p>"
        "<p>Сколько таких чисел содержится в диапазоне?</p>"
        "<p>Введите число.</p>",
        "988",
    )),

    # 17 — наименьшее 7-значное по маске 1?2139*4 = 1021394 (NORMAL)
    (DIFF_NORMAL, [V_MASK1, V_MASK2], sa(
        "<p><strong>Задание 17.</strong> Назовём маской числа последовательность цифр "
        "с символами:</p>"
        "<ul>"
        "<li><strong>«?»</strong> — ровно одна произвольная цифра</li>"
        "<li><strong>«*»</strong> — любая последовательность цифр произвольной длины "
        "(в том числе пустая)</li>"
        "</ul>"
        "<p>Например, маске <code>123*4?5</code> соответствуют числа 123405 и 12300405.</p>"
        "<p>Напишите функцию <code>in_mask(N)</code>, проверяющую соответствует ли число N "
        "маске <code>1?2139*4</code>.</p>"
        "<p><em>Подсказка: можно перевести маску в регулярное выражение: "
        "«?» → <code>\\d</code>, «*» → <code>\\d*</code>.</em></p>"
        "<p>Найдите <strong>наименьшее 7-значное число</strong>, соответствующее этой маске.</p>"
        "<p>Введите число.</p>",
        "1021394",
    )),
]

assert len(SERIES) == N, f"Ожидается {N} заданий, SERIES={len(SERIES)}"


# ── сборка ────────────────────────────────────────────────────────────────────

def build_rows() -> list:
    rows = []
    for i, (diff_id, hints_video, (ttype, stem, options, extra)) in enumerate(
        SERIES, start=1
    ):
        ext_uid = f"lms:c152:vvod:{i:02d}"
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
            "FROM tasks WHERE external_uid LIKE 'lms:c152:vvod:%' "
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
            f"до было {before_cnt} заданий":           before_cnt == 107,
            f"итог {expected_after}":                   after_cnt == expected_after,
            f"позиции непрерывны 1..{expected_after}":  (
                pmin == 1 and pmax == expected_after
                and pdistinct == expected_after
            ),
            "сдвинуто 107 заданий":                    shifted == 107,
            f"вставлено {N} новых":                     len(new_rows) == N,
            f"новые на pos 1..{N}":                     (
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
