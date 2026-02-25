"""
Backfill student_course_state (Learning Engine V1, этап 1).

Идемпотентно заполняет student_course_state для пар (user_id, course_id) из user_courses
только для активных записей (is_active IS TRUE). Деактивированные курсы не создают/не обновляют состояние.

- NOT_STARTED: нет завершённых попыток по курсу или ни по одной задаче нет результата
- IN_PROGRESS: есть завершённые попытки, но не по всем задачам курса есть результат
- COMPLETED: по каждой задаче курса есть хотя бы один результат в завершённой попытке

Повторный запуск безопасен (UPSERT по student_id, course_id).
Требуется только DATABASE_URL (из env или .env).
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# Состояния по ТЗ
STATE_NOT_STARTED = "NOT_STARTED"
STATE_IN_PROGRESS = "IN_PROGRESS"
STATE_COMPLETED = "COMPLETED"


async def run_backfill(db: AsyncSession) -> int:
    """
    Выполняет backfill student_course_state. Возвращает количество обновлённых/вставленных строк.
    """
    # Подсчёт задач по курсу и задач с результатом по (user, course) из завершённых попыток.
    # Затем UPSERT в student_course_state.
    upsert_sql = text("""
    WITH uc AS (
        SELECT user_id AS student_id, course_id FROM user_courses WHERE is_active IS TRUE
    ),
    task_counts AS (
        SELECT t.course_id, COUNT(*) AS total
        FROM tasks t
        GROUP BY t.course_id
    ),
    completed_per_user_course AS (
        SELECT a.user_id AS student_id, a.course_id,
               COUNT(DISTINCT tr.task_id) AS tasks_with_result
        FROM attempts a
        JOIN task_results tr ON tr.attempt_id = a.id
        WHERE a.finished_at IS NOT NULL AND a.course_id IS NOT NULL
        GROUP BY a.user_id, a.course_id
    ),
    computed AS (
        SELECT uc.student_id, uc.course_id,
               COALESCE(tc.total, 0) AS total_tasks,
               COALESCE(cpc.tasks_with_result, 0) AS tasks_with_result
        FROM uc
        LEFT JOIN task_counts tc ON tc.course_id = uc.course_id
        LEFT JOIN completed_per_user_course cpc
             ON cpc.student_id = uc.student_id AND cpc.course_id = uc.course_id
    ),
    state_computed AS (
        SELECT student_id, course_id,
               CASE
                   WHEN total_tasks = 0 THEN :not_started
                   WHEN tasks_with_result = 0 THEN :not_started
                   WHEN tasks_with_result >= total_tasks THEN :completed
                   ELSE :in_progress
               END AS state
        FROM computed
    )
    INSERT INTO student_course_state (student_id, course_id, state, updated_at)
    SELECT student_id, course_id, state, now()
    FROM state_computed
    ON CONFLICT (student_id, course_id)
    DO UPDATE SET state = EXCLUDED.state, updated_at = now()
    """)
    result = await db.execute(
        upsert_sql,
        {
            "not_started": STATE_NOT_STARTED,
            "in_progress": STATE_IN_PROGRESS,
            "completed": STATE_COMPLETED,
        },
    )
    rowcount = result.rowcount
    await db.commit()
    return rowcount if rowcount is not None else 0


async def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("Требуется переменная окружения DATABASE_URL")
        return 1
    engine = create_async_engine(database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with async_session() as session:
            n = await run_backfill(session)
            logger.info("Backfill student_course_state: обновлено/вставлено строк: %s", n)
            return 0
    except Exception as e:
        logger.exception("Backfill failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
