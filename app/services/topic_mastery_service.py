"""Освоение тем: картина по ВСЕМ темам для методиста (tsk-577).

Отличие от `learning_gaps_service` — в адресате и в том, что он решает. Тот
отбирает темы ВЫШЕ порога и отдаёт их как заявку на мини-курс конкретным
ученикам. Этот показывает все темы подряд, включая благополучные: методист
правит не ученика, а материал, и ему нужно видеть в том числе тему, которую
все проходят без единой ошибки за двенадцать секунд, — это тоже дефект
контента, только противоположный.

**Источник данных тот же и другим быть не может.** Фильтр реальных ученических
сдач берётся из `learning_gaps_service.real_student_results_filter`, а не
переписывается здесь: на проде 11 643 строки `task_results` из 13 795 — ручная
простановка преподавателя с нулём ошибок, и любая метрика мимо фильтра врёт
примерно вшестеро в сторону благополучия.

**Малая выборка не скрывается, а помечается.** Живой прогон по проду
(2026-08-07, окно 90 дней): тем со сдачами 115, из них проходят пороги
`MIN_SUBMISSIONS`/`MIN_STUDENTS` всего 8. Порог, отсекающий остальные 107,
превратил бы обзор в тот же экран «Повторение». Поэтому пороги здесь работают
как признак `reliable`, а не как условие отбора.

**Темп: реальное событие с прокси-фолбэком (tsk-578).** LMS пишет `task_opened`
в `learning_events` при каждом `start-or-get-attempt` (показ формы ответа) —
темп темы/задания считается как медиана реального времени «открыл → сдал» по
ближайшей ПЕРЕД сдачей паре событий. Пока таких пар у темы/задания меньше
`MIN_REAL_PACE_SAMPLES`, используется прежний прокси: медиана промежутка между
последовательными сдачами одного ученика внутри темы (промежутки длиннее часа
выброшены — перерыв между занятиями, не размышление). На момент деплоя
телеметрии реальных пар — 0 у всех тем, поведение экрана не меняется; переход
происходит постепенно и виден в ответе API полем `pace_source` ("real" |
"proxy" | null). Пороги `FAST_PACE_SECONDS`/`SLOW_PACE_SECONDS` пока общие для
обоих источников и откалиброваны под прокси — пересчёт на живых данных
реального сигнала является отдельным follow-up после накопления телеметрии.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.learning_gaps_service import (
    ERROR_RATE_THRESHOLD,
    MIN_STUDENTS,
    MIN_SUBMISSIONS,
    real_student_results_filter,
)
from app.utils.task_title import humanize_task_title

logger = logging.getLogger(__name__)

# Промежуток длиннее часа — это не «долго думал», а перерыв между занятиями.
# Считать его временем решения значит объявить медленной любую тему, к которой
# ученик возвращался на следующий день.
PACE_OUTLIER_CAP_SECONDS = 3600

# Доля неверных, ниже которой тема подозрительно лёгкая. Не ноль: одна случайная
# опечатка на сорок сдач не делает тему требующей размышления.
EASY_WRONG_RATE = 0.05

# Пороги темпа. Опорные цифры с прода (окно 90 дней, 1 923 промежутка):
# медиана 26 с, десятый процентиль 7 с, девяностый 386 с. Пороги откалиброваны
# под ПРОКСИ (промежуток между соседними сдачами) и временно наследуются
# реальным сигналом (tsk-578, см. MIN_REAL_PACE_SAMPLES ниже) — единица
# измерения та же (секунды от показа задания до сдачи), а прокси
# систематически не меньше реального времени: он включает ещё и переход между
# заданиями. Значит настоящий порог, вероятно, чуть ниже нынешнего, но не
# выше. Пересчёт на живых данных реального сигнала — отдельный follow-up
# после накопления телеметрии, не блокирует переход на реальный источник.
FAST_PACE_SECONDS = 20
# 120 секунд — вчетверо выше общей медианы: на этой теме заметно застревают.
SLOW_PACE_SECONDS = 120

# tsk-578: с скольки парами «открыл → сдал» (событие task_opened сопоставлено
# сдаче) тема/задание переходят с прокси на реальный темп. Ниже, чем
# MIN_SUBMISSIONS (20) у прокси: каждая реальная пара — это подлинное время
# думания над ИМЕННО этим заданием, а не шумный промежуток между сдачами
# двух разных заданий, поэтому для той же достоверности хватает меньшей
# выборки. При деплое реальных пар — 0 у всех тем: поведение не меняется,
# переход происходит постепенно по мере накопления телеметрии.
MIN_REAL_PACE_SAMPLES = 8

# Признак темы.
SIGNAL_HARD = "hard"
SIGNAL_EASY = "easy"
SIGNAL_OK = "ok"
# Отдельно от `ok`: сдач нет вовсе. «Выбросов нет» и «не по чему судить» — разные
# утверждения, и подменять второе первым нельзя. Значение живёт в ответе API, а
# не только в подписи на экране: иначе любой другой потребитель прочитает
# нетронутое задание как благополучное.
SIGNAL_UNTOUCHED = "untouched"


def classify_topic(wrong_rate: float, median_pace_seconds: float | None) -> str:
    """Признак темы по доле ошибок и темпу.

    Доля ошибок главнее темпа и в одиночку достаточна для «сложной»: тема, где
    треть ответов неверна, — дефект контента независимо от того, быстро на ней
    ошибаются или медленно.

    А вот «слишком лёгкая» без темпа не выводится. Малая доля ошибок сама по
    себе нормальна и чаще всего означает хорошо сделанную тему; подозрительной
    её делает именно скорость — ученик отвечает быстрее, чем успел бы прочитать
    условие. Поэтому при неизвестном темпе (у ученика одна сдача в теме, второй
    точки для промежутка нет) признак «лёгкая» не ставится вовсе.
    """
    if wrong_rate >= ERROR_RATE_THRESHOLD:
        return SIGNAL_HARD
    if (
        wrong_rate <= EASY_WRONG_RATE
        and median_pace_seconds is not None
        and median_pace_seconds <= FAST_PACE_SECONDS
    ):
        return SIGNAL_EASY
    return SIGNAL_OK


@dataclass
class TopicMastery:
    """Одна тема в обзоре освоения."""

    course_id: int
    course_title: str
    submissions: int
    students_reached: int
    students_mastered: int
    tasks_total: int
    correct_rate: float
    wrong_rate: float
    median_pace_seconds: float | None
    pace_source: str | None
    reliable: bool
    signal: str

    def as_dict(self) -> dict:
        d = asdict(self)
        d["correct_percent"] = round(self.correct_rate * 100)
        d["wrong_percent"] = round(self.wrong_rate * 100)
        return d


# Общая основа всех запросов модуля: реальные сдачи ученика за окно.
# `tasks.is_active` — здесь же: выключенное задание не характеризует тему, а
# сдачи по нему в базе остаются.
_REAL_SUBS_CTE = """
real_subs AS (
    SELECT tr.user_id, tr.task_id, t.course_id, tr.received_at, tr.is_correct
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id AND t.is_active
    WHERE {real_student}
      AND tr.received_at > now() - make_interval(days => :days)
      {course_filter}
)
"""

# Промежуток между СОСЕДНИМИ сдачами одного ученика внутри одной темы.
# Разбивка по (user_id, course_id) обязательна: без неё в промежуток попало бы
# расстояние между разными учениками, то есть чистый шум.
#
# Промежуток приписывается ПОЗДНЕЙШЕЙ сдаче — то есть заданию, которое ученик в
# этот момент сдавал. Так разрез по заданиям читается «сколько прошло до ответа
# на него». Первая сдача в теме промежутка не имеет вовсе: до неё не с чем
# сравнивать, и именно поэтому темп у темы с одной сдачей на ученика неизвестен.
_PACE_CTE = """
pace AS (
    SELECT course_id, task_id,
           EXTRACT(EPOCH FROM (
               received_at - LAG(received_at) OVER (
                   PARTITION BY user_id, course_id ORDER BY received_at
               )
           )) AS gap_seconds
    FROM real_subs
)
"""

# tsk-578: реальное время «показали задание → ответил», а не промежуток между
# соседними сдачами. Для каждой сдачи LATERAL-подзапрос берёт БЛИЖАЙШЕЕ ПЕРЕД
# ней событие task_opened той же пары (user_id, task_id) — не первое открытие
# вообще, а последнее перед ЭТОЙ конкретной сдачей: повторный визит после
# перерыва не должен превращаться в промежуток «со вчерашнего дня». Пары без
# события task_opened (сдача раньше деплоя телеметрии) в выборку не попадают —
# gap_seconds отсутствует, а не считается нулём или прокси-суррогатом.
_REAL_PACE_CTE = """
real_pace AS (
    SELECT rs.course_id, rs.task_id,
           EXTRACT(EPOCH FROM (rs.received_at - opened.opened_at)) AS gap_seconds
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
)
"""

PACE_SOURCE_REAL = "real"
PACE_SOURCE_PROXY = "proxy"

_OVERVIEW_SQL = """
WITH {real_subs},
{pace},
{real_pace},
topic_pace AS (
    SELECT course_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_seconds) AS median_pace
    FROM pace
    WHERE gap_seconds IS NOT NULL AND gap_seconds < :pace_cap
    GROUP BY course_id
),
topic_real_pace AS (
    SELECT course_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_seconds) AS median_pace,
           COUNT(*) AS real_samples
    FROM real_pace
    WHERE gap_seconds < :pace_cap
    GROUP BY course_id
),
topic_tasks AS (
    SELECT course_id, COUNT(*) AS tasks_total
    FROM tasks WHERE is_active GROUP BY course_id
),
per_student AS (
    SELECT course_id, user_id,
           COUNT(DISTINCT task_id) FILTER (WHERE is_correct) AS tasks_ok
    FROM real_subs GROUP BY course_id, user_id
),
topic_students AS (
    SELECT ps.course_id,
           COUNT(*) AS students_reached,
           COUNT(*) FILTER (WHERE ps.tasks_ok >= tt.tasks_total) AS students_mastered
    FROM per_student ps
    JOIN topic_tasks tt ON tt.course_id = ps.course_id
    GROUP BY ps.course_id
),
topic_base AS (
    SELECT course_id,
           COUNT(*) AS submissions,
           COUNT(*) FILTER (WHERE is_correct IS FALSE)::float / COUNT(*) AS wrong_rate
    FROM real_subs GROUP BY course_id
)
SELECT b.course_id,
       c.title AS course_title,
       b.submissions,
       b.wrong_rate,
       COALESCE(ts.students_reached, 0) AS students_reached,
       COALESCE(ts.students_mastered, 0) AS students_mastered,
       COALESCE(tt.tasks_total, 0) AS tasks_total,
       tp.median_pace AS proxy_median_pace,
       trp.median_pace AS real_median_pace,
       COALESCE(trp.real_samples, 0) AS real_samples
FROM topic_base b
JOIN courses c ON c.id = b.course_id
LEFT JOIN topic_students ts ON ts.course_id = b.course_id
LEFT JOIN topic_tasks tt ON tt.course_id = b.course_id
LEFT JOIN topic_pace tp ON tp.course_id = b.course_id
LEFT JOIN topic_real_pace trp ON trp.course_id = b.course_id
"""


def _resolve_pace(row) -> tuple[float | None, str | None]:
    """Выбрать источник темпа: реальный при достаточной выборке, иначе прокси.

    tsk-578: реальные пары «открыл → сдал» точнее прокси и достаточны меньшим
    числом (`MIN_REAL_PACE_SAMPLES` < `MIN_SUBMISSIONS`), поэтому при их
    достатке они полностью вытесняют прокси, а не усредняются с ним — смешивать
    точный сигнал с грубым значило бы портить первый вторым.
    """
    real_samples = int(row["real_samples"])
    if real_samples >= MIN_REAL_PACE_SAMPLES and row["real_median_pace"] is not None:
        return float(row["real_median_pace"]), PACE_SOURCE_REAL
    if row["proxy_median_pace"] is not None:
        return float(row["proxy_median_pace"]), PACE_SOURCE_PROXY
    return None, None


def _build_topic(row) -> TopicMastery:
    wrong_rate = float(row["wrong_rate"])
    pace, pace_source = _resolve_pace(row)
    submissions = int(row["submissions"])
    students_reached = int(row["students_reached"])
    return TopicMastery(
        course_id=int(row["course_id"]),
        course_title=row["course_title"],
        submissions=submissions,
        students_reached=students_reached,
        students_mastered=int(row["students_mastered"]),
        tasks_total=int(row["tasks_total"]),
        correct_rate=1.0 - wrong_rate,
        wrong_rate=wrong_rate,
        median_pace_seconds=pace,
        pace_source=pace_source,
        reliable=submissions >= MIN_SUBMISSIONS and students_reached >= MIN_STUDENTS,
        signal=classify_topic(wrong_rate, pace),
    )


async def topic_overview(db: AsyncSession, *, days: int = 90) -> dict:
    """Освоение по всем темам, где за окно была хоть одна ученическая сдача.

    Тему без единой сдачи в список не кладём, но и не замалчиваем: их число
    возвращается отдельным полем. Активных тем с заданиями на проде 558, сдачи
    за 90 дней есть у 115 — вывалить 443 пустые строки значит утопить в них те,
    ради которых экран и заведён, а промолчать об их числе значит скрыть, что
    почти весь каталог никем не тронут.
    """
    sql = _OVERVIEW_SQL.format(
        real_subs=_REAL_SUBS_CTE.format(
            real_student=real_student_results_filter("tr"), course_filter=""
        ),
        pace=_PACE_CTE,
        real_pace=_REAL_PACE_CTE,
    )
    rows = (await db.execute(text(sql), {
        "days": days, "pace_cap": PACE_OUTLIER_CAP_SECONDS,
    })).mappings().all()

    topics = [_build_topic(r) for r in rows]
    topics.sort(key=lambda t: (t.signal == SIGNAL_OK, -t.wrong_rate, -t.submissions))

    total_with_tasks = int((await db.execute(text(
        "SELECT COUNT(DISTINCT course_id) FROM tasks WHERE is_active"
    ))).scalar_one())

    logger.info(
        "topic_mastery: тем со сдачами %s из %s (окно %s дн.), "
        "сложных %s, подозрительно лёгких %s, надёжных по выборке %s",
        len(topics), total_with_tasks, days,
        sum(1 for t in topics if t.signal == SIGNAL_HARD),
        sum(1 for t in topics if t.signal == SIGNAL_EASY),
        sum(1 for t in topics if t.reliable),
    )
    return {
        "days": days,
        "topics": [t.as_dict() for t in topics],
        "topics_without_submissions": max(total_with_tasks - len(topics), 0),
        "thresholds": {
            "min_submissions": MIN_SUBMISSIONS,
            "min_students": MIN_STUDENTS,
            "hard_wrong_rate": ERROR_RATE_THRESHOLD,
            "easy_wrong_rate": EASY_WRONG_RATE,
            "fast_pace_seconds": FAST_PACE_SECONDS,
            "slow_pace_seconds": SLOW_PACE_SECONDS,
            "min_real_pace_samples": MIN_REAL_PACE_SAMPLES,
        },
    }


_TOPIC_TASKS_SQL = """
WITH {real_subs},
{pace},
{real_pace},
task_pace AS (
    SELECT task_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_seconds) AS median_pace
    FROM pace
    WHERE gap_seconds IS NOT NULL AND gap_seconds < :pace_cap
    GROUP BY task_id
),
task_real_pace AS (
    SELECT task_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_seconds) AS median_pace,
           COUNT(*) AS real_samples
    FROM real_pace
    WHERE gap_seconds < :pace_cap
    GROUP BY task_id
),
task_base AS (
    SELECT task_id,
           COUNT(*) AS submissions,
           COUNT(DISTINCT user_id) AS students,
           COUNT(*) FILTER (WHERE is_correct IS FALSE)::float / COUNT(*) AS wrong_rate
    FROM real_subs GROUP BY task_id
)
SELECT t.id AS task_id,
       t.order_position,
       t.task_content->>'title' AS title,
       t.task_content->>'stem' AS stem,
       t.external_uid,
       COALESCE(b.submissions, 0) AS submissions,
       COALESCE(b.students, 0) AS students,
       b.wrong_rate,
       tp.median_pace AS proxy_median_pace,
       trp.median_pace AS real_median_pace,
       COALESCE(trp.real_samples, 0) AS real_samples
FROM tasks t
LEFT JOIN task_base b ON b.task_id = t.id
LEFT JOIN task_pace tp ON tp.task_id = t.id
LEFT JOIN task_real_pace trp ON trp.task_id = t.id
WHERE t.course_id = :course_id AND t.is_active
ORDER BY t.order_position NULLS LAST, t.id
"""


async def topic_tasks(db: AsyncSession, *, course_id: int, days: int = 90) -> list[dict]:
    """Задания темы с их метриками — то, что методист правит руками.

    Задания без сдач остаются в списке с `submissions = 0`. Именно они и есть
    частый ответ на вопрос «почему тему никто не проходит»: до задания просто не
    доходят. Убрать их значит спрятать самый однозначный сигнал.
    """
    sql = _TOPIC_TASKS_SQL.format(
        real_subs=_REAL_SUBS_CTE.format(
            real_student=real_student_results_filter("tr"),
            course_filter="AND t.course_id = :course_id",
        ),
        pace=_PACE_CTE,
        real_pace=_REAL_PACE_CTE,
    )
    rows = (await db.execute(text(sql), {
        "days": days, "course_id": course_id, "pace_cap": PACE_OUTLIER_CAP_SECONDS,
    })).mappings().all()

    out = []
    for r in rows:
        submissions = int(r["submissions"])
        wrong_rate = None if r["wrong_rate"] is None else float(r["wrong_rate"])
        pace, pace_source = _resolve_pace(r)
        out.append({
            "task_id": int(r["task_id"]),
            "order_position": r["order_position"],
            "title": humanize_task_title(
                int(r["task_id"]), r["title"], r["stem"], r["external_uid"]
            ),
            "submissions": submissions,
            "students": int(r["students"]),
            "wrong_rate": wrong_rate,
            "wrong_percent": None if wrong_rate is None else round(wrong_rate * 100),
            "median_pace_seconds": pace,
            "pace_source": pace_source,
            "signal": (
                SIGNAL_UNTOUCHED if wrong_rate is None
                else classify_topic(wrong_rate, pace)
            ),
        })
    return out


_TOPIC_STUDENTS_SQL = """
WITH {real_subs},
topic_tasks AS (
    SELECT COUNT(*) AS tasks_total FROM tasks
    WHERE is_active AND course_id = :course_id
),
per_student AS (
    SELECT user_id,
           COUNT(*) AS submissions,
           COUNT(DISTINCT task_id) AS tasks_touched,
           COUNT(DISTINCT task_id) FILTER (WHERE is_correct) AS tasks_correct,
           COUNT(*) FILTER (WHERE is_correct IS FALSE)::float / COUNT(*) AS wrong_rate,
           MAX(received_at) AS last_submission_at
    FROM real_subs GROUP BY user_id
)
SELECT p.user_id AS student_id,
       u.full_name AS student_name,
       p.submissions, p.tasks_touched, p.tasks_correct, p.wrong_rate,
       p.last_submission_at,
       (SELECT tasks_total FROM topic_tasks) AS tasks_total
FROM per_student p
JOIN users u ON u.id = p.user_id
ORDER BY p.wrong_rate DESC, p.submissions DESC
"""


async def topic_students(db: AsyncSession, *, course_id: int, days: int = 90) -> list[dict]:
    """Кто из учеников освоил тему, а кто нет.

    Освоение считается по сдачам ВНУТРИ окна: ученик, закрывший тему до его
    начала, здесь не появится. Это ограничение окна, а не пробел данных, — но
    читать разрез нужно именно так, иначе «не освоил» прочтётся как «не смог».
    """
    sql = _TOPIC_STUDENTS_SQL.format(
        real_subs=_REAL_SUBS_CTE.format(
            real_student=real_student_results_filter("tr"),
            course_filter="AND t.course_id = :course_id",
        ),
    )
    rows = (await db.execute(text(sql), {
        "days": days, "course_id": course_id,
    })).mappings().all()

    out = []
    for r in rows:
        tasks_total = int(r["tasks_total"] or 0)
        tasks_correct = int(r["tasks_correct"])
        wrong_rate = float(r["wrong_rate"])
        out.append({
            "student_id": int(r["student_id"]),
            "student_name": r["student_name"],
            "submissions": int(r["submissions"]),
            "tasks_touched": int(r["tasks_touched"]),
            "tasks_correct": tasks_correct,
            "tasks_total": tasks_total,
            "wrong_rate": wrong_rate,
            "wrong_percent": round(wrong_rate * 100),
            "mastered": tasks_total > 0 and tasks_correct >= tasks_total,
            "last_submission_at": r["last_submission_at"],
        })
    return out
