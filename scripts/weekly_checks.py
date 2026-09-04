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

Коды выхода: 0 — чек отработал (находки есть или их нет — оба исхода штатные);
2 — чек не отработал. Флаг ``--fail-on-findings`` возвращает 1 при находках: он для
обвязки, которой нужен машинный признак, а не для планировщика.

Почему находки больше не дают 1 (tsk-777). Планировщик Windows семантики кода не знает
и красит любой ненулевой результат как ошибку: `LastTaskResult = 1`. Четыре задачи из
пяти находят что-то каждую неделю, поэтому месяцами стояли «с ошибкой», отрабатывая
штатно. Настоящий сбой в этом ряду было бы не отличить — а ради его заметности чеки и
заведены. Теперь ненулевой результат в планировщике означает ровно одно: чек не дошёл
до конца. Сами находки живут в журналах: подробности — в журнале чека, одна строка
итога на каждый прогон — в общем ``logs/weekly_checks.log``.
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

# Сводный журнал: по строке на каждый прогон любого чека. Журнал чека отвечает на вопрос
# «что именно нашли», этот — на вопрос «отработали ли чеки на этой неделе и у кого есть
# что смотреть». Раньше такого места не было: пять журналов, и чтобы понять картину,
# надо было открыть все пять и сверить даты.
SUMMARY_LOG = "weekly_checks.log"


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
    "external-media": Check(
        module="check_external_stem_media",
        log="external_media_check.log",
        needs_dsn=True,
        ok="OK: все картинки заданий и материалов на разрешённых адресах",
        found="НАЙДЕНЫ картинки, которые браузер ученика не покажет:",
        hint=(
            "  Как чинить — tsk-759 в трекере: перенос в CAS "
            r"(CB scripts\tsk759_external_images_to_cas.py) и перезапись ссылки "
            r"(scripts\tsk759_rewrite_external_images.py)."
        ),
        takes_quiet=True,
    ),
    "slow-requests": Check(
        module="check_slow_requests",
        log="slow_requests_check.log",
        needs_dsn=True,
        ok="OK: запросов дольше порога не было",
        found="МЕДЛЕННЫЕ ЗАПРОСЫ:",
        hint="  Как читать — tsk-644 в трекере.",
        takes_quiet=True,
        # Сводка печатает шапку «База: …» только в подробном режиме, поэтому в
        # тихом любая строка при коде 0 — это фон медленных запросов ниже порога
        # тревоги. Такой фон видеть полезно: по нему заметно, что дни стали
        # тяжелее, ещё до того как это станет затором.
        report_on_zero=True,
    ),
    "tutor-outcomes": Check(
        module="check_tutor_outcomes",
        log="tutor_outcomes_check.log",
        needs_dsn=True,
        ok="OK: с наставником всё в порядке",
        found="НАСТАВНИК:",
        hint="  Что с этим делать — tsk-661 в трекере (охват — tsk-659, молчание модели — tsk-678).",
        takes_quiet=True,
        # Здесь важен именно фон, а не только тревога: главный смысл чека — чтобы
        # пара цифр «сколько поводов дошло» и «чем кончилось» попадала в журнал
        # КАЖДУЮ неделю. Молчание при нуле находок вернуло бы ровно ту слепоту,
        # ради которой чек и заведён: контур полтора месяца выглядел работающим,
        # потому что никто не видел его цифр.
        report_on_zero=True,
    ),
    "etalon-punctuation": Check(
        module="check_etalon_punctuation",
        log="etalon_punctuation_check.log",
        needs_dsn=True,
        ok="OK: эталонов с мусорным ведущим знаком нет",
        found="НАЙДЕНЫ эталоны с мусорным ведущим знаком:",
        hint=(
            "  Как чинить — tsk-787 в трекере "
            r"(scripts\tsk787_strip_leading_dash_etalons.py). Приёму ответа такой "
            "эталон не мешает, поэтому тесты его не видят: цена в том, что "
            "преподаватель принимает бессмыслицу за битый эталон и засчитывает "
            "неверный ответ."
        ),
        takes_quiet=True,
        # Молчание при нуле: класс редкий (41 эталон за всю историю базы), и подшивать
        # «OK» каждую неделю значит утопить настоящую находку. Фон здесь не нужен —
        # в отличие от медленных запросов, цифра «сколько мусора» не растёт постепенно.
    ),
    "cb-drift": Check(
        module="check_cb_drift",
        log="cb_drift_check.log",
        needs_dsn=True,
        ok="OK: все расхождения с источником помечены как ручная правка",
        found="НАЙДЕНЫ правки без пометки:",
        hint=(
            "  Как чинить — tsk-760 в трекере: проставить пометку "
            r"(scripts\tsk760_mark_manual_edits.py, сухой прогон, затем "
            "DBCHECK_OK=1 ... --apply). Без пометки правку затрёт ближайшее "
            "переиздание курса из ContentBackbone."
        ),
        takes_quiet=True,
        # Фон здесь не нужен: чек печатает счётчики сверки и при нуле находок, а
        # подшивать их каждую неделю значит утопить настоящую находку. Числа
        # попадают в журнал чека и без этого — строкой ok.
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


def soften_stdout() -> None:
    """Разрешить выводу терять символы, которых нет в кодировке консоли.

    ``python scripts/weekly_checks.py --list`` падал с ``UnicodeEncodeError`` на стрелке
    ``→``: консоль Windows под русской локалью — cp1251, такого символа там нет (журнал
    26.08 и 01.09). Справка о чеках не работала ровно там, где к ней и обращаются — в
    обычном окне терминала. Сама стрелка заменена на ASCII, но чинить символ по одному
    каждый раз — значит ждать следующего: испорченный знак в выводе лучше, чем оборванный
    чек.

    Под ``pythonw`` потока нет вовсе (``sys.stdout is None``) — тогда делать нечего.
    """
    stream = sys.stdout
    if stream is None or not hasattr(stream, "reconfigure"):
        return
    try:
        stream.reconfigure(errors="replace")
    except (ValueError, OSError):  # поток уже подменён или закрыт — не повод падать
        pass


# --- Сводка в Telegram (tsk-778) -------------------------------------------------
#
# Журналы месяцами лежали непрочитанными: чек про наставника четвёртую неделю подряд
# писал «охват 5% при пороге 20%», и это никуда не попадало. Раз в неделю одно
# сообщение оператору — и только когда есть что сказать: молчание тоже сигнал, иначе
# сводка через месяц станет фоном, как журналы.

# Кого ждём в понедельник — ровно те шесть задач, что стоят в планировщике (сверено
# 04.09.2026 через Get-ScheduledTask). missing-attachments и external-media туда не
# заводили: их молчание — норма, и «тревога» о нём каждую неделю обесценила бы сводку
# быстрее, чем любая другая ошибка. Заведёшь задачу — впиши чек сюда.
# Смысл списка обратный перечислению: чек, который НЕ отчитался, опаснее любой находки —
# он выглядит как «всё хорошо».
DIGEST_EXPECTED = ("cb-drift", "section-order", "ungradable", "stale-verdicts",
                   "slow-requests", "tutor-outcomes")

# Канал оператора — тот же бот, которым Claude пишет ему в Telegram (ADR-0001 Root).
# Заводить второго бота ради шести строк в неделю незачем.
TG_ENV = Path.home() / ".claude" / "channels" / "telegram" / ".env"
TG_CHAT_ID_DEFAULT = "344276500"  # оператор; ср. Root tools/scripts/digest.ps1


def tg_credentials() -> tuple[str, str]:
    """Токен бота и чат оператора.

    Порядок: переменные окружения проекта (их можно положить в ``.env`` LMS), иначе —
    ``.env`` телеграм-канала Claude. Второй источник — чужая папка, и в tsk-641 от таких
    зависимостей уходили; здесь она допустима только потому, что её пропажа не может
    пройти молча: без токена сводка не уйдёт, а вызывающий получит ``RuntimeError`` и
    ненулевой код задачи.

    :raises RuntimeError: токена нет ни там, ни там.
    """
    token = os.getenv("WEEKLY_CHECKS_TG_TOKEN", "").strip()
    chat = os.getenv("WEEKLY_CHECKS_TG_CHAT_ID", "").strip() or TG_CHAT_ID_DEFAULT
    if token:
        return token, chat

    if not TG_ENV.exists():
        raise RuntimeError(
            f"нет токена бота: переменная WEEKLY_CHECKS_TG_TOKEN пуста и файла {TG_ENV} нет"
        )
    for line in TG_ENV.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "TELEGRAM_BOT_TOKEN" and value.strip():
            return value.strip(), chat
    raise RuntimeError(f"в {TG_ENV} нет непустого TELEGRAM_BOT_TOKEN")


def summary_lines_for(day: str) -> list[str]:
    """Строки сводного журнала за указанный день (``ГГГГ-ММ-ДД``).

    Продолжения многострочных записей (трейсбек «ОШИБКИ обёртки») отбрасываются: в
    сводку идёт факт и адрес журнала, а не стек.
    """
    path = LOG_DIR / SUMMARY_LOG
    if not path.exists():
        return []
    return [
        line.rstrip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(day)
    ]


def parse_summary(lines: list[str]) -> dict[str, str]:
    """Итог каждого чека за день: ``{имя чека: что записано}``.

    Разбор намеренно строгий — берутся только строки вида «дата время имя-чека текст»,
    где имя есть в реестре. Свободные пометки, которые человек дописывает в журнал
    рукой, содержат и слово «СБОЙ», и что угодно ещё; принимать их за состояние чека —
    значит слать оператору тревогу о том, что он сам же и написал.

    Повтор за день (чек прогнали руками после планировщика) перекрывает прежнюю
    запись: в сводке важно последнее известное состояние, а не история дня.
    """
    known = set(CHECKS)
    result: dict[str, str] = {}
    for line in lines:
        parts = line.split(maxsplit=3)
        if len(parts) < 4 or parts[2] not in known:
            continue
        result[parts[2]] = parts[3].strip()
    return result


def build_digest(day: str, lines: list[str]) -> Optional[str]:
    """Текст сообщения — или ``None``, если тревожить оператора нечем.

    Тревога — это находки, сбой чека и молчание чека. Последнее особенно: раз в неделю
    задача может не отработать вовсе (машина спала, задача снята, интерпретатор переехал),
    и тогда в журнале просто не появится строки — отказ, который выглядит как тишина.
    """
    state = parse_summary(lines)
    missing = [name for name in DIGEST_EXPECTED if name not in state]
    findings = {n: t for n, t in state.items() if "ЕСТЬ НАХОДКИ" in t}
    failures = {n: t for n, t in state.items() if n not in findings and "СБОЙ" in t}
    if not findings and not failures and not missing:
        return None

    stamp = datetime.strptime(day, "%Y-%m-%d").strftime("%d.%m.%Y")
    parts = [f"LMS, еженедельные чеки за {stamp}", ""]

    if failures:
        parts.append("НЕ ОТРАБОТАЛИ:")
        parts += [f"  - {name}: {text}" for name, text in failures.items()]
        parts.append("")
    if missing:
        parts.append("МОЛЧАТ (сегодня не отчитались вовсе):")
        parts += [f"  - {name}" for name in missing]
        parts.append("")
    if findings:
        parts.append("Есть находки:")
        parts += [f"  - {name}: {text}" for name, text in findings.items()]
        parts.append("")

    parts.append(f"Подробности: {LOG_DIR / SUMMARY_LOG}")
    return "\n".join(parts)


def send_telegram(text: str) -> None:
    """Отправить сообщение оператору.

    :raises RuntimeError: Telegram не принял сообщение. Отказ доставки обязан быть
        ненулевым кодом задачи — молча потерянная сводка вернула бы ровно ту слепоту,
        ради которой она заведена.
    """
    import httpx

    token, chat = tg_credentials()
    response = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
        timeout=20.0,
    )
    if response.status_code != 200 or not response.json().get("ok"):
        # Токен в текст ошибки не попадает: она уходит в журнал.
        raise RuntimeError(f"Telegram ответил {response.status_code}: {response.text[:200]}")


def run_digest(day: Optional[str] = None) -> int:
    """Собрать сводку за день и отправить, если есть о чём.

    :returns: 0 — отправлено либо тревожить не о чем; 2 — сводку не удалось доставить.
    """
    day = day or datetime.now().strftime("%Y-%m-%d")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = build_digest(day, summary_lines_for(day))
    if text is None:
        write_log(SUMMARY_LOG, f"{stamp}  сводка          не нужна: находок и пропусков нет")
        return 0
    try:
        send_telegram(text)
    except Exception as err:  # noqa: BLE001 — под pythonw показать некому
        write_log(SUMMARY_LOG, f"{stamp}  сводка          НЕ ОТПРАВЛЕНА: {err}")
        return 2
    write_log(SUMMARY_LOG, f"{stamp}  сводка          отправлена оператору в Telegram")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    """Разобрать аргументы, выполнить чек, записать журнал."""
    parser = argparse.ArgumentParser(
        description="Еженедельные чеки прода LMS под планировщиком",
    )
    parser.add_argument("check", nargs="?", choices=sorted(CHECKS), help="какой чек выполнить")
    parser.add_argument("--list", action="store_true", help="перечислить доступные чеки")
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help=(
            "вернуть код 1, если чек что-то нашёл. Для обвязки, которой нужен машинный "
            "признак. Планировщику этот флаг не ставят: там ненулевой код должен означать "
            "только сбой (tsk-777)"
        ),
    )
    parser.add_argument(
        "--digest",
        action="store_true",
        help=(
            "не выполнять чек, а собрать итоги сегодняшних прогонов и отправить сводку "
            "оператору в Telegram — только если есть находки, сбой или молчащий чек "
            "(tsk-778)"
        ),
    )
    parser.add_argument(
        "--day",
        help="день для --digest в формате ГГГГ-ММ-ДД (по умолчанию сегодня)",
    )
    args = parser.parse_args(argv)

    if args.digest:
        return run_digest(args.day)

    if args.list or not args.check:
        soften_stdout()
        for name, check in sorted(CHECKS.items()):
            print(f"{name:22} {check.module}  ->  logs/{check.log}")
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
        write_log(SUMMARY_LOG, f"{stamp}  {args.check:18} СБОЙ — подробности в logs/{check.log}")
        return 2

    if code == 0 and (not output or not check.report_on_zero):
        write_log(check.log, f"{stamp}  {check.ok}{where}")
        summary = "чисто"
    elif code == 0:
        # Находок нет, но чек что-то напечатал — например, смежные сигналы сверки
        # незачётов (сменённый тип задания, работа, не прошедшая валидацию схемой).
        write_log(check.log, f"{stamp}  Находок нет, но есть что посмотреть{where}:\n{output}")
        summary = f"находок нет, но есть что посмотреть — logs/{check.log}"
    elif code == 1:
        block = f"{stamp}  {check.found}{where}\n{output}"
        if check.hint:
            block += f"\n{check.hint}"
        write_log(check.log, block)
        summary = f"ЕСТЬ НАХОДКИ — logs/{check.log}"
    else:
        write_log(check.log, f"{stamp}  ОШИБКА чека (код {code}){where}:\n{output}")
        write_log(SUMMARY_LOG, f"{stamp}  {args.check:18} СБОЙ (код {code}) — logs/{check.log}")
        return 2

    write_log(SUMMARY_LOG, f"{stamp}  {args.check:18} {summary}{where}")

    # Находки — штатный исход, а не отказ: планировщику они возвращаются нулём, иначе
    # задача годами стоит «с ошибкой» и настоящий сбой в этом ряду теряется (tsk-777).
    return 1 if (code == 1 and args.fail_on_findings) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 — последняя сетка: журнал важнее аккуратного стека
        write_log(
            SUMMARY_LOG,
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}  ОШИБКА обёртки:\n"
            f"{traceback.format_exc()}",
        )
        sys.exit(2)
