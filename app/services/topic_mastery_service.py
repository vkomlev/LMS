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
выброшены — перерыв между занятиями, не размышление). Источник виден в ответе
API полем `pace_source` ("real" | "proxy" | null): на 2026-09-09 реальный темп
у 83 тем из 247, у остальных пока прокси.

**Пороги темпа откалиброваны на реальном сигнале (tsk-579, 2026-09-09).** За
месяц работы телеметрии накопилось 6 749 пар «открыл → сдал» у 75 учеников по
1 698 заданиям — калибровать есть на чём. Считать пороги по распределению
ОТДЕЛЬНЫХ пар (как делала прокси-калибровка tsk-577) неверно: порог
прикладывается к МЕДИАНЕ темы или задания, а не к одной сдаче, и это другое
распределение. Опорные цифры сняты по обоим и записаны у самих констант.

**Темп сравнивается с ОЖИДАЕМЫМ для такого задания, а не с общей секундой
(tsk-846).** Абсолютный порог измерял формат, а не трудность: медиана по типам
— выбор одного ответа 13 с, выбор нескольких 17 с, короткий ответ 23 с, задача
с решением 264 с, таблица 343 с, развёрнутый ответ 364 с. Медиана темы поэтому
определялась её составом: где 80 % и больше заданий с выбором — 12,5 с, где
меньше 20 % — 182 с, разница в пятнадцать раз. Признак «подозрительно лёгкая
тема» на едином пороге находил тему ИЗ ТЕСТОВ.

Теперь каждое наблюдение делится на медиану СВОЕГО типа задания (и своего
источника: у прокси свои базовые медианы, шкала другая), а признаком служит
медиана этого отношения — `pace_ratio`. 0,5 значит «вдвое быстрее, чем такие
задания решают обычно», 1,0 — «как обычно». Разброс сжимается с пятнадцатикратного
до двукратного: по проду p10 0,59, медиана 1,04, p90 2,51.

**Признак `easy` требует достаточной выборки темпа (tsk-846, решение оператора
09.09).** Из 22 тем, помеченных «подозрительно лёгкими» до этой правки, 20
стояли на 2–10 сдачах — по такой выборке нельзя предлагать методисту
переделывать материал. Порог `MIN_PACE_SAMPLES_FOR_EASY` делает признак редким
и настоящим: на 2026-09-09 он срабатывает у ОДНОЙ темы платформы. Это не
поломка, а честная цена: тем с достаточной выборкой темпа 113 из 247, и среди
них быстрая при нулевых ошибках ровно одна.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.learning_gaps_service import (
    task_error_rate,
    task_min_students,
    task_min_submissions,
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

# Порог «эту тему проходят заметно быстрее, чем такие задания решают обычно»
# (tsk-846; прежний абсолютный порог 15 с отменён — он мерил формат задания).
#
# 0.7 — примерно «в полтора раза быстрее ожидаемого». Цифра не подогнана: в
# распределении есть разрыв ровно здесь. Среди тем с достаточной выборкой темпа
# и нулём ошибок отношения идут 0,35 — а дальше сразу плотная группа 0,85 · 0,90
# · 0,91 · 0,91 · 0,93 · 1,03 · 1,07 … Первая — настоящая находка, остальные
# нормальны. Порог проведён в пустоте между ними.
#
# Опорное распределение по проду (окно 90 дней, 2026-09-09, 233 темы с
# отношением): p05 0,44 · p10 0,59 · p25 0,79 · медиана 1,04 · p90 2,51.
FAST_PACE_RATIO = 0.7

# Сколько наблюдений должно быть у ТИПА задания, чтобы его медиана годилась в
# базу сравнения. Тип с одной парой базой быть не может: делить на такую
# «медиану» значит объявлять случайную величину нормой (на проде так выглядел
# `SC_Qw` — 1 пара за окно). Наблюдения типов без базы в отношение не попадают
# вовсе, а не приравниваются к общей медиане: приравнять — то же самое, что
# вернуть отменённый абсолютный порог, только молча.
MIN_TYPE_PACE_SAMPLES = 30

# Сколько наблюдений темпа нужно теме/заданию, чтобы вообще ставить признак
# «подозрительно лёгкая». Раньше условия не было — и из 22 помеченных тем 20
# стояли на 2–10 сдачах: пометка предлагала методисту переделать материал по
# двум наблюдениям. Решение оператора 09.09 — требовать выборку, даже ценой
# того, что признак станет редким (на 2026-09-09 — одна тема на платформе).
#
# Цифра та же, что у перехода на реальный темп (`MIN_REAL_PACE_SAMPLES`), и по
# той же причине: замер устойчивости tsk-579 показал, что на 8 наблюдениях
# медиана темы отклоняется от полной на 41 % и переворачивает признак в 8
# случаях из 49, на 12 — на 33 % и в 4 из 49.
MIN_PACE_SAMPLES_FOR_EASY = 12

# p90 медиан тем — 195 с. 180 с (три минуты) — «на этой теме заметно
# застревают». Важно: решения по этому порогу сейчас не принимает НИКТО —
# `classify_topic` его не спрашивает, фронт из блока `thresholds` его не читает.
# Значение экспортируется в API как опорная цифра; появится потребитель —
# сверять с распределением заново, а не наследовать вслепую (как вышло с 20 с).
SLOW_PACE_SECONDS = 180

# С скольки парами «открыл → сдал» (событие task_opened сопоставлено сдаче)
# тема/задание переходят с прокси на реальный темп.
#
# tsk-579 (было 8, выбранное до накопления данных): проверено на проде сравнением
# медианы по первым N парам с медианой по всей выборке у тем, где пар ≥ 20.
# Медиана относительной ошибки: 8 пар — 41 %, 12 пар — 33 %, 20 пар — 21 %.
# Важнее ошибки — переворот признака: при 8 парах тема меняет сторону порога в
# 8 случаях из 49, при 12 — в 4. Двенадцать пар вдвое снижают ложные пометки,
# оставляя реальный источник у 83 тем из 98 — цена в охвате меньше выигрыша в
# правдивости. Ниже MIN_SUBMISSIONS (20) по-прежнему: реальная пара — подлинное
# время над ИМЕННО этим заданием, а не шумный промежуток между двумя разными.
MIN_REAL_PACE_SAMPLES = 12

# Признак темы.
SIGNAL_HARD = "hard"
SIGNAL_EASY = "easy"
SIGNAL_OK = "ok"
# Отдельно от `ok`: сдач нет вовсе. «Выбросов нет» и «не по чему судить» — разные
# утверждения, и подменять второе первым нельзя. Значение живёт в ответе API, а
# не только в подписи на экране: иначе любой другой потребитель прочитает
# нетронутое задание как благополучное.
SIGNAL_UNTOUCHED = "untouched"


def classify_topic(
    wrong_rate: float,
    pace_ratio: float | None,
    pace_samples: int = 0,
) -> str:
    """Признак темы по доле ошибок и темпу относительно ожидаемого.

    Доля ошибок главнее темпа и в одиночку достаточна для «сложной»: тема, где
    треть ответов неверна, — дефект контента независимо от того, быстро на ней
    ошибаются или медленно.

    А вот «слишком лёгкая» требует трёх условий сразу, и каждое стоило
    отдельного разбора:

    - мало ошибок — сама по себе величина нормальная и чаще означает хорошо
      сделанную тему;
    - `pace_ratio` заметно ниже единицы — то есть быстро НЕ вообще, а по
      меркам таких же заданий (tsk-846: абсолютная секунда мерила формат);
    - наблюдений темпа хватает, чтобы у слова «быстро» был смысл (tsk-846:
      двадцать из двадцати двух прежних пометок стояли на 2–10 сдачах).

    Не хватает любого — признак не ставится. `None` в `pace_ratio` (темп
    неизвестен, или все задания темы редкого типа без базы сравнения) — это
    «не по чему судить», а не «нормально».
    """
    # tsk-721: тот же порог, что у датчика пробелов, и берётся он оттуда же
    # функцией — иначе кабинет менял бы одно место из двух.
    if wrong_rate >= task_error_rate():
        return SIGNAL_HARD
    if (
        wrong_rate <= EASY_WRONG_RATE
        and pace_ratio is not None
        and pace_samples >= MIN_PACE_SAMPLES_FOR_EASY
        and pace_ratio <= FAST_PACE_RATIO
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
    #: tsk-846: во сколько раз темп темы отличается от ожидаемого для её
    #: заданий. 0,5 — вдвое быстрее обычного, 1,0 — как обычно, `None` — не с
    #: чем сравнивать (нет наблюдений или все задания редкого типа).
    pace_ratio: float | None
    reliable: bool
    signal: str

    def as_dict(self) -> dict:
        d = asdict(self)
        d["correct_percent"] = round(self.correct_rate * 100)
        d["wrong_percent"] = round(self.wrong_rate * 100)
        # Два знака: отношение — оценка, а не измерение, и «0.23133081935»
        # в ответе обещает точность, которой у медианы по дюжине наблюдений
        # нет. Тот же приём, что с процентами выше.
        d["pace_ratio"] = _round_ratio(self.pace_ratio)
        return d


# Общая основа всех запросов модуля: реальные сдачи ученика за окно.
# `tasks.is_active` — здесь же: выключенное задание не характеризует тему, а
# сдачи по нему в базе остаются.
_REAL_SUBS_CTE = """
real_subs AS (
    SELECT tr.user_id, tr.task_id, t.course_id, tr.received_at, tr.is_correct,
           t.task_content->>'type' AS task_type
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
    SELECT course_id, task_id, task_type,
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
    SELECT rs.course_id, rs.task_id, rs.task_type,
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

# tsk-846: с чем сравнивать темп. Медиана по ВСЕЙ платформе для каждой пары
# (тип задания, источник) — она и есть «сколько такие задания обычно занимают».
#
# Источники считаются РАЗДЕЛЬНО, и это не перестраховка: у одного и того же
# типа шкалы разные (выбор ответа — 13,4 с реального времени против 15,2 с
# прокси; задача с решением — 264 с против 216 с). Смешать их значило бы
# сравнивать тему с базой, снятой другим прибором.
#
# База живёт ВНУТРИ того же запроса, а не отдельным вызовом. Отдельный запрос
# написать проще, но он повторяет самую дорогую часть работы — LATERAL по
# каждой сдаче ради события `task_opened`. Замер на проде: отдельным запросом
# база считалась 19,6 с, то есть экран методиста открывался бы двадцать секунд.
_TYPE_BASE_CTE = """
type_base AS (
    SELECT task_type, source,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_seconds) AS median_pace
    FROM (
        -- Значения источника здесь и в JOIN'ах ниже — те же строки, что
        -- PACE_SOURCE_REAL / PACE_SOURCE_PROXY.
        SELECT task_type, 'real' AS source, gap_seconds
        FROM real_pace WHERE gap_seconds < :pace_cap
        UNION ALL
        SELECT task_type, 'proxy', gap_seconds
        FROM pace WHERE gap_seconds IS NOT NULL AND gap_seconds < :pace_cap
    ) observations
    WHERE task_type IS NOT NULL
    GROUP BY task_type, source
    HAVING COUNT(*) >= :min_type_samples
)
"""

_OVERVIEW_SQL = """
WITH {real_subs},
{pace},
{real_pace},
{type_base},
topic_pace AS (
    SELECT p.course_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY p.gap_seconds) AS median_pace,
           -- tsk-846: отношение к ожидаемому для ЭТОГО типа задания. Наблюдения
           -- типов без базы (редкий тип) дают NULL и в медиану отношения не
           -- попадают — percentile_cont их игнорирует, поэтому считаем их
           -- отдельным счётчиком, а не через COUNT(*).
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY p.gap_seconds / b.median_pace
           ) AS median_ratio,
           COUNT(b.median_pace) AS ratio_samples
    FROM pace p
    LEFT JOIN type_base b
           ON b.task_type = p.task_type AND b.source = 'proxy'
    WHERE p.gap_seconds IS NOT NULL AND p.gap_seconds < :pace_cap
    GROUP BY p.course_id
),
topic_real_pace AS (
    SELECT rp.course_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY rp.gap_seconds) AS median_pace,
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY rp.gap_seconds / b.median_pace
           ) AS median_ratio,
           COUNT(*) AS real_samples,
           COUNT(b.median_pace) AS ratio_samples
    FROM real_pace rp
    LEFT JOIN type_base b
           ON b.task_type = rp.task_type AND b.source = 'real'
    WHERE rp.gap_seconds < :pace_cap
    GROUP BY rp.course_id
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
       tp.median_ratio AS proxy_median_ratio,
       COALESCE(tp.ratio_samples, 0) AS proxy_ratio_samples,
       trp.median_pace AS real_median_pace,
       trp.median_ratio AS real_median_ratio,
       COALESCE(trp.ratio_samples, 0) AS real_ratio_samples,
       COALESCE(trp.real_samples, 0) AS real_samples
FROM topic_base b
JOIN courses c ON c.id = b.course_id
LEFT JOIN topic_students ts ON ts.course_id = b.course_id
LEFT JOIN topic_tasks tt ON tt.course_id = b.course_id
LEFT JOIN topic_pace tp ON tp.course_id = b.course_id
LEFT JOIN topic_real_pace trp ON trp.course_id = b.course_id
"""


@dataclass(frozen=True)
class Pace:
    """Темп темы или задания: сколько, чем измерено и насколько это быстро.

    `ratio` и `seconds` — разные вопросы, и оба нужны. Секунды методист читает
    глазами («полторы минуты на задание»), отношение отвечает «а много это или
    мало для ТАКИХ заданий» (tsk-846). Признак строится на отношении, подпись —
    на секундах.
    """

    seconds: float | None
    source: str | None
    ratio: float | None
    ratio_samples: int


def _resolve_pace(row) -> Pace:
    """Выбрать источник темпа: реальный при достаточной выборке, иначе прокси.

    tsk-578: реальные пары «открыл → сдал» точнее прокси и достаточны меньшим
    числом (`MIN_REAL_PACE_SAMPLES` < `MIN_SUBMISSIONS`), поэтому при их
    достатке они полностью вытесняют прокси, а не усредняются с ним — смешивать
    точный сигнал с грубым значило бы портить первый вторым.

    Отношение берётся ОТТУДА ЖЕ, откуда секунды: у прокси своя база сравнения.
    Взять секунды у реального источника, а отношение у прокси значило бы
    сравнить измерение одним прибором с нормой другого.
    """
    real_samples = int(row["real_samples"])
    if real_samples >= MIN_REAL_PACE_SAMPLES and row["real_median_pace"] is not None:
        return Pace(
            seconds=float(row["real_median_pace"]),
            source=PACE_SOURCE_REAL,
            ratio=_opt_float(row["real_median_ratio"]),
            ratio_samples=int(row["real_ratio_samples"]),
        )
    if row["proxy_median_pace"] is not None:
        return Pace(
            seconds=float(row["proxy_median_pace"]),
            source=PACE_SOURCE_PROXY,
            ratio=_opt_float(row["proxy_median_ratio"]),
            ratio_samples=int(row["proxy_ratio_samples"]),
        )
    return Pace(seconds=None, source=None, ratio=None, ratio_samples=0)


def _opt_float(value) -> float | None:
    return None if value is None else float(value)


def _round_ratio(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _build_topic(row) -> TopicMastery:
    wrong_rate = float(row["wrong_rate"])
    pace = _resolve_pace(row)
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
        median_pace_seconds=pace.seconds,
        pace_source=pace.source,
        pace_ratio=pace.ratio,
        reliable=(
            submissions >= task_min_submissions()
            and students_reached >= task_min_students()
        ),
        signal=classify_topic(wrong_rate, pace.ratio, pace.ratio_samples),
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
        type_base=_TYPE_BASE_CTE,
    )
    rows = (await db.execute(text(sql), {
        "days": days, "pace_cap": PACE_OUTLIER_CAP_SECONDS,
        "min_type_samples": MIN_TYPE_PACE_SAMPLES,
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
            "min_submissions": task_min_submissions(),
            "min_students": task_min_students(),
            "hard_wrong_rate": task_error_rate(),
            "easy_wrong_rate": EASY_WRONG_RATE,
            # tsk-846: `fast_pace_seconds` из блока УБРАН вместе с самой
            # константой — абсолютной секунды больше нет, её место заняло
            # отношение к ожидаемому для типа задания.
            "fast_pace_ratio": FAST_PACE_RATIO,
            "slow_pace_seconds": SLOW_PACE_SECONDS,
            "min_real_pace_samples": MIN_REAL_PACE_SAMPLES,
            "min_pace_samples_for_easy": MIN_PACE_SAMPLES_FOR_EASY,
            "min_type_pace_samples": MIN_TYPE_PACE_SAMPLES,
        },
    }


_TOPIC_TASKS_SQL = """
WITH {real_subs},
{pace},
{real_pace},
{type_base},
task_pace AS (
    SELECT p.task_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY p.gap_seconds) AS median_pace,
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY p.gap_seconds / b.median_pace
           ) AS median_ratio,
           COUNT(b.median_pace) AS ratio_samples
    FROM pace p
    LEFT JOIN type_base b
           ON b.task_type = p.task_type AND b.source = 'proxy'
    WHERE p.gap_seconds IS NOT NULL AND p.gap_seconds < :pace_cap
    GROUP BY p.task_id
),
task_real_pace AS (
    SELECT rp.task_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY rp.gap_seconds) AS median_pace,
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY rp.gap_seconds / b.median_pace
           ) AS median_ratio,
           COUNT(*) AS real_samples,
           COUNT(b.median_pace) AS ratio_samples
    FROM real_pace rp
    LEFT JOIN type_base b
           ON b.task_type = rp.task_type AND b.source = 'real'
    WHERE rp.gap_seconds < :pace_cap
    GROUP BY rp.task_id
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
       tp.median_ratio AS proxy_median_ratio,
       COALESCE(tp.ratio_samples, 0) AS proxy_ratio_samples,
       trp.median_pace AS real_median_pace,
       trp.median_ratio AS real_median_ratio,
       COALESCE(trp.ratio_samples, 0) AS real_ratio_samples,
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
    # Наблюдения берутся по ВСЕЙ платформе, хотя показываем одну тему: база
    # сравнения (`type_base`) обязана быть общей, иначе задание сравнивалось бы
    # с соседями по теме и отношение у любой темы вышло бы около единицы
    # (tsk-846). Фильтр по теме стоит ниже, в отборе строк списка.
    sql = _TOPIC_TASKS_SQL.format(
        real_subs=_REAL_SUBS_CTE.format(
            real_student=real_student_results_filter("tr"),
            course_filter="",
        ),
        pace=_PACE_CTE,
        real_pace=_REAL_PACE_CTE,
        type_base=_TYPE_BASE_CTE,
    )
    rows = (await db.execute(text(sql), {
        "days": days, "course_id": course_id, "pace_cap": PACE_OUTLIER_CAP_SECONDS,
        "min_type_samples": MIN_TYPE_PACE_SAMPLES,
    })).mappings().all()

    out = []
    for r in rows:
        submissions = int(r["submissions"])
        wrong_rate = None if r["wrong_rate"] is None else float(r["wrong_rate"])
        pace = _resolve_pace(r)
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
            "median_pace_seconds": pace.seconds,
            "pace_source": pace.source,
            "pace_ratio": _round_ratio(pace.ratio),
            "signal": (
                SIGNAL_UNTOUCHED if wrong_rate is None
                else classify_topic(wrong_rate, pace.ratio, pace.ratio_samples)
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
