"""tsk-997: общий движок условий достижений (`achievements.condition`).

**Каталог — данные, правила — код.** Строка `achievements` несёт
``condition = {"type": ..., <порог>}``. Движок знает реестр «тип условия →
откуда берётся число»; порог задаёт строка каталога. Новое достижение того же
вида — строка в таблице, новый вид — одна запись в `_REGISTRY` и функция
подсчёта в `load_learning_facts`.

**Незнакомый тип не выдаёт награду.** Битый или неизвестный ``condition`` —
это `False` и предупреждение в лог, а не исключение: опечатка в каталоге не
должна ни ронять фоновый тик, ни раздавать веху всей школе.

**Два семейства условий.**

- Удержание (tsk-032): ``weekly_streak``, ``between_lessons_items`` — числа
  берутся из `retention_service.compute_state`.
- Учёба за усилие (tsk-997): шесть видов ниже. Похвала за усилие, а не за
  талант (Дуэк): награждаем исправление ошибки, возврат к ней, рефлексию,
  вопрос, когда застрял, закрытый модуль и пройденный срез. Не награждаем:
  «с первой попытки», скорость, место среди других. Сравнения учеников между
  собой движок не умеет по построению — он считает только числа одного ученика.

**Общие фильтры сдач** (то же правило, что у метрики удержания): попытка не
отменена, источник не ручная проставка преподавателя, сдача относится к
учебному пути (`learning_path_results_filter` — без повторов и среза).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.learning_gaps_service import learning_path_results_filter
from app.services.teacher_lesson_summary_service import MANUAL_SOURCE

logger = logging.getLogger("app.achievements")

# --- удержание (tsk-032) ---
CONDITION_WEEKLY_STREAK = "weekly_streak"
CONDITION_ITEMS_TOTAL = "between_lessons_items"
# --- учёба за усилие (tsk-997) ---
CONDITION_FIXED_AFTER_ERROR = "fixed_after_error"
CONDITION_REVIEW_PASSED = "review_passed"
CONDITION_REFLECTION_DONE = "reflection_done"
CONDITION_HELP_ASKED = "help_asked"
CONDITION_MODULE_COMPLETED = "module_completed"
CONDITION_FINAL_CHECK_DONE = "final_check_done"

#: Тип условия → (имя порога в condition, функция «текущее число по фактам»).
#: Для серии веха берётся и по текущей, и по лучшей серии: однажды
#: достигнутая не отбирается при обрыве (tsk-032).
_REGISTRY: dict[str, tuple[str, Callable[[dict[str, Any]], int]]] = {
    CONDITION_WEEKLY_STREAK: (
        "weeks", lambda f: max(f["weekly_streak"], f["best_weekly_streak"]),
    ),
    CONDITION_ITEMS_TOTAL: ("count", lambda f: f["items_total"]),
    CONDITION_FIXED_AFTER_ERROR: ("count", lambda f: f[CONDITION_FIXED_AFTER_ERROR]),
    CONDITION_REVIEW_PASSED: ("count", lambda f: f[CONDITION_REVIEW_PASSED]),
    CONDITION_REFLECTION_DONE: ("count", lambda f: f[CONDITION_REFLECTION_DONE]),
    CONDITION_HELP_ASKED: ("count", lambda f: f[CONDITION_HELP_ASKED]),
    CONDITION_MODULE_COMPLETED: ("count", lambda f: f[CONDITION_MODULE_COMPLETED]),
    CONDITION_FINAL_CHECK_DONE: ("count", lambda f: f[CONDITION_FINAL_CHECK_DONE]),
}

RETENTION_TYPES: frozenset[str] = frozenset({CONDITION_WEEKLY_STREAK, CONDITION_ITEMS_TOTAL})
LEARNING_TYPES: frozenset[str] = frozenset(_REGISTRY) - RETENTION_TYPES


def condition_type(condition: Any) -> Optional[str]:
    """Тип условия или ``None``, если ``condition`` не объект."""
    return condition.get("type") if isinstance(condition, dict) else None


def _target_and_value(condition: Any, facts: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    """(текущее, целевое) — или ``(None, None)``, если условие не разобрать."""
    if not isinstance(condition, dict):
        logger.warning("tsk-997: condition достижения не объект: %r", condition)
        return None, None
    ctype = condition.get("type")
    entry = _REGISTRY.get(ctype)
    if entry is None:
        logger.warning("tsk-997: неизвестный тип условия достижения: %r", ctype)
        return None, None
    param, getter = entry
    target = condition.get(param)
    # bool — подкласс int: {"count": true} не должен читаться как порог 1.
    if not isinstance(target, int) or isinstance(target, bool) or target <= 0:
        logger.warning("tsk-997: битый порог %s в condition: %r", param, condition)
        return None, None
    try:
        current = int(getter(facts))
    except KeyError:
        # Факты этого семейства не загружены вызывающим — не «получено».
        return None, None
    return current, target


def is_earned(condition: Any, facts: dict[str, Any]) -> bool:
    """Выполнено ли условие при данных фактах ученика."""
    current, target = _target_and_value(condition, facts)
    return current is not None and target is not None and current >= target


def progress_of(condition: Any, facts: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    """(текущее значение, целевое) для полосы прогресса; ``(None, None)`` — не показать."""
    current, target = _target_and_value(condition, facts)
    if current is None or target is None:
        return None, None
    return current, target


def empty_learning_facts() -> dict[str, int]:
    """Нулевые факты учёбы — для ученика без единой записи."""
    return {t: 0 for t in LEARNING_TYPES}


_RESULT_FILTERS = f"""
      AND tr.source_system IS DISTINCT FROM :manual_source
      AND {learning_path_results_filter('tr')}
"""

#: Разные задания, где была неверная сдача и ПОЗЖЕ — верная.
_FIXED_SQL = f"""
SELECT w.user_id, count(DISTINCT w.task_id) AS n
FROM task_results w
JOIN attempts wa ON wa.id = w.attempt_id AND wa.cancelled_at IS NULL
WHERE w.user_id = ANY(:ids) AND w.is_correct = false
  AND w.source_system IS DISTINCT FROM :manual_source
  AND {learning_path_results_filter('w')}
  AND EXISTS (
      SELECT 1 FROM task_results tr
      JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
      WHERE tr.user_id = w.user_id AND tr.task_id = w.task_id
        AND tr.is_correct = true AND tr.submitted_at > w.submitted_at
        {_RESULT_FILTERS}
  )
GROUP BY w.user_id
"""

#: Задания, по которым пройден хотя бы один интервальный повтор (tsk-992):
#: стадия выросла или строка закрыта. Неверный повтор сбрасывает стадию в 0 —
#: уже выданная веха при этом остаётся (однократная запись).
_REVIEW_SQL = """
SELECT student_id AS user_id, count(DISTINCT task_id) AS n
FROM task_review_schedule
WHERE student_id = ANY(:ids) AND (interval_stage > 0 OR completed_at IS NOT NULL)
GROUP BY student_id
"""

#: Рефлексии после разбора заявки (tsk-999) с непустым текстом.
_REFLECTION_SQL = """
SELECT student_id AS user_id, count(*) AS n
FROM help_requests
WHERE student_id = ANY(:ids) AND reflection_at IS NOT NULL
  AND btrim(coalesce(reflection_main, '')) <> ''
GROUP BY student_id
"""

#: Заявки помощи, поданные самим учеником (не авто при исчерпании лимита).
_HELP_SQL = """
SELECT student_id AS user_id, count(*) AS n
FROM help_requests
WHERE student_id = ANY(:ids) AND auto_created = false AND request_type = 'manual_help'
GROUP BY student_id
"""

#: Модуль = подкурс (есть родитель) с хотя бы одним активным обязательным
#: заданием; закрыт, когда по КАЖДОМУ такому заданию есть верная сдача.
#: Персональные послабления обязательности (requirement_overrides) не
#: учитываются — правило строже, а не мягче: модуль не засчитается раньше.
_MODULE_SQL = f"""
WITH req AS (
    SELECT t.id AS task_id, t.course_id
    FROM tasks t
    WHERE t.is_active = true AND t.requirement_level = 'required'
      AND EXISTS (SELECT 1 FROM course_parents cp WHERE cp.course_id = t.course_id)
),
req_count AS (SELECT course_id, count(*) AS total FROM req GROUP BY course_id),
done AS (
    SELECT DISTINCT tr.user_id, req.course_id, req.task_id
    FROM task_results tr
    JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
    JOIN req ON req.task_id = tr.task_id
    WHERE tr.user_id = ANY(:ids) AND tr.is_correct = true
      {_RESULT_FILTERS}
),
closed AS (
    SELECT d.user_id, d.course_id
    FROM done d JOIN req_count rc ON rc.course_id = d.course_id
    GROUP BY d.user_id, d.course_id, rc.total
    HAVING count(*) = rc.total
)
SELECT user_id, count(*) AS n FROM closed GROUP BY user_id
"""

#: Пройденные итоговые срезы (tsk-994): отвечено последнее задание.
_FINAL_SQL = """
SELECT student_id AS user_id, count(*) AS n
FROM final_check_runs
WHERE student_id = ANY(:ids) AND finished_at IS NOT NULL
GROUP BY student_id
"""

_FACT_QUERIES: dict[str, str] = {
    CONDITION_FIXED_AFTER_ERROR: _FIXED_SQL,
    CONDITION_REVIEW_PASSED: _REVIEW_SQL,
    CONDITION_REFLECTION_DONE: _REFLECTION_SQL,
    CONDITION_HELP_ASKED: _HELP_SQL,
    CONDITION_MODULE_COMPLETED: _MODULE_SQL,
    CONDITION_FINAL_CHECK_DONE: _FINAL_SQL,
}


async def load_learning_facts(
    db: AsyncSession, *, student_ids: list[int],
) -> dict[int, dict[str, int]]:
    """Факты учёбы для набора учеников: ``{student_id: {тип условия: число}}``.

    Один запрос на вид условия для всего набора — тик проходит по всей школе."""
    facts = {sid: empty_learning_facts() for sid in student_ids}
    if not student_ids:
        return facts
    params = {"ids": student_ids, "manual_source": MANUAL_SOURCE}
    for ctype, sql in _FACT_QUERIES.items():
        rows = (await db.execute(text(sql), params)).mappings().fetchall()
        for r in rows:
            sid = int(r["user_id"])
            if sid in facts:
                facts[sid][ctype] = int(r["n"])
    return facts
