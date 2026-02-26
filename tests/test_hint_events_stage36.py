"""
Тесты Learning Engine V1, этап 3.6 (Hint events).

Проверяют: запись hint_open (200, deduplicated=false); повтор в окне дедупа (200, deduplicated=true);
409 при невалидной связке attempt/student/task; 409 при завершённой/отменённой попытке;
stats by-task и by-user возвращают hints_used_count, used_text_hints_count, used_video_hints_count;
регрессия типов полей stats.
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
from app.models.tasks import Tasks
from app.services.attempts_service import AttemptsService
from app.services.learning_events_service import record_hint_open
from app.services.tasks_service import TasksService
from app.services.task_results_service import TaskResultsService

settings = Settings()


async def test_record_hint_open_success():
    """Успешная запись hint_open: 200, deduplicated=false."""
    print("\n=== Тест: hint-events успешная запись ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    attempts_svc = AttemptsService()

    async with async_session() as session:
        r = await session.execute(select(Tasks.id, Tasks.course_id).limit(1))
        row = r.first()
        if not row:
            print("[SKIP] Нет задач в БД")
            return True
        task_id, course_id = row[0], row[1]
        r = await session.execute(text("SELECT id FROM users LIMIT 1"))
        user_id = r.scalar()
        if not user_id:
            print("[SKIP] Нет пользователей в БД")
            return True

        attempt = await attempts_svc.create_attempt(
            session, user_id=user_id, course_id=course_id,
            source_system="test_hint_events", meta={"task_ids": [task_id]},
        )
        await session.commit()

        event_id, deduplicated = await record_hint_open(
            session,
            student_id=user_id,
            attempt_id=attempt.id,
            task_id=task_id,
            hint_type="text",
            hint_index=0,
            action="open",
            source="student_execute",
        )
        await session.commit()

        assert deduplicated is False
        assert event_id > 0
        print(f"[PASS] event_id={event_id} deduplicated=False")
        return True


async def test_record_hint_open_deduplicated():
    """Повтор в окне дедупа: 200, deduplicated=true, тот же event_id."""
    print("\n=== Тест: hint-events повтор (дедуп) ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    attempts_svc = AttemptsService()

    async with async_session() as session:
        r = await session.execute(
            text("""
                SELECT le.id, (le.payload->>'attempt_id')::int AS attempt_id,
                       (le.payload->>'task_id')::int AS task_id, le.student_id
                FROM learning_events le
                WHERE le.event_type = 'hint_open'
                ORDER BY le.id DESC LIMIT 1
            """)
        )
        row = r.first()
        if not row:
            print("[SKIP] Нет событий hint_open в БД (запустите test_record_hint_open_success)")
            return True
        event_id_first, attempt_id, task_id, student_id = row[0], row[1], row[2], row[3]

        event_id, deduplicated = await record_hint_open(
            session,
            student_id=student_id,
            attempt_id=attempt_id,
            task_id=task_id,
            hint_type="text",
            hint_index=0,
            action="open",
            source="student_execute",
        )
        await session.commit()

        assert deduplicated is True
        assert event_id == event_id_first
        print(f"[PASS] event_id={event_id} deduplicated=True")
        return True


async def test_http_hint_events_200_first_then_dedupe():
    """HTTP: первый вызов 200 deduplicated=false, повтор 200 deduplicated=true."""
    print("\n=== Тест (HTTP): hint-events 200 + дедуп ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    from app.api.main import app
    from app.core.config import Settings
    cfg = Settings()
    if not getattr(cfg, "valid_api_keys", None) or not cfg.valid_api_keys:
        print("[SKIP] Нет VALID_API_KEYS")
        return True

    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    attempts_svc = AttemptsService()

    async with async_session() as session:
        r = await session.execute(select(Tasks.id, Tasks.course_id).limit(1))
        row = r.first()
        if not row:
            print("[SKIP] Нет задач в БД")
            return True
        task_id, course_id = row[0], row[1]
        r = await session.execute(text("SELECT id FROM users LIMIT 1"))
        user_id = r.scalar()
        if not user_id:
            print("[SKIP] Нет пользователей в БД")
            return True
        attempt = await attempts_svc.create_attempt(
            session, user_id=user_id, course_id=course_id,
            source_system="test_http_hint", meta={"task_ids": [task_id]},
        )
        await session.commit()
        attempt_id = attempt.id

    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    body = {
        "student_id": user_id,
        "attempt_id": attempt_id,
        "hint_type": "video",
        "hint_index": 1,
        "action": "open",
        "source": "test_http",
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp1 = await client.post(
            f"/api/v1/learning/tasks/{task_id}/hint-events?api_key={api_key}",
            json=body,
        )
        if resp1.status_code != 200:
            print(f"[FAIL] Первый вызов: ожидался 200, получен {resp1.status_code} {resp1.text}")
            return False
        data1 = resp1.json()
        if data1.get("deduplicated") is not False:
            print(f"[FAIL] Первый вызов: ожидался deduplicated=false, получен {data1}")
            return False
        event_id = data1.get("event_id")
        if not event_id:
            print(f"[FAIL] Нет event_id в ответе {data1}")
            return False

        resp2 = await client.post(
            f"/api/v1/learning/tasks/{task_id}/hint-events?api_key={api_key}",
            json=body,
        )
        if resp2.status_code != 200:
            print(f"[FAIL] Повтор: ожидался 200, получен {resp2.status_code}")
            return False
        data2 = resp2.json()
        if data2.get("deduplicated") is not True:
            print(f"[FAIL] Повтор: ожидался deduplicated=true, получен {data2}")
            return False
        if data2.get("event_id") != event_id:
            print(f"[FAIL] Повтор: ожидался event_id={event_id}, получен {data2.get('event_id')}")
            return False

    print("[PASS] HTTP 200 первый вызов (deduplicated=false), повтор (deduplicated=true, тот же event_id)")
    return True


async def test_http_hint_events_404():
    """HTTP: 404 при несуществующем задании."""
    print("\n=== Тест (HTTP): hint-events 404 ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    from app.api.main import app
    from app.core.config import Settings
    cfg = Settings()
    if not getattr(cfg, "valid_api_keys", None) or not cfg.valid_api_keys:
        print("[SKIP] Нет VALID_API_KEYS")
        return True

    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        r = await session.execute(text("SELECT COALESCE(MAX(id), 0) + 99999 FROM tasks"))
        fake_task_id = r.scalar()
    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/learning/tasks/{fake_task_id}/hint-events?api_key={api_key}",
            json={
                "student_id": 1,
                "attempt_id": 1,
                "hint_type": "text",
                "hint_index": 0,
                "action": "open",
                "source": "x",
            },
        )
        if resp.status_code != 404:
            print(f"[FAIL] Ожидался 404, получен {resp.status_code}")
            return False
    print("[PASS] HTTP 404 при несуществующем task_id")
    return True


async def test_http_hint_events_422():
    """HTTP: 422 при невалидном body (hint_index < 0 или отсутствует обязательное поле)."""
    print("\n=== Тест (HTTP): hint-events 422 ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    from app.api.main import app
    from app.core.config import Settings
    cfg = Settings()
    if not getattr(cfg, "valid_api_keys", None) or not cfg.valid_api_keys:
        print("[SKIP] Нет VALID_API_KEYS")
        return True

    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        r = await session.execute(select(Tasks.id).limit(1))
        task_id = r.scalar() or 1

    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/learning/tasks/{task_id}/hint-events?api_key={api_key}",
            json={
                "student_id": 1,
                "attempt_id": 1,
                "hint_type": "text",
                "hint_index": -1,
                "action": "open",
                "source": "x",
            },
        )
        if resp.status_code != 422:
            print(f"[FAIL] Ожидался 422 при hint_index=-1, получен {resp.status_code}")
            return False
    print("[PASS] HTTP 422 при невалидном body (hint_index < 0)")
    return True


async def test_stats_by_course_has_hint_fields():
    """by-course возвращает hints_used_count, used_text_hints_count, used_video_hints_count."""
    print("\n=== Тест: stats by-course — поля hint ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = TaskResultsService()

    async with async_session() as session:
        r = await session.execute(text("SELECT id FROM courses LIMIT 1"))
        course_id = r.scalar()
        if not course_id:
            print("[SKIP] Нет курсов в БД")
            return True
        stats = await svc.get_stats_by_course(session, course_id)
        assert "hints_used_count" in stats
        assert "used_text_hints_count" in stats
        assert "used_video_hints_count" in stats
        assert isinstance(stats["hints_used_count"], int)
        assert isinstance(stats["used_text_hints_count"], int)
        assert isinstance(stats["used_video_hints_count"], int)
        print(f"[PASS] by-course: hints_used_count={stats['hints_used_count']}")
        return True


async def test_http_hint_events_409_finished_attempt():
    """HTTP: 409 когда попытка уже завершена (finished_at) — hint-events не принимаются."""
    print("\n=== Тест (HTTP): hint-events 409 — попытка завершена ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    from app.api.main import app
    from app.core.config import Settings
    cfg = Settings()
    if not getattr(cfg, "valid_api_keys", None) or not cfg.valid_api_keys:
        print("[SKIP] Нет VALID_API_KEYS")
        return True

    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    attempts_svc = AttemptsService()

    async with async_session() as session:
        r = await session.execute(select(Tasks.id, Tasks.course_id).limit(1))
        row = r.first()
        if not row:
            print("[SKIP] Нет задач в БД")
            return True
        task_id, course_id = row[0], row[1]
        r = await session.execute(text("SELECT id FROM users LIMIT 1"))
        user_id = r.scalar()
        if not user_id:
            print("[SKIP] Нет пользователей в БД")
            return True
        attempt = await attempts_svc.create_attempt(
            session, user_id=user_id, course_id=course_id,
            source_system="test_http_409_finish", meta={"task_ids": [task_id]},
        )
        await session.commit()
        attempt_id = attempt.id

    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        finish_resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/finish?api_key={api_key}",
        )
        if finish_resp.status_code not in (200, 201):
            print(f"[SKIP] finish вернул {finish_resp.status_code}, нужна активная попытка")
            return True
        resp = await client.post(
            f"/api/v1/learning/tasks/{task_id}/hint-events?api_key={api_key}",
            json={
                "student_id": user_id,
                "attempt_id": attempt_id,
                "hint_type": "text",
                "hint_index": 0,
                "action": "open",
                "source": "test_409",
            },
        )
        if resp.status_code != 409:
            print(f"[FAIL] Ожидался 409 (попытка завершена), получен {resp.status_code} {resp.text}")
            return False
    print("[PASS] HTTP 409 при hint-events для завершённой попытки")
    return True


async def test_http_hint_events_409_cancelled_attempt():
    """HTTP: 409 когда попытка отменена (cancelled_at) — hint-events не принимаются."""
    print("\n=== Тест (HTTP): hint-events 409 — попытка отменена ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    from app.api.main import app
    from app.core.config import Settings
    cfg = Settings()
    if not getattr(cfg, "valid_api_keys", None) or not cfg.valid_api_keys:
        print("[SKIP] Нет VALID_API_KEYS")
        return True

    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    attempts_svc = AttemptsService()

    async with async_session() as session:
        r = await session.execute(select(Tasks.id, Tasks.course_id).limit(1))
        row = r.first()
        if not row:
            print("[SKIP] Нет задач в БД")
            return True
        task_id, course_id = row[0], row[1]
        r = await session.execute(text("SELECT id FROM users LIMIT 1"))
        user_id = r.scalar()
        if not user_id:
            print("[SKIP] Нет пользователей в БД")
            return True
        attempt = await attempts_svc.create_attempt(
            session, user_id=user_id, course_id=course_id,
            source_system="test_http_409_cancel", meta={"task_ids": [task_id]},
        )
        await session.commit()
        attempt_id = attempt.id

    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        cancel_resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/cancel?api_key={api_key}",
        )
        if cancel_resp.status_code not in (200, 201):
            print(f"[SKIP] cancel вернул {cancel_resp.status_code}")
            return True
        resp = await client.post(
            f"/api/v1/learning/tasks/{task_id}/hint-events?api_key={api_key}",
            json={
                "student_id": user_id,
                "attempt_id": attempt_id,
                "hint_type": "text",
                "hint_index": 0,
                "action": "open",
                "source": "test_409",
            },
        )
        if resp.status_code != 409:
            print(f"[FAIL] Ожидался 409 (попытка отменена), получен {resp.status_code} {resp.text}")
            return False
    print("[PASS] HTTP 409 при hint-events для отменённой попытки")
    return True


async def test_http_hint_events_409_wrong_student():
    """HTTP: 409 когда attempt не принадлежит student_id."""
    print("\n=== Тест (HTTP): hint-events 409 — чужой студент ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    from app.api.main import app
    from app.core.config import Settings
    cfg = Settings()
    if not getattr(cfg, "valid_api_keys", None) or not cfg.valid_api_keys:
        print("[SKIP] Нет VALID_API_KEYS")
        return True

    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    attempts_svc = AttemptsService()

    async with async_session() as session:
        r = await session.execute(
            select(Attempts.id, Attempts.user_id, Attempts.course_id).limit(1)
        )
        row = r.first()
        if not row:
            print("[SKIP] Нет попыток в БД")
            return True
        attempt_id, owner_id, course_id = row[0], row[1], row[2]
        r = await session.execute(
            select(Tasks.id).where(Tasks.course_id == course_id).limit(1)
        )
        trow = r.first()
        if not trow:
            print("[SKIP] Нет задачи по курсу попытки")
            return True
        task_id = trow[0]
        r = await session.execute(
            text("SELECT id FROM users WHERE id != :uid LIMIT 1"),
            {"uid": owner_id},
        )
        other_user = r.scalar()
        if not other_user:
            print("[SKIP] Нет другого пользователя для 409")
            return True

    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/learning/tasks/{task_id}/hint-events?api_key={api_key}",
            json={
                "student_id": other_user,
                "attempt_id": attempt_id,
                "hint_type": "text",
                "hint_index": 0,
                "action": "open",
                "source": "test",
            },
        )
        if resp.status_code != 409:
            print(f"[FAIL] Ожидался 409, получен {resp.status_code}")
            return False
        print("[PASS] API вернул 409 при attempt не принадлежит student_id")
        return True


async def test_stats_by_task_has_hint_fields():
    """by-task возвращает hints_used_count, used_text_hints_count, used_video_hints_count."""
    print("\n=== Тест: stats by-task — поля hint ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = TaskResultsService()

    async with async_session() as session:
        r = await session.execute(select(Tasks.id).limit(1))
        task_id = r.scalar()
        if not task_id:
            print("[SKIP] Нет задач в БД")
            return True
        stats = await svc.get_stats_by_task(session, task_id)
        assert "hints_used_count" in stats
        assert "used_text_hints_count" in stats
        assert "used_video_hints_count" in stats
        assert isinstance(stats["hints_used_count"], int)
        assert isinstance(stats["used_text_hints_count"], int)
        assert isinstance(stats["used_video_hints_count"], int)
        print(f"[PASS] by-task: hints_used_count={stats['hints_used_count']}")
        return True


async def test_stats_by_user_has_hint_fields():
    """by-user возвращает три поля hint."""
    print("\n=== Тест: stats by-user — поля hint ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = TaskResultsService()

    async with async_session() as session:
        r = await session.execute(text("SELECT id FROM users LIMIT 1"))
        user_id = r.scalar()
        if not user_id:
            print("[SKIP] Нет пользователей в БД")
            return True
        stats = await svc.get_stats_by_user(session, user_id)
        assert "hints_used_count" in stats
        assert "used_text_hints_count" in stats
        assert "used_video_hints_count" in stats
        assert isinstance(stats["hints_used_count"], int)
        assert isinstance(stats["used_text_hints_count"], int)
        assert isinstance(stats["used_video_hints_count"], int)
        print(f"[PASS] by-user: hints_used_count={stats['hints_used_count']}")
        return True


async def test_stats_regression_existing_fields():
    """Регрессия: существующие поля stats по типам не изменились."""
    print("\n=== Тест: регрессия полей stats ===")
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    svc = TaskResultsService()

    async with async_session() as session:
        r = await session.execute(select(Tasks.id).limit(1))
        task_id = r.scalar()
        r = await session.execute(text("SELECT id FROM users LIMIT 1"))
        user_id = r.scalar()
        if not task_id or not user_id:
            print("[SKIP] Нет task/user в БД")
            return True

        task_stats = await svc.get_stats_by_task(session, task_id)
        assert isinstance(task_stats.get("task_id"), int)
        assert isinstance(task_stats.get("total_attempts"), int)
        assert isinstance(task_stats.get("average_score"), (int, float))
        assert isinstance(task_stats.get("progress_percent"), (int, float))

        user_stats = await svc.get_stats_by_user(session, user_id)
        assert isinstance(user_stats.get("user_id"), int)
        assert isinstance(user_stats.get("total_attempts"), int)
        assert isinstance(user_stats.get("total_score"), (int, type(None)))
        assert isinstance(user_stats.get("last_ratio"), (int, float, type(None)))

        print("[PASS] Типы существующих полей сохранены")
        return True


async def main():
    print("=" * 60)
    print("Тесты Hint events (этап 3.6)")
    print("=" * 60)
    results = [
        await test_record_hint_open_success(),
        await test_record_hint_open_deduplicated(),
        await test_http_hint_events_200_first_then_dedupe(),
        await test_http_hint_events_404(),
        await test_http_hint_events_422(),
        await test_http_hint_events_409_finished_attempt(),
        await test_http_hint_events_409_cancelled_attempt(),
        await test_stats_by_course_has_hint_fields(),
        await test_http_hint_events_409_wrong_student(),
        await test_stats_by_task_has_hint_fields(),
        await test_stats_by_user_has_hint_fields(),
        await test_stats_regression_existing_fields(),
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
