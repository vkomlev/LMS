"""Лента активности учеников для преподавателя (tsk-408).

Единый поток четырёх типов событий, читаемых из разных таблиц:

* ``task_solved`` — реальные (не ручной зачёт) результаты ``task_results``;
* ``help_requested`` — заявки помощи ``help_requests`` (момент создания);
* ``material_studied`` — завершённые (не ручной зачёт) материалы
  ``student_material_progress``;
* ``student_idle`` — простой ученика во время занятия ``lesson_idle_episode``
  (tsk-591): ушёл из кабинета или сидит без действий дольше порога.

Каждый источник читается ОТДЕЛЬНЫМ read-only запросом (top-``limit`` по своему
времени, с ACL и опциональным курсором ``before``), после чего результаты
сливаются и обрезаются до общего топ-``limit`` в Python. Для текущего объёма
данных (см. tsk-297/349) это проще и надёжнее одного гигантского UNION ALL по
разнородным таблицам, а стоимость — три недорогих индексных запроса вместо одного.

ACL — тот же принцип, что у ``manual_progress_service.can_edit_progress``
(tsk-297) и ``teacher_queue_service`` (``HELP_REQUESTS_ACL_SQL``/``REVIEW_ACL_SQL``):
методист/админ — bypass; преподаватель — только свои ученики
(``student_teacher_links``) или ученики на закреплённых за ним курсах
(``teacher_course_acl``, рекурсия по ``course_parents``).
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.current_user import CurrentUser
from app.schemas.code_review import build_code_review_badge
from app.services import roles_service
from app.services.learning_gaps_service import FINAL_CHECK_SOURCE, RETRIEVAL_SOURCE
from app.services.teacher_queue_service import teacher_course_acl
from app.utils.task_title import humanize_task_title

logger = logging.getLogger(__name__)

#: Провенанс синтетических (не реальных) записей — не студенческая активность.
_MANUAL_SOURCE = "manual_teacher"
_ELEVATED_ROLES = frozenset({"admin", "methodist"})
# tsk-1396: запас окна занятия для ведущего (в т.ч. на подмене), минуты.
_LESSON_WINDOW_BEFORE_MIN = 15
_LESSON_WINDOW_AFTER_MIN = 30

#: tsk-996: место источника в общем порядке ленты при равном времени. Лента
#: идёт по убыванию ``(время, ранг источника, ключ строки)`` — это полный
#: порядок: ключ строки уникален внутри источника, ранг различает источники.
_SOURCE_RANK: Dict[str, int] = {
    "task_solved": 4,
    "help_requested": 3,
    "material_studied": 2,
    "student_idle": 1,
}
#: Длина ключа строки у каждого источника (у прогресса материала ключ составной).
_KEY_LEN: Dict[str, int] = {
    "task_solved": 1, "help_requested": 1, "material_studied": 2, "student_idle": 1,
}

#: Верхняя граница ключа курсора: ключи источников — ``integer``, число больше
#: вызвало бы ошибку драйвера (500) вместо 422.
_MAX_KEY = 2**31 - 1


@dataclass(frozen=True)
class FeedCursor:
    """Составной курсор ленты (tsk-996): последнее событие отданной страницы.

    Следующая страница — события строго «после» него в порядке ленты, то есть
    раньше по времени, а при равном времени — ниже по рангу источника или ключу
    строки. Курсор только по времени (``before``) терял события с тем же
    временем, что у последнего на странице.
    """

    timestamp: datetime
    source: str
    key: Tuple[int, ...]

    def encode(self) -> str:
        """Непрозрачная строка для клиента (base64url от JSON, без «=»)."""
        raw = json.dumps(
            {"t": self.timestamp.isoformat(), "s": self.source, "k": list(self.key)},
            separators=(",", ":"),
        )
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, value: str) -> "FeedCursor":
        """Разобрать курсор клиента; на любой мусор — ``ValueError``.

        Время обязано быть с часовым поясом: колонки ленты — ``timestamptz``,
        а наивное время сравнивалось бы по поясу сессии БД.
        """
        try:
            padded = value + "=" * (-len(value) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            ts = datetime.fromisoformat(data["t"])
            source = data["s"]
            key = tuple(data["k"])
        except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error) as exc:
            raise ValueError("некорректный курсор ленты") from exc
        if ts.tzinfo is None or ts.utcoffset() is None:
            raise ValueError("время в курсоре ленты без часового пояса")
        if not 1970 <= ts.year <= 9000:
            # Крайние даты после перевода в UTC выходят за диапазон PG → 500.
            raise ValueError("время в курсоре ленты вне допустимого диапазона")
        if not isinstance(source, str) or source not in _SOURCE_RANK:
            raise ValueError("неизвестный источник в курсоре ленты")
        if len(key) != _KEY_LEN[source] or not all(
            isinstance(k, int) and not isinstance(k, bool) and 0 < k <= _MAX_KEY for k in key
        ):
            raise ValueError("некорректный ключ в курсоре ленты")
        return cls(timestamp=ts, source=source, key=key)


def _page_condition(
    conds: List[str],
    params: Dict[str, Any],
    *,
    source: str,
    time_col: str,
    key_cols: Sequence[str],
    before: Optional[datetime],
    cursor: Optional[FeedCursor],
) -> None:
    """Условие «после курсора» для одного источника.

    ``cursor`` — составной (tsk-996): источник с рангом ниже курсорного берёт
    и равное время, с рангом выше — только строго раньше, тот же источник —
    сравнение ``(время, ключ) < (время курсора, ключ курсора)``. Отдельное
    ``время <= курсор`` рядом с ним — чтобы план мог идти по индексу времени.
    ``before`` — прежний курсор только по времени, оставлен для старых клиентов.
    Столбцы — литералы из закрытого набора call-sites модуля.
    """
    if cursor is not None:
        params["cursor_ts"] = cursor.timestamp
        own, theirs = _SOURCE_RANK[source], _SOURCE_RANK[cursor.source]
        if own < theirs:
            conds.append(f"{time_col} <= :cursor_ts")
        elif own > theirs:
            conds.append(f"{time_col} < :cursor_ts")
        else:
            names = [f"cursor_k{i}" for i in range(len(key_cols))]
            params.update(zip(names, cursor.key))
            lhs = ", ".join([time_col, *key_cols])
            rhs = ", ".join([":cursor_ts", *(f":{n}" for n in names)])
            conds.append(f"{time_col} <= :cursor_ts AND ({lhs}) < ({rhs})")
    elif before is not None:
        conds.append(f"{time_col} < :before")
        params["before"] = before


def _scope_predicate(student_col: str, course_col: str, time_col: Optional[str] = None) -> str:
    """SQL-фрагмент «ученик/курс в зоне ответственности :teacher_id».

    Тот же принцип, что у ``can_edit_progress``: ученик закреплён напрямую
    (``student_teacher_links``) ИЛИ курс попадает под иерархический ACL
    (``teacher_course_acl``). Вызывается только когда bypass (admin/methodist)
    уже исключён на уровне Python — сюда user-input не попадает, только
    литералы столбцов из закрытого набора call-sites этого модуля.

    tsk-1396: с ``time_col`` добавляется окно занятия — событие ученика видно
    тому, кто ВЕДЁТ его занятие (в т.ч. на подмене), но только если оно
    случилось во время этого занятия (с запасом ``_LESSON_WINDOW_*``). Ни
    прошлого ученика до подмены, ни работы после неё подменяющий не видит.
    Владение занятием — как у ``students_on_lesson_now``: строка со-ведения
    сильнее колонки ``teacher_id``.
    """
    lesson_window = ""
    if time_col is not None:
        lesson_window = f"""
            OR EXISTS (
                SELECT 1 FROM lesson_occurrence_participant p
                JOIN lesson_occurrence lo ON lo.id = p.occurrence_id
                LEFT JOIN lesson_occurrence_teacher lot
                  ON lot.occurrence_id = lo.id AND lot.teacher_id = :teacher_id
                WHERE p.student_id = {student_col}
                  AND p.status NOT IN ('declined', 'rescheduled', 'no_show')
                  AND (lot.is_active IS TRUE OR (lot.id IS NULL AND lo.teacher_id = :teacher_id))
                  AND {time_col} >= lo.scheduled_at - make_interval(mins => {_LESSON_WINDOW_BEFORE_MIN})
                  AND {time_col} <= lo.scheduled_at
                      + make_interval(mins => lo.duration_minutes + {_LESSON_WINDOW_AFTER_MIN})
            )
        """
    return f"""
        (
            EXISTS (
                SELECT 1 FROM student_teacher_links stl
                WHERE stl.student_id = {student_col} AND stl.teacher_id = :teacher_id
            )
            OR ({course_col} IS NOT NULL AND {teacher_course_acl(course_col)})
            {lesson_window}
        )
    """  # nosec B608 — столбцы и числа из закрытого набора литералов модуля


async def _is_elevated(db: AsyncSession, current_user: CurrentUser) -> bool:
    """Сервисный токен / роль admin|methodist — полный доступ ко всем ученикам."""
    if current_user.is_service:
        return True
    roles = {
        r.lower().strip() for r in await roles_service.get_user_role_names(db, current_user.id)
    }
    return bool(roles & _ELEVATED_ROLES)


def _add_student_filter(
    conds: List[str], params: Dict[str, Any], student_col: str, student_id: Optional[int]
) -> None:
    """tsk-1362: сузить источник до одного ученика (лента «по ученику»).

    Фильтр добавляется К ACL, а не вместо него: чужой ученик даёт пустую ленту.
    ``student_col`` — литерал из закрытого набора call-sites модуля.
    """
    if student_id is None:
        return
    conds.append(f"{student_col} = :student_id")
    params["student_id"] = student_id


def _outcome_of(is_correct: Optional[bool]) -> str:
    if is_correct is None:
        return "pending_review"
    return "correct" if is_correct else "incorrect"


async def _fetch_task_solved(
    db: AsyncSession, *, teacher_id: int, elevated: bool, limit: int, before: Optional[datetime],
    student_id: Optional[int] = None, cursor: Optional[FeedCursor] = None,
) -> List[Dict[str, Any]]:
    """Реальные результаты заданий (без синтетических зачётов tsk-297)."""
    conds = ["tr.source_system IS DISTINCT FROM :manual_source"]
    params: Dict[str, Any] = {
        "manual_source": _MANUAL_SOURCE, "teacher_id": teacher_id, "limit": limit,
    }
    _page_condition(
        conds, params, source="task_solved", time_col="tr.submitted_at",
        key_cols=("tr.id",), before=before, cursor=cursor,
    )
    _add_student_filter(conds, params, "tr.user_id", student_id)
    if not elevated:
        conds.append(_scope_predicate("tr.user_id", "t.course_id", "tr.submitted_at"))
    where_sql = " AND ".join(conds)

    rows = (
        await db.execute(
            text(f"""
                SELECT tr.user_id AS student_id, u.full_name AS student_name,
                       tr.task_id, t.course_id, t.external_uid,
                       t.task_content->>'title' AS title_raw, t.task_content->>'stem' AS stem,
                       tr.is_correct, tr.submitted_at AS event_at,
                       tr.code_review, tr.source_system, tr.id AS row_id
                FROM task_results tr
                JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
                JOIN tasks t ON t.id = tr.task_id
                JOIN users u ON u.id = tr.user_id
                WHERE {where_sql}
                ORDER BY tr.submitted_at DESC, tr.id DESC
                LIMIT :limit
            """),  # nosec B608 — where_sql из закрытого набора литералов модуля
            params,
        )
    ).mappings().fetchall()

    events: List[Dict[str, Any]] = []
    for r in rows:
        title = humanize_task_title(r["task_id"], r["title_raw"], r["stem"], r["external_uid"])
        student = r["student_name"] or f"Ученик #{r['student_id']}"
        outcome = _outcome_of(r["is_correct"])
        verdict = {
            "correct": "верно", "incorrect": "неверно", "pending_review": "на проверке",
        }[outcome]
        verb = "сдал" if outcome == "pending_review" else "решил"
        if r["source_system"] == RETRIEVAL_SOURCE:
            # tsk-992: повтор уже пройденного задания — «решил неверно» читалось
            # бы как провал по учебному пути, а статус задания не меняется.
            verb = "повторил"
        elif r["source_system"] == FINAL_CHECK_SOURCE:
            # tsk-994: итоговый срез после курса — диагностика, статус задания
            # и курса он не меняет (решение Р4).
            verb = "проверил в итоговом срезе"
        events.append({
            "type": "task_solved",
            "student_id": int(r["student_id"]),
            "student_name": r["student_name"],
            "task_id": int(r["task_id"]),
            "material_id": None,
            "course_id": int(r["course_id"]) if r["course_id"] is not None else None,
            "timestamp": r["event_at"],
            "summary": f"{student} — {verb} задание «{title}» ({verdict})",
            "outcome": outcome,
            # tsk-302: компактный значок машинной оценки. Полный отчёт в ленту не
            # тащим — на сотне событий это лишний вес ради данных, которые в
            # строку списка всё равно не поместятся; подробности преподаватель
            # смотрит в карточке задания.
            "code_review": build_code_review_badge(r["code_review"]),
            "_key": (int(r["row_id"]),),
        })
    return events


async def _fetch_help_requested(
    db: AsyncSession, *, teacher_id: int, elevated: bool, limit: int, before: Optional[datetime],
    student_id: Optional[int] = None, cursor: Optional[FeedCursor] = None,
) -> List[Dict[str, Any]]:
    """Заявки помощи (момент создания — ``created_at``)."""
    conds = ["1 = 1"]
    params: Dict[str, Any] = {"teacher_id": teacher_id, "limit": limit}
    _page_condition(
        conds, params, source="help_requested", time_col="hr.created_at",
        key_cols=("hr.id",), before=before, cursor=cursor,
    )
    _add_student_filter(conds, params, "hr.student_id", student_id)
    if not elevated:
        conds.append(
            _scope_predicate("hr.student_id", "COALESCE(hr.course_id, t.course_id)", "hr.created_at")
        )
    where_sql = " AND ".join(conds)

    rows = (
        await db.execute(
            text(f"""
                SELECT hr.student_id, u.full_name AS student_name, hr.task_id,
                       COALESCE(hr.course_id, t.course_id) AS course_id,
                       t.external_uid, t.task_content->>'title' AS title_raw,
                       t.task_content->>'stem' AS stem, hr.status, hr.created_at AS event_at,
                       hr.material_id, m.title AS material_title,
                       hr.context_json->>'trigger' AS trigger,
                       hr.context_json->>'base_attempts_added' AS added,
                       hr.origin, hr.stuck_reason, hr.id AS row_id
                FROM help_requests hr
                JOIN users u ON u.id = hr.student_id
                LEFT JOIN tasks t ON t.id = hr.task_id
                LEFT JOIN materials m ON m.id = hr.material_id
                WHERE {where_sql}
                ORDER BY hr.created_at DESC, hr.id DESC
                LIMIT :limit
            """),  # nosec B608 — where_sql из закрытого набора литералов модуля
            params,
        )
    ).mappings().fetchall()

    events: List[Dict[str, Any]] = []
    for r in rows:
        student = r["student_name"] or f"Ученик #{r['student_id']}"
        # tsk-943: заявка либо по заданию (task_id), либо по материалу
        # (material_id) — «Я не понял» на материале не имеет task_id вовсе,
        # и голый int(None) здесь до этой правки падал бы на первой же такой
        # заявке (обнаружено при разработке tsk-943, живых записей ещё не было).
        task_id = r["task_id"]
        material_id = r["material_id"]
        outcome = r["status"]
        if task_id is not None:
            title = humanize_task_title(task_id, r["title_raw"], r["stem"], r["external_uid"])
            if r["trigger"] == "auto_extend":
                # tsk-1309: блокировку сняла система, человеку делать нечего —
                # строка для осведомлённости «ученик буксует на этом задании».
                added = f" ({r['added']} шт.)" if r["added"] else ""
                summary = (
                    f"{student} — исчерпал попытки по заданию «{title}», "
                    f"новые выданы автоматически{added}"
                )
                outcome = "auto_extended"
            elif r["origin"] == "oral":
                # tsk-1384: устную просьбу записал преподаватель.
                summary = f"{student} — попросил помощи устно по заданию «{title}»"
            elif r["stuck_reason"] == "no_start":
                summary = f"{student} — не знает, с чего начать задание «{title}»"
            else:
                summary = f"{student} — запросил помощь по заданию «{title}»"
        else:
            title = r["material_title"] or f"Материал #{material_id}"
            summary = f"{student} — не понял материал «{title}»"
        events.append({
            "type": "help_requested",
            "student_id": int(r["student_id"]),
            "student_name": r["student_name"],
            "task_id": int(task_id) if task_id is not None else None,
            "material_id": int(material_id) if material_id is not None else None,
            "course_id": int(r["course_id"]) if r["course_id"] is not None else None,
            "timestamp": r["event_at"],
            "summary": summary,
            "outcome": outcome,
            "_key": (int(r["row_id"]),),
        })
    return events


async def _fetch_material_studied(
    db: AsyncSession, *, teacher_id: int, elevated: bool, limit: int, before: Optional[datetime],
    student_id: Optional[int] = None, cursor: Optional[FeedCursor] = None,
) -> List[Dict[str, Any]]:
    """Завершённые материалы (без синтетических зачётов tsk-297)."""
    conds = [
        "smp.status = 'completed'",
        "smp.completed_at IS NOT NULL",
        "smp.source IS DISTINCT FROM :manual_source",
    ]
    params: Dict[str, Any] = {
        "manual_source": _MANUAL_SOURCE, "teacher_id": teacher_id, "limit": limit,
    }
    _page_condition(
        conds, params, source="material_studied", time_col="smp.completed_at",
        key_cols=("smp.student_id", "smp.material_id",), before=before, cursor=cursor,
    )
    _add_student_filter(conds, params, "smp.student_id", student_id)
    if not elevated:
        conds.append(_scope_predicate("smp.student_id", "m.course_id", "smp.completed_at"))
    where_sql = " AND ".join(conds)

    rows = (
        await db.execute(
            text(f"""
                SELECT smp.student_id, u.full_name AS student_name, smp.material_id,
                       m.course_id, m.title AS material_title, smp.completed_at AS event_at
                FROM student_material_progress smp
                JOIN materials m ON m.id = smp.material_id
                JOIN users u ON u.id = smp.student_id
                WHERE {where_sql}
                ORDER BY smp.completed_at DESC, smp.student_id DESC, smp.material_id DESC
                LIMIT :limit
            """),  # nosec B608 — where_sql из закрытого набора литералов модуля
            params,
        )
    ).mappings().fetchall()

    events: List[Dict[str, Any]] = []
    for r in rows:
        student = r["student_name"] or f"Ученик #{r['student_id']}"
        events.append({
            "type": "material_studied",
            "student_id": int(r["student_id"]),
            "student_name": r["student_name"],
            "task_id": None,
            "material_id": int(r["material_id"]),
            "course_id": int(r["course_id"]) if r["course_id"] is not None else None,
            "timestamp": r["event_at"],
            "summary": f"{student} — изучил материал «{r['material_title']}»",
            "outcome": None,
            "_key": (int(r["student_id"]), int(r["material_id"])),
        })
    return events


async def _fetch_student_idle(
    db: AsyncSession, *, teacher_id: int, elevated: bool, limit: int, before: Optional[datetime],
    student_id: Optional[int] = None, cursor: Optional[FeedCursor] = None,
) -> List[Dict[str, Any]]:
    """Простои учеников на занятиях (tsk-591) — эпизоды «затих → вернулся».

    ACL шире, чем у остальных источников, и это намеренно: кроме обычной зоны
    ответственности (свои ученики / свои курсы) событие видит тот, кто ВЕДЁТ
    это занятие. Иначе преподаватель на подмене не увидел бы простой ученика,
    сидящего прямо у него на уроке, — то есть ровно того, ради кого сигнал и
    заводился.
    """
    conds = ["1 = 1"]
    params: Dict[str, Any] = {"teacher_id": teacher_id, "limit": limit}
    _page_condition(
        conds, params, source="student_idle", time_col="e.detected_at",
        key_cols=("e.id",), before=before, cursor=cursor,
    )
    _add_student_filter(conds, params, "e.student_id", student_id)
    if not elevated:
        conds.append(
            f"""(
                {_scope_predicate("e.student_id", "e.course_id")}
                OR EXISTS (
                    SELECT 1 FROM lesson_occurrence_teacher lot
                    WHERE lot.occurrence_id = e.occurrence_id
                      AND lot.teacher_id = :teacher_id AND lot.is_active
                )
                OR EXISTS (
                    SELECT 1 FROM lesson_occurrence lo
                    WHERE lo.id = e.occurrence_id AND lo.teacher_id = :teacher_id
                )
            )"""
        )
    where_sql = " AND ".join(conds)

    rows = (
        await db.execute(
            text(f"""
                SELECT e.student_id, u.full_name AS student_name, e.kind,
                       e.task_id, e.material_id, e.course_id, e.context,
                       e.silent_since, e.detected_at, e.resolved_at,
                       t.external_uid, t.task_content->>'title' AS title_raw,
                       t.task_content->>'stem' AS stem,
                       m.title AS material_title, e.id AS row_id
                FROM lesson_idle_episode e
                JOIN users u ON u.id = e.student_id
                LEFT JOIN tasks t ON t.id = e.task_id
                LEFT JOIN materials m ON m.id = e.material_id
                WHERE {where_sql}
                ORDER BY e.detected_at DESC, e.id DESC
                LIMIT :limit
            """),  # nosec B608 — where_sql из закрытого набора литералов модуля
            params,
        )
    ).mappings().fetchall()

    events: List[Dict[str, Any]] = []
    for r in rows:
        student = r["student_name"] or f"Ученик #{r['student_id']}"
        resolved = r["resolved_at"] is not None
        end = r["resolved_at"] or r["detected_at"]
        minutes = max(1, int((end - r["silent_since"]).total_seconds() // 60))

        if r["context"] == "task" and r["task_id"] is not None:
            title = humanize_task_title(
                r["task_id"], r["title_raw"], r["stem"], r["external_uid"]
            )
            where = f", открыто задание «{title}»"
        elif r["context"] == "material" and r["material_title"]:
            where = f", открыт материал «{r['material_title']}»"
        else:
            where = ""

        if r["kind"] == "away":
            body = f"вышел из кабинета на {minutes} мин во время занятия"
        else:
            body = f"{minutes} мин без действий на занятии{where}"
        tail = " — уже вернулся" if resolved else ""

        events.append({
            "type": "student_idle",
            "student_id": int(r["student_id"]),
            "student_name": r["student_name"],
            "task_id": int(r["task_id"]) if r["task_id"] is not None else None,
            "material_id": int(r["material_id"]) if r["material_id"] is not None else None,
            "course_id": int(r["course_id"]) if r["course_id"] is not None else None,
            # Время события — момент обнаружения: лента про свежесть, а простой
            # становится фактом именно тогда, когда его заметили.
            "timestamp": r["detected_at"],
            "summary": f"{student} — {body}{tail}",
            "outcome": "resolved" if resolved else "ongoing",
            "_key": (int(r["row_id"]),),
        })
    return events


async def get_activity_feed(
    db: AsyncSession,
    current_user: CurrentUser,
    *,
    limit: int = 100,
    before: Optional[datetime] = None,
    cursor: Optional[FeedCursor] = None,
    student_id: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], bool, Optional[datetime], Optional[str]]:
    """Собрать топ-``limit`` событий по всем ученикам преподавателя, по убыванию времени.

    Порядок полный (tsk-996): ``(время, ранг источника, ключ строки)`` по
    убыванию, см. ``_SOURCE_RANK``.

    :param cursor: составной курсор пагинации (tsk-996) — клиент передаёт
        ``next_cursor`` предыдущей страницы; события с тем же временем на стыке
        страниц не теряются и не повторяются.
    :param before: прежний курсор только по времени — вернуть события строго
        раньше этого момента. Оставлен для старых клиентов: события с тем же
        временем, что у последнего на странице, с ним теряются. При ``cursor``
        не используется.
    :returns: (события, has_more, next_before, next_cursor). ``has_more`` — True
        в двух случаях: (1) слитый набор источников больше ``limit`` — значит,
        страница обрезала уже известные (полученные) события; (2) хотя бы
        один источник вернул ровно ``limit`` строк — у него могут быть ещё
        более старые записи, которые не запрашивались вовсе (``LIMIT`` мог
        обрезать раньше выборки). Только проверки (2) недостаточно: три
        источника по 60 строк каждый (180 > ``limit``=100) не капнутся ни по
        одному, но обрежутся при слиянии — без проверки (1) страница молча
        потеряла бы 80 реальных событий.
        ``next_before`` — время последнего (самого старого) события в ответе,
        ``next_cursor`` — составной курсор по нему же.
    :param student_id: tsk-1362 — только события этого ученика (в пределах ACL);
        пагинация та же, курсор работает внутри отбора.
    """
    elevated = await _is_elevated(db, current_user)
    teacher_id = current_user.id

    task_events, help_events, material_events, idle_events = [
        await fn(
            db, teacher_id=teacher_id, elevated=elevated, limit=limit, before=before,
            student_id=student_id, cursor=cursor,
        )
        for fn in (
            _fetch_task_solved,
            _fetch_help_requested,
            _fetch_material_studied,
            _fetch_student_idle,
        )
    ]

    sources = (task_events, help_events, material_events, idle_events)
    merged = sorted(
        [event for source in sources for event in source],
        key=lambda e: (e["timestamp"], _SOURCE_RANK[e["type"]], e["_key"]),
        reverse=True,
    )
    has_more = len(merged) > limit or any(len(source) == limit for source in sources)
    page = merged[:limit]
    next_before = page[-1]["timestamp"] if page else None
    next_cursor = (
        FeedCursor(
            timestamp=page[-1]["timestamp"], source=page[-1]["type"], key=page[-1]["_key"],
        ).encode()
        if page else None
    )
    for event in page:
        del event["_key"]
    return page, has_more, next_before, next_cursor


async def list_feed_students(
    db: AsyncSession, current_user: CurrentUser, *, on_lesson_ids: List[int],
) -> List[Dict[str, Any]]:
    """Все ученики, чьи события может видеть пользователь ленты (tsk-1362).

    Раньше список для фильтра собирался на клиенте из уже загруженных событий:
    ученика без недавней активности нельзя было найти вовсе. Здесь — та же зона,
    что у самой ленты, но без событий:

    * преподаватель — ученики из ``student_teacher_links``, записанные
      (``user_courses``) на курсы под его ``teacher_courses`` (с потомками по
      ``course_parents``), и те, кто сейчас у него на занятии; записанные
      на курс сотрудники без роли ``student`` в список не попадают (tsk-1396);
    * методист/админ — все ученики школы (роль ``student``).

    Нагрузка (урок tsk-1250): ОДИН запрос на весь список. Дерево курсов
    раскрывается один раз от курсов преподавателя вниз, а не рекурсией на
    каждую строку, как в предикате ``teacher_course_acl``.

    :returns: ``[{id, name, on_lesson}]``: сначала те, кто на занятии, затем по имени.
    """
    live = sorted({int(i) for i in on_lesson_ids})
    if await _is_elevated(db, current_user):
        sql = """
            SELECT u.id, u.full_name
            FROM users u
            WHERE u.merged_into_user_id IS NULL
              AND (
                EXISTS (
                    SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
                    WHERE ur.user_id = u.id AND r.name = 'student'
                )
                OR u.id = ANY(CAST(:live AS integer[]))
              )
        """
    else:
        sql = """
            WITH RECURSIVE acl AS (
                SELECT tc.course_id FROM teacher_courses tc WHERE tc.teacher_id = :teacher_id
                UNION
                SELECT cp.course_id FROM course_parents cp
                JOIN acl ON acl.course_id = cp.parent_course_id
            ), ids AS (
                SELECT stl.student_id AS id FROM student_teacher_links stl
                WHERE stl.teacher_id = :teacher_id
                UNION
                SELECT uc.user_id FROM user_courses uc
                WHERE uc.is_active AND uc.course_id IN (SELECT course_id FROM acl)
                UNION
                SELECT unnest(CAST(:live AS integer[]))
            )
            SELECT u.id, u.full_name
            FROM users u JOIN ids ON ids.id = u.id
            WHERE u.id <> :teacher_id AND u.merged_into_user_id IS NULL
              AND (
                EXISTS (
                    SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
                    WHERE ur.user_id = u.id AND r.name = 'student'
                )
                OR u.id = ANY(CAST(:live AS integer[]))
              )
        """
    rows = (
        await db.execute(text(sql), {"teacher_id": current_user.id, "live": live})
    ).mappings().fetchall()
    live_set = set(live)
    students = [
        {
            "id": int(r["id"]),
            "name": r["full_name"] or f"Ученик #{r['id']}",
            "on_lesson": int(r["id"]) in live_set,
        }
        for r in rows
    ]
    students.sort(key=lambda s: (not s["on_lesson"], s["name"].casefold(), s["id"]))
    return students
