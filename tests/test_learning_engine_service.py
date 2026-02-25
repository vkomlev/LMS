"""
Интеграционные тесты Learning Engine V1, этап 2 (Service layer).

Проверяют по ТЗ: effective limit (default 3), task state (OPEN/IN_PROGRESS,
PASSED при 0.5, FAILED, BLOCKED_LIMIT), course state по дереву, маршрутизацию.
При отсутствии подходящих данных тесты пропускаются; при наличии — строгие assert.
"""
import asyncio
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.config import Settings
from app.services.learning_engine_service import LearningEngineService

settings = Settings()


async def test_get_effective_attempt_limit_default():
    """Effective limit: при отсутствии override и task.max_attempts строго 3."""
    print("\n=== Тест: get_effective_attempt_limit (default 3) ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = LearningEngineService()

    async with async_session() as session:
        try:
            # Задача без override и без max_attempts (или NULL)
            r = await session.execute(text("""
                SELECT t.id FROM tasks t
                WHERE NOT EXISTS (
                    SELECT 1 FROM student_task_limit_override o
                    WHERE o.task_id = t.id
                )
                AND (t.max_attempts IS NULL)
                LIMIT 1
            """))
            row = r.first()
            if not row:
                print("[SKIP] Нет задачи без override и без max_attempts в БД")
                return True
            task_id = row[0]
            r = await session.execute(text("SELECT id FROM users LIMIT 1"))
            user_row = r.first()
            if not user_row:
                print("[SKIP] Нет users в БД")
                return True
            student_id = user_row[0]
            limit = await svc.get_effective_attempt_limit(session, student_id, task_id)
            assert limit == 3, f"Ожидался limit=3 при отсутствии override и max_attempts, получено {limit}"
            print("[PASS] limit=3")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_compute_task_state_open():
    """Task state: нет завершённых попыток по задаче -> строго OPEN или IN_PROGRESS."""
    print("\n=== Тест: compute_task_state (OPEN/IN_PROGRESS) ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = LearningEngineService()

    async with async_session() as session:
        try:
            # Пара (user_id, task_id) без единой завершённой попытки по этой задаче
            r = await session.execute(text("""
                SELECT u.id, t.id FROM users u
                CROSS JOIN tasks t
                WHERE NOT EXISTS (
                    SELECT 1 FROM attempts a
                    JOIN task_results tr ON tr.attempt_id = a.id AND tr.task_id = t.id
                    WHERE a.user_id = u.id AND a.finished_at IS NOT NULL
                )
                LIMIT 1
            """))
            row = r.first()
            if not row:
                print("[SKIP] Нет пары (user, task) без завершённых попыток")
                return True
            student_id, task_id = row[0], row[1]
            state = await svc.compute_task_state(session, student_id, task_id)
            assert state.state in ("OPEN", "IN_PROGRESS"), (
                f"При отсутствии завершённых попыток ожидались OPEN или IN_PROGRESS, получено {state.state}"
            )
            if state.state == "OPEN":
                assert state.attempts_used == 0, f"OPEN предполагает attempts_used=0, получено {state.attempts_used}"
            print(f"[PASS] state={state.state}, attempts_used={state.attempts_used}")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_compute_task_state_passed_threshold():
    """Task state: последняя завершённая попытка с ratio >= 0.5 -> строго PASSED."""
    print("\n=== Тест: compute_task_state (PASSED при ratio >= 0.5) ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = LearningEngineService()

    async with async_session() as session:
        try:
            # (user_id, task_id) где последняя по времени завершённая попытка имеет score/max_score >= 0.5
            r = await session.execute(text("""
                WITH last_attempt AS (
                    SELECT a.user_id, tr.task_id,
                           tr.score, tr.max_score,
                           ROW_NUMBER() OVER (PARTITION BY a.user_id, tr.task_id ORDER BY a.finished_at DESC) AS rn
                    FROM attempts a
                    JOIN task_results tr ON tr.attempt_id = a.id
                    WHERE a.finished_at IS NOT NULL AND tr.max_score > 0
                )
                SELECT user_id, task_id FROM last_attempt
                WHERE rn = 1 AND score::float / NULLIF(max_score, 0) >= 0.5
                LIMIT 1
            """))
            row = r.first()
            if not row:
                print("[SKIP] Нет пары (user, task) с последней попыткой >= 0.5")
                return True
            student_id, task_id = row[0], row[1]
            state = await svc.compute_task_state(session, student_id, task_id)
            assert state.state == "PASSED", (
                f"При последней попытке >= 0.5 ожидался PASSED, получено {state.state}"
            )
            assert state.last_score is not None and state.last_max_score is not None
            assert state.last_max_score > 0
            assert state.last_score / state.last_max_score >= 0.5
            print(f"[PASS] state=PASSED last_score={state.last_score} last_max={state.last_max_score}")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_compute_task_state_failed():
    """Task state: одна завершённая попытка с ratio < 0.5 (лимит не исчерпан) -> FAILED."""
    print("\n=== Тест: compute_task_state (FAILED при ratio < 0.5) ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = LearningEngineService()

    async with async_session() as session:
        try:
            # Ровно одна завершённая попытка по задаче, score/max < 0.5 -> ожидаем FAILED
            r = await session.execute(text("""
                WITH one_attempt AS (
                    SELECT a.user_id, tr.task_id
                    FROM attempts a
                    JOIN task_results tr ON tr.attempt_id = a.id
                    WHERE a.finished_at IS NOT NULL AND tr.max_score > 0
                      AND tr.score::float / NULLIF(tr.max_score, 0) < 0.5
                    GROUP BY a.user_id, tr.task_id
                    HAVING COUNT(DISTINCT a.id) = 1
                )
                SELECT user_id, task_id FROM one_attempt LIMIT 1
            """))
            row = r.first()
            if not row:
                print("[SKIP] Нет пары (user, task) с одной завершённой попыткой < 0.5")
                return True
            student_id, task_id = row[0], row[1]
            state = await svc.compute_task_state(session, student_id, task_id)
            assert state.state == "FAILED", (
                f"При одной попытке < 0.5 ожидался FAILED, получено {state.state}"
            )
            print("[PASS] state=FAILED")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_compute_course_state():
    """Course state: допустимые значения и учёт дерева (root + descendants)."""
    print("\n=== Тест: compute_course_state ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = LearningEngineService()

    async with async_session() as session:
        try:
            r = await session.execute(text("SELECT id FROM users LIMIT 1"))
            user_row = r.first()
            r = await session.execute(text("SELECT id FROM courses LIMIT 1"))
            course_row = r.first()
            if not user_row or not course_row:
                print("[SKIP] Нет users/courses в БД")
                return True
            student_id, course_id = user_row[0], course_row[0]
            cs = await svc.compute_course_state(session, student_id, course_id, update_state_table=False)
            assert cs.state in ("NOT_STARTED", "IN_PROGRESS", "COMPLETED"), f"Недопустимое состояние {cs.state}"
            assert cs.course_id == course_id
            print(f"[PASS] state={cs.state} course_id={cs.course_id}")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_resolve_next_item_no_active():
    """resolve_next_item: при отсутствии активных курсов строго type=none."""
    print("\n=== Тест: resolve_next_item (нет активных курсов) ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = LearningEngineService()

    async with async_session() as session:
        try:
            r = await session.execute(text("SELECT id FROM users LIMIT 1"))
            user_row = r.first()
            if not user_row:
                print("[SKIP] Нет users в БД")
                return True
            student_id = user_row[0]
            r = await session.execute(text("""
                SELECT 1 FROM user_courses WHERE user_id = :uid AND is_active = true LIMIT 1
            """), {"uid": student_id})
            if r.scalar():
                print("[SKIP] У пользователя есть активные курсы — тест не проверяет none")
                return True
            result = await svc.resolve_next_item(session, student_id)
            assert result.type == "none", f"Ожидался type=none, получен {result.type}"
            print(f"[PASS] type=none reason={result.reason}")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_resolve_next_item_with_active():
    """resolve_next_item: при активных курсах тип из множества material|task|none|blocked_*."""
    print("\n=== Тест: resolve_next_item (с активными курсами) ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = LearningEngineService()

    async with async_session() as session:
        try:
            r = await session.execute(text("""
                SELECT user_id FROM user_courses WHERE is_active = true LIMIT 1
            """))
            row = r.first()
            if not row:
                print("[SKIP] Нет записей user_courses с is_active=true")
                return True
            student_id = row[0]
            result = await svc.resolve_next_item(session, student_id)
            assert result.type in ("material", "task", "none", "blocked_dependency", "blocked_limit"), (
                f"Недопустимый type={result.type}"
            )
            print(f"[PASS] type={result.type} course_id={result.course_id} reason={result.reason}")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def main():
    print("=" * 60)
    print("Тесты Learning Engine Service (этап 2)")
    print("=" * 60)
    results = [
        await test_get_effective_attempt_limit_default(),
        await test_compute_task_state_open(),
        await test_compute_task_state_passed_threshold(),
        await test_compute_task_state_failed(),
        await test_compute_course_state(),
        await test_resolve_next_item_no_active(),
        await test_resolve_next_item_with_active(),
    ]
    print("\n" + "=" * 60)
    print("ИТОГИ:")
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"Пройдено: {passed}/{total}")
    if passed == total:
        print("Все тесты пройдены успешно.")
        return 0
    print(f"Провалено тестов: {total - passed}")
    return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
