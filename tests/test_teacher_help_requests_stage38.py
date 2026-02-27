"""
Тесты Learning Engine V1, этап 3.8 — Teacher help-requests.

- request-help создаёт help_requests и возвращает request_id
- GET /teacher/help-requests?status=open только open
- ACL: чужой teacher 403
- close: первый раз already_closed=false, повтор already_closed=true
- reply: создаёт messages, thread_id; idempotency_key -> deduplicated
- close_after_reply закрывает заявку
- Закрытая заявка не в status=open
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
from app.models.tasks import Tasks
from app.services.help_requests_service import get_or_create_help_request
from app.services.student_teacher_links_service import StudentTeacherLinksService

settings = Settings()


async def test_request_help_creates_help_request():
    """request-help создаёт запись в help_requests и возвращает request_id."""
    print("\n=== Тест: request-help создаёт help_requests + request_id ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    from app.api.main import app
    cfg = Settings()
    if not getattr(cfg, "valid_api_keys", None) or not cfg.valid_api_keys:
        print("[SKIP] Нет VALID_API_KEYS")
        return True

    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

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

    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/learning/tasks/{task_id}/request-help?api_key={api_key}",
            json={"student_id": user_id, "message": "Помогите с задачей"},
        )
        if resp.status_code != 200:
            print(f"[FAIL] request-help: {resp.status_code} {resp.text}")
            return False
        data = resp.json()
        if "request_id" not in data:
            print(f"[FAIL] В ответе нет request_id: {data}")
            return False
        request_id = data["request_id"]
        if not isinstance(request_id, int) or request_id < 1:
            print(f"[FAIL] request_id неверный: {request_id}")
            return False

    async with async_session() as session:
        r = await session.execute(
            text("SELECT id, status, student_id, task_id, event_id FROM help_requests WHERE id = :id"),
            {"id": request_id},
        )
        hr = r.fetchone()
        if not hr:
            print("[FAIL] Запись в help_requests не найдена")
            return False
        event_id = hr[4]
        if event_id is None:
            print("[FAIL] У заявки не заполнен event_id")
            return False
        r = await session.execute(
            text("SELECT event_type FROM learning_events WHERE id = :eid"),
            {"eid": event_id},
        )
        row = r.fetchone()
        if not row or row[0] != "help_requested":
            print(f"[FAIL] Нет события help_requested в learning_events для event_id={event_id}")
            return False

    print(f"[PASS] request_id={request_id}, help_requests и learning_events созданы")
    return True


async def test_get_help_requests_status_open():
    """GET ?status=open возвращает только открытые заявки."""
    print("\n=== Тест: GET help-requests status=open ===")
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
        r = await session.execute(
            text("""
                SELECT hr.id FROM help_requests hr
                JOIN student_teacher_links stl ON stl.student_id = hr.student_id
                WHERE hr.status = 'open' LIMIT 1
            """)
        )
        row = r.fetchone()
        if not row:
            r = await session.execute(
                text("""
                    SELECT hr.id FROM help_requests hr
                    JOIN teacher_courses tc ON tc.course_id = hr.course_id
                    WHERE hr.status = 'open' LIMIT 1
                """)
            )
            row = r.fetchone()
        if not row:
            print("[SKIP] Нет открытой заявки с привязкой teacher")
            return True
        request_id = row[0]
        r = await session.execute(
            text("SELECT teacher_id FROM student_teacher_links WHERE student_id = (SELECT student_id FROM help_requests WHERE id = :id) LIMIT 1"),
            {"id": request_id},
        )
        trow = r.fetchone()
        if not trow:
            r = await session.execute(
                text("SELECT teacher_id FROM teacher_courses WHERE course_id = (SELECT course_id FROM help_requests WHERE id = :id) LIMIT 1"),
                {"id": request_id},
            )
            trow = r.fetchone()
        if not trow:
            print("[SKIP] Нет teacher для заявки")
            return True
        teacher_id = trow[0]

    from app.core.config import Settings
    cfg = Settings()
    if not getattr(cfg, "valid_api_keys", None) or not cfg.valid_api_keys:
        print("[SKIP] Нет VALID_API_KEYS")
        return True
    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/teacher/help-requests?teacher_id={teacher_id}&status=open&limit=50&api_key={api_key}",
        )
        if resp.status_code != 200:
            print(f"[FAIL] GET list: {resp.status_code} {resp.text}")
            return False
        data = resp.json()
        items = data.get("items", [])
        ids_open = [it["request_id"] for it in items if it.get("status") == "open"]
        if ids_open and request_id not in ids_open:
            print(f"[FAIL] Ожидалась заявка {request_id} в списке open: {ids_open[:5]}")
            return False
        for it in items:
            if it.get("status") != "open":
                print(f"[FAIL] В status=open попала закрытая заявка: {it}")
                return False
    print("[PASS] GET status=open возвращает только open")
    return True


async def test_acl_foreign_teacher_403():
    """Чужой teacher получает 403 на карточку заявки."""
    print("\n=== Тест: ACL — чужой teacher 403 ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    from app.api.main import app
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        r = await session.execute(text("SELECT id FROM help_requests LIMIT 1"))
        row = r.fetchone()
        if not row:
            print("[SKIP] Нет заявок в БД")
            return True
        request_id = row[0]
        r = await session.execute(
            text("""
                SELECT u.id FROM users u
                WHERE u.id NOT IN (
                    SELECT assigned_teacher_id FROM help_requests WHERE id = :rid
                )
                AND u.id NOT IN (SELECT teacher_id FROM student_teacher_links WHERE student_id = (SELECT student_id FROM help_requests WHERE id = :rid))
                AND u.id NOT IN (SELECT teacher_id FROM teacher_courses WHERE course_id = (SELECT course_id FROM help_requests WHERE id = :rid))
                AND NOT EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = u.id AND r.name = 'methodist')
                LIMIT 1
            """),
            {"rid": request_id},
        )
        other = r.fetchone()
        if not other:
            print("[SKIP] Нет «чужого» пользователя для 403 (критичный сценарий ACL не прогнан)")
            return False
        foreign_teacher_id = other[0]

    from app.core.config import Settings
    cfg = Settings()
    if not getattr(cfg, "valid_api_keys", None) or not cfg.valid_api_keys:
        print("[SKIP] Нет VALID_API_KEYS")
        return True
    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/teacher/help-requests/{request_id}?teacher_id={foreign_teacher_id}&api_key={api_key}",
        )
        if resp.status_code != 403:
            print(f"[FAIL] Ожидался 403, получен {resp.status_code}")
            return False
    print("[PASS] Чужой teacher получил 403")
    return True


async def test_close_idempotent():
    """Первый close -> already_closed=false, второй -> already_closed=true."""
    print("\n=== Тест: close идемпотентен ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    from app.api.main import app
    cfg = Settings()
    if not getattr(cfg, "valid_api_keys", None) or not cfg.valid_api_keys:
        print("[SKIP] Нет VALID_API_KEYS")
        return True

    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        r = await session.execute(
            text("""
                SELECT hr.id, hr.student_id FROM help_requests hr
                WHERE hr.status = 'open'
                AND (EXISTS (SELECT 1 FROM student_teacher_links stl WHERE stl.student_id = hr.student_id)
                     OR EXISTS (SELECT 1 FROM teacher_courses tc WHERE tc.course_id = hr.course_id))
                LIMIT 1
            """)
        )
        row = r.fetchone()
        if not row:
            print("[SKIP] Нет открытой заявки с teacher")
            return True
        request_id, student_id = row[0], row[1]
        r = await session.execute(
            text("SELECT teacher_id FROM student_teacher_links WHERE student_id = :sid LIMIT 1"),
            {"sid": student_id},
        )
        trow = r.fetchone()
        if not trow:
            r = await session.execute(
                text("SELECT teacher_id FROM teacher_courses tc JOIN help_requests hr ON hr.course_id = tc.course_id WHERE hr.id = :id LIMIT 1"),
                {"id": request_id},
            )
            trow = r.fetchone()
        if not trow:
            print("[SKIP] Нет teacher_id для close")
            return True
        closer_id = trow[0]

    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post(
            f"/api/v1/teacher/help-requests/{request_id}/close?api_key={api_key}",
            json={"closed_by": closer_id, "resolution_comment": "Решено"},
        )
        if r1.status_code != 200:
            print(f"[FAIL] Первый close: {r1.status_code} {r1.text}")
            return False
        d1 = r1.json()
        if d1.get("already_closed") is not False:
            print(f"[FAIL] Первый close: ожидался already_closed=false, получен {d1}")
            return False
        r2 = await client.post(
            f"/api/v1/teacher/help-requests/{request_id}/close?api_key={api_key}",
            json={"closed_by": closer_id},
        )
        if r2.status_code != 200:
            print(f"[FAIL] Второй close: {r2.status_code}")
            return False
        d2 = r2.json()
        if d2.get("already_closed") is not True:
            print(f"[FAIL] Второй close: ожидался already_closed=true, получен {d2}")
            return False
    print("[PASS] close идемпотентен: первый already_closed=false, второй true")
    return True


async def test_reply_creates_message_and_dedupe():
    """reply создаёт сообщение и thread_id; повтор с idempotency_key -> deduplicated."""
    print("\n=== Тест: reply создаёт messages, idempotency_key dedupe ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    from app.api.main import app
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        r = await session.execute(
            text("""
                SELECT hr.id, hr.student_id, hr.thread_id FROM help_requests hr
                WHERE hr.status = 'open'
                AND (EXISTS (SELECT 1 FROM student_teacher_links stl WHERE stl.student_id = hr.student_id)
                     OR EXISTS (SELECT 1 FROM teacher_courses tc WHERE tc.course_id = hr.course_id))
                LIMIT 1
            """)
        )
        row = r.fetchone()
        if not row:
            print("[SKIP] Нет открытой заявки для reply (критичный сценарий reply/dedupe не прогнан)")
            return False
        request_id, student_id, thread_before = row[0], row[1], row[2]
        r = await session.execute(
            text("SELECT teacher_id FROM student_teacher_links WHERE student_id = :sid LIMIT 1"),
            {"sid": student_id},
        )
        trow = r.fetchone()
        if not trow:
            r = await session.execute(
                text("SELECT teacher_id FROM teacher_courses tc JOIN help_requests hr ON hr.course_id = tc.course_id WHERE hr.id = :id LIMIT 1"),
                {"id": request_id},
            )
            trow = r.fetchone()
        if not trow:
            print("[SKIP] Нет teacher для reply (критичный сценарий reply/dedupe не прогнан)")
            return False
        teacher_id = trow[0]

    from app.core.config import Settings
    cfg = Settings()
    if not getattr(cfg, "valid_api_keys", None) or not cfg.valid_api_keys:
        print("[SKIP] Нет VALID_API_KEYS")
        return True
    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    key = "test-idem-reply-1"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post(
            f"/api/v1/teacher/help-requests/{request_id}/reply?api_key={api_key}",
            json={
                "teacher_id": teacher_id,
                "message": "Попробуйте перечитать раздел 2.",
                "idempotency_key": key,
            },
        )
        if r1.status_code != 200:
            print(f"[FAIL] Первый reply: {r1.status_code} {r1.text}")
            return False
        d1 = r1.json()
        if d1.get("deduplicated") is not False:
            print(f"[FAIL] Первый reply: ожидался deduplicated=false, получен {d1}")
            return False
        message_id = d1.get("message_id")
        thread_id = d1.get("thread_id")
        if not message_id:
            print(f"[FAIL] Нет message_id в ответе: {d1}")
            return False
        r2 = await client.post(
            f"/api/v1/teacher/help-requests/{request_id}/reply?api_key={api_key}",
            json={
                "teacher_id": teacher_id,
                "message": "Попробуйте перечитать раздел 2.",
                "idempotency_key": key,
            },
        )
        if r2.status_code != 200:
            print(f"[FAIL] Повтор reply: {r2.status_code}")
            return False
        d2 = r2.json()
        if d2.get("deduplicated") is not True:
            print(f"[FAIL] Повтор: ожидался deduplicated=true, получен {d2}")
            return False
        if d2.get("message_id") != message_id:
            print(f"[FAIL] Повтор: ожидался тот же message_id {message_id}, получен {d2.get('message_id')}")
            return False

    async with async_session() as session:
        r = await session.execute(
            text("SELECT id FROM messages WHERE id = :mid"),
            {"mid": message_id},
        )
        if not r.fetchone():
            print("[FAIL] Сообщение не найдено в messages")
            return False
        r = await session.execute(
            text("SELECT thread_id FROM help_requests WHERE id = :id"),
            {"id": request_id},
        )
        row = r.fetchone()
        if row and row[0] is None:
            print("[FAIL] thread_id не записан в help_requests")
            return False

    print("[PASS] reply создаёт messages и thread_id, idempotency_key даёт deduplicated=true")
    return True


async def test_close_after_reply_and_not_in_open():
    """close_after_reply=true закрывает заявку; закрытая не в status=open."""
    print("\n=== Тест: close_after_reply и закрытая не в open ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    from app.api.main import app
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        r = await session.execute(
            text("""
                SELECT hr.id, hr.student_id FROM help_requests hr
                WHERE hr.status = 'open'
                AND (EXISTS (SELECT 1 FROM student_teacher_links stl WHERE stl.student_id = hr.student_id)
                     OR EXISTS (SELECT 1 FROM teacher_courses tc WHERE tc.course_id = hr.course_id))
                LIMIT 1
            """)
        )
        row = r.fetchone()
        if not row:
            print("[SKIP] Нет открытой заявки для close_after_reply (критичный сценарий не прогнан)")
            return False
        request_id, student_id = row[0], row[1]
        r = await session.execute(
            text("SELECT teacher_id FROM student_teacher_links WHERE student_id = :sid LIMIT 1"),
            {"sid": student_id},
        )
        trow = r.fetchone()
        if not trow:
            r = await session.execute(
                text("SELECT teacher_id FROM teacher_courses tc JOIN help_requests hr ON hr.course_id = tc.course_id WHERE hr.id = :id LIMIT 1"),
                {"id": request_id},
            )
            trow = r.fetchone()
        if not trow:
            print("[SKIP] Нет teacher для close_after_reply (критичный сценарий не прогнан)")
            return False
        teacher_id = trow[0]

    from app.core.config import Settings
    cfg = Settings()
    if not getattr(cfg, "valid_api_keys", None) or not cfg.valid_api_keys:
        print("[SKIP] Нет VALID_API_KEYS")
        return True
    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/teacher/help-requests/{request_id}/reply?api_key={api_key}",
            json={
                "teacher_id": teacher_id,
                "message": "Готово, закрываю.",
                "close_after_reply": True,
            },
        )
        if resp.status_code != 200:
            print(f"[FAIL] reply close_after_reply: {resp.status_code} {resp.text}")
            return False
        data = resp.json()
        if data.get("request_status") != "closed":
            print(f"[FAIL] Ожидался request_status=closed: {data}")
            return False
        resp2 = await client.get(
            f"/api/v1/teacher/help-requests?teacher_id={teacher_id}&status=open&limit=100&api_key={api_key}",
        )
        if resp2.status_code != 200:
            print(f"[FAIL] GET open: {resp2.status_code}")
            return False
        ids = [it["request_id"] for it in resp2.json().get("items", [])]
        if request_id in ids:
            print(f"[FAIL] Закрытая заявка {request_id} попала в status=open: {ids[:10]}")
            return False
    print("[PASS] close_after_reply закрыл заявку, её нет в status=open")
    return True


async def main():
    print("=" * 60)
    print("Тесты Teacher help-requests (этап 3.8)")
    print("=" * 60)
    results = [
        await test_request_help_creates_help_request(),
        await test_get_help_requests_status_open(),
        await test_acl_foreign_teacher_403(),
        await test_close_idempotent(),
        await test_reply_creates_message_and_dedupe(),
        await test_close_after_reply_and_not_in_open(),
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
