# -*- coding: utf-8 -*-
"""tsk-371: восстановить ответы трёх заданий «выигрышная стратегия», где стояло «Решение».

ЧТО БЫЛО
У 2383, 2384 и 2385 в поле ответа лежало слово «Ре­ше­ние» (с мягкими переносами) — импорт
вместо ответа подхватил подпись кнопки «Решение» со страницы «Решу ЕГЭ». Задания при этом
активны и проверяются автоматически, то есть ЛЮБОЙ ответ ученика считался неверным.

ПОЧЕМУ ОТВЕТ НЕЛЬЗЯ БЫЛО ПРОСТО ВЗЯТЬ У ИСТОЧНИКА
`problem?id=N` отдаёт связку из трёх задач (19, 20, 21) с общим описанием игры, а ID в LMS
указывает на ПЕРВУЮ задачу связки. Разбор, бравший первый блок «Ответ», выдавал ответ
соседней задачи: для 2385 — «12» вместо «1011». Блок теперь выбирается по хвосту условия
(`sdamgia_block`), а сами ответы независимо перепроверены расчётом.

РАСЧЁТ (полный перебор позиций, обе игры)
  * 2383 — две кучи (10, S), ход: убрать 1 камень или уменьшить кучу вдвое; финиш при сумме
    ≤ 20. Пять значений S, где Петя не выигрывает первым ходом, но выигрывает вторым при
    любой игре Вани: 23, 24, 32, 44, 45. Условие требует записать «без разделительных
    знаков» → «2324324445». Совпало с источником.
  * 2384 — правила связки 58527: добавить 1–3 камня в БОЛЬШУЮ кучу либо удвоить МЕНЬШУЮ
    (при равных кучах удвоение запрещено), финиш при 40 камнях в куче; первая куча 11.
    Подходящие S: 22, 33, 34, 35 → минимальное 22, максимальное 35. Совпало с источником.
  * 2385 — одна куча, ходы +1, +2, ×2, нельзя повторять свой предыдущий ход, финиш при 29.
    Два значения S, где Ваня выигрывает вторым ходом, но не первым: 10 и 11. Совпало с
    источником («1011»).

ФОРМА ЗАПИСИ
Принимаются оба вида — со пробелом и слитно: ЕГЭ-бланк не терпит разделителей, а в LMS
составной ответ принято писать через пробел. Иначе половина верно решивших получила бы
«неверно» из-за формата.

dry-run по умолчанию; `--apply` при DBCHECK_OK=1. Бэкап до записи, проверка после COMMIT.

Запуск: python scripts/tsk371_fix_game_answers.py --backup <файл.json> [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk369_collect import dsn  # noqa: E402

# id → принимаемые формы ответа (первая — основная)
ANSWERS: dict[int, list[str]] = {
    2383: ["2324324445", "23 24 32 44 45"],
    2384: ["22 35", "2235"],
    2385: ["10 11", "1011"],
    # Второй заход: эти три в первом аудите числились «совпал» — разбор брал ответ ПЕРВОЙ
    # задачи связки, и он случайно совпадал с тем, что импорт положил в LMS. После привязки
    # блока по хвосту условия стало видно расхождение; ответы пересчитаны перебором:
    #   3765 — ходы +1 и ×2, финиш ≥54 → S = 13, 25;
    #   3766 — ходы +1 и ×4, финиш ≥65 → S = 4, 15;
    #   3767 — ходы +1 и ×3, финиш ≥38 → S = 4, 11.
    3765: ["13 25", "1325"],
    3766: ["4 15", "415"],
    3767: ["4 11", "411"],
}

SHORT_ANSWER_TEMPLATE = {"regex": None, "use_regex": False,
                         "normalization": ["trim", "lower"]}


async def main(backup_path: Path, apply: bool) -> None:
    ids = sorted(ANSWERS)
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = {r["id"]: r for r in await conn.fetch(
            "SELECT id, external_uid, is_active, solution_rules, "
            "       task_content->>'answer_raw' AS answer_raw "
            "FROM tasks WHERE id = ANY($1::int[])", ids)}
        missing = sorted(set(ids) - set(rows))
        if missing:
            raise RuntimeError(f"не нашёл заданий: {missing}")
        inactive = [i for i in ids if not rows[i]["is_active"]]
        if inactive:
            raise RuntimeError(f"задания неактивны: {inactive}")

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            [{"id": i, "external_uid": rows[i]["external_uid"],
              "solution_rules": json.loads(rows[i]["solution_rules"] or "null"),
              "answer_raw": rows[i]["answer_raw"]} for i in ids],
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Бэкап прежних правил: {backup_path}")

        for i in ids:
            sr = json.loads(rows[i]["solution_rules"] or "{}")
            old = ((sr.get("short_answer") or {}).get("accepted_answers") or [{}])[0].get("value")
            print(f"  id={i} {rows[i]['external_uid']}: {old!r} → {ANSWERS[i]}")

        async with conn.transaction():
            for i, variants in ANSWERS.items():
                sr = json.loads(rows[i]["solution_rules"] or "{}")
                score = sr.get("max_score", 1)
                sr["short_answer"] = {
                    **SHORT_ANSWER_TEMPLATE,
                    "accepted_answers": [{"score": score, "value": v} for v in variants],
                }
                sr["manual_review_required"] = False
                sr["auto_check"] = True
                await conn.execute(
                    "UPDATE tasks SET solution_rules = $2::jsonb, "
                    "  task_content = jsonb_set(task_content, '{answer_raw}', to_jsonb($3::text)) "
                    "WHERE id = $1",
                    i, json.dumps(sr, ensure_ascii=False), variants[0])

            check = {r["id"]: r for r in await conn.fetch(
                "SELECT id, solution_rules#>'{short_answer,accepted_answers}' AS acc "
                "FROM tasks WHERE id = ANY($1::int[])", ids)}
            bad = []
            for i, variants in ANSWERS.items():
                acc = check[i]["acc"]
                got = [a["value"] for a in (json.loads(acc) if isinstance(acc, str) else acc)]
                if got != variants:
                    bad.append((i, got))
            if bad:
                raise AssertionError(f"проверка внутри транзакции не прошла: {bad}")
            print(f"Внутри транзакции: обновлено и проверено {len(ids)} заданий.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after = await conn.fetch(
            "SELECT id, solution_rules#>>'{short_answer,accepted_answers}' AS acc, "
            "       (solution_rules->>'manual_review_required')::bool AS manual "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id", ids)
        for r in after:
            print(f"  id={r['id']}: {r['acc']} ручная_проверка={r['manual']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    try:
        asyncio.run(main(Path(a.backup), a.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
