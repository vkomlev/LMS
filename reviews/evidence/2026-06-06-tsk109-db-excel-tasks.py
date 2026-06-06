# -*- coding: utf-8 -*-
"""tsk-109 итерация 3: материал 323 «Мини-практика для ученика» (курс 138,
«ЕГЭ Задание №3. Базы данных в Excel») -> 10 авто-проверяемых заданий SC/SA_COM
+ гашение материала.

Данные вычислены из файла 3.ods (3.zip):
  Движение_товаров + Товар + Магазин, 2273 строк, 16 магазинов, 65 товаров.
Файл: https://victor-komlev.ru/wp-content/uploads/2026/06/3.zip
"""
import io, os, re, sys
import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID    = 138
MATERIAL_ID  = 323
COURSE_UID   = "wp:ege-po-informatike-zadanie-3-bazy-dannyh-v-excel"
FILE_URL     = "https://victor-komlev.ru/wp-content/uploads/2026/06/3.zip"
N            = 10


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


def sc(stem, options, correct):
    return ("SC", stem, options, {"correct_options": correct, "short_answer": None})


def sa(stem, value, extra_values=None):
    accepted = [{"score": 1, "value": value}]
    if extra_values:
        for v in extra_values:
            accepted.append({"score": 1, "value": v})
    short = {
        "regex": None,
        "use_regex": False,
        "normalization": ["trim", "lower"],
        "accepted_answers": accepted,
    }
    return ("SA_COM", stem, None, {"correct_options": [], "short_answer": short})


FILE_NOTE = (
    f'<p><strong>Файл:</strong> <a href="{FILE_URL}">скачать 3.zip</a> — '
    'три листа: <em>Движение_товаров</em>, <em>Товар</em>, <em>Магазин</em>.</p>'
)


SERIES = [
    # (difficulty_id, builder)
    (2, sc(
        FILE_NOTE +
        "<p>Вы хотите добавить на лист <em>Движение_товаров</em> столбец "
        "«Кол-во в упаковке» с помощью функции ВПР (справочник — лист <em>Товар</em>). "
        "Какой столбец является общим ключом между двумя листами?</p>",
        [opt("A", "Артикул"),
         opt("B", "ID операции"),
         opt("C", "ID магазина"),
         opt("D", "Наименование товара")],
        ["A"])),

    (2, sa(
        FILE_NOTE +
        "<p>Откройте лист <em>Товар</em>. Чему равно «Количество в упаковке» "
        "для артикула <strong>47</strong> («Кофе в зёрнах»)? "
        "Введите число (разделитель — запятая, если нужно).</p>",
        "0,5", ["0.5"])),

    (2, sc(
        FILE_NOTE +
        "<p>Добавьте столбец «Сумма» = «Количество упаковок» × «Цена» на лист "
        "<em>Движение_товаров</em>. Операции какого типа нужно учитывать при расчёте "
        "выручки магазинов?</p>",
        [opt("A", "Поступление"),
         opt("B", "Продажа"),
         opt("C", "Оба типа операций"),
         opt("D", "Возврат")],
        ["B"])),

    (3, sa(
        FILE_NOTE +
        "<p>Посчитайте суммарную выручку по всем магазинам: "
        "СУММ столбца «Сумма» (только строки с типом операции «<strong>Продажа</strong>»). "
        "Введите результат в рублях, целое число.</p>",
        "7875283")),

    (3, sc(
        FILE_NOTE +
        "<p>Постройте сводную таблицу «Выручка по магазинам» "
        "(строки — ID магазина, значения — сумма поля «Сумма», тип «Продажа»). "
        "Отсортируйте по убыванию. "
        "Какой магазин занимает <strong>1-е место</strong>?</p>",
        [opt("A", "M1"),
         opt("B", "M5"),
         opt("C", "M10"),
         opt("D", "M15")],
        ["C"])),

    (3, sa(
        FILE_NOTE +
        "<p>Чему равна суммарная выручка магазина <strong>M10</strong> "
        "(руб., целое число)?</p>",
        "613243")),

    (3, sa(
        FILE_NOTE +
        "<p>В той же сводной таблице «Выручка по магазинам» найдите магазин "
        "с <strong>наименьшей</strong> суммарной выручкой. Введите его ID.</p>",
        "m9", ["M9"])),

    (3, sa(
        FILE_NOTE +
        "<p>Расширьте сводную таблицу: добавьте артикулы (товары) как строки, "
        "магазины — как столбцы. Найдите <strong>товар-лидер</strong> по выручке "
        "в магазине <strong>M5</strong>. Введите точное наименование товара.</p>",
        "кофе растворимый", ["Кофе растворимый"])),

    (3, sa(
        FILE_NOTE +
        "<p>Для сводной с товарами и магазинами выберите «Показать значения как → "
        "% от итога по столбцу». Найдите долю товара-лидера в суммарной выручке "
        "магазина M5 (%). Введите значение с двумя знаками после запятой "
        "(разделитель — запятая или точка).</p>",
        "5.71", ["5,71"])),

    (3, sa(
        FILE_NOTE +
        "<p>Используя сводную таблицу «Выручка по магазинам», посчитайте суммарную "
        "выручку всех магазинов <strong>Октябрьского района</strong> "
        "(ID: M1, M5, M6, M10, M15). "
        "Введите результат в рублях, целое число.</p>",
        "2964510")),
]


def build_rows():
    rows = []
    for i, (difficulty_id, (ttype, stem, options, extra)) in enumerate(SERIES, start=1):
        ext_uid = f"lms:tsk109:c138:{i:02d}"
        task_content = {
            "code": None,
            "stem": stem,
            "tags": None,
            "type": ttype,
            "media": None,
            "title": None,
            "prompt": None,
            "options": options,
            "has_hints": False,
            "course_uid": COURSE_UID,
            "hints_text": [],
            "hints_video": [],
            "difficulty_code": None,
        }
        solution_rules = {
            "max_score": 1,
            "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
            "auto_check": True,
            "text_answer": None,
            "scoring_mode": "all_or_nothing",
            "short_answer": extra["short_answer"],
            "partial_rules": [],
            "correct_options": extra["correct_options"],
            "custom_scoring_config": None,
            "manual_review_required": False,
        }
        rows.append((ext_uid, difficulty_id, i, task_content, solution_rules))
    return rows


def main() -> None:
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")

        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s", (COURSE_ID,))
        before_cnt = cur.fetchone()[0]
        cur.execute("SELECT is_active FROM materials WHERE id=%s", (MATERIAL_ID,))
        mat_before = cur.fetchone()[0]
        print(f"заданий в курсе {COURSE_ID} до: {before_cnt}; "
              f"материал {MATERIAL_ID} is_active до: {mat_before}")

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

        cur.execute("UPDATE materials SET is_active=false WHERE id=%s", (MATERIAL_ID,))
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
            "AND external_uid LIKE 'lms:tsk109:c138:%%'", (COURSE_ID,))
        series_cnt = cur.fetchone()[0]
        cur.execute(
            "SELECT order_position, task_content->>'type', "
            "task_content->>'stem' FROM tasks "
            "WHERE external_uid LIKE 'lms:tsk109:c138:%%' ORDER BY order_position")
        preview = cur.fetchall()
        cur.execute("SELECT is_active FROM materials WHERE id=%s", (MATERIAL_ID,))
        mat_after = cur.fetchone()[0]

        print(f"сдвинуто заданий: {shifted}; вставлено серии: {series_cnt}")
        print(f"заданий в курсе после: {after_cnt}")
        print(f"order_position: min={pmin} max={pmax} count={pcnt} distinct={pdistinct}")
        print(f"материал {MATERIAL_ID} is_active после: {mat_after} (строк {mat_upd})")
        print("--- серия (позиция / тип / начало stem) ---")
        for pos, typ, stem in preview:
            print(f"  {pos:>2}  {typ:<7} {stem[:70]}")

        checks = {
            "вставлено ровно N": series_cnt == N,
            "итог = было + N": after_cnt == before_cnt + N,
            "позиции непрерывны 1..count": (pmin == 1 and pmax == pcnt
                                            and pdistinct == pcnt),
            "серия на позициях 1..N": [p[0] for p in preview] == list(range(1, N + 1)),
            "материал погашен": mat_after is False and mat_upd == 1,
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
            print("РЕЗУЛЬТАТ: проверки пройдены (DRY-RUN), ROLLBACK. "
                  "Запусти с --apply для записи.")
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
