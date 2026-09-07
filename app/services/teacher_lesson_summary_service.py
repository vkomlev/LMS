"""
Сводки преподавателя по occurrence — ПЕРЕД занятием (tsk-022) и ПОСЛЕ занятия
(tsk-410, кнопка «Подвести итоги»). Один и тот же расчёт для обеих сводок
(решение оператора 2026-07-27): SPW встраивает этот же ответ и в
разворачиваемый блок карточки occurrence (до занятия), и в результат кнопки
«Подвести итоги» (после) — формат и источники не должны разойтись между двумя
точками входа фронта.

Переиспользует:
- ``lesson_occurrence_service.get_occurrence_for_teacher`` — ownership-гейт;
- ``manual_progress_service.list_accessible_student_courses``/
  ``get_student_progress`` — дерево курса (заблокированные задания, % прогресса);
- ``help_requests_service.get_help_request_detail`` — текст открытой заявки.

Разведка (2026-07-27) подтвердила, что ни один существующий сервис не считает
"выполнено/с 1 раза/запросил помощь" за окно между занятиями и не отдаёт
"предыдущее occurrence ученика + серию пропусков подряд" — это единственные
новые SQL-агрегации модуля, `get_student_progress` — не оконный снепшот,
`get_activity_feed` поддерживает только курсор `before`.

Важно: в реальном потоке сдачи ответа (``app/api/v1/attempts.py``)
``task_results.count_retry`` НИКОГДА не передаётся явно — всегда дефолт 0,
значение не отражает номер попытки. "С первого раза" здесь считается по
факту (нет более раннего результата по этому заданию у этого ученика), а не
по колонке ``count_retry``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.current_user import CurrentUser
from app.repos.lesson_calendar_repository import LessonOccurrenceParticipantRepository
from app.services import (
    help_requests_service,
    homework_service,
    lesson_occurrence_service,
    manual_progress_service,
)
from app.services.stuck_tasks_service import load_stuck_tasks
from app.utils.task_title import humanize_task_title

#: Сколько последних occurrence ученика поднимаем для поиска предыдущего
#: занятия и подсчёта серии пропусков подряд.
_MISSED_STREAK_LOOKBACK = 12

#: Провенанс синтетических (ручных) результатов — не считаются реальной сдачей.
#: Публичная константа — переиспользуется ``student_dashboard_service`` (tsk-494).
MANUAL_SOURCE = "manual_teacher"

#: Терминальные статусы задания/материала — совпадает с критерием `done` в
#: свёртке процента прогресса (см. `_load_course_progress_and_blocked`).
#: Публичная константа — переиспользуется ``student_dashboard_service`` (tsk-494).
DONE_STATUSES = ("PASSED", "COMPLETED", "SKIPPED")

#: tsk-648: поводы подойти к ученику, в порядке очерёдности. Порядок задан не
#: тяжестью события, а ценой бездействия ИМЕННО СЕГОДНЯ: молчавший на прошлом
#: занятии просидит впустую и это, стоящий на задании сам не сдвинется,
#: пропустивший отстал от темы. Ранг принадлежит поводу, а не ученику: это
#: очерёдность на одно занятие, а не оценка, которая копится.
_ATTENTION_ORDER = (
    "idle_last_lesson",
    "stuck",
    "missed_last_lesson",
    "homework_overdue",
    "help_asked",
)

#: Расписание школы ведётся по Москве — дату занятия в подписи показываем в нём
#: же. Сегодня занятия идут с 10 до 18 по Москве, и в UTC дата та же (проверено
#: по всем 136 занятиям базы), но подпись «пропустил 31.08» под занятием первого
#: сентября — ровно та ошибка, которую потом ищут часами.
_SCHOOL_TZ = ZoneInfo("Europe/Moscow")

#: Окно, в котором ищем «стоит на задании». Совпадает с окном трудностей в
#: плане занятия (tsk-743): окно «между занятиями» на боевых данных короче
#: (медиана 65 часов), но у него разная длина на каждого ученика, а повод
#: должен читаться одинаково у всей группы.
_STUCK_LOOKBACK_DAYS = 7

_participant_repo = LessonOccurrenceParticipantRepository()


async def _load_prev_occurrence_and_streak(
    db: AsyncSession, *, student_id: int, before: datetime,
) -> tuple[Optional[datetime], int, Optional[int], Optional[datetime]]:
    """(конец предыдущего occurrence ученика | None, серия пропусков подряд,
    id предыдущего occurrence | None, его начало | None).

    Один запрос: последние ``_MISSED_STREAK_LOOKBACK`` occurrence ученика
    (ЛЮБОЙ преподаватель) строго ДО текущего, по убыванию времени. Первая
    строка — предыдущее занятие (окно ДЗ начинается с его конца); серия
    пропусков — подряд идущие ``no_show`` от начала списка.
    """
    rows = (
        await db.execute(
            text(
                "SELECT lo.id, lo.scheduled_at, lo.duration_minutes, lop.status "
                "FROM lesson_occurrence_participant lop "
                "JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id "
                "WHERE lop.student_id = :student_id AND lo.scheduled_at < :before "
                "ORDER BY lo.scheduled_at DESC "
                "LIMIT :lookback"
            ),
            {"student_id": student_id, "before": before, "lookback": _MISSED_STREAK_LOOKBACK},
        )
    ).mappings().fetchall()

    if not rows:
        return None, 0, None, None

    prev = rows[0]
    window_from = prev["scheduled_at"] + timedelta(minutes=int(prev["duration_minutes"]))

    streak = 0
    for row in rows:
        if row["status"] == "no_show":
            streak += 1
        else:
            break
    # tsk-648: id предыдущего занятия нужен, чтобы спросить, молчал ли на нём
    # ученик. Отдельным запросом это был бы второй проход по той же истории.
    return window_from, streak, int(prev["id"]), prev["scheduled_at"]


async def _load_last_activity(db: AsyncSession, *, student_id: int) -> Optional[dict[str, Any]]:
    """Последнее реальное (не ручной зачёт) выполненное задание ИЛИ материал —
    что из двух свежее. Та же семантика источников, что
    ``teacher_activity_feed_service._fetch_task_solved``/``_fetch_material_studied``,
    но выборка по ОДНОМУ ученику без ACL (ownership уже проверен на occurrence)."""
    task_row = (
        await db.execute(
            text(
                "SELECT t.task_id, t.submitted_at, tk.external_uid, "
                "       tk.task_content->>'title' AS title_raw, tk.task_content->>'stem' AS stem, "
                "       c.title AS course_title "
                "FROM ( "
                "    SELECT tr.task_id, tr.submitted_at FROM task_results tr "
                "    WHERE tr.user_id = :student_id AND tr.is_correct = true "
                "      AND tr.source_system IS DISTINCT FROM :manual_source "
                "    ORDER BY tr.submitted_at DESC LIMIT 1 "
                ") t "
                "JOIN tasks tk ON tk.id = t.task_id "
                "LEFT JOIN courses c ON c.id = tk.course_id"
            ),
            {"student_id": student_id, "manual_source": MANUAL_SOURCE},
        )
    ).mappings().fetchone()

    material_row = (
        await db.execute(
            text(
                "SELECT smp.material_id, smp.completed_at, m.title, c.title AS course_title "
                "FROM student_material_progress smp "
                "JOIN materials m ON m.id = smp.material_id "
                "LEFT JOIN courses c ON c.id = m.course_id "
                "WHERE smp.student_id = :student_id AND smp.status = 'completed' "
                "  AND smp.completed_at IS NOT NULL "
                "  AND smp.source IS DISTINCT FROM :manual_source "
                "ORDER BY smp.completed_at DESC LIMIT 1"
            ),
            {"student_id": student_id, "manual_source": MANUAL_SOURCE},
        )
    ).mappings().fetchone()

    candidates: list[dict[str, Any]] = []
    if task_row is not None:
        candidates.append({
            "kind": "task",
            "title": humanize_task_title(
                task_row["task_id"], task_row["title_raw"], task_row["stem"], task_row["external_uid"],
            ),
            "course_title": task_row["course_title"],
            "timestamp": task_row["submitted_at"],
        })
    if material_row is not None:
        candidates.append({
            "kind": "material",
            "title": material_row["title"],
            "course_title": material_row["course_title"],
            "timestamp": material_row["completed_at"],
        })
    if not candidates:
        return None
    return max(candidates, key=lambda c: c["timestamp"])


async def load_homework_window(
    db: AsyncSession, *, student_id: int, window_from: Optional[datetime], window_to: datetime,
) -> dict[str, int]:
    """Метрики ДЗ за окно: сколько заданий сдано верно (``tasks_completed``) и
    сколько материалов изучено (``theory_completed``) — РАЗДЕЛЬНО (tsk-473:
    откат объединения от 2026-07-27, оператор попросил вернуть разбивку после
    практической эксплуатации), сколько заданий сдано с первого раза (нет
    более раннего результата по этому заданию у ученика — ``count_retry`` не
    годится, см. docstring модуля; у материалов понятия "с первого раза" нет,
    метрика их не считает), сколько заявок помощи создано."""
    completed_row = (
        await db.execute(
            text(
                "WITH first_success AS ( "
                "    SELECT DISTINCT ON (tr.task_id) tr.task_id, tr.submitted_at "
                "    FROM task_results tr "
                "    JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL "
                "    WHERE tr.user_id = :student_id AND tr.is_correct = true "
                "      AND tr.source_system IS DISTINCT FROM :manual_source "
                "      AND tr.submitted_at >= COALESCE(:window_from, '-infinity'::timestamptz) "
                "      AND tr.submitted_at <= :window_to "
                "    ORDER BY tr.task_id, tr.submitted_at ASC "
                ") "
                "SELECT COUNT(*) AS completed, "
                "       COUNT(*) FILTER ( "
                "           WHERE NOT EXISTS ( "
                "               SELECT 1 FROM task_results tr2 "
                "               WHERE tr2.user_id = :student_id AND tr2.task_id = first_success.task_id "
                "                 AND tr2.submitted_at < first_success.submitted_at "
                "           ) "
                "       ) AS first_try "
                "FROM first_success"
            ),
            {
                "student_id": student_id,
                "manual_source": MANUAL_SOURCE,
                "window_from": window_from,
                "window_to": window_to,
            },
        )
    ).mappings().fetchone()

    help_count = (
        await db.execute(
            text(
                "SELECT COUNT(*) AS cnt FROM help_requests "
                "WHERE student_id = :student_id "
                "  AND created_at >= COALESCE(:window_from, '-infinity'::timestamptz) "
                "  AND created_at <= :window_to"
            ),
            {"student_id": student_id, "window_from": window_from, "window_to": window_to},
        )
    ).scalar()

    # Материалы (видео/чтение — без понятия "верно"/"с первого раза") тоже
    # часть ДЗ между занятиями, не только задания — учтены в `completed`
    # отдельным запросом, т.к. `first_success` CTE выше специфичен для
    # task_results (JOIN attempts, is_correct).
    materials_completed = (
        await db.execute(
            text(
                "SELECT COUNT(*) AS cnt FROM student_material_progress "
                "WHERE student_id = :student_id AND status = 'completed' "
                "  AND completed_at IS NOT NULL "
                "  AND source IS DISTINCT FROM :manual_source "
                "  AND completed_at >= COALESCE(:window_from, '-infinity'::timestamptz) "
                "  AND completed_at <= :window_to"
            ),
            {
                "student_id": student_id,
                "manual_source": MANUAL_SOURCE,
                "window_from": window_from,
                "window_to": window_to,
            },
        )
    ).scalar()

    tasks_completed = int(completed_row["completed"] or 0) if completed_row else 0
    return {
        "tasks_completed": tasks_completed,
        "theory_completed": int(materials_completed or 0),
        "first_try": int(completed_row["first_try"] or 0) if completed_row else 0,
        "help_requested": int(help_count or 0),
    }


async def _load_help_requests(
    db: AsyncSession,
    *,
    teacher_id: int,
    student_id: int,
    window_from: Optional[datetime],
    window_to: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(открытые заявки помощи, заявки закрытые в ЭТОМ ЖЕ окне ДЗ) ЭТОГО
    ученика — с текстом, не только счётчик. Фильтр по студенту — на уровне
    SQL (`list_help_requests(student_id=...)`, tsk-473), НЕ постфильтр по
    общей странице учителя: с `status_filter="all"` общая история учителя
    может быть большой, а сортировка `list_help_requests` — по priority/
    due_at/created_at ASC (старые первыми), не по recency для конкретного
    ученика — без SQL-фильтра `limit` мог бы обрезать список ДО того, как в
    него попадут недавние заявки нужного ученика. При фильтре по одному
    ученику `limit=200` — фактический потолок, столько заявок у одного
    ученика не бывает ("единицы", как и было в исходном комментарии).
    Закрытые ограничены окном ДЗ по `closed_at` (не `updated_at` — тот
    двигается на любую правку, не только закрытие) — иначе список рос бы
    неограниченно всей историей ученика."""
    own, _total = await help_requests_service.list_help_requests(
        db, teacher_id, status_filter="all", student_id=student_id, limit=200, offset=0,
    )
    lower_bound = window_from or datetime.min.replace(tzinfo=timezone.utc)

    async def _to_summary(item: dict[str, Any]) -> Optional[dict[str, Any]]:
        detail, error = await help_requests_service.get_help_request_detail(
            db, item["request_id"], teacher_id,
        )
        if error is not None or detail is None:
            return None
        return {
            "request_id": detail["request_id"],
            "task_id": detail.get("task_id"),
            "task_title": detail.get("task_title"),
            "message": detail.get("message"),
            "created_at": detail["created_at"],
            "resolution_comment": detail.get("resolution_comment"),
            "_closed_at": detail.get("closed_at"),
        }

    open_result: list[dict[str, Any]] = []
    closed_result: list[dict[str, Any]] = []
    for item in own:
        summary = await _to_summary(item)
        if summary is None:
            continue
        closed_at = summary.pop("_closed_at", None)
        if item["status"] == "open":
            open_result.append(summary)
        elif (
            item["status"] == "closed"
            and closed_at is not None
            and lower_bound <= closed_at <= window_to
        ):
            closed_result.append(summary)

    return open_result, closed_result


async def _load_course_progress_and_blocked(
    db: AsyncSession, *, current_user: CurrentUser, student_id: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """% прогресса по каждому доступному курсу ученика + текущая позиция
    (раздел курса + конкретный элемент, tsk-473) + список заблокированных
    лимитом попыток заданий (текущий снепшот, не оконный) — всё берётся из
    уже посчитанного `get_student_progress`, без новой агрегации.

    Текущая позиция — первый НЕзавершённый элемент (`DONE_STATUSES`) в
    учебном порядке `items` (материалы/задания, узлы `course` пропускаются).
    Раздел — заголовок его непосредственного `parent_course_id`; если элемент
    лежит прямо в корне запрошенного курса (раздела как такового нет) или
    курс пройден целиком — `None`."""
    courses = await manual_progress_service.list_accessible_student_courses(
        db, current_user, student_id,
    )
    progress: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for course in courses:
        course_id = course["course_id"]
        data = await manual_progress_service.get_student_progress(
            db, student_id=student_id, course_id=course_id,
        )
        items = data["items"]
        countable = [i for i in items if i["item_type"] != "course"]
        done = sum(1 for i in countable if i["status"] in DONE_STATUSES)
        total = len(countable)
        percent = round(done / total * 100) if total else 0

        section_titles = {i["item_id"]: i["title"] for i in items if i["item_type"] == "course"}
        current_section_title: Optional[str] = None
        current_item_title: Optional[str] = None
        for i in countable:
            if i["status"] in DONE_STATUSES:
                continue
            current_item_title = i["title"]
            parent_id = i.get("parent_course_id")
            if parent_id is not None and parent_id != course_id:
                current_section_title = section_titles.get(parent_id)
            break

        progress.append({
            "course_id": course_id,
            "title": course["title"],
            "percent_complete": percent,
            "current_section_title": current_section_title,
            "current_item_title": current_item_title,
        })
        for i in countable:
            if i["item_type"] == "task" and i["status"] == "BLOCKED_LIMIT":
                blocked.append({
                    "task_id": i["item_id"], "title": i["title"], "course_title": course["title"],
                })
    return progress, blocked


def _assigned_fields(status: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Поля выданного ДЗ для сводки; всё `None`, если ничего не задавали."""
    if status is None:
        return {
            "assigned_total": None,
            "assigned_done": None,
            "assigned_due_at": None,
            "assigned_is_overdue": None,
        }
    return {
        "assigned_total": status["assigned_total"],
        "assigned_done": status["assigned_done"],
        "assigned_due_at": status["due_at"],
        "assigned_is_overdue": status["is_overdue"],
    }


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение: 1 попытка, 2 попытки, 5 попыток."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


async def _load_idle_on_lessons(
    db: AsyncSession, *, pairs: list[tuple[int, int]],
) -> dict[int, dict[str, Any]]:
    """Простой (tsk-591) на ПРЕДЫДУЩЕМ занятии каждого ученика.

    Ключ ответа — student_id, потому что предыдущее занятие у каждого своё
    (в группе состав участников от урока к уроку разный).

    Почему прошлое занятие, а не любое: «сидел и молчал» устаревает вместе с
    занятием. Эпизод трёхнедельной давности не говорит, к кому подойти
    сегодня, — а список, который копит такое, превращается в тот самый вечный
    рейтинг, которого задача просила избежать.
    """
    if not pairs:
        return {}
    occurrence_ids = sorted({occ_id for occ_id, _ in pairs})
    student_ids = sorted({student_id for _, student_id in pairs})
    rows = (
        await db.execute(
            text(
                "SELECT occurrence_id, student_id, count(*) AS episodes, "
                "       max(kind) AS kind, "
                "       sum(EXTRACT(EPOCH FROM ("
                "           COALESCE(resolved_at, detected_at) - silent_since"
                "       ))) AS silent_seconds "
                "FROM lesson_idle_episode "
                "WHERE occurrence_id = ANY(:occ_ids) AND student_id = ANY(:student_ids) "
                "GROUP BY 1, 2"
            ),
            {"occ_ids": occurrence_ids, "student_ids": student_ids},
        )
    ).mappings().fetchall()

    # Оба массива в запросе независимы, поэтому в ответ попадают и лишние
    # сочетания «ученик × чужое занятие» — оставляем только запрошенные пары.
    wanted = set(pairs)
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        key = (int(row["occurrence_id"]), int(row["student_id"]))
        if key not in wanted:
            continue
        result[int(row["student_id"])] = {
            "episodes": int(row["episodes"]),
            "minutes": max(1, int(float(row["silent_seconds"] or 0) // 60)),
            "kind": row["kind"],
        }
    return result


async def _load_absence_followups(
    db: AsyncSession, *, pairs: list[tuple[int, int]],
) -> set[int]:
    """Ученики, с которыми про пропуск ПРОШЛОГО занятия уже поговорили.

    Отметка ставится в плане занятия (tsk-743, `lesson_absence_followup`), и
    здесь она снимает повод: разговор состоялся, звать преподавателя к тому же
    человеку с тем же поводом второй раз — способ научить его не читать список.
    """
    if not pairs:
        return set()
    rows = (
        await db.execute(
            text(
                "SELECT student_id, occurrence_id FROM lesson_absence_followup "
                "WHERE occurrence_id = ANY(:occ_ids) AND student_id = ANY(:student_ids)"
            ),
            {
                "occ_ids": sorted({occ_id for occ_id, _ in pairs}),
                "student_ids": sorted({student_id for _, student_id in pairs}),
            },
        )
    ).mappings().fetchall()
    wanted = set(pairs)
    return {
        int(row["student_id"])
        for row in rows
        if (int(row["occurrence_id"]), int(row["student_id"])) in wanted
    }


def _build_attention(
    *,
    idle: Optional[dict[str, Any]],
    stuck_items: list[dict[str, Any]],
    missed_streak: int,
    prev_started_at: Optional[datetime],
    absence_asked: bool,
    homework: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Повод подойти к ученику сегодня — один, самый срочный (tsk-648).

    Поводов у человека может быть несколько, но в строке нужен один: список
    из пяти пометок на каждого из двенадцати учеников преподаватель во время
    урока не прочитает. Остальное он увидит в личной сводке по клику.

    Возвращает ``None``, если поводов нет вовсе, — и это нормальное состояние:
    на боевых данных за две недели повод был у 38 % участий.

    ``absence_asked`` снимает повод «пропустил»: разговор про этот пропуск уже
    отмечен в плане занятия (tsk-743). Пропуск — самый частый повод (46 из 88
    случаев за две недели), и без такого снятия он вытеснил бы остальные.
    """
    if idle:
        detail = f"молчал {idle['minutes']} мин на прошлом занятии"
        if idle["kind"] == "away":
            detail = f"{detail} (кабинет был закрыт)"
        return {"rank": 1, "reason": "idle_last_lesson", "detail": detail, "task_id": None}

    if stuck_items:
        first = stuck_items[0]
        # «Ошибся N раз», а не «N неверных попыток»: рядом с «попытки кончились»
        # второе давало «3 неверные попытки, попытки кончились» — увидел на
        # живом проде, читается как заикание.
        attempts = first["wrong_attempts"]
        parts = []
        if attempts:
            parts.append(
                f"ошибся {attempts} " + _plural(attempts, "раз", "раза", "раз")
            )
        if first.get("by_limit"):
            parts.append("попытки кончились")
        detail = f"стоит на задании: {first['task_title']} — {', '.join(parts)}"
        return {
            "rank": 2,
            "reason": "stuck",
            "detail": detail,
            "task_id": first["task_id"],
        }

    if missed_streak > 0 and not absence_asked:
        if missed_streak == 1 and prev_started_at is not None:
            local_day = prev_started_at.astimezone(_SCHOOL_TZ).strftime("%d.%m")
            detail = f"пропустил прошлое занятие {local_day}"
        else:
            detail = (
                f"пропустил подряд: {missed_streak} "
                + _plural(missed_streak, "занятие", "занятия", "занятий")
            )
        return {
            "rank": 3,
            "reason": "missed_last_lesson",
            "detail": detail,
            "task_id": None,
        }

    total = homework.get("assigned_total")
    done = homework.get("assigned_done") or 0
    # `None` — ученику не задавали; это НЕ то же самое, что «задали ноль», и
    # спрашивать за несделанное здесь нельзя (та же развилка, что в tsk-741).
    if total and done < total and homework.get("assigned_is_overdue"):
        return {
            "rank": 4,
            "reason": "homework_overdue",
            "detail": f"домашняя работа {done} из {total}, срок прошёл",
            "task_id": None,
        }

    asked = int(homework.get("help_requested") or 0)
    if asked:
        return {
            "rank": 5,
            "reason": "help_asked",
            "detail": (
                f"просил помощи между занятиями: {asked} "
                + _plural(asked, "раз", "раза", "раз")
            ),
            "task_id": None,
        }
    return None


def _attention_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Порядок участников: сперва поводы по рангу, внутри ранга — по имени.

    Ученики без повода идут следом в том же алфавитном порядке. Имя как
    вторичный ключ, а не «вес события»: сравнивать 13 минут молчания с 4
    неверными попытками нечем, а неустойчивый порядок на экране, который
    обновляется раз в минуту, читать нельзя.
    """
    attention = row.get("attention")
    rank = attention["rank"] if attention else len(_ATTENTION_ORDER) + 1
    return (rank, (row.get("full_name") or "").lower(), row["student_id"])


async def get_occurrence_summary(
    db: AsyncSession,
    *,
    occurrence_id: int,
    teacher_id: int,
    current_user: CurrentUser,
    no_show_threshold_minutes: int,
    include_progress: bool = True,
    student_id: Optional[int] = None,
) -> dict[str, Any]:
    """Сводка по всем участникам occurrence — общий источник для сводки ДО
    занятия (встраивается в карточку) и кнопки «Подвести итоги» ПОСЛЕ.

    tsk-665: два необязательных сужения. Панель преподавателя тянет сводку
    РАЗ В МИНУТУ всё занятие, а самая дорогая её часть — прогресс по курсу и
    заблокированные задания (обход дерева курса и состояния всех заданий на
    каждого участника). В списке эти данные видны одним значком, подробности
    преподаватель открывает по клику на одного ученика — значит считать их на
    всех и каждую минуту незачем.

    Args:
        include_progress: считать ли прогресс по курсу и заблокированные
            задания. `False` — в ответе `course_progress` и `blocked_tasks`
            равны `None`. Именно `None`, а не пустой список: пустой список
            означает «посчитали, ничего нет», и спутать эти два смысла —
            ровно тот класс ошибки, когда поле молча обнуляется.
        student_id: вернуть только этого участника (для боковой панели).
            Не найден среди участников занятия — пустой список участников,
            а не 404: занятие существует, состав мог измениться.
    """
    occurrence = await lesson_occurrence_service.get_occurrence_for_teacher(
        db, occurrence_id=occurrence_id, teacher_id=teacher_id,
    )
    participants = await _participant_repo.list_for_occurrence(db, occurrence_id)
    if student_id is not None:
        participants = [p for p in participants if p.student_id == student_id]

    now_utc = datetime.now(timezone.utc)
    threshold = timedelta(minutes=no_show_threshold_minutes)

    student_ids = [p.student_id for p in participants]
    profiles: dict[int, dict[str, Any]] = {}
    if student_ids:
        rows = (
            await db.execute(
                # tsk-588: timezone — сводка занятия и есть тот экран, где
                # преподаватель договаривается с учеником о времени.
                text("SELECT id, full_name, tg_id, timezone FROM users WHERE id = ANY(:ids)"),
                {"ids": student_ids},
            )
        ).mappings().fetchall()
        profiles = {int(r["id"]): dict(r) for r in rows}

    # tsk-741: что ЗАДАНО, рядом с тем, что сделано. Прежние счётчики окна
    # (`load_homework_window`) считают свободную работу ученика между
    # занятиями — по ним не ответить на вопрос «сделал ли он то, что задали».
    # Одним запросом на всю группу: состав выдачи здесь не нужен, нужны три
    # числа на человека.
    # Состояние ДЗ берётся НА ВРЕМЯ ЭТОГО ЗАНЯТИЯ, а не «сейчас» (дефект,
    # замеченный оператором 02.09). Иначе у прошедшего занятия показывалась
    # выдача, сделанная по его итогам, — то есть домашняя работа к СЛЕДУЮЩЕМУ
    # занятию, которую на этом никто не проверял. Просрочку по-прежнему
    # считаем от настоящего времени: срок либо прошёл, либо нет.
    assigned = await homework_service.status_for_students(
        db,
        student_ids=student_ids,
        now=now_utc,
        as_of=occurrence.scheduled_at,
    )

    # tsk-648: «стоит на задании» — один групповой запрос на всю группу, до
    # цикла по участникам. Внутри цикла это был бы N+1 на панели, которая
    # тикает раз в минуту всё занятие.
    stuck_by_student = await load_stuck_tasks(
        db,
        student_ids=student_ids,
        since=now_utc - timedelta(days=_STUCK_LOOKBACK_DAYS),
        until=now_utc,
        include_limit_blocked=True,
    )

    result_participants: list[dict[str, Any]] = []
    prev_lesson_pairs: list[tuple[int, int]] = []
    for p in participants:
        profile = profiles.get(p.student_id, {})
        is_overdue = p.status == "scheduled" and (occurrence.scheduled_at + threshold) < now_utc

        (
            window_from,
            missed_streak,
            prev_occurrence_id,
            prev_started_at,
        ) = await _load_prev_occurrence_and_streak(
            db, student_id=p.student_id, before=occurrence.scheduled_at,
        )
        if prev_occurrence_id is not None:
            prev_lesson_pairs.append((prev_occurrence_id, p.student_id))
        last_activity = await _load_last_activity(db, student_id=p.student_id)
        days_since = None
        if last_activity is not None:
            days_since = max(0, (now_utc - last_activity["timestamp"]).days)
        homework = await load_homework_window(
            db, student_id=p.student_id, window_from=window_from, window_to=now_utc,
        )
        # Ученику могли ещё ничего не задавать — тогда полей плана нет вовсе
        # (`None`), и это не то же самое, что «задали ноль»: пустых выдач не
        # бывает, а спутать «не задавали» с «не сделал» на этом экране дороже
        # всего — преподаватель спросит с человека за то, чего ему не давали.
        homework.update(_assigned_fields(assigned.get(p.student_id)))
        open_help, closed_help = await _load_help_requests(
            db,
            teacher_id=teacher_id,
            student_id=p.student_id,
            window_from=window_from,
            window_to=now_utc,
        )
        course_progress: Optional[list[dict[str, Any]]] = None
        blocked_tasks: Optional[list[dict[str, Any]]] = None
        if include_progress:
            course_progress, blocked_tasks = await _load_course_progress_and_blocked(
                db, current_user=current_user, student_id=p.student_id,
            )

        result_participants.append({
            "student_id": p.student_id,
            "full_name": profile.get("full_name"),
            "tg_id": profile.get("tg_id"),
            "timezone": profile.get("timezone"),
            "status": p.status,
            "is_overdue": is_overdue,
            "last_activity": last_activity,
            "days_since_last_activity": days_since,
            "window_from": window_from,
            "homework": homework,
            "blocked_tasks": blocked_tasks,
            "open_help_requests": open_help,
            "closed_help_requests": closed_help,
            "missed_streak": missed_streak,
            "course_progress": course_progress,
            "prev_started_at": prev_started_at,
        })

    # tsk-648: очерёдность внимания. Считается после цикла — простой на
    # прошлом занятии берётся одним запросом на всю группу.
    idle_by_student = await _load_idle_on_lessons(db, pairs=prev_lesson_pairs)
    absence_asked = await _load_absence_followups(db, pairs=prev_lesson_pairs)
    for row in result_participants:
        row["attention"] = _build_attention(
            idle=idle_by_student.get(row["student_id"]),
            stuck_items=stuck_by_student.get(row["student_id"], []),
            missed_streak=row["missed_streak"],
            prev_started_at=row.pop("prev_started_at"),
            absence_asked=row["student_id"] in absence_asked,
            homework=row["homework"],
        )

    # Сортировка на сервере, а не на клиенте: порядок — это и есть ответ на
    # вопрос задачи, и он не должен разъезжаться между списком занятия и
    # любым другим потребителем ответа.
    result_participants.sort(key=_attention_sort_key)

    return {
        "occurrence_id": occurrence.id,
        "is_ad_hoc": occurrence.slot_id is None,
        "window_to": now_utc,
        "participants": result_participants,
    }


__all__ = ["get_occurrence_summary"]
