# -*- coding: utf-8 -*-
"""Закрыть расхождения материалов навигатора для курсов 155 и 157.

Источник: nav_parser.py на navigator-po-zadaniyu-4-ege и navigator-po-zadaniyu-6-ege.

Курс 155:
- восстановить активные текстовые материалы навигатора:
  Вопросы по разделу 1-5, Мини практика 6-10, Мини практика после заданий.

Курс 157:
- убрать лишний материал "Что повторить из Python перед решением";
- исправить UID/req для "Лайфхаки ускорения рисования";
- восстановить материал "Подсчёт целочисленных точек: 2 подхода";
- деактивировать дублирующее видео id=652.
"""
import io
import os
import re
import sys

import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def load_dsn() -> str:
    if dsn := os.environ.get("LMS_DB_DSN"):
        return dsn
    env = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    with open(env, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("DATABASE_URL"):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)
    raise RuntimeError("DATABASE_URL не найден в .env")


def text_content(html: str) -> Json:
    return Json({"format": "html", "text": html})


C155 = 155
C157 = 157

C155_Q_HTML = """<h3 id="voprosy-po-teorii">🧠 Вопросы по теории</h3>
<ol>
<li><strong>Что такое кодирование и декодирование информации?</strong><br/>Объясните, зачем переводить данные с «человеческого языка» на двоичный код.</li>
<li><strong>Чем отличается равномерное кодирование от неравномерного?</strong><br/>Приведите пример алфавита из трёх символов и покажите различие.</li>
<li><strong>Что означает условие Фано и какую проблему оно решает?</strong><br/>Почему без этого условия возможна неоднозначная расшифровка?</li>
<li><strong>Можно ли составить неравномерный код без выполнения условия Фано,</strong> но при этом допускающий однозначное декодирование?<br/>(Подумайте, в каких случаях это возможно.)</li>
<li><strong>Как проверить выполнение условия Фано?</strong><br/>Опишите пошагово, что нужно сравнивать между кодами.</li>
</ol>"""

C155_MINI_6_10_HTML = """<h3 id="mini-zadaniya-dlya-trenirovki">🧩 Мини-задания для тренировки</h3>
<ol start="6">
<li><strong>Построение дерева:</strong><br/>Постройте дерево кодирования для символов: А — 0, Б — 10, В — 110, Г — 111. Проверяется ли условие Фано?</li>
<li><strong>Поиск ошибки:</strong><br/>Даны коды X — 0, Y — 10, Z — 101. Нарушено ли условие Фано? Как можно исправить?</li>
<li><strong>Мини-расшифровка:</strong><br/>Алфавит: A — 0, B — 10, C — 11. Расшифруйте сообщение: <code>0110110</code>.</li>
<li><strong>Дополни код:</strong><br/>Для букв К, Л, М заданы коды 00, 01, 10. Подберите коды для Н и П так, чтобы выполнялось условие Фано.</li>
<li><strong>Оптимизация длины:</strong><br/>Почему часто выгодно давать короткие коды символам, встречающимся чаще? Придумайте пример пяти символов с разной частотой и подходящими длинами кодов.</li>
</ol>"""

C155_AFTER_HTML = """<h2 id="mini-praktika-dlya-uchenikov">Мини-практика для учеников</h2>
<ol>
<li>Постройте дерево для кодов из первого примера самостоятельно. Проверьте, где появляются листья, а где — внутренние узлы.</li>
<li>Попробуйте добавить новую букву С, выбрав для неё код, который не нарушает условие Фано.</li>
<li>Для второго примера создайте таблицу всех допустимых кодов длиной ≤ 6, вычеркнув запрещённые префиксы, чтобы убедиться, что остаётся ровно 14 вариантов.</li>
</ol>"""

C157_POINTS_HTML = """<h2 id="podschyot-tselochislennyh-tochek-2-podhoda">Подсчёт целочисленных точек: 2 подхода</h2>
<p>Раздел навигатора: <a href="https://victor-komlev.ru/zadanie-6-ege-po-informatike-ispolnitel-cherepaha/#podschyot-tselochislennyh-tochek-2-podhoda" rel="noopener" target="_blank">Подсчёт целочисленных точек: 2 подхода</a>.</p>"""


def show_course(cur, course_id: int) -> None:
    cur.execute(
        """
        SELECT id, order_position, is_active, requirement_level, external_uid, title
        FROM materials
        WHERE course_id=%s
        ORDER BY order_position, id
        """,
        (course_id,),
    )
    print(f"\ncourse={course_id}")
    for row in cur.fetchall():
        print(" ", row)


def main() -> None:
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    checks: dict[str, bool] = {}

    try:
        cur.execute("SELECT set_config('app.skip_material_order_trigger', 'true', true)")

        print("Снимок ДО")
        show_course(cur, C155)
        show_course(cur, C157)

        print("\nКурс 155: временные UID для безопасной перестановки")
        cur.execute(
            """
            UPDATE materials
            SET external_uid = external_uid || ':tmp-codex-155'
            WHERE course_id=%s AND id IN (418,419)
            """,
            (C155,),
        )
        print("  temp ids 418/419:", cur.rowcount)
        checks["c155 temp 2"] = cur.rowcount == 2

        print("\nКурс 155: восстановление материалов")
        cur.execute(
            """
            UPDATE materials
            SET title=%s, content=%s, external_uid=%s,
                requirement_level='required', is_active=true, order_position=5
            WHERE id=419 AND course_id=%s
            """,
            (
                "Вопросы по разделу 1-5",
                text_content(C155_Q_HTML),
                "wp:mat:komlev:zadanie-4-ege-po-informatike-neravnomernoe-kodirovanie-i-uslovie-fano:3",
                C155,
            ),
        )
        print("  id=419 -> Вопросы:", cur.rowcount)
        checks["c155 id419 updated"] = cur.rowcount == 1

        cur.execute(
            """
            INSERT INTO materials (
                course_id, type, content, order_position, title, description,
                caption, is_active, external_uid, requirement_level
            )
            VALUES (%s, 'text', %s, 6, %s, NULL, NULL, true, %s, 'required')
            ON CONFLICT (course_id, external_uid) DO UPDATE
            SET title=EXCLUDED.title, content=EXCLUDED.content,
                order_position=EXCLUDED.order_position, is_active=true,
                requirement_level=EXCLUDED.requirement_level
            RETURNING id
            """,
            (
                C155,
                text_content(C155_MINI_6_10_HTML),
                "Мини практика 6-10",
                "wp:mat:komlev:zadanie-4-ege-po-informatike-neravnomernoe-kodirovanie-i-uslovie-fano:4",
            ),
        )
        c155_new_id = cur.fetchone()[0]
        print("  Мини практика 6-10 id:", c155_new_id)
        checks["c155 mini inserted"] = c155_new_id is not None

        cur.execute(
            """
            UPDATE materials
            SET title=%s, content=%s, external_uid=%s,
                requirement_level='skippable', is_active=true, order_position=7
            WHERE id=418 AND course_id=%s
            """,
            (
                "Мини практика после заданий",
                text_content(C155_AFTER_HTML),
                "wp:mat:komlev:zadanie-4-ege-po-informatike-neravnomernoe-kodirovanie-i-uslovie-fano:5",
                C155,
            ),
        )
        print("  id=418 -> Мини практика после заданий:", cur.rowcount)
        checks["c155 id418 updated"] = cur.rowcount == 1

        print("\nКурс 157: временные UID для безопасной перестановки")
        cur.execute(
            """
            UPDATE materials
            SET external_uid = external_uid || ':tmp-codex-157'
            WHERE course_id=%s AND id IN (429,430,434)
            """,
            (C157,),
        )
        print("  temp ids 429/430/434:", cur.rowcount)
        checks["c157 temp 3"] = cur.rowcount == 3

        print("\nКурс 157: выравнивание материалов")
        cur.execute(
            """
            UPDATE materials
            SET is_active=false, order_position=98, external_uid=%s
            WHERE id=429 AND course_id=%s
            """,
            (
                "wp:mat:komlev:zadanie-6-ege-po-informatike-ispolnitel-cherepaha:python-repeat-old",
                C157,
            ),
        )
        print("  id=429 deactivate:", cur.rowcount)
        checks["c157 id429 inactive"] = cur.rowcount == 1

        cur.execute(
            """
            UPDATE materials
            SET external_uid=%s, requirement_level='required',
                is_active=true, order_position=3
            WHERE id=430 AND course_id=%s AND title='Лайфхаки ускорения рисования'
            """,
            (
                "wp:mat:komlev:zadanie-6-ege-po-informatike-ispolnitel-cherepaha:3",
                C157,
            ),
        )
        print("  id=430 -> Лайфхаки required uid=:3:", cur.rowcount)
        checks["c157 id430 fixed"] = cur.rowcount == 1

        cur.execute(
            """
            UPDATE materials
            SET title=%s, type='text', content=%s, external_uid=%s,
                requirement_level='skippable', is_active=true, order_position=4
            WHERE id=434 AND course_id=%s
            """,
            (
                "Подсчёт целочисленных точек: 2 подхода",
                text_content(C157_POINTS_HTML),
                "wp:mat:komlev:zadanie-6-ege-po-informatike-ispolnitel-cherepaha:4",
                C157,
            ),
        )
        print("  id=434 -> Подсчёт:", cur.rowcount)
        checks["c157 id434 repurposed"] = cur.rowcount == 1

        cur.execute(
            """
            UPDATE materials
            SET is_active=false, order_position=99
            WHERE id=652 AND course_id=%s
            """,
            (C157,),
        )
        print("  id=652 duplicate video inactive:", cur.rowcount)
        checks["c157 id652 inactive"] = cur.rowcount == 1

        print("\nПереупорядочивание активных материалов")
        desired_orders = {
            C155: [415, 416, 417, 639, 640, 419, c155_new_id, 418],
            C157: [426, 427, 428, 430, 434, 431, 432, 433, 650, 651],
        }
        for course_id, ids in desired_orders.items():
            cur.execute(
                "UPDATE materials SET order_position=order_position+1000 WHERE course_id=%s AND is_active=true",
                (course_id,),
            )
            for pos, mat_id in enumerate(ids):
                cur.execute(
                    "UPDATE materials SET order_position=%s WHERE id=%s AND course_id=%s AND is_active=true",
                    (pos, mat_id, course_id),
                )
            print(f"  course={course_id}: active={len(ids)} positions=0-{len(ids)-1}")

        print("\nСнимок ПОСЛЕ")
        show_course(cur, C155)
        show_course(cur, C157)

        cur.execute(
            """
            SELECT count(*)
            FROM materials
            WHERE course_id=%s AND is_active=true
              AND external_uid IN (
                'wp:mat:komlev:zadanie-4-ege-po-informatike-neravnomernoe-kodirovanie-i-uslovie-fano:3',
                'wp:mat:komlev:zadanie-4-ege-po-informatike-neravnomernoe-kodirovanie-i-uslovie-fano:4',
                'wp:mat:komlev:zadanie-4-ege-po-informatike-neravnomernoe-kodirovanie-i-uslovie-fano:5'
              )
            """,
            (C155,),
        )
        checks["c155 nav text 3 active"] = cur.fetchone()[0] == 3

        cur.execute(
            """
            SELECT count(*)
            FROM materials
            WHERE course_id=%s AND is_active=true
              AND external_uid IN (
                'wp:mat:komlev:zadanie-6-ege-po-informatike-ispolnitel-cherepaha:3',
                'wp:mat:komlev:zadanie-6-ege-po-informatike-ispolnitel-cherepaha:4'
              )
            """,
            (C157,),
        )
        checks["c157 nav 3-4 active"] = cur.fetchone()[0] == 2

        print("\nПроверки")
        all_ok = True
        for name, ok in checks.items():
            print(f"  [{'OK' if ok else 'FAIL'}] {name}")
            all_ok = all_ok and ok

        if all_ok and apply:
            conn.commit()
            print("\nРЕЗУЛЬТАТ: COMMIT.")
        elif all_ok:
            conn.rollback()
            print("\nРЕЗУЛЬТАТ: DRY-RUN успешен. Запусти с --apply.")
        else:
            conn.rollback()
            print("\nРЕЗУЛЬТАТ: проверки не пройдены, ROLLBACK.")
            sys.exit(1)
    except Exception:
        conn.rollback()
        print("\nРЕЗУЛЬТАТ: ROLLBACK.")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
