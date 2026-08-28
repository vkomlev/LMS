"""Сигналы «нужно повторение»: теме — методисту, ученику — преподавателю.

tsk-572, фаза 7. Отдельно от `learning_gaps_service` намеренно: тот считает
цифры, этот управляет тем, что с цифрами делают люди.

**Почему адресата два.** Датчик замечает две разные вещи. Проваливается ТЕМА
(много учеников, высокая доля ошибок) — это работа с контентом, заявка методисту
на мини-курс. Буксует КОНКРЕТНЫЙ ученик — это сигнал преподавателю: он ведёт
занятия и видит ученика живьём, а методист нет.

Смешать потоки нельзя: методисту незачем разбирать личные затыки, а
преподавателю — получать заявки на переписывание курса.

**Зачем комментарий преподавателя.** Он видел ученика вживую и знает то, чего в
долях ошибок нет: «болел две недели», «путает ввод и вывод, а не циклы». Этот
комментарий уезжает вместе с эскалацией методисту и часто ценнее самой цифры.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings_store
from app.services.learning_gaps_service import (
    find_topic_gaps,
    real_student_results_filter,
)

logger = logging.getLogger(__name__)

# Пороги для одного ученика отдельные от тем: у человека выборка всегда меньше,
# и требовать от неё той же статистики бессмысленно.
STUDENT_MIN_SUBMISSIONS = 8
STUDENT_ERROR_RATE_THRESHOLD = 0.5

#: Поводы сигнала (tsk-653). Раньше повод был один и потому безымянный.
REASON_ERROR_RATE = "error_rate"
REASON_AI_AUTHORSHIP = "ai_authorship"
REASON_DROPOUT_RISK = "dropout_risk"

# Окно признака «затих» (tsk-647). Две недели — не круглое число, а результат
# замера на боевой базе за август (docs/qa/2026-08-28-tsk647-dropout-signal.md):
#
#   10 дней — 6 помеченных, 3 ухода: половина тревог ложная;
#   14 дней — 3 помеченных, 2 ухода, третий вернулся после трёх пропущенных
#             занятий подряд и трёх недель без единой своей сдачи — то есть
#             тревога по нему была уместной, а не шумом;
#   21 день — 2 помеченных, 2 ухода, но сигнал приходит на неделю позже.
#
# Значение по умолчанию; фактическое берётся из `DROPOUT_RISK_WINDOW_DAYS`.
# Порог — решение оператора, и менять его нужно без выката: цена у обеих ошибок
# разная. Ослаблять можно только новым замером, а не ощущением.
DROPOUT_WINDOW_DAYS = 14

# Пороги признака ИИ-авторства. Оба сразу, а не любой из них: три работы из
# трёхсот — это шум, а две из двух — слишком мало, чтобы звать человека.
#
# Числа взяты из замера на боевом корпусе (2026-08-23,
# docs/qa/2026-08-23-tsk646-text-authorship-calibration.md): при них на ВСЕЙ
# базе — 1028 разобранных работ, 34 ученика — порог берёт ровно одну ученицу,
# ту самую, про которую преподаватель сказал это словами. Ослабить их можно
# только новым замером, а не ощущением.
AI_MIN_FLAGGED_WORKS = 3
AI_MIN_FLAGGED_SHARE = 0.5
#: Окно шире, чем у датчика ошибок (30 дней): признак авторства копится
#: медленно — развёрнутых работ у ученика единицы за месяц.
AI_WINDOW_DAYS = 90

_STUDENT_GAPS_SQL = """
SELECT tr.user_id AS student_id,
       t.course_id,
       c.title AS course_title,
       COUNT(*) AS submissions,
       COUNT(*) FILTER (WHERE tr.is_correct IS FALSE)::float / COUNT(*) AS wrong_rate
FROM task_results tr
JOIN tasks t ON t.id = tr.task_id AND t.is_active
JOIN courses c ON c.id = t.course_id
WHERE {real_student}
  AND tr.received_at > now() - make_interval(days => :days)
GROUP BY tr.user_id, t.course_id, c.title
HAVING COUNT(*) >= :min_submissions
   AND COUNT(*) FILTER (WHERE tr.is_correct IS FALSE)::float / COUNT(*) >= :threshold
ORDER BY wrong_rate DESC
LIMIT :limit
"""


async def find_student_gaps(
    db: AsyncSession,
    *,
    days: int = 30,
    min_submissions: int | None = None,
    threshold: float | None = None,
    limit: int = 100,
) -> list[dict]:
    """Ученики, которым нужно повторение конкретной темы.

    Тот же фильтр источника, что и у тем: ручная простановка преподавателя — не
    ответ ученика и в счёт его ошибок идти не должна.
    """
    # tsk-721: пороги читаются на каждом проходе, а не подставляются
    # умолчанием параметра — умолчание вычисляется при импорте модуля, и
    # правка в кабинете ждала бы перезапуска.
    if min_submissions is None:
        min_submissions = _setting_int("gap_student_min_submissions", STUDENT_MIN_SUBMISSIONS)
    if threshold is None:
        threshold = _setting_float("gap_student_error_rate", STUDENT_ERROR_RATE_THRESHOLD)
    sql = _STUDENT_GAPS_SQL.format(real_student=real_student_results_filter("tr"))
    rows = (await db.execute(text(sql), {
        "days": days, "min_submissions": min_submissions,
        "threshold": threshold, "limit": limit,
    })).mappings().all()
    return [dict(r) for r in rows]


# Датчик признака ИИ-авторства (tsk-653).
#
# **Почему считаем по КОРНЕВОМУ курсу, а не по тому, где лежит задание.**
# У ученицы, с которой всё началось, 12 разобранных работ лежат в 12 разных
# подкурсах — по одной в каждом. По подкурсам сигнал не родился бы вовсе
# (одна работа — не статистика), а роди он их по одной на подкурс, методист
# получил бы 11 карточек об одном человеке. Корень — тот узел, на который
# ученик записан, и ровно тот охват, на который методист собирает мини-курс.
#
# Дерево строится ВНИЗ от записи ученика (`user_courses`), как в
# `me_service._ROOT_OF_LEAF_SQL`: записывают только на корень, вверх по
# `course_parents` идти незачем и неоднозначно. `DISTINCT ON` с тем же
# правилом разрешения, что и там: узел под двумя записанными деревьями иначе
# посчитался бы дважды.
#
# Работа считается «с признаком», если сработала ЛЮБАЯ ось — механические следы
# вставки или вердикт модели. Механическая ось идёт первой не случайно: её
# преподаватель может проверить глазами.
_AI_AUTHORSHIP_SQL = """
WITH RECURSIVE trees AS (
    SELECT uc.user_id, uc.course_id AS root_course_id, uc.course_id AS member_course_id
    FROM user_courses uc
    WHERE uc.is_active = true
    UNION ALL
    SELECT t.user_id, t.root_course_id, cp.course_id
    FROM trees t
    JOIN course_parents cp ON cp.parent_course_id = t.member_course_id
),
roots AS (
    SELECT DISTINCT ON (user_id, member_course_id)
           user_id, member_course_id, root_course_id
    FROM trees
    ORDER BY user_id, member_course_id,
             (root_course_id = member_course_id) DESC, root_course_id ASC
),
reviewed AS (
    SELECT tr.user_id,
           t.course_id AS member_course_id,
           tr.received_at,
           (jsonb_array_length(COALESCE(tr.code_review->'signals', '[]'::jsonb)) > 0
            OR tr.code_review->'ai_authorship'->>'verdict' = 'ai_likely') AS flagged
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id AND t.is_active
    WHERE tr.code_review->>'status' = 'done'
      AND tr.received_at > now() - make_interval(days => :days)
      AND {real_student}
),
-- Когда по этой паре в последний раз ЗАКРЫВАЛИ такой сигнал. Работы, сданные
-- до этого момента, человек уже разобрал — считать их заново значит поднимать
-- один и тот же сигнал бесконечно.
closed AS (
    SELECT student_id, course_id,
           MAX(COALESCE(acknowledged_at, created_at)) AS closed_at
    FROM learning_gap_signal
    WHERE reason = 'ai_authorship'
      AND status IN ('resolved', 'dismissed')
      AND student_id IS NOT NULL
    GROUP BY student_id, course_id
)
SELECT r.user_id AS student_id,
       roots.root_course_id AS course_id,
       c.title AS course_title,
       COUNT(*) AS reviewed,
       COUNT(*) FILTER (WHERE r.flagged) AS flagged
FROM reviewed r
JOIN roots ON roots.user_id = r.user_id AND roots.member_course_id = r.member_course_id
JOIN courses c ON c.id = roots.root_course_id
LEFT JOIN closed ON closed.student_id = r.user_id
                AND closed.course_id = roots.root_course_id
WHERE closed.closed_at IS NULL OR r.received_at > closed.closed_at
GROUP BY r.user_id, roots.root_course_id, c.title
HAVING COUNT(*) FILTER (WHERE r.flagged) >= :min_flagged
   AND COUNT(*) FILTER (WHERE r.flagged)::float / COUNT(*) >= :min_share
ORDER BY COUNT(*) FILTER (WHERE r.flagged) DESC
LIMIT :limit
"""


async def find_ai_authorship_gaps(
    db: AsyncSession,
    *,
    days: int | None = None,
    min_flagged: int | None = None,
    min_share: float | None = None,
    limit: int = 100,
) -> list[dict]:
    """Ученики, чьи работы несут признак ИИ-авторства.

    Отдельный датчик, а не расширение `find_student_gaps`, потому что меряет он
    другое. Тот считает долю ОШИБОК — и ученика, сдающего чужое, не увидит
    никогда: у такого ученика ошибок нет, все работы приняты. Живой случай,
    с которого началась tsk-646, ровно такой: 12 работ, 12 зачётов, ноль ошибок.

    **Почему учитывается дата последнего закрытия.** Признак у уже разобранной
    работы не «стареет»: он останется в отчёте навсегда. У датчика по ошибкам
    этого вопроса нет — старые сдачи сами выпадают из окна в 30 дней, — а здесь
    без поправки сигнал поднимался бы заново на следующем же проходе после
    каждого закрытия, и так бесконечно. Поэтому после разбора считаются только
    работы, сданные ПОСЛЕ него: сигнал вернётся, если признак появился снова, и
    не вернётся, если человек уже всё разобрал.

    Возвращает строки с `reviewed` и `flagged` — числа кладутся в `meta` сигнала
    и показываются человеку. Доля ошибок сюда не входит: у этого повода она
    ничего не значит.
    """
    # tsk-721: пороги признака ИИ — на месте применения, см. find_student_gaps.
    if days is None:
        days = ai_window_days()
    if min_flagged is None:
        min_flagged = _setting_int("ai_signal_min_flagged_works", AI_MIN_FLAGGED_WORKS)
    if min_share is None:
        min_share = _setting_float("ai_signal_min_flagged_share", AI_MIN_FLAGGED_SHARE)
    sql = _AI_AUTHORSHIP_SQL.format(real_student=real_student_results_filter("tr"))
    rows = (await db.execute(text(sql), {
        "days": days, "min_flagged": min_flagged,
        "min_share": min_share, "limit": limit,
    })).mappings().all()
    return [dict(r) for r in rows]


# Датчик «затих» (tsk-647): ученик, который вот-вот перестанет ходить.
#
# **Почему именно два условия сразу, а не пропуски.** Пропуски проверены на
# боевых данных и как признак не работают: `no_show` — четверть всех участий,
# школа так живёт. Правило «два пропуска подряд», проверенное на четырёх датах
# августа, дало 14 тревог и 4 попадания — три четверти шума, а сигнал, который
# читают через раз, равен отсутствию сигнала. Тишина в кабинете сама по себе не
# лучше: паузы в 7–19 дней есть у самых прилежных, включая ученика с 1486
# сдачами. Разделяет только СОВПАДЕНИЕ: занятия идут — его нет; кабинет открыт —
# он в нём не работает. Замер: docs/qa/2026-08-28-tsk647-dropout-signal.md.
#
# **Почему `spw_web`, а не любые строки `task_results`.** 73% строк на боевой
# базе — ручные простановки преподавателя (`manual_teacher`). Считать их работой
# ученика значит мерить активность ПРЕПОДАВАТЕЛЯ: первый вариант этого запроса
# так и делал и «видел» работу там, где ученик не заходил месяц.
#
# **Почему нужна история своей работы.** Без условия «раньше сдавал сам» датчик
# вырождается в «не был 14 дней» — для учеников, чью работу преподаватель
# отмечает руками, второе условие истинно всегда. На тех же данных это дало 11
# тревог вместо 3 при том же числе находок. Цена: пятеро из 46 учеников с
# занятиями остаются вне охвата — у троих работа только с ручной отметкой, у
# двоих сдач нет вовсе. Это честная граница, а не недосмотр.
#
# **Почему перерыв проверяется на пересечение с окном, а не на сегодня.**
# Перерыв заводят задним числом и на месяц вперёд; сверка «идёт ли он прямо
# сейчас» пропустила бы ученика, у которого окно целиком лежит внутри отъезда.
_DROPOUT_RISK_SQL = """
WITH win AS (
    SELECT now() - make_interval(days => :days) AS since
),
-- Один курс на человека, а не по курсу на запись: две карточки об одном
-- ученике — это не два сигнала, это способ отучить читать список. Записывают
-- только на корень дерева, поэтому вверх по `course_parents` идти незачем.
--
-- Граница: ученик без активной записи на курс сигнала не получит — карточку
-- некуда привязать (`learning_gap_signal.course_id` обязателен). На боевой базе
-- такой один, и он и так вне охвата: своей работы у него нет вовсе.
enrolled AS (
    SELECT DISTINCT ON (uc.user_id)
           uc.user_id, uc.course_id
    FROM user_courses uc
    WHERE uc.is_active = true
    ORDER BY uc.user_id, uc.added_at DESC, uc.course_id
),
lessons AS (
    SELECT p.student_id,
           COUNT(*) AS lessons_in_window,
           COUNT(*) FILTER (WHERE p.status = 'confirmed') AS attended
    FROM lesson_occurrence_participant p
    JOIN lesson_occurrence o ON o.id = p.occurrence_id
    CROSS JOIN win
    WHERE p.status IN ('confirmed', 'no_show')
      AND o.scheduled_at >= win.since
      AND o.scheduled_at < now()
    GROUP BY p.student_id
),
own_work AS (
    SELECT tr.user_id, MAX(tr.submitted_at) AS last_own_work
    FROM task_results tr
    WHERE {real_student}
    GROUP BY tr.user_id
),
last_seen AS (
    SELECT p.student_id, MAX(o.scheduled_at) AS last_attended
    FROM lesson_occurrence_participant p
    JOIN lesson_occurrence o ON o.id = p.occurrence_id
    WHERE p.status = 'confirmed'
    GROUP BY p.student_id
),
-- Когда по этому ученику в последний раз ЗАКРЫВАЛИ такой сигнал. Без поправки
-- признак остаётся истинным навсегда: ученик не вернулся — значит завтра
-- заведём тот же сигнал заново, и так до бесконечности.
closed AS (
    SELECT student_id, MAX(COALESCE(acknowledged_at, created_at)) AS closed_at
    FROM learning_gap_signal
    WHERE reason = 'dropout_risk'
      AND status IN ('resolved', 'dismissed')
      AND student_id IS NOT NULL
    GROUP BY student_id
)
SELECT u.id AS student_id,
       e.course_id,
       c.title AS course_title,
       l.lessons_in_window,
       ls.last_attended,
       w.last_own_work,
       EXTRACT(DAY FROM now() - w.last_own_work)::int AS silence_days
FROM users u
JOIN user_roles ur ON ur.user_id = u.id
JOIN roles r ON r.id = ur.role_id AND r.name = 'student'
JOIN enrolled e ON e.user_id = u.id
JOIN courses c ON c.id = e.course_id
JOIN lessons l ON l.student_id = u.id
JOIN own_work w ON w.user_id = u.id
LEFT JOIN last_seen ls ON ls.student_id = u.id
LEFT JOIN closed ON closed.student_id = u.id
CROSS JOIN win
WHERE u.is_active
  AND u.merged_into_user_id IS NULL
  AND u.blocked_at IS NULL
  -- Преподаватель заведён и как ученик, но участником занятий не бывает; всё
  -- же отсекаем явно: одна карточка про коллегу обесценивает весь список.
  --
  -- Смотреть надо ВСЕ ТРИ места: у занятия ведущий хранится и колонкой
  -- `lesson_occurrence.teacher_id`, и строками `lesson_occurrence_teacher`
  -- (несколько ведущих, tsk-443). Первая версия проверяла только таблицы — и
  -- тест поймал на этом преподавателя, чьи занятия заведены старым способом.
  AND NOT EXISTS (SELECT 1 FROM lesson_occurrence lo WHERE lo.teacher_id = u.id)
  AND NOT EXISTS (SELECT 1 FROM lesson_occurrence_teacher lt WHERE lt.teacher_id = u.id)
  AND NOT EXISTS (SELECT 1 FROM lesson_slot_teacher st WHERE st.teacher_id = u.id)
  -- Занятия шли, и он не был ни на одном.
  AND l.attended = 0
  -- И сам в кабинете за это время не работал ни разу.
  AND w.last_own_work < win.since
  -- Перерыв задан ДАТАМИ школы, а окно — моментами времени; сервер живёт в UTC.
  -- Без приведения к московской дате границы уезжают на сутки (то же правило,
  -- что и в `break_service._LOCAL_DAY`).
  AND NOT EXISTS (
      SELECT 1 FROM student_break b
      WHERE b.student_id = u.id
        AND b.ends_on >= (win.since AT TIME ZONE 'Europe/Moscow')::date
        AND b.starts_on <= (now() AT TIME ZONE 'Europe/Moscow')::date
  )
  -- Разобранный сигнал поднимается заново, только если человек успел вернуться
  -- и затих снова.
  AND (closed.closed_at IS NULL
       OR w.last_own_work > closed.closed_at
       OR ls.last_attended > closed.closed_at)
ORDER BY w.last_own_work
LIMIT :limit
"""


def _setting_int(key: str, fallback: int) -> int:
    """Числовой порог из настроек школы; не прочитался — берём запасной.

    Датчик не должен умолкать из-за настроек: молчащий сигнал выглядит как
    «всё хорошо», а это худшая из возможных ошибок здесь (tsk-721).
    """
    try:
        return settings_store.get_int(key)
    except Exception:
        logger.warning("сигналы: настройка %s не прочиталась, беру %s", key, fallback)
        return fallback


def _setting_float(key: str, fallback: float) -> float:
    """То же для долей."""
    try:
        return settings_store.get_float(key)
    except Exception:
        logger.warning("сигналы: настройка %s не прочиталась, беру %s", key, fallback)
        return fallback


def ai_window_days() -> int:
    """Окно признака ИИ-авторства из настроек, с запасным значением."""
    return _setting_int("ai_signal_window_days", AI_WINDOW_DAYS)


def dropout_window_days() -> int:
    """Окно признака «затих» из настроек, с запасным значением.

    Читается на каждом проходе, а не при импорте модуля: иначе смена порога
    требовала бы перезапуска — то есть выката, которого правило и избегает.
    """
    return _setting_int("dropout_risk_window_days", DROPOUT_WINDOW_DAYS)


async def find_dropout_risk(
    db: AsyncSession,
    *,
    days: int | None = None,
    limit: int = 100,
) -> list[dict]:
    """Ученики, которые затихли: занятия идут мимо них, и сами они не работают.

    Отдельный датчик, а не порог внутри существующих: те меряют, КАК ученик
    учится, этот — учится ли вообще. Ученик из первой строки августовского
    замера ошибок не делал вовсе — ему просто нечем было их сделать.

    Признак срабатывает не раньше чем через две недели после отвала: это не
    предсказание ухода, а раннее обнаружение вместо позднего. Сегодня
    преподаватель узнаёт постфактум — по данным августа он узнавал бы на
    2–4 недели раньше.
    """
    sql = _DROPOUT_RISK_SQL.format(real_student=real_student_results_filter("tr"))
    rows = (await db.execute(text(sql), {
        "days": days if days is not None else dropout_window_days(),
        "limit": limit,
    })).mappings().all()
    return [dict(r) for r in rows]


async def upsert_signal(
    db: AsyncSession, *, course_id: int, student_id: int | None,
    submissions: int, students: int, wrong_rate: float,
    reason: str = REASON_ERROR_RATE, meta: dict | None = None,
) -> int | None:
    """Завести сигнал, если открытого такого ещё нет.

    Повтор молча пропускается: cron ходит по расписанию, и без этого за неделю
    накопилось бы семь одинаковых записей — верный способ отучить людей их
    читать. Единственность держит частичный уникальный индекс в БД, и с
    tsk-653 он включает `reason`: сигналы разных поводов по одной паре
    «курс + ученик» — разные сигналы, а не повтор.

    :param reason: повод (`error_rate` | `ai_authorship`).
    :param meta: числа повода. У каждого повода они свои, поэтому лежат здесь,
        а не в колонках. Без них по строке через месяц не понять, откуда она.
    """
    res = await db.execute(text("""
        INSERT INTO learning_gap_signal
            (course_id, student_id, submissions, students, wrong_rate, status, reason, meta)
        VALUES (:cid, :sid, :subs, :studs, :rate, 'new', :reason,
                CAST(:meta AS jsonb))
        ON CONFLICT DO NOTHING
        RETURNING id
    """), {"cid": course_id, "sid": student_id, "subs": submissions,
           "studs": students, "rate": wrong_rate, "reason": reason,
           "meta": json.dumps(meta, ensure_ascii=False) if meta else None})
    row = res.first()
    return int(row[0]) if row else None


async def scan_and_create_signals(db: AsyncSession, *, days: int = 30) -> dict:
    """Полный проход датчика: темы и ученики. Вызывается по расписанию.

    Итог пишется в лог ВСЕГДА, даже когда сигналов ноль: молчащий cron
    неотличим от отсутствующего, и именно так молчаливый отказ живёт годами.
    """
    topics = await find_topic_gaps(db, days=days)
    students = await find_student_gaps(db, days=days)
    authorship = await find_ai_authorship_gaps(db)
    dropout = await find_dropout_risk(db)

    new_topics = 0
    for g in topics:
        if await upsert_signal(
            db, course_id=g.course_id, student_id=None,
            submissions=g.submissions, students=g.students, wrong_rate=g.wrong_rate,
        ):
            new_topics += 1

    new_students = 0
    for r in students:
        if await upsert_signal(
            db, course_id=int(r["course_id"]), student_id=int(r["student_id"]),
            submissions=int(r["submissions"]), students=1,
            wrong_rate=float(r["wrong_rate"]),
        ):
            new_students += 1

    new_authorship = 0
    for r in authorship:
        if await upsert_signal(
            db, course_id=int(r["course_id"]), student_id=int(r["student_id"]),
            submissions=int(r["reviewed"]), students=1,
            # У этого повода доля ошибок ничего не значит и у ученика она обычно
            # нулевая. Ставим настоящий ноль, а не долю работ с признаком: это
            # то самое число, которое человек читает первым, и врать в нём
            # нельзя. Смысл несут `meta` и текст карточки.
            wrong_rate=0.0,
            reason=REASON_AI_AUTHORSHIP,
            meta={
                "reason": REASON_AI_AUTHORSHIP,
                "reviewed": int(r["reviewed"]),
                "flagged": int(r["flagged"]),
                "window_days": ai_window_days(),
            },
        ):
            new_authorship += 1

    new_dropout = 0
    dropout_window = dropout_window_days()
    for r in dropout:
        if await upsert_signal(
            db, course_id=int(r["course_id"]), student_id=int(r["student_id"]),
            # Число, которое человек читает первым, — сколько занятий прошло
            # мимо ученика. Доли ошибок у этого повода нет: он про то, что
            # ученик не работал вовсе, а не про то, как он работал.
            submissions=int(r["lessons_in_window"]), students=1,
            wrong_rate=0.0,
            reason=REASON_DROPOUT_RISK,
            meta={
                "reason": REASON_DROPOUT_RISK,
                "window_days": dropout_window,
                "lessons_missed": int(r["lessons_in_window"]),
                "silence_days": int(r["silence_days"]),
                # None — «не был ни разу»: не то же самое, что «давно не был», и
                # преподавателю это разные разговоры.
                "last_attended": (
                    r["last_attended"].date().isoformat()
                    if r["last_attended"] is not None else None
                ),
            },
        ):
            new_dropout += 1

    await db.commit()
    logger.info(
        "learning_gaps: проход завершён — тем найдено %s (новых сигналов %s), "
        "учеников %s (новых %s), признак авторства %s (новых %s), "
        "затихших %s (новых %s), период %s дн.",
        len(topics), new_topics, len(students), new_students,
        len(authorship), new_authorship, len(dropout), new_dropout, days,
    )
    return {
        "topics_found": len(topics), "topic_signals_created": new_topics,
        "students_found": len(students), "student_signals_created": new_students,
        "authorship_found": len(authorship),
        "authorship_signals_created": new_authorship,
        "dropout_found": len(dropout),
        "dropout_signals_created": new_dropout,
    }


async def acknowledge_signal(
    db: AsyncSession, *, signal_id: int, teacher_id: int,
    comment: str | None = None, escalate: bool = False,
) -> bool:
    """Преподаватель принял сигнал к сведению.

    `escalate=False` — «принял, разберусь сам на занятии». Это нормальный исход,
    а не бездействие: у преподавателя есть живой канал, которого у методиста нет.
    `escalate=True` — уходит методисту вместе с комментарием.
    """
    status = "escalated" if escalate else "acknowledged"
    # Признак передаётся отдельным булевым параметром, а не сравнением `:st`
    # с литералом внутри CASE: драйвер не может вывести тип параметра в таком
    # сравнении и роняет транзакцию целиком, а не только этот запрос.
    res = await db.execute(text("""
        UPDATE learning_gap_signal
        SET status = :st,
            teacher_id = :tid,
            teacher_comment = COALESCE(:comment, teacher_comment),
            acknowledged_at = COALESCE(acknowledged_at, now()),
            escalated_at = CASE WHEN :is_escalation THEN now() ELSE escalated_at END
        WHERE id = :sid AND status IN ('new', 'acknowledged')
        RETURNING id
    """), {"sid": signal_id, "tid": teacher_id, "comment": comment,
           "st": status, "is_escalation": escalate})
    ok = res.first() is not None
    if not ok:
        return False
    await db.commit()

    if escalate:
        await _notify_methodist(db, signal_id=signal_id, teacher_id=teacher_id)
    return True


async def _notify_methodist(db: AsyncSession, *, signal_id: int, teacher_id: int) -> None:
    """Сообщить методистам о переданном сигнале.

    Вызывается ПОСЛЕ коммита решения преподавателя и не имеет права его
    отменить: если письмо не ушло, сигнал всё равно лежит у методиста на экране,
    а вот откат решения означал бы, что нажатие кнопки ничего не сделало.
    Поэтому исключение сюда не выпускается — только след в логе.
    """
    from app.services import methodist_notify_service

    try:
        row = (await db.execute(text("""
            SELECT s.id, s.course_id, c.title AS course_title, s.student_id,
                   u.full_name AS student_name, s.wrong_rate, s.teacher_comment
            FROM learning_gap_signal s
            JOIN courses c ON c.id = s.course_id
            LEFT JOIN users u ON u.id = s.student_id
            WHERE s.id = :sid
        """), {"sid": signal_id})).mappings().first()
        if row is None:
            return
        await methodist_notify_service.escalate_learning_gap(
            db,
            signal_id=int(row["id"]),
            course_id=int(row["course_id"]),
            course_title=str(row["course_title"]),
            student_id=row["student_id"],
            student_name=row["student_name"],
            teacher_id=teacher_id,
            wrong_percent=round(float(row["wrong_rate"]) * 100),
            comment=row["teacher_comment"],
        )
        await db.commit()
    except Exception:
        logger.exception(
            "learning_gaps: сигнал %s передан, но уведомить методистов не удалось — "
            "он всё равно виден у них на экране", signal_id,
        )


async def dismiss_signal(
    db: AsyncSession, *, signal_id: int, teacher_id: int, comment: str | None = None
) -> bool:
    """Отклонить сигнал: повторение не нужно.

    Отклонённые — не мусор. По ним видно, что датчик шумит (ученик болел,
    задание сломано, ошибка в эталоне), и это основание пересмотреть пороги, а
    не молча терпеть ложные срабатывания.
    """
    res = await db.execute(text("""
        UPDATE learning_gap_signal
        SET status = 'dismissed', teacher_id = :tid,
            teacher_comment = COALESCE(:comment, teacher_comment),
            acknowledged_at = COALESCE(acknowledged_at, now())
        WHERE id = :sid AND status IN ('new', 'acknowledged')
        RETURNING id
    """), {"sid": signal_id, "tid": teacher_id, "comment": comment})
    ok = res.first() is not None
    if ok:
        await db.commit()
    return ok


async def resolve_signal(
    db: AsyncSession, *, signal_id: int, methodist_id: int | None = None,
    comment: str | None = None, mini_course_id: int | None = None,
) -> bool:
    """Методист разобрал переданный сигнал — закрыть его.

    **Почему этого не было и почему это важно.** У эскалации не было выхода
    вовсе: `dismiss_signal` работает только из `new`/`acknowledged`, а из
    `escalated` закрыть сигнал было нечем. На 2026-08-23 в проде так висели
    5 сигналов, самый старый с 06.08. Это читалось как «методист их не
    разбирает» — а на деле кнопки, которая фиксирует разбор, не существовало,
    и экран копил вечную очередь из уже сделанной работы.

    `mini_course_id` не обязателен: разбор не всегда кончается курсом (можно
    поправить само задание, поговорить с преподавателем, признать сигнал
    неверным). Но если курс собран — ссылка на него единственное место, где
    видно, ЧЕМ кончилась эскалация.

    `methodist_id` пустой означает «закрыто не человеком» (сервисный ключ). Ноль
    сюда не пишется намеренно: в журнале это выглядело бы как «закрыл
    пользователь 0», то есть неправдой о том, кто принял решение.

    Возвращает False, если сигнал не найден или уже закрыт: повторное нажатие
    не должно выглядеть как успех.
    """
    res = await db.execute(text("""
        UPDATE learning_gap_signal
        SET status = 'resolved',
            teacher_comment = COALESCE(CAST(:comment AS text), teacher_comment),
            -- Типы заданы явно, потому что аргументы `jsonb_build_object`
            -- объявлены как `any`: вывести тип пустого `mini_course_id`
            -- драйверу неоткуда, а пустым он бывает штатно — разбор не всегда
            -- кончается курсом. (В присваивании текстовой колонке выше тип
            -- выводится и без приведения; оно оставлено для единообразия.)
            meta = COALESCE(meta, '{}'::jsonb) || jsonb_strip_nulls(jsonb_build_object(
                'resolved_by', CAST(:mid AS int),
                'resolved_at', to_jsonb(now()),
                'mini_course_id', CAST(:course_id AS int)
            ))
        WHERE id = :sid AND status = 'escalated'
        RETURNING id
    """), {"sid": signal_id, "mid": methodist_id, "comment": comment,
           "course_id": mini_course_id})
    ok = res.first() is not None
    if ok:
        await db.commit()
    return ok


async def list_signals(
    db: AsyncSession, *, for_student: bool | None = None,
    statuses: tuple[str, ...] = ("new", "acknowledged"), limit: int = 50,
) -> list[dict]:
    """Сигналы для показа.

    `for_student=True` — ученические (преподавателю), `False` — темы
    (методисту), `None` — все.
    """
    where = ["s.status = ANY(:statuses)"]
    if for_student is True:
        where.append("s.student_id IS NOT NULL")
    elif for_student is False:
        # НЕ просто «темы». На столе у методиста лежат две разные вещи: темы,
        # которые нашёл датчик, И ученические сигналы, которые ему ПЕРЕДАЛ
        # преподаватель. Живая проверка показала, чем оборачивается фильтр
        # только по темам: преподаватель нажимает «передать методисту», а у того
        # пусто. Оба считают, что дело сделано, — и не делает никто.
        where.append("(s.student_id IS NULL OR s.status = 'escalated')")
    clause = " AND ".join(where)
    rows = (await db.execute(text(f"""
        SELECT s.id, s.course_id, c.title AS course_title, s.student_id,
               u.full_name AS student_name, s.submissions, s.students,
               s.wrong_rate, s.status, s.teacher_comment, s.created_at,
               s.reason, s.meta
        FROM learning_gap_signal s
        JOIN courses c ON c.id = s.course_id
        LEFT JOIN users u ON u.id = s.student_id
        WHERE {clause}
        -- Порядок по СИЛЕ сигнала, а не по доле ошибок. Пока повод был один,
        -- это было одно и то же; с tsk-653 сортировка по `wrong_rate`
        -- отправляла сигнал о признаке авторства в самый низ списка — у него
        -- доля ошибок честно нулевая. Живой проход это и показал: карточка
        -- выехала методисту последней строкой с бейджем «0% ошибок».
        -- «Затих» идёт первым всегда, а не по числу: у остальных поводов речь о
        -- том, КАК ученик учится, и разговор можно отложить до занятия. Здесь
        -- ученика может не оказаться уже на следующем занятии, и ждать нечего.
        --
        -- Ключ 2.0, а не 1.0: доли не превышают единицы, но РАВНЫ ей у ученика
        -- со 100 % ошибок, а `created_at` у всех сигналов одного прохода
        -- одинаковый (`now()` — время начала транзакции). При равном ключе
        -- порядок становится произвольным, и живая проверка на проде это
        -- показала: карточка о риске ухода встала третьей, под двумя «100 %
        -- ошибок».
        ORDER BY CASE s.reason
                     WHEN 'dropout_risk' THEN 2.0
                     WHEN 'ai_authorship' THEN
                         COALESCE((s.meta->>'flagged')::float
                                  / NULLIF((s.meta->>'reviewed')::float, 0), 0)
                     ELSE s.wrong_rate
                 END DESC,
                 s.created_at DESC
        LIMIT :limit
    """), {"statuses": list(statuses), "limit": limit})).mappings().all()
    return [dict(r) for r in rows]
