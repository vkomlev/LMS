"""
Сервис Календаря LMS (tsk-430/435): панель преподавателя, ручное добавление
участника, перенос и отработка вне расписания — всё per-участнику (групповое
occurrence, tsk-435).

Модель и границы — docs/specs/2026-07-26-plan-kalendar-lms.md § «Фаза 3» +
tsk-435 (rework на группы). Переиспользует `ensure_user_has_role` и
`is_within_operating_hours` из `lesson_calendar_service`.

tsk-587: время для переноса и записи берётся из активных слотов расписания
(`lesson_slot`), а не из часов работы школы, нарезанных по полчаса; выдача
вариантов и приём проверяют одно и то же.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings_store
from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.lesson_slot import LessonSlot
from app.repos.lesson_calendar_repository import (
    LessonOccurrenceParticipantRepository,
    LessonOccurrenceRepository,
    LessonOccurrenceTeacherRepository,
    LessonSlotRepository,
    LessonSlotTeacherRepository,
)
from app.services import audit_service, homework_service, lesson_calendar_service
from app.services.lesson_occurrence_generator_service import iter_occurrence_datetimes
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

_occurrence_repo = LessonOccurrenceRepository()
_participant_repo = LessonOccurrenceParticipantRepository()
_occurrence_teacher_repo = LessonOccurrenceTeacherRepository()
_lesson_slot_repo = LessonSlotRepository()
_slot_teacher_repo = LessonSlotTeacherRepository()

# Статусы, при которых участие уже структурно закрыто для reschedule/ownership-операций.
_LOCKED_STATUSES = frozenset({"no_show", "completed", "rescheduled"})

# На сколько дней вперёд подбираются варианты переноса. Совпадает с дефолтом
# горизонта генератора занятий (LESSON_OCCURRENCE_HORIZON_DAYS): дальше него
# занятий ещё нет, и присоединяться было бы не к чему.
#
# tsk-721: значение переехало в настройки школы, здесь остался запасной
# вариант на случай, если настройки не прочитались. Читается функцией ниже, а
# не подставляется умолчанием параметра: умолчание вычисляется при импорте
# модуля, то есть правка в кабинете ждала бы перезапуска.
_RESCHEDULE_HORIZON_DAYS = 14


def _reschedule_horizon_days() -> int:
    """Горизонт переноса из настроек школы, с запасным значением."""
    try:
        return settings_store.get_int("lesson_reschedule_horizon_days")
    except Exception:
        logger.warning(
            "tsk-721: горизонт переноса не прочитался, беру %s дн.",
            _RESCHEDULE_HORIZON_DAYS,
        )
        return _RESCHEDULE_HORIZON_DAYS


# ─── Teacher panel ──────────────────────────────────────────────────────────


async def list_for_teacher(
    db: AsyncSession,
    *,
    teacher_id: int,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    limit: int = 100,
    no_show_threshold_minutes: int = 10,
) -> list[
    tuple[LessonOccurrence, list[tuple[LessonOccurrenceParticipant, bool, Optional[str]]]]
]:
    """Занятия преподавателя, каждое — с полным списком участников; на каждого
    участника: флаг `is_overdue` (живой расчёт, не ждёт cron-тик — участник в
    `status='scheduled'` и порог опоздания уже истёк) и имя.

    tsk-757: имя приходит вместе с занятием. Панель преподавателя подставляла
    его из ростера (ученики по курсам преподавателя), и участник его же
    занятия, в ростер не попавший, показывался как «Ученик #id». Запрос имён —
    тот же, что в сводке занятия (`teacher_lesson_summary_service`), второго
    источника не появляется. Видимость не расширяется: список участников и
    раньше отдавался только владельцу занятия, методисту и админу."""
    occurrences = await _occurrence_repo.list_for_teacher(
        db, teacher_id=teacher_id, from_dt=from_dt, to_dt=to_dt, limit=limit
    )
    if not occurrences:
        return []

    occurrence_ids = [o.id for o in occurrences]
    all_participants = await _participant_repo.list_for_occurrences(db, occurrence_ids)
    participants_by_occurrence: dict[int, list[LessonOccurrenceParticipant]] = {}
    for p in all_participants:
        participants_by_occurrence.setdefault(p.occurrence_id, []).append(p)

    name_by_student_id = await _load_student_names(
        db, {p.student_id for p in all_participants}
    )

    now_utc = datetime.now(timezone.utc)
    threshold = timedelta(minutes=no_show_threshold_minutes)

    result: list[
        tuple[LessonOccurrence, list[tuple[LessonOccurrenceParticipant, bool, Optional[str]]]]
    ] = []
    for occurrence in occurrences:
        participants = participants_by_occurrence.get(occurrence.id, [])
        rows = [
            (
                p,
                p.status == "scheduled" and (occurrence.scheduled_at + threshold) < now_utc,
                name_by_student_id.get(p.student_id),
            )
            for p in participants
        ]
        result.append((occurrence, rows))
    return result


async def _load_student_names(
    db: AsyncSession, student_ids: set[int]
) -> dict[int, Optional[str]]:
    """Имена участников одним запросом (tsk-757). Пустое множество —
    в БД не ходим."""
    if not student_ids:
        return {}
    rows = (
        await db.execute(
            text("SELECT id, full_name FROM users WHERE id = ANY(:ids)"),
            {"ids": list(student_ids)},
        )
    ).mappings().fetchall()
    return {int(r["id"]): r["full_name"] for r in rows}


async def get_occurrence_for_teacher(
    db: AsyncSession, *, occurrence_id: int, teacher_id: int
) -> LessonOccurrence:
    """Ownership-гейт занятия: основной `occurrence.teacher_id` ИЛИ
    со-преподаватель через `lesson_occurrence_teacher` (tsk-443: совместное
    ведение). Проверка по колонке остаётся (не эксклюзивно M2M) — occurrence
    может быть создан в обход строки M2M (напр. прямой ORM в старых тестах)."""
    occurrence = await _occurrence_repo.get_by_id(db, occurrence_id)
    if occurrence is None:
        raise DomainError(f"Занятие id={occurrence_id} не найдено", status_code=404)
    link = await _occurrence_teacher_repo.get(
        db, occurrence_id=occurrence_id, teacher_id=teacher_id
    )
    if link is not None:
        # tsk-492: погашенная строка — разовая подмена «на этом занятии не
        # ведёт». Она сильнее колонки `teacher_id`: иначе снятие с одного
        # занятия не действовало бы на основного, а слоты школы заведены
        # именно на него.
        is_owner = link.is_active
    else:
        is_owner = occurrence.teacher_id == teacher_id
    if not is_owner:
        raise DomainError("Занятие принадлежит другому преподавателю", status_code=403)
    return occurrence


_TEACHER_ACTION_TO_STATUS = {
    "manual_present": "confirmed",
    "manual_absent": "no_show",
}


async def record_teacher_attendance(
    db: AsyncSession,
    *,
    occurrence_id: int,
    teacher_id: int,
    student_id: int,
    action: str,
    ip: Optional[str] = None,
) -> LessonOccurrenceParticipant:
    """Ручная отметка преподавателем ОДНОГО участника occurrence. В отличие
    от студенческого `lesson_attendance_service.record_attendance`, здесь
    заблокирован только `rescheduled` (участие уже заменено другим) —
    `no_show`/`completed` преподаватель обязан уметь исправить вручную."""
    occurrence = await get_occurrence_for_teacher(
        db, occurrence_id=occurrence_id, teacher_id=teacher_id
    )
    participant = await _participant_repo.get(
        db, occurrence_id=occurrence_id, student_id=student_id
    )
    if participant is None:
        raise DomainError(
            f"Ученик id={student_id} не входит в число участников этого занятия",
            status_code=404,
        )
    if participant.status == "rescheduled":
        raise DomainError(
            "Участие перенесено на другое занятие — правьте актуальный occurrence "
            f"(rescheduled_to_occurrence_id={participant.rescheduled_to_occurrence_id})",
            status_code=409,
        )

    new_status = _TEACHER_ACTION_TO_STATUS[action]

    await db.execute(
        text(
            "INSERT INTO attendance_event (occurrence_id, actor_user_id, action) "
            "VALUES (:oid, :uid, :action)"
        ),
        {"oid": occurrence.id, "uid": teacher_id, "action": action},
    )
    participant.status = new_status
    participant.updated_at = datetime.now(timezone.utc)

    await audit_service.log_event(
        db,
        audit_service.STUDENT_LESSON_ATTENDANCE_RECORDED,
        user_id=teacher_id,
        ip=ip,
        details={
            "occurrence_id": occurrence.id,
            "student_id": student_id,
            "action": action,
            "new_status": new_status,
            "actor_role": "teacher",
        },
    )

    # tsk-741: ученик был на занятии — значит пора задать домашнюю работу до
    # следующего. Объём считает формула по темпу и классу. Выдача НЕ ломает
    # отметку явки: она за своим переключателем (по умолчанию выключена),
    # молчит на повторной отметке того же занятия и глушит свои ошибки — иначе
    # преподаватель не смог бы отметить явку из-за домашней работы.
    if new_status == "confirmed":
        try:
            await homework_service.auto_issue_after_lesson(
                db,
                student_id=student_id,
                occurrence_id=occurrence.id,
                occurrence_at=occurrence.scheduled_at,
            )
        except Exception:
            logger.exception(
                "tsk-741: автовыдача ДЗ после занятия %s ученику %s не удалась",
                occurrence.id, student_id,
            )

    await db.commit()
    await db.refresh(participant)
    return participant


# ─── Расписание как источник времён (tsk-587) ──────────────────────────────
#
# До tsk-587 и подбор времени, и приём переноса опирались только на часы работы
# школы (`operating_hours`), нарезанные шагом в полчаса. Часы работы — это когда
# школа В ПРИНЦИПЕ открыта, а не когда у преподавателя есть занятие: на проде
# среда открыта с 13:00 до 19:00, а слоты в ней — 10:00, 11:00, 12:00 и 18:00.
# Ученик выбирал 17:00, система соглашалась, и занятие вставало мимо расписания
# (занятия 4640 и 1422 на проде). Теперь времена берутся из активных слотов, а
# часы работы остаются внешней рамкой поверх них.


async def _leading_teacher_ids(db: AsyncSession, occurrence: LessonOccurrence) -> list[int]:
    """Кто ведёт это занятие: активные строки M2M, иначе основной по колонке."""
    links = await _occurrence_teacher_repo.list_for_occurrence(db, occurrence.id)
    return [link.teacher_id for link in links] or [occurrence.teacher_id]


async def _active_slots_of(
    db: AsyncSession, teacher_ids: list[int], *, duration_minutes: int
) -> list[LessonSlot]:
    """Активные слоты этих преподавателей ТОЙ ЖЕ длительности.

    Длительность сверяется точно, а не «слот вместит»: занятие, созданное на
    время слота, генератор потом подтянет к этому слоту по (slot_id,
    scheduled_at) и выровняет длительность по слоту — то есть 90-минутное
    занятие в часовом слоте всё равно стало бы часовым, только молча.
    """
    by_id: dict[int, LessonSlot] = {}
    for teacher_id in teacher_ids:
        for slot in await _lesson_slot_repo.list_active(db, teacher_id=teacher_id):
            if slot.duration_minutes == duration_minutes:
                by_id[slot.id] = slot
    return list(by_id.values())


def _slot_starts_at(slot: LessonSlot, scheduled_at: datetime) -> bool:
    """Слот начинается ровно в это время (день недели + время в зоне слота).

    tsk-679: слот с датой окончания в этот день может уже не действовать —
    тогда он не «начинается» вовсе. Проверка здесь, а не только в выдаче
    кандидатов: приём переноса обязан быть не мягче выдачи, иначе ученик
    перенесётся на сентябрьское время старого расписания в обход списка.
    """
    local = scheduled_at.astimezone(ZoneInfo(slot.timezone))
    if slot.active_until is not None and local.date() > slot.active_until:
        return False
    return local.weekday() == slot.weekday and local.time() == slot.start_time


async def _find_slot_at(
    db: AsyncSession, *, teacher_ids: list[int], scheduled_at: datetime, duration_minutes: int
) -> Optional[LessonSlot]:
    """Слот расписания, начинающийся ровно в это время, или ``None``."""
    for slot in await _active_slots_of(db, teacher_ids, duration_minutes=duration_minutes):
        if _slot_starts_at(slot, scheduled_at):
            return slot
    return None


async def _list_slot_candidates(
    db: AsyncSession,
    *,
    teacher_ids: list[int],
    duration_minutes: int,
    student_id: int,
    exclude_occurrence_id: Optional[int] = None,
    limit: int = 10,
    horizon_days: int | None = None,
) -> list[datetime]:
    """Ближайшие времена активных слотов этих преподавателей, свободные у
    ученика и попадающие в часы работы школы. Отсортированы по возрастанию.

    Времена считает `iter_occurrence_datetimes` — та же функция, что и у
    генератора занятий. Поэтому выбранный кандидат гарантированно совпадает
    с временем уже созданного занятия слота, и ученик попадает в него, а не
    в параллельное.
    """
    # tsk-721: не задали горизонт явно — берём его из настроек школы прямо
    # здесь, при подборе вариантов.
    horizon = _reschedule_horizon_days() if horizon_days is None else horizon_days
    now_utc = datetime.now(timezone.utc)
    moments: set[datetime] = set()
    for slot in await _active_slots_of(db, teacher_ids, duration_minutes=duration_minutes):
        moments.update(
            iter_occurrence_datetimes(slot, horizon_days=horizon, now_utc=now_utc)
        )

    candidates: list[datetime] = []
    for moment in sorted(moments):
        if moment <= now_utc:
            continue
        within_hours = await lesson_calendar_service.is_within_operating_hours(
            db, scheduled_at=moment, duration_minutes=duration_minutes,
        )
        if within_hours is False:
            continue
        overlap = await _participant_repo.has_student_overlap(
            db,
            student_id=student_id,
            scheduled_at=moment,
            duration_minutes=duration_minutes,
            exclude_occurrence_id=exclude_occurrence_id,
        )
        if overlap:
            continue
        candidates.append(moment)
        if len(candidates) >= limit:
            break
    return candidates


async def _find_occurrence_at(
    db: AsyncSession,
    *,
    slot: Optional[LessonSlot],
    teacher_ids: list[int],
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_occurrence_id: Optional[int] = None,
) -> Optional[LessonOccurrence]:
    """Уже существующее занятие на это время, к которому надо присоединять.

    Две дороги. Первая — занятие ЭТОГО слота (tsk-587): именно оно и есть
    «урок по расписанию». Вторая — занятие этих же преподавателей ровно на это
    время и той же длительности (tsk-464): слота у него может не быть, но
    второе занятие в тот же час всё равно не нужно.
    """
    if slot is not None:
        existing = await _occurrence_repo.get_by_slot_and_time(
            db, slot_id=slot.id, scheduled_at=scheduled_at,
        )
        if existing is not None and existing.id != exclude_occurrence_id:
            return existing

    for teacher_id in teacher_ids:
        same_time = await _occurrence_repo.list_for_teacher(
            db, teacher_id=teacher_id, from_dt=scheduled_at, to_dt=scheduled_at, limit=5,
        )
        match = next(
            (
                o for o in same_time
                if o.id != exclude_occurrence_id and o.duration_minutes == duration_minutes
            ),
            None,
        )
        if match is not None:
            return match
    return None


async def _seat_student_at(
    db: AsyncSession,
    *,
    student_id: int,
    scheduled_at: datetime,
    duration_minutes: int,
    slot: Optional[LessonSlot],
    teacher_ids: list[int],
    exclude_occurrence_id: Optional[int] = None,
) -> tuple[LessonOccurrence, LessonOccurrenceParticipant]:
    """Посадить ученика на занятие в это время: в уже существующее, если оно
    есть, иначе в новое. Без commit — границы транзакции задаёт вызывающий.

    Новое занятие при попадании в слот создаётся ПРИВЯЗАННЫМ к нему
    (`slot_id`), а не отдельным: тогда генератор подхватит его как занятие
    этого слота и досыпет остальных участников и преподавателей. Раньше
    занятие всегда рождалось с `slot_id=NULL` — и в кабинете преподавателя
    рядом с групповым уроком появлялся второй, на одного человека.
    """
    existing = await _find_occurrence_at(
        db,
        slot=slot,
        teacher_ids=teacher_ids,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        exclude_occurrence_id=exclude_occurrence_id,
    )
    if existing is not None:
        participant = await _participant_repo.get(
            db, occurrence_id=existing.id, student_id=student_id,
        )
        if participant is None:
            participant = await _participant_repo.create(
                db, occurrence_id=existing.id, student_id=student_id, status="scheduled",
            )
        await db.flush()
        return existing, participant

    if slot is not None:
        # Ровно то же, что сделал бы генератор занятий на своём тике.
        slot_teachers = await _slot_teacher_repo.list_for_slot(db, slot.id)
        new_teacher_ids = [t.teacher_id for t in slot_teachers] or [slot.teacher_id]
        occurrence = await _occurrence_repo.create(
            db,
            slot_id=slot.id,
            teacher_id=slot.teacher_id,
            scheduled_at=scheduled_at,
            duration_minutes=slot.duration_minutes,
        )
    else:
        new_teacher_ids = teacher_ids
        occurrence = await _occurrence_repo.create(
            db,
            slot_id=None,
            teacher_id=teacher_ids[0],
            scheduled_at=scheduled_at,
            duration_minutes=duration_minutes,
        )
    await db.flush()

    participant = await _participant_repo.create(
        db, occurrence_id=occurrence.id, student_id=student_id, status="scheduled",
    )
    # tsk-443: без строк M2M занятие невидимо в кабинете преподавателя.
    for teacher_id in dict.fromkeys(new_teacher_ids):
        await _occurrence_teacher_repo.create(
            db, occurrence_id=occurrence.id, teacher_id=teacher_id,
        )
    await db.flush()
    return occurrence, participant


# ─── Ad-hoc creation + add-participant (teacher/student) ───────────────────


async def create_ad_hoc_occurrence(
    db: AsyncSession,
    *,
    student_id: int,
    teacher_id: int,
    scheduled_at: datetime,
    duration_minutes: int,
    require_scheduled_slot: bool = True,
) -> tuple[LessonOccurrence, LessonOccurrenceParticipant]:
    """Записать ученика на занятие в указанное время. Используется двумя
    путями: ученик сам записывается на отработку
    (`POST /lesson-occurrences/ad-hoc`) и преподаватель добавляет ученика
    вручную (`POST /teacher/lesson-occurrences/add-student`).

    Если время попадает в слот расписания или в уже существующее занятие
    этого преподавателя — ученик садится в НЕГО. Новое занятие заводится
    только когда садиться не во что (tsk-587: раньше занятие заводилось
    всегда, и на проде рядом с групповым уроком висели три одиночных
    занятия-двойника — 917, 4207, 5674).

    `require_scheduled_slot` — требовать, чтобы время совпадало с началом
    активного слота. Ученику это обязательно: он выбирает из готового
    списка, и приём обязан проверять ровно то же, что показала выдача.
    Преподавателю (методисту) — нет: назначить отработку вне сетки
    расписания это его штатное право, и списка вариантов у него нет.

    Коллизия проверяется только по УЧЕНИКУ (не по преподавателю — групповое
    occurrence по design допускает несколько параллельных occurrence у одного
    преподавателя).

    :raises DomainError: 404/422 — участник не найден/без нужной роли;
        422 — вне часов работы школы (если `operating_hours` настроены) либо
        время не совпадает ни с одним слотом расписания;
        409 — пересечение по времени с другим активным участием ученика.
    """
    await lesson_calendar_service.ensure_user_has_role(db, student_id, "student")
    await lesson_calendar_service.ensure_user_has_role(db, teacher_id, "teacher")

    within_hours = await lesson_calendar_service.is_within_operating_hours(
        db, scheduled_at=scheduled_at, duration_minutes=duration_minutes
    )
    if within_hours is False:
        raise DomainError(
            "Время вне часов работы школы (operating_hours)", status_code=422
        )

    slot = await _find_slot_at(
        db,
        teacher_ids=[teacher_id],
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    if slot is None and require_scheduled_slot:
        raise DomainError(
            "В это время занятий по расписанию нет — выберите время из списка",
            status_code=422,
        )

    overlap = await _participant_repo.has_student_overlap(
        db,
        student_id=student_id,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    if overlap:
        raise DomainError(
            "Время пересекается с другим активным занятием этого ученика",
            status_code=409,
        )

    occurrence, participant = await _seat_student_at(
        db,
        student_id=student_id,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        slot=slot,
        teacher_ids=[teacher_id],
    )
    await db.commit()
    await db.refresh(occurrence)
    await db.refresh(participant)
    logger.info(
        "lesson_occurrence запись: id=%s slot=%s student=%s teacher=%s at=%s",
        occurrence.id, occurrence.slot_id, student_id, teacher_id, scheduled_at,
    )
    return occurrence, participant


async def add_participant_to_occurrence(
    db: AsyncSession,
    *,
    occurrence_id: int,
    student_id: int,
    teacher_id: int,
) -> LessonOccurrenceParticipant:
    """Добавить ученика к УЖЕ существующему occurrence (например, подключить
    опоздавшего/новенького к уже идущей группе). Идемпотентно: уже
    участвующий ученик возвращает текущую строку."""
    occurrence = await get_occurrence_for_teacher(
        db, occurrence_id=occurrence_id, teacher_id=teacher_id
    )
    await lesson_calendar_service.ensure_user_has_role(db, student_id, "student")

    existing = await _participant_repo.get(
        db, occurrence_id=occurrence_id, student_id=student_id
    )
    if existing is not None:
        return existing

    overlap = await _participant_repo.has_student_overlap(
        db,
        student_id=student_id,
        scheduled_at=occurrence.scheduled_at,
        duration_minutes=occurrence.duration_minutes,
        exclude_occurrence_id=occurrence.id,
    )
    if overlap:
        raise DomainError(
            "Время пересекается с другим активным занятием этого ученика",
            status_code=409,
        )

    participant = await _participant_repo.create(
        db, occurrence_id=occurrence.id, student_id=student_id, status="scheduled",
    )
    await db.commit()
    await db.refresh(participant)
    return participant


# ─── Bookable occurrences + join (студент сам, tsk-021/443) ────────────────


async def list_bookable_occurrences_for_student(
    db: AsyncSession, *, student_id: int, teacher_ids: list[int], limit: int = 10,
) -> list[tuple[LessonOccurrence, list[str]]]:
    """Ближайшие БУДУЩИЕ occurrence преподавателей ученика, где он ЕЩЁ НЕ
    участник — кандидаты для присоединения, а не свободный ввод даты
    (реальный инцидент: `POST /lesson-occurrences/ad-hoc` создавал ВТОРОЕ
    отдельное occurrence на то же время, что уже существующий слот —
    оператор: "под него не делается отдельный слот, он присоединяется к
    существующему"). Возвращает пары (occurrence, имена преподавателей).
    """
    if not teacher_ids:
        return []

    now_utc = datetime.now(timezone.utc)
    candidates = await _occurrence_repo.list_for_teachers(
        db, teacher_ids=teacher_ids, from_dt=now_utc, limit=limit * 3,
    )
    if not candidates:
        return []

    already_pairs = await _participant_repo.list_for_student(
        db, student_id=student_id, from_dt=now_utc, limit=500,
    )
    already_occurrence_ids = {o.id for _p, o in already_pairs}

    filtered = [o for o in candidates if o.id not in already_occurrence_ids][:limit]
    if not filtered:
        return []

    names_by_occurrence = await _occurrence_repo.list_teacher_names_for_occurrences(
        db, [o.id for o in filtered],
    )
    return [(o, names_by_occurrence.get(o.id, [])) for o in filtered]


async def join_occurrence_as_student(
    db: AsyncSession, *, occurrence_id: int, student_id: int,
) -> tuple[LessonOccurrence, LessonOccurrenceParticipant]:
    """Ученик сам присоединяется к УЖЕ существующему occurrence (обычно —
    выбранному из `list_bookable_occurrences_for_student`), а не создаёт
    отдельный ad-hoc. Идемпотентно: уже участвующий ученик получает свою
    текущую строку без ошибки.

    :raises DomainError: 404 — occurrence не найден; 409 — уже прошёл, или
        пересекается с другим активным занятием этого ученика.
    """
    occurrence = await _occurrence_repo.get_by_id(db, occurrence_id)
    if occurrence is None:
        raise DomainError(f"Занятие id={occurrence_id} не найдено", status_code=404)

    existing = await _participant_repo.get(
        db, occurrence_id=occurrence_id, student_id=student_id
    )
    if existing is not None:
        return occurrence, existing

    if occurrence.scheduled_at <= datetime.now(timezone.utc):
        raise DomainError("Занятие уже началось или прошло", status_code=409)

    overlap = await _participant_repo.has_student_overlap(
        db,
        student_id=student_id,
        scheduled_at=occurrence.scheduled_at,
        duration_minutes=occurrence.duration_minutes,
        exclude_occurrence_id=occurrence.id,
    )
    if overlap:
        raise DomainError(
            "Время пересекается с другим активным занятием этого ученика",
            status_code=409,
        )

    participant = await _participant_repo.create(
        db, occurrence_id=occurrence.id, student_id=student_id, status="scheduled",
    )
    await db.commit()
    await db.refresh(participant)
    logger.info(
        "lesson_occurrence join: occurrence=%s student=%s", occurrence.id, student_id,
    )
    return occurrence, participant


# ─── Reschedule + available slots (студент, по своему участию) ─────────────


async def _get_own_participant_for_reschedule(
    db: AsyncSession, *, occurrence_id: int, student_id: int
) -> tuple[LessonOccurrenceParticipant, LessonOccurrence]:
    occurrence = await _occurrence_repo.get_by_id(db, occurrence_id)
    if occurrence is None:
        raise DomainError(f"Занятие id={occurrence_id} не найдено", status_code=404)
    participant = await _participant_repo.get(
        db, occurrence_id=occurrence_id, student_id=student_id
    )
    if participant is None:
        raise DomainError(
            "Ученик не входит в число участников этого занятия", status_code=403
        )
    if participant.status in _LOCKED_STATUSES:
        raise DomainError(
            f"Участие уже в статусе '{participant.status}' — перенос недоступен",
            status_code=409,
        )
    return participant, occurrence


async def list_available_slots(
    db: AsyncSession,
    *,
    occurrence_id: int,
    student_id: int,
    limit: int = 10,
    horizon_days: int | None = None,
) -> list[datetime]:
    """Кандидаты для переноса — времена РЕАЛЬНЫХ слотов расписания тех же
    преподавателей, что ведут это занятие: в рамках `operating_hours`, без
    коллизий у ЭТОГО ученика (преподаватель по design может вести несколько
    occurrence одновременно — групповое расписание).

    Почему только свои преподаватели, а не любой слот школы: перенос
    сохраняет преподавателя. Предложи мы слот чужого преподавателя — ученик
    выбрал бы время, в которое ведёт не его человек, а занятие всё равно
    досталось бы прежнему; тот же разрыв между показанным и полученным, что
    и чинит эта задача. На проде все активные слоты и так у одного
    преподавателя, так что выбор ничего не сужает.

    Пустой список — у преподавателей нет активных слотов подходящей
    длительности либо все ближайшие заняты у самого ученика.
    """
    _participant, occurrence = await _get_own_participant_for_reschedule(
        db, occurrence_id=occurrence_id, student_id=student_id
    )

    return await _list_slot_candidates(
        db,
        teacher_ids=await _leading_teacher_ids(db, occurrence),
        duration_minutes=occurrence.duration_minutes,
        student_id=student_id,
        exclude_occurrence_id=occurrence.id,
        limit=limit,
        horizon_days=horizon_days,
    )


async def reschedule_occurrence(
    db: AsyncSession,
    *,
    occurrence_id: int,
    student_id: int,
    new_scheduled_at: datetime,
) -> tuple[LessonOccurrence, LessonOccurrenceParticipant]:
    """Перенести УЧАСТИЕ этого ученика: старая строка участника →
    `status=rescheduled` + `rescheduled_to_occurrence_id`, ученик садится в
    занятие на новое время с новой строкой участника (`status=scheduled`).
    Остальные участники старого (группового) occurrence не затрагиваются —
    их перенос независим (см. модель tsk-435).

    Новое время обязано совпадать с началом активного слота расписания тех
    же преподавателей — ровно с тем, что вернул `list_available_slots`
    (tsk-587). До этого приём был мягче выдачи и пропускал время, которого
    в списке не было: так занятие 4640 встало на среду 17:00, когда слотов
    в среду четыре и ни одного в 17:00.

    Без `attendance_event` для самого переноса — CHECK-constraint
    `attendance_event.action` не включает `rescheduled` (это состояние
    участника, не действие явки); полная провенанс —
    `rescheduled_to_occurrence_id` + смена `status` на старой записи.
    """
    old_participant, occurrence = await _get_own_participant_for_reschedule(
        db, occurrence_id=occurrence_id, student_id=student_id
    )

    if new_scheduled_at == occurrence.scheduled_at:
        raise DomainError(
            "Новое время совпадает с текущим — переносить некуда", status_code=409,
        )

    within_hours = await lesson_calendar_service.is_within_operating_hours(
        db, scheduled_at=new_scheduled_at, duration_minutes=occurrence.duration_minutes
    )
    if within_hours is False:
        raise DomainError(
            "Новое время вне часов работы школы (operating_hours)", status_code=422
        )

    teacher_ids = await _leading_teacher_ids(db, occurrence)
    slot = await _find_slot_at(
        db,
        teacher_ids=teacher_ids,
        scheduled_at=new_scheduled_at,
        duration_minutes=occurrence.duration_minutes,
    )
    if slot is None:
        raise DomainError(
            "В это время занятий по расписанию нет — выберите время из списка",
            status_code=422,
        )

    overlap = await _participant_repo.has_student_overlap(
        db,
        student_id=student_id,
        scheduled_at=new_scheduled_at,
        duration_minutes=occurrence.duration_minutes,
        exclude_occurrence_id=occurrence.id,
    )
    if overlap:
        raise DomainError(
            "Новое время пересекается с другим активным занятием этого ученика",
            status_code=409,
        )

    # tsk-464 + tsk-587: если на новое время занятие уже есть (по слоту или
    # просто у тех же преподавателей) — присоединиться к нему, а не плодить
    # параллельное. Живой инцидент tsk-464: ученик перенёс занятие на 10:00,
    # а в это время уже шёл групповой урок того же преподавателя — вместо
    # присоединения создался ВТОРОЙ occurrence на то же время.
    new_occurrence, new_participant = await _seat_student_at(
        db,
        student_id=student_id,
        scheduled_at=new_scheduled_at,
        duration_minutes=occurrence.duration_minutes,
        slot=slot,
        teacher_ids=teacher_ids,
        exclude_occurrence_id=occurrence.id,
    )

    old_participant.status = "rescheduled"
    old_participant.rescheduled_to_occurrence_id = new_occurrence.id
    old_participant.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(new_occurrence)
    await db.refresh(new_participant)
    logger.info(
        "lesson_occurrence участие перенесено: old_occ=%s new_occ=%s student=%s at=%s",
        occurrence.id, new_occurrence.id, student_id, new_scheduled_at,
    )
    return new_occurrence, new_participant
