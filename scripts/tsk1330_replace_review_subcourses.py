"""tsk-1330: заменить в курсе повторения 2106 подкурсы мехатроники на новые задания.

Снимает связи 863/864/867 -> 2106 (сами курсы и сдачи ученика не трогаются,
решённое остаётся историей — решение оператора 12.10), создаёт три подкурса
«разбор кода» (числа, строки, условия) с 22 заданиями и вешает их под 2106
перед 986. Под 88 не вешаются: от 88 зависит замок 112 у ~70 учеников.
Ответы посчитаны запуском кода (docs/qa/2026-10-12-tsk1330-zadaniya-povtoreniya.md).

Запуск: python scripts/tsk1330_replace_review_subcourses.py [--apply]
Без --apply — прогон с откатом (dry-run).
"""
import asyncio
import html
import json
import logging
import sys
from pathlib import Path
from typing import Any

import asyncpg

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tsk1330")

project_root = Path(__file__).resolve().parent.parent
ROOT = 2106
USER_ID = 4569
DROP = [863, 864, 867]
KEEP_LAST = 986
DIFF = {"EASY": 2, "NORMAL": 3, "HARD": 4}
NORM = ["trim", "collapse_spaces"]
DESCRIPTION = "Повторение: предскажи вывод, найди ошибку, напиши программу. Решай сам, без ИИ."

# (ключ, тип, сложность, заголовок, текст до кода, код | None, вопрос, ответ | варианты, подсказка)
SUBCOURSES: list[dict[str, Any]] = [
    {"uid": "ind:tsk1330-chisla", "title": "Числа: разбор кода", "tasks": [
        ("N1", "SA", "EASY", "Целая часть и остаток", "", "a = 17\nb = 5\nprint(a // b, a % b)",
         "Что выведет программа? Впиши через пробел.", "3 2",
         "// — сколько раз 5 целиком помещается в 17, % — что осталось."),
        ("N2", "SA", "NORMAL", "Деление отрицательного числа", "", "x = -7\nprint(x // 2, x % 2)",
         "Что выведет программа? Впиши через пробел.", "-4 1",
         "// округляет вниз, к меньшему числу. Остаток в Python того же знака, что и делитель."),
        ("N3", "SC", "NORMAL", "Найди ошибку в сумме цифр",
         "Программа должна считать сумму цифр трёхзначного числа, но для 472 выводит 53, а не 13.",
         "n = 472\ns = n // 100 + n // 10 + n % 10\nprint(s)",
         "Чем заменить n // 10, чтобы программа стала верной?",
         [("a", "n // 10 % 10", True), ("b", "n % 100", False),
          ("c", "n // 10 // 10", False), ("d", "n % 10 // 10", False)],
         "Посчитай руками, чему равно 472 // 10. Это ещё не одна цифра."),
        ("N4", "SA", "NORMAL", "Алгоритм Евклида по шагам", "",
         "a, b = 84, 36\na, b = b, a % b\nprint(a, b)\na, b = b, a % b\nprint(a, b)",
         "Что выведет ВТОРАЯ строка print? Впиши через пробел.", "12 0",
         "Сначала посчитай 84 % 36, затем повтори шаг с новой парой."),
        ("N5", "SA", "EASY", "Модуль разности", "", "print(abs(4 - 9) - abs(9 - 4), abs(4 - 9) * 2)",
         "Что выведет программа? Впиши через пробел.", "0 10", "abs убирает минус: abs(-5) равно 5."),
        ("N6", "SA_COM", "NORMAL", "Первая и последняя цифра",
         "Напиши программу: она получает трёхзначное число n и выводит модуль разности его первой и последней цифры.",
         None, "Запусти программу для n = 829 и впиши результат. Код вставь в комментарий к ответу.", "1",
         "Первая цифра — n // 100, последняя — n % 10."),
        ("N7", "SA", "HARD", "Деление и округление", "", "print(7 / 2, 7 // 2, round(2.5), round(3.5))",
         "Что выведет программа? Впиши через пробел.", "3.5 3 2 4",
         "round округляет половину к ближайшему ЧЁТНОМУ числу."),
    ]},
    {"uid": "ind:tsk1330-stroki", "title": "Строки: разбор кода", "tasks": [
        ("S1", "SA", "EASY", "Подсчёт подстроки", "", "s = 'мама мыла раму'\nprint(s.count('ма'))",
         "Что выведет программа?", "2", "Найди все «ма» глазами. В «раму» сочетания «ма» нет."),
        ("S2", "SA", "NORMAL", "count и пересечения", "", "print('ababa'.count('aba'))",
         "Что выведет программа?", "1", "count не считает вхождения, которые налезают друг на друга."),
        ("S3", "SA", "NORMAL", "replace с ограничением", "", "print('10-20-30'.replace('-', '', 1))",
         "Что выведет программа?", "1020-30", "Третий аргумент — сколько замен сделать, не больше."),
        ("S4", "SA", "NORMAL", "Срез с шагом", "", "s = 'Python'\nprint(s[::2], s[1::2])",
         "Что выведет программа? Впиши через пробел.", "Pto yhn",
         "Пронумеруй буквы с нуля. [::2] — 0, 2, 4; [1::2] — 1, 3, 5."),
        ("S5", "SA", "NORMAL", "Срез с отрицательным шагом", "", "s = 'abcdef'\nprint(s[::-2])",
         "Что выведет программа?", "fdb", "Отрицательный шаг идёт с конца строки."),
        ("S6", "SC", "EASY", "Переставить слова",
         "Нужно из строки s = 'Иван Петров' вывести «Петров Иван».", "a = s.split()",
         "Какая строка кода выведет ровно «Петров Иван»?",
         [("a", "print(a[1] + a[0])", False), ("b", "print(a[1], a[0])", True),
          ("c", "print(a[0], a[1])", False), ("d", "print(a[-1] + ' ' + a[1])", False)],
         "Сложение строк склеивает их без пробела, а print через запятую ставит пробел."),
        ("S7", "SA", "NORMAL", "split и сложение строк", "", "w = '3 apple 12'.split()\nprint(w[0] + w[2])",
         "Что выведет программа?", "312", "После split элементы — строки, а не числа."),
        ("S8", "SA_COM", "HARD", "Инициалы",
         "Напиши программу: на вход строка «Фамилия Имя Отчество», на выходе «И.О. Фамилия» "
         "(инициалы с точками, после них пробел).",
         None, "Запусти для строки «Толстой Лев Николаевич» и впиши результат. Код вставь в комментарий к ответу.",
         "Л.Н. Толстой", "Раздели строку split(), первая буква слова — срез [0]."),
    ]},
    {"uid": "ind:tsk1330-usloviya", "title": "Условия: разбор кода", "tasks": [
        ("C1", "SA", "EASY", "Порядок веток elif", "",
         "x = 5\nif x > 3:\n    print('A')\nelif x > 4:\n    print('B')\nelse:\n    print('C')",
         "Что выведет программа?", "A", "Срабатывает только первая ветка, условие которой истинно."),
        ("C2", "SA", "EASY", "Число как условие", "", "x = 0\nif x:\n    print(1)\nelse:\n    print(2)",
         "Что выведет программа?", "2", "Ноль в условии считается ложью."),
        ("C3", "MC", "NORMAL", "Перебор вариантов", "Дано условие:", "a % 3 == 0 and a % 5 != 0 or a > 100",
         "При каких a оно истинно? Отметь все верные варианты.",
         [("a", "15", False), ("b", "9", True), ("c", "105", True), ("d", "20", False)],
         "and выполняется раньше or. Проверь каждое число по отдельности."),
        ("C4", "SA", "NORMAL", "Ошибка в поиске максимума",
         "Программа должна найти наибольшее из трёх чисел.",
         "a, b, c = 7, 3, 9\nif a > b:\n    m = a\nelif b > c:\n    m = b\nelse:\n    m = c\nprint(m)",
         "Что она выведет? Сравни с правильным ответом — так видна ошибка.", "7",
         "Первое условие проверяет только a и b, про c оно ничего не знает."),
        ("C5", "SA", "EASY", "Модуль разности без abs", "", "a, b = 3, 10\nd = a - b\nif d < 0:\n    d = -d\nprint(d)",
         "Что выведет программа?", "7", "Посчитай d, потом проверь условие."),
        ("C6", "SA_COM", "HARD", "Високосный год",
         "Год високосный, если делится на 4 и не делится на 100, либо делится на 400. Напиши программу проверки года.",
         None, "Проверь годы 1900, 2000, 2024, 2100 и впиши через пробел только високосные. "
               "Код вставь в комментарий к ответу.",
         "2000 2024", "Запиши условие через and и or, расставь скобки для ясности."),
        ("C7", "SA", "NORMAL", "Палиндром", "", "s = 'level'\nprint('да' if s == s[::-1] else 'нет')",
         "Что выведет программа?", "да", "s[::-1] — строка задом наперёд."),
    ]},
]


def _dsn() -> str:
    """Прод-DSN learn из `.mcp.json` (секрет не печатаем)."""
    cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
    servers = cfg.get("mcpServers", cfg)
    for arg in servers["learn_prod_db"]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
            return arg
    raise RuntimeError("прод-DSN learn_prod_db не найден в .mcp.json")


def build_task(row: tuple) -> tuple[str, int, dict, dict]:
    """Собрать (external_uid, difficulty_id, task_content, solution_rules) задания."""
    key, ttype, diff, title, intro, code, question, answer, hint = row
    stem = f"<p>{html.escape(intro)}</p>" if intro else ""
    if code:
        stem += f'<pre><code class="language-python">{html.escape(code)}</code></pre>'
    stem += f"<p>{html.escape(question)}</p>"
    options = None
    if ttype in ("SC", "MC"):
        options = [{"id": i, "text": t, "scores": None, "is_active": True, "explanation": None}
                   for i, t, _ in answer]
    content = {
        "code": None, "stem": stem, "tags": None, "type": ttype, "media": None, "table": None,
        "title": title, "prompt": None, "scales": None, "options": options, "has_hints": True,
        "course_uid": None, "hints_text": [hint], "hints_video": [], "difficulty_code": None,
        "multiline_answer": False,
    }
    rules: dict[str, Any] = {
        "quiz": None, "max_score": 1,
        "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
        "auto_check": True, "text_answer": None, "scoring_mode": "all_or_nothing", "partial_rules": [],
        "custom_scoring_config": None, "manual_review_required": False,
    }
    if options:
        rules["short_answer"] = None
        rules["correct_options"] = [i for i, _, ok in answer if ok]
    else:
        rules["correct_options"] = []
        rules["short_answer"] = {"regex": None, "use_regex": False, "normalization": NORM,
                                 "accepted_answers": [{"score": 1, "value": answer}]}
    return f"ind:tsk1330-{key}", DIFF[diff], content, rules


async def _children(conn: asyncpg.Connection) -> list[int]:
    """Подкурсы 2106 по порядку."""
    rows = await conn.fetch(
        "SELECT course_id FROM course_parents WHERE parent_course_id=$1 ORDER BY order_number", ROOT)
    return [r["course_id"] for r in rows]


async def _results_count(conn: asyncpg.Connection) -> int:
    """Число сдач ученика по снимаемым подкурсам (должно сохраниться)."""
    return await conn.fetchval(
        "SELECT count(*) FROM task_results r JOIN tasks t ON t.id=r.task_id "
        "WHERE r.user_id=$1 AND t.course_id = ANY($2::int[])", USER_ID, DROP)


async def main(apply: bool) -> None:
    """Выполнить замену в одной транзакции и проверить результат."""
    conn = await asyncpg.connect(_dsn())
    try:
        before = await _children(conn)
        log.info("до: %s", before)
        if before != [863, 867, 864, 986]:
            raise RuntimeError(f"состав 2106 не совпадает с ожидаемым: {before}")
        taken = await conn.fetchval(
            "SELECT count(*) FROM courses WHERE course_uid = ANY($1::text[])", [s["uid"] for s in SUBCOURSES])
        if taken:
            raise RuntimeError("подкурсы tsk-1330 уже созданы — повторный запуск")
        results_before = await _results_count(conn)
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "DELETE FROM course_parents WHERE parent_course_id=$1 AND course_id = ANY($2::int[])", ROOT, DROP)
            new_ids: list[int] = []
            for n, sc in enumerate(SUBCOURSES, start=1):
                cid = await conn.fetchval(
                    "INSERT INTO courses (title, access_level, description, course_uid) "
                    "VALUES ($1, 'self_guided', $2, $3) RETURNING id", sc["title"], DESCRIPTION, sc["uid"])
                new_ids.append(cid)
                await conn.execute(
                    "INSERT INTO course_parents (course_id, parent_course_id, order_number, is_transparent) "
                    "VALUES ($1, $2, $3, false)", cid, ROOT, n)
                for row in sc["tasks"]:
                    uid, diff_id, content, rules = build_task(row)
                    await conn.execute(
                        "INSERT INTO tasks (external_uid, max_score, task_content, course_id, difficulty_id, "
                        "solution_rules) VALUES ($1, 1, $2::jsonb, $3, $4, $5::jsonb)",
                        uid, json.dumps(content, ensure_ascii=False), cid, diff_id,
                        json.dumps(rules, ensure_ascii=False))
            await conn.execute(
                "UPDATE course_parents SET order_number=$3 WHERE parent_course_id=$1 AND course_id=$2",
                ROOT, KEEP_LAST, len(SUBCOURSES) + 1)
            after = await _children(conn)
            counts = await conn.fetch(
                "SELECT course_id, count(*) n, array_agg(order_position ORDER BY order_position) pos "
                "FROM tasks WHERE course_id = ANY($1::int[]) GROUP BY 1 ORDER BY 1", new_ids)
            results_after = await _results_count(conn)
            log.info("после: %s", after)
            log.info("задания: %s", [(r["course_id"], r["n"], list(r["pos"])) for r in counts])
            log.info("сдачи %s в 863/864/867: до %s, после %s", USER_ID, results_before, results_after)
            if (after != new_ids + [KEEP_LAST]
                    or [r["n"] for r in counts] != [len(s["tasks"]) for s in SUBCOURSES]
                    or results_after != results_before):
                raise RuntimeError("проверка после записи не сошлась")
        except Exception:
            await tr.rollback()
            raise
        if apply:
            await tr.commit()
            log.info("COMMIT; новые подкурсы: %s", new_ids)
        else:
            await tr.rollback()
            log.info("dry-run: ROLLBACK")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
