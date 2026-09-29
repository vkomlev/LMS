"""Отбор заданий банка ЕГЭ/ОГЭ для практикума курса 1009 (tsk-1161). Только чтение."""
import json, re, sys
import psycopg2

sys.stdout.reconfigure(encoding="utf-8")
cfg = json.load(open(r"D:\Work\CreateCourses\.mcp.json", encoding="utf-8"))
dsn = next(a for a in cfg["mcpServers"]["learn_prod_db"]["args"] if a.startswith("postgres"))

# (ключ, глава, курс банка, подпись, фильтр по стему: include-regex или None)
PLAN = [
    ("ege11", 1, 162, "Задание 11 ЕГЭ. Объём информации", None),
    ("ege7p", 1, 158, "Задание 7 ЕГЭ. Передача информации", r"канал|скорост|переда"),
    ("oge12", 2, 1164, "Задание 12 ОГЭ. Маски имён файлов", None),
    ("oge11", 2, 1163, "Задание 11 ОГЭ. Поиск файла по содержимому", None),
    ("oge10", 3, 1162, "Задание 10 ОГЭ. Системы счисления", None),
    ("ege14", 3, 142, "Задание 14 ЕГЭ. Системы счисления", None),
    ("oge1", 3, 1111, "Задание 1 ОГЭ. Кодирование текста", None),
    ("oge2", 3, 1112, "Задание 2 ОГЭ. Декодирование, условие Фано", None),
    ("ege4", 3, 155, "Задание 4 ЕГЭ. Условие Фано", None),
    ("ege7g", 3, 158, "Задание 7 ЕГЭ. Кодирование графики и звука", r"пиксел|цвет|звук|дискретиз|аудио|фото|изображ"),
    ("oge8", 4, 1153, "Задание 8 ОГЭ. Поисковые запросы и множества", None),
    ("oge3", 4, 1120, "Задание 3 ОГЭ. Значение логического выражения", None),
    ("ege2", 4, 148, "Задание 2 ЕГЭ. Таблицы истинности", None),
    ("ege15", 4, 143, "Задание 15 ЕГЭ. Логические операции", None),
    ("oge13", 5, 1178, "Задание 13 ОГЭ. Текстовый документ и презентация", None),
    ("ege10old", 5, 141, "Поиск информации в текстовом документе (ЕГЭ до 2027)", None),
]
PROG = re.compile(r"python|питон|(напишите|составьте|запустите)\s+программ|программ\w*\s+на\s|\bprint\(|\bdef\s|\bfor\s+\w+\s+in\b|\bwhile\b|электронн\w* таблиц", re.I)
FOREIGN = {2938, 3474}  # чужие задания банка (tsk-1132)
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
    acc = (sr.get("short_answer") or {}).get("accepted_answers") or []
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
    for key, ch, cid, title, inc in PLAN:
        cur.execute(SQL, (cid,))
        picked, warm, skipped = [], [], {}
        for tid, d, tp, op, stem, content, sr, uid in cur.fetchall():
            text = strip(stem)
            why = None
            if tid in FOREIGN or tid in used:
                why = "чужое/занято"
            elif not gradable(sr):
                why = "нет эталона"
            elif key != "oge13" and gradable(sr) != "auto":
                why = "ручная"
            elif PROG.search(text):
                why = "программирование"
            elif inc and not re.search(inc, text, re.I):
                why = "не та подтема"
            elif key in EXCL and re.search(EXCL[key], text, re.I):
                why = "не для 10 класса"
            elif key == "oge13" and gradable(sr) == "manual" and sum(p["check"] == "manual" for p in picked) >= 1:
                why = "лимит ручных"
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
