"""tsk-261 (класс «слив ответа через ссылку-источник»): убрать URL, оставить кредит.

ДЕФЕКТ (приёмка QA 2026-07-16, находка B10): «Там где указан источник — указан правильный
ответ». Проверено: 429 активных заданий несут в стеме строку «Источник: …» со ссылкой на
оригинал. Открытие ссылки показывает готовый ответ — на `inf-oge.sdamgia.ru/problem?id=18031`
прямо написано «Ответ: скользя» и полный разбор, без единого клика.

ПОЧЕМУ НЕ УДАЛЯЕМ СТРОКУ ЦЕЛИКОМ (решение оператора 2026-07-17):
ADR-0028 разрешает публиковать без атрибуции только ПЕРЕПИСАННУЮ задачу («stem в LMS =
переписанная версия, НЕ raw original»; `license_note: derivative work — methodist rewrite
required`). Сверка с оригиналом показала: наши стемы — почти дословные копии (задача 6347 против
sdamgia 10314 совпадает вплоть до списка стран и чисел). Значит переписывание не выполнялось, и
удаление атрибуции превратило бы копии в некредитованное заимствование — ровно тот риск, ради
которого ADR-0028 и писался.

Поэтому: кредит («Источник: РешуОГЭ, задача 10314») ОСТАЁТСЯ, убирается только ссылка.
Ответ перестаёт быть в одном клике. Оговорка: РешуОГЭ — публичный банк, по номеру задачу найдут
поиском; это трение, а не барьер. Полное закрытие — переписывание 429 задач (отдельная работа).

ДВА ФОРМАТА (оба на проде):
  oge (410):    Источник: РешуОГЭ, задача 10314 (inf-oge.sdamgia.ru/problem?id=10314)
                → Источник: РешуОГЭ, задача 10314
                ID бывает с суффиксом: 10580_1, 10566_2 — учтено (`[^)]*`).
  wp_nav (19):  Источник: <a href="/test?id=1711632" target="_blank">Демоверсия ЕГЭ—2017</a>
                → Источник: Демоверсия ЕГЭ—2017
                ВАЖНО: у всех 19 в стеме БОЛЬШЕ одной ссылки, поэтому разворачиваем только ту,
                что идёт сразу после «Источник:» (якорь в регулярке). Ссылки в теле не трогаем.
                Побочно чинится и класс B7: эти href относительные (`/test?id=…`) и в SPW вели
                в 404.

Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import os
import re
import sys

import asyncpg
from dotenv import load_dotenv

# ` (inf-oge.sdamgia.ru/problem?id=10314)` / `(inf-ege…)` / со схемой — убираем скобку с URL.
URL_TAIL_RE = re.compile(r"\s*\((?:https?://)?[a-z-]+\.sdamgia\.ru/problem\?id=[^)]*\)")
# Разворачиваем ТОЛЬКО ссылку сразу после «Источник:» (в стеме есть и другие — их не трогаем).
SRC_LINK_RE = re.compile(r"(Источник:\s*)<a\b[^>]*>(.*?)</a>", re.S)


def clean_stem(stem: str) -> str:
    out = URL_TAIL_RE.sub("", stem)
    out = SRC_LINK_RE.sub(r"\1\2", out)
    return out


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        raise RuntimeError("DATABASE_URL не прод (5.42.107.253) — передай прод-DSN из .mcp.json")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(
                """SELECT id, external_uid, task_content->>'stem' AS stem
                     FROM tasks
                    WHERE is_active AND task_content->>'stem' LIKE '%Источник:%'
                    ORDER BY id"""
            )
            print(f"Заданий со строкой «Источник:»: {len(rows)}")
            if not rows:
                raise RuntimeError("кандидатов нет — возможно, уже применено")

            changed = 0
            lost_credit = []
            for r in rows:
                new = clean_stem(r["stem"])
                if new == r["stem"]:
                    continue
                # Инвариант: кредит обязан выжить — удаляем ссылку, не атрибуцию.
                if "Источник:" not in new:
                    lost_credit.append(r["id"])
                    continue
                await conn.execute(
                    "UPDATE tasks SET task_content = jsonb_set(task_content,'{stem}', to_jsonb($2::text)) WHERE id=$1",
                    r["id"], new,
                )
                changed += 1

            assert not lost_credit, f"правка потеряла бы кредит у задач: {lost_credit[:5]}"
            print(f"Изменено заданий: {changed}")

            # Верификация в транзакции.
            url_left = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE is_active AND task_content->>'stem' ~ 'sdamgia\\.ru/problem'"
            )
            link_left = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE is_active AND task_content->>'stem' ~ 'Источник:\\s*<a'"
            )
            credit = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE is_active AND task_content->>'stem' LIKE '%Источник:%'"
            )
            assert url_left == 0, f"остались ссылки на оригинал: {url_left}"
            assert link_left == 0, f"остались <a> сразу после «Источник:»: {link_left}"
            assert credit == len(rows), f"кредит потерян: было {len(rows)}, стало {credit}"
            print(f"Проверка: ссылок на оригинал {url_left}, <a> после «Источник:» {link_left}, "
                  f"кредит сохранён у {credit} из {len(rows)}")

            sample = await conn.fetchrow(
                "SELECT task_content->>'stem' s FROM tasks WHERE id=$1", rows[0]["id"]
            )
            tail = sample["s"][sample["s"].find("Источник:"):][:90]
            print(f"Пример {rows[0]['id']}: {tail!r}")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    try:
        asyncio.run(main(apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
