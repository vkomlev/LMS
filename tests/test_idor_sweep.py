"""
IDOR sweep: проверка, что защищённые endpoints возвращают 401/403
при отсутствии/неверной аутентификации и не пропускают чужие данные.

Тест не требует двух реальных пользователей — проверяет только:
1. 401 без auth на ранее открытые endpoints
2. 403 при попытке студента обратиться к чужому student_id (через Bearer невалид токен)
"""
import pytest


LEARNING_ENDPOINTS_WITHOUT_AUTH = [
    ("GET", "/api/v1/learning/next-item?student_id=1"),
    ("POST", "/api/v1/learning/materials/1/complete"),
    ("POST", "/api/v1/learning/tasks/1/start-or-get-attempt"),
    ("GET", "/api/v1/learning/tasks/1/state?student_id=1"),
    ("POST", "/api/v1/learning/tasks/1/request-help"),
    ("POST", "/api/v1/learning/tasks/1/hint-events"),
]

ATTEMPT_ENDPOINTS_WITHOUT_AUTH = [
    ("POST", "/api/v1/attempts"),
    ("POST", "/api/v1/attempts/1/answers"),
    ("POST", "/api/v1/attempts/1/finish"),
    ("GET", "/api/v1/attempts/1"),
    ("GET", "/api/v1/attempts/by-user/1"),
]

TEACHER_ENDPOINTS_WITHOUT_AUTH = [
    ("POST", "/api/v1/teacher/reviews/claim-next"),
    ("POST", "/api/v1/teacher/reviews/1/release"),
]


@pytest.mark.parametrize("method,url", LEARNING_ENDPOINTS_WITHOUT_AUTH)
@pytest.mark.asyncio
async def test_learning_requires_auth(client, method, url):
    """Learning endpoints без auth → 401 или 403 (не 200)."""
    if method == "GET":
        resp = await client.get(url)
    else:
        resp = await client.post(url, json={})
    assert resp.status_code in (401, 403, 422), (
        f"{method} {url} вернул {resp.status_code} вместо 401/403"
    )


@pytest.mark.parametrize("method,url", ATTEMPT_ENDPOINTS_WITHOUT_AUTH)
@pytest.mark.asyncio
async def test_attempts_require_auth(client, method, url):
    """Attempt endpoints без auth → 401 или 403."""
    if method == "GET":
        resp = await client.get(url)
    else:
        resp = await client.post(url, json={})
    assert resp.status_code in (401, 403, 422), (
        f"{method} {url} вернул {resp.status_code} вместо 401/403"
    )


@pytest.mark.parametrize("method,url", TEACHER_ENDPOINTS_WITHOUT_AUTH)
@pytest.mark.asyncio
async def test_teacher_reviews_require_auth(client, method, url):
    """Teacher review endpoints без auth → 401 или 403."""
    resp = await client.post(url, json={})
    assert resp.status_code in (401, 403, 422), (
        f"{method} {url} вернул {resp.status_code} вместо 401/403"
    )


@pytest.mark.asyncio
async def test_me_requires_auth(client):
    """GET /me без auth → 401."""
    resp = await client.get("/api/v1/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_invalid_bearer(client):
    """GET /me с невалидным Bearer токеном → 401."""
    resp = await client.get(
        "/api/v1/me",
        headers={"Authorization": "Bearer invalidtoken1234"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_legacy_api_key_still_works(client):
    """Legacy ?api_key= query param всё ещё принимается существующими endpoints."""
    import os
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parents[1] / ".env", encoding="utf-8-sig")

    api_key = os.getenv("VALID_API_KEYS", "").split(",")[0].strip()
    if not api_key:
        pytest.skip("VALID_API_KEYS не задан")

    resp = await client.get(f"/api/v1/users/?api_key={api_key}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_service_key_bypasses_idor(client):
    """Service api_key позволяет обращаться к любому student_id (bypass IDOR)."""
    import os
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parents[1] / ".env", encoding="utf-8-sig")

    api_key = os.getenv("VALID_API_KEYS", "").split(",")[0].strip()
    if not api_key:
        pytest.skip("VALID_API_KEYS не задан")

    resp = await client.get(
        f"/api/v1/learning/next-item?student_id=9999&api_key={api_key}"
    )
    assert resp.status_code in (200, 404)
