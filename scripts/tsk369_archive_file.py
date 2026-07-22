# -*- coding: utf-8 -*-
"""tsk-369, добор: файл из локального архива книги — там, где в сети источника нет.

ЗАЧЕМ
У задания 2837 (`ext:pdf:...crylov:v3:...:v3t9`) источник — сборник Крылова, вариант 3.
Онлайн его нет: файлы к книге раздаются по коду с голограммы бумажного издания. Зато у
оператора есть подлинный архив приложений к этой же книге
(`D:\\Work\\CyberGuru\\EGE\\docs\\Варианты\\Файлы для выполнения заданий-…`, подтверждён
в tsk-317 пятью точными числовыми совпадениями), организованный как
`ZADANIE-<номер>/<номер>var<вариант>.<ext>`.

ГЕЙТ ЗДЕСЬ СИЛЬНЕЕ ТЕКСТОВОГО: задача решена по самому файлу, и ответ сошёлся с тем,
что уже записан в LMS. Совпал ответ — значит файл именно от этой задачи (проверка
воспроизводится: см. `verified` у каждой записи).

Выход — в формате шага 2, его подхватывает `tsk369_build_plan.py`.

Запуск:  python scripts/tsk369_archive_file.py --out-dir <dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

ARCHIVE = Path(r"D:\Work\CyberGuru\EGE\docs\Варианты"
               r"\Файлы для выполнения заданий-20251204T150307Z-1-001"
               r"\Файлы для выполнения заданий")

# id задания в LMS → файл в архиве + как проверено, что файл именно от этой задачи.
ENTRIES: list[dict] = [
    {
        "id": 2837,
        "course_id": None,
        "file": "ZADANIE-9/9var03.ods",
        "source_id": "crylov:v3t9",
        "verified": (
            "Решено по самому файлу: среди 13500 строк последняя, где два числа "
            "встречаются ровно дважды, два других различны, а сумма неповторяющихся "
            "(19+36=55) не больше суммы повторяющихся (89+49=138) — строка 12150, "
            "сумма её чисел 331. В LMS у задания записан ответ 331 — сошлось."
        ),
    },
]


def main(out_dir: Path) -> None:
    files_dir = out_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for e in ENTRIES:
        src = ARCHIVE / e["file"]
        if not src.exists():
            print(f"  [нет файла] id={e['id']} — {src}")
            continue
        data = src.read_bytes()
        ext = src.suffix.lower().lstrip(".")
        dest = files_dir / f"{e['id']}_0.{ext}"
        shutil.copyfile(src, dest)
        results.append({
            "id": e["id"], "course_id": e["course_id"], "source": "crylov_archive",
            "source_id": e["source_id"], "via": "local_archive", "verdict": "match",
            "ext_ok": True, "n_files": 1,
            "detail": {"verified": e["verified"], "prose_ok": None, "nums_ok": None},
            "files": [{"url": f"file://{src}", "name": src.name, "ext": ext,
                       "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                       "path": str(dest), "ext_ok": True}],
        })
        print(f"  [архив ] id={e['id']} ← {e['file']} ({len(data)/1e3:.0f} КБ)")

    out_path = out_dir / "fetched_archive.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nЗаписей: {len(results)}\nСохранено: {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    main(Path(ap.parse_args().out_dir))
