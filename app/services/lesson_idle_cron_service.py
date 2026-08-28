"""tsk-591: простой ученика во время занятия — сигнал преподавателю.

Запрос оператора 2026-08-08: на групповом занятии преподаватель не видит, что
кто-то завис или ушёл, — узнаёт в конце урока. Решение оператора 2026-08-09:
порог 10 минут, сигнал ТОЛЬКО преподавателю (событие в ленту + уведомление),
ученику не показывается ничего.

Тик раз в 3 минуты проходит по идущим прямо сейчас занятиям и по каждому
участнику решает одно из трёх: активен, ушёл (``away``), завис (``idle``).

──────────────────────────────────────────────────────────────────────────────
ЧТО СЧИТАЕТСЯ ПРИЗНАКОМ ЖИЗНИ

1. Содержательное действие — открыл задание, сдал ответ, открыл подсказку,
   попросил помощи, прошёл материал (``learning_events``,
   ``task_results.submitted_at``, ``student_material_progress.completed_at``).
2. Взаимодействие руками — печатал, касался экрана, листал страницу; приходит
   пульсом присутствия (``student_presence.last_interaction_at``).
3. Просто открытая вкладка (``last_seen_at``) — это НЕ признак жизни, а только
   признак «в системе». Различие между 2 и 3 и есть ответ на вопрос оператора
   «открыл задание или вообще вне системы».

──────────────────────────────────────────────────────────────────────────────
ПОЧЕМУ СИГНАЛ НЕ ВРЁТ (главное требование задачи)

Сигнал, который врёт, преподаватель перестанет читать через неделю. Поэтому
тревога поднимается только там, где тишина действительно означает проблему:

* **Ученик должен был начать работать.** Тревога возможна только после первого
  содержательного действия на этом занятии. Первые минуты урока преподаватель
  объясняет — молчат ВСЕ, и это норма, а не простой; занятие вообще может
  идти в другом формате (разбор у доски, видеосвязь), и тогда в кабинете не
  делает ничего никто. Без этого условия тик слал бы пачку тревог в начале
  каждого урока — ровно тот случай, когда сигналу перестают верить.
* **Чтение — это работа.** Ученик, который читает длинный материал и листает
  страницу, активен: взаимодействие приходит пульсом. Молчание 10 минут без
  единого касания — уже не чтение.
* **Один эпизод на один простой.** Событие создаётся один раз и живёт до
  возвращения ученика; тик его не дублирует. Держит это частичный уникальный
  индекс в БД, а не проверка в коде.
* **Возвращение закрывается само.** Как только появился признак жизни позже
  начала тишины, эпизод закрывается — преподаватель видит «вернулся» и не
  бежит зря. Второго уведомления при этом НЕТ: сигнал «уже всё в порядке» в
  почтовом ящике — это шум, ради которого ящик перестают открывать.
* **Не простой:** участник в статусе ``declined``/``rescheduled``/``no_show``/
  ``on_break`` (перерыв, отказ, перенос) и время вне окна занятия.

──────────────────────────────────────────────────────────────────────────────
РАБОТА С БД (уроки tsk-626)

Сторож одного worker'а живёт в ОТДЕЛЬНОЙ сессии, чья транзакция не
закрывается до конца прохода: блокировка транзакционная и снимается сама при
закрытии сессии. Сессионную (``pg_try_advisory_lock``) брать нельзя — она
привязана к конкретному соединению, а рабочая сессия после коммита берёт из
пула другое; на dev это незаметно (пул пуст), на бою блокировка утекла бы и
тик замолчал бы навсегда без единой ошибки в логе.

Коммит — после каждого занятия, а не один на весь проход: блокировки строк
живут ровно столько, сколько считается одно занятие.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import settings_store
from app.core.config import Settings
from app.db.session import async_session_factory
from app.services import inbox_service
# tsk-656: правило «это реальная сдача ученика» — из одного места.
from app.services.learning_gaps_service import (
    real_student_material_filter,
    real_student_results_filter,
)
from app.utils.task_title import humanize_task_title

logger = logging.getLogger("app.lesson_idle")

_MOSCOW_TZ = ZoneInfo("Europe/Moscow")

# ascii "LIDL" (Lesson IDLe) — не пересекается с ключами соседних тиков:
# Y-6 (0x59365453), генератор занятий (0x4C534E43), явка (0x4C534E41),
# ссылки (0x4C494E4B), кеш состояний курсов (0x43445354), вложения
# (0x41545443), начисления (0x43485247), оценка кода (0x43445256),
# удержание (0x52544E41).
_LESSON_IDLE_LOCK_KEY = 0x4C49444C

#: Статусы участия, при которых тишина ожидаема и тревоги быть не должно:
#: отказался, перенёс, отмечен не пришедшим, на перерыве (tsk-513).
_SKIP_PARTICIPANT_STATUSES = ("declined", "rescheduled", "no_show", "on_break")

#: Литералы для SQL — набор закрытый и объявлен здесь же, пользовательского
#: ввода в нём нет (см. комментарий `# nosec` у запроса).
_SKIP_STATUSES_SQL = ", ".join(f"'{s}'" for s in _SKIP_PARTICIPANT_STATUSES)

_scheduler: Optional[AsyncIOScheduler] = None


@dataclass
class _Participant:
    """Строка «участник идущего занятия» с метриками активности."""

    occurrence_id: int
    student_id: int
    student_name: Optional[str]
    lesson_start: datetime
    last_action_at: Optional[datetime]
    last_seen_at: Optional[datetime]
    last_interaction_at: Optional[datetime]
    context: Optional[str]
    task_id: Optional[int]
    material_id: Optional[int]
    course_id: Optional[int]
    task_title: Optional[str]
    material_title: Optional[str]

    @property
    def alive_at(self) -> Optional[datetime]:
        """Последний признак жизни: действие или взаимодействие руками."""
        candidates = [t for t in (self.last_action_at, self.last_interaction_at) if t]
        return max(candidates) if candidates else None

    @property
    def worked_on_lesson(self) -> bool:
        """Ученик уже начал работать на этом занятии.

        Именно содержательное действие, а не открытая вкладка: до первого
        действия молчание означает «идёт объяснение», а не «завис».
        """
        return self.last_action_at is not None and self.last_action_at >= self.lesson_start


#: Участники идущих занятий + их метрики активности одним запросом.
#: Подзапросы коррелированы, но участников идущих занятий единицы-десятки, а
#: каждый подзапрос ложится на существующий индекс.
_PARTICIPANTS_SQL = f"""
WITH live AS (
    SELECT lo.id AS occurrence_id,
           lo.scheduled_at AS lesson_start
    FROM lesson_occurrence lo
    WHERE lo.scheduled_at <= :now
      AND lo.scheduled_at + (lo.duration_minutes || ' minutes')::interval > :now
)
SELECT live.occurrence_id,
       live.lesson_start,
       lop.student_id,
       u.full_name AS student_name,
       sp.last_seen_at,
       sp.last_interaction_at,
       sp.context,
       COALESCE(sp.task_id, opened.task_id) AS task_id,
       sp.material_id,
       sp.course_id,
       t.external_uid,
       t.task_content->>'title' AS task_title_raw,
       t.task_content->>'stem' AS task_stem,
       m.title AS material_title,
       GREATEST(
           COALESCE((SELECT max(le.created_at) FROM learning_events le
                      WHERE le.student_id = lop.student_id
                        AND le.created_at >= live.lesson_start), to_timestamp(0)),
           -- tsk-656: сдача считается признаком жизни, только если её сделал
           -- САМ ученик. Ручная отметка преподавателя (`manual_teacher`)
           -- ставится пачками, и 2209 таких отметок на проде попали внутрь
           -- окон идущих занятий: преподаватель проставляет зачёты — датчик
           -- видит «ученик работает» и молчит ровно тогда, когда должен
           -- звать. Это тот самый случай, против которого написан модуль.
           COALESCE((SELECT max(tr.submitted_at) FROM task_results tr
                      WHERE tr.user_id = lop.student_id
                        AND {real_student_results_filter("tr")}
                        AND tr.submitted_at >= live.lesson_start), to_timestamp(0)),
           COALESCE((SELECT max(smp.completed_at) FROM student_material_progress smp
                      WHERE smp.student_id = lop.student_id
                        AND {real_student_material_filter("smp")}
                        AND smp.completed_at >= live.lesson_start), to_timestamp(0))
       ) AS last_action_at
FROM live
JOIN lesson_occurrence_participant lop ON lop.occurrence_id = live.occurrence_id
JOIN users u ON u.id = lop.student_id
LEFT JOIN student_presence sp ON sp.student_id = lop.student_id
-- Какое задание открыто: кабинет знает только вид страницы (адрес задания —
-- внешний код, а не номер), зато номер уже лежит в событии `task_opened`
-- (tsk-578). Второй раз спрашивать его у клиента незачем.
LEFT JOIN LATERAL (
    SELECT (le.payload->>'task_id')::int AS task_id
    FROM learning_events le
    WHERE le.student_id = lop.student_id
      AND le.event_type = 'task_opened'
      AND le.created_at >= live.lesson_start
    ORDER BY le.created_at DESC
    LIMIT 1
) opened ON TRUE
LEFT JOIN tasks t ON t.id = COALESCE(sp.task_id, opened.task_id)
LEFT JOIN materials m ON m.id = sp.material_id
WHERE lop.status NOT IN ({_SKIP_STATUSES_SQL})
ORDER BY live.occurrence_id, lop.student_id
"""  # nosec B608 — список статусов из константы модуля, не пользовательский ввод


def _format_lesson_time(scheduled_at: datetime) -> str:
    """Время занятия по-человечески — школьное, московское."""
    return scheduled_at.astimezone(_MOSCOW_TZ).strftime("%d.%m, %H:%M")


def _where_text(p: _Participant) -> str:
    """Где ученик находился, когда затих — для текста события."""
    if p.context == "task" and p.task_title:
        return f"открыто задание «{p.task_title}»"
    if p.context == "material" and p.material_title:
        return f"открыт материал «{p.material_title}»"
    if p.context == "task":
        return "открыто задание"
    if p.context == "material":
        return "открыт материал"
    if p.context == "course":
        return "открыт курс"
    return "кабинет открыт"


async def _load_participants(db: AsyncSession, *, now: datetime) -> List[_Participant]:
    """Участники идущих занятий с метриками активности."""
    rows = (
        await db.execute(text(_PARTICIPANTS_SQL), {"now": now})
    ).mappings().fetchall()

    result: List[_Participant] = []
    for r in rows:
        # to_timestamp(0) — «действий не было»: GREATEST с ним даёт эпоху,
        # а не NULL, и отличить «не работал» от «работал давно» иначе нельзя.
        last_action = r["last_action_at"]
        if last_action is not None and last_action.year <= 1970:
            last_action = None
        task_title = (
            humanize_task_title(
                r["task_id"], r["task_title_raw"], r["task_stem"], r["external_uid"]
            )
            if r["task_id"] is not None
            else None
        )
        result.append(
            _Participant(
                occurrence_id=int(r["occurrence_id"]),
                student_id=int(r["student_id"]),
                student_name=r["student_name"],
                lesson_start=r["lesson_start"],
                last_action_at=last_action,
                last_seen_at=r["last_seen_at"],
                last_interaction_at=r["last_interaction_at"],
                context=r["context"],
                task_id=r["task_id"],
                material_id=r["material_id"],
                course_id=r["course_id"],
                task_title=task_title,
                material_title=r["material_title"],
            )
        )
    return result


def classify(
    p: _Participant,
    *,
    now: datetime,
    threshold: timedelta,
    stale: timedelta,
) -> Optional[str]:
    """Что происходит с участником: ``None`` — тревоги нет, иначе ``away``/``idle``.

    Порядок проверок важен и отражает решения по ложным тревогам:

    1. Есть свежий признак жизни → активен, тревоги нет.
    2. Ученик ещё не начинал работать на этом занятии → тревоги нет
       (идёт объяснение / урок вообще не в кабинете).
    3. Пульса нет дольше порога → ``away`` («вне системы»).
    4. Пульс идёт, но признаков жизни нет дольше порога → ``idle``
       («открыл задание и молчит»).
    """
    alive_at = p.alive_at
    if alive_at is not None and now - alive_at < threshold:
        return None
    if not p.worked_on_lesson:
        return None

    online = p.last_seen_at is not None and now - p.last_seen_at <= stale
    if not online:
        # Пульса нет. Считаем тишину от последнего пульса, а если его не было
        # вовсе — от последнего действия (ученик работал через другой клиент
        # или из старой версии кабинета, ещё не умеющей слать пульс).
        gone_since = p.last_seen_at or p.last_action_at
        if gone_since is not None and now - gone_since >= threshold:
            return "away"
        return None

    if alive_at is None or now - alive_at >= threshold:
        return "idle"
    return None


def silent_since(p: _Participant, kind: str) -> datetime:
    """С какого момента считается тишина — попадает в текст события."""
    if kind == "away":
        return p.last_seen_at or p.last_action_at or p.lesson_start
    return p.alive_at or p.lesson_start


async def _load_open_episodes(
    db: AsyncSession, occurrence_ids: List[int]
) -> Dict[tuple[int, int], Dict[str, Any]]:
    """Незакрытые эпизоды по идущим занятиям: ключ — (занятие, ученик)."""
    if not occurrence_ids:
        return {}
    rows = (
        await db.execute(
            text(
                """
                SELECT id, occurrence_id, student_id, kind, silent_since
                FROM lesson_idle_episode
                WHERE resolved_at IS NULL AND occurrence_id = ANY(:ids)
                """
            ),
            {"ids": occurrence_ids},
        )
    ).mappings().fetchall()
    return {(int(r["occurrence_id"]), int(r["student_id"])): dict(r) for r in rows}


async def _notify_teachers(
    db: AsyncSession,
    p: _Participant,
    *,
    kind: str,
    minutes: int,
    lesson_start: datetime,
) -> None:
    """Уведомить преподавателей ЭТОГО занятия (совместное ведение — всех)."""
    rows = (
        await db.execute(
            text(
                # tsk-492: is_active — подмена на одно занятие. Подменённый его
                # не ведёт и писем о нём получать не должен.
                "SELECT teacher_id FROM lesson_occurrence_teacher "
                "WHERE occurrence_id = :oid AND is_active"
            ),
            {"oid": p.occurrence_id},
        )
    ).fetchall()
    teacher_ids = {int(r[0]) for r in rows}
    if not teacher_ids:
        # Со-преподавателей может не быть вовсе — тогда основной преподаватель
        # занятия, как в тике явки.
        fallback = (
            await db.execute(
                text("SELECT teacher_id FROM lesson_occurrence WHERE id = :oid"),
                {"oid": p.occurrence_id},
            )
        ).scalar()
        if fallback is None:
            return
        teacher_ids = {int(fallback)}

    student = p.student_name or f"Ученик #{p.student_id}"
    if kind == "away":
        title = "Ученик вышел из кабинета"
        content = (
            f"{student} не появляется в кабинете {minutes} мин "
            f"(занятие {_format_lesson_time(lesson_start)})."
        )
    else:
        title = "Ученик ничего не делает"
        content = (
            f"{student} {minutes} мин без действий — {_where_text(p)} "
            f"(занятие {_format_lesson_time(lesson_start)})."
        )

    payload = {
        "occurrence_id": p.occurrence_id,
        "student_id": p.student_id,
        "kind": kind,
        "minutes": minutes,
        "context": p.context,
        "task_id": p.task_id,
        "material_id": p.material_id,
        "course_id": p.course_id,
        "role": "teacher",
    }
    for teacher_id in sorted(teacher_ids):
        await inbox_service.create_for_user(
            db,
            user_id=teacher_id,
            kind="student_idle",
            title=title,
            content=content,
            payload=payload,
            created_by=None,
        )


async def _process_participant(
    db: AsyncSession,
    p: _Participant,
    open_episode: Optional[Dict[str, Any]],
    *,
    now: datetime,
    threshold: timedelta,
    stale: timedelta,
) -> str:
    """Обработать одного участника. Возвращает, что сделано (для summary)."""
    kind = classify(p, now=now, threshold=threshold, stale=stale)

    if open_episode is not None:
        episode_silent_since = open_episode["silent_since"]
        alive_at = p.alive_at
        # Возвращение: признак жизни ПОЗЖЕ начала тишины. Для ушедшего
        # засчитываем и сам факт возвращения в кабинет — пульс снова идёт.
        came_back = (alive_at is not None and alive_at > episode_silent_since) or (
            open_episode["kind"] == "away"
            and p.last_seen_at is not None
            and p.last_seen_at > episode_silent_since
        )
        if came_back:
            await db.execute(
                text(
                    "UPDATE lesson_idle_episode SET resolved_at = now(), updated_at = now() "
                    "WHERE id = :id AND resolved_at IS NULL"
                ),
                {"id": open_episode["id"]},
            )
            return "resolved"
        if kind is not None and kind != open_episode["kind"]:
            # Молчал в кабинете, а теперь и вкладку закрыл (или наоборот).
            # Уточняем эпизод, но НЕ шлём второе уведомление: преподаватель уже
            # предупреждён, а повтор об одном и том же — начало шума.
            await db.execute(
                text(
                    "UPDATE lesson_idle_episode SET kind = :kind, updated_at = now() "
                    "WHERE id = :id AND resolved_at IS NULL"
                ),
                {"id": open_episode["id"], "kind": kind},
            )
            return "updated"
        return "unchanged"

    if kind is None:
        return "ok"

    since = silent_since(p, kind)
    minutes = max(1, int((now - since).total_seconds() // 60))
    inserted = (
        await db.execute(
            text(
                """
                INSERT INTO lesson_idle_episode (
                    occurrence_id, student_id, kind, silent_since, detected_at,
                    context, task_id, material_id, course_id, created_at, updated_at
                )
                VALUES (
                    :occurrence_id, :student_id, :kind, :silent_since, now(),
                    :context, :task_id, :material_id, :course_id, now(), now()
                )
                ON CONFLICT (occurrence_id, student_id) WHERE resolved_at IS NULL
                DO NOTHING
                RETURNING id
                """
            ),
            {
                "occurrence_id": p.occurrence_id,
                "student_id": p.student_id,
                "kind": kind,
                "silent_since": since,
                "context": p.context,
                "task_id": p.task_id,
                "material_id": p.material_id,
                "course_id": p.course_id,
            },
        )
    ).scalar()
    if inserted is None:
        # Другой worker успел раньше — уведомление уже ушло от него.
        return "unchanged"

    await _notify_teachers(db, p, kind=kind, minutes=minutes, lesson_start=p.lesson_start)
    return "opened"


async def lesson_idle_cron_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> dict:
    """Один проход. Возвращает summary для логов и тестов."""
    factory = session_factory or async_session_factory
    settings = Settings()
    # tsk-721: рубильник проверяется в НАЧАЛЕ прохода, а не при поднятии
    # планировщика. Иначе включение обратно требовало бы перезапуска — то
    # есть ровно того, от чего задача и избавляет. Выключенный проход
    # просыпается и сразу выходит: работы он не делает.
    if not settings_store.get_bool("lesson_idle_cron_enabled"):
        logger.info("tsk-591: слежение за простоем выключено настройкой школы")
        return {
            "locked": False, "lessons": 0, "participants": 0,
            "opened": 0, "resolved": 0, "updated": 0,
        }

    # tsk-721: порог тишины берём на каждом проходе — администратор
    # меняет его в кабинете, и перезапуска это требовать не должно.
    threshold = timedelta(minutes=settings_store.get_int("lesson_idle_threshold_minutes"))
    stale = timedelta(seconds=int(settings.presence_stale_seconds))
    now = datetime.now(timezone.utc)

    summary = {
        "locked": False,
        "lessons": 0,
        "participants": 0,
        "opened": 0,
        "resolved": 0,
        "updated": 0,
    }

    # Сторож одного worker'а — ОТДЕЛЬНАЯ сессия, чья транзакция живёт до конца
    # прохода (tsk-626: сессионная блокировка утекла бы в пул подключений).
    async with factory() as guard:
        got = await guard.execute(
            text("SELECT pg_try_advisory_xact_lock(:k) AS locked"),
            {"k": _LESSON_IDLE_LOCK_KEY},
        )
        if not bool(got.scalar()):
            logger.debug("tsk-591: тик пропущен — работу делает другой worker")
            return summary
        summary["locked"] = True

        async with factory() as db:
            participants = await _load_participants(db, now=now)
            summary["participants"] = len(participants)
            occurrence_ids = sorted({p.occurrence_id for p in participants})
            summary["lessons"] = len(occurrence_ids)

            if participants:
                open_episodes = await _load_open_episodes(db, occurrence_ids)

                by_lesson: Dict[int, List[_Participant]] = {}
                for p in participants:
                    by_lesson.setdefault(p.occurrence_id, []).append(p)

                for occurrence_id in occurrence_ids:
                    for p in by_lesson[occurrence_id]:
                        outcome = await _process_participant(
                            db,
                            p,
                            open_episodes.get((p.occurrence_id, p.student_id)),
                            now=now,
                            threshold=threshold,
                            stale=stale,
                        )
                        if outcome in summary:
                            summary[outcome] += 1
                    # Коммит после каждого занятия (tsk-626): блокировки строк
                    # живут ровно столько, сколько считается одно занятие.
                    await db.commit()

                if summary["opened"] or summary["resolved"] or summary["updated"]:
                    logger.info(
                        "tsk-591 lesson_idle_cron_tick done lessons=%s participants=%s "
                        "opened=%s resolved=%s updated=%s",
                        summary["lessons"], summary["participants"],
                        summary["opened"], summary["resolved"], summary["updated"],
                    )

        # Сторож закрывается КОММИТОМ, а не откатом: под тестовой изоляцией обе
        # сессии сидят на одном соединении, и откат сторожа стёр бы уже
        # записанное (урок tsk-626).
        await guard.commit()

    return summary


def start_scheduler() -> Optional[AsyncIOScheduler]:
    """Поднять периодический тик (если включён настройкой)."""
    global _scheduler
    settings = Settings()
    # Планировщик поднимается всегда — работать или молчать, решает тик по
    # настройке школы (tsk-721).
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    interval_min = int(settings.lesson_idle_cron_interval_min)
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        lesson_idle_cron_tick,
        trigger=IntervalTrigger(minutes=interval_min),
        id="tsk591_lesson_idle_cron",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("tsk-591 lesson_idle scheduler started: interval=%smin", interval_min)
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("tsk-591 lesson_idle scheduler stopped")
    _scheduler = None
