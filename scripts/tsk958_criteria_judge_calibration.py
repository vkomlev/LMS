# scripts/tsk958_criteria_judge_calibration.py
"""
tsk-958: калибровка судьи по критериям на коротких ответах `SA`/`SA_COM`.

**Зачем.** Судья (`rubric_review_service`) до этой задачи работал только на
развёрнутых ответах `TA`. Распространяя его на 200+ заданий с подтверждёнными
критериями и без эталона, мы обязаны сперва измерить, как он судит именно
такие ответы: короткие программы, вывод, суждения. Главная метрика — ложные
«предлагаю зачёт»: преподаватель, увидев «зачёт» от машины, склонен его
подтвердить, и цена ошибки ложится на ученика (довод tsk-605/658).

**Две выборки.**

* **A — живые сдачи с вердиктом преподавателя** (`checked_by IS NOT NULL`) по
  заданиям с `grading_criteria.status=approved` и без эталона. Истина —
  вердикт человека. Вложения (скриншоты, файлы) локально недоступны — такие
  работы честно считаются «судить не по чему», как и на проде.
* **B — свой набор** (`--own <json>`): 10–15 заданий разных курсов, к каждому
  ответы четырёх видов — верный, с типичной ошибкой из `reject`, пересказ
  условия, «не знаю». Истина — ярлык автора набора. Нужен, потому что у живых
  сдач почти нет отказов преподавателя (2 из 61 на 15.09), и ложный зачёт на
  них не измерить.

**Прогон с докатом.** Провайдер отказывает полосами (память проекта): результат
пишется в файл после каждой работы, повторный запуск с тем же `--out`
пропускает уже разобранное и добирает остальное.

Скрипт **строго read-only** к боевой базе: только SELECT. Расход модели
пишется в ЛОКАЛЬНУЮ базу разработки (`DATABASE_URL` из `.env`) с назначением
`criteria_review` — так же, как будет на проде.

Запуск (из корня проекта):
    PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk958_criteria_judge_calibration.py \\
        --out C:/.../tsk958-calibration.json [--own scripts/tsk958_own_set.json]
    ... --dump-tasks 15        # только напечатать задания для составления своего набора
    ... --limit 10             # первые N работ выборки A (для пробы)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg
from dotenv import load_dotenv

load_dotenv(".env")

from app.services import rubric_review_service as rrs  # noqa: E402
from app.services.code_review_service import pick_code_for_review  # noqa: E402

logger = logging.getLogger("tsk958.calibration")

#: Аккаунты персонала — их сдачи не ученические.
STAFF_IDS = (2, 3, 142, 4495, 4496)

_TASKS_SQL = """
    SELECT t.id AS task_id, t.course_id, c.title AS course_title,
           t.task_content->>'type' AS task_type,
           t.task_content->>'stem' AS stem,
           t.solution_rules AS solution_rules
    FROM tasks t JOIN courses c ON c.id = t.course_id
    WHERE t.solution_rules->'grading_criteria'->>'status' = 'approved'
      AND t.task_content->>'type' IN ('SA', 'SA_COM')
      AND COALESCE(jsonb_array_length(t.solution_rules->'short_answer'->'accepted_answers'), 0) = 0
      AND NOT (COALESCE((t.solution_rules->'short_answer'->>'use_regex')::bool, false)
               AND t.solution_rules->'short_answer'->>'regex' IS NOT NULL)
      -- Файл-приложение обязателен -> доказательство модели недоступно, дверь
      -- `ai_check_policy` такое задание не пускает; приём ответа тоже.
      AND NOT COALESCE((t.solution_rules->>'requires_attachment')::bool, false)
    ORDER BY t.course_id, t.id
"""

_RESULTS_SQL = """
    SELECT tr.id AS result_id, tr.task_id, tr.user_id, tr.is_correct, tr.score, tr.max_score,
           tr.answer_json->'response'->>'value'   AS value,
           tr.answer_json->'response'->>'comment' AS comment,
           (tr.answer_json->'response'->'meta'->'attachments') IS NOT NULL AS has_attachments,
           tr.answer_json->'response'->'meta'->'attachments' AS attachments
    FROM task_results tr
    WHERE tr.task_id = ANY($1::int[])
      AND tr.checked_by IS NOT NULL
      AND tr.is_correct IS NOT NULL
      AND NOT (tr.user_id = ANY($2::int[]))
    ORDER BY tr.task_id, tr.id
"""


def _prod_dsn() -> str:
    """Боевой DSN — из `.mcp.json`, а не из `.env` (тот смотрит в базу разработки)."""
    cfg = json.loads(Path(".mcp.json").read_text(encoding="utf-8"))
    dsn = cfg["mcpServers"]["learn_prod_db"]["args"][-1]
    return dsn.split("?")[0]


def _jsonb(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def _fetch(limit: Optional[int]) -> tuple[Dict[int, Dict[str, Any]], List[Dict[str, Any]]]:
    conn = await asyncpg.connect(_prod_dsn())
    try:
        tasks = {
            r["task_id"]: {**dict(r), "solution_rules": _jsonb(r["solution_rules"])}
            for r in await conn.fetch(_TASKS_SQL)
        }
        rows = [dict(r) for r in await conn.fetch(_RESULTS_SQL, list(tasks), list(STAFF_IDS))]
    finally:
        await conn.close()
    if limit:
        rows = rows[:limit]
    return tasks, rows


def _body_for(
    value: Optional[str], comment: Optional[str], attachments: Any = None,
) -> tuple[Optional[str], Optional[str]]:
    """Тот же выбор текста, что в приёме ответа: программа — без порога, проза — с порогом.

    :returns: `(текст для судьи, причина пропуска)`.
    """
    attachments = _jsonb(attachments)
    code = pick_code_for_review(value, comment, None, attempt_id=None)
    if code:
        return rrs.pick_answer_for_criteria(
            value, comment, code, min_chars=0, attachments=attachments,
        ), None
    body = rrs.pick_answer_for_criteria(
        value, comment, min_chars=rrs.MIN_TEXT_CHARS, attachments=attachments,
    )
    return body, (None if body else "too_short")


async def _judge(body: str, task: Dict[str, Any], *, retries: int = 4) -> Dict[str, Any]:
    """Один разбор с докатом по временным отказам провайдера."""
    for attempt in range(retries + 1):
        out = await rrs.review_against_rubric(
            body, solution_rules=task["solution_rules"], task_stem=task["stem"],
            purpose=rrs.CRITERIA_PURPOSE, min_chars=0,
        )
        review = out.get("rubric_review") or {}
        if not review.get("error") or not review.get("retryable") or attempt == retries:
            return review
        pause = 5 * (attempt + 1) + random.uniform(0, 3)
        logger.info("временный отказ (%s) — повтор через %.0f c", review.get("error"), pause)
        await asyncio.sleep(pause)
    return review  # pragma: no cover


def _load(path: Path) -> Dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"live": {}, "own": {}}


def _save(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


async def _run_live(tasks, rows, store: Dict[str, Any], out: Path) -> None:
    for row in rows:
        key = str(row["result_id"])
        done = store["live"].get(key)
        if done and (done.get("verdict") or done.get("skipped")):
            continue
        task = tasks[row["task_id"]]
        body, skipped = _body_for(row["value"], row["comment"], row["attachments"])
        record: Dict[str, Any] = {
            "task_id": row["task_id"], "course_id": task["course_id"],
            "course": task["course_title"], "teacher_pass": bool(row["is_correct"]),
            "has_attachments": bool(row["has_attachments"]),
            "body_len": len(body) if body else 0,
        }
        if not body:
            record["skipped"] = skipped
        else:
            review = await _judge(body, task)
            if review.get("error"):
                record["error"] = review["error"]
            else:
                record["verdict"] = review["suggested_verdict"]
                record["items"] = [(i["id"], i["met"]) for i in review["items"]]
                record["summary"] = review.get("summary")
                record["model"] = review.get("model")
        store["live"][key] = record
        _save(out, store)
        logger.info("A %s: %s", key, record.get("verdict") or record.get("skipped") or record.get("error"))


async def _run_own(tasks, own: List[Dict[str, Any]], store: Dict[str, Any], out: Path) -> None:
    for index, entry in enumerate(own):
        key = f'{entry["task_id"]}:{entry["label"]}:{index}'
        done = store["own"].get(key)
        if done and done.get("verdict"):
            continue
        task = tasks.get(entry["task_id"])
        if task is None:
            logger.warning("своего набора задание %s не в выборке — пропуск", entry["task_id"])
            continue
        body, skipped = _body_for(entry.get("value"), entry.get("comment"))
        record = {
            "task_id": entry["task_id"], "course": task["course_title"], "label": entry["label"],
            "expected": entry["expected"], "body_len": len(body) if body else 0,
        }
        if not body:
            record["skipped"] = skipped
        else:
            review = await _judge(body, task)
            if review.get("error"):
                record["error"] = review["error"]
            else:
                record["verdict"] = review["suggested_verdict"]
                record["items"] = [(i["id"], i["met"]) for i in review["items"]]
                record["summary"] = review.get("summary")
                record["model"] = review.get("model")
        store["own"][key] = record
        _save(out, store)
        logger.info("B %s: %s", key, record.get("verdict") or record.get("skipped") or record.get("error"))


def _report(store: Dict[str, Any]) -> str:
    lines: List[str] = []
    live = list(store["live"].values())
    if live:
        lines.append(f"## A. Живые сдачи с вердиктом преподавателя: {len(live)}")
        skipped = [r for r in live if r.get("skipped")]
        errors = [r for r in live if r.get("error")]
        judged = [r for r in live if r.get("verdict")]
        lines.append(f"- судить не по чему (пусто/коротко/только вложение): {len(skipped)}")
        lines.append(f"- отказ модели после доката: {len(errors)}")
        lines.append(f"- разобрано: {len(judged)}")
        conf = Counter((r["verdict"], r["teacher_pass"]) for r in judged)
        lines.append("")
        lines.append("| вердикт машины | преподаватель: зачёт | преподаватель: незачёт |")
        lines.append("|---|---|---|")
        for verdict in ("pass", "fail", "unclear"):
            lines.append(f"| {verdict} | {conf[(verdict, True)]} | {conf[(verdict, False)]} |")
        n_pass = conf[("pass", True)] + conf[("pass", False)]
        n_fail_t = conf[("pass", False)] + conf[("fail", False)] + conf[("unclear", False)]
        agree = conf[("pass", True)] + conf[("fail", False)]
        decided = n_pass + conf[("fail", True)] + conf[("fail", False)]
        lines.append("")
        lines.append(f"- согласие с преподавателем среди решённых (pass/fail): {agree}/{decided}")
        lines.append(
            f"- ЛОЖНЫЙ «предлагаю зачёт» (машина pass, человек незачёт): "
            f"{conf[('pass', False)]} из {n_pass} pass; отказов преподавателя всего {n_fail_t}"
        )
        lines.append(f"- «нужен человек» (unclear): {conf[('unclear', True)] + conf[('unclear', False)]}/{len(judged)}")
        by_course = Counter(r["course"] for r in judged)
        lines.append("- по курсам: " + ", ".join(f"{c} — {n}" for c, n in by_course.most_common()))
    own = list(store["own"].values())
    if own:
        lines.append("")
        lines.append(f"## B. Свой набор: {len(own)}")
        judged = [r for r in own if r.get("verdict")]
        lines.append(f"- разобрано: {len(judged)}, пропущено: {len([r for r in own if r.get('skipped')])}, "
                     f"отказ: {len([r for r in own if r.get('error')])}")
        lines.append("")
        lines.append("| вид ответа | ожидали | pass | fail | unclear |")
        lines.append("|---|---|---|---|---|")
        for label in ("correct", "reject_error", "restated_stem", "dont_know"):
            group = [r for r in judged if r["label"] == label]
            if not group:
                continue
            c = Counter(r["verdict"] for r in group)
            lines.append(f"| {label} | {group[0]['expected']} | {c['pass']} | {c['fail']} | {c['unclear']} |")
        false_pass = [r for r in judged if r["expected"] == "fail" and r["verdict"] == "pass"]
        lines.append("")
        lines.append(f"- ЛОЖНЫЙ «предлагаю зачёт» на заведомо неверных: {len(false_pass)} "
                     f"из {len([r for r in judged if r['expected'] == 'fail'])}")
        for r in false_pass:
            lines.append(f"  - задание {r['task_id']} ({r['course']}), {r['label']}: {r.get('summary')}")
        missed = [r for r in judged if r["expected"] == "pass" and r["verdict"] == "fail"]
        lines.append(f"- ложный «незачёт» на верных: {len(missed)} из {len([r for r in judged if r['expected'] == 'pass'])}")
        for r in missed:
            lines.append(f"  - задание {r['task_id']} ({r['course']}): {r.get('summary')}")
    return "\n".join(lines)


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="файл результата (JSON), пишется после каждой работы")
    parser.add_argument("--own", help="свой набор ответов (JSON-список {task_id, label, expected, value, comment})")
    parser.add_argument("--limit", type=int, help="первые N живых работ")
    parser.add_argument("--dump-tasks", type=int, help="напечатать N заданий для составления своего набора и выйти")
    parser.add_argument("--skip-live", action="store_true", help="не гонять живые сдачи (только свой набор)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tasks, rows = await _fetch(args.limit)
    logger.info("заданий с критериями без эталона: %s; живых сдач с вердиктом: %s", len(tasks), len(rows))

    if args.dump_tasks:
        with_results = Counter(r["task_id"] for r in rows)
        picked = sorted(tasks.values(), key=lambda t: (-with_results[t["task_id"]], t["course_id"]))
        seen_courses: set = set()
        printed = 0
        for t in picked:
            if t["course_id"] in seen_courses and printed >= 3:
                continue
            seen_courses.add(t["course_id"])
            gc = t["solution_rules"]["grading_criteria"]
            print(f"\n=== task {t['task_id']} · курс {t['course_id']} «{t['course_title']}» · {t['task_type']} "
                  f"· сдач с вердиктом: {with_results[t['task_id']]}")
            print("СТЕМ:", (t["stem"] or "")[:700])
            print("MUST:", json.dumps(gc["must"], ensure_ascii=False))
            print("ACCEPT:", json.dumps(gc.get("accept"), ensure_ascii=False))
            print("REJECT:", json.dumps(gc.get("reject"), ensure_ascii=False))
            print("NOTES:", gc.get("notes"))
            printed += 1
            if printed >= args.dump_tasks:
                break
        return 0

    out = Path(args.out)
    store = _load(out)
    if not args.skip_live:
        await _run_live(tasks, rows, store, out)
    if args.own:
        own = json.loads(Path(args.own).read_text(encoding="utf-8"))
        await _run_own(tasks, own, store, out)
    print()
    print(_report(store))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
