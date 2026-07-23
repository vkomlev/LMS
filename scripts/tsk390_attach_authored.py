# -*- coding: utf-8 -*-
"""tsk-390: привязать файлы к авторским заданиям курса, где источник указал оператор.

ЗАЧЕМ ОТДЕЛЬНЫЙ ПУТЬ
Задания `lms:tsk109:c138:*` и `lms:c145:vvod:*` написаны автором курса, а не импортированы.
Их текст — короткие практикумы («Откройте лист Товар. Чему равно…»), он НЕ совпадает с
текстом задачи-первоисточника, поэтому автоматический гейт tsk369_build_plan (дословный
фрагмент + числа) их закономерно отклоняет. Источник здесь называет оператор, и основание
записывается в аудит-файл — это подтверждённая привязка, а не молчаливый обход гейта.

ЧТО ПРИВЯЗЫВАЕТСЯ (подтверждено оператором 2026-07-24)
  * 4780-4789 (ЕГЭ №3, ВПР + сводные таблицы) → `03.ods` c sdamgia problem?id=75240.
    Сверка: лист «Товар» содержит «Артикул» + «Количество в упаковке» (ключ ВПР из условия),
    лист «Магазин» — ID «М01»…«М20», включая «М10» (задание 4785). Расхождение в тексте
    заданий: лист назван «Движение_товаров», в файле — «Торговля» (передано оператору).
  * 5089-5095 (ЕГЭ №17) → `17_1970.zip` с victor-komlev.ru (файл автора курса).
    Сверка: внутри `17_1970.txt` ровно 5000 целых чисел, диапазон −1000…1000 — дословно
    как обещает условие.

Файлы кладутся в CAS тем же помощником, что и весь tsk-369, и проверяются боевым
эндпоинтом. На выходе plan/stored для штатного `tsk369_apply.py` — запись в БД делает он
(транзакция, бэкап, построчная проверка).

Запуск:
  python scripts/tsk390_attach_authored.py --out-dir reviews/tsk390-dedup [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk369_build_plan import build_block  # noqa: E402
from tsk369_store_cas import check_public, load_cb_env  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0"

# (url, расширение, отображаемое имя, id заданий, основание привязки)
GROUPS = [
    {
        "url": "https://inf-ege.sdamgia.ru/get_file?id=228463",
        "ext": "ods",
        "name": "03.ods",
        "ids": list(range(4780, 4790)),
        "reason": ("оператор указал источник: sdamgia problem?id=75240, вложение 03.ods; "
                   "сверено — лист «Товар» несёт «Артикул» и «Количество в упаковке» (ключ ВПР "
                   "из условия), лист «Магазин» содержит «М10»"),
    },
    {
        "url": "https://victor-komlev.ru/wp-content/uploads/2025/09/17_1970.zip",
        "ext": "zip",
        "name": "17_1970.zip",
        "ids": list(range(5089, 5096)),
        "reason": ("оператор прислал ссылку на авторский файл; сверено — внутри 17_1970.txt "
                   "ровно 5000 целых чисел в диапазоне −1000…1000, как обещает условие"),
    },
]


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


async def main(out_dir: Path, apply: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cas_root = load_cb_env()
    from monolith.external_tasks.media.cas_downloader import store_bytes_to_cas  # noqa: E402

    plan, stored, confirmed = [], [], {}
    for g in GROUPS:
        data = fetch(g["url"])
        sha = hashlib.sha256(data).hexdigest()
        sha_ext = f"{sha}.{g['ext']}"
        print(f"{g['name']}: {len(data)} байт, sha256={sha[:16]}…")

        if apply:
            ok, note = check_public(sha_ext)
            if not ok:
                got = await store_bytes_to_cas(data, g["ext"], cas_root)
                if got != sha_ext:
                    raise RuntimeError(f"CAS вернул {got!r} вместо {sha_ext!r}")
                ok, note = check_public(sha_ext)
            if not ok:
                raise RuntimeError(f"файл не отдаётся боевым эндпоинтом: {note}")
            print(f"  в хранилище и доступен: {note}")
            stored.append({"sha_ext": sha_ext, "size": len(data), "http": note})

        f = {"sha_ext": sha_ext, "ext": g["ext"], "size": len(data),
             "name": g["name"], "path": None, "url": g["url"], "reuse": False}
        for tid in g["ids"]:
            plan.append({"id": tid, "course_id": None, "files": [f],
                         "block": build_block([f]), "verdict": "operator_confirmed",
                         "source": "operator", "source_id": g["url"],
                         "evidence": {"operator_confirmed": g["reason"]}})
            confirmed[str(tid)] = {"reason": g["reason"]}

    (out_dir / "plan_authored.json").write_text(
        json.dumps({"plan": plan, "manual": []}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "stored_authored.json").write_text(
        json.dumps({"stored": stored, "failed": []}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "operator_confirmed.json").write_text(
        json.dumps(confirmed, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nЗаданий в плане: {len(plan)}; файлов уникальных: {len(GROUPS)}")
    print(f"Сохранено в {out_dir}: plan_authored.json, stored_authored.json, operator_confirmed.json")
    if not apply:
        print("DRY-RUN: в CAS ничего не положено (запусти с --apply).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(Path(a.out_dir), a.apply))
