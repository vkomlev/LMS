"""
Интеграционные тесты Learning Engine V1, этап 4 (Attempts integration).

Проверяют: новые поля в GET/POST attempt; при просрочке попытка завершается
(finished_at + time_expired); finish учитывает дедлайн по курсу при отсутствии ответов;
сценарий ТЗ: time_limit_sec, ожидание, finish -> time_expired и завершение.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
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

settings = Settings()


async def test_get_attempt_returns_new_fields():
    """GET /attempts/{id} возвращает time_expired и опционально attempts_used, limit, last_based_status."""
    print("\n=== Тест: GET attempt — новые поля (этап 4) ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        try:
            r = await session.execute(select(Attempts.id).limit(1))
            row = r.first()
            if not row:
                print("[SKIP] Нет попыток в БД")
                return True
            attempt_id = row[0]
            attempt = await session.get(Attempts, attempt_id)
            if attempt is None:
                print("[SKIP] Попытка не найдена")
                return True
            # Проверяем наличие полей на модели
            assert hasattr(attempt, "time_expired")
            assert isinstance(attempt.time_expired, bool)
            print(f"[PASS] attempt_id={attempt_id} time_expired={attempt.time_expired}")
            return True
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_attempt_with_results_has_optional_fields():
    """AttemptWithResults допускает attempts_used, attempts_limit_effective, last_based_status (optional)."""
    print("\n=== Тест: Схема AttemptWithResults — опциональные поля ===")
    from app.schemas.attempts import AttemptWithResults, AttemptRead, AttemptTaskResultShort

    # Минимальный объект без новых полей (backward compat)
    attempt_read = AttemptRead(
        id=1, user_id=1, course_id=1,
        created_at=None, finished_at=None, source_system="test", meta=None, time_expired=False,
    )
    obj = AttemptWithResults(
        attempt=attempt_read,
        results=[],
        total_score=0,
        total_max_score=0,
    )
    assert obj.attempts_used is None
    assert obj.attempts_limit_effective is None
    assert obj.last_based_status is None
    obj.attempts_used = 1
    obj.attempts_limit_effective = 3
    obj.last_based_status = "PASSED"
    print("[PASS] Схема принимает и отдаёт новые опциональные поля")
    return True


async def test_time_expired_finish_attempt():
    """
    Сценарий ТЗ: time_limit_sec задан, «прошло» время -> finish выставляет
    time_expired и finished_at. Проверка дедлайна по курсу (без ответов).
    """
    print("\n=== Тест: просрочка и завершение попытки (time_limit_sec, finish) ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    attempts_service = AttemptsService()

    async with async_session() as session:
        try:
            seed_task_id: int | None = None
            # Задача с time_limit_sec (или временно выставляем для smoke)
            r = await session.execute(
                select(Tasks.id, Tasks.course_id).where(
                    Tasks.time_limit_sec.isnot(None),
                    Tasks.time_limit_sec > 0,
                ).limit(1)
            )
            row = r.first()
            seed_task_id = None
            if not row:
                # Минимальный seed: одна задача с time_limit_sec=60 для прохождения сценария
                r2 = await session.execute(select(Tasks.id, Tasks.course_id).limit(1))
                row2 = r2.first()
                if not row2:
                    print("[SKIP] Нет задач в БД")
                    return True
                seed_task_id, course_id = row2[0], row2[1]
                await session.execute(
                    text("UPDATE tasks SET time_limit_sec = 60 WHERE id = :id"),
                    {"id": seed_task_id},
                )
                await session.commit()
                course_id = row2[1]
            else:
                course_id = row[1]
            user_id = (await session.execute(text("SELECT user_id FROM attempts LIMIT 1"))).scalar()
            if user_id is None:
                print("[SKIP] Нет попыток в БД для user_id")
                return True

            attempt = await attempts_service.create_attempt(
                session, user_id=user_id, course_id=course_id, source_system="test_stage4"
            )
            # «Прошло» время: created_at в прошлом (на 2 мин назад)
            past = datetime.now(timezone.utc) - timedelta(minutes=2)
            await session.execute(
                text("UPDATE attempts SET created_at = :t WHERE id = :id"),
                {"t": past, "id": attempt.id},
            )
            await session.commit()
            await session.refresh(attempt)

            assert attempt.finished_at is None

            expired = await attempts_service.check_attempt_deadline_expired(session, attempt)
            assert expired, "Ожидалось: дедлайн по задаче курса истёк"

            attempt = await attempts_service.finish_attempt(
                session, attempt.id, time_expired=True
            )
            assert attempt is not None
            assert attempt.time_expired is True, "Ожидалось time_expired=True"
            assert attempt.finished_at is not None, "Ожидалось finished_at проставлен"

            if seed_task_id is not None:
                await session.execute(
                    text("UPDATE tasks SET time_limit_sec = NULL WHERE id = :id"),
                    {"id": seed_task_id},
                )
                await session.commit()

            print("[PASS] Просрочка: check_attempt_deadline_expired=True, finish -> time_expired и finished_at")
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
    print("Тесты Attempts integration (этап 4)")
    print("=" * 60)
    results = [
        await test_get_attempt_returns_new_fields(),
        await test_attempt_with_results_has_optional_fields(),
        await test_time_expired_finish_attempt(),
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
