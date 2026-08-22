# -*- coding: utf-8 -*-
"""Единый вход планировщика для еженедельных чеков прода (tsk-641).

Зачем он есть. Каждый чек раньше запускался своей обёрткой на PowerShell, а
`powershell.exe` — консольная программа: планировщик создаёт ей окно и лишь потом
прячет, поэтому каждый понедельник на экране моргал чёрный прямоугольник. Этот вход
рассчитан на запуск через `pythonw.exe` — GUI-программу, которой консоль не
выделяется вовсе, так что окна не будет ни на мгновение.

Что делали обёртки, кроме запуска: доставали боевое подключение из `.mcp.json`
(в `.env` проекта лежит dev-база, tsk-246) и писали итог в журнал, потому что под
планировщиком читать вывод некому. Обе обязанности переехали сюда.

Заодно исчезла межпроектная зависимость: обёртка чека порядка разделов брала DSN из
`D:\\Work\\CreateCourses\\.mcp.json` — переезд или переименование чужой папки убили бы
чек молча. Подключение там ровно то же, что в `.mcp.json` самой LMS (сверено
2026-08-22: один хост, порт, база и роль), поэтому источник теперь один — свой.

Только чтение. Ни один из чеков не пишет в базу; соединение им нужно, чтобы
посмотреть, что видят ученики.

Запуск:
    .venv\\Scripts\\pythonw.exe scripts/weekly_checks.py stale-verdicts   # без окна
    python scripts/weekly_checks.py stale-verdicts                        # то же, видно вывод
    python scripts/weekly_checks.py --list                                # что вообще есть

Задачи планировщика ставит `scripts/install_weekly_checks.ps1`.

Коды выхода: 0 — чисто; 1 — есть находки; 2 — ошибка выполнения.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import io
import json
import os
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

LOG_DIR = PROJECT_ROOT / "logs"
MCP_CONFIG = PROJECT_ROOT / ".mcp.json"


@dataclass(frozen=True)
class Check:
    """Описание одного еженедельного чека.

    :param module: модуль в ``scripts/`` с функцией ``main()``, возвращающей код выхода.
    :param log: имя файла журнала в ``logs/``. Имена сохранены прежними — иначе
        история наблюдений разорвалась бы на середине.
    :param needs_dsn: нужно ли положить боевой DSN в ``DATABASE_URL`` перед импортом.
        У сверки устаревших незачётов False: она читает ``.mcp.json`` сама.
    :param ok: строка журнала, когда находок нет.
    :param found: заголовок журнала, когда находки есть.
    :param hint: подсказка «как чинить», дописывается под находками.
    :param report_on_zero: печатать ли в журнал вывод чека, когда находок нет.
        По умолчанию False: чеки порядка разделов и вложений даже в тихом режиме
        печатают шапку «База: …», и подшивать её каждую неделю — значит утопить
        настоящую находку в шуме. Прежние обёртки на PowerShell вывод при коде 0
        тоже отбрасывали. True стоит у сверки незачётов: её тихий режим по-настоящему
        молчит, поэтому любая строка при коде 0 — смежный сигнал, а не шапка.
    :param is_async: ``main()`` — корутина (её нужно прогнать через ``asyncio.run``).
    :param takes_quiet: ``main()`` принимает ``quiet=True`` вместо разбора ``sys.argv``.
        Единого соглашения у чеков нет: три писались в разное время, и приводить их
        к одной сигнатуре ради красоты — значит трогать работающий код без нужды.
        Дешевле описать различие здесь.
    """

    module: str
    log: str
    needs_dsn: bool
    ok: str
    found: str
    hint: str = ""
    report_on_zero: bool = False
    is_async: bool = True
    takes_quiet: bool = False


CHECKS: dict[str, Check] = {
    "ungradable": Check(
        module="check_ungradable_tasks",
        log="ungradable_tasks_check.log",
        needs_dsn=True,
        ok="OK: непроверяемых заданий нет",
        found="НАЙДЕНЫ непроверяемые задания:",
        hint="  Как чинить — tsk-361 в трекере.",
        takes_quiet=True,
    ),
    "section-order": Check(
        module="check_section_order",
        log="section_order_check.log",
        needs_dsn=True,
        ok="OK: порядок разделов верный",
        found="НАРУШЕН порядок разделов:",
        hint="  Как чинить — tsk-237 в трекере.",
    ),
    "missing-attachments": Check(
        module="check_missing_attachments",
        log="missing_attachments_check.log",
        needs_dsn=True,
        ok="OK: у всех заданий с файловым условием файл на месте",
        found="НАЙДЕНЫ задания без файла-приложения:",
        hint=r"  Как чинить — tsk-369 в трекере (скрипты scripts\tsk369_*.py).",
        takes_quiet=True,
    ),
    "stale-verdicts": Check(
        module="audit_stale_false_verdicts_tsk602",
        log="stale_verdicts_check.log",
        needs_dsn=False,
        ok="OK: устаревших незачётов нет",
        found="НАЙДЕНЫ устаревшие незачёты:",
        hint=(
            "  Сперва журнал правок эталона: SELECT changed_at, changed_by, "
            "old_answer_key, new_answer_key FROM task_audit WHERE task_id = <id> "
            "AND new_answer_key IS NOT NULL ORDER BY changed_at;"
        ),
        report_on_zero=True,
        is_async=False,
    ),
}


def prod_dsn() -> str:
    """Боевой DSN из ``.mcp.json`` в форме, которую ждёт SQLAlchemy.

    Схема приводится к ``postgresql+asyncpg://``: чек порядка разделов поднимает
    сессию через ``app.db.session`` и другую форму не принимает. Двум остальным
    чекам всё равно — они нормализуют схему сами, и повторное приведение им ничего
    не ломает (условие у них — ``startswith("postgresql://")``).

    :returns: строка подключения.
    :raises RuntimeError: файла нет либо в нём не нашлось боевого подключения.
        Молчаливый фолбэк на dev тут недопустим: чек отчитался бы «чисто» по
        пустой локальной базе.
    """
    if not MCP_CONFIG.exists():
        raise RuntimeError(f"не найден {MCP_CONFIG}")
    config = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))
    args = config.get("mcpServers", {}).get("learn_prod_db", {}).get("args", [])
    for arg in args:
        if isinstance(arg, str) and arg.startswith("postgresql"):
            if arg.startswith("postgresql://"):
                return arg.replace("postgresql://", "postgresql+asyncpg://", 1)
            return arg
    raise RuntimeError(f"в {MCP_CONFIG} нет боевого подключения learn_prod_db")


def _describe(dsn: str) -> str:
    """Куда чек сходил — без пароля.

    Строка попадает в журнал: молчаливая подмена базы (dev вместо прода) — самый
    неприятный отказ такого чека, и по журналу это должно быть видно.
    """
    parsed = urlparse(dsn)
    return f"{parsed.hostname}:{parsed.port or 5432}/{(parsed.path or '').lstrip('/')}"


def write_log(log_name: str, text: str) -> None:
    """Дописать блок в журнал чека.

    Под ``pythonw`` писать больше некуда: стандартного вывода у процесса нет, и всё,
    что не попало в файл, исчезает бесследно.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / log_name).open("a", encoding="utf-8") as fh:
        fh.write(text.rstrip() + "\n")


def run_check(check: Check) -> tuple[int, str]:
    """Выполнить один чек и вернуть его код выхода вместе с выводом.

    Чек импортируется и вызывается в этом же процессе — не подпроцессом. Так проще
    и нет второго запуска интерпретатора, но появляется требование: ``DATABASE_URL``
    обязан быть выставлен ДО импорта, потому что чек порядка разделов поднимает
    настройки приложения на уровне модуля.

    :returns: ``(код выхода, весь вывод чека)``.
    """
    buffer = io.StringIO()
    module = importlib.import_module(check.module)

    # Чеки, разбирающие аргументы внутри main(), читают sys.argv — подменяем его на
    # время вызова, иначе им прилетят аргументы этого скрипта и argparse оборвёт
    # процесс с «unrecognized arguments».
    saved_argv = sys.argv
    sys.argv = [f"{check.module}.py", "--quiet"]
    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            result = module.main(quiet=True) if check.takes_quiet else module.main()
            code = int(asyncio.run(result) if check.is_async else result)
    finally:
        sys.argv = saved_argv
    return code, buffer.getvalue().strip()


def main(argv: Optional[list[str]] = None) -> int:
    """Разобрать аргументы, выполнить чек, записать журнал."""
    parser = argparse.ArgumentParser(
        description="Еженедельные чеки прода LMS под планировщиком",
    )
    parser.add_argument("check", nargs="?", choices=sorted(CHECKS), help="какой чек выполнить")
    parser.add_argument("--list", action="store_true", help="перечислить доступные чеки")
    args = parser.parse_args(argv)

    if args.list or not args.check:
        for name, check in sorted(CHECKS.items()):
            print(f"{name:22} {check.module}  →  logs/{check.log}")
        return 0 if args.list else 2

    check = CHECKS[args.check]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Рабочий каталог — корень репозитория: настройки приложения создают uploads/*
    # ОТНОСИТЕЛЬНО cwd, а планировщик стартует в C:\Windows\System32, где mkdir
    # падает с WinError 5. Задача передаёт нужный каталог сама, но полагаться на
    # это нельзя — ручной запуск идёт откуда угодно.
    os.chdir(PROJECT_ROOT)

    # Обоим чекам, что зовут os.system("chcp ..."), под pythonw консоли нет — и
    # cmd.exe получил бы свою, то есть окно всё-таки моргнуло бы. Флаг гасит вызов.
    os.environ["LMS_CHECK_NO_CONSOLE"] = "1"

    where = ""
    try:
        if check.needs_dsn:
            dsn = prod_dsn()
            os.environ["DATABASE_URL"] = dsn
            where = f" [{_describe(dsn)}]"
        code, output = run_check(check)
    except Exception:  # noqa: BLE001 — под pythonw traceback показать некому
        write_log(check.log, f"{stamp}  ОШИБКА чека:\n{traceback.format_exc()}")
        return 2

    if code == 0 and (not output or not check.report_on_zero):
        write_log(check.log, f"{stamp}  {check.ok}{where}")
    elif code == 0:
        # Находок нет, но чек что-то напечатал — например, смежные сигналы сверки
        # незачётов (сменённый тип задания, работа, не прошедшая валидацию схемой).
        write_log(check.log, f"{stamp}  Находок нет, но есть что посмотреть{where}:\n{output}")
    elif code == 1:
        block = f"{stamp}  {check.found}{where}\n{output}"
        if check.hint:
            block += f"\n{check.hint}"
        write_log(check.log, block)
    else:
        write_log(check.log, f"{stamp}  ОШИБКА чека (код {code}){where}:\n{output}")

    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 — последняя сетка: журнал важнее аккуратного стека
        write_log(
            "weekly_checks.log",
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}  ОШИБКА обёртки:\n"
            f"{traceback.format_exc()}",
        )
        sys.exit(2)
