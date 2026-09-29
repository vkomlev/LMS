"""Отбор заданий банка ЕГЭ/ОГЭ для практикумов 7, 8, 9, 11 классов (tsk-1163). Только чтение."""
import json, re, sys
import psycopg2

sys.stdout.reconfigure(encoding="utf-8")
cfg = json.load(open(r"D:\Work\CreateCourses\.mcp.json", encoding="utf-8"))
dsn = next(a for a in cfg["mcpServers"]["learn_prod_db"]["args"] if a.startswith("postgres"))

# (ключ, глава, курс банка, подпись, фильтр по стему: include-regex или None)
# (ключ, класс-глава, курс банка, подпись, фильтр подтемы, разрешено программирование)
PLAN = [
    ("oge7", "7-2", 1152, "Задание 7 ОГЭ. Сетевые адреса", None, False),
    ("oge5", "8-3", 1129, "Задание 5 ОГЭ. Формальный исполнитель", None, False),
    ("oge6", "8-4", 1130, "Задание 6 ОГЭ. Программа с условным оператором", None, True),
    ("oge16", "8-4", 1181, "Задание 16 ОГЭ. Программа обработки последовательности", None, True),
    ("oge4", "9-2", 1128, "Задание 4 ОГЭ. Кратчайший путь по таблице", None, False),
    ("oge9", "9-2", 1154, "Задание 9 ОГЭ. Пути в графе", None, False),
    ("ege1", "9-2", 140, "Задание 1 ЕГЭ. Информационные модели", None, False),
    ("oge14", "9-3", 1179, "Задание 14 ОГЭ. Электронные таблицы", None, True),
    ("ege9", "9-3", 160, "Задание 9 ЕГЭ. Электронные таблицы", None, True),
    ("ege3", "11-1", 138, "Задание 3 ЕГЭ. Базы данных в электронных таблицах", None, True),
    ("ege5", "11-2", 156, "Задание 5 ЕГЭ. Анализ алгоритмов", None, True),
    ("ege12", "11-2", 163, "Задание 12 ЕГЭ. Машина Тьюринга", None, True),
    ("ege16", "11-2", 144, "Задание 16 ЕГЭ. Рекурсивные функции", None, True),
    ("ege17", "11-2", 145, "Задание 17 ЕГЭ. Обработка последовательностей", None, True),
    ("ege22", "11-3", 149, "Задание 22 ЕГЭ. Параллельные процессы", None, True),
    ("ege10n", "11-4", 139, "Задание 10 ЕГЭ. Маски подсети", None, False),
]
PROG = re.compile(r"python|питон|(напишите|составьте|запустите)\s+программ|программ\w*\s+на\s|\bprint\(|\bdef\s|\bfor\s+\w+\s+in\b|\bwhile\b|электронн\w* таблиц", re.I)
FOREIGN = {2938, 3474, 4570}  # чужие задания банка (tsk-1132)
TAKE = 10
# Отсев по вычитке методиста: огромные степени (решаются программой), решение в условии,
# поразрядная конъюнкция (в курсе нет).
EXCL = {
    "ege14": r"ричной записи числа|обозначают некоторые цифры|Операнды|обозначает некоторую цифру|записали в систем|выражени\w*:?\s*n\s*=|\d\s*\^?\{?\d{3,}\}?\s*[+\-–·*]|\d{3,}\s+\d{3,}",
    "ege15": r"поразрядн|&amp;|\&|Решаю|цикл",
}

SQL = """
SELECT id, difficulty_id, task_content->>'type', order_position,
       coalesce(task_content->>'stem',''), task_content::text, solution_rules, external_uid
FROM tasks WHERE is_active AND course_id=%s AND difficulty_id IN (1,2,3)
ORDER BY difficulty_id, order_position, id
"""

def gradable(sr) -> str:
    if not isinstance(sr, dict):
        return ""
    manual = sr.get("manual_review_required") is True
    acc = ((sr.get("short_answer") or {}).get("accepted_answers") or []) + ([1] if any(v for v in (sr.get("io_tests") or {}).values() if isinstance(v, list)) else [])
    opts = sr.get("correct_options") or []
    if acc or opts:
        return "manual" if manual else "auto"
    return "manual" if manual else ""

def strip(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[a-zA-Z/][^>]*>", " ", s)).replace("\xad", "").strip()

used: set[int] = set()
out = []
with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SET default_transaction_read_only = on")
    for key, ch, cid, title, inc, prog_ok in PLAN:
        cur.execute(SQL, (cid,))
        picked, warm, skipped = [], [], {}
        for tid, d, tp, op, stem, content, sr, uid in cur.fetchall():
            text = strip(stem)
            why = None
            if tid in FOREIGN or tid in used:
                why = "чужое/занято"
            elif not gradable(sr):
                why = "нет эталона"
            elif gradable(sr) == "manual" and sum(p["check"] == "manual" for p in picked) >= 1:
                why = "лимит ручных"
            elif not prog_ok and PROG.search(text):
                why = "программирование"
            elif inc and not re.search(inc, text, re.I):
                why = "не та подтема"
            elif key in EXCL and re.search(EXCL[key], text, re.I):
                why = "не для 10 класса"
            elif key == "ege7p" and re.search(r"пиксел|звук|дискретиз|аудио|фото|изображ|цвет", text, re.I):
                why = "не та подтема"
            if why:
                skipped[why] = skipped.get(why, 0) + 1
                continue
            (picked if d > 1 else warm).append({"id": tid, "d": d, "type": tp, "op": op, "uid": uid, "check": gradable(sr), "stem": text[:140]})
        picked = picked[:TAKE]
        if len(picked) < 8:
            picked = warm[: 8 - len(picked)] + picked
        used.update(p["id"] for p in picked)
        out.append({"key": key, "chapter": ch, "bank": cid, "title": title, "picked": picked, "skipped": skipped})
        print(f"гл.{ch} {key:6} банк {cid}: взято {len(picked)} "
              f"(лёгк {sum(p['d']==2 for p in picked)}, сред {sum(p['d']==3 for p in picked)}) отсев {skipped}")

json.dump(out, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
