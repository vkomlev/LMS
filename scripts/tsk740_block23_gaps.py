# -*- coding: utf-8 -*-
"""tsk-740, партия 9: задания под непроверяемые цели и практика другой формы.

ЗАЧЕМ
Экспертное ревью 01.09 дало два P1, которые закрываются заданиями:
- **К1 (выравнивание).** Из семи заявленных учебных результатов заданиями
  проверялись три. «Три ловушки формата», «когда не годится Флойд» и вся
  топологическая сортировка не проверялись ничем: по Biggs цель без оценивания
  ученик не выучит.
- **К4 (практика).** Все 27 заданий были одной формы и одного типа — «дай число
  по файлу». Ни разбора чужого кода, ни «допиши пропущенное», ни «предскажи
  вывод», хотя в этой теме ошибка чаще в коде, чем в понимании алгоритма.

ПОЧЕМУ ЗАОДНО МЕНЯЕТСЯ СТРУКТУРА
Три новых задания относятся к урокам, которые лежат в ГЛАВЕ (формат файла,
словарь смежности, сборка программы). По правилу структуры промежуточный узел
держит только вводный материал, а материалы и задания живут в листьях рядом —
изучил приём, сразу применил. Поэтому появляется третий лист «Читаем файл и
собираем программу»: в него уезжают уроки 2 и 7 из главы и пять новых заданий.
В главе остаётся вводный урок 1 — он обзорный и заданий не требует.

СОСТАВ ПОСЛЕ ПАРТИИ
| Узел | Материалов | Заданий |
| глава 1490            | 1 | 0  |
| лист В (новый)        | 2 | 5  |
| лист А 1492           | 2 | 13 |
| лист Б 1493           | 2 | 12 |
| сложные 1491          | 0 | 6  |
Порог 20 сущностей на лист соблюдён с запасом.

ТИПЫ
`SC` и `SA` — короткие проверки без файла, поэтому гейт tsk-419 (обязательный
комментарий) к ним не относится: он действует на SA_COM/TBL_COM. Порядок
вариантов у SC перемешан так, чтобы верный не стоял всегда на одном месте.

Запуск: вхолостую по умолчанию;
  DBCHECK_OK=1 python scripts/tsk740_block23_gaps.py
  DBCHECK_OK=1 python scripts/tsk740_block23_gaps.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]

GLAVA_UID = "lms:tsk740:ege2027:23"
LIST_V_UID = "lms:tsk740:ege2027:23:osnova"
LIST_A_UID = "lms:tsk740:ege2027:23:short"
LIST_B_UID = "lms:tsk740:ege2027:23:count"

# Уроки, которые переезжают из главы в новый лист (по external_uid материала).
PEREEZD_MATERIALOV = {
    "lms:tsk740:m23:2": ("list_v", 1),
    "lms:tsk740:m23:7": ("list_v", 2),
}


def sc(uid, kurs, title, stem, varianty, verniy, slozhnost=2, hint=None):
    """Задание с одним верным вариантом."""
    return {
        "uid": uid, "kurs": kurs, "slozhnost": slozhnost,
        "content": {
            "type": "SC", "title": title, "stem": stem,
            "options": [{"id": bukva, "text": tekst, "is_active": True, "explanation": ""}
                        for bukva, tekst in varianty],
            "has_hints": bool(hint), "hints_text": [hint] if hint else [],
            "hints_video": [], "manual_review_required": False,
        },
        "rules": {
            "max_score": 1,
            "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
            "auto_check": True, "text_answer": None, "scoring_mode": "all_or_nothing",
            "short_answer": None, "partial_rules": [], "correct_options": [verniy],
            "custom_scoring_config": None, "manual_review_required": False,
        },
    }


def sa(uid, kurs, title, stem, otvety, slozhnost=2, hint=None):
    """Задание с коротким ответом. `otvety` — все равнозначные формы."""
    return {
        "uid": uid, "kurs": kurs, "slozhnost": slozhnost,
        "content": {
            "type": "SA", "title": title, "stem": stem, "options": None,
            "has_hints": bool(hint), "hints_text": [hint] if hint else [],
            "hints_video": [], "manual_review_required": False,
        },
        "rules": {
            "max_score": 1,
            "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
            "auto_check": True, "text_answer": None, "scoring_mode": "all_or_nothing",
            "short_answer": {
                "regex": None, "use_regex": False,
                "normalization": ["trim", "lower", "collapse_spaces"],
                "accepted_answers": [{"score": 1, "value": v} for v in otvety],
            },
            "partial_rules": [], "correct_options": [],
            "custom_scoring_config": None, "manual_review_required": False,
        },
    }


ZADANIYA = [
    # --- лист В: формат файла и сборка программы (закрывает цель «три ловушки») ---
    sc("lms:tsk740:gap23:01", "list_v",
       "Чем резать строку файла",
       "<p>В файле задания 23 числа в строке разделены произвольным количеством пробелов "
       "и символов табуляции вперемешку. Каким способом строку нужно разбирать, чтобы "
       "разделители любого вида обработались правильно?</p>",
       [("A", "<code>строка.split(' ')</code> — резать по одному пробелу"),
        ("B", "<code>строка.split()</code> — без аргументов"),
        ("C", "<code>строка.split('\\t')</code> — резать по табуляции"),
        ("D", "<code>строка.split(', ')</code> — резать по запятой с пробелом")],
       "B",
       hint="Подумайте, какой из вариантов не требует знать заранее, какой именно "
            "разделитель встретится в конкретной строке."),

    sa("lms:tsk740:gap23:02", "list_v",
       "Тип веса ребра",
       "<p>В строке файла третье число — вес ребра, и он может быть дробным, например "
       "<code>2821.0</code> или <code>0.456</code>. Какой функцией Python нужно "
       "преобразовать эту часть строки, чтобы вес прочитался без потерь? Впишите только "
       "имя функции, без скобок.</p>",
       ["float"],
       hint="Целочисленное преобразование здесь не годится: оно либо остановит "
            "программу, либо молча отбросит дробную часть."),

    sc("lms:tsk740:gap23:03", "list_v",
       "Ключ словаря смежности",
       "<p>Программа строит словарь смежности строкой</p>"
       "<pre><code class=\"language-python\">граф[куда].append((откуда, вес))</code></pre>"
       "<p>Файл прочитан целиком, ошибок не возникает, но найденные пути оказываются "
       "неверными. Что здесь не так?</p>",
       [("A", "Рёбра добавлены в обратную сторону: ключом должна быть вершина «откуда»"),
        ("B", "Вес нужно класть первым элементом пары"),
        ("C", "Нужно использовать обычный словарь вместо defaultdict"),
        ("D", "Ничего: строка верна, ошибка в другом месте программы")],
       "A", slozhnost=3,
       hint="Вспомните, что означает строка файла «L M W»: в какую сторону ведёт ребро "
            "и от какой вершины мы будем искать соседей."),

    sc("lms:tsk740:gap23:04", "list_v",
       "Где программа ищет файл",
       "<p>Программа со строкой <code>open('23.txt')</code> остановилась с сообщением "
       "<code>FileNotFoundError</code>. Файл задания скачан и лежит в папке «Загрузки», "
       "а сама программа сохранена на рабочем столе. Что нужно исправить?</p>",
       [("A", "Переустановить Python — он не видит файлы"),
        ("B", "Заменить <code>open</code> на <code>read</code>"),
        ("C", "Положить файл в ту же папку, где лежит программа, либо указать полный путь"),
        ("D", "Открыть файл в текстовом редакторе перед запуском программы")],
       "C",
       hint="Короткое имя в кавычках означает «файл рядом со мной». Спросите себя, где "
            "для программы находится это «рядом»."),

    sa("lms:tsk740:gap23:05", "list_v",
       "Имя скачанного файла",
       "<p>В карточке задания ссылка на файл подписана как <code>23_s1.txt</code>, но "
       "после скачивания в папке лежит файл с длинным именем из букв и цифр — например "
       "<code>372ccd2454e1e4b64f4eade63994d8c830c7ff9444ba01635fd97cd35c5a4129.txt</code>. "
       "Это ошибка платформы или ожидаемое поведение? Впишите одно слово: "
       "<b>ошибка</b> или <b>ожидаемое</b>.</p>",
       ["ожидаемое", "ожидаемо"],
       hint="Вспомните, как хранилище называет файлы и почему у каждого задания это имя "
            "своё."),

    # --- лист А: граница применимости Флойда + предсказание вывода ---
    sc("lms:tsk740:gap23:06", "list_a",
       "Когда Флойду нужна подготовка",
       "<p>В файле задания 40 вершин, но их номера доходят до 1000. Ученик решил "
       "применить алгоритм Флойда и завести таблицу «каждая вершина с каждой» прямо по "
       "номерам вершин. Что произойдёт и что нужно сделать?</p>",
       [("A", "Ничего особенного: таблица 1000×1000 считается мгновенно"),
        ("B", "Программа будет считать недопустимо долго — номера вершин нужно сжать, "
              "заменив их на позиции в списке реально встречающихся"),
        ("C", "Флойд для ориентированных графов не работает вообще"),
        ("D", "Нужно отсортировать рёбра по весу перед запуском")],
       "B", slozhnost=3,
       hint="Оцените, сколько троек вершин переберёт алгоритм, если считать по номерам "
            "до 1000, и сравните с числом вершин, которые реально есть в файле."),

    sc("lms:tsk740:gap23:07", "list_a",
       "Что напечатает программа без int",
       "<p>Кратчайший путь из стартовой вершины в финишную равен 7.5. В конце программы "
       "стоит строка</p>"
       "<pre><code class=\"language-python\">print(расстояние[финиш])</code></pre>"
       "<p>Что появится в выводе и будет ли такой ответ засчитан?</p>",
       [("A", "<code>7</code> — ответ верный"),
        ("B", "<code>8</code> — ответ верный, число округлилось"),
        ("C", "<code>7.5</code> — ответ не будет засчитан: задание просит целую часть"),
        ("D", "Программа остановится с ошибкой типа")],
       "C",
       hint="Python печатает вещественное число как есть. Сравните это с тем, что "
            "именно просит записать в ответе условие задания."),

    # --- лист Б: топологический порядок и роль запоминания ---
    sc("lms:tsk740:gap23:08", "list_b",
       "Не все вершины вошли в порядок",
       "<p>Программа построила топологический порядок для графа из файла. В графе 41 "
       "вершина, а в построенный порядок вошли только 38. О чём это говорит?</p>",
       [("A", "Три вершины изолированы — у них нет ни входящих, ни исходящих рёбер"),
        ("B", "В графе есть цикл: у оставшихся вершин степень захода так и не обнулилась"),
        ("C", "Это нормально: в порядок входят только вершины с исходящими рёбрами"),
        ("D", "Файл прочитан не до конца")],
       "B", slozhnost=3,
       hint="Вспомните, при каком условии вершина попадает в очередь кандидатов, и что "
            "мешает ей обнулиться, если она кого-то ждёт по кругу."),

    sc("lms:tsk740:gap23:09", "list_b",
       "Что будет без запоминания",
       "<p>В программе подсчёта количества путей убрали словарь, в котором запоминались "
       "уже посчитанные вершины. Логика подсчёта осталась верной. Что изменится?</p>",
       [("A", "Программа выдаст неверное число — путей насчитается больше"),
        ("B", "Программа остановится с ошибкой"),
        ("C", "Ничего не изменится, словарь был лишним"),
        ("D", "Число получится верным, но на реальном файле программа не дождётся конца: "
              "каждая вершина пересчитывается заново на каждом пути через неё")],
       "D", slozhnost=3,
       hint="Ответ на вопрос «верно ли посчитает» и ответ на вопрос «дождёмся ли мы "
            "результата» здесь разные. Подумайте про оба."),
]


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                    dsn = arg
                    break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        glava = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", GLAVA_UID)
        list_a = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", LIST_A_UID)
        list_b = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", LIST_B_UID)
        if None in (glava, list_a, list_b):
            raise RuntimeError("Узлы блока 23 не найдены — сперва партии 4-8.")

        po_listam: dict[str, int] = {}
        for z in ZADANIYA:
            po_listam[z["kurs"]] = po_listam.get(z["kurs"], 0) + 1

        print("=== ПЛАН ===")
        print(f"Новый лист «Читаем файл и собираем программу» под главой {glava}, позиция 1")
        print(f"Из главы в него переезжают уроки: {', '.join(PEREEZD_MATERIALOV)}")
        for kurs, skolko in sorted(po_listam.items()):
            print(f"  {kurs}: +{skolko} заданий")
        print(f"Всего новых заданий: {len(ZADANIYA)}")

        # Контроль верного варианта: он не должен всегда стоять на одной позиции.
        pozicii = [z["rules"]["correct_options"][0] for z in ZADANIYA
                   if z["content"]["type"] == "SC"]
        print(f"Позиции верных вариантов у SC: {' '.join(pozicii)} "
              f"(различных {len(set(pozicii))})")
        if len(set(pozicii)) < 3:
            raise RuntimeError("Верный вариант почти всегда на одном месте — переставить.")

        if not apply:
            print("\nВхолостую. Записи не было.")
            return

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            await conn.execute("SELECT set_config('app.skip_course_parent_order_trigger', 'true', true)")

            list_v = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", LIST_V_UID)
            if list_v is None:
                list_v = await conn.fetchval(
                    "INSERT INTO courses (title, access_level, description, is_required, "
                    "course_uid, is_public_demo) "
                    "VALUES ($1, 'self_guided'::access_level_type, $2, false, $3, false) RETURNING id",
                    "Читаем файл и собираем программу",
                    "Формат файла задания 23, словарь смежности и сборка рабочей программы "
                    "от скачанного файла до числа в ответе.",
                    LIST_V_UID,
                )
                await conn.execute(
                    "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
                    "VALUES ($1, $2, 1)", list_v, glava,
                )
                # Листья А и Б сдвигаются на позиции 2 и 3.
                await conn.execute(
                    "UPDATE course_parents SET order_number = 2 WHERE course_id = $1 AND parent_course_id = $2",
                    list_a, glava)
                await conn.execute(
                    "UPDATE course_parents SET order_number = 3 WHERE course_id = $1 AND parent_course_id = $2",
                    list_b, glava)
                print(f"\nЛист В создан: id={list_v}, позиция 1; А и Б сдвинуты на 2 и 3")
            else:
                print(f"\nЛист В уже был: id={list_v}")

            kursy = {"list_v": list_v, "list_a": list_a, "list_b": list_b}

            for uid, (kuda, poryadok) in PEREEZD_MATERIALOV.items():
                await conn.execute(
                    "UPDATE materials SET course_id = $2, order_position = $3 WHERE external_uid = $1",
                    uid, kursy[kuda], poryadok,
                )
            print(f"Уроков перенесено в лист В: {len(PEREEZD_MATERIALOV)}")

            sozdano = obnovleno = 0
            for z in ZADANIYA:
                soderzhimoe = dict(z["content"])
                soderzhimoe["course_uid"] = {
                    "list_v": LIST_V_UID, "list_a": LIST_A_UID, "list_b": LIST_B_UID}[z["kurs"]]
                est = await conn.fetchval("SELECT id FROM tasks WHERE external_uid = $1", z["uid"])
                if est is None:
                    await conn.execute(
                        "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                        "difficulty_id, solution_rules, is_active, requirement_level) "
                        "VALUES ($1, 1, $2::jsonb, $3, $4, $5::jsonb, true, 'required')",
                        z["uid"], json.dumps(soderzhimoe, ensure_ascii=False), kursy[z["kurs"]],
                        z["slozhnost"], json.dumps(z["rules"], ensure_ascii=False),
                    )
                    sozdano += 1
                else:
                    await conn.execute(
                        "UPDATE tasks SET task_content = $2::jsonb, solution_rules = $3::jsonb, "
                        "course_id = $4, difficulty_id = $5 WHERE id = $1",
                        est, json.dumps(soderzhimoe, ensure_ascii=False),
                        json.dumps(z["rules"], ensure_ascii=False), kursy[z["kurs"]], z["slozhnost"],
                    )
                    obnovleno += 1
            print(f"Заданий создано {sozdano}, обновлено {obnovleno}")

            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'false', true)")

            # Верификация до коммита: состав узлов и порог.
            print("\n=== ПРОВЕРКА (до коммита) ===")
            for kurs_id, imya in ((glava, "глава"), (list_v, "лист В"),
                                  (list_a, "лист А"), (list_b, "лист Б")):
                zad = await conn.fetchval(
                    "SELECT count(*) FROM tasks WHERE course_id = $1 AND is_active", kurs_id)
                mat = await conn.fetchval(
                    "SELECT count(*) FROM materials WHERE course_id = $1 AND is_active", kurs_id)
                print(f"  {imya}: материалов {mat}, заданий {zad}, всего {mat + zad}")
                if mat + zad > 20:
                    raise RuntimeError(f"{imya}: {mat + zad} сущностей — сверх порога 20.")
            novye = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE external_uid LIKE 'lms:tsk740:gap23:%' AND is_active")
            if novye != len(ZADANIYA):
                raise RuntimeError(f"Новых заданий в базе {novye}, ждали {len(ZADANIYA)}.")
            bez_pravila = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE external_uid LIKE 'lms:tsk740:gap23:%' "
                "AND coalesce(jsonb_array_length(solution_rules->'correct_options'), 0) = 0 "
                "AND solution_rules#>>'{short_answer,accepted_answers,0,value}' IS NULL")
            if bez_pravila:
                raise RuntimeError(f"{bez_pravila} новых заданий без критерия проверки.")
            print(f"  новых заданий: {novye}, все с критерием проверки")

        print("\nГотово. Пробелы закрыты.")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tsk-740 партия 9: задания под пробелы ревью")
    parser.add_argument("--apply", action="store_true", help="записать изменения")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
