"""Срок ответа на заявку помощи (tsk-995, П6).

Сестра `review_sla` (срок проверки РАБОТ): одно место, где из момента создания
заявки выводятся срок и просрочка. Срок — `created_at + HELP_SLA_HOURS`
(решение оператора Р1: 24 часа).

В отличие от срока проверки работ срок заявки ХРАНИТСЯ в `help_requests.due_at`:
колонка была с этапа 3.9, по ней сортируют очередь и `claim-next`. Поэтому
смена настройки меняет срок только у новых заявок, а не у уже открытых.

Модуль без зависимостей от других сервисов: его читают и `help_requests_service`,
и `teacher_queue_service`, а первый импортирует второй — общий код в любом из
них дал бы круговой импорт.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.config import Settings

logger = logging.getLogger(__name__)

#: SQL-выражение срока для INSERT/UPDATE: `now()` — время транзакции, то же,
#: что пишется в `created_at` этим же оператором, поэтому срок ровно
#: `created_at + HELP_SLA_HOURS`. Параметр `:help_sla_hours` — из `help_sla_hours()`.
HELP_DUE_AT_SQL = "now() + make_interval(hours => :help_sla_hours)"


def help_sla_hours() -> int:
    """Срок ответа на заявку в часах из настроек."""
    return int(Settings().help_sla_hours)


def help_due_at(created_at: Optional[datetime]) -> Optional[datetime]:
    """Срок ответа: момент создания + `HELP_SLA_HOURS`.

    Боевой код пишет срок SQL-выражением `HELP_DUE_AT_SQL` (время транзакции);
    функция — то же правило для Python: эталон в тестах и для будущих чтений.

    `None` и не-datetime → `None`; naive datetime трактуется как UTC с WARNING
    (тот же приём, что `review_sla._aware`).
    """
    if created_at is None:
        return None
    if not isinstance(created_at, datetime):
        logger.warning("help_sla: created_at не datetime (%r) — срок не считаем", type(created_at))
        return None
    if created_at.tzinfo is None:
        logger.warning("help_sla: naive created_at=%s — трактуем как UTC", created_at)
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at + timedelta(hours=help_sla_hours())


def awaiting_teacher_sql(alias: str = "hr") -> str:
    """SQL-условие «заявка ждёт ответа УЧИТЕЛЯ», без привязки к преподавателю.

    tsk-742: правило вынесено из счётчика `get_help_requests_pending_count`,
    потому что читать его стало нужно и другому экрану (доска куратора).
    Скопированный вариант неизбежно выродился бы в `status = 'open'` — а это
    СОВСЕМ другое множество: заявка остаётся открытой, пока её не закроет
    ученик, в том числе после ответа учителя. Тогда у куратора на экране вечно
    висели бы «просроченные» заявки, на которые он уже ответил, — верный способ
    отучить смотреть на экран.

    tsk-995: переехало сюда из `help_requests_service` (там осталось тем же
    именем), чтобы его мог взять и счётчик просрочки в `teacher_queue_service`.

    :param alias: алиас `help_requests` в вызывающем запросе. Только литералы
        из закрытого набора call-sites, user-input сюда не попадает.
    """
    return f"""
        {alias}.status = 'open'
        AND (
            {alias}.thread_id IS NULL
            OR (
                SELECT m.sender_id
                FROM messages m
                WHERE m.thread_id = {alias}.thread_id
                ORDER BY m.sent_at DESC, m.id DESC
                LIMIT 1
            ) = {alias}.student_id
        )
    """  # nosec B608 — alias из закрытого набора литералов


def help_overdue_sql(alias: str = "hr") -> str:
    """SQL-условие «заявка просрочена» (tsk-995). Требует bind `:now_ts`.

    Просрочена открытая заявка, срок которой прошёл, а ход — за преподавателем:
    `awaiting_teacher_sql` (последнее слово за учеником или ответа ещё не было).
    Без этой части в «Просрочено» попадали бы заявки, на которые преподаватель
    ответил вовремя, а ученик просто не закрыл их — то же множество, от
    которого tsk-415/tsk-742 уже уводили счётчик «ждут ответа».

    Ход за преподавателем и тогда, когда последний в переписке он, но ученик
    после этого вернул заявку («не помогло», `help_request_reopens`): возврат
    сообщения в тред не пишет, и без этой ветки возвращённая заявка не
    просрочивалась бы никогда.

    Исключение — индивидуальный разбор без ссылки: ученик ждёт ссылку на
    встречу, хотя последним в переписке мог писать преподаватель.

    ЕДИНСТВЕННОЕ определение: им считают и ячейку «Просрочено» в
    `get_teacher_workload`, и фильтр `overdue=1` списка заявок, и признак
    `is_overdue` в списке и карточке — число и список обязаны совпадать.

    :param alias: алиас `help_requests`; только литералы из закрытого набора.
    """
    return f"""(
        {alias}.status = 'open'
        AND {alias}.due_at IS NOT NULL
        AND {alias}.due_at < :now_ts
        AND (
            ({awaiting_teacher_sql(alias)})
            OR EXISTS (
                SELECT 1 FROM help_request_reopens rr
                WHERE rr.request_id = {alias}.id
                  AND rr.reopened_at > COALESCE(
                      (SELECT MAX(m.sent_at) FROM messages m
                        WHERE m.thread_id = {alias}.thread_id),
                      '-infinity'::timestamptz
                  )
            )
            OR ({alias}.request_type = 'individual_review' AND {alias}.webinar_link IS NULL)
        )
    )"""  # nosec B608 — alias из закрытого набора литералов
