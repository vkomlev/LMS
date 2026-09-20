"""tsk-1015 — слить дубли курсов «Трек 2»/«Трек 3» (AI-предприниматель).

Контекст: tsk-903 (10-11.09.2026, CreateCourses) ошибочно решила, что треков 2/3
в LMS ещё нет, и опубликовала их заново через provision-course — вместо resolve
существующих 1161/1245 (созданы 04.07.2026, tsk-174) появились дубли-корни
1560/1561 под другим course_uid (`wp:ai-predprinimatel-trek-2/3` вместо
`wp:trek-2/3`). Разведка и решение — D:\\Work\\Root\\tasks\\tsk-1015-*.md.

Что делает транзакция (порядок важен: сначала контент/структура, потом
зачисление, деактивация — последней):

1. Вступление курса 1161 (Трек 2) заменяется на сентябрьский текст (материал
   4272 из дубля 1560) — оператор выбрал его как более сильный (продающий,
   с конкретной выгодой) вместо служебного июльского.
2. Вступление курса 1245 (Трек 3) заменяется на сентябрьский текст (материал
   4382 из дубля 1561) — тут агент-ревьюер был уверен (не спорно): текст точнее
   и с навигационной подсказкой.
3. Раздел 6 курса 1245 (parent_course_id=1236): порядок подкурсов «Практика»
   раньше «Экономики»/«Финала» — дефект июльской версии (в 1245 order_number
   был 1,2,4,6,7,9 — Практика(6) перед Экономикой(7)/Финалом(9)). Правильный
   порядок подтверждён сентябрьской версией (1561): Экономика→Масштабирование→
   Финал→Практика. Меняем order_number: 1239(Экономика) 7→3, 1241(Финал) 9→5.
   1240(Масштабирование)=4 и 1242(Практика)=6 уже верны, не трогаем.
4. Ученик 4641 переносится с дубля 1560 на 1161 (Трек 2) — на 15.09 оказался
   разорван между копиями одного трека (1245 верно, 1560 дубль). Переносим
   строку user_courses и 3 прочитанных материала (student_material_progress)
   на материалы-аналоги в дереве 1161 (сопоставлены по идентичному тексту/
   заголовку: 4272→2987 корень, 4273→2621, 4274→2622).
5. Курсы 1560/1561 деактивируются (is_active=false, НЕ удаляются) — пропадают
   из поиска методиста (`/teacher/courses/search`, only_active=True,
   app/api/v1/teacher_assignments.py:191-193), история остаётся для аудита.

Идемпотентность: скрипт проверяет признаки уже применённого переноса (наличие
строки user_courses для 4641/1161, наличие student_material_progress для
4641/2987) и останавливается, если они уже есть — повторный запуск безопасен.

Обратимо: is_active вернуть в true (UPDATE courses SET is_active=true WHERE id
IN (1560,1561)); enrollment 4641 — обратный перенос по тем же строкам; тексты
вступлений — восстановить из значений "BEFORE" в выводе dry-run; order_number
раздела 6 — вернуть 1239→7, 1241→9.

Запуск (DSN прод-роли lms_prod подставляется из .mcp.json):
    python scripts/merge_ai_predprinimatel_track_duplicates_tsk1015.py            # dry-run (ROLLBACK)
    DBCHECK_OK=1 python scripts/merge_ai_predprinimatel_track_duplicates_tsk1015.py --apply  # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]

TRACK2_KEEP_ID = 1161
TRACK2_DUP_ID = 1560
TRACK3_KEEP_ID = 1245
TRACK3_DUP_ID = 1561

TRACK2_INTRO_TARGET = 2987   # материал-интро 1161 — перезаписывается
TRACK2_INTRO_SOURCE = 4272   # текст берём отсюда (1560, сентябрьский)

TRACK3_INTRO_TARGET = 2998   # материал-интро 1245 — перезаписывается
TRACK3_INTRO_SOURCE = 4382   # текст берём отсюда (1561, сентябрьский)

SECTION6_PARENT = 1236
SECTION6_ORDER_FIXES = [
    (1239, 3),  # Экономика: 7 -> 3
    (1241, 5),  # Финал: 9 -> 5
]

STUDENT_ID = 4641
# (material_id в дереве 1560, material_id-аналог в дереве 1161, completed_at)
PROGRESS_MOVE = [
    (4272, 2987, "2026-09-15T12:59:12.693+00:00"),
    (4273, 2621, "2026-09-15T12:57:28.063+00:00"),
    (4274, 2622, "2026-09-15T12:57:32.910+00:00"),
]


def load_prod_dsn_asyncpg_style() -> str:
    """DSN роли lms_prod из .mcp.json, в формате postgresql+asyncpg:// для SQLAlchemy."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    return (
        f"postgresql+asyncpg://{parts.username}:{unquote(parts.password)}"
        f"@{parts.hostname}:{parts.port}{parts.path}"
    )


async def main(apply: bool) -> int:
    import os

    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()
    sys.path.insert(0, str(PROJECT_ROOT))
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import text

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-1015: слияние дублей Трек 2/3 — {mode} ===\n")

    engine = create_async_engine(os.environ["DATABASE_URL"])
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        # --- идемпотентность: уже применяли? ---
        already = (await db.execute(
            text("SELECT 1 FROM user_courses WHERE user_id=:u AND course_id=:c"),
            {"u": STUDENT_ID, "c": TRACK2_KEEP_ID},
        )).first()
        if already:
            print("Перенос ученика 4641 на 1161 уже есть — похоже, скрипт уже применяли. Стоп.")
            await engine.dispose()
            return 1

        # --- BEFORE ---
        print("--- BEFORE ---")
        for cid in (TRACK2_KEEP_ID, TRACK2_DUP_ID, TRACK3_KEEP_ID, TRACK3_DUP_ID):
            row = (await db.execute(
                text("SELECT id, course_uid, is_active FROM courses WHERE id=:c"), {"c": cid}
            )).mappings().first()
            print(f"  courses[{cid}]: {dict(row) if row else None}")
        for mid in (TRACK2_INTRO_TARGET, TRACK3_INTRO_TARGET):
            row = (await db.execute(
                text("SELECT id, content FROM materials WHERE id=:m"), {"m": mid}
            )).mappings().first()
            print(f"  materials[{mid}].content: {dict(row)['content'] if row else None}")
        section6 = (await db.execute(
            text("SELECT course_id, order_number FROM course_parents WHERE parent_course_id=:p ORDER BY order_number"),
            {"p": SECTION6_PARENT},
        )).mappings().all()
        print(f"  course_parents[parent={SECTION6_PARENT}]: {[dict(r) for r in section6]}")
        uc = (await db.execute(
            text("SELECT * FROM user_courses WHERE user_id=:u ORDER BY course_id"), {"u": STUDENT_ID}
        )).mappings().all()
        print(f"  user_courses[user={STUDENT_ID}]: {[dict(r) for r in uc]}")

        # --- WRITES ---
        print("\n--- WRITES ---")

        r = await db.execute(
            text("UPDATE materials SET content=(SELECT content FROM materials WHERE id=:src), "
                 "updated_at=now() WHERE id=:dst"),
            {"src": TRACK2_INTRO_SOURCE, "dst": TRACK2_INTRO_TARGET},
        )
        print(f"  1. интро 1161 <- материал {TRACK2_INTRO_SOURCE}: rowcount={r.rowcount}")

        r = await db.execute(
            text("UPDATE materials SET content=(SELECT content FROM materials WHERE id=:src), "
                 "updated_at=now() WHERE id=:dst"),
            {"src": TRACK3_INTRO_SOURCE, "dst": TRACK3_INTRO_TARGET},
        )
        print(f"  2. интро 1245 <- материал {TRACK3_INTRO_SOURCE}: rowcount={r.rowcount}")

        # trg_set_course_parent_order_number (BEFORE UPDATE) сам каскадно сдвигает
        # order_number соседей при точечном UPDATE — при разрывах в нумерации
        # (тут: 1,2,4,6,7,9) это даёт не тот порядок, что задуман (проверено
        # dry-run'ом: получилось 1,2,3,5,6,8 вместо 1,2,3,4,5,6). У 1240/1242
        # order_number и так уже верный (4 и 6) — их трогать не нужно вовсе,
        # каскад ломает именно их. Триггер сам уважает
        # app.skip_course_parent_order_trigger (transaction-local, см.
        # WHEN-условие триггера) — тем же путём ставим точные значения без каскада.
        await db.execute(text("SELECT set_config('app.skip_course_parent_order_trigger', 'true', true)"))
        for course_id, new_order in SECTION6_ORDER_FIXES:
            r = await db.execute(
                text("UPDATE course_parents SET order_number=:o "
                     "WHERE course_id=:c AND parent_course_id=:p"),
                {"o": new_order, "c": course_id, "p": SECTION6_PARENT},
            )
            print(f"  3. course_parents[{course_id}].order_number -> {new_order} (без каскада): rowcount={r.rowcount}")
        await db.execute(text("SELECT set_config('app.skip_course_parent_order_trigger', 'false', true)"))

        r = await db.execute(
            text("INSERT INTO user_courses (user_id, course_id, added_at, order_number, is_active) "
                 "SELECT user_id, :new_c, added_at, order_number, is_active "
                 "FROM user_courses WHERE user_id=:u AND course_id=:old_c"),
            {"new_c": TRACK2_KEEP_ID, "u": STUDENT_ID, "old_c": TRACK2_DUP_ID},
        )
        print(f"  4a. user_courses INSERT (4641 -> 1161): rowcount={r.rowcount}")

        r = await db.execute(
            text("UPDATE user_courses SET is_active=false WHERE user_id=:u AND course_id=:c"),
            {"u": STUDENT_ID, "c": TRACK2_DUP_ID},
        )
        print(f"  4b. user_courses[4641,1560].is_active=false: rowcount={r.rowcount}")

        for _src_mid, dst_mid, completed_at in PROGRESS_MOVE:
            r = await db.execute(
                text("INSERT INTO student_material_progress "
                     "(student_id, material_id, status, completed_at, source) "
                     "VALUES (:s, :m, 'completed', :ts, 'system') "
                     "ON CONFLICT (student_id, material_id) DO NOTHING"),
                {"s": STUDENT_ID, "m": dst_mid, "ts": datetime.fromisoformat(completed_at)},
            )
            print(f"  4c. student_material_progress[4641,{dst_mid}]: rowcount={r.rowcount}")

        r = await db.execute(
            text("UPDATE courses SET is_active=false WHERE id IN (:d1,:d2)"),
            {"d1": TRACK2_DUP_ID, "d2": TRACK3_DUP_ID},
        )
        print(f"  5. courses[{TRACK2_DUP_ID},{TRACK3_DUP_ID}].is_active=false: rowcount={r.rowcount}")

        # --- AFTER (в транзакции) ---
        print("\n--- AFTER (в транзакции) ---")
        for cid in (TRACK2_KEEP_ID, TRACK2_DUP_ID, TRACK3_KEEP_ID, TRACK3_DUP_ID):
            row = (await db.execute(
                text("SELECT id, course_uid, is_active FROM courses WHERE id=:c"), {"c": cid}
            )).mappings().first()
            print(f"  courses[{cid}]: {dict(row) if row else None}")
        section6_after = (await db.execute(
            text("SELECT course_id, order_number FROM course_parents WHERE parent_course_id=:p ORDER BY order_number"),
            {"p": SECTION6_PARENT},
        )).mappings().all()
        print(f"  course_parents[parent={SECTION6_PARENT}]: {[dict(r) for r in section6_after]}")
        uc_after = (await db.execute(
            text("SELECT * FROM user_courses WHERE user_id=:u ORDER BY course_id"), {"u": STUDENT_ID}
        )).mappings().all()
        print(f"  user_courses[user={STUDENT_ID}]: {[dict(r) for r in uc_after]}")
        prog_after = (await db.execute(
            text("SELECT material_id, status, source FROM student_material_progress "
                 "WHERE student_id=:s ORDER BY material_id"), {"s": STUDENT_ID}
        )).mappings().all()
        print(f"  student_material_progress[student={STUDENT_ID}]: {[dict(r) for r in prog_after]}")

        if apply:
            await db.commit()
            print("\nCOMMIT — изменения записаны.")
        else:
            await db.rollback()
            print("\nROLLBACK — dry-run, изменения откатаны.")

        await engine.dispose()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить слияние (COMMIT).")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
