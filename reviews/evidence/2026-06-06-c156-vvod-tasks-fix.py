# -*- coding: utf-8 -*-
"""Курс 156 «Задание 5 ЕГЭ» — правки вводных (вспомогательных) заданий.

Порядок операций в одной транзакции:
  1. SET skip=true
  2. Сдвинуть pos >= 62 на +4  → дубль переезжает на 74, слоты 62-65 свободны
  3. Вставить 5_2..5_5 на pos 62-65
  4. Удалить дубль id=3452 (теперь на pos 74)
       → trigg reorder_tasks_after_delete закрывает пробел (75-151 → 74-150)
         и сбрасывает флаг в false
  5. SET skip=true (повторно — после сброса триггером)
  6. Обновить hints_video 5_1 (id=3240): добавить вторую ссылку
  7. Обновить hints_video 5_8 (id=3450): добавить вторую ссылку
  8. Обновить stem 5_6 (id=3451): добавить пример N=13 → 242

Итог: 147 - 1 дубль + 4 новых = 150 заданий, pos 1..150.
"""
import io, json, os, re, sys
import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID  = 156
COURSE_UID = "wp:zadanie-5-ege-analiz-algoritmov-dlya-ispolnitelej"
DIFF_NORM  = 3    # difficulty NORMAL

ID_5_1     = 3240   # 5_1  — остаётся
ID_5_1_DUP = 3452   # дубль 5_1 — удаляется
ID_5_8     = 3450   # 5_8
ID_5_6     = 3451   # 5_6

HINTS_5_1 = [
    "https://vk.com/video-53400615_456239363",
    "https://vk.com/video-53400615_456239650",
]
HINTS_5_8 = [
    "https://vk.com/video-53400615_456239652",
    "https://vk.com/video-53400615_456239763",
]

STEM_5_6_EXAMPLE = (
    "\n<p><strong>Например.</strong><br>"
    "Дано число N&nbsp;=&nbsp;13.<br>"
    "Восьмибитная двоичная запись числа N: <code>00001101</code>.<br>"
    "Все цифры заменяются на противоположные, новая запись: <code>11110010</code>.<br>"
    "Десятичное значение полученного числа: <strong>242</strong>.</p>"
)

# 4 новых задания — вставляются на pos 62-65
NEW_TASKS = [
    ("lms:c156:vvod:5_2", 62,
     "<p>Вспомогательное задание 5_2.</p>\n"
     "<p>Преобразуйте десятичные числа 45, 110, 2323, 3456 в двоичную форму "
     "и выведите результат.</p>"),
    ("lms:c156:vvod:5_3", 63,
     "<p>Вспомогательное задание 5_3.</p>\n"
     "<p>Посчитайте, сколько единиц содержится в каждом из двоичных чисел: "
     "45, 110, 2323, 3456.</p>"),
    ("lms:c156:vvod:5_4", 64,
     "<p>Вспомогательное задание 5_4.</p>\n"
     "<p>Дана строка S. Удалите её последний символ и допишите справа "
     "<code>'11'</code>.</p>"),
    ("lms:c156:vvod:5_5", 65,
     "<p>Вспомогательное задание 5_5.</p>\n"
     "<p>Дана строка S. Замените её последний символ на второй слева символ.</p>"),
]
N_NEW = len(NEW_TASKS)  # 4


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


def make_task_content(stem: str) -> dict:
    return {
        "code": None, "stem": stem, "tags": None,
        "type": "SA_COM", "media": None, "title": None, "prompt": None,
        "options": None, "has_hints": False, "course_uid": COURSE_UID,
        "hints_text": [], "hints_video": [], "difficulty_code": None,
    }


def make_solution_rules() -> dict:
    return {
        "max_score": 1,
        "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
        "auto_check": True, "text_answer": None,
        "scoring_mode": "all_or_nothing", "short_answer": None,
        "partial_rules": [], "correct_options": [],
        "custom_scoring_config": None, "manual_review_required": False,
    }


def skip_on(cur) -> None:
    cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")


def main() -> None:
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    try:
        skip_on(cur)

        # ── Снимок до ────────────────────────────────────────────────────────
        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s", (COURSE_ID,))
        before_cnt = cur.fetchone()[0]
        cur.execute("SELECT order_position FROM tasks WHERE id=%s", (ID_5_1,))
        pos_5_1_before = cur.fetchone()[0]
        cur.execute("SELECT task_content->>'stem' FROM tasks WHERE id=%s", (ID_5_6,))
        stem_5_6_before = cur.fetchone()[0]

        print(f"заданий в курсе {COURSE_ID} до: {before_cnt}")
        print(f"  5_1 (id={ID_5_1}) pos: {pos_5_1_before}")
        print(f"  5_6 (id={ID_5_6}) stem[-50:]: {stem_5_6_before[-50:]!r}")

        # ── 2. Сдвинуть pos >= 62 на +4 (дубль 70 → 74; слоты 62-65 пусты) ──
        cur.execute(
            "UPDATE tasks SET order_position = order_position + 4 "
            "WHERE course_id=%s AND order_position >= 62",
            (COURSE_ID,),
        )
        shifted = cur.rowcount
        print(f"\n2. Сдвинуто на +4: {shifted} строк")

        # ── 3. Вставить 4 новых задания на pos 62-65 ─────────────────────────
        inserted = 0
        for ext_uid, pos, stem in NEW_TASKS:
            tc = make_task_content(stem)
            sr = make_solution_rules()
            cur.execute(
                "INSERT INTO tasks "
                "(external_uid, max_score, task_content, course_id, difficulty_id, "
                "solution_rules, max_attempts, time_limit_sec, order_position) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ext_uid, 1, Json(tc), COURSE_ID, DIFF_NORM, Json(sr),
                 None, None, pos),
            )
            inserted += 1
        print(f"3. Вставлено новых заданий: {inserted}")

        # ── 4. Удалить дубль (теперь на pos 74) ──────────────────────────────
        #    Триггер reorder_tasks_after_delete сбросит флаг в false —
        #    поэтому сразу после удаления восстанавливаем skip.
        cur.execute("DELETE FROM tasks WHERE id=%s", (ID_5_1_DUP,))
        del_cnt = cur.rowcount
        print(f"4. Удалён дубль id={ID_5_1_DUP}: {del_cnt} строк")
        #    (триггер уже сработал и перенумеровал 75-151 → 74-150)

        # ── 5. Восстановить флаг (триггер сбросил его в false) ───────────────
        skip_on(cur)

        # ── 6. hints_video 5_1: добавить вторую ссылку ───────────────────────
        cur.execute(
            "UPDATE tasks "
            "SET task_content = jsonb_set(task_content, '{hints_video}', %s::jsonb) "
            "WHERE id=%s",
            (json.dumps(HINTS_5_1), ID_5_1),
        )
        print(f"6. hints_video 5_1 обновлён: {cur.rowcount} строк")

        # ── 7. hints_video 5_8: добавить вторую ссылку ───────────────────────
        cur.execute(
            "UPDATE tasks "
            "SET task_content = jsonb_set(task_content, '{hints_video}', %s::jsonb) "
            "WHERE id=%s",
            (json.dumps(HINTS_5_8), ID_5_8),
        )
        print(f"7. hints_video 5_8 обновлён: {cur.rowcount} строк")

        # ── 8. stem 5_6: добавить пример N=13 → 242 ─────────────────────────
        new_stem = stem_5_6_before + STEM_5_6_EXAMPLE
        cur.execute(
            "UPDATE tasks "
            "SET task_content = jsonb_set(task_content, '{stem}', to_jsonb(%s::text)) "
            "WHERE id=%s",
            (new_stem, ID_5_6),
        )
        print(f"8. stem 5_6 обновлён: {cur.rowcount} строк")

        # ── Самопроверка ──────────────────────────────────────────────────────
        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s", (COURSE_ID,))
        after_cnt = cur.fetchone()[0]

        cur.execute(
            "SELECT min(order_position), max(order_position), count(*), "
            "count(DISTINCT order_position) FROM tasks WHERE course_id=%s",
            (COURSE_ID,),
        )
        pmin, pmax, pcnt, pdistinct = cur.fetchone()

        cur.execute("SELECT count(*) FROM tasks WHERE id=%s", (ID_5_1_DUP,))
        dup_gone = cur.fetchone()[0] == 0

        cur.execute(
            "SELECT order_position, task_content->'hints_video' "
            "FROM tasks WHERE id=%s", (ID_5_1,))
        pos_5_1, hints_5_1 = cur.fetchone()

        cur.execute(
            "SELECT order_position, task_content->'hints_video' "
            "FROM tasks WHERE id=%s", (ID_5_8,))
        pos_5_8, hints_5_8 = cur.fetchone()

        cur.execute(
            "SELECT order_position, task_content->>'stem' "
            "FROM tasks WHERE id=%s", (ID_5_6,))
        pos_5_6, stem_5_6_after = cur.fetchone()

        cur.execute(
            "SELECT order_position, external_uid FROM tasks "
            "WHERE external_uid LIKE 'lms:c156:vvod:%' ORDER BY order_position",
        )
        new_rows = cur.fetchall()

        print(f"\n── состояние после ──────────────────────────────")
        print(f"заданий: {after_cnt}  (было {before_cnt})")
        print(f"order_position: min={pmin} max={pmax} count={pcnt} distinct={pdistinct}")
        print(f"  5_1  pos={pos_5_1},  hints({len(hints_5_1)}): {hints_5_1}")
        print(f"  5_8  pos={pos_5_8},  hints({len(hints_5_8)}): {hints_5_8}")
        print(f"  5_6  pos={pos_5_6},  '242' in stem: {'242' in stem_5_6_after}")
        print("  новые задания:")
        for pos, uid in new_rows:
            print(f"    pos={pos}  uid={uid}")

        checks = {
            "до было 147 заданий":        before_cnt == 147,
            "итог 147 - 1 + 4 = 150":     after_cnt == 150,
            "позиции непрерывны 1..150":  (pmin == 1 and pmax == 150
                                            and pdistinct == 150),
            "дубль удалён":               dup_gone,
            "5_1 на pos 61":              pos_5_1 == 61,
            "5_1 имеет 2 видеоподсказки": len(hints_5_1) == 2,
            "5_8 имеет 2 видеоподсказки": len(hints_5_8) == 2,
            "5_6 пример N=13→242 добавлен": "242" in stem_5_6_after,
            "4 новых задания вставлено":  len(new_rows) == N_NEW,
            "новые на pos 62-65":         [r[0] for r in new_rows] == [62, 63, 64, 65],
        }

        print("\n── проверки ──────────────────────────────────────")
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
