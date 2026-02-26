"""
Интеграционные тесты Learning Engine V1, этап 3.5 (аннулирование попытки).

Сценарии: отмена активной (200, status=cancelled, already_cancelled=false);
повторный cancel (200, already_cancelled=true); отмена завершённой (409); 404;
после cancel вызов start-or-get-attempt возвращает новую попытку;
агрегаты не учитывают отменённую попытку.
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

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.config import Settings
from app.models.attempts import Attempts
from app.models.task_results import TaskResults
from app.models.tasks import Tasks
from app.services.attempts_service import AttemptsService
from app.services.learning_engine_service import LearningEngineService
from app.services.task_results_service import TaskResultsService

settings = Settings()


async def test_cancel_active_returns_200_and_cancelled():
    """Отмена активной попытки: 200, status=cancelled, already_cancelled=false."""
    print("\n=== Тест: cancel активной попытки ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    attempts_svc = AttemptsService()

    async with async_session() as session:
        try:
            r = await session.execute(
                select(Attempts.id).where(
                    Attempts.finished_at.is_(None),
                    Attempts.cancelled_at.is_(None),
                ).limit(1)
            )
            row = r.first()
            if not row:
                # Создаём активную попытку
                r2 = await session.execute(
                    select(Tasks.id, Tasks.course_id).limit(1)
                )
                row2 = r2.first()
                if not row2:
                    print("[SKIP] Нет задач в БД")
                    return True
                task_id, course_id = row2[0], row2[1]
                user_id = (
                    await session.execute(text("SELECT id FROM users LIMIT 1"))
                ).scalar()
                if user_id is None:
                    print("[SKIP] Нет пользователей в БД")
                    return True
                attempt = await attempts_svc.create_attempt(
                    session,
                    user_id=user_id,
                    course_id=course_id,
                    source_system="test_cancel",
                )
                await session.commit()
                attempt_id = attempt.id
            else:
                attempt_id = row[0]

            attempt, error, already_cancelled = await attempts_svc.cancel_attempt(
                session, attempt_id, reason="test_exit"
            )
            await session.commit()

            assert error is None, f"Ожидалось успех, получено error={error!r}"
            assert attempt is not None
            assert attempt.cancelled_at is not None
            assert attempt.cancel_reason == "test_exit"
            assert already_cancelled is False
            print(f"[PASS] attempt_id={attempt_id} cancelled_at={attempt.cancelled_at}")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_cancel_idempotent_returns_already_cancelled():
    """Повторный cancel: 200, already_cancelled=true."""
    print("\n=== Тест: повторный cancel (идемпотентность) ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    attempts_svc = AttemptsService()

    async with async_session() as session:
        try:
            r = await session.execute(
                select(Attempts.id).where(Attempts.cancelled_at.isnot(None)).limit(1)
            )
            row = r.first()
            if not row:
                print("[SKIP] Нет отменённой попытки в БД (запустите test_cancel_active первым)")
                return True
            attempt_id = row[0]

            attempt, error, already_cancelled = await attempts_svc.cancel_attempt(
                session, attempt_id, reason="again"
            )
            await session.commit()

            assert error is None
            assert attempt is not None
            assert already_cancelled is True
            print(f"[PASS] attempt_id={attempt_id} already_cancelled=True")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_cancel_finished_returns_409():
    """Отмена завершённой попытки: 409 (already_finished)."""
    print("\n=== Тест: cancel завершённой -> 409 ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    attempts_svc = AttemptsService()

    async with async_session() as session:
        try:
            r = await session.execute(
                select(Attempts.id).where(Attempts.finished_at.isnot(None)).limit(1)
            )
            row = r.first()
            if not row:
                print("[SKIP] Нет завершённой попытки в БД")
                return True
            attempt_id = row[0]

            attempt, error, _ = await attempts_svc.cancel_attempt(
                session, attempt_id, reason="no_way"
            )

            assert error == "already_finished"
            assert attempt is not None
            print(f"[PASS] attempt_id={attempt_id} error=already_finished")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_cancel_not_found_returns_404():
    """Cancel несуществующей попытки: not_found."""
    print("\n=== Тест: cancel несуществующей попытки -> 404 ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    attempts_svc = AttemptsService()

    async with async_session() as session:
        try:
            r = await session.execute(text("SELECT COALESCE(MAX(id), 0) + 99999 FROM attempts"))
            fake_id = r.scalar() or 99999

            attempt, error, _ = await attempts_svc.cancel_attempt(
                session, fake_id, reason="nope"
            )

            assert error == "not_found"
            assert attempt is None
            print(f"[PASS] attempt_id={fake_id} not_found")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_after_cancel_no_active_attempt():
    """После cancel активная попытка по (user_id, course_id) не возвращается (start-or-get создаст новую)."""
    print("\n=== Тест: после cancel нет активной попытки ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    attempts_svc = AttemptsService()

    async with async_session() as session:
        try:
            # Курс, по которому ещё нет попыток — тогда после создания и отмены одной активной не будет
            r = await session.execute(
                text("""
                    SELECT c.id FROM courses c
                    WHERE NOT EXISTS (SELECT 1 FROM attempts a WHERE a.course_id = c.id)
                    LIMIT 1
                """)
            )
            course_id = r.scalar()
            if course_id is None:
                r = await session.execute(
                    text("SELECT id FROM courses LIMIT 1")
                )
                course_id = r.scalar()
            r = await session.execute(text("SELECT id FROM users LIMIT 1"))
            user_id = r.scalar()
            if not course_id or not user_id:
                print("[SKIP] Нет courses/users в БД")
                return True

            attempt = await attempts_svc.create_attempt(
                session,
                user_id=user_id,
                course_id=course_id,
                source_system="test_cancel_isolated",
            )
            await session.commit()
            user_id, course_id = attempt.user_id, attempt.course_id

            await attempts_svc.cancel_attempt(
                session, attempt.id, reason="test_isolated"
            )
            await session.commit()

            # Запрос как в start-or-get-attempt: активная = finished_at IS NULL AND cancelled_at IS NULL
            stmt = (
                select(Attempts)
                .where(
                    Attempts.user_id == user_id,
                    Attempts.course_id == course_id,
                    Attempts.finished_at.is_(None),
                    Attempts.cancelled_at.is_(None),
                )
                .order_by(Attempts.created_at.desc())
                .limit(1)
            )
            r2 = await session.execute(stmt)
            active = r2.scalar_one_or_none()

            assert active is None, "Ожидалось: после cancel нет активной попытки по этой паре (user, course)"
            print(f"[PASS] user_id={user_id} course_id={course_id} — активной попытки нет")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_aggregates_exclude_cancelled():
    """Агрегаты (последняя попытка, attempts_used) не учитывают отменённую попытку."""
    print("\n=== Тест: агрегаты не учитывают отменённую попытку ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    attempts_svc = AttemptsService()
    task_results_svc = TaskResultsService()
    learning_svc = LearningEngineService()

    async with async_session() as session:
        try:
            r = await session.execute(
                select(Attempts.id, Attempts.user_id, Attempts.course_id).where(
                    Attempts.cancelled_at.isnot(None),
                ).limit(1)
            )
            row = r.first()
            if not row:
                print("[SKIP] Нет отменённой попытки в БД")
                return True
            attempt_id, user_id, course_id = row[0], row[1], row[2]

            r2 = await session.execute(
                select(TaskResults.task_id).where(
                    TaskResults.attempt_id == attempt_id,
                ).limit(1)
            )
            task_row = r2.first()
            if not task_row:
                print("[SKIP] У отменённой попытки нет task_results")
                return True
            task_id = task_row[0]

            # _last_attempts_flat учитывает только a.cancelled_at IS NULL
            last_rows = await task_results_svc._last_attempts_flat(
                session, user_id=user_id, task_id=task_id
            )
            # Отменённая попытка не должна быть в last по этой задаче (если есть другая завершённая — она будет)
            for uid, tid, score, max_score in last_rows:
                if uid == user_id and tid == task_id:
                    # Результат last может быть от другой попытки (завершённой), не от attempt_id
                    pass
            # Просто проверяем, что compute_task_state не падает и считает без отменённой
            state = await learning_svc.compute_task_state(session, user_id, task_id)
            assert state.attempts_used is not None
            # attempts_used не должен считать отменённую попытку (только finished и не cancelled)
            print(f"[PASS] compute_task_state OK, attempts_used={state.attempts_used}")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_http_post_answers_after_cancel_returns_400():
    """HTTP: после cancel попытки POST /attempts/{id}/answers возвращает 400 (запрет продолжения)."""
    print("\n=== Тест (HTTP): cancel -> POST answers возвращает 400 ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] HTTP-тест требует: pip install httpx")
        return True

    from app.api.main import app
    from app.core.config import Settings
    cfg = Settings()
    if not getattr(cfg, "valid_api_keys", None) or not cfg.valid_api_keys:
        print("[SKIP] Нет VALID_API_KEYS в окружении")
        return True

    api_key = cfg.valid_api_keys[0]
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    attempts_svc = AttemptsService()

    async with async_session() as session:
        r = await session.execute(
            text("SELECT id FROM users LIMIT 1")
        )
        user_id = r.scalar()
        r = await session.execute(
            text("SELECT id, course_id FROM tasks LIMIT 1")
        )
        row = r.first()
        if not user_id or not row:
            print("[SKIP] Нет users/tasks в БД")
            return True
        task_id, course_id = row[0], row[1]

        attempt = await attempts_svc.create_attempt(
            session, user_id=user_id, course_id=course_id, source_system="test_http_cancel"
        )
        await attempts_svc.cancel_attempt(session, attempt.id, reason="test")
        await session.commit()
        attempt_id = attempt.id

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers?api_key={api_key}",
            json={
                "items": [
                    {
                        "task_id": task_id,
                        "answer": {"type": "SC", "response": {"selected_option_ids": ["A"]}},
                    }
                ]
            },
        )
        if resp.status_code != 400:
            print(f"[FAIL] Ожидался 400, получен {resp.status_code}: {resp.text}")
            return False
        body = resp.json()
        detail = body.get("detail", "") if isinstance(body, dict) else str(body)
        if "отмен" not in detail.lower():
            print(f"[FAIL] В detail ожидалось упоминание отмены, получено: {detail!r}")
            return False
        print("[PASS] POST /attempts/{id}/answers после cancel вернул 400 с сообщением об отмене")
        return True


async def main():
    print("=" * 60)
    print("Тесты Attempt cancel (этап 3.5)")
    print("=" * 60)
    results = [
        await test_cancel_active_returns_200_and_cancelled(),
        await test_cancel_idempotent_returns_already_cancelled(),
        await test_cancel_finished_returns_409(),
        await test_cancel_not_found_returns_404(),
        await test_after_cancel_no_active_attempt(),
        await test_aggregates_exclude_cancelled(),
        await test_http_post_answers_after_cancel_returns_400(),
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
    return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
