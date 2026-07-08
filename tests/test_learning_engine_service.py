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


async def test_compute_task_state_active_attempt_passed():
    """
    Regression (2026-05-02): course-level attempt model.

    Фикс: compute_task_state не должен фильтровать `a.finished_at IS NOT NULL`.
    start-or-get-attempt возвращает один открытый attempt на (user, course),
    в который накапливаются task_results по многим задачам без finish.
    Если task_result имеет ratio>=0.5, состояние должно быть PASSED — даже когда
    содержащий attempt ещё не завершён.

    До фикса: attempts_used=0, state=OPEN/IN_PROGRESS → resolve_next_item
    отдавал ту же задачу снова и снова.
    """
    print("\n=== Тест: compute_task_state (PASSED при незавершённом course-level attempt) ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = LearningEngineService()

    async with async_session() as session:
        try:
            # Найти (user, task) где есть task_result с ratio>=0.5 в АКТИВНОМ
            # (finished_at IS NULL, cancelled_at IS NULL) attempt
            r = await session.execute(text("""
                SELECT tr.user_id, tr.task_id
                FROM task_results tr
                JOIN attempts a ON a.id = tr.attempt_id
                WHERE a.finished_at IS NULL AND a.cancelled_at IS NULL
                  AND tr.max_score > 0 AND tr.score::float / tr.max_score >= 0.5
                LIMIT 1
            """))
            row = r.first()
            if not row:
                print("[SKIP] Нет (user, task) с PASS-ответом в активном course-level attempt")
                return True
            student_id, task_id = row[0], row[1]
            state = await svc.compute_task_state(session, student_id, task_id)
            assert state.state == "PASSED", (
                f"Regression: при ratio>=0.5 в активном attempt ожидался PASSED, "
                f"получено {state.state} (attempts_used={state.attempts_used})"
            )
            assert state.attempts_used >= 1, (
                f"attempts_used должен учитывать task_results из активного attempt, "
                f"получено {state.attempts_used}"
            )
            print(f"[PASS] state=PASSED attempts_used={state.attempts_used}")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_resolve_next_item_skips_passed_in_active_attempt():
    """
    Regression (2026-05-02): resolve_next_item не должен возвращать ту же задачу,
    по которой уже есть PASS-ответ в активном course-level attempt.
    Связан с test_compute_task_state_active_attempt_passed.
    """
    print("\n=== Тест: resolve_next_item не возвращает PASSED-задачу из активного attempt ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = LearningEngineService()

    async with async_session() as session:
        try:
            r = await session.execute(text("""
                SELECT tr.user_id, tr.task_id
                FROM task_results tr
                JOIN attempts a ON a.id = tr.attempt_id
                JOIN user_courses uc ON uc.user_id = tr.user_id AND uc.is_active = true
                WHERE a.finished_at IS NULL AND a.cancelled_at IS NULL
                  AND tr.max_score > 0 AND tr.score::float / tr.max_score >= 0.5
                LIMIT 1
            """))
            row = r.first()
            if not row:
                print("[SKIP] Нет (user, task) PASS в активном attempt + активный курс")
                return True
            student_id, task_id = row[0], row[1]
            result = await svc.resolve_next_item(session, student_id)
            if result.type == "task":
                assert result.task_id != task_id, (
                    f"Regression: next-item вернул ту же задачу {task_id}, "
                    f"хотя она уже PASSED в активном attempt"
                )
            print(f"[PASS] next-item type={result.type} task_id={result.task_id} (не {task_id})")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_collect_courses_post_order():
    """
    tsk-127 (первопричина, 2026-07-08): _collect_courses_in_order обходит дерево
    POST-ORDER — подкурсы раньше курса-контейнера. Инвариант: каждый курс идёт
    ПОСЛЕ всех своих детей, корень — строго последним. Так next-item выдаёт
    контент подкурсов раньше материалов, привязанных напрямую к корню
    (регресс на дубль-импорт `authored:*` на корневом курсе).
    """
    print("\n=== Тест: _collect_courses_in_order post-order (подкурсы раньше корня) ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = LearningEngineService()

    async with async_session() as session:
        try:
            # Корень с максимальным числом потомков — самый показательный кейс.
            r = await session.execute(text("""
                SELECT parent_course_id, COUNT(*) AS n
                FROM course_parents
                GROUP BY parent_course_id
                ORDER BY n DESC
                LIMIT 1
            """))
            row = r.first()
            if not row:
                print("[SKIP] Нет ни одного курса с подкурсами (course_parents пуст)")
                return True
            root_id = int(row[0])

            order = await svc._collect_courses_in_order(session, root_id)
            assert order, "Обход вернул пустой список"
            assert order[-1] == root_id, (
                f"Post-order нарушен: корень {root_id} должен быть последним, "
                f"а список кончается на {order[-1]}"
            )
            pos = {cid: i for i, cid in enumerate(order)}

            # Инвариант post-order: индекс родителя > индекса каждого его ребёнка.
            edges = await session.execute(text("""
                SELECT parent_course_id, course_id
                FROM course_parents
                WHERE parent_course_id = ANY(:ids) AND course_id = ANY(:ids)
            """), {"ids": order})
            checked = 0
            for parent_id, child_id in edges.fetchall():
                if parent_id in pos and child_id in pos:
                    assert pos[child_id] < pos[parent_id], (
                        f"Post-order нарушен: подкурс {child_id} (поз {pos[child_id]}) "
                        f"идёт ПОСЛЕ родителя {parent_id} (поз {pos[parent_id]})"
                    )
                    checked += 1
            print(f"[PASS] root={root_id}, курсов в обходе={len(order)}, "
                  f"проверено рёбер родитель→ребёнок={checked}, корень последний")
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
        await test_compute_task_state_active_attempt_passed(),
        await test_resolve_next_item_skips_passed_in_active_attempt(),
        await test_collect_courses_post_order(),
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
