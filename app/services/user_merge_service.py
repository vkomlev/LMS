"""tsk-442/455: слияние двух учётных записей одного человека.

Перенос ВСЕХ данных `source_id` в `target_id` и деактивация source
(`is_active=false`, `merged_into_user_id=target`) — источник не удаляется,
история остаётся читаемой. Правила переноса и список таблиц — см.
`SIMPLE_MOVES`/`CONFLICT_MOVES`/`DELETE_ON_MERGE` ниже, детали — докстринг
`scripts/merge_users.py` (CLI-обёртка над этим модулем, ручной запуск по
протоколу /db-check).

tsk-455: та же логика используется автоматически сразу после регистрации
нового аккаунта (`check_and_merge_duplicate_on_registration`), когда пара
проходит порог автослияния (`users_dedup_service.select_auto_merge_pairs`) —
раньше для этого требовался ручной запуск `scripts/tsk442_auto_merge_duplicates.py`,
и второй аккаунт мог провисеть несливённым сколько угодно (живой инцидент:
второй аккаунт ученика провисел несданным полдня, пока никто не запустил
скрипт вручную).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("app.user_merge")

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


async def fetch_user(db: AsyncSession, user_id: int) -> UserRow | None:
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


async def count_rows(db: AsyncSession, table: str, column: str, user_id: int) -> int:
    return (
        await db.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {column} = :id"),
            {"id": user_id},
        )
    ).scalar_one()


async def apply_merge(db: AsyncSession, source_id: int, target_id: int) -> None:
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

    # Карточные поля: почта и ФИО переезжают, если у target их нет (tsk-433,
    # 2026-07-30). Раньше слияние переносило только связанные строки, а
    # `users.email` оставался у source — и держал почту ЗАНЯТОЙ: частичный
    # уникальный индекс считает и неактивные записи, поэтому проставить тот же
    # адрес живому человеку было нельзя (409 при правке карточки). Плюс более
    # полное ФИО («Астафьев Данил Алексеевич») пропадало вместе с дублем.
    #
    # Порядок важен: сперва СНЯТЬ адрес у source, только потом записать его
    # target. Обратный порядок упирается в тот же уникальный индекс — адрес
    # ещё занят дублем.
    row = (
        await db.execute(
            text("SELECT email, full_name FROM users WHERE id = :source"),
            {"source": source_id},
        )
    ).first()
    source_email = row.email if row else None
    source_name = row.full_name if row else None

    await db.execute(
        text(
            "UPDATE users SET is_active = false, merged_into_user_id = :target, "
            "email = NULL WHERE id = :source"
        ),
        {"target": target_id, "source": source_id},
    )

    await db.execute(
        text(
            "UPDATE users SET "
            "  email = COALESCE(email, CAST(:src_email AS varchar)), "
            "  full_name = CASE "
            "    WHEN full_name IS NULL OR btrim(full_name) = '' "
            "      THEN CAST(:src_name AS varchar) "
            "    WHEN CAST(:src_name AS varchar) IS NOT NULL "
            "         AND length(CAST(:src_name AS varchar)) > length(full_name) "
            "      THEN CAST(:src_name AS varchar) "
            "    ELSE full_name END "
            "WHERE id = :target"
        ),
        {"target": target_id, "src_email": source_email, "src_name": source_name},
    )


async def verify_merge(db: AsyncSession, source_id: int, target_id: int) -> None:
    row = await fetch_user(db, source_id)
    assert row is not None and row.is_active is False and row.merged_into_user_id == target_id, (
        f"верификация провалена: source id={source_id} не деактивирован корректно: {row}"
    )
    leftover = 0
    for table, column in SIMPLE_MOVES + [(t, c) for t, c, _ in CONFLICT_MOVES]:
        leftover += await count_rows(db, table, column, source_id)
    for table, column in DELETE_ON_MERGE:
        leftover += await count_rows(db, table, column, source_id)
    assert leftover == 0, f"верификация провалена: у source осталось {leftover} строк"


async def merge_users(db: AsyncSession, *, source_id: int, target_id: int) -> bool:
    """Guarded слияние в SAVEPOINT текущей сессии (вызывающий код коммитит
    внешнюю транзакцию). `False` — слияние не выполнено (source/target не
    найдены, совпадают, или уже неактивны).

    tsk-455: запись обёрнута в `db.begin_nested()` — вызывается из
    `check_and_merge_duplicate_on_registration`, а та живёт ВНУТРИ той же
    транзакции, что и создание нового пользователя (auth-роутеры, soft-fail
    try/except). Без savepoint любая ошибка внутри apply_merge/verify_merge
    (например неожиданное срабатывание append-only триггера на audit_event)
    отравила бы ВСЮ транзакцию регистрации — try/except поймал бы
    исключение, но последующий `await db.commit()` в роутере упал бы
    повторно ("current transaction is aborted"), и только что созданный
    пользователь не сохранился бы. С savepoint откатывается только сама
    попытка слияния, регистрация остаётся невредимой."""
    if source_id == target_id:
        return False
    source = await fetch_user(db, source_id)
    target = await fetch_user(db, target_id)
    if source is None or target is None:
        return False
    if not source.is_active or not target.is_active:
        return False

    async with db.begin_nested():
        await apply_merge(db, source_id, target_id)
        await db.flush()
        await verify_merge(db, source_id, target_id)
    return True


async def check_and_merge_duplicate_on_registration(
    db: AsyncSession, *, new_user_id: int,
) -> Optional[int]:
    """tsk-455: сразу после регистрации нового аккаунта проверить его на
    дубль с уже существующим "плавающим" учеником (без identity_link) и,
    если пара проходит те же защиты, что и ручной автослияние-скрипт
    (`select_auto_merge_pairs`: score>=0.9, ровно одна сторона с identity,
    пара единственная в обе стороны), слить немедленно.

    Полный (не scoped на новый аккаунт) прогон `find_duplicate_candidates` —
    намеренно: `select_auto_merge_pairs` требует ГЛОБАЛЬНОЙ уникальности
    пары (у "плавающего" нет ДРУГИХ кандидатов-совпадений), урезанный до
    одного пользователя список кандидатов эту проверку бы сломал.

    НЕ триггерит UI-диалог "это вы?" и не делает auto-link на identity —
    решение оператора из tsk-442 (никакого подтверждения на самой
    регистрации) остаётся в силе, тут автоматизирован уже существующий
    безопасный порог, который раньше требовал ручного запуска
    `scripts/tsk442_auto_merge_duplicates.py`.

    Возвращает id слитого source-аккаунта (для лога вызывающей стороны) или
    `None`, если подходящей пары не нашлось."""
    from app.services.users_dedup_service import (
        DEFAULT_MATCH_THRESHOLD,
        find_duplicate_candidates,
        select_auto_merge_pairs,
    )

    candidates = await find_duplicate_candidates(db, threshold=DEFAULT_MATCH_THRESHOLD)
    auto_pairs, _manual = select_auto_merge_pairs(candidates)

    pair = next((p for p in auto_pairs if p.target_id == new_user_id), None)
    if pair is None:
        return None

    merged = await merge_users(db, source_id=pair.source_id, target_id=pair.target_id)
    if not merged:
        return None

    logger.info(
        "tsk-455 auto-merge on registration: source=%d («%s») -> target=%d («%s») score=%.3f",
        pair.source_id, pair.source_name, pair.target_id, pair.target_name, pair.score,
    )
    return pair.source_id
