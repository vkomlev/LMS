# -*- coding: utf-8 -*-
"""Регулярный чек: задание правили руками, а признака этого нет (tsk-760).

Зачем. Источник истины по заданиям — LMS: их выверяют и правят здесь. Перенос из
ContentBackbone по умолчанию только создаёт новое, а осознанное переиздание курса
(`--republish`) обходит стороной задания с пометкой ручной правки. Вся защита
опирается на эту пометку — значит цена ошибки одна: правка БЕЗ пометки будет
затёрта ближайшим переизданием, и заметит это ученик, а не мы.

Признак ручной правки в самой базе появился только 01.09.2026 (`tasks.updated_at`
по триггеру, отпечаток условия в `task_audit`). Про всё, что правили раньше, база
молчит — и молчит про любую правку, сделанную прямым UPDATE мимо кабинета. Ответ
находится не в LMS, а в расхождении: CB хранит содержимое, которое отправлял.
Разошлось — значит после публикации кто-то правил.

Что делает чек. Раз в неделю прогоняет сверку CB ↔ LMS (`lms-drift-audit`,
только чтение с обеих сторон) и оставляет от её находок то, о чём мы ещё не
знаем: расхождения БЕЗ пометки ручной правки. Про помеченные молчит — они уже
защищены, и повторять их каждую неделю значит утопить новую находку в списке из
трёхсот старых. Находка здесь означает ровно одно действие: проставить пометку
(`scripts/tsk760_mark_manual_edits.py`, протокол db-check), после чего задание
переиздание больше не тронет, а чек про него замолчит.

Почему чек ходит в чужой проект. Сверка живёт в CB — там лежит вторая половина
данных (то, что отправляли), и переносить её сюда значило бы держать две копии
одной логики. Зависимость от чужой папки допустима потому, что её пропажа не
может пройти молча: без CB чек возвращает 2 и попадает в сводку строкой «СБОЙ»,
а не «чисто» (ср. tsk-641, где от таких зависимостей уходили именно из-за
молчаливого отказа).

Read-only: ни одного UPDATE ни здесь, ни в CB.

Куда смотрит. LMS — база из `DATABASE_URL`; по умолчанию это dev (прод от
скриптов закрыт, tsk-246), под планировщиком боевой DSN подставляет
`weekly_checks.py`. CB — своё окружение и свой `.env` (`CB_LMS_BASE_URL`
указывает на боевой кабинет). Скрипт всегда печатает, куда сходил.

Запуск из корня проекта:
    python scripts/check_cb_drift.py                 # полный отчёт
    python scripts/check_cb_drift.py --quiet         # только находки
    python scripts/check_cb_drift.py --uid-prefix tg:ege:     # одна партия
    python scripts/check_cb_drift.py --report out/drift.json  # готовый отчёт, без прогона
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

# tsk-641: под планировщиком чек идёт через pythonw.exe, где консоли нет вовсе.
if sys.platform == "win32" and not os.environ.get("LMS_CHECK_NO_CONSOLE"):
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

#: Где лежит ContentBackbone. Переопределяется переменной CB_PROJECT_ROOT — путь
#: захардкожен только как значение по умолчанию, чтобы переезд папки чинился
#: настройкой, а не правкой кода.
CB_ROOT = Path(os.environ.get("CB_PROJECT_ROOT", r"D:\Work\ContentBackbone"))

#: Сколько ждать сверку. Она опрашивает боевой кабинет по одному заданию: полторы
#: тысячи заданий — около двадцати минут (замер 04.09.2026: 475 заданий за 7:52).
#: Час — это уже не «медленно», а зависание; задача планировщика живёт 90 минут.
CB_TIMEOUT_SEC = int(os.environ.get("CB_DRIFT_TIMEOUT_SEC", "3600"))

#: Источники пометки, которые LMS считает ручной правкой (ср. HUMAN_EDIT_SOURCES
#: в app/services/tasks_service.py и в CB monolith/lms_client/create_only.py).
HUMAN_EDIT_SOURCES = ("manual_web", "manual_script")

SQL_MARKED = """
SELECT external_uid
FROM tasks
WHERE external_uid = ANY(:uids)
  AND content_provenance->>'source' = ANY(:sources)
"""

SQL_TASK_ROWS = """
SELECT external_uid, id, is_active
FROM tasks
WHERE external_uid = ANY(:uids)
"""


def run_drift_audit(uid_prefix: Optional[str]) -> tuple[dict[str, Any], str]:
    """Прогнать сверку в CB и вернуть её отчёт.

    :returns: ``(отчёт, куда сходили)``.
    :raises RuntimeError: CB не найден, не отработал или не отдал отчёт. Любой из
        этих случаев обязан быть ненулевым кодом чека: «сверка не прошла» и
        «расхождений нет» — разные вещи, и путать их нельзя.
    """
    python = CB_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = CB_ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        raise RuntimeError(f"не найдено окружение ContentBackbone: {CB_ROOT}\\.venv")

    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "drift.json"
        cmd = [str(python), "-m", "monolith", "lms-drift-audit", "--out", str(report_path)]
        if uid_prefix:
            cmd += ["--uid-prefix", uid_prefix]
        env = dict(os.environ)
        # У CB своё подключение к кабинету и своя база; DATABASE_URL этого чека —
        # про LMS, и подсовывать его чужому процессу нельзя.
        env.pop("DATABASE_URL", None)
        # Под планировщиком чек идёт через pythonw.exe — GUI-процесс без консоли.
        # Дочернему консольному питону Windows выделила бы своё окно, и каждый
        # понедельник на экране моргал бы чёрный прямоугольник: ровно то, от чего
        # уходили в tsk-641, только на шаг глубже.
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            done = subprocess.run(
                cmd, cwd=str(CB_ROOT), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=CB_TIMEOUT_SEC,
                creationflags=no_window,
            )
        except subprocess.TimeoutExpired as err:
            raise RuntimeError(f"сверка не уложилась в {CB_TIMEOUT_SEC} с") from err
        if not report_path.exists():
            tail = (done.stderr or done.stdout or "").strip()[-500:]
            raise RuntimeError(f"сверка не отдала отчёт (код {done.returncode}): {tail}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
    return report, str(CB_ROOT)


async def _select(dsn: str, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Один read-only запрос к базе LMS."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(dsn, echo=False)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text(sql), params)).mappings().all()
    finally:
        await engine.dispose()
    return [dict(row) for row in rows]


async def already_marked(dsn: str, uids: list[str]) -> set[str]:
    """Какие из этих заданий уже помечены как правленные руками."""
    if not uids:
        return set()
    rows = await _select(dsn, SQL_MARKED, {"uids": uids, "sources": list(HUMAN_EDIT_SOURCES)})
    return {row["external_uid"] for row in rows}


async def task_rows(dsn: str, uids: list[str]) -> dict[str, dict[str, Any]]:
    """Номер и активность по ключам — чтобы с находкой было с чем идти к заданию."""
    if not uids:
        return {}
    rows = await _select(dsn, SQL_TASK_ROWS, {"uids": uids})
    return {row["external_uid"]: row for row in rows}


def unreadable_uids(report: dict[str, Any]) -> list[str]:
    """Задания, про которые сверка ничего не утверждает: LMS их не отдала."""
    return [
        str(row.get("external_uid"))
        for row in report.get("rows") or []
        if row.get("status") == "unreadable"
    ]


async def main(quiet: bool = False, uid_prefix: Optional[str] = None,
               report_path: Optional[str] = None) -> int:
    """Сверить CB и LMS и показать расхождения без пометки. 1 — находки, 0 — чисто."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("ОШИБКА: не задан DATABASE_URL (ни в окружении, ни в .env)", file=sys.stderr)
        return 2

    safe = dsn.split("@")[-1] if "@" in dsn else dsn
    print(f"База: {safe}")

    if report_path:
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        print(f"Отчёт сверки: {report_path} (готовый, сверка не запускалась)")
    else:
        report, where = run_drift_audit(uid_prefix)
        print(f"Сверка CB: {where}")

    counts = report.get("counts") or {}
    edited = [str(uid) for uid in report.get("edited_in_lms") or []]
    unreadable = unreadable_uids(report)

    marked = await already_marked(dsn, edited)
    unmarked = [uid for uid in edited if uid not in marked]

    print(
        f"Сверено заданий: {report.get('total', 0)}; "
        f"совпало {counts.get('same', 0)}, разошлось {len(edited)} "
        f"(из них уже помечено {len(marked)}), в LMS нет {counts.get('missing_in_lms', 0)}"
    )

    if not unmarked and not unreadable:
        if not quiet:
            print("\nOK: все расхождения с источником уже помечены как ручная правка.")
        return 0

    if unreadable:
        print(f"\nНЕ УДАЛОСЬ ПРОЧИТАТЬ из LMS: {len(unreadable)}")
        for uid in unreadable[:10]:
            print(f"  {uid}")
        if len(unreadable) > 10:
            print(f"  … и ещё {len(unreadable) - 10}")
        print("  Про эти задания сверка ничего не утверждает — это не «чисто».")

    if unmarked:
        rows = await task_rows(dsn, unmarked)
        active = [uid for uid in unmarked if (rows.get(uid) or {}).get("is_active")]
        print(
            f"\nНАЙДЕНЫ правки без пометки: {len(unmarked)} "
            f"(активных заданий среди них: {len(active)})"
        )
        for uid in unmarked[:30]:
            row = rows.get(uid) or {}
            mark = "активное" if row.get("is_active") else "скрытое"
            print(f"  {uid} -> задание {row.get('id', '?')} ({mark})")
        if len(unmarked) > 30:
            print(f"  … и ещё {len(unmarked) - 30}")
        print(
            "\n  Чем это опасно: переиздание курса из ContentBackbone пропускает "
            "только задания с пометкой ручной правки. У этих её нет — правку затрёт "
            "ближайшее переиздание (tsk-760)."
        )
        print(
            "  Как чинить: проставить пометку — scripts/tsk760_mark_manual_edits.py "
            "(сухой прогон, затем DBCHECK_OK=1 ... --apply, протокол db-check)."
        )
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quiet", action="store_true", help="печатать только находки")
    ap.add_argument("--uid-prefix", dest="uid_prefix", help="сверять только одну партию ключей")
    ap.add_argument("--report", help="взять готовый отчёт сверки вместо своего прогона")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(main(quiet=args.quiet, uid_prefix=args.uid_prefix,
                                  report_path=args.report)))
    except Exception as exc:  # noqa: BLE001 — чек под планировщиком, причина обязана попасть в лог
        print(f"ОШИБКА выполнения чека: {exc}", file=sys.stderr)
        sys.exit(2)
