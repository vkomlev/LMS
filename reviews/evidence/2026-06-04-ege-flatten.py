# -*- coding: utf-8 -*-
"""Сделать плоской иерархию подкурсов курса ЕГЭ (id 112).

Слияние 25 навигаторов (113-137) с их курсами-заданиями + исправление
ошибки 9/12 (курс 160 возвращается в задание 9 и сливается с навигатором 121).
Все изменения в одной транзакции; перед мутацией пишется SQL отката.
"""
import json
import os
import sys
import io
import datetime as dt
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Строка подключения — из env (LMS_DB_DSN), без секретов в коде.
# Локально: задать LMS_DB_DSN или положиться на PGPASSWORD/.pgpass.
DSN = os.environ.get("LMS_DB_DSN", "host=localhost port=5432 dbname=Learn user=postgres")
ROOT = 112
NAV_MIN, NAV_MAX = 113, 137
ROLLBACK_FILE = r"D:\Work\LMS\reviews\evidence\2026-06-04-ege-flatten-rollback.sql"

# (навигатор, курс-задание, order_number навигатора под корнем)
PAIRS = [
    (113, 140, 2), (114, 148, 3), (115, 138, 4), (116, 155, 5), (117, 156, 6),
    (118, 157, 7), (119, 158, 8), (120, 159, 9), (121, 160, 10), (122, 141, 11),
    (123, 162, 12), (124, 163, 13), (125, 139, 14), (126, 142, 15), (127, 143, 16),
    (128, 144, 17), (129, 145, 18), (130, 146, 19), (131, 147, 20), (132, 149, 21),
    (133, 150, 22), (134, 151, 23), (135, 152, 24), (136, 153, 25), (137, 154, 26),
]
# (ребёнок-довесок, старый родитель-навигатор, новый родитель-задание)
EXTRAS = [(165, 118, 157), (161, 121, 160), (164, 121, 160)]

AFFECTED_LO, AFFECTED_HI = 113, 165


def main() -> None:
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        # ---- 0. Снимок состояния для отката ----
        cur.execute(
            "SELECT id, title, access_level::text, description, is_required, "
            "course_uid, is_public_demo, created_at FROM courses "
            "WHERE id BETWEEN %s AND %s ORDER BY id", (NAV_MIN, NAV_MAX))
        nav_rows = cur.fetchall()
        cur.execute(
            "SELECT course_id, parent_course_id, order_number FROM course_parents "
            "WHERE course_id BETWEEN %s AND %s ORDER BY course_id",
            (AFFECTED_LO, AFFECTED_HI))
        cp_rows = cur.fetchall()
        cur.execute(
            "SELECT id, course_id, order_position FROM materials "
            "WHERE course_id BETWEEN %s AND %s ORDER BY id",
            (AFFECTED_LO, AFFECTED_HI))
        mat_rows = cur.fetchall()

        write_rollback(nav_rows, cp_rows, mat_rows)
        print(f"Снимок отката записан: {ROLLBACK_FILE}")
        print(f"  навигаторов: {len(nav_rows)}, course_parents: {len(cp_rows)}, "
              f"materials: {len(mat_rows)}")

        # ---- сумма материалов до (по парам) для верификации ----
        cur.execute(
            "SELECT count(*) FROM materials WHERE course_id BETWEEN %s AND %s",
            (AFFECTED_LO, AFFECTED_HI))
        total_mat_before = cur.fetchone()[0]

        # Отключаем штатные триггеры нормализации порядка на время массовых
        # правок (каскадно двигают соседние строки и конфликтуют с bulk-update).
        # Порядок выставляем/нормализуем вручную ниже.
        cur.execute("SET LOCAL app.skip_material_order_trigger = 'true'")
        cur.execute("SET LOCAL app.skip_course_parent_order_trigger = 'true'")
        # AFTER DELETE триггер пересчёта order_number не имеет флага-предохранителя
        # и конфликтует при каскадном удалении 25 строк-сестёр. Отключаем его
        # на транзакцию (DDL транзакционен, откатится при ROLLBACK).
        cur.execute("ALTER TABLE course_parents "
                    "DISABLE TRIGGER trg_reorder_course_parents_after_delete")

        # ---- 1. Перепривязка довесков ----
        for child, oldp, newp in EXTRAS:
            cur.execute(
                "UPDATE course_parents SET parent_course_id=%s "
                "WHERE course_id=%s AND parent_course_id=%s", (newp, child, oldp))
            assert cur.rowcount == 1, f"довесок {child}: rowcount={cur.rowcount}"

        # ---- 2. Подъём курсов-заданий на корень 112 ----
        for nav, zad, order_no in PAIRS:
            cur.execute(
                "UPDATE course_parents SET parent_course_id=%s, order_number=%s "
                "WHERE course_id=%s", (ROOT, order_no, zad))
            assert cur.rowcount == 1, f"задание {zad}: rowcount={cur.rowcount}"

        # ---- 3. Перенос материалов навигатора в курс-задание ----
        # Материалы навигатора должны идти первыми. Триггер отключён, поэтому
        # отодвигаем материалы задания на +1000, переносим материалы навигатора,
        # затем нормализуем порядок (0-based, непрерывно) по всем заданиям.
        zad_ids = [zad for _, zad, _ in PAIRS]
        for nav, zad, _ in PAIRS:
            cur.execute("SELECT count(*) FROM materials WHERE course_id=%s", (nav,))
            if cur.fetchone()[0] == 0:
                continue
            cur.execute(
                "UPDATE materials SET order_position = order_position + 1000 "
                "WHERE course_id=%s", (zad,))
            cur.execute(
                "UPDATE materials SET course_id=%s WHERE course_id=%s", (zad, nav))
        cur.execute(
            "UPDATE materials m SET order_position = rn.np "
            "FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY course_id "
            "      ORDER BY order_position NULLS LAST, id)::integer - 1 AS np "
            "      FROM materials WHERE course_id = ANY(%s)) rn "
            "WHERE m.id = rn.id AND m.order_position IS DISTINCT FROM rn.np",
            (zad_ids,))

        # ---- 4. Удаление навигаторов ----
        cur.execute("DELETE FROM courses WHERE id BETWEEN %s AND %s",
                    (NAV_MIN, NAV_MAX))
        deleted = cur.rowcount

        # ---- 5. Верификация ----
        ok = True
        cur.execute("SELECT count(*) FROM courses WHERE id BETWEEN %s AND %s",
                    (NAV_MIN, NAV_MAX))
        navs_left = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM course_parents WHERE parent_course_id=%s",
                    (ROOT,))
        children_root = cur.fetchone()[0]

        cur.execute(
            "SELECT count(*) FROM materials WHERE course_id BETWEEN %s AND %s",
            (AFFECTED_LO, AFFECTED_HI))
        total_mat_after = cur.fetchone()[0]

        # каждое задание под корнем с правильным order
        cur.execute(
            "SELECT course_id, order_number FROM course_parents "
            "WHERE parent_course_id=%s ORDER BY order_number", (ROOT,))
        root_children = cur.fetchall()
        expected = {zad: order_no for _, zad, order_no in PAIRS}
        got = {cid: onum for cid, onum in root_children}

        # довески
        cur.execute("SELECT course_id, parent_course_id FROM course_parents "
                    "WHERE course_id IN (165,161,164) ORDER BY course_id")
        extras_after = dict(cur.fetchall())

        print("\n=== ВЕРИФИКАЦИЯ ===")
        print(f"навигаторов осталось (ожид. 0): {navs_left}")
        print(f"детей у корня 112 (ожид. 25): {children_root}")
        print(f"удалено курсов (ожид. 25): {deleted}")
        print(f"материалов в наборе до/после (должно совпасть): "
              f"{total_mat_before}/{total_mat_after}")
        print(f"довески 165->{extras_after.get(165)} (ожид 157), "
              f"161->{extras_after.get(161)} (ожид 160), "
              f"164->{extras_after.get(164)} (ожид 160)")

        if navs_left != 0:
            ok = False; print("FAIL: навигаторы не удалены")
        if children_root != 25:
            ok = False; print("FAIL: детей у корня не 25")
        if deleted != 25:
            ok = False; print("FAIL: удалено не 25 курсов")
        if total_mat_before != total_mat_after:
            ok = False; print("FAIL: число материалов изменилось")
        for zad, order_no in expected.items():
            if got.get(zad) != order_no:
                ok = False
                print(f"FAIL: задание {zad} ожидался order {order_no}, "
                      f"факт {got.get(zad)}")
        if extras_after.get(165) != 157 or extras_after.get(161) != 160 \
                or extras_after.get(164) != 160:
            ok = False; print("FAIL: довески перепривязаны неверно")

        # Возвращаем триггер пересчёта order_number
        cur.execute("ALTER TABLE course_parents "
                    "ENABLE TRIGGER trg_reorder_course_parents_after_delete")

        if ok:
            conn.commit()
            print("\nРЕЗУЛЬТАТ: все проверки пройдены, COMMIT выполнен.")
        else:
            conn.rollback()
            print("\nРЕЗУЛЬТАТ: проверки НЕ пройдены, ROLLBACK. БД не изменена.")
            sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"\nОШИБКА: {exc!r}. ROLLBACK. БД не изменена.")
        raise
    finally:
        cur.close()
        conn.close()


def write_rollback(nav_rows, cp_rows, mat_rows) -> None:
    """Сформировать SQL для восстановления исходного состояния."""
    lines = [
        "-- Rollback snapshot ЕГЭ flatten, " + dt.datetime.now().isoformat(),
        "-- Применять при необходимости отката внутри BEGIN;...COMMIT;",
        "BEGIN;",
        "",
        "-- 1. Восстановить удалённые курсы-навигаторы",
    ]
    for (cid, title, acc, descr, isreq, uid, demo, created) in nav_rows:
        lines.append(
            "INSERT INTO courses (id, title, access_level, description, "
            "created_at, is_required, course_uid, is_public_demo) VALUES ("
            f"{cid}, {sql_str(title)}, {sql_str(acc)}::access_level, "
            f"{sql_str(descr)}, {sql_str(str(created))}::timestamptz, "
            f"{str(isreq).lower()}, {sql_str(uid)}, {str(demo).lower()}) "
            "ON CONFLICT (id) DO NOTHING;")
    lines += ["", "-- 2. Восстановить привязку материалов (course_id, order_position)"]
    for (mid, cid, pos) in mat_rows:
        pos_sql = "NULL" if pos is None else str(pos)
        lines.append(
            f"UPDATE materials SET course_id={cid}, order_position={pos_sql} "
            f"WHERE id={mid};")
    lines += ["", "-- 3. Восстановить course_parents",
              "DELETE FROM course_parents WHERE course_id BETWEEN 113 AND 165;"]
    for (cid, pid, onum) in cp_rows:
        onum_sql = "NULL" if onum is None else str(onum)
        lines.append(
            "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
            f"VALUES ({cid}, {pid}, {onum_sql});")
    lines += ["", "-- COMMIT;  -- снять комментарий после проверки", ""]
    with open(ROLLBACK_FILE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def sql_str(val) -> str:
    if val is None:
        return "NULL"
    return "'" + str(val).replace("'", "''") + "'"


if __name__ == "__main__":
    main()
