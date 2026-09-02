"""Активность кураторов: что куратор СДЕЛАЛ, а не как учатся его ученики (tsk-742).

Это различие — главное в задаче и главный способ её провалить. Сводка «средний
балл по группе Светланы» отвечает на другой вопрос: ученик может учиться плохо
при безупречном кураторе и хорошо — при отсутствующем. Меряем работу взрослого:
посмотрел, ответил, разобрал сигнал, проверил работу.

**Что считается касанием.** Пять источников, перечисленных в `_TOUCH_SOURCES`.
Список ЕДИНСТВЕННЫЙ: и экран куратора («когда я последний раз смотрел этого
ученика»), и недельный отчёт («скольких не тронул») читают его отсюда. Две копии
одного правила разъезжаются молча, и тогда экран показывает «всё в порядке», а
отчёт — «неделю не заходил».

**Почему проверка работ берётся из `task_results.checked_by`, а не из журнала.**
Событие `teacher.review.graded` не несёт ученика — только `result_id`. Считать по
нему «касания ученика» пришлось бы через дополнительный переход, а колонка
`checked_by` даёт то же самое прямо и надёжнее.

**Главная цифра отчёта — охват, а не сумма действий.** Куратор, проверивший
сорок работ у двух учеников, оставил без внимания остальных восемнадцать. Сумма
это спрячет, охват — нет.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings_store

logger = logging.getLogger(__name__)

#: Куратор открыл карточку своего ученика. Единственное касание, у которого не
#: было следа в системе вовсе: «посмотрел» — это обязанность из устава § 3.1, а
#: подтвердить её было нечем. Пишется из карточки ученика в кабинете.
CURATOR_STUDENT_VIEWED = "curator.student.viewed"

#: Срок разбора сигнала по умолчанию — дублирует реестр настроек на случай, если
#: настройки не прочитались. Молчащий отчёт хуже неточного.
SIGNAL_RESPONSE_DAYS = 7
URGENT_RESPONSE_HOURS = 24
REVIEW_RESPONSE_DAYS = 3

#: Поводы сигналов, у которых срок реакции сутки, а не неделя: ученика может не
#: оказаться уже на следующем занятии.
URGENT_SIGNAL_REASONS = ("dropout_risk",)

# Все касания куратора одним объединением. Каждая ветвь отдаёт одинаковую тройку
# (кто, кого, когда) плюс вид — вид нужен отчёту, чтобы «посмотрел» не
# засчитывалось за «ответил».
#
# Параметры одни на все ветви: :curator_ids (список), :since, :until.
_TOUCH_SOURCES = """
-- Действия из журнала: отметка явки, выдача попыток, правка прогресса,
-- выдача ДЗ и сам просмотр карточки. Все они кладут ученика в details.
SELECT a.user_id AS curator_id,
       (a.details->>'student_id')::int AS student_id,
       a.ts AS at,
       CASE WHEN a.event_type = :viewed_event THEN 'viewed' ELSE 'acted' END AS kind
FROM audit_event a
WHERE a.user_id = ANY(:curator_ids)
  AND a.ts >= :since AND a.ts < :until
  AND a.details ? 'student_id'
  AND (a.details->>'student_id') ~ '^[0-9]+$'

UNION ALL

-- Проверил работу.
SELECT tr.checked_by, tr.user_id, tr.checked_at, 'reviewed'
FROM task_results tr
WHERE tr.checked_by = ANY(:curator_ids)
  AND tr.checked_at >= :since AND tr.checked_at < :until

UNION ALL

-- Написал ученику.
SELECT m.sender_id, m.recipient_id, m.sent_at, 'messaged'
FROM messages m
WHERE m.sender_id = ANY(:curator_ids)
  AND m.sent_at >= :since AND m.sent_at < :until

UNION ALL

-- Ответил на заявку помощи.
SELECT rep.teacher_id, hr.student_id, rep.created_at, 'helped'
FROM help_request_replies rep
JOIN help_requests hr ON hr.id = rep.request_id
WHERE rep.teacher_id = ANY(:curator_ids)
  AND rep.created_at >= :since AND rep.created_at < :until

UNION ALL

-- Разобрал сигнал: принял, передал методисту или отклонил.
SELECT s.teacher_id, s.student_id, s.acknowledged_at, 'signal_handled'
FROM learning_gap_signal s
WHERE s.teacher_id = ANY(:curator_ids)
  AND s.student_id IS NOT NULL
  AND s.acknowledged_at >= :since AND s.acknowledged_at < :until
"""


def touches_sql() -> str:
    """SQL-объединение всех касаний куратора.

    Функция, а не константа-строка: правило одно, и вызывать его обязаны все
    места, которые считают активность. Скопированное условие живёт своей жизнью.
    """
    return _TOUCH_SOURCES


def _setting_int(key: str, fallback: int) -> int:
    """Порог из настроек школы; не прочитался — берём запасной."""
    try:
        return settings_store.get_int(key)
    except Exception:
        logger.warning("кураторство: настройка %s не прочиталась, беру %s", key, fallback)
        return fallback


def signal_response_days() -> int:
    """Сколько дней куратору даётся на разбор обычного сигнала."""
    return _setting_int("curator_signal_response_days", SIGNAL_RESPONSE_DAYS)


def urgent_response_hours() -> int:
    """Сколько часов даётся на срочный сигнал (риск ухода)."""
    return _setting_int("curator_urgent_response_hours", URGENT_RESPONSE_HOURS)


def review_response_days() -> int:
    """Сколько дней работа ученика может лежать на проверке."""
    return _setting_int("curator_review_response_days", REVIEW_RESPONSE_DAYS)


async def last_touches(
    db: AsyncSession,
    *,
    curator_id: int,
    since: datetime,
) -> Dict[int, datetime]:
    """Когда куратор в последний раз касался каждого своего ученика.

    Возвращает `{student_id: момент}`. Ученика в словаре нет — значит за окно
    касаний не было; это и есть то, что показывает экран красным.
    """
    rows = (await db.execute(text(f"""
        SELECT student_id, max(at) AS last_at
        FROM ({touches_sql()}) t
        WHERE student_id IS NOT NULL
        GROUP BY student_id
    """), {  # nosec B608 — touches_sql() возвращает литерал модуля
        "curator_ids": [curator_id],
        "since": since,
        "until": datetime.now(timezone.utc),
        "viewed_event": CURATOR_STUDENT_VIEWED,
    })).all()
    return {int(r[0]): r[1] for r in rows}


async def record_view(
    db: AsyncSession, *, curator_id: int, student_id: int, commit: bool = True
) -> None:
    """Записать, что куратор открыл карточку ученика.

    Своя запись, а не побочный эффект чтения где-то ещё: обязанность «знать, как
    идут дела у каждого» подтверждается именно этим, и подтверждать её нечем,
    если след не оставить. Пишется в общий журнал `audit_event` — он
    append-only, и подделать «я смотрел» задним числом там нельзя.

    Ошибку наружу не выпускаем: не записавшийся просмотр не повод ломать показ
    карточки ученику куратора.
    """
    from app.services import audit_service

    try:
        await audit_service.log_event(
            db,
            CURATOR_STUDENT_VIEWED,
            user_id=curator_id,
            details={"student_id": int(student_id)},
        )
        if commit:
            await db.commit()
    except Exception:
        logger.exception(
            "кураторство: не записали просмотр карточки %s куратором %s",
            student_id, curator_id,
        )


def week_bounds(week_start: Optional[date] = None) -> tuple[datetime, datetime]:
    """Границы отчётной недели: понедельник 00:00 — следующий понедельник 00:00.

    Время московское: школа живёт по Москве, и «неделя» в отчёте оператора
    обязана совпадать с той неделей, которую он прожил. Без приведения границы
    уезжают на три часа и захватывают вечер воскресенья.
    """
    tz = timezone(timedelta(hours=3))
    if week_start is None:
        today = datetime.now(tz).date()
        week_start = today - timedelta(days=today.weekday() + 7)
    start = datetime.combine(week_start, datetime.min.time(), tzinfo=tz)
    return start, start + timedelta(days=7)


_REPORT_SQL = """
WITH curators AS (
    SELECT DISTINCT sc.curator_id AS id
    FROM student_curator sc
    WHERE sc.ended_at IS NULL
),
roster AS (
    SELECT sc.curator_id, sc.student_id
    FROM student_curator sc
    JOIN users s ON s.id = sc.student_id
    WHERE sc.ended_at IS NULL
      AND s.is_active AND s.merged_into_user_id IS NULL AND s.blocked_at IS NULL
      -- Выпускники, демо и служебные учётки в счёт кураторства не идут: они
      -- попали бы в «не тронул ни разу», и куратор был бы прав, что не трогал.
      AND {active_student}
),
touch AS (
    SELECT curator_id, student_id, kind
    FROM ({touches}) t
    WHERE student_id IS NOT NULL
),
-- Касания засчитываются ТОЛЬКО по своим ученикам. Куратор может проверить
-- работу чужого ученика — это полезно школе, но к его кураторству отношения
-- не имеет, и в охват идти не должно.
own_touch AS (
    SELECT t.* FROM touch t
    JOIN roster r ON r.curator_id = t.curator_id AND r.student_id = t.student_id
),
-- Сигналы по своим ученикам, поднятые за неделю.
sig AS (
    SELECT r.curator_id,
           count(*) AS raised,
           count(*) FILTER (WHERE s.acknowledged_at IS NOT NULL) AS handled,
           count(*) FILTER (
               WHERE s.acknowledged_at IS NULL AND s.status IN ('new', 'acknowledged')
           ) AS still_open
    FROM learning_gap_signal s
    JOIN roster r ON r.student_id = s.student_id
    WHERE s.created_at >= :since AND s.created_at < :until
    GROUP BY r.curator_id
),
-- Сигналы, просроченные НА МОМЕНТ ОТЧЁТА, независимо от того, когда подняты.
-- Считаются отдельно от недельных: сигнал, поднятый в июле и до сих пор не
-- разобранный, — самое важное в отчёте и в недельное окно не попадёт никогда.
overdue AS (
    SELECT r.curator_id,
           count(*) AS overdue_signals,
           max(EXTRACT(DAY FROM now() - s.created_at))::int AS oldest_days
    FROM learning_gap_signal s
    JOIN roster r ON r.student_id = s.student_id
    WHERE s.status IN ('new', 'acknowledged')
      AND s.acknowledged_at IS NULL
      AND s.created_at < now() - CASE
          WHEN s.reason = ANY(:urgent_reasons)
              THEN make_interval(hours => :urgent_hours)
          ELSE make_interval(days => :signal_days)
      END
    GROUP BY r.curator_id
),
-- Работы своих учеников, лежащие на проверке дольше срока.
--
-- Предикат обязательной ручной проверки — ТОТ ЖЕ, что у очереди преподавателя
-- (`mandatory_review_sql`), и это не формальность. Без него условие
-- вырождается в «любая непроверенная строка `task_results`», а таких строк —
-- вся история авто-проверенных сдач: живой прогон на проде 02.09 дал 2201
-- «просроченную работу» у куратора, у которого очередь честно пуста. Число,
-- которое человек читает первым, обязано означать то, что написано рядом.
stale_reviews AS (
    SELECT r.curator_id, count(*) AS stale_reviews
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id
    JOIN roster r ON r.student_id = tr.user_id
    WHERE tr.checked_at IS NULL
      AND tr.submitted_at < now() - make_interval(days => :review_days)
      AND {mandatory}
    GROUP BY r.curator_id
)
SELECT c.id AS curator_id,
       u.full_name AS curator_name,
       (SELECT count(*) FROM roster r WHERE r.curator_id = c.id) AS students,
       (SELECT count(DISTINCT ot.student_id) FROM own_touch ot WHERE ot.curator_id = c.id)
           AS students_touched,
       (SELECT count(DISTINCT ot.student_id) FROM own_touch ot
         WHERE ot.curator_id = c.id AND ot.kind <> 'viewed') AS students_acted_on,
       (SELECT count(*) FROM own_touch ot WHERE ot.curator_id = c.id) AS touches_total,
       (SELECT count(*) FROM own_touch ot
         WHERE ot.curator_id = c.id AND ot.kind = 'reviewed') AS reviews,
       (SELECT count(*) FROM own_touch ot
         WHERE ot.curator_id = c.id AND ot.kind = 'helped') AS help_replies,
       (SELECT count(*) FROM own_touch ot
         WHERE ot.curator_id = c.id AND ot.kind = 'messaged') AS messages,
       COALESCE(sig.raised, 0) AS signals_raised,
       COALESCE(sig.handled, 0) AS signals_handled,
       COALESCE(overdue.overdue_signals, 0) AS signals_overdue,
       overdue.oldest_days AS oldest_open_signal_days,
       COALESCE(stale_reviews.stale_reviews, 0) AS reviews_overdue
FROM curators c
JOIN users u ON u.id = c.id
LEFT JOIN sig ON sig.curator_id = c.id
LEFT JOIN overdue ON overdue.curator_id = c.id
LEFT JOIN stale_reviews ON stale_reviews.curator_id = c.id
ORDER BY u.full_name
"""


async def weekly_report(
    db: AsyncSession, *, week_start: Optional[date] = None
) -> Dict[str, Any]:
    """Отчёт по активности кураторов за неделю.

    Считает работу взрослых, а не успеваемость детей. По каждому куратору:
    сколько у него учеников, скольких он за неделю тронул хоть как-то, скольких
    не просто посмотрел, а сделал что-то, сколько сигналов пришло и сколько он
    разобрал, что просрочено прямо сейчас.

    `week_start` — понедельник отчётной недели; по умолчанию прошлая полная
    неделя (отчёт приходит в понедельник о том, что было).
    """
    from app.services.curator_service import active_student_sql
    from app.services.teacher_queue_service import mandatory_review_sql

    since, until = week_bounds(week_start)
    sql = _REPORT_SQL.format(
        touches=touches_sql(), mandatory=mandatory_review_sql("t", "tr"),
        active_student=active_student_sql("s.id"),
    )
    curator_ids = [
        int(r[0]) for r in (await db.execute(text(
            "SELECT DISTINCT curator_id FROM student_curator WHERE ended_at IS NULL"
        ))).all()
    ]
    rows = (await db.execute(text(sql), {  # nosec B608 — подставляется литерал модуля
        "curator_ids": curator_ids,
        "since": since,
        "until": until,
        "viewed_event": CURATOR_STUDENT_VIEWED,
        "signal_days": signal_response_days(),
        "urgent_hours": urgent_response_hours(),
        "urgent_reasons": list(URGENT_SIGNAL_REASONS),
        "review_days": review_response_days(),
    })).mappings().all()

    curators: List[dict] = []
    for r in rows:
        item = dict(r)
        students = int(item["students"] or 0)
        touched = int(item["students_touched"] or 0)
        item["students_untouched"] = students - touched
        # Доля, а не только числа: «13 из 20» и «13 из 40» читаются одинаково
        # быстро только если рядом стоит процент.
        item["coverage"] = round(touched / students, 2) if students else None
        curators.append(item)

    # Ученики без куратора — часть отчёта, а не отдельная справка. Пока они
    # есть, делегирование неполное: за них по-прежнему отвечает оператор.
    # То же определение «ученик, а не сотрудник», что у раскладки: иначе
    # отчёт и предпросмотр называют разные числа ничьих, и оба выглядят
    # правдой.
    from app.services.curator_service import coverage, unassigned_students

    orphans = (await coverage(db))["students_without_curator"]
    # Поимённо, а не числом (решение оператора 02.09). «Без куратора: 24»
    # ничего не говорит о том, что с ними делать: у одного двое ведущих, у
    # другого занятия ведёт сам владелец школы, третьему просто не завели
    # расписание. Список с причиной превращает наблюдение в список дел.
    orphan_list = await unassigned_students(db)

    return {
        "week_start": since.date().isoformat(),
        "week_end": (until.date()).isoformat(),
        "curators": curators,
        "students_without_curator": int(orphans),
        "students_without_curator_list": orphan_list,
        "thresholds": {
            "signal_response_days": signal_response_days(),
            "urgent_response_hours": urgent_response_hours(),
            "review_response_days": review_response_days(),
        },
    }


#: Сколько недель подряд куратор должен не тронуть НИКОГО, прежде чем сигнал
#: уйдёт ему самому. Запасное значение; фактическое — из настроек школы.
INACTIVITY_WEEKS = 2


def inactivity_weeks() -> int:
    """Через сколько недель молчания куратор получает сигнал о себе."""
    return _setting_int("curator_inactivity_weeks", INACTIVITY_WEEKS)


async def curators_without_coverage(
    db: AsyncSession, *, weeks: Optional[int] = None
) -> List[dict]:
    """Кураторы, не тронувшие НИ ОДНОГО своего ученика N недель подряд.

    Решение оператора 2026-09-02: отчёт не заканчивается наблюдением. На второй
    неделе полного молчания сигнал уходит самому куратору — тем же контуром,
    что и сигналы об учениках. Владелец школы в этом не участвует: разговор
    начинается без него, в этом и смысл делегирования.

    **Порог намеренно строгий — ноль касаний, а не «мало».** «Мало» у куратора
    с тридцатью учениками и у куратора с двенадцатью — разные числа, и любой
    процент здесь был бы выдуман. Ноль означает одно и то же для всех: человек
    за неделю не сделал по своей группе ничего. Ослаблять порог можно замером
    на живых неделях, а не ощущением.

    Недели считаются подряд и заканчиваются последней полной: куратор,
    молчавший в июле и работавший вчера, сигнала не получит.
    """
    if weeks is None:
        weeks = inactivity_weeks()
    silent: Optional[set] = None
    names: Dict[int, Optional[str]] = {}
    for back in range(weeks):
        start = (week_bounds()[0] - timedelta(weeks=back)).date()
        report = await weekly_report(db, week_start=start)
        week_silent = set()
        for c in report["curators"]:
            if int(c["students"] or 0) > 0 and int(c["students_touched"] or 0) == 0:
                week_silent.add(int(c["curator_id"]))
                names[int(c["curator_id"])] = c["curator_name"]
        silent = week_silent if silent is None else (silent & week_silent)
        if not silent:
            break
    return [
        {"curator_id": cid, "curator_name": names.get(cid), "weeks": weeks}
        for cid in sorted(silent or ())
    ]


def render_report_text(report: Dict[str, Any]) -> str:
    """Отчёт словами — то, что оператор читает в уведомлении.

    Порядок строк не случаен: сперва то, чего не сделали (непокрытые ученики,
    просроченные сигналы), потом объём работы. Отчёт нужен, чтобы заметить
    провал, а не чтобы порадоваться числам.
    """
    lines = [f"Кураторство, неделя {report['week_start']} — {report['week_end']}"]
    if not report["curators"]:
        # Раньше здесь был выход — и отчёт молчал о том, за кого никто не
        # отвечает, ровно в тот момент, когда без куратора ВСЕ. Список ничьих
        # ниже нужен тем сильнее, чем меньше кураторов.
        lines.append("Кураторов нет: раскладка ещё не применена.")

    for c in report["curators"]:
        untouched = c["students_untouched"]
        head = f"{c['curator_name']}: {c['students']} учеников"
        if untouched:
            head += f", без внимания {untouched}"
        else:
            head += ", без внимания никого"
        lines.append(head)

        parts = []
        if c["signals_raised"]:
            parts.append(f"сигналов {c['signals_raised']}, разобрано {c['signals_handled']}")
        if c["signals_overdue"]:
            days = c["oldest_open_signal_days"]
            parts.append(
                f"просрочено сигналов {c['signals_overdue']}"
                + (f" (старшему {days} дн.)" if days is not None else "")
            )
        if c["reviews_overdue"]:
            parts.append(f"работ на проверке дольше срока {c['reviews_overdue']}")
        work = []
        if c["reviews"]:
            work.append(f"проверено {c['reviews']}")
        if c["help_replies"]:
            work.append(f"ответов на заявки {c['help_replies']}")
        if c["messages"]:
            work.append(f"сообщений {c['messages']}")
        if work:
            parts.append("сделано: " + ", ".join(work))
        if not parts:
            parts.append("действий за неделю нет")
        lines.append("  " + "; ".join(parts))

    orphans = report.get("students_without_curator_list") or []
    if report["students_without_curator"]:
        lines.append("")
        lines.append(
            f"Без куратора: {report['students_without_curator']} — за них отвечаете вы."
        )
        # Сгруппировано по причине: с каждой группой делают РАЗНОЕ, и вперемешку
        # список читается как один длинный упрёк, а не как список дел.
        by_reason: Dict[str, List[str]] = {}
        for o in orphans:
            by_reason.setdefault(str(o["reason_label"]), []).append(
                str(o["student_name"] or f"ученик {o['student_id']}")
            )
        for label, names in by_reason.items():
            lines.append(f"  {label}: " + ", ".join(sorted(names)))
    return "\n".join(lines)
