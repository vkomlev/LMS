"""tsk-442/455: слияние двух учётных записей одного человека.

Перенос ВСЕХ данных `source_id` в `target_id` и деактивация source
(`is_active=false`, `merged_into_user_id=target`) — источник не удаляется,
история остаётся читаемой. Правила переноса и список таблиц — см.
`SIMPLE_MOVES`/`CONFLICT_MOVES`/`DELETE_ON_MERGE` ниже, детали — докстринг
`scripts/merge_users.py` (CLI-обёртка над этим модулем, ручной запуск по
протоколу /db-check).

tsk-610: списки переноса — не «всё, что связано с человеком», а перечисление,
которое надо пополнять вместе со схемой. Подписка (tsk-301) и перерыв (tsk-511)
появились после них и потому не переезжали, а `verify_merge` этого не видел:
он проверял ровно те же таблицы, что и переносил. Живой случай — Грабовский
4525→4560: занятия, курсы и расписание уехали на новую учётку, тариф с тарифной
группой остался на слитой, и человек две недели ходил, невидимый для денег.

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

from app.services.learning_engine_service import lock_course_state

logger = logging.getLogger("app.user_merge")

# Прямой перенос — свой `id` PK у таблицы, FK на users без доп. уникальности.
SIMPLE_MOVES = [
    ("identity_link", "user_id"),
    # tsk-610: перерыв — основание, по которому месяц считается неполным.
    # Оставшись у слитой учётки, он молча пропадает из денег живой: расписание
    # уже переехало, а причина пропусков — нет (прод, Грабовский 4525→4560).
    ("student_break", "student_id"),
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
    # tsk-742: кто закрепил и кто снял куратора. Уникальности на этих колонках
    # нет, поэтому обычный перенос; сами периоды ответственности переезжают
    # отдельно (`_move_curator_periods`) — там мешает частичный уникальный
    # индекс «один действующий куратор на ученика».
    ("student_curator", "assigned_by"),
    ("student_curator", "ended_by"),
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
    # tsk-548: ручная цена — договорённость с человеком, а не свойство строки в
    # таблице. Не переехав, она молча превратилась бы в тариф по прайсу.
    ("student_price_override", "student_id", ["group_id"]),
]

# Не переносится — удаляется у source (форсированный логаут деактивируемой учётки).
DELETE_ON_MERGE = [
    ("user_session", "user_id"),
    # tsk-610: `student_course_state` — производный кеш доступности подкурсов, а
    # не история. У target он свой и пересчитывается сам; строки source остались
    # бы мусором, который никто уже не обновляет.
    ("student_course_state", "student_id"),
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


async def _move_subscription(db: AsyncSession, source_id: int, target_id: int) -> None:
    """Перенести тариф, квоту наставника и купленные пакеты на целевую учётку.

    **Найдено на проде 2026-08-14, tsk-301.** Слияние переносило расписание и курс,
    но не подписку: старый аккаунт с тарифом `base_legacy` уходил в неактивные, а
    новый оставался с `demo`, выданным при регистрации. `demo` денежной привязки не
    имеет и перекрывает группу курса — ученик, ходивший два раза в неделю, остался
    вообще без начисления, и заметно это стало только при разборе.

    **Тариф — договорённость с человеком, а не свойство строки** (та же логика, что
    у ручной цены в `CONFLICT_MOVES`). Поэтому `demo` у цели считается пустым
    местом: он не выбран, а проставлен автоматически при регистрации. Любой другой
    тариф цели — осознанное решение, его не трогаем.

    Квота складывается, а не затирается: расход обеих учёток в одном месяце — это
    расход одного человека. Пакеты переезжают всегда, они оплачены.
    """
    from app.services.subscription_service import DEFAULT_PLAN_CODE  # noqa: PLC0415

    source_plan = (
        await db.execute(
            text(
                "SELECT p.code FROM student_subscription s "
                "  JOIN subscription_plan p ON p.id = s.plan_id "
                " WHERE s.student_id = :s AND s.ends_on IS NULL"
            ),
            {"s": source_id},
        )
    ).scalar()
    target_plan = (
        await db.execute(
            text(
                "SELECT p.code FROM student_subscription s "
                "  JOIN subscription_plan p ON p.id = s.plan_id "
                " WHERE s.student_id = :s AND s.ends_on IS NULL"
            ),
            {"s": target_id},
        )
    ).scalar()

    # У цели тариф выбран человеком — он и остаётся; действующую строку источника
    # закрываем, чтобы после переноса истории не оказалось двух действующих.
    if target_plan is not None and target_plan != DEFAULT_PLAN_CODE:
        await db.execute(
            text(
                # GREATEST — из-за CHECK `ends_on >= starts_on` (tsk-610):
                # подписка, открытая будущей датой, иначе уронила бы слияние.
                "UPDATE student_subscription "
                "   SET ends_on = GREATEST(starts_on, CURRENT_DATE) "
                " WHERE student_id = :s AND ends_on IS NULL"
            ),
            {"s": source_id},
        )
    elif source_plan is not None:
        # У цели пусто или автоматический `demo` — уступает тарифу источника.
        await db.execute(
            text(
                # GREATEST — из-за CHECK `ends_on >= starts_on` (tsk-610):
                # подписка, открытая будущей датой, иначе уронила бы слияние.
                "UPDATE student_subscription "
                "   SET ends_on = GREATEST(starts_on, CURRENT_DATE) "
                " WHERE student_id = :s AND ends_on IS NULL"
            ),
            {"s": target_id},
        )

    # Историю переносим целиком: по ней видно, по какой группе считался прошлый
    # месяц, а это единственный источник правды для закрытых начислений.
    await db.execute(
        text(
            "UPDATE student_subscription SET student_id = :t WHERE student_id = :s"
        ),
        {"t": target_id, "s": source_id},
    )

    # Квота: сложить расход за общие месяцы, остальные строки перенести.
    await db.execute(
        text(
            "UPDATE student_ai_quota t SET used = t.used + s.used, updated_at = now() "
            "  FROM student_ai_quota s "
            " WHERE t.student_id = :t AND s.student_id = :s AND s.period = t.period"
        ),
        {"t": target_id, "s": source_id},
    )
    await db.execute(
        text(
            "DELETE FROM student_ai_quota s WHERE s.student_id = :s AND EXISTS "
            "  (SELECT 1 FROM student_ai_quota t "
            "    WHERE t.student_id = :t AND t.period = s.period)"
        ),
        {"t": target_id, "s": source_id},
    )
    await db.execute(
        text("UPDATE student_ai_quota SET student_id = :t WHERE student_id = :s"),
        {"t": target_id, "s": source_id},
    )

    # Пакеты оплачены — переезжают без условий.
    await db.execute(
        text("UPDATE student_ai_grant SET student_id = :t WHERE student_id = :s"),
        {"t": target_id, "s": source_id},
    )


async def _move_curator_periods(db: AsyncSession, source_id: int, target_id: int) -> None:
    """Кураторство переезжает к живой учётке (tsk-742).

    **Почему отдельным шагом.** Общий механизм `CONFLICT_MOVES` разводит строки
    по составной уникальности «колонка + соседняя колонка», а у `student_curator`
    уникальность частичная и одноколоночная: один ДЕЙСТВУЮЩИЙ куратор на
    ученика. Прогнать её через общий механизм нельзя — он снял бы не то.

    **Почему это вообще нужно.** Оставшись у слитой учётки, закрепление молча
    пропадает: живой ученик становится «ничей» между занятиями, то есть
    возвращается ровно в то состояние, из которого задача выводит. У слитого
    преподавателя так же молча исчезает вся его группа. Тот же класс, что
    перерыв в tsk-610 и ручная цена в tsk-548.

    Две стороны, и правило у них разное:

    * **ученик** — история переезжает целиком, но если у живой учётки уже есть
      действующий куратор, открытый период слитой закрывается: двух
      ответственных быть не может, и решает тот, кто закреплён за живым
      человеком;
    * **преподаватель** — его ученики переходят к целевой учётке; ученик, у
      которого действующий куратор уже целевая учётка, у слитой закрывается,
      иначе на нём столкнулись бы два открытых периода.
    """
    # Ученик: гасим открытый период слитой учётки, если у живой он уже есть.
    await db.execute(
        text(
            "UPDATE student_curator sc "
            "   SET ended_at = now(), "
            "       ended_reason = COALESCE(sc.ended_reason, 'учётная запись слита') "
            " WHERE sc.student_id = :source AND sc.ended_at IS NULL "
            "   AND EXISTS (SELECT 1 FROM student_curator t "
            "                WHERE t.student_id = :target AND t.ended_at IS NULL)"
        ),
        {"source": source_id, "target": target_id},
    )
    # Преподаватель: если у ученика действующий куратор — уже целевая учётка,
    # открытый период слитой закрываем, иначе после переноса их станет два.
    await db.execute(
        text(
            "UPDATE student_curator sc "
            "   SET ended_at = now(), "
            "       ended_reason = COALESCE(sc.ended_reason, 'учётная запись слита') "
            " WHERE sc.curator_id = :source AND sc.ended_at IS NULL "
            "   AND EXISTS (SELECT 1 FROM student_curator t "
            "                WHERE t.student_id = sc.student_id "
            "                  AND t.curator_id = :target AND t.ended_at IS NULL)"
        ),
        {"source": source_id, "target": target_id},
    )
    # Строки, которые после переноса схлопнулись бы в «куратор самому себе»:
    # это бывает, когда сливают ученика в его же преподавателя. Ограничение
    # `curator_id <> student_id` уронило бы всё слияние на UPDATE, поэтому
    # убираем такие строки заранее и ТОЛЬКО у сливаемой пары.
    await db.execute(
        text(
            "DELETE FROM student_curator "
            " WHERE (student_id = :source AND curator_id = :target) "
            "    OR (student_id = :target AND curator_id = :source)"
        ),
        {"source": source_id, "target": target_id},
    )
    for column in ("student_id", "curator_id"):
        await db.execute(
            text(
                f"UPDATE student_curator SET {column} = :target "  # nosec B608
                f" WHERE {column} = :source"
            ),
            {"target": target_id, "source": source_id},
        )


async def apply_merge(db: AsyncSession, source_id: int, target_id: int) -> None:
    # tsk-626: слияние — тоже писатель кеша `student_course_state` (строки
    # source удаляются списком DELETE_ON_MERGE). Правило кеша одно для всех
    # писателей: сначала блокировка ученика, иначе одиночный DELETE и
    # параллельный next-item того же ученика могут захватить строки в разном
    # порядке. Берём по возрастанию id — так же, как обходят учеников
    # многоучениковые писатели.
    for uid in sorted((int(source_id), int(target_id))):
        await lock_course_state(db, uid)

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

    # tsk-548: начисления. Расписание уже переехало к target, поэтому суммы
    # source посчитаны по расписанию, которого у него больше нет — переносить
    # их значило бы принести чужую цифру. Открытые месяцы source убираем и
    # пересчитываем target заново; закрытые не трогаем — это история, и по ним
    # человеку уже называли сумму.
    #
    # Платежей у source здесь заведомо нет: слияние с деньгами останавливается
    # выше и уходит на ручной разбор.
    await db.execute(
        text(
            "DELETE FROM charge_adjustment a WHERE a.student_id = :source "
            "  AND EXISTS (SELECT 1 FROM student_monthly_charge ch "
            "               WHERE ch.student_id = a.student_id "
            "                 AND ch.group_id = a.group_id "
            "                 AND ch.period = a.period "
            "                 AND ch.status = 'open')"
        ),
        {"source": source_id},
    )
    await db.execute(
        text(
            "DELETE FROM student_monthly_charge "
            " WHERE student_id = :source AND status = 'open'"
        ),
        {"source": source_id},
    )

    await _move_curator_periods(db, source_id, target_id)

    await _move_subscription(db, source_id, target_id)

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
    # tsk-610: того, что переносит `_move_subscription`, в списках нет — эти
    # таблицы проверяем отдельно. Верификация, которая смотрит ровно туда же,
    # куда писала, потерю тарифа не заметила: на проде она отрапортовала «всё
    # перенесено», пока платёжная принадлежность человека лежала на слитой
    # учётке (4525 → 4560).
    for table in ("student_subscription", "student_ai_quota", "student_ai_grant"):
        leftover += await count_rows(db, table, "student_id", source_id)
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

    # tsk-010: за учёткой числятся деньги — слияние останавливаем.
    # Платежи привязаны к начислению парой «ученик + группа + месяц», и просто
    # переписать им `student_id` нельзя: у target может не быть строки того же
    # месяца, а составной внешний ключ этого не допустит. Автослияние дублей
    # (tsk-455) идёт молча при регистрации — оно не должно решать за человека
    # судьбу подтверждённых платежей. Разбирать такую пару нужно руками.
    money = (
        await db.execute(
            text("SELECT count(*) AS n FROM student_payment WHERE student_id = :s"),
            {"s": source_id},
        )
    ).one()
    if money.n > 0:
        logger.warning(
            "Слияние %s → %s остановлено: за source числится платежей: %s. "
            "Нужен ручной разбор, деньги молча не переносим.",
            source_id,
            target_id,
            money.n,
        )
        return False

    async with db.begin_nested():
        await apply_merge(db, source_id, target_id)
        await db.flush()
        await verify_merge(db, source_id, target_id)

    # tsk-610: расписание приехало слиянием, а не правкой календаря — значит
    # автоприсвоение тарифа по расписанию (tsk-301) о нём не узнало: его зовёт
    # только `lesson_calendar_service`. Ученик с занятиями оставался на `demo`.
    # Повышает по-прежнему лишь с `demo`/«тарифа нет», так что legacy-цену и
    # осознанно выданные тарифы это не трогает.
    # Свой savepoint и мягкий отказ — по той же причине, по которой в savepoint
    # обёрнута сама запись слияния: слияние зовётся ВНУТРИ транзакции
    # регистрации, и любое исключение здесь оставило бы её отравленной —
    # `try/except` в роутере поймал бы ошибку, а следующий `commit()` упал бы
    # снова, и только что созданный пользователь не сохранился бы. Тариф важен,
    # но не важнее регистрации: не присвоился — это увидит суточный страж.
    from app.services import charge_service, subscription_service

    try:
        async with db.begin_nested():
            await subscription_service.upgrade_on_schedule(db, target_id)
    except Exception:
        logger.warning(
            "tsk-610: автоприсвоение тарифа после слияния %s → %s не выполнено",
            source_id,
            target_id,
            exc_info=True,
        )

    # tsk-548: расписание переехало — значит сумма месяца у target изменилась.
    # Без этого шага живая учётка остаётся вообще без начисления (на проде так
    # и вышло: у слитого «Лазаря» висели 5 500 ₽, а у настоящего ученика с
    # двумя занятиями в неделю долга не было вовсе).
    await charge_service.recalculate_open_months_for_student(db, student_id=target_id)
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
