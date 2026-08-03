# -*- coding: utf-8 -*-
"""tsk-384, шаг 2: положить 5 сгенерированных PNG в CAS + прод-S3, проверить доступность.

Тот же durable-паттерн, что tsk-369/526: сначала файл кладётся в хранилище и
проверяется живым HTTP-запросом к боевому эндпоинту, и лишь потом (отдельным
скриптом) правится БД — обратный порядок оставил бы ссылку в никуда.

Использует штатный помощник ContentBackbone `store_bytes_to_cas` — идемпотентен
по содержимому (имя объекта = sha256), креды/CAS_MEDIA_ROOT из .env CB (не
печатаются).

Запуск: python scripts/tsk384_store_cas.py [--apply]
Без --apply — только считает sha256 и печатает план (ничего не грузит).
Пишет reviews/tsk384-chess-visuals/stored.json.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

CB_ROOT = Path(r"D:\Work\ContentBackbone")
OUT_DIR = Path(__file__).resolve().parents[1] / "reviews" / "tsk384-chess-visuals"
MEDIA_URL = "https://api.learn.victor-komlev.ru/api/v1/media/{}"
UA = "tsk384-store/1.0"

FIGURES = ["rook", "bishop", "king", "queen", "knight"]


def load_cb_env() -> Path:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=CB_ROOT / ".env", encoding="utf-8-sig")
    sys.path.insert(0, str(CB_ROOT))
    return Path(os.environ.get("CAS_MEDIA_ROOT", str(CB_ROOT / "data" / "media_store")))


def check_public(sha_ext: str) -> tuple[bool, str]:
    req = urllib.request.Request(MEDIA_URL.format(sha_ext), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            ctype = resp.headers.get("Content-Type") or "?"
            size = resp.headers.get("Content-Length") or "?"
            return resp.status == 200, f"HTTP {resp.status}, Content-Type={ctype}, Content-Length={size}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return False, f"сеть: {exc}"


async def main(apply: bool) -> None:
    cas_root = load_cb_env()
    from monolith.external_tasks.media.cas_downloader import store_bytes_to_cas  # noqa: E402

    plan = []
    for key in FIGURES:
        data = (OUT_DIR / f"{key}.png").read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        sha_ext = f"{sha}.png"
        plan.append({"key": key, "sha_ext": sha_ext, "size": len(data)})
        print(f"  {key}: {sha_ext} ({len(data)} B)")

    if not apply:
        print("\nDRY-RUN: ничего не загружено (запусти с --apply).")
        return

    stored, failed = [], []
    for item in plan:
        key, sha_ext = item["key"], item["sha_ext"]
        ok, note = check_public(sha_ext)
        if ok:
            stored.append({**item, "http": note, "skipped": "уже в хранилище"})
            print(f"  [ok ] {key} {sha_ext[:12]} — уже в хранилище ({note})")
            continue
        data = (OUT_DIR / f"{key}.png").read_bytes()
        got = await store_bytes_to_cas(data, "png", cas_root)
        if got != sha_ext:
            failed.append({**item, "error": f"CAS вернул {got!r}"})
            print(f"  [ОШИБКА] {key} {sha_ext[:12]} → CAS вернул {got!r}")
            continue
        ok, note = check_public(sha_ext)
        (stored if ok else failed).append({**item, "http": note})
        print(f"  [{'ok ' if ok else 'НЕТ'}] {key} {sha_ext[:12]} — {note}")

    (OUT_DIR / "stored.json").write_text(
        json.dumps({"stored": stored, "failed": failed}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"\nЗагружено и доступно: {len(stored)}/5; проблемных: {len(failed)}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(a.apply))
