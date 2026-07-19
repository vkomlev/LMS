# -*- coding: utf-8 -*-
"""tsk-316 (F4): прикрепить видео-разборы @CyberGuruPython как hints к Python SA_COM.

ЧТО ДЕЛАЕТ
0 из 250 активных Python SA_COM-заданий (навигатор 88 → темы 90,103–111) имели
подсказки (аудит tsk-299 §5). Разборы этих WP-канонов — видео-посты в ТГ-канале
«Обучение Python для новичков» (@CyberGuruPython, source_item канал 1821049174 в CB).
Скрипт проставляет task_content.hints_video = [прямая VK-ссылка на видео] и
has_hints=true у ПОДТВЕРЖДЁННОГО подмножества заданий.

ИСПРАВЛЕНИЕ ПЕРВОЙ ВЕРСИИ СКРИПТА (важно)
Первый прогон (27+10=37 пар) ошибочно считал, что разбор-видео нигде не хранится
кроме имени файла, и писал догадку — ссылку на живой пост t.me/CyberGuruPython/<id>.
Это было НЕВЕРНО: в CB есть точное соответствие ТГ-пост → загруженное VK-видео,
`content_hub.publication`/`content_hub.asset` (join по content_hash файла на
`vk_importer:video:<hash>` записи content_hub.source_item + publication.destination='vk').
Проверено: **100% из 402 постов канала, у которых вообще есть видео, имеют
разрешённую VK-ссылку** (159 «unmatched» ранее — это посты БЕЗ видео вообще, не
сбой матчинга). Это даёт (1) точную прямую ссылку на видео вместо догадки о живом
посте и (2) кратно больше кандидатов для сопоставления с LMS-заданиями (402 вместо
91 первоначально найденных по имени файла).

Ключ маппинга остаётся тем же: сопоставление по СОДЕРЖАНИЮ условия (тема курса +
close-match текста, demand-driven task→best-video И supply-driven video→best-task
в обе стороны, чтобы поймать случаи, где top-1 матчер выбирает более «громкое»
обобщённое видео вместо точного). Каждая пара провалидирована вручную сверкой
условия LMS-stem ↔ текста ТГ-поста — совпадает ИМЕННО задача/алгоритм, не только
тема. Отсеяны магниты — обобщённые вебинары («abs одного числа» ≠ «модуль разности
двух», «объединение множеств» ≠ «разность множеств»).

ИСПРАВЛЕНИЕ ВТОРОЙ ВЕРСИИ (шахматы, важно)
difflib сматчил LMS#182 (ЛАДЬЯ) на TG-пост про ФЕРЗЯ (msg 348) — совпал общий
текст-преамбула «поле шахматной доски задаётся парой чисел…», а имя фигуры в
конце текста не сверил. Обнаружено оператором (прислал прямые ссылки на посты
per-фигура). Полный текст постов расставил по фигурам однозначно: ладья→471,
слон→418, король→524, ферзь→348, конь→493 (короткий заголовок-пост, но тема
точная и это единственный пост в канале про коня). Урок: при совпадении общей
преамбулы — сверять специфичную деталь в КОНЦЕ текста, не только общий скор.

Итог: 65 из 250 (26%) — рост с 37 после первого прохода, +4 из третьего (шахматы).
Остаток (~185) — надёжного видео-разбора той же задачи нет (наборы WP-канона и
канала пересекаются частично; часть постов — про темы вне канона: двумерные
списки, рекурсия, Поляков).

ИДЕМПОТЕНТНОСТЬ / BLAST-RADIUS
UPDATE трогает только перечисленные id, только SA_COM. В отличие от первой версии,
WHERE-guard больше не требует hints_video=[] — здесь мы намеренно ПЕРЕЗАПИСЫВАЕМ
СОБСТВЕННУЮ более раннюю запись (t.me-догадку → проверенная vk.com-ссылка); других
источников правды на hints_video/has_hints для Python SA_COM ещё не было. Патч
task_content = task_content || {hints_video, has_hints, hints_text} добавляет РОВНО
3 ключа верхнего уровня; ответы/сложность/медиа/stem/solution_rules не затрагиваются
(проверяется в транзакции по md5 до/после). 0 попыток учеников по этим заданиям.
Обратимо.

Запуск: dry-run по умолчанию (транзакция откатывается); --apply — запись
(нужен DBCHECK_OK=1, прод-хост 5.42.107.253).
"""
from __future__ import annotations

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

# LMS task.id -> прямая VK-ссылка на видео-разбор (content_hub: TG-пост -> VK видео
# через content_hash asset -> vk_importer:video:<hash> -> publication.destination='vk').
# Провалидировано поштучно сверкой условия LMS-stem ↔ текста ТГ-поста (см. отчёт tsk-316).
MAPPING: dict[int, str] = {
    # --- Числа (курс 103) ---
    50: "https://vk.com/video-53400615_456239940",   # abs: насколько одно число больше другого
    52: "https://vk.com/video-53400615_456239944",   # среднее двух чисел с плавающей точкой
    53: "https://vk.com/video-53400615_456240047",   # НОД math.gcd
    49: "https://vk.com/video-53400615_456239946",   # целочисленное деление и остаток
    46: "https://vk.com/video-53400615_456240043",   # периметр окружности по радиусу
    48: "https://vk.com/video-53400615_456240050",   # градусы -> радианы
    60: "https://vk.com/video-53400615_456239926",   # первая цифра после точки
    57: "https://vk.com/video-53400615_456239899",   # сумма двух натуральных, последняя цифра
    62: "https://vk.com/video-53400615_456240052",   # дней на статью (символы/мин, floordiv)
    58: "https://vk.com/video-53400615_456240004",   # дистанция v*t, круги/остаток на дорожке

    # --- Функции (курс 104) ---
    71: "https://vk.com/video-53400615_456239945",   # calculate_average
    75: "https://vk.com/video-53400615_456240118",   # hello(name, age=18)
    72: "https://vk.com/video-53400615_456240120",   # print_square(n)
    78: "https://vk.com/video-53400615_456239949",   # global counter / increase_counter
    81: "https://vk.com/video-53400615_456239950",   # summ_numbers(numbers) + локальная total
    76: "https://vk.com/video-53400615_456240312",   # mult(*args) — произведение
    77: "https://vk.com/video-53400615_456240119",   # student_info(name, **kwargs)
    87: "https://vk.com/video-53400615_456239928",   # сокращение дроби (gcd)
    563: "https://vk.com/video-53400615_456239891",  # generate_password(length)
    83: "https://vk.com/video-53400615_456239923",   # count_char(string, char)
    86: "https://vk.com/video-53400615_456239896",   # "счастливые" шестизначные числа
    91: "https://vk.com/video-53400615_456240602",   # find_duplicates — неуникальные элементы
    565: "https://vk.com/video-53400615_456239941",  # дата: предыдущий/следующий день
    89: "https://vk.com/video-53400615_456240007",   # check_text — запрещённые слова/фразы
    84: "https://vk.com/video-53400615_456240026",   # get_primes(n) — простые в диапазоне
    80: "https://vk.com/video-53400615_456239948",   # global name: set_name()/hello()

    # --- Множества (курс 105) ---
    318: "https://vk.com/video-53400615_456240099",  # apple/orange/banana + grape/kiwi, буквы
    299: "https://vk.com/video-53400615_456240035",  # 10 простых + 11..20 включительно
    323: "https://vk.com/video-53400615_456240101",  # fruits ∩ слова предложения
    320: "https://vk.com/video-53400615_456240100",  # 26 букв + гласные python, разность

    # --- Первая программа (курс 106) ---
    121: "https://vk.com/video-53400615_456240022",  # мотопробег Москва/СПб/Екб
    566: "https://vk.com/video-53400615_456239914",  # n долларов между k друзьями
    119: "https://vk.com/video-53400615_456239913",  # площадь прямоугольника
    112: "https://vk.com/video-53400615_456240108",  # сумма двух чисел, только число
    120: "https://vk.com/video-53400615_456239942",  # минуты с полуночи -> часы/минуты

    # --- Словари (курс 107) ---
    348: "https://vk.com/video-53400615_456239962",  # частота слов в тексте
    370: "https://vk.com/video-53400615_456240044",  # частота элементов списка через словарь
    374: "https://vk.com/video-53400615_456239962",  # частота слов в тексте (другой пример)
    353: "https://vk.com/video-53400615_456239952",  # цены товаров +10% через функцию
    568: "https://vk.com/video-53400615_456239955",  # словарь имя -> случайный пароль

    # --- Строки (курс 108) ---
    150: "https://vk.com/video-53400615_456239943",  # слово кратно 3 — поменять трети
    136: "https://vk.com/video-53400615_456239925",  # каждый второй символ, шаг 2

    # --- Списки (курс 109) ---
    276: "https://vk.com/video-53400615_456240311",  # удалить из numbers элементы > 5
    258: "https://vk.com/video-53400615_456239983",  # список квадратных корней 1..10
    253: "https://vk.com/video-53400615_456240309",  # список из букв строки (list(s))

    # --- Циклы (курс 110) ---
    220: "https://vk.com/video-53400615_456239856",  # вложенные циклы: 1;12;123;1234;12345
    221: "https://vk.com/video-53400615_456239856",  # вложенные циклы: 1;22;333;4444;55555
    222: "https://vk.com/video-53400615_456239856",  # вложенные циклы: 1;23;456;78910
    225: "https://vk.com/video-53400615_456239907",  # a1,a2: возрастание/убывание
    232: "https://vk.com/video-53400615_456240488",  # Хоббит съедает +20 г в день
    231: "https://vk.com/video-53400615_456240487",  # простое число: два делителя
    219: "https://vk.com/video-53400615_456239889",  # while: строки до пустой, длина макс.
    213: "https://vk.com/video-53400615_456240358",  # сумма первых 10 натуральных
    218: "https://vk.com/video-53400615_456240125",  # while: числа до отрицательного, break
    233: "https://vk.com/video-53400615_456240375",  # неотрицательные до отрицательного: нули
    217: "https://vk.com/video-53400615_456239885",  # while: пароль до правильного
    216: "https://vk.com/video-53400615_456239876",  # цифры в строке через isdigit
    229: "https://vk.com/video-53400615_456240051",  # count сочетаний букв «ла» в n словах

    # --- Условные (курс 111) ---
    205: "https://vk.com/video-53400615_456239951",  # логические выражения True/False
    182: "https://vk.com/video-53400615_456240072",  # шахматы: ЛАДЬЯ — может пойти? (msg 471)
    183: "https://vk.com/video-53400615_456240024",  # шахматы: СЛОН — может пойти? (msg 418)
    184: "https://vk.com/video-53400615_456240121",  # шахматы: КОРОЛЬ — может пойти? (msg 524)
    185: "https://vk.com/video-53400615_456239956",  # шахматы: ФЕРЗЬ — может пойти? (msg 348)
    186: "https://vk.com/video-53400615_456240093",  # шахматы: КОНЬ — может пойти? (msg 493)
    175: "https://vk.com/video-53400615_456239954",  # три числа — два наибольших
}


# Идемпотентно: то же вычисление на повторном прогоне даёт тот же результат (не только
# "если пусто" — здесь мы владеем этим полем целиком, апгрейд собственной записи ок).
UPDATE_ONE = """
UPDATE tasks
SET task_content = task_content || jsonb_build_object(
        'hints_video', $2::jsonb,
        'has_hints', true,
        'hints_text', COALESCE(task_content->'hints_text', '[]'::jsonb)
    )
WHERE id = $1
  AND is_active
  AND task_content->>'type' = 'SA_COM'
"""


def _dsn() -> str:
    """Прод-DSN для learn. Из окружения или из .mcp.json (learn_prod_db), без пароля в коде."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно.")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    target_ids = list(MAPPING.keys())
    try:
        async with conn.transaction():
            # --- Снимок ДО (для проверки, что коллатеральных полей не тронули) ---
            before = {
                r["id"]: r
                for r in await conn.fetch(
                    "SELECT id, task_content->>'type' AS type, "
                    "md5(COALESCE(task_content->>'stem','')) AS stem_md5, "
                    "md5(COALESCE(solution_rules::text,'')) AS solrules_md5, "
                    "task_content->'hints_video' AS hv_before "
                    "FROM tasks WHERE id = ANY($1::int[])",
                    target_ids,
                )
            }
            missing = [i for i in target_ids if i not in before]
            if missing:
                raise RuntimeError(f"нет в БД: {missing}")
            not_sacom = [i for i, r in before.items() if r["type"] != "SA_COM"]
            if not_sacom:
                raise RuntimeError(f"не SA_COM (маппинг неверен): {not_sacom}")

            print(f"Целевых заданий: {len(target_ids)} (все SA_COM)")
            upgraded = [i for i in target_ids if before[i]["hv_before"] and before[i]["hv_before"] != "[]"]
            new = [i for i in target_ids if i not in upgraded]
            print(f"Новых (hints_video был пуст): {len(new)}; апгрейд собственной записи: {len(upgraded)}")
            print("Примеры (id → новая ссылка):")
            for tid in target_ids[:8]:
                print(f"  id={tid}: → ['{MAPPING[tid]}']")

            # --- Запись ---
            updated = 0
            for tid, url in MAPPING.items():
                payload = json.dumps([url], ensure_ascii=False)
                res = await conn.execute(UPDATE_ONE, tid, payload)
                updated += int(res.split()[-1])
            print(f"\nUPDATE затронул строк: {updated} (ожидали {len(target_ids)})")
            if updated != len(target_ids):
                raise AssertionError(f"обновлено {updated} != {len(target_ids)} — расхождение состояния")

            # --- Верификация независимым чтением внутри транзакции ---
            after = {
                r["id"]: r
                for r in await conn.fetch(
                    "SELECT id, "
                    "task_content->'hints_video' AS hv, "
                    "(task_content->>'has_hints')::bool AS has_hints, "
                    "md5(COALESCE(task_content->>'stem','')) AS stem_md5, "
                    "md5(COALESCE(solution_rules::text,'')) AS solrules_md5 "
                    "FROM tasks WHERE id = ANY($1::int[])",
                    target_ids,
                )
            }
            for tid in target_ids:
                a = after[tid]
                want = MAPPING[tid]
                hv = json.loads(a["hv"]) if a["hv"] else []
                if hv != [want]:
                    raise AssertionError(f"id={tid}: hints_video={hv} != ['{want}']")
                if a["has_hints"] is not True:
                    raise AssertionError(f"id={tid}: has_hints={a['has_hints']} != true")
                if a["stem_md5"] != before[tid]["stem_md5"]:
                    raise AssertionError(f"id={tid}: stem ИЗМЕНЁН — недопустимо")
                if a["solrules_md5"] != before[tid]["solrules_md5"]:
                    raise AssertionError(f"id={tid}: solution_rules ИЗМЕНЁН — недопустимо")

            print(f"Верификация: у всех {len(target_ids)} hints_video=[верная VK-ссылка], "
                  "has_hints=true, stem и solution_rules не изменены.")
            print("\nOK: подсказки проставлены, коллатералей нет.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main("--apply" in sys.argv))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
