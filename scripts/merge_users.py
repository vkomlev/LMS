"""tsk-442: слияние двух учётных записей (write, протокол /db-check).

Сценарий: "плавающий" ученик заведён вручную по имени (без email/tg_id), и
тот же человек либо ещё не регистрировался сам, либо уже успел завести
ВТОРОЙ аккаунт при самостоятельной регистрации (другой порядок ФИО,
опечатка, неполная фамилия — расширенный маппинг из
`scripts/tsk442_find_duplicate_candidates.py` подсвечивает такие пары, но
решение — за человеком). Скрипт переносит ВСЕ данные `--source-id` в
`--target-id` и деактивирует source (`is_active=false`,
`merged_into_user_id=target`) — источник не удаляется, история остаётся
читаемой.

Режимы:
- (по умолчанию) — dry-run: печатает обе учётки + количество строк в каждой
  затрагиваемой таблице у source. Ничего не пишет.
- `--apply` — выполняет перенос в ОДНОЙ транзакции + независимая
  read-only верификация после коммита.

Правила переноса:
- Простые таблицы (свой `id` PK, FK на users без доп. уникальности) —
  прямой UPDATE.
- Таблицы с составным PK / UNIQUE(student_col, other_col) — сначала DELETE
  строк source, которые уже есть у target (та же пара other_col), затем
  UPDATE остальных (иначе UPDATE упадёт на UNIQUE/PK violation).
- `identity_link.user_id` — прямой UPDATE (уникальность у неё на
  (kind, value), не на user_id — переносу не мешает).
- `user_session` — НЕ переносится, а удаляется: сессия, выданная
  деактивированной учётке, не должна тихо начать действовать от лица
  другой учётки (форсированный логаут вместо "подмены личности").
- `audit_event.user_id` / `attendance_event.actor_user_id` — НЕ
  переносятся вовсе, остаются на source. Это исторические аудит-логи ("кто
  совершил ЭТО действие тогда"), а не текущее состояние: `audit_event`
  вдобавок физически защищена DB-триггером `audit_event_no_modify`
  (`RAISE EXCEPTION 'audit_event is append-only'` — первый реальный прогон
  на проде упал именно на этом, транзакция откатилась целиком, данные не
  пострадали). Поскольку `users` строка при слиянии не удаляется, а только
  деактивируется — FK остаётся валиден, историю не нужно переписывать.

Запуск (см. `.env` DATABASE_URL — сначала на dev, потом на проде):
    venv/bin/python scripts/merge_users.py --source-id 123 --target-id 456
    DBCHECK_OK=1 venv/bin/python scripts/merge_users.py --source-id 123 --target-id 456 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8-sig")

from app.db.session import async_session_factory  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("merge_users")


# Прямой перенос — свой `id` PK у таблицы, FK на users без доп. уникальности.
SIMPLE_MOVES = [
    ("identity_link", "user_id"),
    ("attempts", "user_id"),
    ("task_results", "user_id"),
    ("messages", "sender_id"),
    ("messages", "recipient_id"),
    ("notifications", "user_id"),
    ("notifications", "modified_by"),
    ("access_requests", "user_id"),
    ("social_posts", "user_id"),
    ("help_requests", "student_id"),
    ("help_requests", "assigned_teacher_id"),
    ("help_requests", "closed_by"),
    ("help_requests", "claimed_by"),
    ("help_request_replies", "teacher_id"),
    ("lesson_slot", "teacher_id"),
    ("lesson_slot", "created_by"),
    ("lesson_occurrence", "teacher_id"),
    ("assignment_event", "student_id"),
    ("assignment_event", "assigned_by"),
    ("guest_session", "attributed_user_id"),
    ("guest_attempt", "attributed_user_id"),
    ("lesson_slot_student", "added_by"),
]

# (таблица, колонка_с_user_id, остальные_колонки_составной_уникальности) —
# перед UPDATE удаляем у source те строки, что уже есть у target (та же
# комбинация остальных колонок), иначе UPDATE упадёт на PK/UNIQUE violation.
CONFLICT_MOVES = [
    ("user_courses", "user_id", ["course_id"]),
    ("user_roles", "user_id", ["role_id"]),
    ("student_teacher_links", "student_id", ["teacher_id"]),
    ("student_teacher_links", "teacher_id", ["student_id"]),
    ("teacher_courses", "teacher_id", ["course_id"]),
    ("user_achievements", "user_id", ["achievement_id"]),
    ("student_task_progress", "student_id", ["task_id"]),
    ("lesson_slot_student", "student_id", ["slot_id"]),
    ("lesson_occurrence_participant", "student_id", ["occurrence_id"]),
]

# Не переносится — удаляется у source (форсированный логаут деактивируемой учётки).
DELETE_ON_MERGE = [
    ("user_session", "user_id"),
]


@dataclass
class UserRow:
    id: int
    full_name: str | None
    email: str | None
    tg_id: int | None
    is_active: bool
    merged_into_user_id: int | None


async def _fetch_user(db, user_id: int) -> UserRow | None:
    row = (
        await db.execute(
            text(
                "SELECT id, full_name, email, tg_id, is_active, merged_into_user_id "
                "FROM users WHERE id = :id"
            ),
            {"id": user_id},
        )
    ).mappings().first()
    return UserRow(**row) if row else None


async def _count(db, table: str, column: str, user_id: int) -> int:
    return (
        await db.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {column} = :id"),
            {"id": user_id},
        )
    ).scalar_one()


async def _preflight(db, source: UserRow, target: UserRow) -> None:
    print(f"source: id={source.id} «{source.full_name}» email={source.email} "
          f"tg_id={source.tg_id} is_active={source.is_active}")
    print(f"target: id={target.id} «{target.full_name}» email={target.email} "
          f"tg_id={target.tg_id} is_active={target.is_active}\n")
    print("Строк у source по таблицам (будут перенесены/удалены):")
    total = 0
    for table, column in SIMPLE_MOVES:
        n = await _count(db, table, column, source.id)
        if n:
            print(f"  {table}.{column}: {n}")
            total += n
    for table, column, _other in CONFLICT_MOVES:
        n = await _count(db, table, column, source.id)
        if n:
            print(f"  {table}.{column}: {n} (с проверкой конфликтов у target)")
            total += n
    for table, column in DELETE_ON_MERGE:
        n = await _count(db, table, column, source.id)
        if n:
            print(f"  {table}.{column}: {n} (будет УДАЛЕНО, не перенесено)")
    print(f"\nВсего строк на перенос: {total}")


async def _apply(db, source_id: int, target_id: int) -> None:
    for table, column in SIMPLE_MOVES:
        await db.execute(
            text(f"UPDATE {table} SET {column} = :target WHERE {column} = :source"),
            {"target": target_id, "source": source_id},
        )

    for table, column, other_cols in CONFLICT_MOVES:
        other = other_cols[0]
        await db.execute(
            text(
                f"DELETE FROM {table} t1 WHERE t1.{column} = :source AND EXISTS "
                f"(SELECT 1 FROM {table} t2 WHERE t2.{column} = :target "
                f"AND t2.{other} = t1.{other})"
            ),
            {"source": source_id, "target": target_id},
        )
        await db.execute(
            text(f"UPDATE {table} SET {column} = :target WHERE {column} = :source"),
            {"target": target_id, "source": source_id},
        )

    for table, column in DELETE_ON_MERGE:
        await db.execute(
            text(f"DELETE FROM {table} WHERE {column} = :source"),
            {"source": source_id},
        )

    await db.execute(
        text(
            "UPDATE users SET is_active = false, merged_into_user_id = :target "
            "WHERE id = :source"
        ),
        {"target": target_id, "source": source_id},
    )


async def _verify(db, source_id: int, target_id: int) -> None:
    row = await _fetch_user(db, source_id)
    assert row is not None and row.is_active is False and row.merged_into_user_id == target_id, (
        f"верификация провалена: source id={source_id} не деактивирован корректно: {row}"
    )
    leftover = 0
    for table, column in SIMPLE_MOVES + [(t, c) for t, c, _ in CONFLICT_MOVES]:
        leftover += await _count(db, table, column, source_id)
    for table, column in DELETE_ON_MERGE:
        leftover += await _count(db, table, column, source_id)
    assert leftover == 0, f"верификация провалена: у source осталось {leftover} строк"
    print(f"Верификация OK: source id={source_id} деактивирован, данных не осталось.")


async def _run(
    source_id: int, target_id: int, apply: bool, *, session_factory=None,
) -> None:
    """`session_factory` — точка подмены источника сессий (тесты передают
    фабрику, привязанную к своему соединению/транзакции — см.
    `lesson_occurrence_generator_tick` docstring про ту же причину)."""
    factory = session_factory or async_session_factory
    if source_id == target_id:
        print("source-id и target-id совпадают — нечего сливать.")
        return

    async with factory() as db:
        source = await _fetch_user(db, source_id)
        target = await _fetch_user(db, target_id)
        if source is None or target is None:
            print(f"Не найден пользователь: source={source}, target={target}")
            return
        if not source.is_active:
            print(f"source id={source_id} уже деактивирован (merged_into_user_id="
                  f"{source.merged_into_user_id}) — повторное слияние не выполняется.")
            return
        if not target.is_active:
            print(f"target id={target_id} сам деактивирован — выбери другую цель.")
            return

        await _preflight(db, source, target)

        if not apply:
            print("\n[dry-run] Ничего не изменено. Запустите с --apply для переноса.")
            return

        await _apply(db, source_id, target_id)
        await db.commit()
        logger.info("merge_users: source=%d -> target=%d применено", source_id, target_id)

    async with factory() as verify_db:
        await _verify(verify_db, source_id, target_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", type=int, required=True, help="Учётка, которая деактивируется")
    parser.add_argument("--target-id", type=int, required=True, help="Учётка-получатель данных")
    parser.add_argument("--apply", action="store_true", help="Выполнить перенос (по умолчанию — dry-run)")
    args = parser.parse_args()
    asyncio.run(_run(args.source_id, args.target_id, args.apply))


if __name__ == "__main__":
    main()
