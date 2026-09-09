"""Сколько минут занимает задание — измеренный вес элемента программы (tsk-851).

Зачем модуль существует. Объём домашней работы и прогноз окончания курса
считаются в ШТУКАХ элементов (`homework_volume_service`), а элементы стали
разновесными. «Двадцать заданий» — это пять минут дома, если это выбор ответа,
и полтора часа, если это задачи с решением. Норма в штуках одинаково врёт в обе
стороны: одному ученику она даёт вечер работы, другому — три минуты.

**Вес НЕ берётся из справочника сложности, и это проверено на боевых данных.**
Колонка `difficulties.weight` (THEORY 1 … PROJECT 5) выглядит готовым ответом,
но с фактическим временем не совпадает: медиана реального времени «открыл →
ответил» за 90 дней — THEORY 19 с, EASY 54 с, **NORMAL 35 с**, HARD 82 с,
PROJECT 406 с. NORMAL решается быстрее EASY, а разрыв между крайними уровнями
не пятикратный, а двадцатикратный.

**Главный множитель — ФОРМАТ задания, а не его сложность** (тот же вывод, что
в tsk-846 про темп темы). Матрица медиан по проду:

    сложность   выбор одного   короткий ответ   задача с решением   таблица
    THEORY           —              9 с              186 с           198 с
    EASY            12 с           17 с              236 с           334 с
    NORMAL          14 с           43 с              365 с           500 с
    HARD            14 с           30 с              516 с           555 с

«Сложное» с выбором ответа занимает 14 секунд — ровно столько же, сколько
лёгкое с выбором. А «лёгкая» задача с решением — 236 секунд, в семнадцать раз
дольше «сложного» выбора. Зато ВНУТРИ одного формата сложность работает
честно: задача с решением идёт 186 → 236 → 365 → 516 с по мере роста уровня.
Поэтому единица веса — пара (сложность, формат), а не что-то одно из двух.

**Что этот вес есть и чего в нём нет.** Это медиана времени от показа формы
ответа до отправки, по реальным ученическим сдачам. В него не входит чтение
теории перед заданием и не входят повторные попытки после неверного ответа —
то есть он занижает настоящие затраты и годится как ЕДИНИЦА СРАВНЕНИЯ, а не
как обещание ребёнку «уложишься в сорок минут».

Потребитель — расчёт объёма ДЗ и прогноза (`homework_volume_service`,
`program_scope_service`). Этот модуль ничего не решает сам: он только меряет.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.learning_gaps_service import real_student_results_filter
from app.services.topic_mastery_service import PACE_OUTLIER_CAP_SECONDS

logger = logging.getLogger(__name__)

#: Окно наблюдения. Совпадает с окном экрана освоения тем: вес и темп меряются
#: одними и теми же событиями, и расходиться окнами им незачем.
EFFORT_WINDOW_DAYS = 90

#: Сколько наблюдений нужно ячейке (сложность × формат), чтобы стать весом.
#: Двадцать — та же планка, по которой считается достоверность выборки у
#: датчика пробелов (`MIN_SUBMISSIONS`). На проде её проходят ячейки, покрывающие
#: 6509 активных заданий из 6610, то есть 98 % каталога: вес получает и то
#: задание, которого никто ещё не открывал, — а именно такие и составляют
#: остаток программы, ради которого считается прогноз.
MIN_CELL_SAMPLES = 20

#: Вес материала (теории) — ПРОКСИ, а не измерение, и путать их нельзя.
#: События «материал открыт» в системе нет: у `student_material_progress` есть
#: только отметка о прохождении, причём чаще её ставит преподаватель пачкой.
#: 49 секунд — медиана промежутка между соседними отметками у реальных
#: прохождений за 90 дней; в неё входит и переход между элементами. Цифра нужна
#: потому, что в норму ДЗ материалы входят наравне с заданиями (решение
#: оператора: «теорию учат дома»), и без неё бюджет времени неполон.
MATERIAL_EFFORT_SECONDS_PROXY = 49.0

# Ближайшее ПЕРЕД сдачей событие `task_opened` — тот же приём, что в
# `topic_mastery_service` (tsk-578): не первое открытие задания вообще, а
# последнее перед ЭТОЙ сдачей, иначе возврат после перерыва превратится в
# «думал со вчерашнего дня». Пары без события в выборку не попадают.
_EFFORT_SQL = """
WITH real_subs AS (
    SELECT tr.user_id, tr.task_id, tr.received_at,
           t.difficulty_id, t.task_content->>'type' AS task_type
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id AND t.is_active
    WHERE {real_student}
      AND tr.received_at > now() - make_interval(days => :days)
),
observed AS (
    SELECT rs.difficulty_id, rs.task_type,
           EXTRACT(EPOCH FROM (rs.received_at - opened.opened_at)) AS seconds
    FROM real_subs rs
    CROSS JOIN LATERAL (
        SELECT le.created_at AS opened_at
        FROM learning_events le
        WHERE le.event_type = 'task_opened'
          AND le.student_id = rs.user_id
          AND (le.payload->>'task_id')::int = rs.task_id
          AND le.created_at <= rs.received_at
        ORDER BY le.created_at DESC
        LIMIT 1
    ) opened
),
capped AS (
    SELECT * FROM observed WHERE seconds < :cap
)
SELECT difficulty_id, task_type, COUNT(*) AS samples,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY seconds) AS median_seconds,
       -- Уровень разреза берётся у GROUPING, а не из «поле пусто»: у задания
       -- сложность и формат ДЕЙСТВИТЕЛЬНО бывают пустыми, и такая строка
       -- неотличима от итоговой по значению. Спутать их — значит принять вес
       -- одной ячейки за общую медиану всей платформы.
       GROUPING(difficulty_id) AS grp_difficulty,
       GROUPING(task_type) AS grp_type
FROM capped
GROUP BY GROUPING SETS ((difficulty_id, task_type), (task_type), ())
"""


@dataclass(frozen=True)
class EffortTable:
    """Таблица весов: сколько секунд занимает задание того или иного рода.

    Три уровня, и порядок обращения к ним не случаен: сперва пара (сложность,
    формат), затем ОДИН ФОРМАТ, и только потом общая медиана. Второй ступенью
    стоит формат, а не сложность, потому что на боевых данных именно он
    определяет время: «сложное» с выбором ответа занимает столько же, сколько
    лёгкое с выбором.
    """

    by_cell: dict[tuple[Optional[int], Optional[str]], float]
    by_type: dict[Optional[str], float]
    overall: Optional[float]
    window_days: int
    samples: int

    def seconds_for(
        self, *, difficulty_id: Optional[int], task_type: Optional[str],
    ) -> Optional[float]:
        """Вес одного задания в секундах. `None` — мерить нечем.

        `None` возвращается честно: пустая база (новая установка, окно без
        сдач) не должна превращаться в выдуманное число, иначе объём ДЗ будет
        посчитан по норме, которой никто не мерил.
        """
        cell = self.by_cell.get((difficulty_id, task_type))
        if cell is not None:
            return cell
        by_type = self.by_type.get(task_type)
        if by_type is not None:
            return by_type
        return self.overall

    def minutes_for(
        self, *, difficulty_id: Optional[int], task_type: Optional[str],
    ) -> Optional[float]:
        seconds = self.seconds_for(difficulty_id=difficulty_id, task_type=task_type)
        return None if seconds is None else seconds / 60


async def load_effort_table(
    db: AsyncSession, *, days: int = EFFORT_WINDOW_DAYS,
) -> EffortTable:
    """Снять таблицу весов по телеметрии за окно.

    Один запрос на все три уровня: `GROUPING SETS` считает разрезы
    (сложность, формат), (формат) и общий за один проход по данным. Отдельными
    запросами это стоило бы трёх проходов с LATERAL по каждой сдаче — самой
    дорогой части расчёта (замер в tsk-846: такой проход занимает секунды, а не
    миллисекунды).
    """
    rows = (await db.execute(text(
        _EFFORT_SQL.format(real_student=real_student_results_filter("tr")),
    ), {"days": days, "cap": PACE_OUTLIER_CAP_SECONDS})).mappings().all()

    by_cell: dict[tuple[Optional[int], Optional[str]], float] = {}
    by_type: dict[Optional[str], float] = {}
    overall: Optional[float] = None
    total = 0
    for r in rows:
        samples = int(r["samples"])
        median = None if r["median_seconds"] is None else float(r["median_seconds"])
        if median is None:
            continue
        grouped_difficulty = int(r["grp_difficulty"]) == 1
        grouped_type = int(r["grp_type"]) == 1
        if not grouped_difficulty and not grouped_type:
            if samples >= MIN_CELL_SAMPLES:
                difficulty_id = r["difficulty_id"]
                by_cell[(
                    None if difficulty_id is None else int(difficulty_id),
                    r["task_type"],
                )] = median
        elif not grouped_type:
            if samples >= MIN_CELL_SAMPLES:
                by_type[r["task_type"]] = median
        else:
            overall = median
            total = samples

    logger.info(
        "вес заданий: ячеек %s, форматов %s, наблюдений %s за %s дн.",
        len(by_cell), len(by_type), total, days,
    )
    return EffortTable(
        by_cell=by_cell, by_type=by_type, overall=overall,
        window_days=days, samples=total,
    )


_TASK_ROWS_SQL = """
SELECT id, difficulty_id, task_content->>'type' AS task_type
FROM tasks WHERE id = ANY(:task_ids)
"""


async def effort_for_tasks(
    db: AsyncSession,
    *,
    task_ids: list[int],
    table: Optional[EffortTable] = None,
    days: int = EFFORT_WINDOW_DAYS,
) -> dict[int, Optional[float]]:
    """Вес каждого из перечисленных заданий в секундах.

    `table` передаётся снаружи, когда заданий много и таблица уже снята: за
    один расчёт объёма ДЗ её нужно снимать ОДИН раз, а не на каждое задание.
    Задание, которого нет в базе, в ответ не попадает вовсе — молча подставлять
    ему средний вес значило бы прятать ошибку вызывающего.
    """
    if not task_ids:
        return {}
    effort = table or await load_effort_table(db, days=days)
    rows = (await db.execute(
        text(_TASK_ROWS_SQL), {"task_ids": list(task_ids)},
    )).mappings().all()
    return {
        int(r["id"]): effort.seconds_for(
            difficulty_id=r["difficulty_id"], task_type=r["task_type"],
        )
        for r in rows
    }


__all__ = [
    "EFFORT_WINDOW_DAYS",
    "MATERIAL_EFFORT_SECONDS_PROXY",
    "MIN_CELL_SAMPLES",
    "EffortTable",
    "effort_for_tasks",
    "load_effort_table",
]
