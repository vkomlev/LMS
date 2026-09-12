"""Домашняя работа: выдача, состав, срок, выполнение (tsk-741, фаза 3).

Что здесь есть и чего намеренно нет:

- **Выдача** (`issue`) собирает состав из СЛЕДУЮЩИХ незавершённых элементов
  программы ученика — в том же учебном порядке, в каком их отдаёт дерево курса.
  Отсюда бесплатно выполняется требование «теорию учить дома»: материалы в
  дереве стоят перед заданиями своего узла, и в выдачу они попадают первыми.
  Своего порядка здесь не заводится — иначе домашняя работа однажды разошлась
  бы с тем, что показывает ученику кабинет.
- **Выполнение НЕ хранится.** Считается у источника: задание — есть верный
  результат, материал — есть отметка прохождения. Ученик работает обычным
  путём из кабинета, а не «внутри ДЗ»; своя колонка была бы вторым источником
  правды и разъехалась бы с фактом в первый же день.
- **Ручной зачёт преподавателя пункт ДЗ закрывает.** Здесь — намеренно, в
  отличие от расчёта темпа (`homework_volume_service`), где ручные зачёты
  отсечены. Это два разных вопроса: «сделано ли задание» решает преподаватель
  (зачёл — значит закрыто, иначе он видел бы красную отметку, которую сам же
  и снял), а «с какой скоростью работает человек» — вопрос про его
  собственные сдачи, и ручные зачёты там задрали бы норму до недостижимой.
- **Отменённые выдачи остаются.** `cancelled_at` вместо удаления: преподаватель
  должен видеть, что задавал и почему передумал, а счётчики за прошлые недели
  не должны меняться задним числом.

Одна действующая выдача на ученика: новая гасит предыдущую (`cancelled_at`).
Иначе «текущее ДЗ» перестаёт быть определённым — а именно этот вопрос задаёт и
ученик в кабинете, и преподаватель перед занятием.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import (
    course_activity_service,
    homework_volume_service,
    program_scope_service,
)
# tsk-867: вес элемента — состав набирается до бюджета времени, а не до штук.
from app.services.task_effort_service import (
    MATERIAL_EFFORT_SECONDS_PROXY,
    EffortTable,
    effort_for_tasks,
    load_effort_table,
)

logger = logging.getLogger(__name__)

#: Статусы дерева, означающие «с элементом покончено» — брать в ДЗ нечего.
_DONE_STATUSES = ("PASSED", "COMPLETED", "SKIPPED")

#: Задание, упершееся в лимит попыток, в домашнюю работу не кладём: ученик
#: физически не сможет его сдать, а в сводке оно будет висеть невыполненным.
_UNASSIGNABLE_TASK_STATUSES = ("BLOCKED_LIMIT",)

#: tsk-882: сколько пунктов сверх набора просмотреть вперёд. Нужно, чтобы
#: увидеть начало СЛЕДУЮЩЕЙ темы: без запаса набор кончается ровно на границе
#: и «что идёт дальше» неизвестно.
_THEORY_AHEAD_LOOKAHEAD = 20

#: Сколько материалов следующей темы уходит домой сверх бюджета. Требование
#: оператора 10.09: теорию читают дома, занятие — для работы, поэтому при
#: закрытии темы теория следующей задаётся ОБЯЗАТЕЛЬНО, даже если недельный
#: бюджет уже выбран. Потолок затем, что у тем разный размер: медиана — 4
#: материала, девять из десяти тем укладываются в семь, но есть и курс на 29,
#: и вот он превратил бы домашнюю работу в марафон.
_THEORY_AHEAD_MAX_ITEMS = 6


#: Корни ученика вне программы — сначала тот, где он работал последним (tsk-913).
#: Без активности вовсе — в порядке записи, как раньше: другого ориентира нет.
_ROOTS_BY_ACTIVITY_SQL = """
WITH RECURSIVE roots AS (
    SELECT uc.course_id AS root, uc.course_id, uc.order_number
      FROM user_courses uc
     WHERE uc.user_id = :sid AND uc.is_active = true
    UNION
    SELECT r.root, cp.course_id, r.order_number
      FROM roots r
      JOIN course_parents cp ON cp.parent_course_id = r.course_id
),
last_task AS (
    SELECT r.root, max(tr.submitted_at) AS at
      FROM roots r
      JOIN tasks t ON t.course_id = r.course_id
      JOIN task_results tr ON tr.task_id = t.id AND tr.user_id = :sid
     GROUP BY r.root
),
last_material AS (
    SELECT r.root, max(coalesce(smp.completed_at, smp.skipped_at)) AS at
      FROM roots r
      JOIN materials m ON m.course_id = r.course_id
      JOIN student_material_progress smp
        ON smp.material_id = m.id AND smp.student_id = :sid
     GROUP BY r.root
)
SELECT r.root
  FROM (SELECT DISTINCT root, order_number FROM roots) r
  LEFT JOIN last_task lt ON lt.root = r.root
  LEFT JOIN last_material lm ON lm.root = r.root
 ORDER BY greatest(lt.at, lm.at) DESC NULLS LAST,
          r.order_number ASC NULLS LAST, r.root
"""


async def _program_roots(db: AsyncSession, *, student_id: int) -> list[int]:
    """Корневые курсы программы подготовки ученика (tsk-869).

    Пустой список — ученик вне программ ЕГЭ/ОГЭ; тогда домашнюю работу берут
    из всех его курсов, как было раньше: других ориентиров у такого ученика
    нет.

    Программу определяет `homework_volume_service` — здесь она НЕ выбирается
    заново, иначе «программа ученика» стала бы двумя разными ответами в двух
    местах (у ОГЭшника, которому открыли материалы ЕГЭ, они разошлись бы
    сразу).
    """
    program = await homework_volume_service.program_for_student(
        db,
        student_id=student_id,
        grade=(
            await db.execute(
                text("SELECT school_grade FROM users WHERE id = :uid"),
                {"uid": student_id},
            )
        ).scalar(),
        today=datetime.now(timezone.utc).date(),
    )
    return list(program["root_ids"]) if program else []


async def _next_items(
    db: AsyncSession,
    *,
    student_id: int,
    limit: int,
    minutes_budget: Optional[int] = None,
    effort_table: Optional[EffortTable] = None,
) -> list[dict[str, Any]]:
    """Следующие незавершённые элементы программы ученика — до бюджета.

    Идёт по корневым курсам в порядке `user_courses.order_number` и внутри
    каждого — по учебному порядку дерева (`manual_progress_service.
    get_student_progress`). Дорого (обход дерева), но выдача бывает раз в
    занятие на ученика; чтение готового ДЗ этот путь не трогает вовсе.

    **Набор идёт до БЮДЖЕТА ВРЕМЕНИ** (`minutes_budget`, tsk-867), а `limit` —
    ограждение сверху по штукам. Ограничения работают вместе, и кончается
    набор по тому, которое сработает раньше:

    * без ограждения серия заданий с выбором ответа по 12-15 секунд набрала бы
      недельные 75 минут только на трёхстах штуках — это не учебная работа;
    * без бюджета времени двадцать задач с решением дают 79 минут там, где
      двадцать тестов дают четыре, — то есть ровно тот разброс, ради которого
      задача и заводилась.

    Первый элемент берётся всегда, даже если он один перекрывает бюджет:
    выдача без состава бессмысленна, а «задача на 40 минут» — это законный
    состав из одного пункта, а не ошибка расчёта.

    `minutes_budget=None` (вес не измерен) — набор по штукам, как раньше.
    """
    # Локальный импорт: `manual_progress_service` тянет движок и репозитории,
    # а этот модуль зовут из сводки преподавателя — цикла быть не должно.
    from app.services import manual_progress_service
    from app.services.content_grace_service import compute_graced_items

    # tsk-869: у ученика программы подготовки состав берётся ТОЛЬКО из её
    # курсов. Раньше обход шёл по всем записям `user_courses`, и домой уходило
    # что угодно из соседних курсов — на проде так попадали «Собираем
    # Бот-Угадайку», «Знакомство с SQLite», вводные курсы школы. К экзамену
    # это не готовит, а место в недельном объёме занимает.
    #
    # Вне программ подготовки поведение прежнее: там «программа ученика» — и
    # есть все его курсы, других ориентиров нет.
    roots = await _program_roots(db, student_id=student_id)
    if not roots:
        # tsk-913: вне программы курсы идут в порядке ПОСЛЕДНЕЙ АКТИВНОСТИ, а
        # не записи. Правило оператора 12.09: «ДЗ должно быть по курсу, над
        # которым сейчас работает ученик». Порядок записи это не отражает:
        # первым в нём стоит то, на что записали раньше всех, — обычно летний
        # курс, который забросили, — и домой уходили его хвосты, пока ученик
        # уже занимался другим. Активность считается по ВСЕМУ дереву корня:
        # ученик работает в подкурсах, а записан на корень.
        roots = list(
            (await db.execute(text(_ROOTS_BY_ACTIVITY_SQL), {"sid": student_id}))
            .scalars()
            .all()
        )

    # tsk-886: курсы, выведенные из работы, в подбор не идут — ни корнем, ни
    # темой внутри дерева. Каскада нет (граница tsk-873): выключенный корень не
    # выключает подкурсы, поэтому проверяется КАЖДЫЙ узел, а не только корень.
    # Берём весь список разом: он ограничен числом выведенных курсов (на 10.09
    # их ноль из 843), а спрашивать про каждый узел обхода было бы дороже.
    inactive_courses = await course_activity_service.load_inactive_course_ids(db)
    roots = [c for c in roots if int(c) not in inactive_courses]

    # tsk-882: собираем НЕ ТОЛЬКО набор, но и хвост за ним — что идёт следом
    # по учебному порядку. Без хвоста не ответить на вопрос «закрывает ли эта
    # выдача тему» и нечем взять теорию следующей.
    pending: list[dict[str, Any]] = []
    lookahead = limit + _THEORY_AHEAD_LOOKAHEAD
    for course_id in roots:
        if len(pending) >= lookahead:
            break
        progress = await manual_progress_service.get_student_progress(
            db, student_id=student_id, course_id=int(course_id)
        )
        # tsk-838: содержимое, добавленное в курс ПОСЛЕ того, как ученик прошёл
        # тему, для него необязательно (правило tsk-692) — движок такие элементы
        # не предлагает и не считает в прогрессе. Выдача про это правило не
        # знала и клала их в домашнюю работу: ученица прошла «Первую программу»
        # 21 июля, 7 сентября в курс досыпали два задания, и 8 сентября они
        # пришли ей на дом как долг по пройденной теме.
        graced = await compute_graced_items(db, student_id, int(course_id))

        for item in progress.get("items", []):
            if len(pending) >= lookahead:
                break
            if item["item_type"] not in ("task", "material"):
                continue
            if item["status"] in _DONE_STATUSES:
                continue
            # tsk-886: тема (подкурс) выведена из работы — её содержимое домой
            # не задаём, хотя в прогрессе ученика оно по-прежнему видно.
            if int(item["course_id"]) in inactive_courses:
                continue
            item_id = int(item["item_id"])
            if item["item_type"] == "task" and item_id in graced.tasks:
                continue
            if item["item_type"] == "material" and item_id in graced.materials:
                continue
            if (
                item["item_type"] == "task"
                and item["status"] in _UNASSIGNABLE_TASK_STATUSES
            ):
                continue
            pending.append(
                {
                    "kind": item["item_type"],
                    "item_id": int(item["item_id"]),
                    "title": item.get("title"),
                    # tsk-882: тема, которой принадлежит пункт. Нужна только
                    # здесь и наружу не отдаётся — снимается перед возвратом.
                    "topic_id": int(item["course_id"]),
                }
            )

    picked = pending[:limit]
    if minutes_budget is not None and effort_table is not None and picked:
        picked = _trim_to_budget(
            picked,
            minutes_budget=minutes_budget,
            weights=await effort_for_tasks(
                db,
                task_ids=[i["item_id"] for i in picked if i["kind"] == "task"],
                table=effort_table,
            ),
            table=effort_table,
        )
    return _strip_topics(_with_theory_ahead(picked, pending))


def _with_theory_ahead(
    picked: list[dict[str, Any]], pending: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Добавить теорию следующей темы, если эта выдача закрывает текущую.

    Требование оператора 10.09: «теорию желательно изучать дома (материалы и
    видео), поэтому если тема подходит к концу — обязательно задавать теорию
    на дом». Смысл в том, чтобы занятие уходило на работу, а не на чтение:
    ученик приходит, уже зная материал следующей темы.

    Условие «тема подходит к концу» берётся не порогом в процентах, а фактом:
    после этой выдачи в теме не остаётся ни одного незавершённого пункта.
    Порог пришлось бы подбирать, а факт виден точно — и ровно он означает,
    что на следующем занятии человек будет уже в новой теме.

    Материалы добавляются СВЕРХ бюджета времени, как и первый элемент выдачи:
    иначе правило не работало бы в самом частом случае — тема закрывается
    заданиями, набранными ровно под бюджет, и теории места уже нет.

    Ничего не делает, если выдача пуста, тему не закрывает или следующая тема
    начинается сразу с задания (теории у неё нет).
    """
    if not picked:
        return picked
    taken = {(i["kind"], i["item_id"]) for i in picked}
    tail = [i for i in pending if (i["kind"], i["item_id"]) not in taken]

    current_topic = picked[-1]["topic_id"]
    if any(i["topic_id"] == current_topic for i in tail):
        return picked  # тема не закрывается — теория следующей не к спеху

    ahead: list[dict[str, Any]] = []
    for item in tail:
        # Только ведущие материалы следующей темы: дошли до её первого
        # задания — теория кончилась, дальше уже работа.
        if item["kind"] != "material" or item["topic_id"] == current_topic:
            break
        ahead.append(item)
        if len(ahead) >= _THEORY_AHEAD_MAX_ITEMS:
            break
    if ahead:
        logger.info(
            "ДЗ: тема %s закрывается выдачей, добавляем теорию следующей: %s материалов",
            current_topic, len(ahead),
        )
    return picked + ahead


def _strip_topics(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Убрать служебное поле темы: наружу состав выдачи отдаётся без него."""
    return [{k: v for k, v in item.items() if k != "topic_id"} for item in items]


async def _items_minutes(
    db: AsyncSession, *, items: list[dict[str, Any]], table: EffortTable
) -> int:
    """Во сколько минут работы оценивается состав выдачи.

    Оценка, а не обещание: вес задания — медиана времени «открыл → ответил»
    без чтения теории и без повторных попыток, а вес материала вовсе прокси
    ([[tsk-868]]). Поэтому на экране число подписано «≈».
    """
    weights = await effort_for_tasks(
        db,
        task_ids=[i["item_id"] for i in items if i["kind"] == "task"],
        table=table,
    )
    seconds = 0.0
    for item in items:
        if item["kind"] == "material":
            # tsk-904: измеренный вес теории, когда наблюдений хватает.
            seconds += table.material_effort_seconds()
        else:
            seconds += weights.get(item["item_id"]) or table.overall or 0.0
    return int(round(seconds / 60))


def _trim_to_budget(
    picked: list[dict[str, Any]],
    *,
    minutes_budget: int,
    weights: dict[int, Optional[float]],
    table: EffortTable,
) -> list[dict[str, Any]]:
    """Обрезать набранный список по бюджету времени (tsk-867).

    Элемент, которого нет в таблице весов (задание удалено между обходом и
    взвешиванием), считается по общей медиане, а не пропускается: пропуск
    занизил бы бюджет молча и пустил бы в выдачу лишний пункт.
    """
    budget_seconds = minutes_budget * 60.0
    spent = 0.0
    result: list[dict[str, Any]] = []
    for item in picked:
        if item["kind"] == "material":
            cost = table.material_effort_seconds()
        else:
            cost = weights.get(item["item_id"]) or table.overall or 0.0
        # Первый пункт берётся всегда: выдача без состава бессмысленна, а одна
        # задача на сорок минут — законный состав, а не ошибка расчёта.
        if result and spent + cost > budget_seconds:
            break
        result.append(item)
        spent += cost
    return result


async def issue(
    db: AsyncSession,
    *,
    student_id: int,
    due_at: datetime,
    source: str,
    issued_by: Optional[int] = None,
    occurrence_id: Optional[int] = None,
    volume_override: Optional[int] = None,
    note: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Выдать домашнюю работу. Гасит предыдущую действующую выдачу.

    Args:
        db: async session (коммит — на вызывающем).
        student_id: кому.
        due_at: срок — обычно начало следующего занятия.
        source: `auto` (по формуле) или `teacher` (рука преподавателя).
        issued_by: кто выдал; None у автоматической выдачи.
        occurrence_id: занятие, после которого выдано.
        volume_override: сколько элементов задать вместо расчёта формулы —
            преподаватель вправе задать своё число, норма при этом всё равно
            считается и уходит в `volume_details` (иначе потом не понять, от
            чего он отступил и на сколько).
        note: комментарий преподавателя.
        now: момент выдачи (для тестов).

    Returns:
        Словарь выдачи — тот же, что отдаёт `get_current`.

    Raises:
        ValueError: срок в прошлом либо в программе не осталось элементов.
    """
    moment = now or datetime.now(timezone.utc)
    if due_at <= moment:
        raise ValueError("Срок домашней работы должен быть в будущем.")

    # tsk-798: объём программы пересчитывается ЗДЕСЬ, до подбора состава.
    # Порядок важен: план решает, какие задания вообще попадут ученику в обход,
    # и посчитай мы его после — первая выдача собралась бы по старому объёму.
    # Выдача происходит раз в несколько дней, то есть план обновляется ровно
    # с той частотой, с какой меняется картина, и без отдельного расписания.
    await program_scope_service.refresh_for_student(
        db, student_id=student_id, now=moment
    )

    plan = await homework_volume_service.compute(db, student_id=student_id, now=moment)
    days = max((due_at - moment).days, 1)
    minutes_budget = homework_volume_service.minutes_for_window(plan, days=days)

    # tsk-867: ведёт БЮДЖЕТ ВРЕМЕНИ, штуки остаются ограждением. Ограждением
    # служит штучный ПОТОЛОК, а не штучная норма: норма посчитана из того же
    # «факт × 1.2», что и минутная, и взяв её, мы бы отдали ведущую роль
    # обратно штукам — то есть не изменили бы ничего.
    #
    # Явное число преподавателя отменяет бюджет времени целиком: он сказал
    # «задай двенадцать», и получить в ответ четыре — не то, о чём он просил.
    if volume_override is not None:
        volume, minutes_budget = int(volume_override), None
    elif minutes_budget is not None:
        volume = max(
            int(round(
                homework_volume_service.ceiling_for(plan.fact_per_week)
                * max(days, 1) / 7.0
            )),
            1,
        )
    else:
        volume = homework_volume_service.volume_for_window(plan, days=days)
    if volume <= 0:
        raise ValueError(
            "Программа пройдена: ученик идёт с опережением, и задавать больше "
            "нечего. Добавьте ему курс — тогда домашняя работа появится снова."
        )

    effort_table = (
        await load_effort_table(db) if minutes_budget is not None else None
    )
    items = await _next_items(
        db,
        student_id=student_id,
        limit=volume,
        minutes_budget=minutes_budget,
        effort_table=effort_table,
    )
    if not items:
        raise ValueError(
            "Программа пройдена: ученик идёт с опережением, и задавать больше "
            "нечего. Добавьте ему курс — тогда домашняя работа появится снова."
        )

    await db.execute(
        text(
            "UPDATE homework_assignment SET cancelled_at = :now "
            " WHERE student_id = :sid AND cancelled_at IS NULL"
        ),
        {"sid": student_id, "now": moment},
    )

    details = plan.as_details()
    details["requested_volume"] = volume
    details["window_days"] = days
    if volume_override is not None:
        details["volume_override"] = int(volume_override)
    # tsk-867: снимок бюджета и фактического веса состава. Пересчитывать вес
    # при чтении нельзя: таблица весов живая, и «сколько минут было задано»
    # менялось бы задним числом при каждом открытии экрана.
    if minutes_budget is not None and effort_table is not None:
        details["minutes_budget"] = minutes_budget
        details["planned_minutes"] = await _items_minutes(
            db, items=items, table=effort_table
        )

    homework_id = (
        await db.execute(
            text(
                "INSERT INTO homework_assignment "
                "  (student_id, issued_at, due_at, source, issued_by, occurrence_id, "
                "   planned_volume, volume_details, note) "
                "VALUES (:sid, :now, :due, :source, :by, :occ, :vol, "
                "        CAST(:details AS jsonb), :note) "
                "RETURNING id"
            ),
            {
                "sid": student_id,
                "now": moment,
                "due": due_at,
                "source": source,
                "by": issued_by,
                "occ": occurrence_id,
                # Сколько ЗАДАНО, а не сколько разрешал расчёт: при бюджете
                # времени (tsk-867) штучный лимит — только ограждение, и набор
                # почти всегда кончается раньше него. Расчётное число осталось
                # в `volume_details.requested_volume`.
                "vol": len(items),
                "details": json.dumps(details, ensure_ascii=False),
                "note": note,
            },
        )
    ).scalar_one()

    for position, item in enumerate(items):
        await db.execute(
            text(
                "INSERT INTO homework_item (homework_id, kind, task_id, material_id, position) "
                "VALUES (:hid, :kind, :task_id, :material_id, :pos)"
            ),
            {
                "hid": homework_id,
                "kind": item["kind"],
                "task_id": item["item_id"] if item["kind"] == "task" else None,
                "material_id": item["item_id"] if item["kind"] == "material" else None,
                "pos": position,
            },
        )

    logger.info(
        "tsk-741: выдано ДЗ id=%s ученику %s — %s элементов, срок %s, источник %s",
        homework_id, student_id, len(items), due_at, source,
    )
    result = await get_current(db, student_id=student_id)
    assert result is not None  # только что вставили
    return result


#: Перерыв, до которого соседнее занятие считается той же парой. Сдвоенный
#: час — это один учебный блок: между его половинами домашней работы не бывает,
#: и срок «до второй пары» ученик выполнить физически не может. На проде такие
#: блоки у 22 учеников, 52 пары (замер 02.09).
PAIRED_LESSON_GAP_MINUTES = 20

#: Сколько дней даём на работу, если следующего занятия в расписании нет.
#: Неделя — шаг сетки школы: у большинства занятия раз в неделю, и «до
#: следующего» для них ровно столько.
_FALLBACK_DUE_DAYS = 7


async def next_due_for(
    db: AsyncSession,
    *,
    student_id: int,
    after: datetime,
    now: Optional[datetime] = None,
) -> datetime:
    """Срок домашней работы: начало СЛЕДУЮЩЕГО занятия ученика.

    Занятия в расписании нет — неделя от «сейчас». Общая функция для ручной
    выдачи и автоматической: два разных ответа на вопрос «до когда» означали
    бы, что преподаватель и система задают ДЗ на разные сроки.
    """
    moment = now or datetime.now(timezone.utc)
    gap = timedelta(minutes=PAIRED_LESSON_GAP_MINUTES)

    # Берём занятия, ЗАКАНЧИВАЮЩИЕСЯ после `after`, — то есть вместе с тем,
    # которое в этот момент идёт. Без него не собрать блок: сдвоенный час
    # склеивается по «конец предыдущего — начало следующего».
    rows = (
        await db.execute(
            text(
                "SELECT lo.scheduled_at, lo.duration_minutes "
                "  FROM lesson_occurrence lo "
                "  JOIN lesson_occurrence_participant lop "
                "    ON lop.occurrence_id = lo.id AND lop.student_id = :sid "
                " WHERE lop.status <> 'rescheduled' "
                "   AND lo.scheduled_at + (lo.duration_minutes || ' minutes')::interval "
                "       > :after "
                " ORDER BY lo.scheduled_at ASC LIMIT 20"
            ),
            {"sid": student_id, "after": after},
        )
    ).fetchall()

    block_end = after
    for scheduled_at, duration_minutes in rows:
        if scheduled_at <= block_end + gap:
            # То же занятие или вторая половина сдвоенного часа — срока здесь
            # нет: ученик всё это время сидит на уроке.
            block_end = max(
                block_end, scheduled_at + timedelta(minutes=int(duration_minutes))
            )
            continue
        if scheduled_at > moment:
            return scheduled_at
        # Занятие уже прошло (отмечают задним числом) — двигаем блок дальше.
        block_end = max(
            block_end, scheduled_at + timedelta(minutes=int(duration_minutes))
        )
    return moment + timedelta(days=_FALLBACK_DUE_DAYS)


async def auto_issue_after_lesson(
    db: AsyncSession,
    *,
    student_id: int,
    occurrence_id: int,
    occurrence_at: datetime,
    now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """Задать домашнюю работу сразу после занятия — по темпу и классу.

    Зовётся, когда преподаватель отметил, что ученик БЫЛ на занятии. Молчит и
    возвращает `None`, если:

    - выключен переключатель `homework_auto_issue_enabled` (по умолчанию —
      выключен, включение без выката);
    - по этому занятию уже выдавали (преподаватель поправляет статус задним
      числом — а каждая новая выдача гасит прежнюю, и ученик потерял бы то,
      что уже начал делать);
    - после начала занятия ученику уже что-то выдали — обычно это сам
      преподаватель нажал «Задать домашнюю работу» в карточке. Его выдача идёт
      без `occurrence_id`, и без этой ветки отметка явки затирала бы её своей;
    - задавать нечего (программа пройдена) или срок не собрался.

    Срок — начало СЛЕДУЮЩЕГО занятия ученика; нет такого в расписании —
    неделя. Ошибки не поднимает: отметка явки не должна падать из-за
    домашней работы.
    """
    from app.core import settings_store

    if not settings_store.get_bool("homework_auto_issue_enabled"):
        return None

    moment = now or datetime.now(timezone.utc)
    # «Уже задавали» — это не только «мы сами по этому занятию», но и «после
    # начала занятия ученику что-то выдали». Второе — про преподавателя: он
    # задаёт из карточки ученика, и та выдача идёт БЕЗ `occurrence_id`, потому
    # что кнопка живёт не в занятии. Проверка только по `occurrence_id` её не
    # видела, и отметка явки затирала работу преподавателя своей: ученик
    # получал один список, а задавали ему другой (вопрос оператора 02.09).
    already = (
        await db.execute(
            text(
                "SELECT 1 FROM homework_assignment "
                " WHERE student_id = :sid "
                "   AND (occurrence_id = :oid "
                "        OR (cancelled_at IS NULL AND issued_at >= :since)) "
                " LIMIT 1"
            ),
            {"sid": student_id, "oid": occurrence_id, "since": occurrence_at},
        )
    ).first()
    if already is not None:
        return None

    # Сдвоенный час: после первой пары домашней работы не бывает — ученик
    # сразу идёт на вторую. Выдать здесь значит дать задание со сроком «через
    # перемену» и через час погасить его следующей выдачей (вопрос оператора
    # 02.09). Ждём конца блока.
    paired_ahead = (
        await db.execute(
            text(
                # Перерыв подставлен константой проекта, а не параметром:
                # `(:gap || ' minutes')` требует строки и падает на int.
                "SELECT 1 "
                "  FROM lesson_occurrence cur "
                "  JOIN lesson_occurrence nxt ON nxt.scheduled_at > cur.scheduled_at "
                "  JOIN lesson_occurrence_participant lop "
                "    ON lop.occurrence_id = nxt.id AND lop.student_id = :sid "
                "   AND lop.status <> 'rescheduled' "
                " WHERE cur.id = :oid "
                "   AND nxt.scheduled_at <= cur.scheduled_at "
                "       + (cur.duration_minutes || ' minutes')::interval "
                f"       + interval '{PAIRED_LESSON_GAP_MINUTES} minutes' "
                " LIMIT 1"
            ),
            {"sid": student_id, "oid": occurrence_id},
        )
    ).first()
    if paired_ahead is not None:
        logger.info(
            "tsk-741: ДЗ ученику %s не выдаём после занятия %s — сдвоенный час, "
            "ждём конца блока",
            student_id, occurrence_id,
        )
        return None

    due_at = await next_due_for(
        db, student_id=student_id, after=occurrence_at, now=moment
    )

    try:
        return await issue(
            db,
            student_id=student_id,
            due_at=due_at,
            source="auto",
            occurrence_id=occurrence_id,
            now=moment,
        )
    except ValueError as exc:
        # Программа пройдена целиком — это не ошибка занятия.
        logger.info(
            "tsk-741: автовыдача ДЗ ученику %s пропущена: %s", student_id, exc
        )
        return None


#: Состав действующей выдачи с отметкой выполнения, посчитанной у источника.
#: Один запрос: экран ученика и сводка преподавателя зовут его часто.
_ITEMS_SQL = """
SELECT hi.id, hi.kind, hi.task_id, hi.material_id, hi.position,
       CASE hi.kind
            WHEN 'task' THEN EXISTS (
                SELECT 1 FROM task_results tr
                  JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
                 WHERE tr.user_id = :sid AND tr.task_id = hi.task_id
                   AND tr.is_correct = true
            )
            ELSE EXISTS (
                SELECT 1 FROM student_material_progress smp
                 WHERE smp.student_id = :sid AND smp.material_id = hi.material_id
                   AND smp.status IN ('completed', 'skipped')
            )
       END AS done,
       t.course_id AS task_course_id,
       m.title AS material_title,
       m.course_id AS material_course_id,
       -- tsk-838: из чего клиент строит ссылку НА САМ элемент. Адрес урока —
       -- `/courses/{course_uid}/task/{external_uid}`, то есть числовых id для
       -- него мало: нужен `course_uid` узла и внешний код задания.
       tc.course_uid AS task_course_uid,
       mc.course_uid AS material_course_uid,
       -- Название задания живёт в jsonb `task_content`, отдельной колонки нет
       -- (`project_lms_task_title_lives_in_task_content`); стем и внешний код
       -- нужны `humanize_task_title` как запасные имена.
       t.task_content->>'title' AS task_title,
       t.task_content->>'stem'  AS task_stem,
       t.external_uid           AS task_external_uid
  FROM homework_item hi
  LEFT JOIN tasks t ON t.id = hi.task_id
  LEFT JOIN materials m ON m.id = hi.material_id
  LEFT JOIN courses tc ON tc.id = t.course_id
  LEFT JOIN courses mc ON mc.id = m.course_id
 WHERE hi.homework_id = :hid
 ORDER BY hi.position
"""


async def _load_items(
    db: AsyncSession, *, homework_id: int, student_id: int
) -> list[dict[str, Any]]:
    """Состав выдачи с отметками выполнения."""
    from app.utils.task_title import humanize_task_title

    rows = (
        await db.execute(text(_ITEMS_SQL), {"hid": homework_id, "sid": student_id})
    ).mappings().fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        if row["kind"] == "task":
            title = humanize_task_title(
                int(row["task_id"]),
                row["task_title"],
                row["task_stem"],
                row["task_external_uid"],
            )
            course_id = row["task_course_id"]
            course_uid = row["task_course_uid"]
        else:
            title = row["material_title"]
            course_id = row["material_course_id"]
            course_uid = row["material_course_uid"]
        items.append(
            {
                "kind": row["kind"],
                "item_id": int(row["task_id"] or row["material_id"]),
                "course_id": int(course_id) if course_id is not None else None,
                "title": title,
                "done": bool(row["done"]),
                "position": int(row["position"]),
                # tsk-838: чтобы пункт списка открывался нажатием. Оба поля
                # необязательны: узел мог остаться без `course_uid`, и тогда
                # пункт просто не станет ссылкой — но и не исчезнет.
                "course_uid": course_uid,
                "external_uid": (
                    row["task_external_uid"] if row["kind"] == "task" else None
                ),
            }
        )
    return items


async def get_current(
    db: AsyncSession, *, student_id: int, now: Optional[datetime] = None
) -> Optional[dict[str, Any]]:
    """Действующая домашняя работа ученика или None, если её нет.

    «Действующая» — неотменённая и самая свежая. Просроченную не прячем:
    невыполненное ДЗ с прошедшим сроком — ровно то, что должен увидеть и
    ученик, и преподаватель перед занятием.
    """
    moment = now or datetime.now(timezone.utc)
    row = (
        await db.execute(
            text(
                "SELECT id, student_id, issued_at, due_at, source, issued_by, "
                "       occurrence_id, planned_volume, volume_details, note "
                "  FROM homework_assignment "
                " WHERE student_id = :sid AND cancelled_at IS NULL "
                " ORDER BY issued_at DESC, id DESC LIMIT 1"
            ),
            {"sid": student_id},
        )
    ).mappings().fetchone()
    if row is None:
        return None

    items = await _load_items(db, homework_id=int(row["id"]), student_id=student_id)
    done = sum(1 for i in items if i["done"])
    raw_details = row["volume_details"]
    planned_minutes = (
        raw_details.get("planned_minutes") if isinstance(raw_details, dict) else None
    )
    return {
        "id": int(row["id"]),
        "student_id": int(row["student_id"]),
        "issued_at": row["issued_at"],
        "due_at": row["due_at"],
        "source": row["source"],
        "issued_by": row["issued_by"],
        "occurrence_id": row["occurrence_id"],
        "planned_volume": int(row["planned_volume"]),
        # tsk-867: во сколько минут работы оценён состав В МОМЕНТ ВЫДАЧИ.
        # None — выдача сделана до перехода на бюджет времени либо вес тогда
        # не был измерен; экран в этом случае обходится без оценки.
        "planned_minutes": planned_minutes,
        "volume_details": row["volume_details"],
        "note": row["note"],
        "items": items,
        "total": len(items),
        "done": done,
        "is_overdue": bool(row["due_at"] <= moment and done < len(items)),
    }


#: Свёртка «сколько задано / сколько сделано / просрочено» сразу на группу.
#: Отдельный запрос от `_ITEMS_SQL`: сводке преподавателя нужны числа по
#: каждому участнику, а не состав — тянуть состав на группу из 8 человек
#: значило бы вернуть сотни строк ради трёх чисел.
_SUMMARY_SQL = """
WITH current AS (
    SELECT DISTINCT ON (ha.student_id)
           ha.id, ha.student_id, ha.due_at, ha.planned_volume, ha.issued_at
      FROM homework_assignment ha
     WHERE ha.student_id = ANY(:student_ids)
       -- Состояние НА МОМЕНТ `as_of`, а не «сейчас» (tsk-741, дефект 02.09).
       -- Выдача существовала к этому моменту и не была к нему отменена.
       -- `cancelled_at IS NULL` в чистом виде здесь неверен: каждая новая
       -- выдача гасит предыдущую, поэтому у прошедшего занятия «неотменённой»
       -- оказывалась та, что выдали ПОСЛЕ него, — и преподаватель видел на
       -- прошедшем занятии домашнюю работу к следующему.
       AND ha.issued_at <= :as_of
       AND (ha.cancelled_at IS NULL OR ha.cancelled_at > :as_of)
     ORDER BY ha.student_id, ha.issued_at DESC, ha.id DESC
),
counted AS (
    SELECT c.student_id, c.id, c.due_at, c.issued_at,
           count(hi.id) AS total,
           count(*) FILTER (
               WHERE (hi.kind = 'task' AND EXISTS (
                        SELECT 1 FROM task_results tr
                          JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
                         WHERE tr.user_id = c.student_id AND tr.task_id = hi.task_id
                           AND tr.is_correct = true))
                  OR (hi.kind = 'material' AND EXISTS (
                        SELECT 1 FROM student_material_progress smp
                         WHERE smp.student_id = c.student_id
                           AND smp.material_id = hi.material_id
                           AND smp.status IN ('completed', 'skipped')))
           ) AS done
      FROM current c
      LEFT JOIN homework_item hi ON hi.homework_id = c.id
     GROUP BY c.student_id, c.id, c.due_at, c.issued_at
)
SELECT * FROM counted
"""


async def status_for_students(
    db: AsyncSession,
    *,
    student_ids: list[int],
    now: Optional[datetime] = None,
    as_of: Optional[datetime] = None,
) -> dict[int, dict[str, Any]]:
    """Состояние ДЗ каждого ученика группы НА МОМЕНТ `as_of`.

    `as_of` — «какую домашнюю работу человек должен был принести к этому
    моменту». Для сводки занятия это ВРЕМЯ ЗАНЯТИЯ, а не «сейчас»: иначе у
    прошедшего занятия показывается выдача, сделанная по его итогам, то есть
    домашняя работа к СЛЕДУЮЩЕМУ занятию (дефект, замеченный оператором
    02.09). По умолчанию — «сейчас», прежнее поведение.

    `now` отвечает на другой вопрос — просрочена ли выдача. Для прошедшего
    занятия это по-прежнему настоящее время: срок либо прошёл, либо нет,
    независимо от того, какое занятие мы разглядываем.

    Ученик без выдачи в ответе отсутствует — это не то же самое, что «выдача
    пустая»: первое значит «ещё не задавали», второе невозможно (пустую выдачу
    `issue` не создаёт).
    """
    if not student_ids:
        return {}
    moment = now or datetime.now(timezone.utc)
    rows = (
        await db.execute(
            text(_SUMMARY_SQL),
            {"student_ids": student_ids, "as_of": as_of or moment},
        )
    ).mappings().fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        total = int(row["total"] or 0)
        done = int(row["done"] or 0)
        result[int(row["student_id"])] = {
            "homework_id": int(row["id"]),
            "issued_at": row["issued_at"],
            "due_at": row["due_at"],
            "assigned_total": total,
            "assigned_done": done,
            "is_overdue": bool(row["due_at"] <= moment and done < total),
        }
    return result


#: Доля выполненного из выданного за период — на группу одним запросом.
#: Берутся выдачи, СОЗДАННЫЕ в периоде, включая отменённые: отменённая выдача
#: всё равно была работой, которую человеку давали, и вычёркивать её задним
#: числом значило бы менять прошлые показатели.
_RATIO_SQL = """
WITH scoped AS (
    SELECT ha.id, ha.student_id
      FROM homework_assignment ha
     WHERE ha.student_id = ANY(:student_ids)
       AND ha.issued_at >= :period_from AND ha.issued_at <= :period_to
)
SELECT s.student_id,
       count(hi.id) AS total,
       count(*) FILTER (
           WHERE (hi.kind = 'task' AND EXISTS (
                    SELECT 1 FROM task_results tr
                      JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
                     WHERE tr.user_id = s.student_id AND tr.task_id = hi.task_id
                       AND tr.is_correct = true))
              OR (hi.kind = 'material' AND EXISTS (
                    SELECT 1 FROM student_material_progress smp
                     WHERE smp.student_id = s.student_id
                       AND smp.material_id = hi.material_id
                       AND smp.status IN ('completed', 'skipped')))
       ) AS done
  FROM scoped s
  JOIN homework_item hi ON hi.homework_id = s.id
 GROUP BY s.student_id
"""


async def completion_ratio_for_students(
    db: AsyncSession,
    *,
    student_ids: list[int],
    period_from: datetime,
    period_to: datetime,
) -> dict[int, float]:
    """Доля выполненного из выданного за период, по каждому ученику (0..1).

    Ученик, которому за период ничего не выдавали, в ответе отсутствует — у
    него нет доли, и подставлять ему ноль нельзя: это превратило бы «ему не
    задавали» в «он не сделал» и утянуло бы его вниз в сравнении с группой.
    """
    if not student_ids:
        return {}
    rows = (
        await db.execute(
            text(_RATIO_SQL),
            {
                "student_ids": student_ids,
                "period_from": period_from,
                "period_to": period_to,
            },
        )
    ).mappings().fetchall()
    result: dict[int, float] = {}
    for row in rows:
        total = int(row["total"] or 0)
        if total == 0:
            continue
        result[int(row["student_id"])] = int(row["done"] or 0) / total
    return result


async def cancel(
    db: AsyncSession, *, homework_id: int, now: Optional[datetime] = None
) -> bool:
    """Отменить выдачу. Идемпотентно: уже отменённая остаётся как была.

    Returns:
        True — выдача была действующей и стала отменённой.
    """
    moment = now or datetime.now(timezone.utc)
    result = await db.execute(
        text(
            "UPDATE homework_assignment SET cancelled_at = :now "
            " WHERE id = :hid AND cancelled_at IS NULL"
        ),
        {"hid": homework_id, "now": moment},
    )
    return bool(result.rowcount)
