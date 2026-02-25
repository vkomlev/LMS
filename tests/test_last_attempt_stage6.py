"""
Тесты Learning Engine V1, этап 6 (Last-attempt statistics).

Проверяют: _is_pass, наличие last-based полей в ответах stats by-user/by-course/by-task,
что основной статус определяется по last, а best/avg — дополнительные.
Семантика: last хуже best / last лучше best (через подмену _last_attempts_flat).
"""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text
from app.services.task_results_service import TaskResultsService, PASS_THRESHOLD_RATIO


def test_is_pass():
    """PASS: max_score > 0 и score/max_score >= 0.5."""
    svc = TaskResultsService()
    assert svc._is_pass(5, 10) is True
    assert svc._is_pass(0, 10) is False
    assert svc._is_pass(4, 10) is False
    assert svc._is_pass(5, 10) is True
    assert svc._is_pass(10, 10) is True
    assert svc._is_pass(0, 0) is False
    print("[PASS] _is_pass: 0.5 порог и max_score > 0")


def test_last_worse_than_best_aggregates():
    """
    Семантика: две задачи, last хуже best.
    Task1: last 2/10 (FAIL); Task2: last 10/10 (PASS).
    Основной прогресс и счётчики должны считаться по last.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.config import Settings
    try:
        settings = Settings()
    except Exception:
        print("[SKIP] Нет настроек БД")
        return True
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = TaskResultsService()
    # (user_id, task_id, score, max_score) — последняя попытка по каждой задаче
    last_rows = [(1, 1, 2, 10), (1, 2, 10, 10)]

    async def _run():
        with patch.object(svc, "_last_attempts_flat", new_callable=AsyncMock, return_value=last_rows):
            async with async_session() as db:
                stats = await svc.get_stats_by_user(db, 1)
        assert stats["passed_tasks_count"] == 1, "Одна задача с last PASS (10/10)"
        assert stats["failed_tasks_count"] == 1, "Одна задача с last FAIL (2/10)"
        assert stats["progress_percent"] == 50.0, "1 из 2 = 50%"
        assert stats["last_score"] == 12, "2+10"
        assert stats["last_max_score"] == 20, "10+10"
        assert stats["current_score"] == 12
        assert stats["current_ratio"] == 0.6, "12/20"
        print("[PASS] last хуже best: агрегаты по last (1 PASS, 1 FAIL, 50%%)")

    return asyncio.run(_run())


def test_last_pass_main_status():
    """Семантика: одна задача, last PASS (5/10) — основной статус PASS."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.config import Settings
    try:
        settings = Settings()
    except Exception:
        print("[SKIP] Нет настроек БД")
        return True
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = TaskResultsService()
    last_rows = [(1, 1, 5, 10)]  # ровно порог

    async def _run():
        with patch.object(svc, "_last_attempts_flat", new_callable=AsyncMock, return_value=last_rows):
            async with async_session() as db:
                stats = await svc.get_stats_by_user(db, 1)
        assert stats["passed_tasks_count"] == 1
        assert stats["failed_tasks_count"] == 0
        assert stats["progress_percent"] == 100.0
        assert stats["last_ratio"] == 0.5
        print("[PASS] last PASS: passed_tasks_count=1, progress_percent=100")

    return asyncio.run(_run())


def test_last_fail_main_status():
    """Семантика: одна задача, last FAIL (4/10) — основной статус FAIL, не best."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.config import Settings
    try:
        settings = Settings()
    except Exception:
        print("[SKIP] Нет настроек БД")
        return True
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = TaskResultsService()
    last_rows = [(1, 1, 4, 10)]

    async def _run():
        with patch.object(svc, "_last_attempts_flat", new_callable=AsyncMock, return_value=last_rows):
            async with async_session() as db:
                stats = await svc.get_stats_by_user(db, 1)
        assert stats["passed_tasks_count"] == 0
        assert stats["failed_tasks_count"] == 1
        assert stats["progress_percent"] == 0.0
        assert stats["current_score"] == 4 and stats["last_max_score"] == 10
        print("[PASS] last FAIL: passed_tasks_count=0, progress_percent=0 (регресс-тест)")

    return asyncio.run(_run())


def test_no_completed_attempts():
    """Нет завершённых попыток: задача не учитывается как passed/failed."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.config import Settings
    try:
        settings = Settings()
    except Exception:
        print("[SKIP] Нет настроек БД")
        return True
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = TaskResultsService()
    last_rows = []

    async def _run():
        with patch.object(svc, "_last_attempts_flat", new_callable=AsyncMock, return_value=last_rows):
            async with async_session() as db:
                stats = await svc.get_stats_by_user(db, 1)
        assert stats["passed_tasks_count"] == 0
        assert stats["failed_tasks_count"] == 0
        assert stats["progress_percent"] == 0.0
        assert stats["current_score"] == 0 and stats["last_score"] == 0
        assert stats["last_max_score"] == 0 and stats["last_ratio"] == 0.0
        print("[PASS] Нет завершённых попыток: passed=0, failed=0, progress=0")

    return asyncio.run(_run())


def test_stats_by_user_has_last_fields():
    """GET stats by-user возвращает progress_percent, passed_tasks_count, current_score, last_*."""
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.config import Settings
    try:
        settings = Settings()
    except Exception:
        print("[SKIP] Нет настроек БД")
        return True
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = TaskResultsService()

    async def _run():
        async with async_session() as db:
            r = await db.execute(text("SELECT id FROM users LIMIT 1"))
            row = r.first()
            if not row:
                print("[SKIP] Нет users в БД")
                return True
            user_id = row[0]
            stats = await svc.get_stats_by_user(db, user_id)
        for key in (
            "progress_percent", "passed_tasks_count", "failed_tasks_count",
            "current_score", "current_ratio", "last_score", "last_max_score", "last_ratio",
        ):
            assert key in stats, f"В ответе by-user должно быть поле {key}"
        assert "average_score" in stats and "total_attempts" in stats
        print("[PASS] get_stats_by_user: все last-based и дополнительные поля присутствуют")
        return True

    return asyncio.run(_run())


def test_stats_by_course_has_last_fields():
    """GET stats by-course возвращает progress_percent, passed_tasks_count, failed_tasks_count."""
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.config import Settings
    try:
        settings = Settings()
    except Exception:
        print("[SKIP] Нет настроек БД")
        return True
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = TaskResultsService()

    async def _run():
        async with async_session() as db:
            r = await db.execute(text("SELECT id FROM courses LIMIT 1"))
            row = r.first()
            if not row:
                print("[SKIP] Нет courses в БД")
                return True
            course_id = row[0]
            stats = await svc.get_stats_by_course(db, course_id)
        for key in ("progress_percent", "passed_tasks_count", "failed_tasks_count"):
            assert key in stats, f"В ответе by-course должно быть поле {key}"
        assert "average_score" in stats and "tasks_count" in stats
        print("[PASS] get_stats_by_course: last-based и дополнительные поля присутствуют")
        return True

    return asyncio.run(_run())


def test_by_task_last_passed_failed_counts():
    """Семантика by-task: два пользователя — last один PASS, один FAIL → last_passed_count=1, last_failed_count=1."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.config import Settings
    try:
        settings = Settings()
    except Exception:
        print("[SKIP] Нет настроек БД")
        return True
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = TaskResultsService()
    # (user_id, task_id, score, max_score): user 1 PASS 10/10, user 2 FAIL 3/10
    last_rows = [(1, 99, 10, 10), (2, 99, 3, 10)]

    async def _run():
        with patch.object(svc, "_last_attempts_flat", new_callable=AsyncMock, return_value=last_rows):
            async with async_session() as db:
                stats = await svc.get_stats_by_task(db, 99)
        assert stats["last_passed_count"] == 1
        assert stats["last_failed_count"] == 1
        assert stats["passed_tasks_count"] == 1 and stats["failed_tasks_count"] == 1
        assert stats["progress_percent"] == 50.0
        print("[PASS] by-task: last_passed_count=1, last_failed_count=1, progress_percent=50")

    return asyncio.run(_run())


def test_stats_by_task_has_last_fields():
    """GET stats by-task возвращает progress_percent, passed_tasks_count, last_passed_count."""
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.config import Settings
    try:
        settings = Settings()
    except Exception:
        print("[SKIP] Нет настроек БД")
        return True
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = TaskResultsService()

    async def _run():
        async with async_session() as db:
            r = await db.execute(text("SELECT id FROM tasks LIMIT 1"))
            row = r.first()
            if not row:
                print("[SKIP] Нет tasks в БД")
                return True
            task_id = row[0]
            stats = await svc.get_stats_by_task(db, task_id)
        for key in ("progress_percent", "passed_tasks_count", "failed_tasks_count", "last_passed_count", "last_failed_count"):
            assert key in stats, f"В ответе by-task должно быть поле {key}"
        assert "average_score" in stats
        print("[PASS] get_stats_by_task: last-based и дополнительные поля присутствуют")
        return True

    return asyncio.run(_run())


def test_pass_threshold_constant():
    """PASS_THRESHOLD_RATIO = 0.5."""
    assert PASS_THRESHOLD_RATIO == 0.5
    print("[PASS] PASS_THRESHOLD_RATIO = 0.5")


def main():
    print("=" * 60)
    print("Тесты Last-attempt statistics (этап 6)")
    print("=" * 60)
    test_pass_threshold_constant()
    test_is_pass()
    test_last_worse_than_best_aggregates()
    test_last_pass_main_status()
    test_last_fail_main_status()
    test_no_completed_attempts()
    test_by_task_last_passed_failed_counts()
    test_stats_by_user_has_last_fields()
    test_stats_by_course_has_last_fields()
    test_stats_by_task_has_last_fields()
    print("\n" + "=" * 60)
    print("Все тесты пройдены успешно.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
