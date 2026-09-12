"""tsk-894: выпускник (alumni) не заводится в новое ПОСЛЕ перевода на тариф.

`graduation_service` (tsk-673) отрабатывает перевод на «Выпускник» одним
событием: снимает с расписания то, что было НА МОМЕНТ перехода. Он не
вызывается снова и ничего не проверяет на будущих операциях записи — значит
человека можно было завести обратно в любое место для действующих учеников
уже ПОСЛЕ перехода: добавить в слот расписания, посадить на отработку,
зачислить на курс.

Правило, по которому проведена граница (то же, что у `course_activity_service`,
tsk-886, — не изобретать заново):

* **Запрещается создание НОВОЙ связи** ученик↔занятие и ученик↔курс, если у
  ученика ДЕЙСТВУЮЩИЙ тариф — «Выпускник». Решение оператора (tsk-894):
  жёсткий запрет 409 везде, без исключений ни для самозаписи, ни для
  сотрудника — легитимная разовая причина закрывается переводом тарифа
  обратно, а не обходом гейта.
* **Чтение и уже начатое не трогаем.** Идемпотентный повтор (ученик уже
  активный участник слота/occurrence, курс уже назначен) отказа не получает —
  повторный вызов ничего не создаёт, и отказывать там не за что.

Код тарифа берётся из `graduation_service.ALUMNI_PLAN_CODE` — единственный
источник этой константы в кодовой базе, вторая копия строки `"alumni"`
разъехалась бы с ним при первом же переименовании.

Работу УЖЕ идущего курса (начать попытку, отправить ответ) закрывает другой
гейт — `graduation_service.assert_course_work_allowed` (tsk-673), он стоит на
своих трёх адресах и здесь не дублируется: там речь о продолжении обучения на
уже назначенном курсе, здесь — о появлении НОВОЙ связи.
"""
from __future__ import annotations

import logging
from typing import Iterable

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.graduation_service import ALUMNI_PLAN_CODE

logger = logging.getLogger(__name__)

#: Текст отказа. Один на все пути — методист/преподаватель/ученик получают
#: одинаковое объяснение, откуда бы отказ ни пришёл.
ALUMNI_DETAIL = (
    "Ученик переведён на тариф «Выпускник» и больше не принимает новых "
    "записей на занятия и курсы. Верните действующий тариф, если он "
    "продолжает учиться."
)


async def is_alumni(db: AsyncSession, student_id: int) -> bool:
    """Действующий тариф ученика — «Выпускник»?

    Тариф не назначен вовсе (строки без `ends_on IS NULL` нет) — не считается
    выпускником: отсутствие подписки значит «ещё не размечен», а не «выпуск»,
    и блокировать по этому признаку значило бы закрыть запись всем, кому
    тариф просто не успели поставить (то же рассуждение, что у
    `graduation_service.assert_course_work_allowed`).
    """
    row = (
        await db.execute(
            text(
                "SELECT p.code "
                "  FROM student_subscription s "
                "  JOIN subscription_plan p ON p.id = s.plan_id "
                " WHERE s.student_id = :sid AND s.ends_on IS NULL"
            ),
            {"sid": student_id},
        )
    ).first()
    return row is not None and row.code == ALUMNI_PLAN_CODE


async def load_alumni_ids(db: AsyncSession, student_ids: Iterable[int]) -> set[int]:
    """Кто из перечисленных учеников сейчас — действующий «Выпускник».

    Батч-версия `is_alumni` для списков (ростер преподавателя и т.п., tsk-917
    п.5) — один запрос вместо N, тот же приём, что и
    `course_activity_service.load_inactive_course_ids` для is_active курсов.
    """
    ids = list(student_ids)
    if not ids:
        return set()
    rows = (
        await db.execute(
            text(
                "SELECT s.student_id "
                "  FROM student_subscription s "
                "  JOIN subscription_plan p ON p.id = s.plan_id "
                " WHERE s.student_id = ANY(:ids) AND s.ends_on IS NULL AND p.code = :code"
            ),
            {"ids": ids, "code": ALUMNI_PLAN_CODE},
        )
    ).scalars().all()
    return {int(r) for r in rows}


async def assert_not_alumni(db: AsyncSession, student_id: int, *, action: str) -> None:
    """Отказать, если ученик — действующий выпускник.

    Зовётся ПЕРЕД созданием новой связи (слот, occurrence, курс). Уже
    существующую связь проверять не нужно: вызывающий сам решает это ДО
    вызова (идемпотентный путь ничего не создаёт).

    :param db: async-сессия.
    :param student_id: кого проверяем.
    :param action: что делали — только для журнала.
    :raises HTTPException: 409, если ученик — выпускник.
    """
    if not await is_alumni(db, student_id):
        return
    logger.info("tsk-894: отказ «%s» — ученик %s выпускник", action, student_id)
    raise HTTPException(status.HTTP_409_CONFLICT, ALUMNI_DETAIL)
