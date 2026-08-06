"""tsk-572: перенос ключа CloseRouter из ContentBackbone в .env LMS.

Зачем отдельный скрипт. В LMS до сих пор не было ни одной LLM-переменной
(проверено на проде — ноль), а ключ уже живёт в CB под legacy-именем
`CB_CLAUDE_API_KEY`. Копировать руками — значит однажды напечатать секрет
в терминал или в лог сессии агента.

Имя в LMS — `CLOSEROUTER_API_KEY`, канон ADR-0046 (`<PROVIDER>_*` имеет
приоритет над legacy-алиасами CB). Legacy-имя `CB_CLAUDE_*` намеренно НЕ
переносится: в LMS оно ничего не значит и только тянуло бы за собой чужую
историю именований.

Безопасность: значение ключа не печатается никогда — ни в stdout, ни в
логах. Показывается только длина и последние 4 символа для сверки.
`.env` в LMS в .gitignore (проверено), в git не попадёт.

Использование:
    python scripts/import_llm_key_from_cb.py            # dry-run, ничего не пишет
    python scripts/import_llm_key_from_cb.py --apply    # записать в .env LMS
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import sys
from datetime import datetime

try:
    from dotenv import dotenv_values
except ImportError:
    sys.exit("нужен python-dotenv: pip install python-dotenv")

CB_ENV = pathlib.Path(r"D:\Work\ContentBackbone\.env")
LMS_ENV = pathlib.Path(__file__).resolve().parent.parent / ".env"

# Канон ADR-0046: подключение общее, политика локальная.
KEY_TARGET = "CLOSEROUTER_API_KEY"
KEY_SOURCES = ("CLOSEROUTER_API_KEY", "CB_CLAUDE_API_KEY")
BASE_TARGET = "CLOSEROUTER_BASE_URL"
BASE_SOURCES = ("CLOSEROUTER_BASE_URL", "CB_CLAUDE_BASE_URL")


def _mask(value: str) -> str:
    """Маска для сверки: длина + хвост. Само значение не раскрывается."""
    if not value:
        return "<пусто>"
    tail = value[-4:] if len(value) > 8 else "?"
    return f"<{len(value)} символов, хвост …{tail}>"


def _read_source() -> tuple[str, str | None]:
    if not CB_ENV.exists():
        sys.exit(f"не найден {CB_ENV}")
    cb = dotenv_values(CB_ENV, encoding="utf-8-sig")
    key = next((cb[n] for n in KEY_SOURCES if cb.get(n)), None)
    if not key:
        sys.exit(f"в {CB_ENV} нет ни одной из переменных: {', '.join(KEY_SOURCES)}")
    base = next((cb[n] for n in BASE_SOURCES if cb.get(n)), None)
    return key, base


def _upsert(lines: list[str], name: str, value: str) -> tuple[list[str], str]:
    """Заменить строку `name=` или дописать в конец. Возвращает (строки, действие)."""
    prefix = f"{name}="
    for i, line in enumerate(lines):
        if line.lstrip().startswith(prefix):
            if line.rstrip("\n") == f"{prefix}{value}":
                return lines, "без изменений"
            lines[i] = f"{prefix}{value}\n"
            return lines, "обновлено"
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{prefix}{value}\n")
    return lines, "добавлено"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="записать (без флага — dry-run)")
    args = ap.parse_args()

    key, base = _read_source()
    print(f"источник : {CB_ENV}")
    print(f"  ключ   : {_mask(key)}")
    print(f"  base   : {base or '<не задан, будет дефолт клиента>'}")
    print(f"цель     : {LMS_ENV}")
    print(f"  имя    : {KEY_TARGET}  (канон ADR-0046, legacy CB_CLAUDE_* не переносится)")

    if not LMS_ENV.exists():
        sys.exit(f"не найден {LMS_ENV} — создайте из .env.example перед импортом")

    lines = LMS_ENV.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    planned: list[tuple[str, str, str]] = [(KEY_TARGET, key, "секрет")]
    if base:
        planned.append((BASE_TARGET, base, base))

    for name, value, shown in planned:
        probe, action = _upsert(list(lines), name, value)
        del probe
        print(f"  {name:<22} -> {action}" + (f" ({shown})" if shown != "секрет" else ""))

    # Кириллица в прод-.env уже ломала чтение настроек — проверяем и здесь.
    for name, value, _ in planned:
        if not value.isascii():
            sys.exit(f"ОТКАЗ: значение {name} содержит не-ASCII символы. "
                     "В .env допустима только латиница.")

    if not args.apply:
        print("\nDRY-RUN. Ничего не записано. Повторите с --apply.")
        return

    # NB: не with_suffix — у файла ".env" суффиксом считается ".env",
    # и получилось бы ".env.env.bak-…".
    backup = LMS_ENV.parent / f".env.bak-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(LMS_ENV, backup)
    for name, value, _ in planned:
        lines, _action = _upsert(lines, name, value)
    LMS_ENV.write_text("".join(lines), encoding="utf-8")
    print(f"\nЗаписано. Резервная копия: {backup.name}")


if __name__ == "__main__":
    main()
