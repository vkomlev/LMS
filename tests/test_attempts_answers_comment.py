"""
Автотесты API: контракт комментария в ответах (SA_COM).

Проверки:
1. SA_COM с value + comment -> 200, comment сохранён в answer_json.response.comment
2. SA_COM только с value -> 200, comment отсутствует или null
3. GET /attempts/{id} после ответа -> в results[].answer_json.response.comment возвращается комментарий
4. Регрессия: старый payload без comment -> 200

Требует: сервер запущен, в .env или окружении HOST, VALID_API_KEYS (или API_KEY для запросов).
Для полного прогона задайте TASK_ID — id задачи типа SA_COM.
"""
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

# Загрузка .env
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")
except Exception:
    pass

HOST = os.environ.get("HOST", "http://localhost:8000")
API_KEY = os.environ.get("API_KEY") or (os.environ.get("VALID_API_KEYS", "").strip().split(",")[0] if os.environ.get("VALID_API_KEYS") else None)
TASK_ID = os.environ.get("TASK_ID")
USER_ID = os.environ.get("USER_ID", "1")
COURSE_ID = os.environ.get("COURSE_ID", "1")
if not API_KEY:
    print("SKIP: задайте API_KEY или VALID_API_KEYS")
    sys.exit(0)


def _post(path: str, data: dict) -> tuple[int, dict]:
    req = Request(
        f"{HOST}{path}?api_key={API_KEY}",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def _get(path: str) -> tuple[int, dict]:
    req = Request(f"{HOST}{path}?api_key={API_KEY}", method="GET")
    with urlopen(req) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def _create_attempt() -> int:
    status, body = _post("/api/v1/attempts", {"user_id": int(USER_ID), "course_id": int(COURSE_ID), "source_system": "test"})
    if status != 201:
        raise RuntimeError(f"create attempt: {status} {body}")
    return body["id"]


def run_tests() -> int:
    if not TASK_ID:
        print("SKIP: задайте TASK_ID (id задачи типа SA_COM) для полного прогона")
        return 0

    task_id = int(TASK_ID)
    fails = 0

    # 1) SA_COM с value + comment -> 200, comment сохранён
    attempt_id = _create_attempt()
    status, body = _post(
        f"/api/v1/attempts/{attempt_id}/answers",
        {"items": [{"task_id": task_id, "answer": {"type": "SA_COM", "response": {"value": "42", "comment": "мой комментарий"}}}]},
    )
    if status != 200:
        print(f"FAIL test 1: expected 200, got {status} {body}")
        fails += 1
    else:
        status2, attempt_body = _get(f"/api/v1/attempts/{attempt_id}")
        if status2 != 200:
            print(f"FAIL test 1 (GET): {status2}")
            fails += 1
        else:
            results = attempt_body.get("results") or []
            found = next((r for r in results if r.get("task_id") == task_id), None)
            comment = (found.get("answer_json") or {}).get("response") or {}
            comment_val = comment.get("comment")
            if comment_val != "мой комментарий":
                print(f"FAIL test 1: comment not saved or wrong: {comment_val!r}")
                fails += 1
            else:
                print("PASS test 1: SA_COM value+comment -> 200, comment in answer_json.response.comment")

    # 2) SA_COM только value -> 200, comment отсутствует или null
    attempt_id2 = _create_attempt()
    status, body = _post(
        f"/api/v1/attempts/{attempt_id2}/answers",
        {"items": [{"task_id": task_id, "answer": {"type": "SA_COM", "response": {"value": "42"}}}]},
    )
    if status != 200:
        print(f"FAIL test 2: expected 200, got {status} {body}")
        fails += 1
    else:
        status2, attempt_body = _get(f"/api/v1/attempts/{attempt_id2}")
        if status2 != 200:
            print(f"FAIL test 2 (GET): {status2}")
            fails += 1
        else:
            results = attempt_body.get("results") or []
            found = next((r for r in results if r.get("task_id") == task_id), None)
            comment = (found.get("answer_json") or {}).get("response") or {}
            comment_val = comment.get("comment")
            if comment_val is not None and comment_val != "":
                print(f"FAIL test 2: comment should be absent/null, got {comment_val!r}")
                fails += 1
            else:
                print("PASS test 2: SA_COM only value -> 200, comment absent or null")

    # 3) GET /attempts/{id} возвращает comment (проверено в тесте 1)
    print("PASS test 3: GET /attempts/{id} returns comment (covered by test 1)")

    # 4) Регрессия: старый payload без comment
    attempt_id4 = _create_attempt()
    status, body = _post(
        f"/api/v1/attempts/{attempt_id4}/answers",
        {"items": [{"task_id": task_id, "answer": {"type": "SA_COM", "response": {"value": "ответ"}}}]},
    )
    if status != 200:
        print(f"FAIL test 4: old payload without comment -> expected 200, got {status} {body}")
        fails += 1
    else:
        print("PASS test 4: old payload without comment -> 200")

    return fails


if __name__ == "__main__":
    try:
        n = run_tests()
        sys.exit(1 if n else 0)
    except HTTPError as e:
        print(f"HTTP error: {e.code} {e.reason}")
        if e.fp:
            print(e.fp.read().decode("utf-8", errors="replace"))
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
