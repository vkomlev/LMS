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
from app.services.learning_gaps_service import real_student_results_filter
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

#: tsk-648: поводы подойти к ученику, в порядке очерёдности. Ранг принадлежит
#: поводу, а не ученику: это очерёдность на одно занятие, а не оценка, которая
#: копится.
#:
#: Порядок исправлен 08.09 по ответу преподавателей — сначала было наоборот,
#: «молчал» стоял первым. Двое сказали одно и то же независимо: «застрял»
#: важнее. Довод Екатерины Алексеевой: «молчал ещё насколько ты помнишь —
#: человек смотрит видео по теории», а застрявший «может стесняться первым
#: попросить помощи, но он эффективен». Светлана Коротких: «когда сидишь на
#: занятии, как раз это и надо знать».
#:
#: Боевая база довод подтвердила буквально: из 51 эпизода простоя 28 случились
#: на материале, и в 25 из них ученик закрыл этот материал прямо в окне
#: «молчания» — то есть он его изучал (см. `_load_idle_on_lessons`).
_ATTENTION_ORDER = (
    "stuck",
    "idle_last_lesson",
    "missed_last_lesson",
    "homework_overdue",
    "help_asked",
)

#: С какого числа пропущенных подряд занятий пропуск становится поводом
#: подойти. Решение оператора 08.09 по предложению Кирилла Ладесова: «ученики
#: достаточно часто одно занятие пропускают». На боевой базе за две недели
#: повод срабатывал 30 раз, из них 18 — ровно один пропуск. Одиночный пропуск
#: не исчезает: он остаётся значком «Пропустил подряд: 1» в той же строке,
#: просто не поднимает человека наверх списка.
_MISSED_STREAK_FOR_ATTENTION = 2

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

#: Длительность занятия, когда в расписании её нет (tsk-874). Та же величина,
#: что и в `homework_volume_service`, где по расписанию определяется «работа
#: шла на уроке»: два ответа про один урок расходиться не должны.
_DEFAULT_LESSON_MINUTES = 60

# --- tsk-649: «пора усложнить» ------------------------------------------------
#
# Обратная сторона сводки: слабого видно по незачётам и заявкам помощи, а
# сильный не жалуется — он молча скучает. Признак отвечает на вопрос «кому
# рычаг выборки заданий (tsk-314/tsk-553) пора дёрнуть в сторону сложного».
#
# ЧЕГО ЗДЕСЬ НЕТ — СКОРОСТИ, И ЭТО ПРОВЕРЕНО. Разведка tsk-589/tsk-646 по
# боевой базе опровергла оба скоростных кандидата: доля сдач быстрее 30 секунд
# у самых быстрых учеников доходит до 100 % и силы не означает (часть заданий
# отвечается за секунды), а «проскакивает теорию» меряет темп простановки
# отметок преподавателем, а не чтение ребёнка. Признак на скорости пометил бы
# старательных.
#
# Уровни сложности: 3 NORMAL, 4 HARD, 5 PROJECT. «Лёгкие» (2) и «теорию» (1)
# в базу признака не берём: после переоценки сложности (tsk-389) 66 % курса
# стало лёгким, и доля верных на них не различает учеников вовсе.
_HARDER_DIFFICULTY_IDS = (3, 4, 5)

#: Окно наблюдения. Не «между занятиями», как у поводов подойти: «пора
#: усложнить» — вывод об уровне человека, а не событие этой недели, и на окне в
#: неделю нелёгких заданий у большинства просто не набирается.
_HARDER_WINDOW_DAYS = 60

#: Сколько нелёгких заданий должно быть решено, чтобы вообще судить. На проде
#: (2026-09-09, окно 60 дней) планку берут 43 ученика из 81 активного.
#:
#: Планка стоит на НЕЛЁГКИХ, а не на «сложных» намеренно. Первым заходом
#: признак строился на HARD — и оказался нечем питать: сложные вынесены в
#: опциональный подкурс (tsk-347), за 60 дней по ним всего 212 первых сдач у
#: 57 учеников, то есть по 2–3 задания на человека. «Три из трёх верно» — не
#: доказательство силы, а совпадение.
_HARDER_MIN_TASKS = 20

#: Доля решённого С ПЕРВОЙ ПОПЫТКИ, начиная с которой ученику предлагается
#: слишком простое. Распределение по проду среди учеников с достаточной
#: выборкой: медиана 79 %, p75 85 %, p90 93 %. Порог 90 % — верхняя десятая
#: часть: сигнал должен быть редким, иначе преподаватель перестанет его читать.
_HARDER_FIRST_TRY_RATE = 0.90

#: Самостоятельность: сколько заявок помощи и упоров в лимит попыток НА ТЕХ ЖЕ
#: нелёгких заданиях допустимо на одно задание. 0.05 — одна заявка на двадцать
#: заданий. Ноль был бы враньём в другую сторону: один вопрос за два месяца не
#: отменяет того, что человек идёт сам, а на боевых данных именно так терялся
#: ученик с 50 верными из 51.
_HARDER_MAX_HELP_RATE = 0.05

#: Считать «с первой попытки» по колонке `count_retry` НЕЛЬЗЯ: в боевом потоке
#: сдачи она никогда не проставляется и всегда равна 0 (см. шапку модуля).
#: Первая попытка определяется по факту — самая ранняя сдача ученика по этому
#: заданию.
#:
#: `{real_student}` — фильтр «это сдача САМОГО ученика» из
#: `learning_gaps_service`, единственное место, где живёт это правило. Мимо него
#: в выборку попала бы ручная простановка преподавателя, а её на проде больше,
#: чем настоящих сдач, и любая доля верных ушла бы в потолок.
_READY_FOR_HARDER_SQL = """
WITH firsts AS (
    SELECT tr.user_id, tr.task_id, t.difficulty_id, tr.is_correct,
           row_number() OVER (
               PARTITION BY tr.user_id, tr.task_id ORDER BY tr.received_at
           ) AS rn
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id AND t.is_active
    WHERE tr.user_id = ANY(:student_ids)
      AND {real_student}
      AND tr.received_at > now() - make_interval(days => :days)
      AND t.difficulty_id = ANY(:difficulty_ids)
      -- tsk-873: задания курсов подготовки в признак не идут. Там правит СРОК,
      -- а объём и состав уже подбираются автоматически (tsk-798): на замере
      -- 09.09 у всех четверых, кого признак назвал сильными, банк заданий был
      -- отдан целиком — методисту показывали рычаг, выкрученный до упора.
      -- Признак смотрит вверх по дереву: помечается КОРЕНЬ программы, а
      -- решает ученик задание подкурса.
      --
      -- tsk-877: и задания СЛУЖЕБНЫХ курсов — тех, что объясняют устройство
      -- сервиса или экзамена, а не учат предмету. Живая проверка 10.09: у
      -- Машталер признак держался на 24 заданиях, и все 24 были из «С чего
      -- начать: кабинет» и «Что за экзамен» — по два вопроса уровня NORMAL в
      -- каждом из 12 разделов. Порог в 20 нелёгких заданий закрывался
      -- служебным набором в одиночку, одинаковым у всей школы.
      AND NOT EXISTS (
          WITH RECURSIVE up AS (
              SELECT t.course_id AS id
              UNION
              SELECT cp.parent_course_id
                FROM up JOIN course_parents cp ON cp.course_id = up.id
          )
          SELECT 1 FROM up JOIN courses c ON c.id = up.id
           WHERE c.is_exam OR c.is_service
      )
),
solved AS (
    SELECT user_id,
           COUNT(*) AS tasks,
           COUNT(*) FILTER (WHERE is_correct) AS first_try_ok,
           COUNT(*) FILTER (WHERE difficulty_id <> 3) AS hard_tasks
    FROM firsts WHERE rn = 1 GROUP BY user_id
),
asked AS (
    SELECT le.student_id, COUNT(*) AS episodes
    FROM learning_events le
    -- Проверка на число ВНУТРИ того же выражения, а не отдельным условием в
    -- WHERE: порядок вычисления условий планировщик не обещает, и приведение
    -- типа может выполниться раньше фильтра. `payload` — свободный jsonb, и
    -- одно кривое событие уронило бы не признак, а панель занятия целиком, у
    -- всей группы и посреди урока.
    JOIN tasks t ON t.id = CASE
                        WHEN le.payload->>'task_id' ~ '^[0-9]+$'
                        THEN (le.payload->>'task_id')::int
                    END
                AND t.difficulty_id = ANY(:difficulty_ids)
    WHERE le.student_id = ANY(:student_ids)
      AND le.created_at > now() - make_interval(days => :days)
      AND le.event_type IN ('help_requested', 'attempt_limit_reached')
    GROUP BY le.student_id
)
SELECT s.user_id, s.tasks, s.first_try_ok, s.hard_tasks,
       COALESCE(a.episodes, 0) AS help_episodes
FROM solved s LEFT JOIN asked a ON a.student_id = s.user_id
"""

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

    **Эпизод, на котором ученик изучал материал, молчанием не считается.**
    Замечание преподавателя (08.09): «молчал на прошлом занятии — это ещё
    насколько ты помнишь, человек смотрит видео по теории». Проверка боевой
    базы его подтвердила: из 51 эпизода 28 случились на материале, и в 25 из
    них ученик закрыл материал прямо в окне эпизода. Датчик простоя (tsk-591)
    считает бездействием отсутствие кликов, а просмотр ролика кликов и не
    требует. Поэтому эпизод отбрасывается, если в его окне (плюс четверть часа
    после — отметка прохождения приходит следом) есть закрытый материал.
    Сам датчик не трогаем: там сигнал нужен по ходу занятия, здесь — повод
    подойти на следующем.
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
                "FROM lesson_idle_episode e "
                "WHERE occurrence_id = ANY(:occ_ids) AND student_id = ANY(:student_ids) "
                # Ученик, закрывший материал в окне эпизода, всё это время его
                # изучал — молчанием это считать нельзя (см. докстринг).
                "  AND NOT EXISTS ( "
                "      SELECT 1 FROM student_material_progress smp "
                "      WHERE smp.student_id = e.student_id "
                "        AND smp.completed_at BETWEEN e.silent_since "
                "            AND COALESCE(e.resolved_at, e.detected_at) + interval '15 minutes' "
                "  ) "
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


async def _load_ready_for_harder(
    db: AsyncSession, *, student_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Кому пора усложнить — один запрос на всю группу (tsk-649).

    Возвращает только тех, кто признак прошёл: у остальных на экране не должно
    быть ни пометки, ни намёка на неё. «Не сработало» тут значит «нет повода
    менять человеку набор заданий», а не «слабый» — превращать отсутствие
    признака в оценку нельзя.

    Чего признак НЕ утверждает: что работа сделана честно. Списывание у ИИ
    выглядит ровно так же — верно с первой попытки и без вопросов (tsk-646,
    там же дыра с развёрнутыми ответами). Поэтому подпись говорит про задания,
    а не про ученика, и решение остаётся за человеком.
    """
    if not student_ids:
        return {}
    rows = (await db.execute(
        text(_READY_FOR_HARDER_SQL.format(
            real_student=real_student_results_filter("tr"),
        )),
        {
            "student_ids": student_ids,
            "days": _HARDER_WINDOW_DAYS,
            "difficulty_ids": list(_HARDER_DIFFICULTY_IDS),
        },
    )).mappings().all()

    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        tasks = int(r["tasks"])
        if tasks < _HARDER_MIN_TASKS:
            continue
        first_try_ok = int(r["first_try_ok"])
        if first_try_ok < tasks * _HARDER_FIRST_TRY_RATE:
            continue
        if int(r["help_episodes"]) > tasks * _HARDER_MAX_HELP_RATE:
            continue
        hard_tasks = int(r["hard_tasks"])
        detail = (
            f"с первой попытки {first_try_ok} из {tasks} непростых заданий "
            f"за {_HARDER_WINDOW_DAYS} дней, помощи не просил"
        )
        if hard_tasks:
            detail = (
                f"{detail}; сложных среди них {hard_tasks} "
                + _plural(hard_tasks, "задание", "задания", "заданий")
            )
        out[int(r["user_id"])] = {
            "tasks": tasks,
            "first_try_ok": first_try_ok,
            "percent": round(100 * first_try_ok / tasks),
            "hard_tasks": hard_tasks,
            "window_days": _HARDER_WINDOW_DAYS,
            "detail": detail,
        }
    return out


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
    отмечен в плане занятия (tsk-743). Пропуск — самый частый повод, и без
    такого снятия он вытеснил бы остальные; с 08.09 к этому добавлен порог
    ``_MISSED_STREAK_FOR_ATTENTION`` — одиночный пропуск виден значком в
    строке, но наверх списка не поднимает.
    """
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
            "rank": 1,
            "reason": "stuck",
            "detail": detail,
            "task_id": first["task_id"],
        }

    if idle:
        detail = f"молчал {idle['minutes']} мин на прошлом занятии"
        if idle["kind"] == "away":
            detail = f"{detail} (кабинет был закрыт)"
        return {"rank": 2, "reason": "idle_last_lesson", "detail": detail, "task_id": None}

    if missed_streak >= _MISSED_STREAK_FOR_ATTENTION and not absence_asked:
        detail = (
            f"пропустил подряд: {missed_streak} "
            + _plural(missed_streak, "занятие", "занятия", "занятий")
        )
        if prev_started_at is not None:
            local_day = prev_started_at.astimezone(_SCHOOL_TZ).strftime("%d.%m")
            detail = f"{detail}, последнее — {local_day}"
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
    # tsk-874: границы САМОГО занятия — окно для «что сделано на уроке».
    # Длительность может быть пустой: тогда берём час, как и везде, где
    # расписание отвечает на вопрос «урок ещё идёт».
    lesson_started_at = occurrence.scheduled_at
    lesson_ends_at = lesson_started_at + timedelta(
        minutes=occurrence.duration_minutes or _DEFAULT_LESSON_MINUTES
    )

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
        # tsk-874: сделанное НА ЭТОМ занятии — отдельно от недели. Требование
        # оператора 10.09: экраны «сводка перед занятием» и «итоги после»
        # выглядели одинаково, хотя вопросы у них разные. Считаем тем же
        # счётчиком, только окном урока: «сделал» на двух экранах обязано
        # означать одно и то же.
        #
        # Занятие ещё не началось — `None`, а не нули: ноль читается как «был
        # и ничего не сделал», и на экране итогов это прямая неправда.
        current_lesson = None
        if lesson_started_at <= now_utc:
            current_lesson = await load_homework_window(
                db,
                student_id=p.student_id,
                window_from=lesson_started_at,
                window_to=min(now_utc, lesson_ends_at),
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
            "current_lesson": current_lesson,
            "prev_started_at": prev_started_at,
        })

    # tsk-648: очерёдность внимания. Считается после цикла — простой на
    # прошлом занятии берётся одним запросом на всю группу.
    idle_by_student = await _load_idle_on_lessons(db, pairs=prev_lesson_pairs)
    absence_asked = await _load_absence_followups(db, pairs=prev_lesson_pairs)
    # tsk-649: «пора усложнить» живёт РЯДОМ с очерёдностью внимания, а не
    # внутри неё. Повод подойти — про цену бездействия сегодня, и подмешать
    # туда сильного ученика значит испортить смысл самого списка: он бы встал
    # выше тех, кому действительно нужна помощь.
    harder_by_student = await _load_ready_for_harder(db, student_ids=student_ids)
    for row in result_participants:
        row["ready_for_harder"] = harder_by_student.get(row["student_id"])
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
