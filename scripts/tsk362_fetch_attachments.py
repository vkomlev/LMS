# -*- coding: utf-8 -*-
"""tsk-362, добор через файлы-приложения: скачать данные задачи и её ответ из источника.

ЗАЧЕМ
У оставшихся заданий сверка «текст + числа» не срабатывает по объективной причине: они
серии 17–27, где условие короткое и одинаковое у целого класса задач, а различают их **данные
в приложенном файле**. В LMS файлов нет вовсе (`task_content.media` пуст у всех), поэтому
числового признака взяться неоткуда.

Выход: у источника файл есть. `GET https://kompege.ru/api/v1/task/<id>` возвращает и ответ
(`key`), и список приложений (`files[].url`), которые лежат на том же домене. Скачав файл,
задачу можно **решить самостоятельно и сверить с ответом источника** — это сильнее прежнего
гейта: совпало решение по данным → и задача та самая, и ответ верный.

Скрипт только собирает материал: условие LMS, условие источника, ответ источника и сами
файлы. Решение и сверку делает человек (или отдельный расчёт) — автоматически «угадывать»
ответ здесь нельзя.

Запуск:
  python scripts/tsk362_fetch_attachments.py --pairs 3127:kompege:24613 3281:kompege:23203 --out-dir <каталог>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk362_fetch_answers import UA, fetch, strip_html  # noqa: E402


def kompege_task(task_id: str) -> dict:
    return json.loads(fetch(f"https://kompege.ru/api/v1/task/{task_id}"))


def download(url: str, dest: Path) -> int:
    if url.startswith("/"):
        url = "https://kompege.ru" + url
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    dest.write_bytes(data)
    return len(data)


def main(pairs: list[str], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for pair in pairs:
        lms_id, source, sid = pair.split(":")
        if source != "kompege":
            print(f"  [пропуск] {pair} — пока умею только kompege")
            continue
        d = kompege_task(sid)
        files = d.get("files") or []
        saved = []
        for f in files:
            name = re.sub(r"[^A-Za-z0-9_.\-]", "_", f.get("name") or "file")
            dest = out_dir / f"{lms_id}_{name}"
            try:
                size = download(f["url"], dest)
                saved.append({"name": f.get("name"), "path": str(dest), "size": size})
                print(f"  [файл ] id={lms_id} {f.get('name')} → {size} байт")
            except Exception as exc:  # noqa: BLE001 — источник внешний, любая ошибка не критична
                print(f"  [ошибка] id={lms_id} {f.get('name')}: {exc}")
            time.sleep(0.4)
        report.append({"lms_id": int(lms_id), "source": source, "source_id": sid,
                       "answer": d.get("key"), "number": d.get("number"),
                       "text": strip_html(d.get("text") or "")[:3000], "files": saved})
        if not files:
            print(f"  [нет файлов] id={lms_id} {source}:{sid} (ответ источника: {d.get('key')!r})")
        time.sleep(0.4)

    (out_dir / "attachments.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nСобрано заданий: {len(report)}; каталог: {out_dir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", nargs="+", required=True, help="lms_id:источник:id_в_источнике")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    main(a.pairs, Path(a.out_dir))
