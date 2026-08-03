# -*- coding: utf-8 -*-
"""tsk-546: перевести 132 задания с обязательной ручной проверкой из типа SA в SA_COM.

КОНТЕКСТ
Продолжение tsk-438 (открытый вопрос: давать ли типу SA «оптимистичный зачёт»). Решение
оператора — не править движок, а привести данные в соответствие смыслу заданий.

Разведка прод-БД (read-only, MCP `learn_prod_db`) показала, что у ВСЕХ 132 активных
заданий `type='SA' AND manual_review_required=true` вычислимого эталонного ответа нет и
быть не может — это не потерянный при импорте ключ (класс tsk-358/362), а структурное
несоответствие типа: ответом служит файл, программа или произвольный текст, а не короткое
значение. Четыре однородных кластера:

  * `sdamgia:oge:13:*` (25, курс 1178 «Презентация или текстовый документ») — ответ
    буквально ФАЙЛ (.odp/.odt), в условии ссылка на архив с исходными материалами;
  * `sdamgia:oge:15:*` (25, курс 1180 «Робот в среде КуМир») — программа для исполнителя;
  * `sdamgia:oge:16:*` (30, курс 1181 «Программа на анализ последовательности») — программа;
  * `authored:chat-boty-tg-vk-max:*` (52, 42 курса) — авторские практические задания
    (скриншот работающего бота либо объяснение в одно предложение).

Инвариант `scripts/oge_answer_invariant.py` подтверждает это для ОГЭ: у заданий 13/15/16
(часть 2) ответа может не быть, но пометка ручной проверки обязана стоять. Источник
(sdamgia) машинно-проверяемого ключа для этих номеров не публикует.

ЧТО ДАЁТ СМЕНА ТИПА (и почему это ровно то, что просил оператор)
  1. `attempts.py:661-663` — SA_COM входит в `COMMENT_TASK_TYPES` и при
     `manual_review_required=true` (авто-вердикта нет, `is_correct=None`) получает
     ОПТИМИСТИЧНЫЙ ЗАЧЁТ: ученику сразу `score=max_score, is_correct=true`, задание при
     этом остаётся в очереди преподавателя (`checked_at IS NULL`), и преподаватель может
     снять зачёт через `/regrade`. Плоскому SA этого не даёт ничто — отсюда и была задача.
  2. `attempts.py:705-736` (гейт tsk-419) — у SA_COM обязателен комментарий ИЛИ файл.
     Именно этого не хватало: без доказательства работы преподавателю нечего проверять.
  3. Форма SPW `TaskFormSA_COM.tsx` даёт поле «Комментарий» — редактор кода с подсветкой
     («пояснение, код или фрагмент решения») и поле «Файл»; кнопка отправки блокируется
     ДО сабмита с текстом-причиной, а не отклоняется сервером постфактум.

ЧТО ДЕЛАЕТ (одна транзакция)
  A. `task_content.type`: SA → SA_COM у всех 132.
  B. `solution_rules.requires_attachment` → true у 25 заданий ОГЭ-13: ответ там буквально
     файл, и без флага гейт tsk-419 удовлетворился бы одним комментарием. У ОГЭ-15/16
     флаг НЕ ставится намеренно — короткую программу естественнее вписать в поле
     «Комментарий» (это редактор кода), файл остаётся возможным, но не обязательным.
     У 26 из 52 авторских заданий флаг уже стоит — он сохраняется как есть, у остальных
     26 не ставится (там по условию требуется объяснение словами, а не файл).

ЧЕГО НЕ ТРОГАЕТ: `manual_review_required` (остаётся true везде — считать нечего),
эталоны/`short_answer`, тексты условий, файлы, порядок, требования курса, любые другие
задания и типы.

ЗАЩИТЫ
  * состав выборки сверяется с разведкой поштучно: неизвестный `external_uid` или
    расхождение в размере кластера — СТОП без записи (данные уехали с момента разбора);
  * бэкап прежнего состояния (type + requires_attachment по каждому id) на диск ДО записи;
  * повторный запуск идемпотентен: уже переведённые в SA_COM в выборку не попадают;
  * dry-run по умолчанию; запись — только с --apply при DBCHECK_OK=1;
  * построчная проверка внутри транзакции и независимая построчная после COMMIT.

Запуск:
  python scripts/tsk546_sa_to_sa_com.py --backup <файл>            # dry-run
  DBCHECK_OK=1 python scripts/tsk546_sa_to_sa_com.py --backup <файл> --apply
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

# Ожидаемый состав выборки по разведке 2026-08-03 (MCP learn_prod_db, read-only).
# Расхождение = данные изменились после разбора → останавливаемся, а не пишем вслепую.
EXPECTED: dict[str, int] = {
    "oge_13": 25,
    "oge_15": 25,
    "oge_16": 30,
    "authored_bot": 52,
}
# Кластер, где ответ — файл: требуем вложение явно (см. докстринг, пункт B).
ATTACHMENT_BUCKETS = {"oge_13"}


def bucket_of(external_uid: str) -> str:
    """Кластер задания по external_uid. Неизвестный префикс → ValueError (стоп-сигнал)."""
    uid = external_uid or ""
    if uid.startswith("sdamgia:oge:13:"):
        return "oge_13"
    if uid.startswith("sdamgia:oge:15:"):
        return "oge_15"
    if uid.startswith("sdamgia:oge:16:"):
        return "oge_16"
    if uid.startswith("authored:chat-boty-tg-vk-max:"):
        return "authored_bot"
    raise ValueError(f"неизвестный кластер для external_uid={uid!r}")


async def load_targets(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """Активные SA с обязательной ручной проверкой — ровно тот предикат, что в разведке."""
    return await conn.fetch(
        "SELECT id, external_uid, course_id, "
        "       task_content->>'type' AS type, "
        "       COALESCE((solution_rules->>'requires_attachment')::boolean, false) "
        "         AS requires_attachment "
        "FROM tasks "
        "WHERE task_content->>'type' = 'SA' "
        "  AND COALESCE((solution_rules->>'manual_review_required')::boolean, false) IS TRUE "
        "  AND is_active = true "
        "ORDER BY id"
    )


async def student_data_report(conn: asyncpg.Connection, ids: list[int]) -> str:
    """Что уже сдано по этим заданиям.

    Смена типа НЕ переоценивает прошлые результаты (checking_service работает на приёме
    ответа), поэтому это не гейт, а факт для отчёта и артефакта ревью: незакрытые
    (`checked_at IS NULL`) работы после смены типа поедут по ветке SA_COM в очереди.
    """
    row = await conn.fetchrow(
        "SELECT count(*) AS total, "
        "       count(*) FILTER (WHERE checked_at IS NULL) AS pending, "
        "       count(DISTINCT user_id) AS students "
        "FROM task_results WHERE task_id = ANY($1::int[])",
        ids,
    )
    return (f"результатов {row['total']}, из них незакрытых {row['pending']}, "
            f"учеников {row['students']}")


async def main(backup_path: Path, apply: bool) -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await load_targets(conn)
        if not rows:
            print("Нечего менять: активных SA с ручной проверкой нет (повторный запуск?).")
            return

        by_bucket: dict[str, list[asyncpg.Record]] = {}
        for r in rows:
            by_bucket.setdefault(bucket_of(r["external_uid"]), []).append(r)

        print(f"Кандидатов (type=SA, manual_review_required=true, is_active=true): {len(rows)}")
        for name in sorted(by_bucket):
            got, want = len(by_bucket[name]), EXPECTED.get(name)
            mark = "ок" if got == want else f"ЖДАЛИ {want}"
            print(f"  {name:<14} {got:>3}  ({mark})")

        if {k: len(v) for k, v in by_bucket.items()} != EXPECTED:
            raise RuntimeError(
                "СТОП: состав выборки разошёлся с разведкой — разбирать вручную, "
                "не переводить вслепую."
            )

        ids = [r["id"] for r in rows]
        attach_ids = [
            r["id"] for name in ATTACHMENT_BUCKETS for r in by_bucket.get(name, [])
            if not r["requires_attachment"]
        ]
        already_attach = sum(1 for r in rows if r["requires_attachment"])
        print(f"\nA. SA → SA_COM: {len(ids)} заданий")
        print(f"B. requires_attachment → true: {len(attach_ids)} (ОГЭ-13; "
              f"флаг уже стоял у {already_attach} авторских — их не трогаю)")
        print(f"Данные учеников по этим заданиям: {await student_data_report(conn, ids)}")

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(
            json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"Бэкап прежнего состояния: {backup_path}")

        async with conn.transaction():
            await conn.execute(
                "UPDATE tasks SET task_content = jsonb_set(task_content, '{type}', '\"SA_COM\"') "
                "WHERE id = ANY($1::int[])",
                ids,
            )
            if attach_ids:
                await conn.execute(
                    "UPDATE tasks SET solution_rules = "
                    "  jsonb_set(solution_rules, '{requires_attachment}', 'true') "
                    "WHERE id = ANY($1::int[])",
                    attach_ids,
                )

            bad: list[tuple[int, str]] = []
            for r in await conn.fetch(
                "SELECT id, task_content->>'type' AS type, "
                "       COALESCE((solution_rules->>'requires_attachment')::boolean, false) AS ra, "
                "       COALESCE((solution_rules->>'manual_review_required')::boolean, false) AS mrr "
                "FROM tasks WHERE id = ANY($1::int[])",
                ids,
            ):
                if r["type"] != "SA_COM":
                    bad.append((r["id"], f"тип {r['type']}, ждали SA_COM"))
                if not r["mrr"]:
                    bad.append((r["id"], "manual_review_required слетел"))
                if r["id"] in attach_ids and not r["ra"]:
                    bad.append((r["id"], "requires_attachment не выставлен"))
            if bad:
                raise AssertionError(f"проверка внутри транзакции не прошла: {bad[:10]}")
            print(f"Внутри транзакции: переведено {len(ids)}, "
                  f"вложение потребовано у {len(attach_ids)} — проверено построчно.")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after = {
            r["id"]: r
            for r in await conn.fetch(
                "SELECT id, task_content->>'type' AS type, "
                "       COALESCE((solution_rules->>'requires_attachment')::boolean, false) AS ra, "
                "       COALESCE((solution_rules->>'manual_review_required')::boolean, false) AS mrr "
                "FROM tasks WHERE id = ANY($1::int[])",
                ids,
            )
        }
        problems = [
            (tid, dict(after[tid]))
            for tid in ids
            if after[tid]["type"] != "SA_COM"
            or not after[tid]["mrr"]
            or (tid in attach_ids and not after[tid]["ra"])
        ]
        left = await conn.fetchval(
            "SELECT count(*) FROM tasks "
            "WHERE task_content->>'type' = 'SA' "
            "  AND COALESCE((solution_rules->>'manual_review_required')::boolean, false) IS TRUE "
            "  AND is_active = true"
        )
        print(f"  проверено построчно: {len(ids)}; расхождений: {len(problems)}")
        print(f"  активных SA с ручной проверкой осталось: {left} (ждём 0)")
        if problems:
            print(f"  ПРОБЛЕМНЫЕ: {problems[:10]}")
            sys.exit(1)
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
