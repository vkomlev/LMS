"""Разовая чистка остатков тестовых прогонов из Learn.public (tsk-333).

Тесты создавали курсы/задания с фиксированными названиями и коммитили их в
общую dev-БД. Упал тест до своего `finally: _cleanup(...)` — мусор оставался
навсегда: session-sweep в `tests/conftest.py` чистит только users/audit_event/
magic_link/guest_session, курсы и задания не чистит никто.

Последствие — тесты с ФИКСИРОВАННЫМИ идентификаторами (например
`external_uid='tsk272-task'` в `test_attempts_enrollment_hole_tsk272.py`)
падают на UniqueViolation при следующем прогоне.

После включения транзакционной изоляции (tsk-333) новый мусор не копится —
этот скрипт разбирает уже накопленный.

ВАЖНО: цели задаются ЯВНЫМ списком названий, а не маской «содержит тест».
Маска по слову «тест» цепляет живые курсы — например «Агент гипотез для
A/B-тестов». Расширять `_COURSE_TITLE_PATTERNS` только после проверки выборки.

Запуск:
    python scripts/cleanup_test_leftovers_tsk333.py            # dry-run (ROLLBACK)
    python scripts/cleanup_test_leftovers_tsk333.py --apply    # боевой COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from app.core.config import Settings  # noqa: E402

_logger = logging.getLogger("cleanup_test_leftovers")

# Явные шаблоны названий тестовых курсов (LIKE). Только литеральные
# префиксы тестовых фикстур — никаких «%тест%».
_COURSE_TITLE_PATTERNS: tuple[str, ...] = (
    "tsk261 %",
    "tsk272 %",
    "tsk273 %",
    "tsk297 %",
    "test_order_position",
)

_TARGET_COURSES_CTE = """
    WITH targets AS (
        SELECT id FROM courses
        WHERE {conds}
    )
"""


def _targets_cte() -> str:
    conds = " OR ".join(f"title LIKE :p{i}" for i in range(len(_COURSE_TITLE_PATTERNS)))
    return _TARGET_COURSES_CTE.format(conds=conds)


def _params() -> dict[str, str]:
    return {f"p{i}": pat for i, pat in enumerate(_COURSE_TITLE_PATTERNS)}


async def _inspect(conn) -> dict[str, int]:
    """Прочитать текущее состояние целей ДО записи."""
    row = (
        await conn.execute(
            text(
                _targets_cte()
                + """
                SELECT
                    (SELECT count(*) FROM targets) AS courses,
                    (SELECT count(*) FROM tasks WHERE course_id IN (SELECT id FROM targets)) AS tasks,
                    (SELECT count(*) FROM user_courses WHERE course_id IN (SELECT id FROM targets)) AS enrollments,
                    (SELECT count(*) FROM attempts WHERE course_id IN (SELECT id FROM targets)) AS attempts,
                    (SELECT count(*) FROM course_parents
                     WHERE course_id IN (SELECT id FROM targets)
                        OR parent_course_id IN (SELECT id FROM targets)) AS parent_links
                """
            ),
            _params(),
        )
    ).mappings().first()
    return dict(row)


async def _assert_no_real_course_touched(conn) -> None:
    """Страховка: ни одна связь course_parents не должна выходить за набор целей.

    Если тестовый курс окажется привязан к живому дереву (или наоборот),
    удаление заденет реальный контент — тогда останавливаемся.
    """
    leaks = (
        await conn.execute(
            text(
                _targets_cte()
                + """
                SELECT cp.parent_course_id, cp.course_id
                FROM course_parents cp
                WHERE (cp.course_id IN (SELECT id FROM targets))
                   <> (cp.parent_course_id IN (SELECT id FROM targets))
                """
            ),
            _params(),
        )
    ).all()
    if leaks:
        raise RuntimeError(
            f"СТОП: {len(leaks)} связей course_parents выходят за тестовый набор — "
            f"удаление заденет живые курсы: {leaks[:10]}"
        )

    real_enroll = (
        await conn.execute(
            text(
                _targets_cte()
                + """
                SELECT uc.user_id, uc.course_id
                FROM user_courses uc
                JOIN users u ON u.id = uc.user_id
                WHERE uc.course_id IN (SELECT id FROM targets)
                  AND u.email IS NOT NULL
                  AND u.email NOT ILIKE '%@example.%'
                """
            ),
            _params(),
        )
    ).all()
    if real_enroll:
        raise RuntimeError(
            f"СТОП: на тестовые курсы записаны пользователи с реальным email: {real_enroll[:10]}"
        )


async def _delete(conn) -> dict[str, int]:
    """Удалить цели. Порядок — от зависимых к курсам."""
    counts: dict[str, int] = {}
    cte = _targets_cte()
    params = _params()

    counts["course_parents"] = (
        await conn.execute(
            text(
                cte
                + """
                DELETE FROM course_parents
                WHERE course_id IN (SELECT id FROM targets)
                   OR parent_course_id IN (SELECT id FROM targets)
                """
            ),
            params,
        )
    ).rowcount
    counts["user_courses"] = (
        await conn.execute(
            text(cte + "DELETE FROM user_courses WHERE course_id IN (SELECT id FROM targets)"),
            params,
        )
    ).rowcount
    counts["tasks"] = (
        await conn.execute(
            text(cte + "DELETE FROM tasks WHERE course_id IN (SELECT id FROM targets)"),
            params,
        )
    ).rowcount
    counts["courses"] = (
        await conn.execute(
            text(cte + "DELETE FROM courses WHERE id IN (SELECT id FROM targets)"),
            params,
        )
    ).rowcount
    return counts


async def main(apply: bool) -> int:
    settings = Settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                before = await _inspect(conn)
                _logger.info("ДО: %s", before)
                if before["courses"] == 0:
                    _logger.info("Целей нет — чистить нечего.")
                    await trans.rollback()
                    return 0

                await _assert_no_real_course_touched(conn)

                counts = await _delete(conn)
                _logger.info("Удалено: %s", counts)

                after = await _inspect(conn)
                _logger.info("ПОСЛЕ (в той же транзакции): %s", after)
                if any(after[k] for k in ("courses", "tasks", "enrollments", "parent_links")):
                    raise RuntimeError(f"Цели удалены не полностью: {after}")

                if apply:
                    await trans.commit()
                    _logger.info("COMMIT — изменения зафиксированы.")
                else:
                    await trans.rollback()
                    _logger.info("DRY-RUN — ROLLBACK, БД не изменена. Для записи: --apply")
                return 0
            except Exception:
                await trans.rollback()
                raise
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="зафиксировать изменения (COMMIT)")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
