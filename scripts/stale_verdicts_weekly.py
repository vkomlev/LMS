# -*- coding: utf-8 -*-
"""Еженедельный запуск сверки устаревших незачётов под планировщиком (tsk-636).

Зачем отдельный вход, а не .ps1 как у соседних чеков. Соседям
(`check_ungradable_tasks_weekly.ps1`, `check_section_order_weekly.ps1`) обёртка на
PowerShell нужна ради одного — подсунуть прод-DSN в `DATABASE_URL`. Здесь этого не
требуется: `audit_stale_false_verdicts_tsk602.py` читает подключение из `.mcp.json`
сам. Остаётся только журналирование, и ради него запускать PowerShell невыгодно:
`powershell.exe` — консольная программа, планировщик создаёт ей окно и лишь потом
прячет, поэтому раз в неделю на экране моргает чёрный прямоугольник. `pythonw.exe`
собран как GUI-программа, консоль ему не выделяется вовсе — окна не будет ни на
мгновение.

Тихий вариант «выполнять независимо от того, вошёл ли пользователь» (S4U) на этой
машине недоступен: регистрация такой задачи требует права «Вход в качестве пакетного
задания» и без администратора отвечает «Access is denied».

Только чтение. Скрипт ничего не пишет в базу — он вызывает read-only аудит.

Запуск (обычно — планировщиком, см. `scripts/install_stale_verdicts_check.ps1`):
    .venv\\Scripts\\pythonw.exe scripts/stale_verdicts_weekly.py   # без окна
    python scripts/stale_verdicts_weekly.py                        # то же, но видно вывод

Коды выхода: 0 — расхождений нет; 1 — найдены; 2 — ошибка выполнения.
"""

from __future__ import annotations

import io
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

LOG_PATH = PROJECT_ROOT / "logs" / "stale_verdicts_check.log"


def _write_log(text: str) -> None:
    """Дописать блок в журнал чека.

    Отдельная функция, потому что под `pythonw` писать больше некуда: стандартного
    вывода у процесса нет, и всё, что не попало в файл, исчезает бесследно.

    :param text: готовый блок строк (без завершающего перевода строки).
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(text.rstrip() + "\n")


def main() -> int:
    """Прогнать аудит, положить итог в журнал, вернуть код выхода планировщику."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    buffer = io.StringIO()

    try:
        # Импорт внутри try: отсутствующий psycopg2 или сломанный аудит — такая же
        # ошибка чека, как и падение запроса, и обязана попасть в журнал, а не
        # уронить процесс молча (под pythonw traceback показать некому).
        from audit_stale_false_verdicts_tsk602 import main as audit_main

        # Аудит рассчитан на аргументы командной строки; тихий режим — его же флаг.
        sys.argv = ["audit_stale_false_verdicts_tsk602.py", "--quiet"]
        with redirect_stdout(buffer), redirect_stderr(buffer):
            code = audit_main()
    except Exception:  # noqa: BLE001 — причина обязана попасть в журнал целиком
        _write_log(f"{stamp}  ОШИБКА чека:\n{traceback.format_exc()}")
        return 2

    output = buffer.getvalue().strip()

    if code == 0 and not output:
        _write_log(f"{stamp}  OK: устаревших незачётов нет")
    elif code == 0:
        # Расхождений нет, но аудит нашёл смежные сигналы (сменённый тип задания,
        # работа, не прошедшая валидацию схемой) — это тоже повод посмотреть.
        _write_log(f"{stamp}  Расхождений нет, но есть что посмотреть:\n{output}")
    elif code == 1:
        _write_log(
            f"{stamp}  НАЙДЕНЫ устаревшие незачёты:\n{output}\n"
            "  Сперва журнал правок эталона: SELECT changed_at, changed_by, "
            "old_answer_key, new_answer_key FROM task_audit WHERE task_id = <id> "
            "AND new_answer_key IS NOT NULL ORDER BY changed_at;"
        )
    else:
        _write_log(f"{stamp}  ОШИБКА чека (код {code}):\n{output}")

    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — последняя сетка: журнал важнее аккуратного стека
        _write_log(
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}  ОШИБКА обёртки:\n"
            f"{traceback.format_exc()}"
        )
        sys.exit(2)
