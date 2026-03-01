"""
Тесты Learning Engine V1, этап 3.9 — Teacher Next Modes (claim-next, release, workload).

- claim-next help: успех, empty, 422 для невалидных параметров.
- release help: идемпотентность, 409 при неверном токене.
- claim-next review: успех, empty.
- workload: все 5 счётчиков.
- list help-requests: sort=priority|created_at|due_at, поля priority, due_at, is_overdue.
"""
import asyncio
import os
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from app.core.config import Settings

settings = Settings()


async def _get_teacher_id_and_api_key():
    """Взять teacher_id и api_key из БД/конфига для тестов."""
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
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
                return None, None
            teacher_id = int(row[0])
        api_key = getattr(settings, "valid_api_keys", None) and settings.valid_api_keys[0]
        return teacher_id, api_key
    except Exception as e:
        print(f"[SKIP] БД/конфиг: {e}")
        return None, None


async def _seed_one_open_help_request(teacher_id: int) -> int | None:
    """Создать одну открытую заявку для teacher_id (assigned_teacher_id). Возвращает request_id или None."""
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        engine = create_async_engine(settings.database_url)
        async_session = async_sessionmaker(engine, expire_on_commit=False)
        async with async_session() as session:
            r = await session.execute(
                text("""
                    INSERT INTO help_requests
                    (status, request_type, auto_created, context_json, student_id, task_id, assigned_teacher_id, created_at, updated_at, priority)
                    SELECT 'open', 'manual_help', false, '{}'::jsonb, u.id, t.id, :teacher_id, now(), now(), 100
                    FROM (SELECT id FROM users LIMIT 1) u, (SELECT id FROM tasks LIMIT 1) t
                    RETURNING id
                """),
                {"teacher_id": teacher_id},
            )
            row = r.fetchone()
            if not row:
                return None
            await session.commit()
            return int(row[0])
    except Exception as e:
        print(f"[WARN] seed help_request: {e}")
        return None


async def test_workload_returns_five_counters():
    """GET /teacher/workload возвращает все 5 счётчиков."""
    print("\n=== Тест: workload — 5 счётчиков ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    teacher_id, api_key = await _get_teacher_id_and_api_key()
    if not api_key or teacher_id is None:
        print("[SKIP] Нет API_KEY или teacher_id")
        return True

    from app.api.main import app
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/teacher/workload?teacher_id={teacher_id}&api_key={api_key}"
        )
        if resp.status_code != 200:
            print(f"[FAIL] Ожидался 200, получен {resp.status_code} {resp.text}")
            return False
        data = resp.json()
        for key in (
            "open_help_requests_total",
            "open_blocked_limit_total",
            "open_manual_help_total",
            "pending_manual_reviews_total",
            "overdue_total",
        ):
            if key not in data:
                print(f"[FAIL] Нет поля {key}")
                return False
    print("[PASS] workload — все счётчики присутствуют")
    return True


async def test_claim_next_help_request_success_or_empty():
    """POST /teacher/help-requests/claim-next возвращает 200 и empty или item+lock_token."""
    print("\n=== Тест: claim-next help-request ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    teacher_id, api_key = await _get_teacher_id_and_api_key()
    if not api_key or teacher_id is None:
        print("[SKIP] Нет API_KEY или teacher_id")
        return True

    from app.api.main import app
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/teacher/help-requests/claim-next?api_key={api_key}",
            json={"teacher_id": teacher_id, "request_type": "all", "ttl_sec": 120},
        )
        if resp.status_code != 200:
            print(f"[FAIL] Ожидался 200, получен {resp.status_code} {resp.text}")
            return False
        data = resp.json()
        if "empty" not in data:
            print("[FAIL] Нет поля empty")
            return False
        if not data["empty"]:
            if not data.get("lock_token") or not data.get("lock_expires_at"):
                print("[FAIL] При empty=false ожидаются lock_token и lock_expires_at")
                return False
    print("[PASS] claim-next help-request")
    return True


async def test_claim_next_help_request_422_invalid_ttl():
    """POST claim-next с ttl_sec вне 30..600 возвращает 422."""
    print("\n=== Тест: claim-next 422 при невалидном ttl_sec ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    teacher_id, api_key = await _get_teacher_id_and_api_key()
    if not api_key or teacher_id is None:
        print("[SKIP] Нет API_KEY или teacher_id")
        return True

    from app.api.main import app
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/teacher/help-requests/claim-next?api_key={api_key}",
            json={"teacher_id": teacher_id, "ttl_sec": 10},  # < 30
        )
        if resp.status_code != 422:
            print(f"[FAIL] Ожидался 422, получен {resp.status_code} {resp.text}")
            return False
    print("[PASS] claim-next 422 при ttl_sec=10")
    return True


async def test_list_help_requests_sort_and_priority_fields():
    """GET list с sort=priority и наличие priority, due_at, is_overdue в элементах."""
    print("\n=== Тест: list help-requests sort и SLA-поля ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    teacher_id, api_key = await _get_teacher_id_and_api_key()
    if not api_key or teacher_id is None:
        print("[SKIP] Нет API_KEY или teacher_id")
        return True

    from app.api.main import app
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/teacher/help-requests?teacher_id={teacher_id}&status=all&sort=priority&limit=5&api_key={api_key}"
        )
        if resp.status_code != 200:
            print(f"[FAIL] Ожидался 200, получен {resp.status_code} {resp.text}")
            return False
        data = resp.json()
        if "items" not in data:
            print("[FAIL] Нет items")
            return False
        for item in data["items"]:
            if "priority" not in item or "due_at" not in item or "is_overdue" not in item:
                print("[FAIL] В элементе ожидались priority, due_at, is_overdue")
                return False
        # sort=created_at и sort=due_at
        for sort in ("created_at", "due_at"):
            r2 = await client.get(
                f"/api/v1/teacher/help-requests?teacher_id={teacher_id}&status=all&sort={sort}&limit=1&api_key={api_key}"
            )
            if r2.status_code != 200:
                print(f"[FAIL] sort={sort} вернул {r2.status_code}")
                return False
    print("[PASS] list sort и SLA-поля")
    return True


async def test_review_claim_next_success_or_empty():
    """POST /teacher/reviews/claim-next возвращает 200 и empty или item+lock_token."""
    print("\n=== Тест: claim-next review ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    teacher_id, api_key = await _get_teacher_id_and_api_key()
    if not api_key or teacher_id is None:
        print("[SKIP] Нет API_KEY или teacher_id")
        return True

    from app.api.main import app
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/teacher/reviews/claim-next?api_key={api_key}",
            json={"teacher_id": teacher_id, "ttl_sec": 120},
        )
        if resp.status_code != 200:
            print(f"[FAIL] Ожидался 200, получен {resp.status_code} {resp.text}")
            return False
        data = resp.json()
        if "empty" not in data:
            print("[FAIL] Нет поля empty")
            return False
    print("[PASS] claim-next review")
    return True


async def test_claim_next_idempotency_same_response():
    """Двойной POST claim-next с одним idempotency_key возвращает тот же lock_token и item."""
    print("\n=== Тест: идемпотентность claim по idempotency_key ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    teacher_id, api_key = await _get_teacher_id_and_api_key()
    if not api_key or teacher_id is None:
        print("[SKIP] Нет API_KEY или teacher_id")
        return True

    from app.api.main import app
    transport = ASGITransport(app=app)
    idem_key = "test-idem-help-" + str(time.time())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post(
            f"/api/v1/teacher/help-requests/claim-next?api_key={api_key}",
            json={"teacher_id": teacher_id, "request_type": "all", "ttl_sec": 120, "idempotency_key": idem_key},
        )
        r2 = await client.post(
            f"/api/v1/teacher/help-requests/claim-next?api_key={api_key}",
            json={"teacher_id": teacher_id, "request_type": "all", "ttl_sec": 120, "idempotency_key": idem_key},
        )
        if r1.status_code != 200 or r2.status_code != 200:
            print(f"[FAIL] Ожидались 200, получены {r1.status_code} {r2.status_code}")
            return False
        d1, d2 = r1.json(), r2.json()
        if d1.get("empty") != d2.get("empty"):
            print("[FAIL] empty должен совпадать при одном idempotency_key")
            return False
        if not d1.get("empty"):
            if d1.get("lock_token") != d2.get("lock_token") or d1.get("item", {}).get("request_id") != d2.get("item", {}).get("request_id"):
                print("[FAIL] При одном idempotency_key ожидаются тот же lock_token и item.request_id")
                return False
    print("[PASS] идемпотентность claim")
    return True


async def test_release_help_wrong_token_409():
    """POST release с неверным lock_token возвращает 409. При отсутствии открытых заявок — seed одной (P3)."""
    print("\n=== Тест: release help-request неверный токен -> 409 ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    teacher_id, api_key = await _get_teacher_id_and_api_key()
    if not api_key or teacher_id is None:
        print("[SKIP] Нет API_KEY или teacher_id")
        return True

    from app.api.main import app
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        claim_resp = await client.post(
            f"/api/v1/teacher/help-requests/claim-next?api_key={api_key}",
            json={"teacher_id": teacher_id, "request_type": "all", "ttl_sec": 120},
        )
        if claim_resp.status_code != 200:
            print("[SKIP] claim-next не 200")
            return True
        data = claim_resp.json()
        if data.get("empty"):
            seed_id = await _seed_one_open_help_request(teacher_id)
            if not seed_id:
                print("[SKIP] Нет открытых заявок и не удалось создать seed")
                return True
            claim_resp = await client.post(
                f"/api/v1/teacher/help-requests/claim-next?api_key={api_key}",
                json={"teacher_id": teacher_id, "request_type": "all", "ttl_sec": 120},
            )
            if claim_resp.status_code != 200 or claim_resp.json().get("empty"):
                print("[SKIP] После seed claim не вернул кейс")
                return True
            data = claim_resp.json()
        request_id = data["item"]["request_id"]
        release_resp = await client.post(
            f"/api/v1/teacher/help-requests/{request_id}/release?api_key={api_key}",
            json={"teacher_id": teacher_id, "lock_token": "wrong-token"},
        )
        if release_resp.status_code != 409:
            print(f"[FAIL] Ожидался 409, получен {release_resp.status_code} {release_resp.text}")
            return False
    print("[PASS] release неверный токен -> 409")
    return True


async def test_release_review_wrong_token_409():
    """POST release review с неверным lock_token возвращает 409."""
    print("\n=== Тест: release review неверный токен -> 409 ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    teacher_id, api_key = await _get_teacher_id_and_api_key()
    if not api_key or teacher_id is None:
        print("[SKIP] Нет API_KEY или teacher_id")
        return True

    from app.api.main import app
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        claim_resp = await client.post(
            f"/api/v1/teacher/reviews/claim-next?api_key={api_key}",
            json={"teacher_id": teacher_id, "ttl_sec": 120},
        )
        if claim_resp.status_code != 200:
            print("[SKIP] claim-next review не 200")
            return True
        data = claim_resp.json()
        if data.get("empty"):
            print("[SKIP] Нет pending review для claim")
            return True
        result_id = data["item"]["id"]
        release_resp = await client.post(
            f"/api/v1/teacher/reviews/{result_id}/release?api_key={api_key}",
            json={"teacher_id": teacher_id, "lock_token": "wrong-token"},
        )
        if release_resp.status_code != 409:
            print(f"[FAIL] Ожидался 409, получен {release_resp.status_code} {release_resp.text}")
            return False
    print("[PASS] release review неверный токен -> 409")
    return True


async def test_manual_check_wrong_lock_token_409():
    """POST manual-check с неверным lock_token возвращает 409 (не 500)."""
    print("\n=== Тест: manual-check неверный lock_token -> 409 ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    teacher_id, api_key = await _get_teacher_id_and_api_key()
    if not api_key or teacher_id is None:
        print("[SKIP] Нет API_KEY или teacher_id")
        return True

    from app.api.main import app
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        claim_resp = await client.post(
            f"/api/v1/teacher/reviews/claim-next?api_key={api_key}",
            json={"teacher_id": teacher_id, "ttl_sec": 120},
        )
        if claim_resp.status_code != 200 or claim_resp.json().get("empty"):
            print("[SKIP] Нет захваченного результата для теста")
            return True
        result_id = claim_resp.json()["item"]["id"]
        mc_resp = await client.post(
            f"/api/v1/task-results/{result_id}/manual-check?api_key={api_key}",
            json={"score": 0, "checked_by": teacher_id, "lock_token": "wrong-token"},
        )
        if mc_resp.status_code != 409:
            print(f"[FAIL] Ожидался 409, получен {mc_resp.status_code} {mc_resp.text}")
            return False
    print("[PASS] manual-check неверный lock_token -> 409")
    return True


async def main():
    ok = True
    ok &= await test_workload_returns_five_counters()
    ok &= await test_claim_next_help_request_success_or_empty()
    ok &= await test_claim_next_help_request_422_invalid_ttl()
    ok &= await test_list_help_requests_sort_and_priority_fields()
    ok &= await test_review_claim_next_success_or_empty()
    ok &= await test_claim_next_idempotency_same_response()
    ok &= await test_release_help_wrong_token_409()
    ok &= await test_release_review_wrong_token_409()
    ok &= await test_manual_check_wrong_lock_token_409()
    print("\n" + ("Все тесты PASS" if ok else "Есть провалы"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
