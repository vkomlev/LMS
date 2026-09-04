"""tsk-743: план занятия — что преподаватель делает В НАЧАЛЕ, ПО ХОДУ и В КОНЦЕ.

Контур «до и после занятия» уже закрыт (сводка перед занятием tsk-022, итоги по
каждому ученику tsk-410/473). Не хватало ровно «во время»: преподаватель ведёт
урок, а показатели, по которым надо действовать прямо сейчас, лежат по разным
экранам и уведомлениям.

──────────────────────────────────────────────────────────────────────────────
ГЛАВНОЕ ОГРАНИЧЕНИЕ: НЕ ПЕРЕГРУЗИТЬ

За экраном живой человек, ведущий урок. Поэтому три правила, и они важнее
полноты:

1. **Шаг без данных не показывается вовсе.** Пустого «не забудьте спросить про
   ДЗ» здесь нет: если у всех всё сделано, шага нет.
2. **Видна только текущая фаза.** Начало, ход, конец не показываются разом.
3. **Имена, а не призывы.** «Спроси про ДЗ» бесполезно, «Петя 1 из 4,
   просрочено» — работает. Каждый шаг несёт список конкретных учеников.

Что это НЕ делает: не шлёт уведомлений. Замер боевой базы 04.09 — за 30 дней
преподавателям ушло 480 уведомлений, и как раз те, что про ход занятия, не
читают: `student_idle` — 102 штуки, прочитано 17%; `lesson_missed` — 105,
прочитано 21%. Ещё один поток сообщений в урок добавил бы шума туда, где он уже
не работает; поэтому решение оператора (04.09) — только экран.

──────────────────────────────────────────────────────────────────────────────
ФАЗЫ

Границы берутся из тех же настроек школы, что и кнопка «Подвести итоги»
(tsk-741), — новых окон не заводим:

* `before`  — занятие ещё не скоро; шагов НЕТ. Считать домашнюю работу и
  пропуски для занятия, до которого две недели, незачем: смотреть их всё равно
  будут перед уроком, а данные к тому времени изменятся;
* `start`   — от `lesson_summary_after_start_minutes` ДО начала и столько же
  после: преподаватель успевает посмотреть, пока рассаживаются;
* `during`  — до `wrapup_from` (конец − `lesson_wrapup_before_end_minutes`, но
  не раньше начала + `lesson_summary_after_start_minutes`);
* `wrapup`  — до конца занятия + `_AFTER_END_MINUTES`;
* `after`   — панель больше не нужна, шагов нет.

──────────────────────────────────────────────────────────────────────────────
ИСТОЧНИКИ — ТОЛЬКО УЖЕ СОБИРАЕМЫЕ ДАННЫЕ

Ничего нового не считается: домашняя работа — `homework_service` (tsk-741),
простои — `lesson_idle_episode` (tsk-591), заявки помощи — `help_requests`,
пропуски — статусы участия, успехи — `user_achievements`. Новая здесь одна
запись: отметка «про пропуск уже спросили» (`lesson_absence_followup`), без
которой список пропустивших не схлопывается.

Запросы — групповые, по всей группе занятия сразу: панель тикает раз в минуту,
и N+1 на каждого участника здесь недопустим.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings_store
from app.repos.lesson_calendar_repository import LessonOccurrenceParticipantRepository
from app.services import homework_service, lesson_occurrence_service
from app.utils.task_title import humanize_task_title

logger = logging.getLogger(__name__)

_participant_repo = LessonOccurrenceParticipantRepository()

#: Сколько панель живёт после конца занятия: итоги подводят не ровно в звонок.
_AFTER_END_MINUTES = 30

#: Окно поиска неразобранных пропусков. Спрашивать про пропуск полуторамесячной
#: давности бессмысленно — человек не вспомнит, а строка будет висеть.
_ABSENCE_LOOKBACK_DAYS = 30

#: Окно свежих успехов. Неделя — типичный промежуток между занятиями.
_WINS_LOOKBACK_DAYS = 7

#: Сколько неверных попыток по ОДНОМУ заданию считаем «застрял».
_STUCK_WRONG_ATTEMPTS = 3

#: Окно поиска трудностей к началу занятия (что накопилось между занятиями).
_DIFFICULTY_LOOKBACK_DAYS = 7

#: Сколько учеников показываем в одном шаге. Больше — это уже не напоминание,
#: а таблица, которую на уроке не читают.
_MAX_STUDENTS_PER_STEP = 5

#: Успехи — самый «мягкий» шаг, ему хватает трёх имён: назвать всех, кто хоть
#: что-то сделал, значит обесценить похвалу.
_MAX_WINS = 3

#: Ручные (проставленные преподавателем) результаты не считаются работой ученика
#: — та же константа, что в сводке занятия.
_MANUAL_SOURCE = "manual_teacher"

#: Причины пропуска в одно нажатие. Свободный текст — отдельным полем.
ABSENCE_REASONS = ("illness", "forgot", "busy", "no_answer", "other")


def _fmt_day(moment: datetime) -> str:
    """Дата занятия для строки вида «пропустил 01.09»."""
    return moment.strftime("%d.%m")


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение числительного: 1 ученик, 2 ученика, 5 учеников."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


async def _load_names(db: AsyncSession, student_ids: list[int]) -> dict[int, Optional[str]]:
    """Имена участников одним запросом."""
    if not student_ids:
        return {}
    rows = (
        await db.execute(
            text("SELECT id, full_name FROM users WHERE id = ANY(:ids)"),
            {"ids": student_ids},
        )
    ).mappings().fetchall()
    return {int(r["id"]): r["full_name"] for r in rows}


async def _load_unasked_absences(
    db: AsyncSession, *, student_ids: list[int], before: datetime,
) -> dict[int, list[dict[str, Any]]]:
    """Пропуски участников, про которые ещё не спрашивали.

    «Не отметился» — это и есть ``no_show``: отказ (``declined``), перенос
    (``rescheduled``) и перерыв (``on_break``) — отдельные статусы, ученик в них
    как раз отметился. Отсеиваем то, про что разговор уже был
    (``lesson_absence_followup``), иначе строка висела бы вечно.
    """
    if not student_ids:
        return {}
    rows = (
        await db.execute(
            text(
                "SELECT p.student_id, lo.id AS occurrence_id, lo.scheduled_at "
                "FROM lesson_occurrence_participant p "
                "JOIN lesson_occurrence lo ON lo.id = p.occurrence_id "
                "LEFT JOIN lesson_absence_followup f "
                "  ON f.student_id = p.student_id AND f.occurrence_id = lo.id "
                "WHERE p.student_id = ANY(:ids) AND p.status = 'no_show' "
                "  AND lo.scheduled_at < :before AND lo.scheduled_at >= :since "
                "  AND f.id IS NULL "
                "ORDER BY lo.scheduled_at DESC"
            ),
            {
                "ids": student_ids,
                "before": before,
                "since": before - timedelta(days=_ABSENCE_LOOKBACK_DAYS),
            },
        )
    ).mappings().fetchall()

    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(int(row["student_id"]), []).append(
            {"occurrence_id": int(row["occurrence_id"]), "scheduled_at": row["scheduled_at"]}
        )
    return result


async def _load_recent_achievements(
    db: AsyncSession, *, student_ids: list[int], now: datetime,
) -> dict[int, str]:
    """Самое свежее достижение каждого ученика за неделю — повод похвалить.

    Второй системы достижений заводить не стали (решение по tsk-741): то, за что
    ученику уже выдали значок, и есть его успех между занятиями.
    """
    if not student_ids:
        return {}
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT ON (ua.user_id) ua.user_id, a.name, ua.earned_at "
                "FROM user_achievements ua "
                "JOIN achievements a ON a.id = ua.achievement_id "
                "WHERE ua.user_id = ANY(:ids) AND ua.earned_at >= :since "
                "ORDER BY ua.user_id, ua.earned_at DESC"
            ),
            {"ids": student_ids, "since": now - timedelta(days=_WINS_LOOKBACK_DAYS)},
        )
    ).mappings().fetchall()
    return {int(r["user_id"]): r["name"] for r in rows}


async def _load_open_help(
    db: AsyncSession, *, student_ids: list[int], since: Optional[datetime] = None,
) -> dict[int, list[dict[str, Any]]]:
    """Открытые заявки помощи участников (при `since` — только созданные позже).

    Заголовок задания собирается тем же ``humanize_task_title``, что в сводке и
    ленте: у заданий обычно пустой `title`, и без этого в строке оказался бы
    технический слаг.
    """
    if not student_ids:
        return {}
    rows = (
        await db.execute(
            text(
                "SELECT h.id, h.student_id, h.task_id, h.created_at, "
                "       tk.external_uid, tk.task_content->>'title' AS title_raw, "
                "       tk.task_content->>'stem' AS stem "
                "FROM help_requests h "
                "LEFT JOIN tasks tk ON tk.id = h.task_id "
                "WHERE h.student_id = ANY(:ids) AND h.status = 'open' "
                "  AND h.created_at >= COALESCE(:since, '-infinity'::timestamptz) "
                "ORDER BY h.created_at DESC"
            ),
            {"ids": student_ids, "since": since},
        )
    ).mappings().fetchall()

    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        title = (
            humanize_task_title(
                int(row["task_id"]), row["title_raw"], row["stem"], row["external_uid"],
            )
            if row["task_id"] is not None
            else None
        )
        result.setdefault(int(row["student_id"]), []).append(
            {"request_id": int(row["id"]), "task_id": row["task_id"], "task_title": title}
        )
    return result


async def _load_stuck_tasks(
    db: AsyncSession, *, student_ids: list[int], since: datetime, until: datetime,
) -> dict[int, list[dict[str, Any]]]:
    """Задания, где ученик за окно ошибся ``_STUCK_WRONG_ATTEMPTS`` раз и так и
    не решил.

    Это «трудности по заданиям» из постановки, и они не теоретические: замер
    боевой базы за 30 дней — 47 таких пар «ученик + задание» на 26 занятиях из
    56, то есть почти на каждом втором уроке кто-то буксует молча.

    Ручные зачёты преподавателя (``source_system = manual_teacher``) не считаем:
    это не попытка ученика.
    """
    if not student_ids:
        return {}
    rows = (
        await db.execute(
            text(
                "WITH wrong AS ( "
                "    SELECT tr.user_id, tr.task_id, count(*) AS wrong_cnt, "
                "           max(tr.submitted_at) AS last_at "
                "    FROM task_results tr "
                "    WHERE tr.user_id = ANY(:ids) AND tr.is_correct = false "
                "      AND tr.source_system IS DISTINCT FROM :manual_source "
                "      AND tr.submitted_at >= :since AND tr.submitted_at <= :until "
                "    GROUP BY 1, 2 "
                "    HAVING count(*) >= :min_wrong "
                ") "
                "SELECT w.user_id, w.task_id, w.wrong_cnt, w.last_at, "
                "       tk.external_uid, tk.task_content->>'title' AS title_raw, "
                "       tk.task_content->>'stem' AS stem "
                "FROM wrong w "
                "JOIN tasks tk ON tk.id = w.task_id "
                "WHERE NOT EXISTS ( "
                "    SELECT 1 FROM task_results ok "
                "    WHERE ok.user_id = w.user_id AND ok.task_id = w.task_id "
                "      AND ok.is_correct = true "
                ") "
                "ORDER BY w.last_at DESC"
            ),
            {
                "ids": student_ids,
                "since": since,
                "until": until,
                "min_wrong": _STUCK_WRONG_ATTEMPTS,
                "manual_source": _MANUAL_SOURCE,
            },
        )
    ).mappings().fetchall()

    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(int(row["user_id"]), []).append(
            {
                "task_id": int(row["task_id"]),
                "task_title": humanize_task_title(
                    int(row["task_id"]), row["title_raw"], row["stem"], row["external_uid"],
                ),
                "wrong_attempts": int(row["wrong_cnt"]),
            }
        )
    return result


async def _load_open_idle(
    db: AsyncSession, *, occurrence_id: int, now: datetime,
) -> dict[int, dict[str, Any]]:
    """Незакрытые эпизоды простоя на ЭТОМ занятии (tsk-591).

    Сигнал уже считается фоновым тиком и уже уходит уведомлением, которое
    почти не читают (17% за 30 дней). Здесь он попадает туда, куда
    преподаватель и так смотрит во время урока.
    """
    rows = (
        await db.execute(
            text(
                "SELECT student_id, kind, silent_since FROM lesson_idle_episode "
                "WHERE occurrence_id = :oid AND resolved_at IS NULL"
            ),
            {"oid": occurrence_id},
        )
    ).mappings().fetchall()
    return {
        int(r["student_id"]): {
            "kind": r["kind"],
            "minutes": max(1, int((now - r["silent_since"]).total_seconds() // 60)),
        }
        for r in rows
    }


def _student_entry(
    student_id: int,
    names: dict[int, Optional[str]],
    detail: str,
    **extra: Any,
) -> dict[str, Any]:
    """Строка ученика в шаге плана — единый вид для всех шагов."""
    entry: dict[str, Any] = {
        "student_id": student_id,
        "full_name": names.get(student_id),
        "detail": detail,
        "missed_occurrence_ids": [],
        "task_id": None,
    }
    entry.update(extra)
    return entry


def _step(
    key: str, phase: str, title: str, action: str, students: list[dict[str, Any]],
    *, limit: int = _MAX_STUDENTS_PER_STEP,
) -> Optional[dict[str, Any]]:
    """Шаг плана; `None`, если показывать нечего — пустой шаг не рисуется."""
    if not students:
        return None
    hidden = max(0, len(students) - limit)
    return {
        "key": key,
        "phase": phase,
        "title": title,
        "action": action,
        "students": students[:limit],
        "more_count": hidden,
    }


def compute_phase(
    now: datetime,
    *,
    scheduled_at: datetime,
    ends_at: datetime,
    wrapup_from: datetime,
    lead_minutes: int,
) -> tuple[str, Optional[datetime]]:
    """(фаза занятия, когда она сменится).

    Вынесено отдельной функцией: границы фаз — единственное, что здесь можно
    посчитать неверно тихо, и на них удобно смотреть тестом.
    """
    start_visible = scheduled_at - timedelta(minutes=lead_minutes)
    start_until = scheduled_at + timedelta(minutes=lead_minutes)
    panel_until = ends_at + timedelta(minutes=_AFTER_END_MINUTES)

    if now < start_visible:
        return "before", start_visible
    if now < start_until:
        return "start", start_until
    if now < wrapup_from:
        return "during", wrapup_from
    if now <= panel_until:
        return "wrapup", panel_until
    return "after", None


async def get_lesson_plan(
    db: AsyncSession,
    *,
    occurrence_id: int,
    teacher_id: int,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """План занятия для преподавателя: шаги ТЕКУЩЕЙ фазы с данными.

    Args:
        occurrence_id: занятие.
        teacher_id: преподаватель (ownership проверяет
            ``lesson_occurrence_service.get_occurrence_for_teacher``).
        now: подменяемое «сейчас» — фазы иначе нечем проверить в тестах.

    Returns:
        Словарь с границами занятия, фазой и списком шагов. Шаг без данных не
        возвращается вовсе, шаги чужих фаз не считаются.
    """
    occurrence = await lesson_occurrence_service.get_occurrence_for_teacher(
        db, occurrence_id=occurrence_id, teacher_id=teacher_id,
    )
    moment = now or datetime.now(timezone.utc)
    ends_at = occurrence.scheduled_at + timedelta(minutes=int(occurrence.duration_minutes))

    lead_minutes = settings_store.get_int("lesson_summary_after_start_minutes")
    wrapup_minutes = settings_store.get_int("lesson_wrapup_before_end_minutes")
    # Та же формула, что у кнопки «Подвести итоги» (tsk-741): нижняя граница
    # держит короткое занятие от «итогов с первой минуты».
    wrapup_from = max(
        ends_at - timedelta(minutes=wrapup_minutes),
        occurrence.scheduled_at + timedelta(minutes=lead_minutes),
    )

    phase, phase_until = compute_phase(
        moment,
        scheduled_at=occurrence.scheduled_at,
        ends_at=ends_at,
        wrapup_from=wrapup_from,
        lead_minutes=lead_minutes,
    )

    participants = await _participant_repo.list_for_occurrence(db, occurrence_id)
    # Отказавшихся и перенёсших на этом занятии нет — спрашивать их не о чем,
    # и в списках они были бы шумом.
    active = [p for p in participants if p.status not in ("declined", "rescheduled")]
    student_ids = [p.student_id for p in active]

    result: dict[str, Any] = {
        "occurrence_id": occurrence.id,
        "scheduled_at": occurrence.scheduled_at,
        "ends_at": ends_at,
        "wrapup_from": wrapup_from,
        "phase": phase,
        "phase_until": phase_until,
        "steps": [],
    }
    # Ни «занятие ещё не скоро», ни «давно кончилось» шагов не несут — и не
    # считаются: каждый шаг это запросы к базе, а панель тикает раз в минуту.
    if phase in ("before", "after") or not student_ids:
        return result

    names = await _load_names(db, student_ids)
    steps: list[Optional[dict[str, Any]]] = []

    if phase == "start":
        steps.extend(
            await _start_steps(
                db,
                student_ids=student_ids,
                names=names,
                scheduled_at=occurrence.scheduled_at,
                now=moment,
            )
        )
    elif phase == "during":
        steps.extend(
            await _during_steps(
                db,
                occurrence_id=occurrence.id,
                student_ids=student_ids,
                names=names,
                scheduled_at=occurrence.scheduled_at,
                now=moment,
            )
        )
    else:  # wrapup
        steps.extend(
            await _wrapup_steps(db, participants=active, names=names, now=moment)
        )

    result["steps"] = [s for s in steps if s is not None]
    return result


async def _start_steps(
    db: AsyncSession,
    *,
    student_ids: list[int],
    names: dict[int, Optional[str]],
    scheduled_at: datetime,
    now: datetime,
) -> list[Optional[dict[str, Any]]]:
    """Начало урока: домашняя работа, пропуски, успехи, трудности."""
    # Состояние ДЗ — НА ВРЕМЯ ЭТОГО занятия (tsk-741, дефект 02.09): «что
    # человек должен был принести сегодня», а не выдача, созданная по итогам.
    assigned = await homework_service.status_for_students(
        db, student_ids=student_ids, now=now, as_of=scheduled_at,
    )
    absences = await _load_unasked_absences(db, student_ids=student_ids, before=scheduled_at)
    achievements = await _load_recent_achievements(db, student_ids=student_ids, now=now)
    # Окно у заявок то же, что у трудностей: заявка, открытая месяц назад,
    # сделала бы этот шаг постоянным фоном, который перестают читать.
    open_help = await _load_open_help(
        db,
        student_ids=student_ids,
        since=now - timedelta(days=_DIFFICULTY_LOOKBACK_DAYS),
    )
    stuck = await _load_stuck_tasks(
        db,
        student_ids=student_ids,
        since=now - timedelta(days=_DIFFICULTY_LOOKBACK_DAYS),
        until=now,
    )

    # 1. Домашняя работа
    not_done: list[dict[str, Any]] = []
    done_fully: list[int] = []
    for student_id in student_ids:
        status = assigned.get(student_id)
        if status is None:
            continue
        total = int(status["assigned_total"] or 0)
        done = int(status["assigned_done"] or 0)
        if total and done >= total:
            done_fully.append(student_id)
            continue
        suffix = ", просрочено" if status.get("is_overdue") else ""
        not_done.append(
            _student_entry(student_id, names, f"ДЗ {done} из {total}{suffix}")
        )
    homework_step = _step(
        "homework",
        "start",
        f"Домашняя работа не сделана: {len(not_done)}",
        "Спросите, что не получилось",
        not_done,
    )

    # 2. Пропуски без объяснения
    absence_students: list[dict[str, Any]] = []
    for student_id, missed in absences.items():
        last = missed[0]["scheduled_at"]
        count = len(missed)
        absence_students.append(
            _student_entry(
                student_id,
                names,
                f"пропусков: {count}, последний — {_fmt_day(last)}",
                missed_occurrence_ids=[m["occurrence_id"] for m in missed],
            )
        )
    absence_step = _step(
        "absences",
        "start",
        f"Не отметились и пропустили: {len(absence_students)}",
        "Спросите причину и отметьте разговор",
        absence_students,
    )

    # 3. Успехи
    wins: list[dict[str, Any]] = []
    for student_id in done_fully:
        wins.append(_student_entry(student_id, names, "домашняя работа сделана полностью"))
    for student_id, title in achievements.items():
        if any(w["student_id"] == student_id for w in wins):
            continue
        wins.append(_student_entry(student_id, names, f"новое достижение: {title}"))
    wins_step = _step(
        "wins", "start", f"Есть кого похвалить: {len(wins)}", "Назовите вслух", wins,
        limit=_MAX_WINS,
    )

    # 4. Трудности, накопившиеся между занятиями
    difficulties: list[dict[str, Any]] = []
    for student_id in student_ids:
        help_items = open_help.get(student_id, [])
        stuck_items = stuck.get(student_id, [])
        if not help_items and not stuck_items:
            continue
        parts: list[str] = []
        task_id: Optional[int] = None
        if help_items:
            parts.append(
                f"{len(help_items)} "
                + _plural(len(help_items), "вопрос", "вопроса", "вопросов")
                + " без ответа"
            )
            task_id = help_items[0]["task_id"]
        if stuck_items:
            first = stuck_items[0]
            parts.append(
                f"буксует: {first['task_title']} — "
                f"{first['wrong_attempts']} "
                + _plural(
                    first["wrong_attempts"],
                    "неверная попытка", "неверные попытки", "неверных попыток",
                )
            )
            task_id = task_id or first["task_id"]
        difficulties.append(
            _student_entry(student_id, names, "; ".join(parts), task_id=task_id)
        )
    difficulties_step = _step(
        "difficulties",
        "start",
        f"Трудности с прошлого занятия: {len(difficulties)}",
        "Разберите на уроке",
        difficulties,
    )

    return [homework_step, absence_step, wins_step, difficulties_step]


async def _during_steps(
    db: AsyncSession,
    *,
    occurrence_id: int,
    student_ids: list[int],
    names: dict[int, Optional[str]],
    scheduled_at: datetime,
    now: datetime,
) -> list[Optional[dict[str, Any]]]:
    """Ход урока: кто выпал из работы и кто буксует прямо сейчас."""
    idle = await _load_open_idle(db, occurrence_id=occurrence_id, now=now)
    stuck = await _load_stuck_tasks(
        db, student_ids=student_ids, since=scheduled_at, until=now,
    )
    help_now = await _load_open_help(db, student_ids=student_ids, since=scheduled_at)

    idle_students = [
        _student_entry(
            student_id,
            names,
            ("не в кабинете" if data["kind"] == "away" else "молчит")
            + f" {data['minutes']} мин",
        )
        for student_id, data in idle.items()
        if student_id in student_ids
    ]
    idle_step = _step(
        "idle",
        "during",
        f"Выпали из работы: {len(idle_students)}",
        "Окликните",
        idle_students,
    )

    stuck_students: list[dict[str, Any]] = []
    for student_id in student_ids:
        stuck_items = stuck.get(student_id, [])
        help_items = help_now.get(student_id, [])
        if not stuck_items and not help_items:
            continue
        if stuck_items:
            first = stuck_items[0]
            detail = (
                f"{first['task_title']} — {first['wrong_attempts']} "
                + _plural(
                    first["wrong_attempts"],
                    "неверная попытка", "неверные попытки", "неверных попыток",
                )
            )
            task_id = first["task_id"]
        else:
            first_help = help_items[0]
            detail = "нужна помощь" + (
                f": {first_help['task_title']}" if first_help["task_title"] else ""
            )
            task_id = first_help["task_id"]
        stuck_students.append(_student_entry(student_id, names, detail, task_id=task_id))
    stuck_step = _step(
        "stuck",
        "during",
        f"Буксуют на задании: {len(stuck_students)}",
        "Подойдите или подскажите",
        stuck_students,
    )

    return [idle_step, stuck_step]


async def _wrapup_steps(
    db: AsyncSession,
    *,
    participants: list[Any],
    names: dict[int, Optional[str]],
    now: datetime,
) -> list[Optional[dict[str, Any]]]:
    """Конец урока: итоги по каждому, домашняя работа, тема сейчас и дальше.

    Итоги по ученику уже есть (tsk-473, кнопка «Подвести итоги»), поэтому здесь
    не второй экран, а перечень тех, с кем разговор ещё предстоит: шаги ведут в
    ту же сводку, а не показывают её копию.
    """
    present = [
        p.student_id for p in participants if p.status in ("confirmed", "completed")
    ]
    if not present:
        # Никого не было — итоги подводить не с кем, и домашнюю работу тоже.
        return []

    # Домашняя работа НА СЕЙЧАС: что уже действует к следующему занятию.
    assigned_now = await homework_service.status_for_students(
        db, student_ids=present, now=now,
    )
    auto_issue = settings_store.get_bool("homework_auto_issue_enabled")

    # Разбор работы и разговор про тему — один шаг, а не два: живой просмотр
    # 04.09 показал два соседних списка с ОДНИМИ И ТЕМИ ЖЕ тремя именами, и это
    # ровно та перегрузка, против которой задача. Оба разговора идут по одному
    # ученику подряд, и открываются они в одной и той же сводке (tsk-473).
    review_step = _step(
        "review",
        "wrapup",
        f"Обсудите каждого: {len(present)} "
        + _plural(len(present), "ученик", "ученика", "учеников"),
        "Работа на уроке, тема сейчас и что дальше — в итогах ученика",
        [_student_entry(sid, names, "на занятии") for sid in present],
    )

    without_homework = [sid for sid in present if sid not in assigned_now]
    homework_step = None
    if without_homework and not auto_issue:
        # Автовыдача выключена — задавать некому, кроме преподавателя.
        homework_step = _step(
            "homework_next",
            "wrapup",
            f"Без домашней работы: {len(without_homework)}",
            "Задайте из карточки ученика",
            [_student_entry(sid, names, "выдачи нет") for sid in without_homework],
        )

    return [review_step, homework_step]


async def mark_absence_asked(
    db: AsyncSession,
    *,
    student_id: int,
    occurrence_ids: list[int],
    current_occurrence_id: int,
    teacher_id: Optional[int],
    reason: Optional[str],
    note: Optional[str],
) -> int:
    """Отметить, что про эти пропуски у ученика спросили. Возвращает число новых
    отметок.

    Отмечаются ВСЕ показанные пропуски ученика разом: разговор один («почему
    тебя не было?»), а не по одному на каждое занятие — иначе строка не
    исчезнет и преподаватель будет спрашивать снова.

    Повторное нажатие (панель открыта и на телефоне, и в браузере) не создаёт
    вторую запись — держит уникальный индекс, а не проверка в коде.

    **Что здесь проверяется и почему.** Занятия и ученик приходят из тела
    запроса, то есть подбираются снаружи. Владение текущим занятием роутер уже
    проверил, но без двух проверок ниже отметку можно было бы поставить на
    ЧУЖОГО ученика и ЧУЖОЙ пропуск — и он молча исчез бы из плана у другого
    преподавателя:

    1. ученик — участник текущего занятия (того, на котором идёт разговор);
    2. каждое названное занятие — действительно его пропуск (``no_show``).

    Лишние id не роняют запрос, а отбрасываются: список пропусков мог
    измениться между открытием панели и нажатием.
    """
    if not occurrence_ids:
        return 0
    if reason is not None and reason not in ABSENCE_REASONS:
        raise ValueError(f"Неизвестная причина пропуска: {reason}")

    is_participant = (
        await db.execute(
            text(
                "SELECT 1 FROM lesson_occurrence_participant "
                "WHERE occurrence_id = :oid AND student_id = :student_id"
            ),
            {"oid": current_occurrence_id, "student_id": student_id},
        )
    ).scalar()
    if not is_participant:
        raise ValueError("Ученик не участвует в этом занятии")

    allowed = {
        int(row[0])
        for row in (
            await db.execute(
                text(
                    "SELECT occurrence_id FROM lesson_occurrence_participant "
                    "WHERE student_id = :student_id AND status = 'no_show' "
                    "  AND occurrence_id = ANY(:ids)"
                ),
                {"student_id": student_id, "ids": occurrence_ids},
            )
        ).fetchall()
    }
    if not allowed:
        return 0

    inserted = 0
    for occurrence_id in sorted(allowed):
        row = (
            await db.execute(
                text(
                    "INSERT INTO lesson_absence_followup "
                    "  (student_id, occurrence_id, asked_by, asked_at, reason, note) "
                    "VALUES (:student_id, :occurrence_id, :asked_by, now(), :reason, :note) "
                    "ON CONFLICT (student_id, occurrence_id) DO NOTHING "
                    "RETURNING id"
                ),
                {
                    "student_id": student_id,
                    "occurrence_id": occurrence_id,
                    "asked_by": teacher_id,
                    "reason": reason,
                    "note": note,
                },
            )
        ).fetchone()
        if row is not None:
            inserted += 1
    await db.commit()
    logger.info(
        "tsk-743: отмечен разговор о пропусках — ученик %s, занятий %s, новых отметок %s",
        student_id, len(occurrence_ids), inserted,
    )
    return inserted


__all__ = ["get_lesson_plan", "mark_absence_asked", "compute_phase", "ABSENCE_REASONS"]
