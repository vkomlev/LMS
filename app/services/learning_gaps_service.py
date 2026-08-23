"""Датчик учебных пробелов (tsk-572, фаза 7).

Находит темы, на которых ученики массово спотыкаются, — основание для заявки
методисту на мини-курс повторения.

**Единственная причина, по которой этот модуль устроен именно так.**
`task_results` на проде состоит из двух совершенно разных вещей:

    manual_teacher   11 643 строки, 0.0% ошибок  — отметка преподавателя
    spw_web           2 191 строка, 24.5% ошибок — реальная сдача ученика

Первое — не ответы учеников, а простановка зачёта задним числом, и ошибок там
нет по определению. Датчик, считающий по сырой таблице, получает частоту ошибок,
разбавленную примерно вшестеро, и **молча не срабатывает никогда**: порог не
берётся, заявок нет, всё выглядит благополучно. Ни ошибки, ни лога — просто
тишина там, где должен быть сигнал.

Поэтому фильтр источника живёт в ОДНОЙ именованной функции, через которую
обязаны идти все аналитические пути. Скопировать запрос мимо неё — значит
воспроизвести дефект.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Каналы, где ответ набирает САМ ученик. Список явный, а не «всё кроме
# manual_teacher»: новый служебный источник (прогон, миграция, импорт) не должен
# автоматически считаться ученической работой только потому, что его забыли
# внести в исключения. Появится сдача из ТГ-бота — сюда добавляется строка.
REAL_STUDENT_SOURCES: tuple[str, ...] = ("spw_web",)

# Ниже этого числа сдач тема не считается: на трёх ответах «66% ошибок» не значит
# ничего, кроме того, что отвечали трое.
MIN_SUBMISSIONS = 20
# Доля неверных, с которой тема попадает в кандидаты на мини-курс.
ERROR_RATE_THRESHOLD = 0.35
# Минимум РАЗНЫХ учеников. Найдено живым прогоном по проду: почти все кандидаты
# по порогу ошибок оказались с одним учеником — «29 сдач, 1 ученик, 59% ошибок»
# говорит о человеке, а не о теме. Мини-курс на такое заводить нельзя: это либо
# личный затык (лечится преподавателем), либо чей-то прогон. Без этого порога
# методиста завалило бы артефактами, и он перестал бы читать заявки вовсе.
MIN_STUDENTS = 3


def real_student_results_filter(alias: str = "tr") -> str:
    """Условие «это реальная сдача ученика» для SQL.

    ЕДИНСТВЕННОЕ место, где живёт правило. Любой аналитический запрос обязан
    подставлять его отсюда, а не переписывать руками.
    """
    sources = ", ".join(f"'{s}'" for s in REAL_STUDENT_SOURCES)
    return f"{alias}.source_system IN ({sources})"


#: Провенанс ручной отметки — один и тот же у заданий и у материалов.
MANUAL_TEACHER_SOURCE = "manual_teacher"


def real_student_material_filter(alias: str = "smp") -> str:
    """Условие «материал прошёл САМ ученик» для SQL (tsk-656).

    Парное правило к `real_student_results_filter`, но условие обратное по форме,
    и перепутать их дорого. У `student_material_progress` провенанс живёт в
    колонке `source`, и реальное прохождение помечается `'system'`: при настоящем
    прохождении `learning_events_service` ПЕРЕЗАПИСЫВАЕТ `source` с
    `'manual_teacher'` на `'system'` (tsk-297) — иначе снятие ручной отметки
    удалило бы настоящий прогресс ученика. Поэтому здесь чёрный список из одного
    значения, а не белый: у заданий `'system'` — служебный дефолт колонки и в
    ученическую работу не входит, у материалов `'system'` — как раз она.

    На проде (2026-08-23): 4662 строки `manual_teacher` против 2186 `system`.
    """
    return f"{alias}.source IS DISTINCT FROM '{MANUAL_TEACHER_SOURCE}'"


@dataclass
class TopicGap:
    """Тема-кандидат на мини-курс повторения."""

    course_id: int
    course_title: str
    submissions: int
    students: int
    wrong_rate: float

    @property
    def wrong_percent(self) -> int:
        return round(self.wrong_rate * 100)


_GAPS_SQL = """
SELECT t.course_id,
       c.title AS course_title,
       COUNT(*) AS submissions,
       COUNT(DISTINCT tr.user_id) AS students,
       COUNT(*) FILTER (WHERE tr.is_correct IS FALSE)::float / COUNT(*) AS wrong_rate
FROM task_results tr
JOIN tasks t ON t.id = tr.task_id AND t.is_active
JOIN courses c ON c.id = t.course_id
WHERE {real_student}
  AND tr.received_at > now() - make_interval(days => :days)
GROUP BY t.course_id, c.title
HAVING COUNT(*) >= :min_submissions
   AND COUNT(DISTINCT tr.user_id) >= :min_students
   AND COUNT(*) FILTER (WHERE tr.is_correct IS FALSE)::float / COUNT(*) >= :threshold
ORDER BY wrong_rate DESC, submissions DESC
LIMIT :limit
"""


async def find_topic_gaps(
    db: AsyncSession,
    *,
    days: int = 30,
    min_submissions: int = MIN_SUBMISSIONS,
    min_students: int = MIN_STUDENTS,
    threshold: float = ERROR_RATE_THRESHOLD,
    limit: int = 20,
) -> list[TopicGap]:
    """Темы с высокой долей неверных сдач за период.

    Считает ТОЛЬКО по реальным ученическим сдачам — см. модульную докстроку.
    """
    sql = _GAPS_SQL.format(real_student=real_student_results_filter("tr"))
    rows = (await db.execute(text(sql), {
        "days": days, "min_submissions": min_submissions,
        "min_students": min_students, "threshold": threshold, "limit": limit,
    })).mappings().all()

    gaps = [
        TopicGap(
            course_id=int(r["course_id"]), course_title=r["course_title"],
            submissions=int(r["submissions"]), students=int(r["students"]),
            wrong_rate=float(r["wrong_rate"]),
        )
        for r in rows
    ]
    logger.info(
        "learning_gaps: найдено тем-кандидатов %s (период %s дн., порог %.0f%%, "
        "минимум сдач %s, минимум учеников %s)",
        len(gaps), days, threshold * 100, min_submissions, min_students,
    )
    return gaps


async def source_breakdown(db: AsyncSession, days: int = 30) -> list[dict]:
    """Разбивка сдач по источникам — диагностика самого датчика.

    Нужна, чтобы поймать перекос ДО того, как он тихо обнулит сигнал: если
    служебный источник вдруг вырос, это видно здесь, а не по отсутствию заявок.
    """
    rows = (await db.execute(text("""
        SELECT tr.source_system,
               COUNT(*) AS submissions,
               COUNT(*) FILTER (WHERE tr.is_correct IS FALSE) AS wrong
        FROM task_results tr
        WHERE tr.received_at > now() - make_interval(days => :days)
        GROUP BY tr.source_system
        ORDER BY submissions DESC
    """), {"days": days})).mappings().all()
    return [dict(r) for r in rows]
