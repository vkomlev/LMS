"""
Тесты Learning Engine V1, этап 3.8.1 — типизация заявок и auto-create при BLOCKED_LIMIT.

- Фильтр request_type: manual_help | blocked_limit | all; невалидный -> 422.
- В list и detail есть request_type, auto_created, context.
- Без request_type (или all) — обратная совместимость.
- Auto-create: get_or_create_blocked_limit_help_request создаёт open заявку blocked_limit.
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
from app.services.help_requests_service import get_or_create_blocked_limit_help_request

settings = Settings()


async def test_invalid_request_type_422():
    """Невалидный request_type в query возвращает 422."""
    print("\n=== Тест: невалидный request_type -> 422 ===")
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
            text("SELECT teacher_id FROM student_teacher_links LIMIT 1")
        )
        row = r.fetchone()
        if not row:
            r = await session.execute(text("SELECT id FROM users LIMIT 1"))
            row = r.fetchone()
        if not row:
            print("[SKIP] Нет пользователей в БД")
            return True
        teacher_id = row[0]

    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/teacher/help-requests?teacher_id={teacher_id}&status=all&request_type=invalid&api_key={api_key}",
        )
        if resp.status_code != 422:
            print(f"[FAIL] Ожидался 422, получен {resp.status_code} {resp.text}")
            return False
    print("[PASS] request_type=invalid -> 422")
    return True


async def test_list_items_have_request_type_auto_created_context():
    """Элементы списка содержат request_type, auto_created, context."""
    print("\n=== Тест: list содержит request_type, auto_created, context ===")
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
                SELECT hr.id FROM help_requests hr
                JOIN student_teacher_links stl ON stl.student_id = hr.student_id
                LIMIT 1
            """)
        )
        row = r.fetchone()
        if not row:
            r = await session.execute(
                text("""
                    SELECT hr.id FROM help_requests hr
                    JOIN teacher_courses tc ON tc.course_id = hr.course_id
                    LIMIT 1
                """)
            )
            row = r.fetchone()
        if not row:
            print("[SKIP] Нет заявки с привязкой teacher")
            return True
        r = await session.execute(
            text("SELECT teacher_id FROM student_teacher_links WHERE student_id = (SELECT student_id FROM help_requests WHERE id = :id) LIMIT 1"),
            {"id": row[0]},
        )
        trow = r.fetchone()
        if not trow:
            r = await session.execute(
                text("SELECT teacher_id FROM teacher_courses WHERE course_id = (SELECT course_id FROM help_requests WHERE id = :id) LIMIT 1"),
                {"id": row[0]},
            )
            trow = r.fetchone()
        if not trow:
            print("[SKIP] Нет teacher для заявки")
            return True
        teacher_id = trow[0]

    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/teacher/help-requests?teacher_id={teacher_id}&status=all&limit=5&api_key={api_key}",
        )
        if resp.status_code != 200:
            print(f"[FAIL] GET list: {resp.status_code} {resp.text}")
            return False
        data = resp.json()
        items = data.get("items", [])
        if not items:
            print("[SKIP] Нет заявок в списке")
            return True
        for it in items:
            if "request_type" not in it:
                print(f"[FAIL] Нет request_type в элементе: {it}")
                return False
            if "auto_created" not in it:
                print(f"[FAIL] Нет auto_created в элементе: {it}")
                return False
            if "context" not in it:
                print(f"[FAIL] Нет context в элементе: {it}")
                return False
            if it["request_type"] not in ("manual_help", "blocked_limit"):
                print(f"[FAIL] Недопустимый request_type: {it['request_type']}")
                return False
    print("[PASS] В списке есть request_type, auto_created, context")
    return True


async def test_detail_has_request_type_auto_created_context():
    """Карточка заявки содержит request_type, auto_created, context."""
    print("\n=== Тест: detail содержит request_type, auto_created, context ===")
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
                SELECT hr.id FROM help_requests hr
                JOIN student_teacher_links stl ON stl.student_id = hr.student_id
                LIMIT 1
            """)
        )
        row = r.fetchone()
        if not row:
            r = await session.execute(
                text("""
                    SELECT hr.id FROM help_requests hr
                    JOIN teacher_courses tc ON tc.course_id = hr.course_id
                    LIMIT 1
                """)
            )
            row = r.fetchone()
        if not row:
            print("[SKIP] Нет заявки с привязкой teacher")
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

    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/teacher/help-requests/{request_id}?teacher_id={teacher_id}&api_key={api_key}",
        )
        if resp.status_code != 200:
            print(f"[FAIL] GET detail: {resp.status_code} {resp.text}")
            return False
        data = resp.json()
        if "request_type" not in data:
            print(f"[FAIL] Нет request_type в detail: {list(data.keys())}")
            return False
        if "auto_created" not in data:
            print(f"[FAIL] Нет auto_created в detail")
            return False
        if "context" not in data:
            print(f"[FAIL] Нет context в detail")
            return False
        if data["request_type"] not in ("manual_help", "blocked_limit"):
            print(f"[FAIL] Недопустимый request_type в detail: {data['request_type']}")
            return False
    print("[PASS] В detail есть request_type, auto_created, context")
    return True


async def test_filter_request_type_and_auto_create_blocked_limit():
    """Создание blocked_limit через сервис; фильтр request_type=blocked_limit возвращает только её."""
    print("\n=== Тест: blocked_limit auto-create и фильтр request_type ===")
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
                SELECT t.id, t.course_id FROM tasks t
                WHERE EXISTS (SELECT 1 FROM users u LIMIT 1)
                AND EXISTS (SELECT 1 FROM student_teacher_links stl LIMIT 1)
                LIMIT 1
            """)
        )
        row = r.fetchone()
        if not row:
            print("[SKIP] Нет задач или связей в БД")
            return True
        task_id, course_id = row[0], row[1]
        r = await session.execute(
            text("SELECT student_id, teacher_id FROM student_teacher_links WHERE student_id IN (SELECT id FROM users) LIMIT 1")
        )
        link = r.fetchone()
        if not link:
            print("[SKIP] Нет student_teacher_links")
            return True
        student_id, teacher_id = link[0], link[1]

    async with async_session() as session:
        request_id, created, dedup = await get_or_create_blocked_limit_help_request(
            session,
            student_id=student_id,
            task_id=task_id,
            course_id=course_id,
            attempts_used=3,
            attempts_limit_effective=3,
            last_based_status="BLOCKED_LIMIT",
        )
        await session.commit()

    if not request_id:
        print("[SKIP] Нет request_id (нет данных для blocked_limit)")
        return True

    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/teacher/help-requests?teacher_id={teacher_id}&status=open&request_type=blocked_limit&limit=50&api_key={api_key}",
        )
        if resp.status_code != 200:
            print(f"[FAIL] GET request_type=blocked_limit: {resp.status_code} {resp.text}")
            return False
        data = resp.json()
        items = data.get("items", [])
        blocked = [it for it in items if it.get("request_type") == "blocked_limit"]
        if request_id not in [it["request_id"] for it in blocked]:
            print(f"[FAIL] Заявка {request_id} не в request_type=blocked_limit: {[it['request_id'] for it in items]}")
            return False
        for it in items:
            if it.get("request_type") != "blocked_limit":
                print(f"[FAIL] В request_type=blocked_limit попала заявка с типом {it.get('request_type')}")
                return False
        one = next((it for it in items if it["request_id"] == request_id), None)
        if one is None:
            print(f"[FAIL] Заявка {request_id} не найдена в items")
            return False
        if not one.get("auto_created"):
            print(f"[FAIL] Ожидался auto_created=true: {one}")
            return False
        if "context" not in one:
            print(f"[FAIL] Нет context в элементе: {one}")
            return False
        ctx = one.get("context")
        if not isinstance(ctx, dict):
            print(f"[FAIL] context должен быть dict: {type(ctx)}")
            return False
        if ctx.get("attempts_used") != 3 or ctx.get("attempts_limit_effective") != 3:
            print(f"[FAIL] context должен содержать attempts_used=3, attempts_limit_effective=3: {ctx}")
            return False

    print(f"[PASS] blocked_limit заявка {request_id} (created={created}), фильтр и context проверены")
    return True


async def test_request_type_all_backward_compat():
    """Без request_type или request_type=all — список как раньше (все типы)."""
    print("\n=== Тест: request_type=all / без параметра — обратная совместимость ===")
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
            text("SELECT teacher_id FROM student_teacher_links LIMIT 1")
        )
        row = r.fetchone()
        if not row:
            r = await session.execute(text("SELECT id FROM users LIMIT 1"))
            row = r.fetchone()
        if not row:
            print("[SKIP] Нет пользователей в БД")
            return True
        teacher_id = row[0]

    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r_all = await client.get(
            f"/api/v1/teacher/help-requests?teacher_id={teacher_id}&status=all&request_type=all&limit=10&api_key={api_key}",
        )
        r_no_param = await client.get(
            f"/api/v1/teacher/help-requests?teacher_id={teacher_id}&status=all&limit=10&api_key={api_key}",
        )
        if r_all.status_code != 200 or r_no_param.status_code != 200:
            print(f"[FAIL] GET: all={r_all.status_code} no_param={r_no_param.status_code}")
            return False
        if r_no_param.json().get("total") != r_all.json().get("total"):
            print(f"[FAIL] total без request_type и с request_type=all должны совпадать")
            return False
    print("[PASS] request_type=all и без параметра ведут себя одинаково")
    return True


async def main():
    print("=" * 60)
    print("Тесты Teacher help-requests (этап 3.8.1)")
    print("=" * 60)
    results = [
        await test_invalid_request_type_422(),
        await test_list_items_have_request_type_auto_created_context(),
        await test_detail_has_request_type_auto_created_context(),
        await test_filter_request_type_and_auto_create_blocked_limit(),
        await test_request_type_all_backward_compat(),
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
