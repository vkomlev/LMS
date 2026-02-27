"""
Проверка регистрации маршрутов Learning API (этап 3).

Без поднятия БД: проверяем, что в OpenAPI есть нужные пути.
Требуется .env с DATABASE_URL (при импорте app поднимается конфиг).
"""
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")


def test_learning_api_routes_registered():
    """Маршруты Learning API и Teacher Learning зарегистрированы в приложении."""
    from app.api.main import app
    openapi = app.openapi()
    paths = list(openapi.get("paths", {}).keys())
    expected = [
        "/api/v1/learning/next-item",
        "/api/v1/learning/materials/{material_id}/complete",
        "/api/v1/learning/tasks/{task_id}/start-or-get-attempt",
        "/api/v1/learning/tasks/{task_id}/state",
        "/api/v1/learning/tasks/{task_id}/request-help",
        "/api/v1/teacher/task-limits/override",
        "/api/v1/teacher/help-requests",
        "/api/v1/teacher/help-requests/{request_id}",
        "/api/v1/teacher/help-requests/{request_id}/close",
        "/api/v1/teacher/help-requests/{request_id}/reply",
    ]
    for p in expected:
        assert p in paths, f"Ожидался путь {p} в OpenAPI. Найдены: {paths}"


if __name__ == "__main__":
    test_learning_api_routes_registered()
    print("OK: все маршруты Learning API зарегистрированы")
