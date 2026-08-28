"""Помощник вёрстки осеннего расписания (tsk-674, фаза 2).

Что здесь есть и чего здесь намеренно нет.

**Есть.** Спрос по часам (сколько человек назвали час желательным и сколько
возможным), подбор набора часов под этот спрос, раскладка учеников по слотам
с целью 5-6 человек и потолком 10, счёт «цены решения» (разрывы, переполнение,
кому не досталось желаемого) и применение утверждённой сетки в реальные слоты.

**Нет.** Автоматической расстановки без человека. Любой расчёт — это
предложение: набор часов методист правит руками, применение идёт отдельным
подтверждённым действием и всегда сперва показывает отчёт (`dry_run`).

Три вещи, которые ломают понимание, если про них забыть:

1. **Слоты последовательные.** Все занятия ведёт один преподаватель (решение
   оператора от 2026-08-25), поэтому два слота в один час невозможны, а потолок
   школы — 33 часа сетки, а не «сколько угодно параллельных групп».
2. **Время везде московское.** Пожелания собраны по Москве, сетка ведётся по
   Москве. Пояс ученика дорисовывает клиент (tsk-588) — здесь его нет вовсе.
3. **Осеннее окно почти не пересекается с нынешним расписанием.** Утренние
   занятия (10:00, 11:00) в сетку не попадают, то есть вёрстка — это переезд
   людей, а не правка сетки. Поэтому у каждого ученика видно его нынешний час,
   а у каждого слота — попадает ли он в окно.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import time
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.exceptions import DomainError
from app.schemas.schedule_plan import (
    HARD_MAX,
    TARGET_MAX,
    TARGET_MIN,
    ApplyLessonChange,
    ApplySlotOutcome,
    PlanCapacity,
    PlanCurrentSlot,
    PlanDayGap,
    PlanDemandCell,
    PlanHour,
    PlanMetrics,
    PlanSlot,
    PlanStudentRef,
    PlanStudentRow,
    PlanTeacher,
    PlanUnmatchedStudent,
    SchedulePlanApplyRequest,
    SchedulePlanApplyResult,
    SchedulePlanPreview,
    SchedulePlanSnapshot,
    SlotLoadLevel,
)
from app.schemas.schedule_preference import (
    DEFAULT_LESSONS_PER_WEEK,
    GRID_SLOT_MINUTES,
    GRID_TIMEZONE,
    SCHEDULE_GRID,
)
from app.services import lesson_calendar_service
from app.services.schedule_preference_service import COUNTED_AUDIENCE_FROM, grid_as_days

logger = logging.getLogger(__name__)

#: Час сетки: (день недели, время начала по Москве).
HourKey = tuple[int, time]

#: Насколько «возможный» час дешевле «желательного» при подборе набора часов.
#: Возможный тоже считается — иначе редкий, но всем приемлемый час проиграет
#: часу, который двое назвали любимым.
POSSIBLE_WEIGHT = 0.35

#: Надбавка часу, в котором слот уже стоит: не переезжать зря.
EXISTING_BONUS = 0.75

#: Надбавка часу, примыкающему к уже выбранному в тот же день: занятия должны
#: идти подряд (требование оператора).
ADJACENT_BONUS = 0.5


@dataclass
class StudentPlanInput:
    """Ученик глазами раскладки: сколько занятий нужно и какие часы он назвал."""

    student_id: int
    lessons_per_week: int
    preferred: list[HourKey] = field(default_factory=list)
    possible: list[HourKey] = field(default_factory=list)


def grid_hours() -> list[HourKey]:
    """Все 33 часа осенней сетки, по порядку: Пн-Чт 12-19, Сб 9-14 МСК."""
    return [
        (weekday, time(hour=h))
        for weekday, hours in sorted(SCHEDULE_GRID.items())
        for h in hours
    ]


def in_grid(hour: HourKey) -> bool:
    """Существует ли такой час осенью. Нынешние 10:00 и 11:00 — нет."""
    allowed = SCHEDULE_GRID.get(hour[0])
    return allowed is not None and hour[1].hour in allowed and hour[1].minute == 0


def level_for(count: int) -> SlotLoadLevel:
    """Наполнение слота словами методиста."""
    if count > HARD_MAX:
        return "over"
    if count > TARGET_MAX:
        return "crowded"
    if count >= TARGET_MIN:
        return "ok"
    return "light"


# ────────────────────────── раскладка (чистые функции) ──────────────────────


@dataclass
class Assignment:
    """Результат раскладки: кто в каком часу и кому чего не хватило."""

    by_hour: dict[HourKey, list[tuple[int, str]]] = field(default_factory=dict)
    by_student: dict[int, list[tuple[HourKey, str]]] = field(default_factory=dict)

    def placed(self, student_id: int) -> int:
        return len(self.by_student.get(student_id, []))

    def preferred_placed(self, student_id: int) -> int:
        return sum(1 for _, kind in self.by_student.get(student_id, []) if kind == "preferred")


def assign(students: Iterable[StudentPlanInput], hours: Iterable[HourKey]) -> Assignment:
    """Разложить учеников по выбранным часам.

    Правила, и все они — про то, что увидит человек:

    - Сначала желательные часы, возможные — только когда желательных не хватило.
    - В слот не ставим больше десяти: это запрет оператора, а не пожелание.
    - Пока в часе меньше шести, ставим в самый заполненный из подходящих —
      иначе получится два десятка слотов по двое, и все 33 часа кончатся.
      Как только все подходящие часы добрали до шести, идём в наименее
      заполненный: дальше цель — не превысить десять.
    - Первыми раскладываем самых зажатых: у кого выбор уже меньше, чем нужно
      занятий. Иначе им не остаётся места, а списку «поговорить лично» это
      добавляет людей на ровном месте.
    """
    hour_set = list(dict.fromkeys(hours))
    order = {h: i for i, h in enumerate(hour_set)}
    loads: dict[HourKey, int] = {h: 0 for h in hour_set}
    result = Assignment(by_hour={h: [] for h in hour_set}, by_student={})

    def freedom(s: StudentPlanInput) -> tuple[int, int, int]:
        avail_pref = sum(1 for h in s.preferred if h in loads)
        avail_all = avail_pref + sum(1 for h in s.possible if h in loads)
        # Первым — тот, у кого желательных вариантов меньше, чем нужно занятий.
        return (avail_pref - s.lessons_per_week, avail_all, s.student_id)

    for student in sorted(students, key=freedom):
        need = student.lessons_per_week
        taken: set[HourKey] = set()
        for kind, source in (("preferred", student.preferred), ("possible", student.possible)):
            if need <= 0:
                break
            candidates = [h for h in source if h in loads and h not in taken]
            while need > 0 and candidates:
                open_hours = [h for h in candidates if loads[h] < HARD_MAX]
                if not open_hours:
                    break
                under_target = [h for h in open_hours if loads[h] < TARGET_MAX]
                if under_target:
                    # Кучкуем: самый заполненный из тех, что ещё не добрали цель.
                    chosen = sorted(under_target, key=lambda h: (-loads[h], order[h]))[0]
                else:
                    # Все добрали цель — растекаемся по наименее заполненным.
                    chosen = sorted(open_hours, key=lambda h: (loads[h], order[h]))[0]
                loads[chosen] += 1
                taken.add(chosen)
                result.by_hour[chosen].append((student.student_id, kind))
                result.by_student.setdefault(student.student_id, []).append((chosen, kind))
                need -= 1
                candidates = [h for h in candidates if h != chosen]

    return result


def suggest_hours(
    students: Iterable[StudentPlanInput],
    *,
    existing_hours: Iterable[HourKey] = (),
    keep_existing: bool = True,
) -> list[HourKey]:
    """Подобрать набор часов под собранные пожелания.

    Жадно и объяснимо: на каждом шаге берём час, который закрывает больше всего
    ещё не поставленных занятий, с надбавкой за то, что слот в этот час уже
    существует (не переезжать зря) и что час примыкает к уже выбранному
    (занятия идут подряд). Останавливаемся, когда очередной час не закрывает
    никого — оставшимся людям сетка не поможет, им нужен разговор.

    Это предложение, а не расстановка: методист набор правит.
    """
    people = list(students)
    all_hours = grid_hours()
    existing = {h for h in existing_hours if in_grid(h)}
    chosen: list[HourKey] = sorted(existing) if keep_existing else []

    while len(chosen) < len(all_hours):
        current = assign(people, chosen)
        deficit = {
            s.student_id: s.lessons_per_week - current.placed(s.student_id) for s in people
        }
        if all(v <= 0 for v in deficit.values()):
            break

        best: Optional[HourKey] = None
        best_score = 0.0
        for hour in all_hours:
            if hour in chosen:
                continue
            pref_cov = 0
            poss_cov = 0
            for s in people:
                if deficit.get(s.student_id, 0) <= 0:
                    continue
                if hour in [h for h, _ in current.by_student.get(s.student_id, [])]:
                    continue
                if hour in s.preferred:
                    pref_cov += 1
                elif hour in s.possible:
                    poss_cov += 1
            if pref_cov == 0 and poss_cov == 0:
                continue
            usable_pref = min(pref_cov, HARD_MAX)
            usable_poss = min(poss_cov, max(0, HARD_MAX - usable_pref))
            score = usable_pref + POSSIBLE_WEIGHT * usable_poss
            if hour in existing:
                score += EXISTING_BONUS
            if _is_adjacent(hour, chosen):
                score += ADJACENT_BONUS
            if score > best_score:
                best, best_score = hour, score

        if best is None:
            break
        chosen.append(best)

    return sorted(_tighten(people, chosen, protected=existing if keep_existing else set()))


def _tighten(
    people: list[StudentPlanInput],
    chosen: list[HourKey],
    *,
    protected: set[HourKey],
) -> list[HourKey]:
    """Убрать часы, без которых ничего не теряется.

    Жадный подбор всегда оставляет хвост: час, взятый ради двоих, у которых
    потом нашлось место в общем слоте. Формально сетка от него не портится, но
    методист видит слот на одного человека и час преподавателя, потраченный
    зря. Поэтому каждый час пробуем снять и оставляем снятым, если после
    переклада никто не потерял ни занятия, ни желательного часа.

    Часы, где слот уже стоит, не трогаем: их сохранность — отдельное решение
    («не переезжать зря»), и отменять его уплотнением нельзя.
    """
    def cost(hours: list[HourKey]) -> tuple[int, int]:
        a = assign(people, hours)
        placed = sum(min(a.placed(s.student_id), s.lessons_per_week) for s in people)
        with_pref = sum(1 for s in people if a.preferred_placed(s.student_id) > 0)
        return (placed, with_pref)

    current = list(chosen)
    base = cost(current)
    changed = True
    while changed and current:
        changed = False
        a = assign(people, current)
        # Самые пустые — первые кандидаты на снятие.
        for hour in sorted(current, key=lambda h: (len(a.by_hour.get(h, [])), h)):
            if hour in protected:
                continue
            candidate = [h for h in current if h != hour]
            if cost(candidate) >= base:
                current = candidate
                changed = True
                break
    return current


def _is_adjacent(hour: HourKey, chosen: Iterable[HourKey]) -> bool:
    """Примыкает ли час к уже выбранному в тот же день недели."""
    weekday, start = hour
    for other_day, other_start in chosen:
        if other_day != weekday:
            continue
        if abs(other_start.hour - start.hour) == 1:
            return True
    return False


def find_gaps(hours: Iterable[HourKey]) -> list[PlanDayGap]:
    """Дырки внутри дня: час без занятия между двумя занятыми.

    Требование «без разрывов» — про преподавателя: пустой час посреди дня он
    всё равно проводит в школе. Разрыв в начале и в конце дня разрывом не
    считается — это просто более короткий день.
    """
    by_day: dict[int, list[int]] = {}
    for weekday, start in hours:
        by_day.setdefault(weekday, []).append(start.hour)

    gaps: list[PlanDayGap] = []
    for weekday in sorted(by_day):
        taken = sorted(by_day[weekday])
        if len(taken) < 2:
            continue
        allowed = set(SCHEDULE_GRID.get(weekday, ()))
        missing = [
            h
            for h in range(taken[0] + 1, taken[-1])
            if h not in taken and h in allowed
        ]
        if missing:
            gaps.append(PlanDayGap(weekday=weekday, hours=[time(hour=h) for h in missing]))
    return gaps


# ────────────────────────────── чтение данных ───────────────────────────────

#: Аудитория вёрстки — буквально та же, что у сводки охвата: подзапрос собран
#: из `schedule_preference_service.COUNTED_AUDIENCE_FROM`, а не переписан рядом.
#: Иначе «в сводке 51 человек, а в вёрстке 49» — и никто не знает, кто прав.
#: Счётная, а не показная (tsk-712): тестовым учёткам опрос показывается, но
#: занимать ими живые слоты нельзя — расписание собирается настоящим людям.
_STUDENTS_SQL = f"""
    WITH audience AS (
        SELECT u.id AS student_id
        {COUNTED_AUDIENCE_FROM}
    )
    SELECT u.id,
           u.full_name,
           u.email,
           u.timezone,
           pref.id IS NOT NULL AS is_filled,
           COALESCE(pref.lessons_per_week, :default_lpw) AS lessons_per_week,
           pref.comment,
           COALESCE(hours.rows, '[]'::json) AS hours,
           COALESCE(slots.rows, '[]'::json) AS current_hours
      FROM audience a
      JOIN users u ON u.id = a.student_id
      LEFT JOIN student_schedule_preference pref ON pref.student_id = u.id
      LEFT JOIN LATERAL (
          SELECT json_agg(
                   json_build_object('weekday', h.weekday,
                                     'start_time', to_char(h.start_time, 'HH24:MI'),
                                     'kind', h.kind)
                   ORDER BY h.weekday, h.start_time
                 ) AS rows
            FROM student_schedule_preference_hour h
           WHERE h.preference_id = pref.id
      ) hours ON TRUE
      LEFT JOIN LATERAL (
          SELECT json_agg(
                   json_build_object('weekday', ls.weekday,
                                     'start_time', to_char(ls.start_time, 'HH24:MI'))
                   ORDER BY ls.weekday, ls.start_time
                 ) AS rows
            FROM lesson_slot_student lss
            JOIN lesson_slot ls ON ls.id = lss.slot_id
           WHERE lss.student_id = u.id AND lss.is_active AND ls.is_active
      ) slots ON TRUE
     ORDER BY u.full_name NULLS LAST, u.id
"""


def _hour_key(raw: dict[str, Any]) -> HourKey:
    return (int(raw["weekday"]), time.fromisoformat(raw["start_time"]))


async def load_students(db: AsyncSession) -> tuple[list[PlanStudentRow], list[StudentPlanInput]]:
    """Аудитория вёрстки: что каждый просил и где занимается сейчас."""
    rows = (
        await db.execute(text(_STUDENTS_SQL), {"default_lpw": DEFAULT_LESSONS_PER_WEEK})
    ).fetchall()

    view: list[PlanStudentRow] = []
    plan_input: list[StudentPlanInput] = []
    for r in rows:
        hours = r[7] or []
        current = [_hour_key(h) for h in (r[8] or [])]
        preferred = [_hour_key(h) for h in hours if h["kind"] == "preferred"]
        possible = [_hour_key(h) for h in hours if h["kind"] == "possible"]
        view.append(
            PlanStudentRow(
                student_id=int(r[0]),
                full_name=r[1],
                email=r[2],
                timezone=r[3],
                is_filled=bool(r[4]),
                lessons_per_week=int(r[5]),
                comment=r[6],
                preferred=[PlanHour(weekday=w, start_time=t) for w, t in preferred],
                possible=[PlanHour(weekday=w, start_time=t) for w, t in possible],
                current_hours=[PlanHour(weekday=w, start_time=t) for w, t in current],
                needs_move=any(not in_grid(h) for h in current),
            )
        )
        # В раскладку идут только заполнившие: у молчащих нет ни одного часа, и
        # ставить их «куда-нибудь» значит выдумать за них ответ.
        if bool(r[4]):
            plan_input.append(
                StudentPlanInput(
                    student_id=int(r[0]),
                    lessons_per_week=int(r[5]),
                    preferred=preferred,
                    possible=possible,
                )
            )
    return view, plan_input


async def load_current_slots(db: AsyncSession) -> list[PlanCurrentSlot]:
    """Действующие слоты — то, что вёрстка будет менять."""
    rows = (
        await db.execute(
            text(
                """
                SELECT ls.id,
                       ls.teacher_id,
                       t.full_name,
                       ls.weekday,
                       ls.start_time,
                       ls.duration_minutes,
                       COALESCE(p.ids, ARRAY[]::int[]) AS student_ids
                  FROM lesson_slot ls
                  LEFT JOIN users t ON t.id = ls.teacher_id
                  LEFT JOIN LATERAL (
                      SELECT array_agg(lss.student_id ORDER BY lss.student_id) AS ids
                        FROM lesson_slot_student lss
                       WHERE lss.slot_id = ls.id AND lss.is_active
                  ) p ON TRUE
                 WHERE ls.is_active
                 ORDER BY ls.weekday, ls.start_time, ls.id
                """
            )
        )
    ).fetchall()

    slots: list[PlanCurrentSlot] = []
    for r in rows:
        student_ids = list(r[6] or [])
        slots.append(
            PlanCurrentSlot(
                slot_id=int(r[0]),
                teacher_id=int(r[1]),
                teacher_name=r[2],
                weekday=int(r[3]),
                start_time=r[4],
                duration_minutes=int(r[5]),
                student_count=len(student_ids),
                in_grid=in_grid((int(r[3]), r[4])),
                level=level_for(len(student_ids)),
                student_ids=student_ids,
            )
        )
    return slots


async def load_teachers(db: AsyncSession) -> list[PlanTeacher]:
    """Преподаватели и сколько активных слотов на каждом."""
    rows = (
        await db.execute(
            text(
                """
                SELECT u.id,
                       u.full_name,
                       COUNT(ls.id) FILTER (WHERE ls.is_active) AS active_slots
                  FROM users u
                  JOIN user_roles ur ON ur.user_id = u.id
                  JOIN roles r ON r.id = ur.role_id AND r.name = 'teacher'
                  LEFT JOIN lesson_slot ls ON ls.teacher_id = u.id
                 WHERE u.is_active
                 GROUP BY u.id, u.full_name
                 ORDER BY active_slots DESC, u.full_name NULLS LAST, u.id
                """
            )
        )
    ).fetchall()
    return [
        PlanTeacher(teacher_id=int(r[0]), full_name=r[1], active_slots=int(r[2] or 0))
        for r in rows
    ]


async def get_snapshot(db: AsyncSession) -> SchedulePlanSnapshot:
    """Всё, что нужно экрану вёрстки до первого расчёта."""
    students, plan_input = await load_students(db)
    slots = await load_current_slots(db)
    teachers = await load_teachers(db)

    slot_by_hour = {(s.weekday, s.start_time): s for s in slots if s.in_grid}
    demand: list[PlanDemandCell] = []
    for hour in grid_hours():
        pref = sum(1 for s in plan_input if hour in s.preferred)
        poss = sum(1 for s in plan_input if hour in s.possible)
        existing = slot_by_hour.get(hour)
        demand.append(
            PlanDemandCell(
                weekday=hour[0],
                start_time=hour[1],
                preferred_count=pref,
                possible_count=poss,
                existing_slot_id=existing.slot_id if existing else None,
                existing_student_count=existing.student_count if existing else None,
            )
        )

    lessons_demand = sum(s.lessons_per_week for s in plan_input)
    filled_total = len(plan_input)
    return SchedulePlanSnapshot(
        grid=grid_as_days(),
        grid_timezone=GRID_TIMEZONE,
        slot_minutes=GRID_SLOT_MINUTES,
        audience_total=len(students),
        filled_total=filled_total,
        silent_total=len(students) - filled_total,
        lessons_demand=lessons_demand,
        demand=demand,
        students=students,
        current_slots=slots,
        teachers=teachers,
        capacity=PlanCapacity(
            hours_total=len(grid_hours()),
            lessons_demand=lessons_demand,
            slots_needed_min=-(-lessons_demand // TARGET_MAX) if lessons_demand else 0,
            slots_needed_max=-(-lessons_demand // TARGET_MIN) if lessons_demand else 0,
        ),
    )


# ─────────────────────────────── предпросмотр ───────────────────────────────


def build_preview(
    students: list[PlanStudentRow],
    plan_input: list[StudentPlanInput],
    current_slots: list[PlanCurrentSlot],
    hours: list[HourKey],
) -> SchedulePlanPreview:
    """Собрать раскладку по набору часов и посчитать цену решения."""
    hours = sorted(dict.fromkeys(h for h in hours if in_grid(h)))
    result = assign(plan_input, hours)
    by_id = {s.student_id: s for s in students}
    input_by_id = {s.student_id: s for s in plan_input}
    slot_by_hour = {(s.weekday, s.start_time): s for s in current_slots if s.in_grid}

    slots: list[PlanSlot] = []
    for hour in hours:
        members = result.by_hour.get(hour, [])
        existing = slot_by_hour.get(hour)
        slots.append(
            PlanSlot(
                weekday=hour[0],
                start_time=hour[1],
                students=[
                    PlanStudentRef(
                        student_id=sid,
                        full_name=by_id[sid].full_name if sid in by_id else None,
                        timezone=by_id[sid].timezone if sid in by_id else None,
                        match=kind,
                        lessons_per_week=(
                            input_by_id[sid].lessons_per_week if sid in input_by_id else 2
                        ),
                    )
                    for sid, kind in members
                ],
                count=len(members),
                level=level_for(len(members)),
                existing_slot_id=existing.slot_id if existing else None,
                existing_student_count=existing.student_count if existing else None,
            )
        )

    without_preferred: list[PlanUnmatchedStudent] = []
    unplaced: list[PlanUnmatchedStudent] = []
    fully_preferred = partial = 0
    for s in plan_input:
        placed = result.placed(s.student_id)
        pref_placed = result.preferred_placed(s.student_id)
        row = by_id.get(s.student_id)
        if placed >= s.lessons_per_week and pref_placed == placed:
            fully_preferred += 1
        elif placed > 0:
            partial += 1

        if pref_placed == 0:
            reason = (
                "не встал ни в один слот: ни один из его часов не вошёл в сетку"
                if placed == 0
                else "занятия только в «возможные» часы — желательные не вошли в сетку"
            )
            entry = PlanUnmatchedStudent(
                student_id=s.student_id,
                full_name=row.full_name if row else None,
                timezone=row.timezone if row else None,
                lessons_per_week=s.lessons_per_week,
                placed=placed,
                preferred_placed=pref_placed,
                preferred=[PlanHour(weekday=w, start_time=t) for w, t in s.preferred],
                possible=[PlanHour(weekday=w, start_time=t) for w, t in s.possible],
                current_hours=row.current_hours if row else [],
                reason=reason,
            )
            without_preferred.append(entry)
            if placed == 0:
                unplaced.append(entry)
        elif placed < s.lessons_per_week:
            unplaced.append(
                PlanUnmatchedStudent(
                    student_id=s.student_id,
                    full_name=row.full_name if row else None,
                    timezone=row.timezone if row else None,
                    lessons_per_week=s.lessons_per_week,
                    placed=placed,
                    preferred_placed=pref_placed,
                    preferred=[PlanHour(weekday=w, start_time=t) for w, t in s.preferred],
                    possible=[PlanHour(weekday=w, start_time=t) for w, t in s.possible],
                    current_hours=row.current_hours if row else [],
                    reason=(
                        f"поставлено {placed} занятий из {s.lessons_per_week}: "
                        "свободных подходящих часов не хватило"
                    ),
                )
            )

    moving = 0
    for s in plan_input:
        row = by_id.get(s.student_id)
        if row is None:
            continue
        planned = {h for h, _ in result.by_student.get(s.student_id, [])}
        now = {(h.weekday, h.start_time) for h in row.current_hours}
        if planned != now:
            moving += 1

    metrics = PlanMetrics(
        slots_total=len(slots),
        hours_total=len(grid_hours()),
        students_placed=sum(1 for s in plan_input if result.placed(s.student_id) > 0),
        students_total=len(plan_input),
        lessons_planned=sum(len(v) for v in result.by_student.values()),
        lessons_demand=sum(s.lessons_per_week for s in plan_input),
        fully_preferred=fully_preferred,
        partial=partial,
        without_preferred=len(without_preferred),
        unplaced=sum(1 for s in plan_input if result.placed(s.student_id) == 0),
        slots_light=sum(1 for s in slots if s.level == "light"),
        slots_ok=sum(1 for s in slots if s.level == "ok"),
        slots_crowded=sum(1 for s in slots if s.level == "crowded"),
        slots_over=sum(1 for s in slots if s.level == "over"),
        gap_count=sum(len(g.hours) for g in find_gaps(hours)),
        moving_students=moving,
    )

    return SchedulePlanPreview(
        hours=[PlanHour(weekday=w, start_time=t) for w, t in hours],
        slots=slots,
        metrics=metrics,
        gaps=find_gaps(hours),
        without_preferred=without_preferred,
        unplaced=unplaced,
    )


async def preview(
    db: AsyncSession,
    *,
    hours: Optional[list[HourKey]] = None,
    keep_existing: bool = True,
) -> SchedulePlanPreview:
    """Расчёт по набору часов; если набора нет — сервер предлагает свой."""
    students, plan_input = await load_students(db)
    current_slots = await load_current_slots(db)

    if hours is None:
        existing = [(s.weekday, s.start_time) for s in current_slots if s.in_grid]
        hours = suggest_hours(
            plan_input, existing_hours=existing, keep_existing=keep_existing
        )
    return build_preview(students, plan_input, current_slots, hours)


# ──────────────────────────────── применение ────────────────────────────────


async def apply_plan(
    db: AsyncSession,
    body: SchedulePlanApplyRequest,
    *,
    actor_id: Optional[int],
) -> SchedulePlanApplyResult:
    """Применить утверждённую сетку: создать слоты и разложить по ним учеников.

    Что здесь важно:

    - `dry_run=True` ничего не меняет и возвращает тот же отчёт. Экран всегда
      зовёт его первым: это расписание живых людей, и число затронутых должно
      быть видно ДО действия.
    - Слоты в часах, где они уже есть у этого преподавателя, **переиспользуются**:
      создавать второй слот в тот же час нельзя (сервис календаря вернёт 409),
      да и не нужно — состав просто синхронизируется.
    - Ученики, которых в плане слота нет, из него снимаются. Иначе «применить»
      означало бы «дописать», и старый переполненный состав остался бы жить.
    - Слоты вне плана по умолчанию НЕ гасятся: их видно списком, гасит их
      методист. Флагом `deactivate_missing_slots` можно попросить погасить —
      именно так выглядит переезд утренних занятий.
    - Смена числа занятий в неделю пересчитывает сумму месяца (это делает
      штатный сервис календаря, как и при ручной правке). Кого это коснётся —
      видно в отчёте заранее.
    """
    plan_hours = [(s.weekday, s.start_time) for s in body.slots]
    outside = [h for h in plan_hours if not in_grid(h)]
    if outside:
        raise DomainError(
            "В плане есть часы вне осенней сетки (Пн-Чт 12:00-19:00, "
            "Сб 09:00-14:00 МСК)",
            status_code=422,
        )
    if len(set(plan_hours)) != len(plan_hours):
        raise DomainError("Один и тот же час указан в плане дважды", status_code=422)

    over = [s for s in body.slots if len(s.student_ids) > HARD_MAX]
    if over:
        raise DomainError(
            f"В слоте не может быть больше {HARD_MAX} учеников: "
            + ", ".join(f"{s.weekday}/{s.start_time:%H:%M}" for s in over),
            status_code=422,
        )

    current_slots = await load_current_slots(db)
    teacher_slots = {
        (s.weekday, s.start_time): s
        for s in current_slots
        if s.teacher_id == body.teacher_id
    }

    warnings: list[str] = []
    outcomes: list[ApplySlotOutcome] = []
    #: Кого снимаем со слотов. Нужен поимённо, а не числом: среди снимаемых
    #: могут оказаться те, кто просто не ответил на опрос, — и это совсем
    #: другой разговор, чем «человек переехал в новый час».
    detached_ids: set[int] = set()
    attached_total = 0
    detached_total = 0
    created = reused = 0

    # Сколько занятий в неделю у человека сейчас и сколько станет — по нему
    # считаются деньги, и молча менять это нельзя.
    before_counts: dict[int, int] = {}
    for slot in current_slots:
        for sid in slot.student_ids:
            before_counts[sid] = before_counts.get(sid, 0) + 1

    after_counts: dict[int, int] = {}
    planned_hours = set(plan_hours)
    for slot in body.slots:
        for sid in slot.student_ids:
            after_counts[sid] = after_counts.get(sid, 0) + 1
    # Слоты вне плана: их состав сохраняется, если методист их не гасит.
    leftover = [
        s
        for s in current_slots
        if s.teacher_id == body.teacher_id and (s.weekday, s.start_time) not in planned_hours
    ]
    if not body.deactivate_missing_slots:
        for slot in leftover:
            for sid in slot.student_ids:
                after_counts[sid] = after_counts.get(sid, 0) + 1
    # Занятия у ДРУГИХ преподавателей эта вёрстка не трогает, но в счёт «сколько
    # у человека занятий в неделю» они входят. Забыть их — значит показать
    # методисту падение числа занятий там, где ничего не меняется, и напугать
    # его пересчётом суммы на ровном месте.
    for slot in current_slots:
        if slot.teacher_id == body.teacher_id:
            continue
        for sid in slot.student_ids:
            after_counts[sid] = after_counts.get(sid, 0) + 1

    for slot in sorted(body.slots, key=lambda s: (s.weekday, s.start_time)):
        key = (slot.weekday, slot.start_time)
        existing = teacher_slots.get(key)
        current_members = set(existing.student_ids) if existing else set()
        wanted = list(dict.fromkeys(slot.student_ids))
        to_attach = [sid for sid in wanted if sid not in current_members]
        to_detach = sorted(current_members - set(wanted))
        kept = [sid for sid in wanted if sid in current_members]

        outcome = ApplySlotOutcome(
            weekday=slot.weekday,
            start_time=slot.start_time,
            slot_id=existing.slot_id if existing else None,
            action="reuse" if existing else "create",
            attached=to_attach,
            detached=to_detach,
            kept=kept,
        )

        if not body.dry_run:
            if existing is None:
                row = await lesson_calendar_service.create_lesson_slot(
                    db,
                    teacher_id=body.teacher_id,
                    weekday=slot.weekday,
                    start_time=slot.start_time,
                    duration_minutes=GRID_SLOT_MINUTES,
                    timezone=GRID_TIMEZONE,
                    created_by=actor_id,
                    student_ids=wanted,
                )
                outcome.slot_id = row.id
            else:
                for sid in to_attach:
                    await lesson_calendar_service.add_slot_participant(
                        db, existing.slot_id, sid, added_by=actor_id
                    )
                for sid in to_detach:
                    await lesson_calendar_service.remove_slot_participant(
                        db, existing.slot_id, sid
                    )

        if existing is None:
            created += 1
        else:
            reused += 1
        attached_total += len(to_attach)
        detached_total += len(to_detach)
        detached_ids.update(to_detach)
        outcomes.append(outcome)

    deactivated: list[int] = []
    if body.deactivate_missing_slots:
        for slot in leftover:
            deactivated.append(slot.slot_id)
            detached_ids.update(slot.student_ids)
            detached_total += len(slot.student_ids)
            if not body.dry_run:
                for sid in slot.student_ids:
                    await lesson_calendar_service.remove_slot_participant(
                        db, slot.slot_id, sid
                    )
                await lesson_calendar_service.deactivate_lesson_slot(db, slot.slot_id)
    elif leftover:
        warnings.append(
            f"Вне плана остаются действующие слоты: {len(leftover)}. Пока они активны, "
            "ученики числятся и в них — погасите их вручную или повторите с галкой."
        )

    other_teachers = {
        s.slot_id for s in current_slots if s.teacher_id != body.teacher_id
    }
    if other_teachers:
        warnings.append(
            f"У других преподавателей есть активные слоты ({len(other_teachers)}). "
            "Эта вёрстка их не трогает."
        )

    students, _ = await load_students(db)
    names = {s.student_id: s.full_name for s in students}

    # Главная опасность вёрстки: раскладка видит только тех, кто ответил на
    # опрос. Все остальные из слота просто выпадают — и на отчёте это выглядит
    # как «снимем 23», без единого слова о том, что это НЕ переезд, а потеря
    # места людьми, которых никто ни о чём не спросил. Нашлось на живой
    # проверке 25.08, когда молчали 47 из 51.
    silent_ids = {s.student_id for s in students if not s.is_filled}
    detached_silent = sorted(detached_ids & silent_ids)
    if detached_silent:
        warnings.append(
            f"Внимание: {len(detached_silent)} из снимаемых со слотов не заполнили "
            "пожелания — раскладка их просто не видит. Это не переезд, а потеря "
            "места. Сперва дождитесь их ответов или добавьте таких людей в слоты "
            "руками."
        )
    changes = [
        ApplyLessonChange(
            student_id=sid,
            full_name=names.get(sid),
            before=before_counts.get(sid, 0),
            after=after_counts.get(sid, 0),
        )
        for sid in sorted(set(before_counts) | set(after_counts))
        if before_counts.get(sid, 0) != after_counts.get(sid, 0)
    ]
    if changes and not body.dry_run:
        logger.info(
            "tsk-674: применена сетка — слотов создано %s, переиспользовано %s, "
            "привязок %s, отвязок %s, у %s учеников изменилось число занятий",
            created, reused, attached_total, detached_total, len(changes),
        )

    return SchedulePlanApplyResult(
        dry_run=body.dry_run,
        slots_created=created,
        slots_reused=reused,
        slots_deactivated=deactivated,
        students_attached=attached_total,
        students_detached=detached_total,
        detached_silent=detached_silent,
        outcomes=outcomes,
        leftover_slots=leftover,
        lesson_changes=changes,
        warnings=warnings,
    )
