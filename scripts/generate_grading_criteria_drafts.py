"""Пакетная генерация черновиков критериев оценивания (tsk-590).

Скрипт проходит очередь заданий без критериев и просит сервер составить для
каждого заготовку. Работает ЧЕРЕЗ API, а не через базу: запись критериев
обязана идти тем же путём, что правка методистом, иначе валидация схемы и
предикат допуска обойдены (прецедент tsk-396 — правило, записанное мимо API,
перестаёт разбираться).

Черновики записываются со `status="draft"`: к оценке ответов ученика они не
допускаются, пока их не подтвердит человек в кабинете. Подтверждать скриптом
нельзя — сервер отвечает 403 сервисному ключу.

Примеры:
    # Посмотреть, сколько заданий ждёт заготовок, ничего не тратя:
    python scripts/generate_grading_criteria_drafts.py --dry-run

    # Составить черновики для одного курса:
    python scripts/generate_grading_criteria_drafts.py --course-id 1181 --apply

    # Первые 20 заданий всей очереди:
    python scripts/generate_grading_criteria_drafts.py --limit 20 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any, Optional

import httpx

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("grading-criteria-drafts")

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.environ.get("LMS_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key", default=os.environ.get("LMS_API_KEY"), help="сервисный ключ")
    parser.add_argument("--course-id", type=int, default=None, help="ограничить одним курсом")
    parser.add_argument("--limit", type=int, default=50, help="сколько заданий обработать")
    parser.add_argument("--model", default=None, help="явная модель вместо цепочки по умолчанию")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="действительно составлять черновики (без флага — только показать очередь)",
    )
    parser.add_argument("--dry-run", action="store_true", help="то же, что без --apply")
    return parser.parse_args()


async def _queue(http: httpx.AsyncClient, *, course_id: Optional[int], limit: int) -> list[dict[str, Any]]:
    """Задания, у которых критериев нет вовсе (`state=none`)."""
    params: dict[str, Any] = {"state": "none", "limit": limit}
    if course_id is not None:
        params["course_id"] = course_id
    resp = await http.get("/api/v1/tasks/grading-criteria/queue", params=params)
    resp.raise_for_status()
    body = resp.json()
    logger.info(
        "в очереди: без критериев %s, с черновиками %s",
        body.get("empty_total"),
        body.get("drafts_total"),
    )
    return body.get("items", [])


async def main() -> int:
    args = _parse_args()
    if not args.api_key:
        logger.error("нужен сервисный ключ: --api-key либо переменная LMS_API_KEY")
        return 2

    headers = {"X-API-Key": args.api_key}
    timeout = httpx.Timeout(connect=10.0, read=180.0, write=180.0, pool=10.0)
    async with httpx.AsyncClient(base_url=args.base_url, headers=headers, timeout=timeout) as http:
        items = await _queue(http, course_id=args.course_id, limit=args.limit)
        if not items:
            logger.info("заданий без критериев не осталось")
            return 0

        if not args.apply or args.dry_run:
            logger.info("пробный прогон, черновики не составляются. Первые задания очереди:")
            for item in items[:10]:
                logger.info(
                    "  %s · курс %s · %s",
                    item["task_id"],
                    item["course_id"],
                    (item.get("title") or item["stem"])[:70],
                )
            logger.info("всего к обработке: %s. Повтори с --apply", len(items))
            return 0

        done = 0
        failed = 0
        tokens_in = tokens_out = 0
        for item in items:
            task_id = item["task_id"]
            params = {"model": args.model} if args.model else None
            try:
                resp = await http.post(
                    f"/api/v1/tasks/{task_id}/grading-criteria/draft", params=params
                )
            except httpx.HTTPError as exc:
                failed += 1
                logger.warning("  %s — сбой связи: %s", task_id, exc)
                continue
            if resp.status_code != 200:
                failed += 1
                logger.warning("  %s — отказ %s: %s", task_id, resp.status_code, resp.text[:200])
                continue
            body = resp.json()
            done += 1
            tokens_in += body.get("tokens_in", 0)
            tokens_out += body.get("tokens_out", 0)
            warning = (body.get("criteria") or {}).get("draft_warning")
            logger.info(
                "  %s — черновик готов, требований %s%s",
                task_id,
                len((body.get("criteria") or {}).get("must", [])),
                f" · оговорка: {warning[:60]}…" if warning else "",
            )

        logger.info(
            "\nготово: черновиков %s, отказов %s, токенов вход %s, выход %s",
            done,
            failed,
            tokens_in,
            tokens_out,
        )
        logger.info(
            "Черновики НЕ участвуют в оценке: их читает и подтверждает методист "
            "в кабинете, раздел «Критерии оценивания»."
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
