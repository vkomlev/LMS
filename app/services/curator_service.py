"""Кураторство: кто за кого отвечает и как это выводится из расписания (tsk-742).

Здесь живут две разные вещи, и смешивать их нельзя:

* **Вывод раскладки** (`derive_from_schedule`) — правило «куратор это тот, кто
  и так ведёт занятия». Считается по живым данным, ничего не пишет.
* **Закрепление** (`assign`, `apply_derived`) — запись в базу с историей.

Правило вывода целиком описано словами в `docs/curator-charter.md` § 6; здесь
только его исполнение. Расхождение между документом и этим модулем — дефект
модуля, а не документа: устав читают люди, которых раскладка касается.

**Почему вывод не фоновый.** Раскладка запускается человеком и не перетирает
ручные закрепления. Иначе куратор, назначенный руками из-за неоднозначности,
молча вернулся бы к автоматическому следующей ночью — и никто бы не понял,
почему ученик снова «ничей».
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: Окно, за которое считаются проведённые занятия во втором уровне правила.
#: 90 дней — не «побольше»: за меньший срок летний перерыв стирает историю
#: занятий целиком, и правило вырождается в «никого».
SCHEDULE_WINDOW_DAYS = 90

#: Причины назначения — те же слова, что в уставе. Пишутся в `reason`, и
#: человек читает их в карточке ученика, поэтому они по-русски и без жаргона.
REASON_PERMANENT_SLOT = "постоянный слот"
REASON_MOST_LESSONS = "больше занятий за 90 дней"

SOURCE_DERIVED = "derived"
SOURCE_MANUAL = "manual"

#: Почему ученик остался без куратора — для списка оператору.
UNRESOLVED_AMBIGUOUS = "ambiguous"
UNRESOLVED_NO_TEACHER = "no_teacher"

#: Роли, из-за которых человек не считается учеником школы.
#:
#: Преподаватели, методисты и владелец школы заведены и как `student` — иначе
#: они не могли бы открыть кабинет ученика. Считать их учениками нельзя нигде:
#: в раскладке они дают строки «без куратора», в отчёте раздувают число тех, за
#: кого владелец школы якобы отвечает сам. Живой прогон 02.09 показал это в
#: расхождении на единицу — сам оператор стоял в собственном списке «ничьих».
STAFF_ROLES = ("teacher", "methodist", "admin")


#: Тарифы, на которых кураторство не нужно (решение оператора 2026-09-02).
#:
#: `test` — служебные учётки; `demo` — человек смотрит курс, а не учится;
#: `alumni` — выпускник, обучение закончено. Общего признака в колонках у них
#: нет (`billing_exempt` только у test, `course_work=false` только у alumni),
#: поэтому список кодов, а не условие по свойствам. Появится четвёртый такой
#: тариф — строка сюда.
#:
#: Смысл не в экономии строк, а в честности отчёта: пятеро выпускников,
#: закреплённых первой раскладкой, попали бы кураторам в «не тронул ни разу» —
#: и были бы правы, потому что трогать там нечего.
NON_CURATED_PLAN_CODES = ("test", "demo", "alumni")


def active_student_sql(user_col: str) -> str:
    """SQL-условие «за этого человека имеет смысл отвечать».

    Ученик школы (не сотрудник) И его действующий тариф не из числа тех, где
    обучения нет. Отсутствие подписки исключением НЕ считается: ученик без
    строки тарифа — это обычный человек до перевода на тарифы
    (`starts_on` — дата переезда, а не прихода), и куратор ему нужен.

    Одна функция на все места: раскладка, сводка, доска и отчёт обязаны
    считать одно и то же множество, иначе они начнут спорить о том, сколько в
    школе учеников.
    """
    codes = ", ".join(f"'{c}'" for c in NON_CURATED_PLAN_CODES)
    return f"""
        {not_staff_sql(user_col)}
        AND NOT EXISTS (
            SELECT 1 FROM student_subscription ss_a
            JOIN subscription_plan sp_a ON sp_a.id = ss_a.plan_id
            WHERE ss_a.student_id = {user_col}
              AND ss_a.ends_on IS NULL
              AND sp_a.code IN ({codes})
        )
    """  # nosec B608 — user_col и коды из закрытого набора литералов модуля


def not_staff_sql(user_col: str) -> str:
    """SQL-условие «это ученик школы, а не сотрудник».

    Функция, а не скопированный `NOT EXISTS`: правило зовут и раскладка, и
    сводка, и недельный отчёт. Разошедшись, они начинают спорить о том, сколько
    в школе учеников.

    :param user_col: ссылка на колонку с идентификатором человека. Только
        литералы из закрытого набора call-sites.
    """
    roles = ", ".join(f"'{r}'" for r in STAFF_ROLES)
    return f"""
        NOT EXISTS (
            SELECT 1 FROM user_roles ur_s
            JOIN roles r_s ON r_s.id = ur_s.role_id
            WHERE ur_s.user_id = {user_col} AND r_s.name IN ({roles})
        )
    """  # nosec B608 — user_col из закрытого набора литералов модуля

# Ведущий занятия хранится В ДВУХ МЕСТАХ: колонкой (`lesson_slot.teacher_id`,
# `lesson_occurrence.teacher_id`) и строками таблиц совместного ведения
# (tsk-443). Отбор по одному из них пропускает занятия, заведённые другим
# способом, — на этом уже ловились датчик риска ухода и тесты. Поэтому оба
# источника объединяются здесь и ниже.
#
# `is_active` у строк совместного ведения — мягкое удаление: снятый
# со-преподаватель остаётся в истории занятия, но куратором быть не должен.
_DERIVE_SQL = """
WITH candidates AS (
    -- Кандидаты в кураторы: действующие преподаватели, кроме исключённых.
    -- Исключение приходит списком (оператор), а не зашито в код: завтра
    -- из кураторства выйдет кто-то другой.
    SELECT DISTINCT u.id
    FROM users u
    JOIN user_roles ur ON ur.user_id = u.id
    JOIN roles r ON r.id = ur.role_id AND r.name = 'teacher'
    WHERE u.is_active
      AND u.merged_into_user_id IS NULL
      AND u.blocked_at IS NULL
      AND NOT (u.id = ANY(:excluded))
),
students AS (
    SELECT u.id, u.full_name
    FROM users u
    JOIN user_roles ur ON ur.user_id = u.id
    JOIN roles r ON r.id = ur.role_id AND r.name = 'student'
    WHERE u.is_active
      AND u.merged_into_user_id IS NULL
      AND u.blocked_at IS NULL
      -- Сотрудники заведены и как ученики. Проверять «нет среди кандидатов»
      -- недостаточно: владелец школы из кандидатов исключён и потому
      -- проваливался в собственный список «ничьих» (живой прогон 02.09).
      -- Здесь же отсекаются тарифы без обучения (тест, демо, выпускник).
      AND {not_staff}
),
-- Уровень 1: постоянное расписание.
slot_pairs AS (
    SELECT DISTINCT lss.student_id, t.teacher_id
    FROM lesson_slot_student lss
    JOIN lesson_slot s ON s.id = lss.slot_id
    JOIN LATERAL (
        SELECT lst.teacher_id
        FROM lesson_slot_teacher lst
        WHERE lst.slot_id = s.id AND lst.is_active
        UNION
        SELECT s.teacher_id WHERE s.teacher_id IS NOT NULL
    ) t ON TRUE
    JOIN candidates c ON c.id = t.teacher_id
    WHERE lss.is_active
      AND s.is_active
      -- Слот с прошедшей датой окончания — уже не расписание.
      AND (s.active_until IS NULL OR s.active_until >= CURRENT_DATE)
),
tier1 AS (
    SELECT student_id, min(teacher_id) AS teacher_id
    FROM slot_pairs
    GROUP BY student_id
    HAVING count(*) = 1
),
-- Уровень 2: проведённые занятия за окно. Только прошедшие: занятие в
-- будущем ещё никем не проведено, и считать его работой преподавателя рано.
occ_counts AS (
    SELECT lop.student_id, t.teacher_id, count(DISTINCT o.id) AS lessons
    FROM lesson_occurrence_participant lop
    JOIN lesson_occurrence o ON o.id = lop.occurrence_id
    JOIN LATERAL (
        SELECT lot.teacher_id
        FROM lesson_occurrence_teacher lot
        WHERE lot.occurrence_id = o.id AND lot.is_active
        UNION
        SELECT o.teacher_id WHERE o.teacher_id IS NOT NULL
    ) t ON TRUE
    JOIN candidates c ON c.id = t.teacher_id
    WHERE o.scheduled_at >= now() - make_interval(days => :window_days)
      AND o.scheduled_at < now()
      -- Отменённое, отклонённое и перенесённое участие занятием не было.
      AND lop.status NOT IN ('cancelled', 'declined', 'rescheduled')
    GROUP BY lop.student_id, t.teacher_id
),
ranked AS (
    SELECT student_id, teacher_id, lessons,
           rank() OVER (PARTITION BY student_id ORDER BY lessons DESC) AS rk
    FROM occ_counts
),
tier2 AS (
    SELECT student_id, min(teacher_id) AS teacher_id, min(lessons) AS lessons
    FROM ranked
    WHERE rk = 1
    GROUP BY student_id
    -- Строгий лидер. Ничья не разрешается монеткой: у этого ученика
    -- действительно два ответственных преподавателя, и выбирать должен человек.
    HAVING count(*) = 1
)
SELECT s.id AS student_id,
       s.full_name AS student_name,
       COALESCE(t1.teacher_id, t2.teacher_id) AS curator_id,
       cu.full_name AS curator_name,
       CASE
           WHEN t1.teacher_id IS NOT NULL THEN :reason_slot
           WHEN t2.teacher_id IS NOT NULL THEN :reason_lessons
       END AS reason,
       t2.lessons AS lessons_in_window,
       -- Что именно помешало, если не вышло: несколько преподавателей —
       -- это одно, полное отсутствие — совсем другое, и оператор поступит
       -- с ними по-разному.
       (SELECT count(*) FROM slot_pairs sp WHERE sp.student_id = s.id) AS slot_teachers,
       (SELECT count(*) FROM occ_counts oc WHERE oc.student_id = s.id) AS lesson_teachers,
       (SELECT string_agg(DISTINCT u2.full_name, ', ')
          FROM occ_counts oc JOIN users u2 ON u2.id = oc.teacher_id
         WHERE oc.student_id = s.id) AS lesson_teacher_names,
       cur.curator_id AS current_curator_id,
       cur_u.full_name AS current_curator_name,
       cur.source AS current_source
FROM students s
LEFT JOIN tier1 t1 ON t1.student_id = s.id
LEFT JOIN tier2 t2 ON t2.student_id = s.id
LEFT JOIN users cu ON cu.id = COALESCE(t1.teacher_id, t2.teacher_id)
LEFT JOIN student_curator cur ON cur.student_id = s.id AND cur.ended_at IS NULL
LEFT JOIN users cur_u ON cur_u.id = cur.curator_id
ORDER BY s.full_name
"""


async def excluded_curator_ids(db: AsyncSession) -> List[int]:
    """Кто из преподавателей в кураторы не идёт.

    Сегодня это оператор: он выходит из кураторства совсем — в этом и смысл
    делегирования. Список выводится из данных, а не из константы: оператор —
    единственный, у кого есть роль `admin`. Зашивать сюда конкретный
    идентификатор нельзя, он разный в dev и в бою.
    """
    rows = (await db.execute(text("""
        SELECT DISTINCT ur.user_id
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE r.name = 'admin'
    """))).all()
    return [int(r[0]) for r in rows]


async def derive_from_schedule(
    db: AsyncSession,
    *,
    excluded: Optional[Sequence[int]] = None,
    window_days: int = SCHEDULE_WINDOW_DAYS,
) -> Dict[str, List[dict]]:
    """Вывести раскладку «ученик → куратор» из расписания. Ничего не пишет.

    Возвращает два списка:

    * ``resolved`` — ученик, предложенный куратор и причина;
    * ``unresolved`` — те, кого правило не закрыло, с указанием почему.

    Второй список не менее важен первого: молча оставить ученика без
    ответственного — это ровно то состояние, из которого задача выводит.
    """
    if excluded is None:
        excluded = await excluded_curator_ids(db)
    sql = _DERIVE_SQL.format(not_staff=active_student_sql("u.id"))  # nosec B608
    rows = (await db.execute(text(sql), {
        "excluded": list(excluded),
        "window_days": window_days,
        "reason_slot": REASON_PERMANENT_SLOT,
        "reason_lessons": REASON_MOST_LESSONS,
    })).mappings().all()

    resolved: List[dict] = []
    unresolved: List[dict] = []
    for r in rows:
        item = dict(r)
        if item["curator_id"] is not None:
            resolved.append(item)
            continue
        item["unresolved_reason"] = (
            UNRESOLVED_AMBIGUOUS
            if (item["slot_teachers"] or 0) > 1 or (item["lesson_teachers"] or 0) > 1
            else UNRESOLVED_NO_TEACHER
        )
        unresolved.append(item)
    logger.info(
        "кураторство: раскладка выведена — закреплено %s, к оператору %s",
        len(resolved), len(unresolved),
    )
    return {"resolved": resolved, "unresolved": unresolved}


async def get_current(db: AsyncSession, student_id: int) -> Optional[dict]:
    """Действующий куратор ученика или None."""
    row = (await db.execute(text("""
        SELECT sc.id, sc.curator_id, u.full_name AS curator_name,
               sc.assigned_at, sc.source, sc.reason
        FROM student_curator sc
        JOIN users u ON u.id = sc.curator_id
        WHERE sc.student_id = :sid AND sc.ended_at IS NULL
    """), {"sid": student_id})).mappings().first()
    return dict(row) if row else None


async def history(db: AsyncSession, student_id: int) -> List[dict]:
    """Все периоды ответственности по ученику, свежие сверху.

    Это и есть ответ на вопрос «кто отвечал за него в сентябре», ради которого
    закрепление хранится отрезками, а не колонкой.
    """
    rows = (await db.execute(text("""
        SELECT sc.id, sc.curator_id, u.full_name AS curator_name,
               sc.assigned_at, sc.ended_at, sc.source, sc.reason,
               sc.ended_reason,
               ab.full_name AS assigned_by_name,
               eb.full_name AS ended_by_name
        FROM student_curator sc
        JOIN users u ON u.id = sc.curator_id
        LEFT JOIN users ab ON ab.id = sc.assigned_by
        LEFT JOIN users eb ON eb.id = sc.ended_by
        WHERE sc.student_id = :sid
        ORDER BY sc.assigned_at DESC, sc.id DESC
    """), {"sid": student_id})).mappings().all()
    return [dict(r) for r in rows]


async def roster_ids(db: AsyncSession, curator_id: int) -> List[int]:
    """Идентификаторы учеников, за которых отвечает этот куратор."""
    rows = (await db.execute(text("""
        SELECT student_id FROM student_curator
        WHERE curator_id = :cid AND ended_at IS NULL
    """), {"cid": curator_id})).all()
    return [int(r[0]) for r in rows]


async def assign(
    db: AsyncSession,
    *,
    student_id: int,
    curator_id: int,
    source: str = SOURCE_MANUAL,
    reason: Optional[str] = None,
    assigned_by: Optional[int] = None,
    ended_reason: Optional[str] = None,
    commit: bool = True,
) -> Dict[str, Any]:
    """Закрепить ученика за куратором; прежнее закрепление закрывается.

    Идемпотентно: если этот куратор уже действующий, ничего не меняется и не
    пишется. Повторное нажатие не должно порождать новую строку истории —
    иначе журнал заполнится «сменами» с тем же человеком по обе стороны.

    :param ended_reason: почему прежний куратор снят. Отдельный смысл от
        `reason`: тот про нового, этот про старого.
    :raises ValueError: ученик и куратор — один человек, или куратор без роли.
    """
    if student_id == curator_id:
        raise ValueError("Куратор не может быть закреплён сам за собой")

    ok = (await db.execute(text("""
        SELECT 1 FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id AND r.name IN ('teacher', 'methodist')
        WHERE ur.user_id = :cid LIMIT 1
    """), {"cid": curator_id})).first()
    if ok is None:
        raise ValueError("Куратором может быть только преподаватель или методист")

    current = await get_current(db, student_id)
    if current and int(current["curator_id"]) == curator_id:
        return {"changed": False, "previous_curator_id": curator_id}

    previous_id = int(current["curator_id"]) if current else None
    if current:
        await db.execute(text("""
            UPDATE student_curator
            SET ended_at = now(), ended_reason = :er, ended_by = :by
            WHERE id = :id AND ended_at IS NULL
        """), {"id": current["id"], "er": ended_reason, "by": assigned_by})

    await db.execute(text("""
        INSERT INTO student_curator
            (student_id, curator_id, source, reason, assigned_by)
        VALUES (:sid, :cid, :src, :reason, :by)
    """), {"sid": student_id, "cid": curator_id, "src": source,
           "reason": reason, "by": assigned_by})

    if commit:
        await db.commit()
    logger.info(
        "кураторство: ученик %s закреплён за %s (%s, было %s)",
        student_id, curator_id, source, previous_id,
    )
    return {"changed": True, "previous_curator_id": previous_id}


async def unassign(
    db: AsyncSession,
    *,
    student_id: int,
    ended_reason: Optional[str] = None,
    ended_by: Optional[int] = None,
    commit: bool = True,
) -> bool:
    """Снять куратора, никого не назначая. Ученик уходит в список оператору."""
    res = await db.execute(text("""
        UPDATE student_curator
        SET ended_at = now(), ended_reason = :er, ended_by = :by
        WHERE student_id = :sid AND ended_at IS NULL
        RETURNING id
    """), {"sid": student_id, "er": ended_reason, "by": ended_by})
    ok = res.first() is not None
    if ok and commit:
        await db.commit()
    return ok


async def apply_derived(
    db: AsyncSession,
    *,
    dry_run: bool = True,
    assigned_by: Optional[int] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Применить выведенную раскладку к базе.

    По умолчанию **только к ученикам без действующего куратора**. Уже
    закреплённых не трогает: закрепление мог поставить человек, разобравший
    неоднозначность руками, и перетереть его правилом значит отменить его
    решение без спроса. `overwrite=True` — осознанная переразметка, и она
    оставляет след в истории с причиной.

    `dry_run=True` (умолчание) считает и показывает, но ничего не пишет.
    """
    derived = await derive_from_schedule(db)
    planned: List[dict] = []
    skipped: List[dict] = []

    for row in derived["resolved"]:
        has_curator = row["current_curator_id"] is not None
        same = has_curator and int(row["current_curator_id"]) == int(row["curator_id"])
        if same:
            continue
        if has_curator and not overwrite:
            skipped.append(row)
            continue
        planned.append(row)

    applied = 0
    if not dry_run:
        for row in planned:
            res = await assign(
                db,
                student_id=int(row["student_id"]),
                curator_id=int(row["curator_id"]),
                source=SOURCE_DERIVED,
                reason=row["reason"],
                assigned_by=assigned_by,
                ended_reason="переразметка по расписанию" if row["current_curator_id"] else None,
                commit=False,
            )
            if res["changed"]:
                applied += 1
        await db.commit()

    logger.info(
        "кураторство: применение раскладки — план %s, применено %s, "
        "пропущено (уже закреплены) %s, без куратора %s, сухой прогон %s",
        len(planned), applied, len(skipped), len(derived["unresolved"]), dry_run,
    )
    return {
        "dry_run": dry_run,
        "planned": planned,
        "applied": applied,
        "skipped_existing": skipped,
        "unresolved": derived["unresolved"],
    }


async def coverage(db: AsyncSession) -> Dict[str, Any]:
    """Сводка раскладки: у кого сколько учеников и сколько осталось без куратора.

    Считается по активным ученикам, а не по всем строкам таблицы: закреплённый
    когда-то и уже ушедший ученик не должен раздувать список живого куратора.
    """
    rows = (await db.execute(text("""
        SELECT u.id AS curator_id, u.full_name AS curator_name,
               count(*) AS students,
               count(*) FILTER (WHERE sc.source = 'manual') AS manual
        FROM student_curator sc
        JOIN users u ON u.id = sc.curator_id
        JOIN users s ON s.id = sc.student_id
        WHERE sc.ended_at IS NULL
          AND s.is_active AND s.merged_into_user_id IS NULL AND s.blocked_at IS NULL
        GROUP BY u.id, u.full_name
        ORDER BY count(*) DESC
    """))).mappings().all()
    without = (await db.execute(text(f"""
        SELECT count(*)
        FROM users u
        JOIN user_roles ur ON ur.user_id = u.id
        JOIN roles r ON r.id = ur.role_id AND r.name = 'student'
        WHERE u.is_active AND u.merged_into_user_id IS NULL AND u.blocked_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM student_curator sc
              WHERE sc.student_id = u.id AND sc.ended_at IS NULL
          )
          AND {active_student_sql("u.id")}
    """))).scalar() or 0  # nosec B608 — фрагмент собран из литералов модуля
    return {"curators": [dict(r) for r in rows], "students_without_curator": int(without)}
