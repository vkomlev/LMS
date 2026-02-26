"""
Тесты контракта attempt.meta.task_ids (Learning API).

Гарантии: после start-or-get-attempt для task_id=X в GET /attempts/{id}
meta — объект, meta.task_ids — int[], X входит в task_ids;
повторный вызов не дублирует; пустой/битый meta восстанавливается.
"""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from app.services.attempts_service import AttemptsService
from app.models.attempts import Attempts


def test_ensure_attempt_task_ids_new_meta():
    """Новая попытка: meta=None -> после ensure meta.task_ids = [task_id]."""
    svc = AttemptsService()
    attempt = Attempts(id=1, user_id=1, course_id=1, meta=None, finished_at=None)
    attempt.created_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    attempt.source_system = "test"
    attempt.time_expired = False

    async def _run():
        db = AsyncMock()
        capture = {}
        async def _update(db, db_obj, obj_in):
            capture["obj_in"] = obj_in
            out = MagicMock()
            out.meta = obj_in.get("meta", {})
            return out
        svc.update = _update
        result = await svc.ensure_attempt_task_ids(db, attempt, 42)
        assert capture["obj_in"]["meta"]["task_ids"] == [42]
        return True

    asyncio.run(_run())
    print("[PASS] ensure_attempt_task_ids: meta=None -> task_ids=[42]")


def test_ensure_attempt_task_ids_empty_list_adds():
    """Существующая попытка: meta.task_ids=[] -> после ensure содержит task_id."""
    svc = AttemptsService()
    attempt = Attempts(id=1, user_id=1, course_id=1, meta={"task_ids": []}, finished_at=None)
    attempt.created_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    attempt.source_system = "test"
    attempt.time_expired = False

    async def _run():
        db = AsyncMock()
        capture = {}
        async def _update(db, db_obj, obj_in):
            capture["obj_in"] = obj_in
            out = MagicMock()
            out.meta = obj_in.get("meta", {})
            return out
        svc.update = _update
        await svc.ensure_attempt_task_ids(db, attempt, 7)
        assert capture["obj_in"]["meta"]["task_ids"] == [7]
        return True

    asyncio.run(_run())
    print("[PASS] ensure_attempt_task_ids: task_ids=[] -> добавляется X")


def test_ensure_attempt_task_ids_no_duplicate():
    """task_ids уже содержит X -> без дубля (идемпотентность)."""
    svc = AttemptsService()
    attempt = Attempts(id=1, user_id=1, course_id=1, meta={"task_ids": [5]}, finished_at=None)
    attempt.created_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    attempt.source_system = "test"
    attempt.time_expired = False

    async def _run():
        db = AsyncMock()
        capture = {}
        async def _update(db, db_obj, obj_in):
            capture["obj_in"] = obj_in
            out = MagicMock()
            out.meta = obj_in.get("meta", {})
            return out
        svc.update = _update
        await svc.ensure_attempt_task_ids(db, attempt, 5)
        ids = capture["obj_in"]["meta"]["task_ids"]
        assert ids == [5]
        assert ids.count(5) == 1
        return True

    asyncio.run(_run())
    print("[PASS] ensure_attempt_task_ids: X уже в списке, без дубля")


def test_ensure_attempt_task_ids_merge():
    """task_ids=[Y], X != Y -> после ensure содержит и Y, и X."""
    svc = AttemptsService()
    attempt = Attempts(id=1, user_id=1, course_id=1, meta={"task_ids": [10]}, finished_at=None)
    attempt.created_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    attempt.source_system = "test"
    attempt.time_expired = False

    async def _run():
        db = AsyncMock()
        capture = {}
        async def _update(db, db_obj, obj_in):
            capture["obj_in"] = obj_in
            out = MagicMock()
            out.meta = obj_in.get("meta", {})
            return out
        svc.update = _update
        await svc.ensure_attempt_task_ids(db, attempt, 20)
        ids = capture["obj_in"]["meta"]["task_ids"]
        assert 10 in ids and 20 in ids
        return True

    asyncio.run(_run())
    print("[PASS] ensure_attempt_task_ids: merge Y и X")


def test_ensure_attempt_task_ids_normalize_ints():
    """task_ids с не-int отфильтровываются, остаётся только int[]."""
    svc = AttemptsService()
    attempt = Attempts(
        id=1, user_id=1, course_id=1,
        meta={"task_ids": [1, "x", None, 2]},
        finished_at=None,
    )
    attempt.created_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    attempt.source_system = "test"
    attempt.time_expired = False

    async def _run():
        db = AsyncMock()
        capture = {}
        async def _update(db, db_obj, obj_in):
            capture["obj_in"] = obj_in
            out = MagicMock()
            out.meta = obj_in.get("meta", {})
            return out
        svc.update = _update
        await svc.ensure_attempt_task_ids(db, attempt, 3)
        ids = capture["obj_in"]["meta"]["task_ids"]
        assert ids == [1, 2, 3]
        assert all(isinstance(x, int) for x in ids)
        return True

    asyncio.run(_run())
    print("[PASS] ensure_attempt_task_ids: нормализация только int")


def main():
    print("=" * 60)
    print("Тесты attempt.meta.task_ids")
    print("=" * 60)
    test_ensure_attempt_task_ids_new_meta()
    test_ensure_attempt_task_ids_empty_list_adds()
    test_ensure_attempt_task_ids_no_duplicate()
    test_ensure_attempt_task_ids_merge()
    test_ensure_attempt_task_ids_normalize_ints()
    print("\n" + "=" * 60)
    print("Все тесты пройдены успешно.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
