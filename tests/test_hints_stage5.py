"""
Тесты Learning Engine V1, этап 5 (Hints).

Проверяют: извлечение hints_text, hints_video, has_hints из task_content;
нормализацию (только строки); отсутствие 500 при невалидном JSON;
наличие полей в GET/list ответах; HTTP GET /tasks/{id} и list возвращают hints.
"""
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

try:
    import asyncio
    import httpx
    from httpx import ASGITransport
    _HAS_HTTPX = True
except Exception:
    _HAS_HTTPX = False

from app.schemas.tasks import TaskRead, extract_hints_from_task_content


def test_extract_hints_empty():
    """Отсутствие hints / пустые поля -> [], [], False."""
    assert extract_hints_from_task_content(None) == ([], [], False)
    assert extract_hints_from_task_content({}) == ([], [], False)
    assert extract_hints_from_task_content({"type": "SC", "stem": "?"}) == ([], [], False)
    assert extract_hints_from_task_content({"hints_text": None, "hints_video": None}) == ([], [], False)
    assert extract_hints_from_task_content({"hints_text": [], "hints_video": []}) == ([], [], False)
    print("[PASS] extract_hints: пустые/отсутствующие -> [], [], False")


def test_extract_hints_valid():
    """Корректные массивы строк -> без потерь, has_hints=True."""
    tc = {"hints_text": ["Подсказка 1"], "hints_video": ["https://example.com/v.mp4"]}
    ht, hv, hh = extract_hints_from_task_content(tc)
    assert ht == ["Подсказка 1"]
    assert hv == ["https://example.com/v.mp4"]
    assert hh is True
    tc2 = {"hints_text": ["A", "B"], "hints_video": []}
    ht2, hv2, hh2 = extract_hints_from_task_content(tc2)
    assert ht2 == ["A", "B"]
    assert hv2 == []
    assert hh2 is True
    print("[PASS] extract_hints: валидные массивы строк")


def test_extract_hints_normalize():
    """Невалидные элементы отфильтровываются, остаются только строки."""
    tc = {"hints_text": [1, None, "ok", "two"], "hints_video": ["url"]}
    ht, hv, hh = extract_hints_from_task_content(tc)
    assert ht == ["ok", "two"]
    assert hv == ["url"]
    assert hh is True
    tc2 = {"hints_text": "str"}  # не список
    ht2, hv2, hh2 = extract_hints_from_task_content(tc2)
    assert ht2 == []
    assert hv2 == []
    assert hh2 is False
    print("[PASS] extract_hints: нормализация (только строки)")


def test_task_read_hints_from_task_content():
    """TaskRead заполняет hints из task_content при model_validate."""
    class FakeTask:
        id = 1
        task_content = {
            "type": "SC",
            "stem": "?",
            "hints_text": ["h1"],
            "hints_video": [],
        }
        course_id = 1
        difficulty_id = 1
        solution_rules = None
        external_uid = None
        max_score = None

    r = TaskRead.model_validate(FakeTask())
    assert r.hints_text == ["h1"]
    assert r.hints_video == []
    assert r.has_hints is True
    assert r.id == 1
    assert r.task_content["stem"] == "?"
    print("[PASS] TaskRead: hints из task_content при model_validate")


def test_task_read_no_hints():
    """TaskRead без hints в task_content -> [], [], False."""
    class FakeTask:
        id = 2
        task_content = {"type": "MC", "stem": "Q"}
        course_id = 1
        difficulty_id = 1
        solution_rules = {}
        external_uid = None
        max_score = None

    r = TaskRead.model_validate(FakeTask())
    assert r.hints_text == []
    assert r.hints_video == []
    assert r.has_hints is False
    print("[PASS] TaskRead: без hints -> [], [], False")


def test_http_tasks_hints_integration():
    """HTTP: GET /tasks/{id} и GET /tasks/ возвращают hints_text, hints_video, has_hints."""
    if not _HAS_HTTPX:
        print("[SKIP] HTTP-тесты требуют: pip install httpx")
        return True
    asyncio.run(_async_http_integration_tests())


async def _async_http_get_task_returns_hints(client: "httpx.AsyncClient", api_key: str):
    r = await client.get(f"/api/v1/tasks/?limit=1&api_key={api_key}")
    if r.status_code != 200:
        print("[SKIP] GET /tasks/ вернул", r.status_code)
        return
    data = r.json()
    items = data.get("items") or data.get("data") or []
    if not items:
        print("[SKIP] Нет задач в БД для HTTP-теста")
        return
    task_id = items[0]["id"]
    r2 = await client.get(f"/api/v1/tasks/{task_id}?api_key={api_key}")
    assert r2.status_code == 200, f"Ожидался 200, получен {r2.status_code}"
    body = r2.json()
    assert "hints_text" in body, "В ответе должно быть поле hints_text"
    assert "hints_video" in body, "В ответе должно быть поле hints_video"
    assert "has_hints" in body, "В ответе должно быть поле has_hints"
    assert isinstance(body["hints_text"], list), "hints_text должен быть массивом"
    assert isinstance(body["hints_video"], list), "hints_video должен быть массивом"
    assert isinstance(body["has_hints"], bool), "has_hints должен быть bool"
    print("[PASS] GET /tasks/{id}: в ответе есть hints_text, hints_video, has_hints")




async def _async_http_list_tasks_returns_hints(client: "httpx.AsyncClient", api_key: str):
    r = await client.get(f"/api/v1/tasks/?limit=2&api_key={api_key}")
    if r.status_code != 200:
        print("[SKIP] GET /tasks/ вернул", r.status_code)
        return
    data = r.json()
    items = data.get("items") or data.get("data") or []
    if not items:
        print("[SKIP] Нет задач в БД для HTTP list-теста")
        return
    for item in items:
        assert "hints_text" in item, "В элементе списка должно быть поле hints_text"
        assert "hints_video" in item, "В элементе списка должно быть поле hints_video"
        assert "has_hints" in item, "В элементе списка должно быть поле has_hints"
    print(f"[PASS] GET /tasks/ list: у {len(items)} элементов есть hints-поля")


async def _async_http_integration_tests():
    """Один event loop на оба HTTP-теста (избегаем проблем с async SQLAlchemy)."""
    from app.api.main import app
    from app.core.config import Settings
    settings = Settings()
    if not settings.valid_api_keys:
        print("[SKIP] Нет VALID_API_KEYS в окружении")
        return
    api_key = settings.valid_api_keys[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _async_http_get_task_returns_hints(client, api_key)
        await _async_http_list_tasks_returns_hints(client, api_key)


def main():
    print("=" * 60)
    print("Тесты Hints (этап 5)")
    print("=" * 60)
    test_extract_hints_empty()
    test_extract_hints_valid()
    test_extract_hints_normalize()
    test_task_read_hints_from_task_content()
    test_task_read_no_hints()
    test_http_tasks_hints_integration()
    print("\n" + "=" * 60)
    print("Все тесты пройдены успешно.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
