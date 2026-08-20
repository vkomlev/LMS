"""
Периодный дашборд ученика (tsk-494) — основа для будущего кабинета родителя
(tsk-478, read-only) и текущих teacher/methodist/admin. План:
docs/specs/2026-08-01-plan-tsk494-student-dashboard-api.md.

Принцип минимизации данных для менее доверенного зрителя (см. прецедент
tsk-460, reviews/2026-07-29-tsk460-solution-rules-leak.md): контракт НЕ
включает текст заявок помощи (``message``/``resolution_comment``) и
``solution_rules`` — только агрегаты/счётчики, заложено на уровне схемы
(``app/schemas/student_dashboard.py``), не постфильтром.

Переиспользует:
- ``teacher_lesson_summary_service.load_homework_window`` (tsk-473) — уже
  принимает произвольные ``window_from``/``window_to``, обобщать SQL не
  понадобилось.
- ``manual_progress_service.get_student_progress``/
  ``list_accessible_student_courses``/``ensure_can_edit_progress`` — дерево
  курса и ACL, без изменений.

Новое (разведка tsk-494 подтвердила: аналогов в коде нет):
- Агрегация "в часы занятий" — та же форма CTE, что ``load_homework_window``,
  с EXISTS против ``lesson_occurrence``/``lesson_occurrence_participant``.
  "Между занятиями" = ИТОГ (``load_homework_window`` за весь период) МИНУС
  "в часы занятий", арифметика в Python по каждой метрике — гарантия
  отсутствия задвоения по построению (обосновано в плане), не третий
  независимый SQL-запрос.
- Прогноз окончания курса — простая эвристика (темп за последние
  ``Settings.student_forecast_pace_weeks`` недель по ЭТОМУ курсу →
  экстраполяция на оставшиеся элементы дерева).
- Посещение за период (пропуски/незакрытые) — ``lesson_occurrence_participant.status``
  за окно occurrence. tsk-503 (решение оператора 2026-08-04): "закрыт" пропуск
  означает ФАКТИЧЕСКУЮ явку, не просто выбор новой даты. ``status='rescheduled'``
  сам по себе НЕ значит "закрыто" — нужно пройти цепочку
  ``rescheduled_to_occurrence_id`` до финального статуса участника (если
  очередной перенос снова ``no_show``/``declined``/``rescheduled`` — пропуск
  всё ещё открыт, идём по цепочке дальше). Прежняя версия останавливалась на
  первом ``rescheduled`` — это и было дефектом tsk-503.

Цветовая подсветка относительно сверстников (tsk-504, решения оператора
2026-08-06):
- Метрики: темп прохождения (per-course ``percent_complete``), доля пропусков
  (``attendance.missed``/``attendance.planned``), активность между занятиями
  (``between_lessons.tasks_completed + theory_completed``).
  ``period_total``/``in_class_hours`` НЕ подсвечиваются (решение оператора).
- Когорта — ДРУГИЕ активные ученики (``user_courses.is_active``, сам ученик
  исключён — оператор: "опираться можно на других учеников"). Порог
  ``>= Settings.student_dashboard_cohort_min_size`` (дефолт 5) — иначе
  ``insufficient_data``, а не молчаливое отсутствие подсветки.
- Для ``pace_level`` (percent_complete) когорта — активные ученики ТОГО ЖЕ
  ``course_id`` (метрика per-course, однозначно). Для ``missed_level``/
  ``between_lessons_activity_level`` (метрики общие для ученика, не per-course
  — расписание вообще не привязано к курсу, см. cross-project
  ``project_lms_pricing_model.md``) когорта — ОБЪЕДИНЕНИЕ активных учеников
  ВСЕХ корневых курсов, на которые записан ребёнок (прод: 33 из 47 активных
  учеников на 2026-08-06 записаны на ≥2 корневых курса одновременно —
  единственный курс как якорь потерял бы фактуру половине семей). Так же
  отражает исходную формулировку оператора ("опираться можно на других
  учеников ТЕХ ЖЕ курсОВ", множественное число), уточнённую позже до
  единственного числа уже применительно к per-course-метрике.
- Терциль (нижняя/средняя/верхняя треть распределения когорты) — простой и
  устойчивый к выбросам метод из вариантов, разрешённых оператором явно
  ("на усмотрение реализации"). Ранг ученика — доля пиров со значением ``<=``
  его собственного; ``rank < 1/3`` → ``worse``, ``< 2/3`` → ``average``, иначе
  ``better`` (для метрик, где "меньше — лучше", напр. доля пропусков, шкала
  инвертируется).
- **Производительность.** СОБСТВЕННОЕ значение ученика — ВСЕГДА точное, тем же
  кодом, что и раньше (``percent``/``attendance``/``between_lessons`` не
  меняются). Для распределения КОГОРТЫ (только чтобы найти терцильные
  границы, само число не публикуется) используются облегчённые bulk-запросы
  (``_bulk_percent_complete``/``_bulk_missed_ratio``/
  ``_bulk_between_lessons_activity``, GROUP BY по всем пирам ОДНИМ запросом) —
  не полный движок статусов на пира (что было бы N+1: до 30+ пиров × несколько
  запросов на каждого). Упрощения bulk-пути документированы у каждой функции;
  расхождение с точным движком возможно только в редких пограничных случаях
  (напр. хвост норматива за горизонтом генератора occurrence не учитывается у
  ПИРОВ) и не влияет на публикуемое числовое значение — только на позицию в
  терциле.
- **Минимизация данных** (tsk-460): в ответе нет ни сырых значений пиров, ни
  их состава/количества — только ``CohortLevel`` СВОЕГО ребёнка.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services import (
    charge_service,
    manual_progress_service,
    pricing_service,
    retention_service,
)
from app.services.teacher_lesson_summary_service import (
    DONE_STATUSES,
    MANUAL_SOURCE,
    load_homework_window,
)

_METRIC_KEYS = ("tasks_completed", "theory_completed", "first_try", "help_requested")

#: Статусы участия, означающие ФАКТИЧЕСКУЮ явку.
_ATTENDED_STATUSES = ("confirmed", "completed")
#: Статусы, при которых занятие не входит в норматив вовсе:
#: `rescheduled` — участие заменено другим (его место занимает целевая строка,
#: где бы она ни оказалась), `on_break` — ученик в перерыве.
_NOT_COUNTED_STATUSES = ("rescheduled", "on_break")


def _subtract_metrics(total: dict[str, int], subset: dict[str, int]) -> dict[str, int]:
    """``total - subset`` по каждой метрике (см. docstring модуля — субтракция
    корректна по построению, subset ⊆ total на уровне затронутых заданий)."""
    return {key: max(0, total[key] - subset[key]) for key in _METRIC_KEYS}


async def _load_in_class_hours_window(
    db: AsyncSession, *, student_id: int, period_from: datetime, period_to: datetime,
) -> dict[str, int]:
    """Метрики ДЗ (тот же состав, что ``load_homework_window``), ограниченные
    объединением occurrence-окон ``[scheduled_at, +duration_minutes]`` этого
    ученика, пересекающихся с ``[period_from, period_to]``."""
    completed_row = (
        await db.execute(
            text(
                "WITH first_success AS ( "
                "    SELECT DISTINCT ON (tr.task_id) tr.task_id, tr.submitted_at "
                "    FROM task_results tr "
                "    JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL "
                "    WHERE tr.user_id = :student_id AND tr.is_correct = true "
                "      AND tr.source_system IS DISTINCT FROM :manual_source "
                "      AND tr.submitted_at >= :period_from AND tr.submitted_at <= :period_to "
                "      AND EXISTS ( "
                "          SELECT 1 FROM lesson_occurrence lo "
                "          JOIN lesson_occurrence_participant lop "
                "              ON lop.occurrence_id = lo.id AND lop.student_id = :student_id "
                "          WHERE tr.submitted_at BETWEEN lo.scheduled_at "
                "              AND lo.scheduled_at + make_interval(mins => lo.duration_minutes) "
                "      ) "
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
                "period_from": period_from,
                "period_to": period_to,
            },
        )
    ).mappings().fetchone()

    help_count = (
        await db.execute(
            text(
                "SELECT COUNT(*) AS cnt FROM help_requests hr "
                "WHERE hr.student_id = :student_id "
                "  AND hr.created_at >= :period_from AND hr.created_at <= :period_to "
                "  AND EXISTS ( "
                "      SELECT 1 FROM lesson_occurrence lo "
                "      JOIN lesson_occurrence_participant lop "
                "          ON lop.occurrence_id = lo.id AND lop.student_id = :student_id "
                "      WHERE hr.created_at BETWEEN lo.scheduled_at "
                "          AND lo.scheduled_at + make_interval(mins => lo.duration_minutes) "
                "  )"
            ),
            {"student_id": student_id, "period_from": period_from, "period_to": period_to},
        )
    ).scalar()

    materials_completed = (
        await db.execute(
            text(
                "SELECT COUNT(*) AS cnt FROM student_material_progress smp "
                "WHERE smp.student_id = :student_id AND smp.status = 'completed' "
                "  AND smp.completed_at IS NOT NULL "
                "  AND smp.source IS DISTINCT FROM :manual_source "
                "  AND smp.completed_at >= :period_from AND smp.completed_at <= :period_to "
                "  AND EXISTS ( "
                "      SELECT 1 FROM lesson_occurrence lo "
                "      JOIN lesson_occurrence_participant lop "
                "          ON lop.occurrence_id = lo.id AND lop.student_id = :student_id "
                "      WHERE smp.completed_at BETWEEN lo.scheduled_at "
                "          AND lo.scheduled_at + make_interval(mins => lo.duration_minutes) "
                "  )"
            ),
            {
                "student_id": student_id,
                "manual_source": MANUAL_SOURCE,
                "period_from": period_from,
                "period_to": period_to,
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


async def _list_active_student_courses(db: AsyncSession, student_id: int) -> list[dict[str, Any]]:
    """Активные курсы ученика (``user_courses.is_active``) — БЕЗ повторной
    ACL-проверки. ``manual_progress_service.list_accessible_student_courses``
    для этого не годится: она внутри звонит ``can_edit_progress`` ПО КАЖДОМУ
    курсу, а та функция не знает о роли `parent` (tsk-478) — с ней courses[]
    молча оставался бы пустым для родителя даже у реально записанного
    ученика (найдено живой проверкой на проде, не тестами: тестовый ученик в
    юнит-тестах случайно не имел записей на курсы, поэтому пустой список
    выглядел "правильным"). Доступ к ЭТОМУ ученику уже проверен вызывающим
    роутом (`_ensure_dashboard_access`) ДО вызова `get_student_dashboard` —
    повторная per-course проверка здесь избыточна и для parent-ветки просто
    ломает контракт."""
    rows = (
        await db.execute(
            text(
                "SELECT c.id AS course_id, c.title "
                "FROM user_courses uc "
                "JOIN courses c ON c.id = uc.course_id "
                "WHERE uc.user_id = :student_id AND uc.is_active = true "
                "ORDER BY uc.order_number ASC NULLS LAST, c.id"
            ),
            {"student_id": student_id},
        )
    ).mappings().fetchall()
    return [{"course_id": int(r["course_id"]), "title": r["title"]} for r in rows]


async def _load_attendance(
    db: AsyncSession, *, student_id: int, period_from: datetime, period_to: datetime,
    now: datetime, include_norm_diagnostics: bool,
) -> dict[str, Any]:
    """Посещение за период по НОРМАТИВУ (tsk-556, решение оператора 2026-08-04):
    «должен был посетить N, посетил M — значит пропусков N−M».

    Прежняя (статусная) модель считала пропуски по статусу каждой записи и
    ломалась на порядке и авторстве отметок: преподаватель ставит пропуск, потом
    сам же исправляет на явку; ученик не отменил занятие, отменил преподаватель,
    а ученик записался на другой день. Нормативная модель к этому безразлична —
    важен только итог.

    **Источник норматива — гибрид.** За часть периода, до которой генератор
    занятий уже дошёл, норматив берётся из ФАКТИЧЕСКИ заведённых занятий: это
    исторически верно и невосприимчиво к смене расписания среди периода (при
    откреплении ученика удаляются только БУДУЩИЕ `scheduled`-участия, прошлое
    остаётся; самих занятий система не удаляет нигде). За хвост периода за
    горизонтом генератора — из постоянного расписания за вычетом перерывов,
    тем же счётом, что и деньги (``charge_service.lesson_counts_for_period``).

    Переносы в норматив не входят вовсе: строка `rescheduled` заменена другой
    строкой, и та посчитается там, куда переехала. Поэтому перенос не может
    стать пропуском ни сам по себе, ни задвоением — обход цепочки
    `rescheduled_to_occurrence_id` (tsk-503) здесь не нужен, инвариант держится
    по построению.

    :returns: ``planned`` (норматив за период), ``attended`` (посетил),
        ``missed`` (пропустил — из уже прошедших), ``upcoming`` (ещё впереди).
        Инвариант: ``planned == attended + missed + upcoming``. Плюс
        ``norm_source``/``not_conducted``/``discrepancy`` (tsk-557,
        `include_norm_diagnostics=False` держит их `None` — для
        родителя/гостевой ссылки).
    """
    elapsed_to = min(period_to, now)
    row = (
        await db.execute(
            text(
                "SELECT "
                "  count(*) FILTER (WHERE lo.scheduled_at <= :elapsed_to) AS elapsed, "
                "  count(*) FILTER (WHERE lo.scheduled_at <= :elapsed_to "
                "                     AND lop.status = ANY(:attended)) AS attended, "
                "  count(*) AS generated, "
                "  max(lo.scheduled_at) AS last_generated "
                "FROM lesson_occurrence_participant lop "
                "JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id "
                "WHERE lop.student_id = :student_id "
                "  AND lo.scheduled_at >= :period_from AND lo.scheduled_at <= :period_to "
                "  AND NOT (lop.status = ANY(:not_counted))"
            ),
            {
                "student_id": student_id,
                "period_from": period_from,
                "period_to": period_to,
                "elapsed_to": elapsed_to,
                "attended": list(_ATTENDED_STATUSES),
                "not_counted": list(_NOT_COUNTED_STATUSES),
            },
        )
    ).mappings().fetchone()

    elapsed = int(row["elapsed"] or 0)
    attended = int(row["attended"] or 0)
    planned = int(row["generated"] or 0)

    # Хвост периода за горизонтом генератора — по постоянному расписанию.
    # Горизонт берём по ЭТОМУ ученику: у прикреплённого позже занятий может
    # быть сгенерировано меньше, чем у остальных.
    horizon = await _generator_horizon(db, student_id=student_id, fallback=now)
    tail_from = max(horizon.date() + timedelta(days=1), period_from.date())
    tail_to = period_to.date()
    if tail_from <= tail_to:
        counts = await charge_service.lesson_counts_for_period(
            db, student_id=student_id, period_from=tail_from, period_to=tail_to,
        )
        # `billable` — то же «за вычетом невыставляемого», что и у денег: хвост
        # всегда за горизонтом генератора, то есть в будущем, поэтому вычет
        # «ученик ещё не пришёл» (tsk-630) там нулевой по построению. Зовём
        # общее свойство, а не повторяем вычитание, чтобы норматив и деньги не
        # разъехались, если вычетов станет больше.
        planned += counts.billable

    norm_source: Optional[str] = None
    not_conducted: Optional[int] = None
    discrepancy: Optional[bool] = None
    if include_norm_diagnostics:
        resolution = await pricing_service.resolve_attendance_frequency(
            db, student_id=student_id,
        )
        norm_source = resolution.source
        discrepancy = resolution.discrepancy
        if resolution.source == "inferred_from_price" and resolution.weekly_lessons is not None:
            # При активном расписании разница уже отражена в `planned` по
            # построению (гибридный источник считает прошлое по факту); при
            # `unknown` считать нечем. Сигнал есть только здесь — расписания
            # нет вовсе, а генератор занятий никогда не заполнит его сам.
            elapsed_days = max((elapsed_to - period_from).days, 0)
            expected_from_price = resolution.weekly_lessons * elapsed_days // 7
            not_conducted = max(0, expected_from_price - elapsed)

    return {
        "planned": planned,
        "attended": attended,
        "missed": max(0, elapsed - attended),
        "upcoming": max(0, planned - elapsed),
        "norm_source": norm_source,
        "not_conducted": not_conducted,
        "discrepancy": discrepancy,
    }


async def _generator_horizon(
    db: AsyncSession, *, student_id: int, fallback: datetime
) -> datetime:
    """До какой даты занятия этому ученику уже сгенерированы.

    Считается по ВСЕМ его занятиям, не только попавшим в период: период может
    целиком лежать в прошлом, а горизонт — далеко впереди, и тогда хвоста по
    расписанию быть не должно вовсе. Занятий нет совсем (только что прикреплён
    или их и не будет) — горизонт «сейчас», весь будущий хвост считается по
    расписанию.
    """
    last = (
        await db.execute(
            text(
                "SELECT max(lo.scheduled_at) FROM lesson_occurrence_participant lop "
                "JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id "
                "WHERE lop.student_id = :student_id"
            ),
            {"student_id": student_id},
        )
    ).scalar()
    return max(last, fallback) if last is not None else fallback


def _tercile_level(
    value: Optional[float],
    peer_values: list[float],
    *,
    higher_is_better: bool,
    cohort_size: int,
    min_cohort: int,
) -> str:
    """Классифицировать ``value`` относительно ``peer_values`` (tsk-504).

    ``cohort_size`` — размер когорты ПО ЗАПИСИ на курс (``user_courses``),
    а не ``len(peer_values)``: у части пиров конкретная метрика может быть
    неопределена (напр. ``planned == 0``) и они отфильтрованы из
    ``peer_values`` — порог "фактуры" при этом решает ЗАПИСЬ, а не то, у
    скольких пиров метрика посчиталась (иначе порог оператора ``>= 5``
    незаметно сузился бы до другого числа).

    Ранг — доля пиров со значением ``<=`` собственного (не строгое
    неравенство: иначе ученик, равный ХУДШЕМУ пиру, ранжировался бы как 0-й
    ранг вместо честного "на уровне нижней трети").
    """
    if value is None or cohort_size < min_cohort or not peer_values:
        return "insufficient_data"
    rank = sum(1 for v in peer_values if v <= value) / len(peer_values)
    if rank < 1 / 3:
        tier = "worse"
    elif rank < 2 / 3:
        tier = "average"
    else:
        tier = "better"
    if not higher_is_better:
        tier = {"worse": "better", "better": "worse", "average": "average"}[tier]
    return tier


async def _active_course_peers(
    db: AsyncSession, *, course_ids: list[int], exclude_student_id: int,
) -> list[int]:
    """Другие активные ученики (``user_courses.is_active``) курсов
    ``course_ids`` — сам ``exclude_student_id`` исключён по построению
    (оператор: "опираться можно на других учеников", tsk-504)."""
    if not course_ids:
        return []
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT user_id FROM user_courses "
                "WHERE course_id = ANY(:course_ids) AND is_active = true "
                "  AND user_id != :exclude_id"
            ),
            {"course_ids": course_ids, "exclude_id": exclude_student_id},
        )
    ).scalars().all()
    return [int(r) for r in rows]


async def _bulk_percent_complete(
    db: AsyncSession, *, peer_ids: list[int], task_ids: list[int], material_ids: list[int],
) -> dict[int, float]:
    """Процент прохождения ЭТОГО дерева курса для каждого пира, ОДНИМ
    запросом (не движок статусов на пира — см. docstring модуля).

    Приближение: "пройдено" — задание с ЛЮБЫМ верным результатом
    (``is_correct=true``, попытка не отменена) либо явно пропущенное
    (``student_task_progress.status='skipped'``); материал — ``completed``
    либо ``skipped``. Не учитывает ``BLOCKED_LIMIT`` и прочие промежуточные
    состояния движка — им и так не место в "пройдено", расхождения с точным
    движком нет. Пир без единого результата в выдачу не попадёт — учтён как
    0 через ``unnest(:peer_ids)`` (LEFT JOIN), а не молча пропущен.
    """
    total = len(task_ids) + len(material_ids)
    if not peer_ids or total == 0:
        return {}
    rows = (
        await db.execute(
            text(
                "WITH task_done AS ( "
                "  SELECT user_id, COUNT(DISTINCT task_id) AS n FROM ( "
                "    SELECT tr.user_id, tr.task_id FROM task_results tr "
                "    JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL "
                "    WHERE tr.task_id = ANY(:task_ids) AND tr.is_correct = true "
                "      AND tr.user_id = ANY(:peer_ids) "
                "    UNION "
                "    SELECT stp.student_id AS user_id, stp.task_id FROM student_task_progress stp "
                "    WHERE stp.task_id = ANY(:task_ids) AND stp.status = 'skipped' "
                "      AND stp.student_id = ANY(:peer_ids) "
                "  ) x GROUP BY user_id "
                "), material_done AS ( "
                "  SELECT student_id AS user_id, COUNT(DISTINCT material_id) AS n "
                "  FROM student_material_progress "
                "  WHERE material_id = ANY(:material_ids) AND status IN ('completed', 'skipped') "
                "    AND student_id = ANY(:peer_ids) "
                "  GROUP BY student_id "
                ") "
                "SELECT p.user_id, COALESCE(td.n, 0) + COALESCE(md.n, 0) AS done "
                "FROM unnest(CAST(:peer_ids AS int[])) AS p(user_id) "
                "LEFT JOIN task_done td ON td.user_id = p.user_id "
                "LEFT JOIN material_done md ON md.user_id = p.user_id"
            ),
            {"peer_ids": peer_ids, "task_ids": task_ids, "material_ids": material_ids},
        )
    ).mappings().fetchall()
    return {int(r["user_id"]): int(r["done"]) / total * 100.0 for r in rows}


async def _bulk_missed_ratio(
    db: AsyncSession, *, peer_ids: list[int], period_from: datetime, period_to: datetime, now: datetime,
) -> dict[int, float]:
    """``missed/planned`` для каждого пира, ОДНИМ запросом.

    Упрощение относительно точного ``_load_attendance`` (см. docstring
    модуля): ``planned`` — только УЖЕ сгенерированные occurrence в периоде,
    БЕЗ хвоста по расписанию за горизонтом генератора (тот хвост требует
    per-student `_generator_horizon` — снова N+1). Занятия генерируются на
    3 недели вперёд (проектная память) — хвост существен только для периодов
    длиннее этого горизонта. Пир с ``planned == 0`` в выдачу не попадает —
    ratio для него не определён, не 0 (0 пропусков при 0 занятий — не
    "отлично", а "нечего сравнивать")."""
    if not peer_ids:
        return {}
    elapsed_to = min(period_to, now)
    rows = (
        await db.execute(
            text(
                "SELECT lop.student_id AS user_id, "
                "  count(*) FILTER (WHERE lo.scheduled_at <= :elapsed_to) AS elapsed, "
                "  count(*) FILTER (WHERE lo.scheduled_at <= :elapsed_to "
                "                     AND lop.status = ANY(:attended)) AS attended, "
                "  count(*) AS planned "
                "FROM lesson_occurrence_participant lop "
                "JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id "
                "WHERE lop.student_id = ANY(:peer_ids) "
                "  AND lo.scheduled_at >= :period_from AND lo.scheduled_at <= :period_to "
                "  AND NOT (lop.status = ANY(:not_counted)) "
                "GROUP BY lop.student_id"
            ),
            {
                "peer_ids": peer_ids, "period_from": period_from, "period_to": period_to,
                "elapsed_to": elapsed_to, "attended": list(_ATTENDED_STATUSES),
                "not_counted": list(_NOT_COUNTED_STATUSES),
            },
        )
    ).mappings().fetchall()
    result: dict[int, float] = {}
    for r in rows:
        planned = int(r["planned"] or 0)
        if planned <= 0:
            continue
        elapsed = int(r["elapsed"] or 0)
        attended = int(r["attended"] or 0)
        result[int(r["user_id"])] = max(0, elapsed - attended) / planned
    return result


async def _bulk_between_lessons_activity(
    db: AsyncSession, *, peer_ids: list[int], period_from: datetime, period_to: datetime,
) -> dict[int, float]:
    """"Между занятиями" активность (``tasks_completed + theory_completed``
    вне occurrence-окон) для каждого пира, ДВУМЯ запросами (задания +
    материалы, не по одному на пира — см. docstring модуля).

    Пир без единого результата за период НЕ появится ни в одной из двух
    выборок — инициализируем 0 явно для всех ``peer_ids``, иначе он выпал бы
    из распределения когорты вместо честного "наименее активный"."""
    activity: dict[int, float] = {uid: 0.0 for uid in peer_ids}
    if not peer_ids:
        return activity
    task_rows = (
        await db.execute(
            text(
                "SELECT tr.user_id, "
                "  COUNT(DISTINCT tr.task_id) AS total_n, "
                "  COUNT(DISTINCT tr.task_id) FILTER ( "
                "    WHERE EXISTS ( "
                "      SELECT 1 FROM lesson_occurrence_participant lop "
                "      JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id "
                "      WHERE lop.student_id = tr.user_id "
                "        AND tr.submitted_at BETWEEN lo.scheduled_at "
                "            AND lo.scheduled_at + make_interval(mins => lo.duration_minutes) "
                "    ) "
                "  ) AS in_class_n "
                "FROM task_results tr "
                "JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL "
                "WHERE tr.user_id = ANY(:peer_ids) AND tr.is_correct = true "
                "  AND tr.source_system IS DISTINCT FROM :manual_source "
                "  AND tr.submitted_at >= :period_from AND tr.submitted_at <= :period_to "
                "GROUP BY tr.user_id"
            ),
            {
                "peer_ids": peer_ids, "manual_source": MANUAL_SOURCE,
                "period_from": period_from, "period_to": period_to,
            },
        )
    ).mappings().fetchall()
    material_rows = (
        await db.execute(
            text(
                "SELECT smp.student_id AS user_id, "
                "  COUNT(DISTINCT smp.material_id) AS total_n, "
                "  COUNT(DISTINCT smp.material_id) FILTER ( "
                "    WHERE EXISTS ( "
                "      SELECT 1 FROM lesson_occurrence_participant lop "
                "      JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id "
                "      WHERE lop.student_id = smp.student_id "
                "        AND smp.completed_at BETWEEN lo.scheduled_at "
                "            AND lo.scheduled_at + make_interval(mins => lo.duration_minutes) "
                "    ) "
                "  ) AS in_class_n "
                "FROM student_material_progress smp "
                "WHERE smp.student_id = ANY(:peer_ids) AND smp.status = 'completed' "
                "  AND smp.completed_at IS NOT NULL "
                "  AND smp.source IS DISTINCT FROM :manual_source "
                "  AND smp.completed_at >= :period_from AND smp.completed_at <= :period_to "
                "GROUP BY smp.student_id"
            ),
            {
                "peer_ids": peer_ids, "manual_source": MANUAL_SOURCE,
                "period_from": period_from, "period_to": period_to,
            },
        )
    ).mappings().fetchall()
    for r in task_rows:
        activity[int(r["user_id"])] += max(0, int(r["total_n"]) - int(r["in_class_n"]))
    for r in material_rows:
        activity[int(r["user_id"])] += max(0, int(r["total_n"]) - int(r["in_class_n"]))
    return activity


async def _load_course_pace_and_forecast(
    db: AsyncSession,
    *,
    student_id: int,
    course_id: int,
    items: list[dict[str, Any]],
    now: datetime,
    pace_weeks: int,
) -> tuple[Optional[date], bool]:
    """(дата прогноза окончания | None, курс уже пройден целиком).

    Темп — сколько элементов ИМЕННО ЭТОГО курса (по ``task_id``/``material_id``
    из уже посчитанного ``items``) ученик завершил за последние ``pace_weeks``
    недель, делённое на ``pace_weeks``. При темпе 0 или уже пройденном курсе —
    ``None`` (не делить на ноль)."""
    countable = [i for i in items if i["item_type"] != "course"]
    done = sum(1 for i in countable if i["status"] in DONE_STATUSES)
    total = len(countable)
    remaining = total - done
    if remaining <= 0:
        return None, True

    if pace_weeks <= 0:
        # Некорректная конфигурация (STUDENT_FORECAST_PACE_WEEKS<=0) — не
        # делить на ноль/отрицательное число, прогноз просто недоступен.
        return None, False

    task_ids = [i["item_id"] for i in countable if i["item_type"] == "task"]
    material_ids = [i["item_id"] for i in countable if i["item_type"] == "material"]
    since = now - timedelta(weeks=pace_weeks)

    done_recent_tasks = (
        await db.execute(
            text(
                "SELECT COUNT(DISTINCT tr.task_id) FROM task_results tr "
                "WHERE tr.user_id = :student_id AND tr.is_correct = true "
                "  AND tr.task_id = ANY(:task_ids) AND tr.submitted_at >= :since"
            ),
            {"student_id": student_id, "task_ids": task_ids, "since": since},
        )
    ).scalar() or 0
    done_recent_materials = (
        await db.execute(
            text(
                "SELECT COUNT(DISTINCT smp.material_id) FROM student_material_progress smp "
                "WHERE smp.student_id = :student_id AND smp.status = 'completed' "
                "  AND smp.material_id = ANY(:material_ids) AND smp.completed_at >= :since"
            ),
            {"student_id": student_id, "material_ids": material_ids, "since": since},
        )
    ).scalar() or 0

    pace_per_week = (int(done_recent_tasks) + int(done_recent_materials)) / pace_weeks
    if pace_per_week <= 0:
        return None, False

    weeks_left = remaining / pace_per_week
    forecast_dt = now + timedelta(weeks=weeks_left)
    return forecast_dt.date(), False


async def get_student_dashboard(
    db: AsyncSession,
    *,
    student_id: int,
    period_from: datetime,
    period_to: datetime,
    viewer_is_staff: bool = False,
) -> dict[str, Any]:
    """Собрать периодный дашборд ученика (tsk-494) — состав из 5 пунктов
    задачи: курсы+прогресс+прогноз, итог за период, посещение, ДЗ между
    занятиями, произвольный период. Вызывающий обязан проверить доступ
    (композитный гейт роута — ``can_edit_progress`` ИЛИ родительская связка,
    tsk-478) ДО вызова — эта функция сама ACL не проверяет и потому не
    принимает ``current_user`` (раньше принимала для проброса в
    `list_accessible_student_courses`, но та внутренняя ACL-проверка
    ломала courses[] для роли `parent` — см. `_list_active_student_courses`).

    :param viewer_is_staff: персонал (`can_edit_progress` — сервис/admin/
        methodist/teacher) видит норматив из цены в `attendance` (tsk-557);
        родитель/гостевая ссылка — `False` по умолчанию, поля остаются `None`.
    :raises ValueError: ``period_from``/``period_to`` naive (без timezone) —
        CLAUDE.md Date/Time Safety, тут не reject-им молча подменой на
        локальное "сейчас"."""
    if period_from.tzinfo is None or period_to.tzinfo is None:
        raise ValueError("period_from/period_to должны быть timezone-aware")
    now = datetime.now(period_to.tzinfo)
    settings = Settings()

    period_total = await load_homework_window(
        db, student_id=student_id, window_from=period_from, window_to=period_to,
    )
    in_class_hours = await _load_in_class_hours_window(
        db, student_id=student_id, period_from=period_from, period_to=period_to,
    )
    between_lessons = _subtract_metrics(period_total, in_class_hours)
    attendance = await _load_attendance(
        db, student_id=student_id, period_from=period_from, period_to=period_to, now=now,
        include_norm_diagnostics=viewer_is_staff,
    )

    accessible = await _list_active_student_courses(db, student_id)
    min_cohort = settings.student_dashboard_cohort_min_size

    # tsk-504: когорта для missed_level/between_lessons_activity_level —
    # объединение активных учеников ВСЕХ корневых курсов ребёнка (метрики не
    # per-course, см. docstring модуля).
    own_course_ids = [c["course_id"] for c in accessible]
    global_peer_ids = await _active_course_peers(
        db, course_ids=own_course_ids, exclude_student_id=student_id,
    )
    missed_peer_ratios = await _bulk_missed_ratio(
        db, peer_ids=global_peer_ids, period_from=period_from, period_to=period_to, now=now,
    )
    own_missed_ratio = (
        attendance["missed"] / attendance["planned"] if attendance["planned"] else None
    )
    attendance["missed_level"] = _tercile_level(
        own_missed_ratio, list(missed_peer_ratios.values()),
        higher_is_better=False, cohort_size=len(global_peer_ids), min_cohort=min_cohort,
    )

    activity_peer_values = await _bulk_between_lessons_activity(
        db, peer_ids=global_peer_ids, period_from=period_from, period_to=period_to,
    )
    own_activity = between_lessons["tasks_completed"] + between_lessons["theory_completed"]
    between_lessons_activity_level = _tercile_level(
        own_activity, list(activity_peer_values.values()),
        higher_is_better=True, cohort_size=len(global_peer_ids), min_cohort=min_cohort,
    )

    courses: list[dict[str, Any]] = []
    for course in accessible:
        course_id = course["course_id"]
        data = await manual_progress_service.get_student_progress(
            db, student_id=student_id, course_id=course_id,
        )
        items = data["items"]
        countable = [i for i in items if i["item_type"] != "course"]
        done = sum(1 for i in countable if i["status"] in DONE_STATUSES)
        total = len(countable)
        percent = round(done / total * 100) if total else 0

        # tsk-504: когорта per-course (метрика однозначно привязана к
        # course_id) — активные ученики ИМЕННО этого курса.
        course_peer_ids = await _active_course_peers(
            db, course_ids=[course_id], exclude_student_id=student_id,
        )
        task_ids = [i["item_id"] for i in countable if i["item_type"] == "task"]
        material_ids = [i["item_id"] for i in countable if i["item_type"] == "material"]
        percent_peer_values = await _bulk_percent_complete(
            db, peer_ids=course_peer_ids, task_ids=task_ids, material_ids=material_ids,
        )
        pace_level = _tercile_level(
            float(percent) if total else None, list(percent_peer_values.values()),
            higher_is_better=True, cohort_size=len(course_peer_ids), min_cohort=min_cohort,
        )

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

        forecast_date, is_completed = await _load_course_pace_and_forecast(
            db,
            student_id=student_id,
            course_id=course_id,
            items=items,
            now=now,
            pace_weeks=settings.student_forecast_pace_weeks,
        )

        courses.append({
            "course_id": course_id,
            "title": course["title"],
            "percent_complete": percent,
            "pace_level": pace_level,
            "current_section_title": current_section_title,
            "current_item_title": current_item_title,
            "forecast_completion_date": forecast_date,
            "is_completed": is_completed,
        })

    return {
        "student_id": student_id,
        "period_from": period_from,
        "period_to": period_to,
        "courses": courses,
        "period_total": {
            "tasks_completed": period_total["tasks_completed"],
            "theory_completed": period_total["theory_completed"],
            "first_try": period_total["first_try"],
            "help_requested_count": period_total["help_requested"],
        },
        "in_class_hours": {
            "tasks_completed": in_class_hours["tasks_completed"],
            "theory_completed": in_class_hours["theory_completed"],
            "first_try": in_class_hours["first_try"],
            "help_requested_count": in_class_hours["help_requested"],
        },
        "between_lessons": {
            "tasks_completed": between_lessons["tasks_completed"],
            "theory_completed": between_lessons["theory_completed"],
            "first_try": between_lessons["first_try"],
            "help_requested_count": between_lessons["help_requested"],
        },
        "attendance": attendance,
        "between_lessons_activity_level": between_lessons_activity_level,
        # tsk-032: серия активных недель между занятиями. Считается тем же
        # определением события, что и `between_lessons` выше (общий код —
        # `retention_service`), поэтому число и серия не могут разойтись.
        "retention": await retention_service.get_retention_summary(
            db, student_id=student_id,
        ),
    }


__all__ = ["get_student_dashboard"]
