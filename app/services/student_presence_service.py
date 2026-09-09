"""tsk-591: пульс присутствия ученика в кабинете.

Кабинет (SPW) шлёт сюда короткий сигнал раз в две минуты, пока вкладка открыта
и видима. На этом сигнале держится различение, которое просил оператор:
«ученика нет в системе» против «открыл задание и молчит».

**Почему одна строка на ученика, а не событие в журнал.** Пульс частый по
своей природе: 30 учеников за полуторачасовое занятие дали бы ~1400 записей,
из которых нужна ровно последняя. Поэтому UPSERT в ``student_presence`` —
каждый ученик пишет свою строку, писатели не пересекаются, блокировка живёт
доли миллисекунды. Урок tsk-621 (запись на каждый запрос в общую строку —
мина) учтён именно так: общей строки здесь нет ни одной.

**Снимок — не единственное, что делает пульс (tsk-868).** Тем же сигналом
продлевается сеанс работы над элементом в ``learning_time_session``: снимок
отвечает «что открыто сейчас», сеанс — «сколько человек на этом провёл». От
истории пульсов при этом по-прежнему отказываемся: сеанс это одна строка на
непрерывную работу, а не запись каждые две минуты.

**Что такое ``interacted``.** Клиент ставит его, если за прошедший интервал
человек что-то делал руками: печатал, касался экрана, листал страницу. Без
этого признака ученик, вдумчиво читающий длинный материал, ничем не отличался
бы от ученика, который отошёл и оставил вкладку открытой, — и тревога
преподавателю приходила бы на каждый длинный текст.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import learning_time_service

logger = logging.getLogger(__name__)

#: Что открыто у ученика в момент пульса. Совпадает с CHECK-ограничением
#: таблицы: незнакомое значение упало бы ошибкой на записи, поэтому лишнее
#: сводим к ``other`` ещё на входе.
#:
#: tsk-835: список нужно править ВМЕСТЕ со схемой `PresenceContext`, моделью и
#: CHECK в БД — иначе новое значение проходит проверку тела запроса, а сюда
#: доезжает как `other` и молча теряется. Ровно так и вышло с `video` при
#: первом прогоне: эндпоинт отвечал 200, в таблице лежало `other`.
ALLOWED_CONTEXTS = frozenset({"task", "material", "course", "video", "other"})


def normalize_context(context: Optional[str]) -> str:
    """Привести контекст к допустимому значению (незнакомое → ``other``)."""
    if context is None:
        return "other"
    value = context.strip().lower()
    return value if value in ALLOWED_CONTEXTS else "other"


async def touch(
    db: AsyncSession,
    student_id: int,
    *,
    interacted: bool,
    context: Optional[str] = None,
    course_id: Optional[int] = None,
    task_id: Optional[int] = None,
    material_id: Optional[int] = None,
) -> None:
    """Записать пульс ученика. Один UPSERT, без чтения перед записью.

    ``last_interaction_at`` двигается только при ``interacted=True``: пульс без
    взаимодействия говорит «вкладка открыта», но не «человек за экраном».

    Транзакцию не закрывает — коммитит вызывающий эндпоинт.
    """
    await db.execute(
        text(
            """
            INSERT INTO student_presence (
                student_id, last_seen_at, last_interaction_at,
                context, course_id, task_id, material_id, updated_at
            )
            VALUES (
                :student_id, now(),
                CASE WHEN :interacted THEN now() ELSE NULL END,
                :context, :course_id, :task_id, :material_id, now()
            )
            ON CONFLICT (student_id) DO UPDATE SET
                last_seen_at = now(),
                -- Прежняя отметка о взаимодействии не стирается пульсом без
                -- взаимодействия: иначе «читал, потом просто смотрит» мгновенно
                -- превращалось бы в «никогда ничего не делал».
                last_interaction_at = CASE
                    WHEN :interacted THEN now()
                    ELSE student_presence.last_interaction_at
                END,
                context = :context,
                course_id = :course_id,
                task_id = :task_id,
                material_id = :material_id,
                updated_at = now()
            """
        ),
        {
            "student_id": student_id,
            "interacted": bool(interacted),
            "context": normalize_context(context),
            "course_id": course_id,
            "task_id": task_id,
            "material_id": material_id,
        },
    )

    # tsk-868: тем же пульсом продлевается СЕАНС работы над элементом. Снимок
    # выше отвечает на вопрос «что у ученика открыто сейчас», сеанс — «сколько
    # он на этом провёл»; это разные вопросы, и держать их в одной строке
    # нельзя. Одна транзакция на оба: разъехавшиеся снимок и история хуже, чем
    # отсутствие истории.
    #
    # Запись идёт во ВЛОЖЕННОЙ транзакции и её отказ гасится. Причина конкретная:
    # пульс держит сигнал преподавателю о простое ученика (tsk-591), и уронить
    # его из-за побочной записи нельзя. Первый заход это уже показал — внешние
    # ключи в новой таблице свалили пульс на тестах, потому что кабинет шлёт
    # `task_id` как есть, никем не проверенный. Ключи убраны, но страховка
    # остаётся: у побочной записи нет права ломать основную.
    try:
        async with db.begin_nested():
            await learning_time_service.record_beat(
                db,
                student_id=student_id,
                item_type=normalize_context(context),
                course_id=course_id,
                task_id=task_id,
                material_id=material_id,
                interacted=bool(interacted),
            )
    except Exception:
        logger.warning(
            "не удалось записать сеанс работы ученика %s — пульс сохранён",
            student_id, exc_info=True,
        )
