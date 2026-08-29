# -*- coding: utf-8 -*-
"""tsk-732: задание 217 «Проверка пароля до успеха» — отделить ввод от вывода.

ЧТО НЕ ТАК
Условие велит «поместить весь вывод программы в поле "Ответ"». Приглашение
`Введите пароль: ` — это тоже вывод программы, а набранное учеником печатается
рядом эхом. Ученик, скопировавший экран дословно, выполняет инструкцию буквально
и получает ноль: эталон принимает только три строки сообщений.

ЗАМЕР (боевая база, 29.08): 16 незачётов из 23 самостоятельных сдач. Трое
учеников независимо отправили только ВВЕДЁННЫЕ значения (`wrong1/wrong2/qwerty`),
решив, что просят ввод; трое вставили экран целиком с приглашениями; трое
ошиблись при перенаборе руками («Попрубуйте», «Добро подаловать!»). Из 45 сдач
22 — ручные зачёты преподавателя.

ЧТО ПРАВИМ
Только формулировку и форму ответа. Смысл задачи (цикл `while True`, три ввода,
`break`) и эталон не меняются — оператор сказал прямо: вопрос корректен.

Три изменения в стеме:
  1. Критерий отбора строк стал механическим: в ответ идёт то, что напечатано
     через `print`. «Весь вывод программы» убрано — именно оно включало
     приглашение.
  2. Названы обе лишние вещи поимённо (набранное с клавиатуры; приглашение
     внутри `input()`), и дан способ их не порождать — `input()` без текста.
  3. Показан образец ФОРМЫ ответа. Образец намеренно на ДРУГОМ наборе вводов
     (`zzz`, `qwerty` — одна ошибка вместо двух): он показывает вид ответа, но
     не выдаёт сам ответ — сколько раз повторится сообщение, ученик выводит из
     работы цикла, а это и есть проверяемое умение.

РАЗМЕТКА: только code-fences и одиночные бэктики. `**жирный**` не используется
намеренно — стем рендерится SPW в plain-режиме (`plainTextToHtml`), где `**` не
разбирается и уехало бы к ученику сырым (класс tsk-706). Fences дают в SPW
код-блок с кнопкой «Копировать» — этим закрывается перенабор руками.

Запуск: dry-run по умолчанию;
  python scripts/tsk732_task217_input_output.py
  python scripts/tsk732_task217_input_output.py --show
  DBCHECK_OK=1 python scripts/tsk732_task217_input_output.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

TASK_ID = 217
EXPECTED_COURSE = 110
EXPECTED_UID = "wp:task:komlev:tsikly-v-python:5"

OLD_STEM = (
    "Напишите программу, которая в цикле `while True` запрашивает у\n"
    "пользователя пароль (через `input()`) до тех пор, пока он не введёт\n"
    "правильный пароль `qwerty`. На каждый неправильный ввод программа\n"
    "выводит `Неверный пароль. Попробуйте еще раз.`. После правильного\n"
    "пароля выводит `Добро пожаловать!` и завершается через `break`.\n"
    "\n"
    "Запустите программу со следующей последовательностью вводов\n"
    "(3 строки):\n"
    "```\n"
    "wrong1\n"
    "wrong2\n"
    "qwerty\n"
    "```\n"
    "\n"
    "Поместите весь вывод программы в поле «Ответ».\n"
)

NEW_STEM = (
    "Напишите программу, которая в цикле `while True` запрашивает у\n"
    "пользователя пароль через `input()` и повторяет запрос до тех пор,\n"
    "пока не будет введён правильный пароль `qwerty`.\n"
    "\n"
    "На каждый неправильный пароль программа печатает через `print`\n"
    "строку:\n"
    "```\n"
    "Неверный пароль. Попробуйте еще раз.\n"
    "```\n"
    "После правильного пароля печатает строку:\n"
    "```\n"
    "Добро пожаловать!\n"
    "```\n"
    "и выходит из цикла через `break`.\n"
    "\n"
    "Запустите программу и введите по очереди три строки:\n"
    "```\n"
    "wrong1\n"
    "wrong2\n"
    "qwerty\n"
    "```\n"
    "\n"
    "Что вписать в поле «Ответ»:\n"
    "в ответе — только те строки, которые программа напечатала сама\n"
    "через `print`. Не переносите то, что вы набирали с клавиатуры\n"
    "(`wrong1`, `wrong2`, `qwerty`), и приглашение вида\n"
    "`Введите пароль:`, если вы написали его внутри `input()`. Чтобы\n"
    "приглашения на экране не было вовсе, вызывайте `input()` без\n"
    "текста в скобках.\n"
    "\n"
    "Образец формы ответа. Так выглядел бы ответ для другого набора\n"
    "вводов — сначала `zzz`, потом `qwerty` (одна ошибка вместо двух):\n"
    "```\n"
    "Неверный пароль. Попробуйте еще раз.\n"
    "Добро пожаловать!\n"
    "```\n"
    "Одна напечатанная строка — одна строка ответа, порядок важен.\n"
    "Свой ответ составьте по этому образцу для вводов `wrong1`,\n"
    "`wrong2`, `qwerty`.\n"
    "\n"
    "Текст сообщений копируйте из блоков выше или прямо из консоли —\n"
    "набирать руками не нужно: опечатка не засчитывается.\n"
)

# Эталон правкой не затрагивается: три строки сообщений, два варианта («е»/«ё»,
# восстановлены в tsk-687). Сторож ниже проверяет, что оба на месте и что новое
# условие им по-прежнему соответствует.
EXPECTED_ANSWER_LINES = 3
EXPECTED_LAST_LINE = "Добро пожаловать!"


def _prod_dsn_from_mcp() -> str:
    """Строка подключения к прод-`learn` из `.mcp.json` (хост и пароль — не в коде)."""
    cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
    servers = cfg.get("mcpServers", cfg)
    for arg in servers["learn_prod_db"]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://"):
            return arg
    raise RuntimeError("В .mcp.json нет строки подключения learn_prod_db.")


def _dsn() -> str:
    """Прод-DSN learn. Из окружения — только если тот же хост и база, что в
    `.mcp.json`: локальный `.env` смотрит на dev, и правка ушла бы молча туда."""
    prod = _prod_dsn_from_mcp()
    p = urlparse(prod)
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    candidate = env.replace("postgresql+asyncpg://", "postgresql://")
    c = urlparse(candidate)
    return candidate if (c.hostname == p.hostname and c.path == p.path) else prod


def _check_new_stem() -> list[str]:
    """Сторож формулировки: что новый текст обязан содержать и чего не должен."""
    problems: list[str] = []
    if "**" in NEW_STEM:
        problems.append("в стеме есть `**` — SPW рендерит стем в plain-режиме, "
                        "разметка уехала бы к ученику сырой (tsk-706)")
    if "весь вывод программы" in NEW_STEM.lower():
        problems.append("осталась формулировка «весь вывод программы» — она и "
                        "включала приглашение ввода")
    for must in ("через `print`", "Введите пароль:", "Образец формы ответа",
                 "input()` без", "копируйте"):
        if must not in NEW_STEM:
            problems.append(f"в стеме нет обязательного куска: {must!r}")
    # Образец не должен выдавать сам ответ: в задании сообщение об ошибке
    # повторяется дважды, в образце — один раз (плюс один раз в описании).
    if NEW_STEM.count("Неверный пароль. Попробуйте еще раз.") != 2:
        problems.append("образец формы, похоже, выдаёт готовый ответ "
                        "(сообщение об ошибке встречается не дважды)")
    if len(NEW_STEM) > 2000:
        problems.append(f"стем длиннее 2000 знаков ({len(NEW_STEM)}) — "
                        "в Telegram-боте он будет обрезан")
    return problems


def _check_rules(rules: dict) -> list[str]:
    """Сторож эталона: правка условия не должна расходиться с приёмом ответа."""
    problems: list[str] = []
    accepted = (((rules or {}).get("short_answer") or {}).get("accepted_answers")) or []
    if len(accepted) != 2:
        problems.append(f"вариантов эталона {len(accepted)}, ожидалось 2 (е/ё, tsk-687)")
    has_yo = False
    for item in accepted:
        lines = [ln for ln in (item.get("value") or "").split("\n") if ln.strip()]
        if len(lines) != EXPECTED_ANSWER_LINES:
            problems.append(
                f"в эталоне {len(lines)} строк, ожидалось {EXPECTED_ANSWER_LINES}: {lines}"
            )
        if lines and lines[-1].strip() != EXPECTED_LAST_LINE:
            problems.append(
                f"последняя строка эталона {lines[-1]!r}, ожидалось {EXPECTED_LAST_LINE!r}"
            )
        if "ё" in (item.get("value") or ""):
            has_yo = True
    if not has_yo:
        problems.append("нет варианта эталона с буквой «ё» (был восстановлен в tsk-687)")
    return problems


async def main() -> int:
    ap = argparse.ArgumentParser(description="tsk-732: условие задания 217")
    ap.add_argument("--apply", action="store_true", help="записать в прод-БД")
    ap.add_argument("--show", action="store_true", help="показать стем целиком")
    args = ap.parse_args()

    stem_problems = _check_new_stem()
    if stem_problems:
        print("СТОП: новый стем не прошёл проверку:")
        for p in stem_problems:
            print(f"  - {p}")
        return 2

    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT id, course_id, external_uid, is_active, task_content, solution_rules "
            "FROM tasks WHERE id = $1",
            TASK_ID,
        )
        if row is None:
            print(f"СТОП: задание {TASK_ID} не найдено.")
            return 2
        if row["course_id"] != EXPECTED_COURSE:
            print(f"СТОП: {TASK_ID} не в курсе {EXPECTED_COURSE} (сейчас {row['course_id']}).")
            return 2
        if row["external_uid"] != EXPECTED_UID:
            print(f"СТОП: ключ {row['external_uid']!r}, ожидался {EXPECTED_UID!r}.")
            return 2
        if not row["is_active"]:
            print(f"СТОП: {TASK_ID} неактивно.")
            return 2

        content = json.loads(row["task_content"])
        rules = json.loads(row["solution_rules"]) if row["solution_rules"] else {}

        if content.get("type") != "TBL_COM" or (content.get("table") or {}).get("columns") != 1:
            print(f"СТОП: тип {content.get('type')!r}, table={content.get('table')!r} — "
                  "ожидался TBL_COM с одной колонкой (форма «строка ответа = "
                  "напечатанная строка», на которую опирается новое условие).")
            return 2

        rule_problems = _check_rules(rules)
        if rule_problems:
            print("СТОП: эталон не соответствует ожидаемому — условие вслепую не правлю:")
            for p in rule_problems:
                print(f"  - {p}")
            return 2

        old_stem = content.get("stem") or ""
        if old_stem == NEW_STEM:
            print(f"  {TASK_ID}: условие уже переписано, пропускаю")
            print("\nDry-run: записи не было. К обновлению 0 заданий.")
            return 0
        if old_stem != OLD_STEM:
            print("СТОП: текущий стем не совпадает с ожидаемым — его правили после "
                  f"разведки, вслепую не переписываю.\n--- сейчас ---\n{old_stem}")
            return 2

        content = dict(content)
        content["stem"] = NEW_STEM
        print(f"\n  {TASK_ID}: стем {len(old_stem)} → {len(NEW_STEM)} символов; "
              "«весь вывод программы» → «строки, напечатанные через print», "
              "названы приглашение и набранное, добавлен образец формы ответа")
        print("  эталон: не трогаем (три строки, варианты «е»/«ё» на месте)")
        if args.show:
            print("\n=== БЫЛО ===\n" + old_stem)
            print("\n=== СТАЛО ===\n" + NEW_STEM)

        if not args.apply:
            print("\nDry-run: записи не было. К обновлению 1 задание.")
            return 0

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.audit_actor', 'tsk-732', true)")
            await conn.execute(
                "UPDATE tasks SET task_content = $2::jsonb WHERE id = $1",
                TASK_ID, json.dumps(content, ensure_ascii=False),
            )
        print("\nОбновлено заданий: 1")

        print("\n=== ПОСЛЕ ===")
        check = await conn.fetchrow(
            "SELECT length(task_content->>'stem') AS len, "
            "(task_content->>'stem' LIKE '%весь вывод программы%') AS staraya_formulirovka, "
            "(task_content->>'stem' LIKE '%Образец формы ответа%') AS est_obrazets, "
            "(task_content->>'stem' LIKE '%через `print`%') AS est_kriteriy, "
            "(task_content->>'stem' LIKE '%**%') AS syraya_razmetka, "
            "task_content->>'type' AS tip, task_content->'table' AS tabl, "
            "jsonb_array_length(solution_rules->'short_answer'->'accepted_answers') AS etalonov "
            "FROM tasks WHERE id = $1",
            TASK_ID,
        )
        print(f"  {TASK_ID}: длина={check['len']} образец={check['est_obrazets']} "
              f"критерий_print={check['est_kriteriy']} "
              f"старая_формулировка={check['staraya_formulirovka']} "
              f"сырая_разметка={check['syraya_razmetka']} "
              f"тип={check['tip']} таблица={check['tabl']} эталонов={check['etalonov']}")
        ok = (
            check["est_obrazets"]
            and check["est_kriteriy"]
            and not check["staraya_formulirovka"]
            and not check["syraya_razmetka"]
            and check["etalonov"] == 2
        )
        return 0 if ok else 3
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
