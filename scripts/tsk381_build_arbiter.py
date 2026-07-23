"""tsk-381 — сбор арбитра сложности для партии Крылова (READ-ONLY).

Канон уровня сложности (решение оператора 2026-07-23):
  1. Разметка «Уровень …» в ТГ-разборах канала @cyberguru_ege (source_id
     1701256430) — главный канон, авторская субъективная оценка.
  2. Оценка внешних сайтов (kompege / Поляков / Яндекс) — вспомогательный.
  3. Ручные оценки оператора из прошлых задач — истина, не пересматриваются.

Правило «номер задания ЕГЭ → уровень» (docstring
`CB/monolith/external_tasks/normalizer/difficulty.py`) каноном НЕ является:
сложность зависит от самого задания, а не от его номера.

Скрипт ничего не пишет: собирает (вариант, номер) → уровень из постов,
джойнит с активными заданиями партии в LMS и печатает расхождения.

Запуск:
    python scripts/tsk381_build_arbiter.py
    python scripts/tsk381_build_arbiter.py --out out/tsk381_arbiter.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

import psycopg2

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk381")

_LMS_ROOT = Path(__file__).resolve().parent.parent
_CB_ROOT = Path("D:/Work/ContentBackbone")

TG_SOURCE_ID = "1701256430"

# difficulty_id в LMS (таблица difficulties).
LEVEL_TO_DIFFICULTY_ID: dict[str, int] = {
    "простой": 2,
    "лёгкий": 2,
    "легкий": 2,
    "средний": 3,
    "сложный": 4,
}
DIFFICULTY_ID_TO_LEVEL: dict[int, str] = {2: "простой", 3: "средний", 4: "сложный"}

_LEVEL_RE = re.compile(
    r"[Уу]ровень\s*[:\-–—]?\s*(простой|лёгкий|легкий|средний|сложный)",
    re.IGNORECASE,
)

# Заголовки ТГ-постов Крылова — форматы накопились за год, единого нет.
# Порядок важен: специфичные раньше общих.
_HEAD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # «Задание 15_v1 (Сборник Крылова С.С. 2026)»
    ("task_underscore_v", re.compile(r"[Зз]адание\s*(\d{1,2})[_\s]*v(\d{1,2})\b", re.IGNORECASE)),
    # «Задание 11 Сборник Крылова С.С. вариант 5», «Задание 24 Вариант Крылова С.С. 5»
    ("task_variant", re.compile(r"[Зз]адание\s*(\d{1,2}).{0,70}?[Вв]ариант\D{0,20}?(\d{1,2})", re.DOTALL)),
    # «18_1 Сборник Крылова С.С.» → задание 18, вариант 1
    ("num_underscore_num", re.compile(r"^\s*(\d{1,2})_(\d{1,2})\s+Сборник\s*Крылов", re.IGNORECASE)),
    # «17 Сборник Крылова С.С. вариант 1», «23 Крылов С.С. 2026  Вариант 1.», «7_Крылов С.С. Вариант 1.»
    ("num_variant", re.compile(r"^\s*(\d{1,2})[_\s].{0,70}?[Вв]ариант\D{0,20}?(\d{1,2})", re.DOTALL)),
)


def _dsn(mcp_path: Path, server: str) -> str:
    """DSN сервера из .mcp.json проекта. Секрет не логируется."""
    config = json.loads(mcp_path.read_text(encoding="utf-8"))
    args = config["mcpServers"][server]["args"]
    for arg in args:
        if isinstance(arg, str) and arg.startswith("postgresql://"):
            return arg.split("?")[0]
    raise RuntimeError(f"DSN для {server} не найден в {mcp_path}")


def parse_level(body: str) -> Optional[str]:
    """Уровень из разметки поста. None если автор его не проставил."""
    match = _LEVEL_RE.search(body)
    if match is None:
        return None
    return match.group(1).lower().replace("ё", "е").replace("легкий", "простой")


def parse_identity(body: str) -> tuple[Optional[tuple[int, int]], Optional[str]]:
    """(вариант, номер задания) из заголовка поста + имя сработавшего шаблона."""
    head = body[:200]
    for name, pattern in _HEAD_PATTERNS:
        match = pattern.search(head)
        if match is None:
            continue
        task_n, variant = int(match.group(1)), int(match.group(2))
        if not (1 <= task_n <= 27 and 1 <= variant <= 30):
            continue
        return (variant, task_n), name
    return None, None


_HEADER_TAIL_RE = re.compile(
    r".*?[Уу]ровень\s*[:\-–—]?\s*(?:простой|лёгкий|легкий|средний|сложный)[\.\s]*",
    re.IGNORECASE | re.DOTALL,
)
_NOISE_RE = re.compile(r"[^0-9a-zA-Zа-яёА-ЯЁ]+")


def normalize_body(text: str) -> str:
    """Текст условия без заголовка и разметки — для сверки задания с постом.

    Урок tsk-355 (раунд 2): ключ (вариант, номер) НЕ уникален — под одной меткой
    в книге и в канале встречаются разные задачи (расхождение изданий). Поэтому
    совпадение ключа обязано подтверждаться совпадением текста.
    """
    body = text
    match = _HEADER_TAIL_RE.match(body[:400])
    if match is not None:
        body = body[match.end():]
    body = _NOISE_RE.sub(" ", body).strip().lower()
    return " ".join(body.split())


def text_similarity(left: str, right: str) -> float:
    """Доля совпадения текстов условия (0..1) по первым 900 значимым символам."""
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left[:900], right[:900]).ratio()


def _is_entity_dump(body: str) -> bool:
    """Служебная копия поста (python-repr списка сущностей), не текст."""
    return body.lstrip().startswith(("[", "{"))


def load_posts(dsn: str) -> tuple[dict[tuple[int, int], list[dict[str, Any]]], list[dict[str, Any]]]:
    """Посты Крылова из канала: индекс (вариант, номер) → посты + неразобранные."""
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT external_id, body, created_at
            FROM content_hub.source_item
            WHERE source_id = %s AND body ILIKE %s
            ORDER BY external_id
            """,
            (TG_SOURCE_ID, "%Крылов%"),
        )
        rows = cur.fetchall()

    by_external_id: dict[str, str] = {}
    for external_id, body, _created in rows:
        if not body or _is_entity_dump(body):
            continue
        # при дублях берём самый длинный текст поста
        if len(body) > len(by_external_id.get(external_id, "")):
            by_external_id[external_id] = body

    index: dict[tuple[int, int], list[dict[str, Any]]] = {}
    unparsed: list[dict[str, Any]] = []
    for external_id, body in by_external_id.items():
        identity, pattern_name = parse_identity(body)
        level = parse_level(body)
        record = {
            "post_id": external_id,
            "level": level,
            "pattern": pattern_name,
            "head": " ".join(body[:110].split()),
            "norm_body": normalize_body(body),
        }
        if identity is None:
            unparsed.append(record)
            continue
        index.setdefault(identity, []).append(record)
    return index, unparsed


def load_lms_tasks(dsn: str) -> list[dict[str, Any]]:
    """Активные задания партии Крылова из прод-LMS."""
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            r"""
            SELECT t.id, t.external_uid, t.course_id, t.difficulty_id,
                   (regexp_match(t.external_uid, 'v(\d+)t(\d+)'))[1]::int AS variant,
                   (regexp_match(t.external_uid, 'v(\d+)t(\d+)'))[2]::int AS task_n,
                   lower(regexp_replace(coalesce(t.task_content->>'stem', ''), '<[^>]+>', ' ', 'g'))
            FROM tasks t
            WHERE t.external_uid LIKE '%crylov%' AND t.is_active
            ORDER BY t.id
            """
        )
        rows = cur.fetchall()

    tasks: list[dict[str, Any]] = []
    for task_id, uid, course_id, difficulty_id, variant, task_n, stem in rows:
        tasks.append(
            {
                "id": task_id,
                "external_uid": uid,
                "course_id": course_id,
                "difficulty_id": difficulty_id,
                "variant": variant,
                "task_n": task_n,
                "stem_level": parse_level(stem or ""),
                "norm_stem": normalize_body(stem or ""),
            }
        )
    return tasks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="tsk-381 арбитр сложности (read-only)")
    parser.add_argument("--out", default="out/tsk381_arbiter.json")
    parser.add_argument(
        "--threshold", type=float, default=0.65,
        help="минимальная доля совпадения текста поста и условия (0..1)",
    )
    args = parser.parse_args(argv)

    posts_index, unparsed = load_posts(_dsn(_CB_ROOT / ".mcp.json", "content_backbone_prod_db"))
    tasks = load_lms_tasks(_dsn(_LMS_ROOT / ".mcp.json", "learn_prod_db"))

    logger.info("ТГ-постов Крылова разобрано: %d ключей (вариант, номер)", len(posts_index))
    logger.info("ТГ-постов без разбора заголовка: %d", len(unparsed))
    for record in unparsed:
        logger.info("  post %s: %s", record["post_id"], record["head"])

    report: dict[str, Any] = {
        "task": "tsk-381",
        "canon": "ТГ-разметка @cyberguru_ege (главный канон оператора)",
        "rows": [],
        "unparsed_posts": unparsed,
    }
    stats = {
        "total": len(tasks),
        "tg_level_found": 0,
        "no_tg_level": 0,
        "match": 0,
        "mismatch": 0,
        "tg_conflict": 0,
        "stem_vs_tg_conflict": 0,
    }

    for task in tasks:
        key = (task["variant"], task["task_n"])
        candidates = posts_index.get(key, [])
        scored = [
            (text_similarity(task["norm_stem"], post["norm_body"]), post)
            for post in candidates
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        # Ключ (вариант, номер) подтверждается только совпадением текста (tsk-355 р.2).
        confirmed = [(score, post) for score, post in scored if score >= args.threshold]

        levels = sorted({post["level"] for _score, post in confirmed if post["level"]})
        tg_level = levels[0] if len(levels) == 1 else None
        if len(levels) > 1:
            stats["tg_conflict"] += 1

        expected_id = LEVEL_TO_DIFFICULTY_ID.get(tg_level) if tg_level else None
        row = {
            **{k: v for k, v in task.items() if k != "norm_stem"},
            "tg_level": tg_level,
            "tg_levels_all": levels,
            "tg_post_ids": [post["post_id"] for _score, post in confirmed],
            "tg_best_similarity": round(scored[0][0], 3) if scored else None,
            "tg_key_candidates": [post["post_id"] for _score, post in scored],
            "expected_difficulty_id": expected_id,
            "current_level": DIFFICULTY_ID_TO_LEVEL.get(task["difficulty_id"]),
        }
        if expected_id is None:
            stats["no_tg_level"] += 1
            row["verdict"] = "нет канона в ТГ"
        else:
            stats["tg_level_found"] += 1
            if expected_id == task["difficulty_id"]:
                stats["match"] += 1
                row["verdict"] = "совпадает"
            else:
                stats["mismatch"] += 1
                row["verdict"] = "РАСХОЖДЕНИЕ"
            if task["stem_level"] and task["stem_level"] != tg_level:
                stats["stem_vs_tg_conflict"] += 1
                row["verdict"] += " (текст условия ≠ пост)"
        report["rows"].append(row)

    report["stats"] = stats
    logger.info("")
    logger.info("=== Сводка по %d активным заданиям ===", stats["total"])
    for key, value in stats.items():
        logger.info("  %-24s %s", key, value)

    logger.info("")
    logger.info("=== Расхождения (поле ≠ канон ТГ) ===")
    for row in report["rows"]:
        if row["verdict"].startswith("РАСХОЖДЕНИЕ"):
            logger.info(
                "  id=%-5s %-18s курс=%-5s поле=%s(%s) -> канон=%s(%s) посты=%s",
                row["id"], row["external_uid"], row["course_id"],
                row["difficulty_id"], row["current_level"],
                row["expected_difficulty_id"], row["tg_level"],
                ",".join(row["tg_post_ids"]),
            )

    logger.info("")
    logger.info("=== Без канона в ТГ (нужен второй источник) ===")
    for row in report["rows"]:
        if row["verdict"] == "нет канона в ТГ":
            logger.info(
                "  id=%-5s %-40s поле=%s(%s) текст_условия=%s",
                row["id"], row["external_uid"], row["difficulty_id"],
                row["current_level"], row["stem_level"],
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("")
    logger.info("отчёт: %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
