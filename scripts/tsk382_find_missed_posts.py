"""tsk-382 часть А — поиск пропущенных ТГ-разборов по ТЕКСТУ, без ключа (READ-ONLY).

Прежний джойн (`tsk381_build_arbiter.py`) искал разбор по паре «вариант + номер»
из шапки поста. Если шапка оформлена иначе — «18_1 Сборник Крылова», «7_Крылов
С.С. Вариант 1», «Задание 15_v1 (…)» — ключ разбирается неверно или не
разбирается вовсе, и настоящий разбор проходит мимо.

Здесь ключ не используется совсем: текст каждого задания сверяется с текстом
ВСЕХ постов канала, упоминающих Крылова. Это тот же приём, которым в tsk-355
(раунд 2) вручную нашли ложные и пропущенные совпадения.

Скрипт ничего не пишет. Запуск:
    python scripts/tsk382_find_missed_posts.py
    python scripts/tsk382_find_missed_posts.py --min 0.45
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import re
from pathlib import Path
from typing import Any

import psycopg2

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk382.search")

_LMS_ROOT = Path(__file__).resolve().parent.parent
_CB_ROOT = Path("D:/Work/ContentBackbone")

TG_SOURCE_ID = "1701256430"

# Задания части А — партия Крылова без канона сильнее агентской оценки.
TARGET_IDS = [9481, 9484, 9498, 9512, 9516, 9521, 9523, 9525, 9528, 9530, 9532, 9555]

_LEVEL_RE = re.compile(
    r"[Уу]ровень\s*[:\-–—]?\s*(простой|лёгкий|легкий|средний|сложный)", re.IGNORECASE
)
# Шапка до слова «Уровень» включительно, либо до слов «Задание N … вариант M».
_HEAD_RE = re.compile(
    r"^.{0,200}?(?:[Уу]ровень\s*[:\-–—]?\s*(?:простой|лёгкий|легкий|средний|сложный)[.\s]*"
    r"|вариант\s*\d{1,2}[.\s]*)",
    re.IGNORECASE | re.DOTALL,
)
_NOISE_RE = re.compile(r"[^0-9a-zA-Zа-яёА-ЯЁ]+")


def _dsn(mcp_path: Path, server: str) -> str:
    """DSN сервера из .mcp.json. Секрет не логируется."""
    config = json.loads(mcp_path.read_text(encoding="utf-8"))
    for arg in config["mcpServers"][server]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://"):
            return arg.split("?")[0]
    raise RuntimeError(f"DSN для {server} не найден")


def normalize(text: str) -> str:
    """Текст условия без шапки и разметки — только содержательная часть."""
    body = _HEAD_RE.sub("", text or "", count=1)
    return " ".join(_NOISE_RE.sub(" ", body).lower().split())


def similarity(left: str, right: str) -> float:
    """Доля совпадения по первым 900 значимым символам."""
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left[:900], right[:900]).ratio()


def load_posts() -> list[dict[str, Any]]:
    """Посты канала про Крылова: текстовые версии, без служебных дампов."""
    with psycopg2.connect(_dsn(_CB_ROOT / ".mcp.json", "content_backbone_prod_db")) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT external_id, body, source_updated_at
            FROM content_hub.source_item
            WHERE source_id = %s AND body ILIKE %s AND body !~ '^\\['
            """,
            (TG_SOURCE_ID, "%Крылов%"),
        )
        rows = cur.fetchall()

    best: dict[str, tuple[str, Any]] = {}
    for external_id, body, updated in rows:
        if not body:
            continue
        if external_id not in best or len(body) > len(best[external_id][0]):
            best[external_id] = (body, updated)

    posts: list[dict[str, Any]] = []
    for external_id, (body, updated) in best.items():
        match = _LEVEL_RE.search(body)
        posts.append({
            "post_id": external_id,
            "level": match.group(1).lower().replace("ё", "е").replace("легкий", "простой") if match else None,
            "date": str(updated)[:10] if updated else None,
            "head": " ".join(body[:95].split()),
            "norm": normalize(body),
        })
    return posts


def load_targets() -> list[dict[str, Any]]:
    """Задания части А с нормализованным текстом условия."""
    with psycopg2.connect(_dsn(_LMS_ROOT / ".mcp.json", "learn_prod_db")) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, external_uid, difficulty_id,
                   regexp_replace(coalesce(task_content->>'stem', ''), '<[^>]+>', ' ', 'g')
            FROM tasks WHERE id = ANY(%s) ORDER BY id
            """,
            (TARGET_IDS,),
        )
        return [
            {"id": r[0], "uid": r[1], "difficulty_id": r[2], "norm": normalize(r[3])}
            for r in cur.fetchall()
        ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="tsk-382 поиск пропущенных разборов по тексту")
    parser.add_argument("--min", type=float, default=0.45, help="минимальная похожесть для показа")
    parser.add_argument("--out", default="out/tsk382_missed_posts.json")
    args = parser.parse_args(argv)

    posts = load_posts()
    targets = load_targets()
    logger.info("постов канала про Крылова: %d | заданий части А: %d", len(posts), len(targets))
    logger.info("")

    report: list[dict[str, Any]] = []
    for task in targets:
        scored = sorted(
            ((similarity(task["norm"], p["norm"]), p) for p in posts),
            key=lambda pair: pair[0], reverse=True,
        )
        top = [(score, p) for score, p in scored[:3] if score >= args.min]
        logger.info("id=%s %s (уровень сейчас %s)", task["id"], task["uid"], task["difficulty_id"])
        if not top:
            best_score = scored[0][0] if scored else 0.0
            logger.info("    разбора не найдено (лучшая похожесть %.2f)", best_score)
        for score, post in top:
            logger.info(
                "    %.2f  пост %s от %s, уровень «%s» | %s",
                score, post["post_id"], post["date"], post["level"] or "не указан", post["head"],
            )
        report.append({
            **{k: v for k, v in task.items() if k != "norm"},
            "candidates": [
                {"score": round(score, 3), "post_id": p["post_id"], "date": p["date"],
                 "level": p["level"], "head": p["head"]}
                for score, p in top
            ],
        })
        logger.info("")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("отчёт: %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
