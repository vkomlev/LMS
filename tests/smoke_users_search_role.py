"""
Smoke tests for updated users endpoints:
- GET /api/v1/users/search (added role filter)
- GET /api/v1/users/ (role filter behavior)

Validates:
- HTTP 200 responses
- Expected IDs present in payload

NOTE: Uses only ASCII in source via \\u escapes to avoid encoding issues in some shells.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE = "http://localhost:8000/api/v1"
API_KEY = "bot-key-1"


def http_get(path: str, params: dict) -> object:
    url = f"{BASE}{path}?{urlencode(params, doseq=True)}"
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=15) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw)


def assert_contains(ids: list[int], expected: list[int], name: str) -> None:
    missing = [e for e in expected if e not in ids]
    if missing:
        raise SystemExit(f"FAIL {name}: missing {missing}, got {ids}")
    print(f"OK {name}: ids={ids}")


def main() -> None:
    # "Преподаватель"
    q_teacher = "\u041f\u0440\u0435\u043f\u043e\u0434\u0430\u0432\u0430\u0442\u0435\u043b\u044c"
    # "Студент"
    q_student = "\u0421\u0442\u0443\u0434\u0435\u043d\u0442"
    # "Комлев"
    q_komlev = "\u041a\u043e\u043c\u043b\u0435\u0432"
    # "Методист"
    role_methodist_ru = "\u041c\u0435\u0442\u043e\u0434\u0438\u0441\u0442"
    # "Преподаватель" (role russian alias)
    role_teacher_ru = q_teacher

    # 1) search without role
    j = http_get("/users/search", {"q": q_teacher, "api_key": API_KEY})
    ids = [row["id"] for row in j]  # type: ignore[index]
    assert_contains(ids, [16, 17], "search_no_role")

    # 2) search role=teacher (case-insensitive + russian alias)
    for role in ["teacher", "TeAcHeR", role_teacher_ru]:
        j = http_get("/users/search", {"q": q_teacher, "role": role, "api_key": API_KEY})
        ids = [row["id"] for row in j]  # type: ignore[index]
        assert_contains(ids, [16, 17], f"search_role_{role}")

    # 3) search role=student
    # Note: "Студент" matches "Студентов Студент Студентович" and "Студент Тестовый 2"
    # but NOT "Студенческий Хвост" (different word)
    j = http_get("/users/search", {"q": q_student, "role": "student", "api_key": API_KEY})
    ids = [row["id"] for row in j]  # type: ignore[index]
    assert_contains(ids, [13, 14], "search_role_student")

    # 4) search role=methodist (alias -> Методист)
    j = http_get("/users/search", {"q": q_komlev, "role": "methodist", "api_key": API_KEY})
    ids = [row["id"] for row in j]  # type: ignore[index]
    assert_contains(ids, [10], "search_role_methodist")

    # 5) list role in Russian
    j = http_get(
        "/users/",
        {
            "role": role_methodist_ru,
            "sort_by": "full_name",
            "order": "asc",
            "skip": 0,
            "limit": 50,
            "api_key": API_KEY,
        },
    )
    ids = [row["id"] for row in j["items"]]  # type: ignore[index]
    assert_contains(ids, [10], "list_role_russian")


if __name__ == "__main__":
    main()

