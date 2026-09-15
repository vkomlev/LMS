"""Разнести новую пару ключей S3 по всем потребителям, не печатая значений (tsk-736).

Источник правды — `D:\\Work\\Avito\\.env`: оператор вписывает туда новую пару
`S3_ACCESS_KEY` / `S3_SECRET_KEY` сам, в редакторе. Скрипт читает их оттуда и
кладёт в остальные места. **Ни одно значение не выводится и не попадает в
командную строку**: на сервер пара уезжает через stdin ssh-сессии.

Потребители (снято инвентарём 2026-09-15):
- `D:\\Work\\ContentBackbone\\.env` — cas_downloader, upload_screenshot_s3;
- `/opt/lms/.env` на lms-spw-vds — вложения, чеки, медиа (LMS API).

Полигон lms-poligon ключей не держит (только публичный URL), SPW и боты — тоже.

Порядок (§5а ownership.md): захват `/opt/lms/.env` взят ДО запуска, бэкап
кладётся рядом с файлом под именем задачи, после замены — рестарт lms и живая
проверка загрузки.

Запуск:  python scripts/rotate_s3_keys_tsk736.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

AVITO_ENV = Path(r"D:\Work\Avito\.env")
CB_ENV = Path(r"D:\Work\ContentBackbone\.env")
LMS_HOST = "lms-spw-vds"
LMS_ENV = "/opt/lms/.env"
KEYS = ("S3_ACCESS_KEY", "S3_SECRET_KEY")


def read_pair(path: Path) -> dict[str, str]:
    """Достать пару ключей из .env. Пустое значение — ошибка, не «оставить как есть»."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        for key in KEYS:
            if line.startswith(f"{key}="):
                values[key] = line.split("=", 1)[1].strip().strip('"').strip("'")
    missing = [k for k in KEYS if not values.get(k)]
    if missing:
        raise SystemExit(f"в {path} пусто: {missing} — сначала впишите новую пару")
    return values


def replace_in_text(text: str, pair: dict[str, str]) -> tuple[str, int]:
    """Заменить значения по именам; вернуть текст и число заменённых строк."""
    replaced = 0
    out: list[str] = []
    for line in text.splitlines():
        hit = next((k for k in KEYS if line.startswith(f"{k}=")), None)
        if hit:
            out.append(f"{hit}={pair[hit]}")
            replaced += 1
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), replaced


def update_local(path: Path, pair: dict[str, str], dry_run: bool) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, replaced = replace_in_text(text, pair)
    if replaced != len(KEYS):
        raise SystemExit(f"{path}: найдено {replaced} строк из {len(KEYS)} — не трогаю")
    same = new_text == text
    print(f"{path}: {replaced} строки, {'уже совпадает' if same else 'заменю'}"
          f"{' (dry-run)' if dry_run else ''}")
    if not dry_run and not same:
        backup = path.with_name(f"{path.name}.bak-tsk736-{date.today():%Y%m%d}")
        backup.write_text(text, encoding="utf-8")
        path.write_text(new_text, encoding="utf-8")


#: Программа для сервера. Секретов в ней нет — они приходят по stdin.
_REMOTE_PY = r'''import sys
path = sys.argv[1]
access = sys.stdin.readline().rstrip("\n")
secret = sys.stdin.readline().rstrip("\n")
if not access or not secret:
    raise SystemExit("пара не пришла по stdin")
lines = open(path, encoding="utf-8").read().split("\n")
out = []
for line in lines:
    if line.startswith("S3_ACCESS_KEY="):
        out.append("S3_ACCESS_KEY=" + access)
    elif line.startswith("S3_SECRET_KEY="):
        out.append("S3_SECRET_KEY=" + secret)
    else:
        out.append(line)
open(path, "w", encoding="utf-8").write("\n".join(out))
print("заменено")
'''


def _ssh(command: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["ssh", LMS_HOST, command],
        input=stdin, capture_output=True, text=True, encoding="utf-8",
    )
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"{LMS_HOST}: код {proc.returncode}")
    return proc


def update_remote(pair: dict[str, str], dry_run: bool) -> None:
    """Заменить пару в /opt/lms/.env на сервере.

    Значения не попадают ни в argv (sudo пишет командную строку в журнал — урок
    tsk-402), ни в текст скрипта на сервере: python читает их из stdin. В /tmp
    остаётся только программа без секретов, и та удаляется.
    """
    checks = (
        f"set -e; F={LMS_ENV}; "
        "for k in S3_ACCESS_KEY S3_SECRET_KEY; do "
        "n=$(sudo grep -c \"^$k=\" \"$F\" || true); "
        "[ \"$n\" = 1 ] || { echo \"в $F строк $k: $n — не трогаю\"; exit 2; }; done; "
        "echo \"$F: обе строки на месте\""
    )
    _ssh(checks)
    if dry_run:
        print("dry-run: сервер не трогаю")
        return
    _ssh("cat > /tmp/rot736.py", stdin=_REMOTE_PY)
    apply_cmd = (
        f"set -e; F={LMS_ENV}; B=$F.bak-tsk736-$(date +%Y%m%d-%H%M%S); "
        "sudo cp \"$F\" \"$B\"; sudo python3 /tmp/rot736.py \"$F\"; "
        "rm -f /tmp/rot736.py; echo \"бэкап: $B\""
    )
    _ssh(apply_cmd, stdin=pair["S3_ACCESS_KEY"] + "\n" + pair["S3_SECRET_KEY"] + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="только проверить, ничего не менять")
    args = parser.parse_args()

    pair = read_pair(AVITO_ENV)
    for key in KEYS:
        if not re.fullmatch(r"[A-Za-z0-9+/=_-]{8,}", pair[key]):
            raise SystemExit(f"{key} в {AVITO_ENV} выглядит не как ключ — проверьте, что вписали")
    print(f"источник: {AVITO_ENV} — пара на месте (значения не печатаю)")

    update_local(CB_ENV, pair, args.dry_run)
    update_remote(pair, args.dry_run)


if __name__ == "__main__":
    main()
