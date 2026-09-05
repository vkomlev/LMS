"""Сколько программы подготовки помещается ученику в срок (tsk-798).

Задача [[tsk-797]] показала разрыв: программа ЕГЭ — 1439 обязательных
элементов, и к 31 марта она не помещается у 58 учеников из 76. Показать разрыв
мало, его надо закрывать — сокращая объём программы под срок и темп конкретного
человека. Требование оператора 05.09: «не ослабить нагрузку сильно, но чтобы
человек успевал пройти материал».

**Программа делится на две части, и только одна сокращается.**

Замер боевой базы 05.09 по курсам 88 + 112:

- **ядро, 605 элементов** — теория (310 заданий), сложные и проектные (32),
  все материалы (263). Проходится целиком: это разбор всех номеров ЕГЭ, и
  выбросить оттуда что-то значит не пройти номер вовсе;
- **тренажёр, 834 задания** — EASY и NORMAL. Это отработка уже разобранного,
  и вот её объём можно подобрать под человека.

Такое деление не выдумано здесь: механика выборки [[tsk-314]] с самого начала
не трогает THEORY/HARD/PROJECT — этот сервис только решает, СКОЛЬКО тренажёра
брать, а сам отбор делает `task_sampling`.

**Бюджет = темп × недели.** Темп — не сырой факт ученика: у пришедшего вчера
он ноль, и по нулю нельзя планировать год. Берётся большее из двух: базовое
ожидание школы (настройка `homework_program_planned_pace`) и фактический темп
человека, если он выше. Второе — и есть «поднимать тем, кто тянет»: ученик с
40 в неделю получает программу под 40, а не под средние 25.

**Ядро режется только когда иначе никак.** Ученику, стартующему 1 ноября, до
31 марта остаётся 21.4 недели: при темпе 25 бюджет 535 против 605 ядра. Это не
экзотика марта, а обычный ноябрь, поэтому признак `core_trimmed` поднимается
рано и громко — преподаватель должен узнать об этом от системы, а не заметить
через месяц. Само сокращение ядра по номерам ЕГЭ — отдельный шаг задачи:
здесь только честно сказано, что бюджета не хватило.

**Порог только растёт.** `per_course` пересчитывается максимумом со старым
значением: механика выборки даёт вложенные наборы, поэтому рост порога
добавляет задания к уже выданным. Уменьшение означало бы, что у человека
пропала часть программы, в том числе решённая.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _requirement_levels() -> list[str]:
    """«Что входит в программу» — одно правило на весь проект, взятое функцией.

    Импорт ленивый, а не на уровне модуля: `manual_progress_service` тянет за
    собой движок обучения, а движок импортирует этот модуль ради персональных
    порогов — на уровне модуля вышло бы кольцо. Копировать список сюда нельзя:
    разъехавшись, «что обязательно» в планировании и в обходе стали бы двумя
    разными ответами на один вопрос.
    """
    from app.services.manual_progress_service import REQUIREMENT_LEVELS

    return list(REQUIREMENT_LEVELS)

#: Сложности, которые выборке не подлежат ни при каких условиях. Тот же список,
#: что в движке (`learning_engine_service`): сокращается только EASY/NORMAL.
CORE_DIFFICULTIES = ("THEORY", "HARD", "PROJECT")
#: Сложности тренажёра — их объём и подбирается под ученика.
DRILL_DIFFICULTIES = ("EASY", "NORMAL")

#: Минимальная доля тренажёра, ниже которой выборку не включаем вовсе. Оставить
#: человеку 2% отработки — не «щадящий режим», а курс без практики; лучше
#: честно показать, что программа не помещается, и решать это по-другому.
MIN_DRILL_RATIO = 0.05


@dataclass(frozen=True)
class ProgramScope:
    """Объём программы, помещающийся ученику в срок."""

    #: `ege` или `oge`.
    kind: str
    #: К какому дню программу нужно закончить.
    deadline: date
    #: Недель до срока; может быть дробным и нулевым (срок прошёл).
    weeks_left: float
    #: Темп, на который рассчитан план.
    planned_pace: int
    #: Несокращаемых элементов в программе.
    core_total: int
    #: Заданий тренажёра всего.
    drill_total: int
    #: Сколько тренажёра помещается в срок.
    drill_allowed: int
    #: Бюджета не хватило даже на ядро.
    core_trimmed: bool
    #: `{course_id: порог выборки}` — бюджет, разложенный по подкурсам.
    per_course: dict[int, int]
    #: Подкурсы (номера ЕГЭ), выпавшие из программы целиком. Пусто — ничего не
    #: выброшено: либо ядро поместилось, либо приоритеты не размечены.
    excluded_courses: frozenset[int] = frozenset()

    @property
    def drill_ratio(self) -> float:
        """Какая доля тренажёра достаётся ученику (0..1)."""
        if self.drill_total <= 0:
            return 1.0
        return min(self.drill_allowed / self.drill_total, 1.0)

    @property
    def fits_fully(self) -> bool:
        """Программа помещается целиком — сокращать нечего."""
        return self.drill_allowed >= self.drill_total and not self.core_trimmed


#: Размер программы по подкурсам: сколько в каждом ядра и сколько тренажёра.
#: Считается по НЕПРОЙДЕННЫМ элементам конкретного ученика — планировать надо
#: остаток, а не курс целиком: у пришедшего в ноябре и у идущего с сентября
#: остаток разный, и это ровно то, что должно влиять на объём.
_SCOPE_SQL = f"""
WITH RECURSIVE tree AS (
    SELECT unnest(CAST(:root_ids AS int[])) AS member_course_id
    UNION
    SELECT cp.course_id
      FROM tree t
      JOIN course_parents cp ON cp.parent_course_id = t.member_course_id
),
course_tasks AS (
    SELECT DISTINCT t.id, t.course_id, COALESCE(d.code, 'NORMAL') AS code
      FROM tasks t
      JOIN tree ON tree.member_course_id = t.course_id
      LEFT JOIN difficulties d ON d.id = t.difficulty_id
     WHERE COALESCE(t.is_active, true) AND t.requirement_level = ANY(:levels)
),
tasks_done AS (
    SELECT DISTINCT tr.task_id AS id
      FROM task_results tr
      JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
     WHERE tr.user_id = :student_id AND tr.is_correct = true
    UNION
    SELECT stp.task_id
      FROM student_task_progress stp
     WHERE stp.student_id = :student_id AND stp.status = 'skipped'
)
SELECT ct.course_id,
       (SELECT c.program_priority FROM courses c WHERE c.id = ct.course_id)
           AS program_priority,
       count(*) FILTER (
           WHERE ct.code = ANY(:core_codes)
             AND ct.id NOT IN (SELECT id FROM tasks_done)
       ) AS core_left,
       count(*) FILTER (
           WHERE ct.code = ANY(:drill_codes)
             AND ct.id NOT IN (SELECT id FROM tasks_done)
       ) AS drill_left,
       count(*) FILTER (
           WHERE ct.code = ANY(:drill_codes)
             AND ct.id IN (SELECT id FROM tasks_done)
       ) AS drill_done,
       count(*) FILTER (WHERE ct.id IN (SELECT id FROM tasks_done)) AS done_any
  FROM course_tasks ct
 GROUP BY ct.course_id
"""

#: Непройденных материалов программы. Отдельным запросом, а не колонкой в
#: `_SCOPE_SQL`: там группировка по подкурсу заданий, и на ученике, решившем
#: все задания, не осталось бы ни одной строки — вместе с ней потерялись бы и
#: материалы, то есть программа выглядела бы пройденной при непрочитанной
#: теории.
_MATERIALS_LEFT_SQL = f"""
WITH RECURSIVE tree AS (
    SELECT unnest(CAST(:root_ids AS int[])) AS member_course_id
    UNION
    SELECT cp.course_id
      FROM tree t
      JOIN course_parents cp ON cp.parent_course_id = t.member_course_id
)
SELECT m.course_id, count(*) AS n
  FROM materials m
  JOIN tree ON tree.member_course_id = m.course_id
 WHERE COALESCE(m.is_active, true) AND m.requirement_level = ANY(:levels)
   AND NOT EXISTS (
       SELECT 1 FROM student_material_progress smp
        WHERE smp.student_id = :student_id AND smp.material_id = m.id
          AND smp.status IN ('completed', 'skipped')
   )
 GROUP BY m.course_id
"""


def planned_pace_for(fact_per_week: float) -> int:
    """Темп, на который планируем объём программы.

    Большее из двух: базовое ожидание школы и фактический темп ученика с
    обычным шагом роста. По сырому факту планировать нельзя — у пришедшего
    вчера он ноль, и любой план от нуля даст пустую программу.
    """
    from app.core import settings_store
    from app.services.homework_volume_service import GROWTH_FACTOR

    base = settings_store.get_int("homework_program_planned_pace")
    return max(base, int(round(fact_per_week * GROWTH_FACTOR)))


def _split_budget(
    per_course_drill: dict[int, int], drill_allowed: int
) -> dict[int, int]:
    """Разложить бюджет тренажёра по подкурсам пропорционально их размеру.

    Пропорционально, а не поровну: в подкурсе «Задание 7» 72 обязательных
    задания, в «Задании 12» — 15, и равный порог оставил бы первый почти
    нетронутым, а второй выдал бы целиком. Остаток от округления отдаётся
    самым большим подкурсам — там он теряется незаметнее всего.
    """
    total = sum(per_course_drill.values())
    if total <= 0 or drill_allowed >= total:
        return dict(per_course_drill)

    share = drill_allowed / total
    result = {cid: int(n * share) for cid, n in per_course_drill.items()}

    # Остаток раздаём по убыванию размера подкурса — детерминированно, с
    # разрешением ничьей по id, иначе порог плавал бы между пересчётами.
    leftover = drill_allowed - sum(result.values())
    for cid, _ in sorted(
        per_course_drill.items(), key=lambda kv: (-kv[1], kv[0])
    ):
        if leftover <= 0:
            break
        if result[cid] < per_course_drill[cid]:
            result[cid] += 1
            leftover -= 1
    return result


def _trim_core(
    core_by_course: dict[int, int],
    priority_by_course: dict[int, Optional[int]],
    budget: int,
    started: Optional[set[int]] = None,
) -> tuple[set[int], int]:
    """Какие подкурсы выпадают, когда бюджета не хватает даже на ядро.

    Решение оператора 05.09: резать **по номерам ЕГЭ** — выбрасывать номер
    целиком, а не куски из каждого. Половина разбора каждого номера не готовит
    ни к одному из них; целый номер, пройденный до конца, даёт балл.

    Подкурсы берутся по возрастанию `program_priority`, пока помещаются в
    бюджет. Не выпадают никогда две группы:

    * **неразмеченные** (`priority is None`) — NULL означает «методист сюда не
      смотрел», и выбросить у выпускника разбор номера по догадке хуже, чем
      показать преподавателю, что программа не помещается;
    * **начатые** (`started`) — отнять номер, в котором человек уже что-то
      решил, значит обесценить сделанную работу. Темп меняется по ходу года, и
      без этого правила номер выпадал бы у того, кто просто сбавил на неделю.

    Args:
        core_by_course: сколько несокращаемых элементов в каждом подкурсе.
        priority_by_course: приоритет подкурса; None — не размечен.
        budget: сколько элементов ученик успевает всего.
        started: подкурсы, где у ученика уже есть пройденное.

    Returns:
        `(что выбросить, сколько ядра осталось)`.
    """
    protected = {cid for cid, p in priority_by_course.items() if p is None}
    protected |= set(started or ()) & set(core_by_course)
    protected_size = sum(core_by_course.get(cid, 0) for cid in protected)

    # Размеченные — кандидаты на выбывание, по возрастанию приоритета.
    ranked = sorted(
        (cid for cid in core_by_course if cid not in protected),
        key=lambda cid: (priority_by_course[cid], cid),
    )

    kept_size = protected_size
    excluded: set[int] = set()
    for cid in ranked:
        size = core_by_course.get(cid, 0)
        if kept_size + size <= budget:
            kept_size += size
        else:
            # Не `break`: следующий номер может оказаться меньше и поместиться.
            # Выбрасывать заодно и его только потому, что не влез предыдущий, —
            # значит терять то, что ученик успел бы пройти.
            excluded.add(cid)
    return excluded, kept_size


async def compute_scope(
    db: AsyncSession,
    *,
    student_id: int,
    kind: str,
    root_ids: list[int],
    deadline: date,
    fact_per_week: float,
    today: Optional[date] = None,
) -> ProgramScope:
    """Посчитать, что из программы помещается ученику в срок.

    Только чтение: ничего не сохраняет — записью занимается `store_scope`.
    Отдельная функция затем, чтобы план можно было показать и проверить, не
    меняя ничего у ученика.

    Args:
        db: async session.
        student_id: ID ученика.
        kind: `ege` или `oge`.
        root_ids: корневые курсы программы.
        deadline: к какому дню программу нужно закончить.
        fact_per_week: фактический недельный темп ученика.
        today: дата расчёта; None — сегодня.

    Returns:
        `ProgramScope` — объём и всё, из чего он сложился.
    """
    moment = today or date.today()
    rows = (
        await db.execute(
            text(_SCOPE_SQL),
            {
                "student_id": student_id,
                "root_ids": root_ids,
                "levels": _requirement_levels(),
                "core_codes": list(CORE_DIFFICULTIES),
                "drill_codes": list(DRILL_DIFFICULTIES),
            },
        )
    ).mappings().all()

    materials_by_course = {
        int(r["course_id"]): int(r["n"])
        for r in (
            await db.execute(
                text(_MATERIALS_LEFT_SQL),
                {
                    "student_id": student_id,
                    "root_ids": root_ids,
                    "levels": _requirement_levels(),
                },
            )
        ).mappings()
    }
    materials_left = sum(materials_by_course.values())

    per_course_drill = {
        int(r["course_id"]): int(r["drill_left"])
        for r in rows
        if int(r["drill_left"]) > 0
    }
    core_tasks_left = sum(int(r["core_left"]) for r in rows)
    core_total = core_tasks_left + materials_left
    drill_total = sum(per_course_drill.values())

    weeks_left = max((deadline - moment).days, 0) / 7.0
    planned_pace = planned_pace_for(fact_per_week)
    budget = int(planned_pace * weeks_left)

    core_trimmed = budget < core_total
    excluded: set[int] = set()
    if core_trimmed:
        # Ядро не помещается — режем его ПО НОМЕРАМ (решение оператора 05.09).
        # Материалы подкурса считаются вместе с его заданиями: выбрасывая
        # номер, выбрасываем и его теорию, иначе ученик получил бы разбор
        # темы, задания по которой ему всё равно не покажут.
        core_by_course = {
            int(r["course_id"]): int(r["core_left"])
            + materials_by_course.get(int(r["course_id"]), 0)
            for r in rows
        }
        for cid, n in materials_by_course.items():
            core_by_course.setdefault(cid, n)
        priority_by_course = {
            int(r["course_id"]): (
                int(r["program_priority"])
                if r["program_priority"] is not None
                else None
            )
            for r in rows
        }
        for cid in core_by_course:
            priority_by_course.setdefault(cid, None)

        started = {
            int(r["course_id"]) for r in rows if int(r["done_any"]) > 0
        }
        excluded, core_total = _trim_core(
            core_by_course, priority_by_course, budget, started=started
        )
        # Выпавшие подкурсы уходят из программы целиком — вместе со своим
        # тренажёром: задавать отработку по номеру, который не проходим, незачем.
        for cid in excluded:
            per_course_drill.pop(cid, None)
        drill_total = sum(per_course_drill.values())
        # Признак остаётся поднятым, даже если после резки всё поместилось:
        # преподаватель обязан знать, что программа этого ученика короче.

    drill_allowed = max(budget - core_total, 0)

    # Совсем тонкий слой отработки бесполезен: курс без практики — это не
    # облегчённая программа, а другая. Ниже порога выборку не включаем вовсе,
    # а честно поднимаем core_trimmed: разговор тут не про объём тренажёра.
    if drill_total > 0 and 0 < drill_allowed < drill_total * MIN_DRILL_RATIO:
        drill_allowed = int(drill_total * MIN_DRILL_RATIO)

    allowed = min(drill_allowed, drill_total)
    # Порог, который получит движок, — ПОЛНЫЙ размер выборки подкурса, вместе с
    # уже решёнными заданиями: сэмплер трактует его именно так. Считаем же мы
    # бюджет по остатку, поэтому решённые прибавляются здесь. Не прибавь их —
    # ученик, прошедший половину тренажёра, получил бы порог меньше того, что
    # уже сделал, и часть его работы выпала бы из программы.
    drill_done = {int(r["course_id"]): int(r["drill_done"]) for r in rows}
    per_course = {
        cid: threshold + drill_done.get(cid, 0)
        for cid, threshold in _split_budget(per_course_drill, allowed).items()
    }

    return ProgramScope(
        kind=kind,
        deadline=deadline,
        weeks_left=round(weeks_left, 1),
        planned_pace=planned_pace,
        core_total=core_total,
        drill_total=drill_total,
        drill_allowed=allowed,
        core_trimmed=core_trimmed,
        per_course=per_course,
        excluded_courses=frozenset(excluded),
    )


async def store_scope(
    db: AsyncSession, *, student_id: int, scope: ProgramScope
) -> dict[int, int]:
    """Сохранить план, не давая порогам уменьшиться.

    Порог по каждому подкурсу берётся максимумом со старым значением. Механика
    выборки даёт вложенные наборы, поэтому рост порога только добавляет
    задания; уменьшение выбросило бы из программы часть уже решённого.

    Не коммитит: вызывающий решает, когда закрывать транзакцию.

    Returns:
        Итоговые пороги по подкурсам — уже с учётом прежних значений.
    """
    previous = (
        await db.execute(
            text(
                "SELECT per_course FROM student_program_scope "
                " WHERE student_id = :sid AND program_kind = :kind"
            ),
            {"sid": student_id, "kind": scope.kind},
        )
    ).scalar()

    merged = dict(scope.per_course)
    if isinstance(previous, dict):
        for raw_cid, raw_value in previous.items():
            try:
                cid, value = int(raw_cid), int(raw_value)
            except (TypeError, ValueError):
                logger.warning(
                    "tsk-798: битый per_course у ученика %s: %r -> %r",
                    student_id, raw_cid, raw_value,
                )
                continue
            merged[cid] = max(merged.get(cid, 0), value)

    await db.execute(
        text(
            """
            INSERT INTO student_program_scope (
                student_id, program_kind, deadline, planned_pace,
                core_total, drill_total, drill_allowed, core_trimmed,
                per_course, excluded_courses, computed_at
            ) VALUES (
                :sid, :kind, :deadline, :pace,
                :core_total, :drill_total, :drill_allowed, :core_trimmed,
                CAST(:per_course AS jsonb), CAST(:excluded AS jsonb), now()
            )
            ON CONFLICT (student_id, program_kind) DO UPDATE SET
                deadline = EXCLUDED.deadline,
                planned_pace = EXCLUDED.planned_pace,
                core_total = EXCLUDED.core_total,
                drill_total = EXCLUDED.drill_total,
                drill_allowed = EXCLUDED.drill_allowed,
                core_trimmed = EXCLUDED.core_trimmed,
                per_course = EXCLUDED.per_course,
                excluded_courses = EXCLUDED.excluded_courses,
                computed_at = now()
            """
        ),
        {
            "sid": student_id,
            "kind": scope.kind,
            "deadline": scope.deadline,
            "pace": scope.planned_pace,
            "core_total": scope.core_total,
            "drill_total": scope.drill_total,
            "drill_allowed": scope.drill_allowed,
            "core_trimmed": scope.core_trimmed,
            "per_course": _json_keys(merged),
            "excluded": json.dumps(sorted(scope.excluded_courses)),
        },
    )
    return merged


def _json_keys(per_course: dict[int, int]) -> str:
    """`{138: 12}` → `'{"138": 12}'` — ключи JSON обязаны быть строками."""
    import json

    return json.dumps({str(k): int(v) for k, v in per_course.items()})


async def refresh_for_student(
    db: AsyncSession, *, student_id: int, now: Optional[datetime] = None
) -> Optional[ProgramScope]:
    """Пересчитать и сохранить план ученика. `None` — он вне программ подготовки.

    Зовётся при выдаче домашней работы и еженедельным пересчётом, а не из
    показа сводки: показ обязан оставаться чтением, иначе просмотр карточки
    ученика молча менял бы состав его программы.

    Не коммитит — вызывающий закрывает транзакцию сам.
    """
    from app.services import homework_volume_service as volume

    moment = now or datetime.now(timezone.utc)
    plan = await volume.compute(db, student_id=student_id, now=moment)
    if plan.program_kind is None or plan.program_deadline is None:
        return None

    grade = plan.grade
    program = await volume.program_for_student(
        db, student_id=student_id, grade=grade, today=moment.date()
    )
    if program is None:
        return None

    scope = await compute_scope(
        db,
        student_id=student_id,
        kind=program["kind"],
        root_ids=program["root_ids"],
        deadline=program["deadline"],
        fact_per_week=plan.fact_per_week,
        today=moment.date(),
    )
    merged = await store_scope(db, student_id=student_id, scope=scope)
    logger.info(
        "tsk-798: план ученика %s (%s): ядро %s, тренажёр %s из %s, темп %s%s",
        student_id, scope.kind, scope.core_total, scope.drill_allowed,
        scope.drill_total, scope.planned_pace,
        ", ядро не помещается" if scope.core_trimmed else "",
    )
    return ProgramScope(
        kind=scope.kind,
        deadline=scope.deadline,
        weeks_left=scope.weeks_left,
        planned_pace=scope.planned_pace,
        core_total=scope.core_total,
        drill_total=scope.drill_total,
        drill_allowed=scope.drill_allowed,
        core_trimmed=scope.core_trimmed,
        per_course=merged,
        excluded_courses=scope.excluded_courses,
    )


async def excluded_courses_for(
    db: AsyncSession, *, student_id: int
) -> frozenset[int]:
    """Подкурсы, выпавшие из программы ученика (tsk-798).

    Пустое множество — ничего не выброшено: ядро поместилось, приоритеты не
    размечены или плана нет вовсе. Движок в этом случае ведёт себя как раньше.

    Ученик может держать план и по ЕГЭ, и по ОГЭ (подкурсы у них общие) —
    берётся ПЕРЕСЕЧЕНИЕ: номер, нужный хотя бы одной из его программ, выпасть
    не может.
    """
    rows = (
        await db.execute(
            text(
                "SELECT excluded_courses FROM student_program_scope "
                " WHERE student_id = :sid"
            ),
            {"sid": student_id},
        )
    ).scalars().all()

    sets: list[set[int]] = []
    for raw in rows:
        if not isinstance(raw, list):
            continue
        current: set[int] = set()
        for cid in raw:
            try:
                current.add(int(cid))
            except (TypeError, ValueError):
                logger.warning(
                    "tsk-798: битый excluded_courses у ученика %s: %r",
                    student_id, cid,
                )
        sets.append(current)

    if not sets:
        return frozenset()
    result = sets[0]
    for other in sets[1:]:
        result &= other
    return frozenset(result)


async def thresholds_for(
    db: AsyncSession, *, student_id: int
) -> dict[int, int]:
    """Сохранённые пороги выборки ученика по подкурсам.

    Пустой словарь — плана нет (ученик вне программ подготовки либо план ещё
    не считался). Движок в этом случае работает по-старому: порог берётся из
    настройки курса, а если её нет — выборки не происходит вовсе.
    """
    rows = (
        await db.execute(
            text(
                "SELECT per_course FROM student_program_scope "
                " WHERE student_id = :sid"
            ),
            {"sid": student_id},
        )
    ).scalars().all()

    result: dict[int, int] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        for raw_cid, raw_value in raw.items():
            try:
                cid, value = int(raw_cid), int(raw_value)
            except (TypeError, ValueError):
                continue
            # Ученик может держать план и по ЕГЭ, и по ОГЭ (подкурсы общие) —
            # берём больший порог: программа не должна сужаться оттого, что
            # человека записали ещё на одну подготовку.
            result[cid] = max(result.get(cid, 0), value)
    return result
