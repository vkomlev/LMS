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
       ) AS drill_done
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
SELECT count(*) AS n
  FROM materials m
  JOIN tree ON tree.member_course_id = m.course_id
 WHERE COALESCE(m.is_active, true) AND m.requirement_level = ANY(:levels)
   AND NOT EXISTS (
       SELECT 1 FROM student_material_progress smp
        WHERE smp.student_id = :student_id AND smp.material_id = m.id
          AND smp.status IN ('completed', 'skipped')
   )
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

    materials_left = int(
        (
            await db.execute(
                text(_MATERIALS_LEFT_SQL),
                {
                    "student_id": student_id,
                    "root_ids": root_ids,
                    "levels": _requirement_levels(),
                },
            )
        ).scalar()
        or 0
    )

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
                per_course, computed_at
            ) VALUES (
                :sid, :kind, :deadline, :pace,
                :core_total, :drill_total, :drill_allowed, :core_trimmed,
                CAST(:per_course AS jsonb), now()
            )
            ON CONFLICT (student_id, program_kind) DO UPDATE SET
                deadline = EXCLUDED.deadline,
                planned_pace = EXCLUDED.planned_pace,
                core_total = EXCLUDED.core_total,
                drill_total = EXCLUDED.drill_total,
                drill_allowed = EXCLUDED.drill_allowed,
                core_trimmed = EXCLUDED.core_trimmed,
                per_course = EXCLUDED.per_course,
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
    )


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
