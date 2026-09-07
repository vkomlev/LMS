# -*- coding: utf-8 -*-
"""tsk-816: завести практические задания курса Python, которых не было в LMS,
и сразу прикрепить к ним авторский видеоразбор канала @CyberGuruPython.

ОТКУДА ЗАДАНИЯ
Наряд `reviews/tsk814-missing-tasks.md` (из tsk-814): 51 разбор конкретных
практических заданий висел без применения — соответствующего задания в базе
нет. Условие берётся из текста поста канала (ContentBackbone, source_id
1821049174, выгрузка tg_parser), план — `reviews/tsk816-new-tasks.json`.

ЧЕМ ПЛАН ОТЛИЧАЕТСЯ ОТ НАРЯДА (сверка 07.09, решение оператора)
Наряд искал соответствие метрикой сходства текста, и она системно ошибается:
условия в LMS переписаны под ОДИН проверяемый ответ (добавлен фиксированный
ввод), из-за чего похожесть падает. Механическая сверка со всеми 379 заданиями
курсов Python дала другую картину: девять записей раздела «завести новое» уже
есть в базе слово в слово (списки 21 → #272, списки 30 → #281, циклы 14/15/17
→ #225/#226/#228, словари 26 → #374, функции 15/18/19/30 → #563/#564/#85/#94),
а три записи раздела «объединить» оказались ДРУГОЙ задачей и заводятся здесь
заново. Такие записи ушли в план прикрепления `tsk816-attach-hints.json`.

ОТВЕТ НЕ ПИШЕТСЯ РУКОЙ
Эталон каждого задания получен прогоном эталонного решения (оно лежит в плане
полем `reference_code`) и перепроверен второй, иначе написанной реализацией.
Видео показывает ход решения, но конкретные числа задаёт условие — так устроены
все соседние задания этих курсов.

ПОРЯДОК
`order_position` не задаётся: триггер `trg_set_task_order_position` ставит
задание в конец своего курса. Позиции существующих заданий не сдвигаются.

Запуск: dry-run по умолчанию (всегда ROLLBACK);
  python scripts/tsk816_create_tasks.py
  DBCHECK_OK=1 python scripts/tsk816_create_tasks.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

DEFAULT_PLAN = project_root / "reviews" / "tsk816-new-tasks.json"

INSERT = """
INSERT INTO tasks (course_id, external_uid, max_score, task_content, difficulty_id,
                   solution_rules, order_position, is_active, requirement_level,
                   content_provenance)
VALUES ($1, $2, 1, $3::jsonb, $4, $5::jsonb, NULL, true, 'required', $6::jsonb)
RETURNING id, order_position
"""


def _dsn() -> str:
    """Прод-DSN базы learn: из окружения, иначе из .mcp.json проекта."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


def _task_content(item: dict[str, Any]) -> dict[str, Any]:
    """task_content в том же виде, что у соседних заданий этих курсов."""
    return {
        "code": None,
        "stem": item["stem"],
        "tags": None,
        "type": "SA_COM",
        "media": None,
        "title": item["title"],
        "prompt": None,
        "options": None,
        "has_hints": bool(item["videos"]),
        "course_uid": None,
        "hints_text": [],
        "hints_video": list(item["videos"]),
        "difficulty_code": None,
    }


def _solution_rules(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "max_score": 1,
        "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
        "auto_check": True,
        "text_answer": None,
        "scoring_mode": "all_or_nothing",
        "short_answer": {
            "regex": None,
            "use_regex": False,
            "normalization": item["normalization"],
            "accepted_answers": [{"score": 1, "value": item["answer"]}],
        },
        "partial_rules": [],
        "correct_options": [],
        "custom_scoring_config": None,
        "manual_review_required": False,
    }


def _provenance(item: dict[str, Any]) -> dict[str, Any]:
    posts = ", ".join(item["posts"])
    return {
        "source": "manual_script",
        "edited_by": "tsk-816",
        "fields": ["task_content", "solution_rules"],
        "reason": (f"задание заведено по разбору канала @CyberGuruPython "
                   f"(пост(ы) {posts}, тема «{item['source_topic']}», "
                   f"задание {item['source_num']} у автора); эталон получен прогоном "
                   f"эталонного решения и сверен второй реализацией"),
    }


def _validate(items: list[dict[str, Any]]) -> None:
    """Схема solution_rules — та же, что проверяет приложение."""
    from app.schemas.solution_rules import SolutionRules

    for item in items:
        SolutionRules.model_validate(_solution_rules(item))
        if not item["answer"].strip():
            raise AssertionError(f"{item['external_uid']}: пустой эталонный ответ")
        if not item["videos"]:
            raise AssertionError(f"{item['external_uid']}: нет ни одной ссылки на разбор")


async def main(apply: bool, plan_path: Path) -> None:
    items: list[dict[str, Any]] = json.loads(plan_path.read_text(encoding="utf-8"))["items"]
    _validate(items)

    print("=" * 78)
    print(f"tsk-816 · заведение {len(items)} заданий · {'ПРИМЕНЕНИЕ' if apply else 'DRY-RUN'}")
    print("=" * 78)

    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            course_ids = sorted({i["course_id"] for i in items})
            before = {r["id"]: r for r in await conn.fetch(
                "SELECT c.id, c.title, "
                "(SELECT count(*) FROM tasks t WHERE t.course_id=c.id) n, "
                "(SELECT COALESCE(max(t.order_position),0) FROM tasks t WHERE t.course_id=c.id) mx "
                "FROM courses c WHERE c.id = ANY($1::int[])", course_ids)}
            missing_courses = [c for c in course_ids if c not in before]
            if missing_courses:
                raise AssertionError(f"нет таких курсов: {missing_courses}")

            uids = [i["external_uid"] for i in items]
            taken = await conn.fetch(
                "SELECT id, external_uid FROM tasks WHERE external_uid = ANY($1::text[])", uids)
            if taken:
                raise AssertionError(
                    "external_uid уже заняты (скрипт уже отрабатывал?): "
                    + ", ".join(f"#{r['id']} {r['external_uid']}" for r in taken))

            print("\nКурсы-приёмники (до вставки):")
            for cid in course_ids:
                row = before[cid]
                add = sum(1 for i in items if i["course_id"] == cid)
                print(f"  курс {cid:>4} «{row['title'][:44]}»: заданий {row['n']}, "
                      f"последняя позиция {row['mx']} -> добавим {add}")

            print("\nЧто будет заведено:")
            for i, item in enumerate(items, 1):
                answer = item["answer"].rstrip("\n")
                short = answer if len(answer) <= 60 else answer[:57] + "..."
                print(f"  [{i:>2}] курс {item['course_id']:>4} d={item['difficulty_id']} "
                      f"{item['external_uid']}")
                print(f"       {item['title']}")
                print(f"       ответ: {short!r}")
                print(f"       разбор(ы): {', '.join(item['videos'])}")

            created: list[tuple[int, int, dict]] = []
            for item in items:
                row = await conn.fetchrow(
                    INSERT,
                    item["course_id"],
                    item["external_uid"],
                    json.dumps(_task_content(item), ensure_ascii=False),
                    item["difficulty_id"],
                    json.dumps(_solution_rules(item), ensure_ascii=False),
                    json.dumps(_provenance(item), ensure_ascii=False),
                )
                created.append((row["id"], row["order_position"], item))

            print("\nВерификация в транзакции:")
            ids = [c[0] for c in created]
            rows = {r["id"]: r for r in await conn.fetch(
                "SELECT id, course_id, external_uid, order_position, is_active, difficulty_id, "
                "task_content->>'type' t_type, task_content->>'stem' stem, "
                "task_content->>'title' title, "
                "COALESCE(task_content->'hints_video','[]'::jsonb)::text hv, "
                "solution_rules->'short_answer'->'accepted_answers'->0->>'value' answer "
                "FROM tasks WHERE id = ANY($1::int[])", ids)}
            for tid, pos, item in created:
                row = rows[tid]
                assert row["stem"] == item["stem"], f"#{tid}: условие записалось иначе"
                assert row["answer"] == item["answer"], f"#{tid}: эталон записался иначе"
                assert row["title"] == item["title"], f"#{tid}: заголовок записался иначе"
                assert json.loads(row["hv"]) == item["videos"], f"#{tid}: ссылки записались иначе"
                assert row["t_type"] == "SA_COM", f"#{tid}: тип {row['t_type']}"
                assert row["is_active"], f"#{tid}: неактивно"
                print(f"  #{tid} курс {row['course_id']} позиция {pos} :: {row['title']}")

            for cid in course_ids:
                added = sum(1 for _, _, i in created if i["course_id"] == cid)
                n_now = await conn.fetchval(
                    "SELECT count(*) FROM tasks WHERE course_id=$1", cid)
                dupes = await conn.fetchval(
                    "SELECT COALESCE(sum(c),0) FROM (SELECT count(*)-1 c FROM tasks "
                    "WHERE course_id=$1 GROUP BY order_position HAVING count(*)>1) x", cid)
                assert n_now == before[cid]["n"] + added, f"курс {cid}: счёт заданий не сошёлся"
                assert dupes == 0, f"курс {cid}: коллизии order_position"
                print(f"  курс {cid}: заданий {before[cid]['n']} -> {n_now}, "
                      f"коллизий позиций {dupes}")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (повтор с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan", default=str(DEFAULT_PLAN))
    args = ap.parse_args()
    path = Path(args.plan)
    if not path.is_absolute():
        path = project_root / path
    try:
        asyncio.run(main(args.apply, path))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
