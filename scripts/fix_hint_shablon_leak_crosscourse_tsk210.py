"""tsk-210 (аудитор кросс-курсовой): срезать слив подсказки «Шаблон: <ответ>» по всему LMS.

Аудитор нашёл системный остаток: SA/SA_COM-задания, где hints_text = ["<наводка>. Шаблон:
<точный ответ>."] — подсказка выдаёт эталонный ответ. tsk-218 пофиксил только 13 отмеченных
ревью заданий Python-подростков; аудит показал ~43 таких в ~19 курсах (серия «Занятие 1-8»
862-870 + остаток 823-дерева). Тот же фикс, что tsk-218, но кросс-курсовой и по факту.

Логика (безопасно, только подтверждённые сливы):
  - берём активные SA/SA_COM с «Шаблон:» в hints_text;
  - ЛИК подтверждён, если полный accepted-ответ содержится в объединённом тексте подсказок;
  - в каждом элементе массива срезаем хвост от «Шаблон:» до конца, оставляя наводку;
  - VERIFY: после среза полного ответа в подсказке нет и «Шаблон:» нет;
  - если после среза ответ ВСЁ РАВНО в наводке (сама наводка палит) — НЕ авто-фиксим,
    выводим в manual-список.

Правится только LMS-прод. Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import json
import os
import re
import sys

import asyncpg
from dotenv import load_dotenv

SHABLON_RE = re.compile(r"\s*Шаблон:.*$", re.DOTALL)


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def _strip_hints(hints: list[str]) -> list[str]:
    out = []
    for h in hints:
        stripped = SHABLON_RE.sub("", h).rstrip()
        if stripped:
            out.append(stripped)
    return out


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    fixed = 0
    manual: list[int] = []
    skipped = 0
    try:
        rows = await conn.fetch(
            "SELECT id, course_id, task_content->'hints_text' AS hints, "
            "solution_rules->'short_answer'->'accepted_answers'->0->>'value' AS ans "
            "FROM tasks WHERE is_active "
            "AND task_content->>'type' IN ('SA','SA_COM') "
            "AND task_content->>'hints_text' ILIKE '%Шаблон:%'"
        )
        async with conn.transaction():
            for r in rows:
                hints = r["hints"]
                if isinstance(hints, str):
                    hints = json.loads(hints)
                if not isinstance(hints, list):
                    skipped += 1
                    continue
                ans = r["ans"]
                joined = " ".join(hints)
                # лик подтверждён только если полный ответ реально в подсказке
                if not ans or ans not in joined:
                    skipped += 1
                    continue
                new_hints = _strip_hints(hints)
                new_joined = " ".join(new_hints)
                if ans in new_joined or "Шаблон:" in new_joined:
                    manual.append(r["id"])  # наводка сама палит ответ — руками
                    continue
                await conn.execute(
                    "UPDATE tasks SET task_content = jsonb_set(task_content, '{hints_text}', $2::jsonb) "
                    "WHERE id=$1",
                    r["id"], json.dumps(new_hints, ensure_ascii=False),
                )
                fixed += 1
                if fixed <= 6:
                    print(f"  OK {r['id']} (курс {r['course_id']}): {hints} -> {new_hints}")

            print(f"\nИтог: исправлено {fixed}, manual (наводка палит) {len(manual)}, пропущено {skipped}")
            if manual:
                print(f"  manual-список (проверить руками): {manual}")
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
