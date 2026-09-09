"""Сеансы работы ученика над элементом программы (tsk-868).

Зачем модуль. Время прохождения материала и задания в системе не сохранялось
нигде: `student_presence` — снимок на ученика (история намеренно не велась,
tsk-591), `product_event` заведена в апреле и пуста до сих пор. Из-за этого вес
теории в `task_effort_service` пришлось брать прокси, сложность заданий
калибруется мнением методиста, а не замером, и нет способа увидеть задание, на
котором ученики сидят втрое дольше похожих.

**Хранятся сеансы, а не пульсы** (решение оператора 09.09). Пульс приходит раз
в две минуты; писать каждый — это десятки тысяч строк в месяц ради данных, от
которых в tsk-591 отказались осознанно. Сеанс — одна строка на непрерывную
работу над элементом: сколько заняло, когда, сколько было заходов.

**Как сшивается.** Пульс продлевает последний сеанс ученика, если это тот же
элемент и разрыв не больше `SESSION_GAP_SECONDS`; иначе открывается новый.
Разрыв взят с запасом в два пульса: вкладку могли свернуть на минуту, сеть
могла моргнуть, и дробить из-за этого работу над одним материалом на три
сеанса — значит потерять сам смысл записи.

**Чего в сеансе нет.** Хвоста после последнего пульса: человек мог читать ещё
полторы минуты, но подтверждения этому нет, и додумывать его нельзя — иначе
любое закрытие вкладки добавляло бы к времени выдуманные две минуты. Поэтому
время сеанса это `ended_at - started_at`, и оно СИСТЕМАТИЧЕСКИ занижено на
величину до одного интервала пульса. Читателю про это надо помнить: сеанс из
одного пульса длится ноль секунд, и это честнее выдуманных двух минут.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: Разрыв, после которого работа считается НОВЫМ сеансом. Пульс приходит раз в
#: две минуты (`use-presence-heartbeat`), поэтому пять — это пропуск двух
#: подряд: свернул вкладку, отвлёкся, моргнула сеть. Больше — уже другой заход.
SESSION_GAP_SECONDS = 300

#: Сколько максимум засчитывается за один пульс. Страховка от «разбудили
#: ноутбук»: если между пульсами прошло больше, значит человека не было, и
#: время сеанса растянулось бы на весь перерыв.
MAX_BEAT_SECONDS = SESSION_GAP_SECONDS

SOURCE_PRESENCE = "presence"
SOURCE_PLAYER = "player"

#: Типы элементов. Словарь общий с `student_presence.context` — пульс и сеанс
#: говорят об одном и том же, и расходиться им нельзя (CHECK в БД тот же).
ITEM_TYPES = frozenset({"task", "material", "video", "course", "other"})

# Продление считается ОДНИМ запросом: найти последний подходящий сеанс и
# обновить его. Разделять на SELECT + UPDATE нельзя — пульсы одного ученика
# приходят из нескольких вкладок, и между чтением и записью успел бы вклиниться
# соседний, создав второй сеанс на то же время (тот же класс, что дедуп в
# `learning_events_service`).
_EXTEND_SQL = """
WITH candidate AS (
    SELECT id, ended_at
    FROM learning_time_session
    WHERE student_id = :student_id
      AND source = :source
      AND item_type = :item_type
      AND task_id IS NOT DISTINCT FROM :task_id
      AND material_id IS NOT DISTINCT FROM :material_id
      AND ended_at >= now() - make_interval(secs => :gap)
    ORDER BY ended_at DESC
    LIMIT 1
    FOR UPDATE
)
UPDATE learning_time_session s
SET ended_at = now(),
    -- Прибавляем ФАКТИЧЕСКИЙ промежуток с прошлого пульса, но не больше
    -- предела: иначе «закрыл ноутбук и вернулся через час» станет часом работы.
    seconds = s.seconds + LEAST(
        GREATEST(EXTRACT(EPOCH FROM (now() - c.ended_at))::int, 0), :max_beat
    ),
    beats = s.beats + 1,
    interactions = s.interactions + CASE WHEN :interacted THEN 1 ELSE 0 END,
    updated_at = now()
FROM candidate c
WHERE s.id = c.id
RETURNING s.id
"""

_OPEN_SQL = """
INSERT INTO learning_time_session (
    student_id, item_type, task_id, material_id, course_id,
    started_at, ended_at, seconds, beats, interactions, source, payload
)
VALUES (
    :student_id, :item_type, :task_id, :material_id, :course_id,
    now(), now(), 0, 1, CASE WHEN :interacted THEN 1 ELSE 0 END,
    :source, CAST(:payload AS jsonb)
)
RETURNING id
"""


async def record_beat(
    db: AsyncSession,
    *,
    student_id: int,
    item_type: str,
    course_id: Optional[int] = None,
    task_id: Optional[int] = None,
    material_id: Optional[int] = None,
    interacted: bool = False,
    source: str = SOURCE_PRESENCE,
    payload: Optional[dict[str, Any]] = None,
) -> int:
    """Учесть пульс: продлить открытый сеанс или начать новый. Вернуть его id.

    Транзакцию не закрывает — коммитит вызывающий эндпоинт, ровно как у
    `student_presence_service.record`: пульс и сеанс пишутся одной транзакцией,
    иначе снимок и история разъедутся между собой.

    Контекст `course` и `other` сеансом НЕ становится: «открыт кабинет» — это
    не работа над элементом, и складывать его со временем на задании значило бы
    выдать блуждание по оглавлению за учёбу.
    """
    if item_type not in ITEM_TYPES:
        item_type = "other"
    if item_type in ("course", "other"):
        return 0

    params = {
        "student_id": student_id,
        "item_type": item_type,
        "task_id": task_id,
        "material_id": material_id,
        "course_id": course_id,
        "interacted": interacted,
        "source": source,
        "gap": SESSION_GAP_SECONDS,
        "max_beat": MAX_BEAT_SECONDS,
    }
    extended = (await db.execute(text(_EXTEND_SQL), params)).scalar()
    if extended is not None:
        return int(extended)

    opened = (await db.execute(text(_OPEN_SQL), {
        **params, "payload": None if payload is None else json.dumps(payload),
    })).scalar_one()
    return int(opened)


_PLAYER_SQL = """
WITH candidate AS (
    SELECT id
    FROM learning_time_session
    WHERE student_id = :student_id
      AND source = 'player'
      AND item_type = 'video'
      AND material_id IS NOT DISTINCT FROM :material_id
      AND payload->>'video_id' IS NOT DISTINCT FROM :video_id
      AND ended_at >= now() - make_interval(secs => :gap)
    ORDER BY ended_at DESC
    LIMIT 1
    FOR UPDATE
)
UPDATE learning_time_session s
SET ended_at = now(),
    -- Проигранные секунды приходят от плеера и НЕ равны промежутку по часам:
    -- пауза, перемотка назад, повтор куска. Берём наибольшее из накопленного и
    -- присланного — плеер шлёт суммарно с начала просмотра.
    seconds = GREATEST(s.seconds, :watched_seconds),
    beats = s.beats + 1,
    payload = COALESCE(s.payload, '{}'::jsonb) || CAST(:payload AS jsonb),
    updated_at = now()
FROM candidate c
WHERE s.id = c.id
RETURNING s.id
"""


async def record_video_progress(
    db: AsyncSession,
    *,
    student_id: int,
    video_id: str,
    watched_seconds: int,
    duration_seconds: Optional[int] = None,
    completed: bool = False,
    material_id: Optional[int] = None,
    course_id: Optional[int] = None,
) -> int:
    """Учесть отчёт видеоплеера о просмотре.

    Плеер шлёт накопленный итог, а не приращение: связь с iframe может
    оборваться на любом сообщении, и «прибавь мне десять секунд» после потери
    пары отчётов дало бы недосчёт, который уже ничем не восстановить.

    `completed` ставится клиентом по событию конца, а не вычисляется здесь из
    доли: у длинного разбора «досмотрел» и «доиграл до конца, уйдя с вкладки» —
    разные вещи, и решать это должен тот, кто видит события плеера.
    """
    payload: dict[str, Any] = {"video_id": video_id, "completed": completed}
    if duration_seconds is not None:
        payload["duration_seconds"] = int(duration_seconds)
    payload["watched_seconds"] = int(watched_seconds)

    params = {
        "student_id": student_id,
        "material_id": material_id,
        "video_id": video_id,
        "watched_seconds": int(watched_seconds),
        "gap": SESSION_GAP_SECONDS,
        "payload": json.dumps(payload),
    }
    updated = (await db.execute(text(_PLAYER_SQL), params)).scalar()
    if updated is not None:
        return int(updated)

    opened = (await db.execute(text("""
        INSERT INTO learning_time_session (
            student_id, item_type, task_id, material_id, course_id,
            started_at, ended_at, seconds, beats, interactions, source, payload
        )
        VALUES (
            :student_id, 'video', NULL, :material_id, :course_id,
            now(), now(), :watched_seconds, 1, 0, 'player', CAST(:payload AS jsonb)
        )
        RETURNING id
    """), {**params, "course_id": course_id})).scalar_one()
    return int(opened)


__all__ = [
    "ITEM_TYPES",
    "MAX_BEAT_SECONDS",
    "SESSION_GAP_SECONDS",
    "SOURCE_PLAYER",
    "SOURCE_PRESENCE",
    "record_beat",
    "record_video_progress",
]
