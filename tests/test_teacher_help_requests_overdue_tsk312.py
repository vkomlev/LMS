"""
Тест Learning Engine (tsk-312) — фильтр «только просроченные» в списке заявок.

GET /api/v1/teacher/help-requests?overdue=1 возвращает только заявки с
due_at < now (ортогонально типу). Предикат зеркалит get_teacher_workload.overdue_total.

Сценарий: сидим две открытые заявки на одного teacher — одну с due_at в прошлом
(просрочена), одну с due_at в будущем (не просрочена). Проверяем:
- overdue=1 → просроченная в списке, непросроченная — нет;
- overdue=0/без параметра → обе в списке.
Сид-строки удаляются в finally.
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

from app.core.config import Settings

settings = Settings()


async def _get_teacher_id_and_api_key():
    """Взять teacher_id (из student_teacher_links) и api_key из конфига."""
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
                return None, None
            teacher_id = int(row[0])
        api_key = getattr(settings, "valid_api_keys", None) and settings.valid_api_keys[0]
        return teacher_id, api_key
    except Exception as e:
        print(f"[SKIP] БД/конфиг: {e}")
        return None, None


async def _seed_overdue_pair(teacher_id: int) -> tuple[int | None, int | None]:
    """Создать две открытые заявки для teacher_id: (просроченная, непросроченная).

    Просроченная — due_at = now() - 1 day; непросроченная — due_at = now() + 1 day.
    Обе привязаны к teacher_id через assigned_teacher_id (попадают под ACL).
    Возвращает (overdue_id, fresh_id) или (None, None) при ошибке.
    """
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        engine = create_async_engine(settings.database_url)
        async_session = async_sessionmaker(engine, expire_on_commit=False)
        async with async_session() as session:
            ids: list[int] = []
            for offset in ("-1 day", "+1 day"):
                r = await session.execute(
                    text(f"""
                        INSERT INTO help_requests
                        (status, request_type, auto_created, context_json,
                         student_id, task_id, assigned_teacher_id,
                         created_at, updated_at, priority, due_at)
                        SELECT 'open', 'manual_help', false, '{{}}'::jsonb,
                               u.id, t.id, :teacher_id, now(), now(), 100,
                               now() + interval '{offset}'
                        FROM (SELECT id FROM users LIMIT 1) u,
                             (SELECT id FROM tasks LIMIT 1) t
                        RETURNING id
                    """),
                    {"teacher_id": teacher_id},
                )
                row = r.fetchone()
                if not row:
                    await session.rollback()
                    return None, None
                ids.append(int(row[0]))
            await session.commit()
            return ids[0], ids[1]
    except Exception as e:
        print(f"[WARN] seed overdue pair: {e}")
        return None, None


async def _cleanup(ids: list[int]) -> None:
    """Удалить сид-заявки по id."""
    if not ids:
        return
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        engine = create_async_engine(settings.database_url)
        async_session = async_sessionmaker(engine, expire_on_commit=False)
        async with async_session() as session:
            await session.execute(
                text("DELETE FROM help_requests WHERE id = ANY(:ids)"),
                {"ids": ids},
            )
            await session.commit()
    except Exception as e:
        print(f"[WARN] cleanup: {e}")


async def test_overdue_filter_returns_only_overdue():
    """overdue=1 → только просроченная; без overdue → обе заявки в списке."""
    print("\n=== Тест: list help-requests overdue-фильтр (tsk-312) ===")
    try:
        import httpx
        from httpx import ASGITransport
    except Exception:
        print("[SKIP] Требуется httpx")
        return True

    teacher_id, api_key = await _get_teacher_id_and_api_key()
    if not api_key or teacher_id is None:
        print("[SKIP] Нет API_KEY или teacher_id (student_teacher_links)")
        return True

    overdue_id, fresh_id = await _seed_overdue_pair(teacher_id)
    if overdue_id is None or fresh_id is None:
        print("[SKIP] Не удалось создать сид-заявки")
        return True

    seeded = [overdue_id, fresh_id]
    try:
        from app.api.main import app
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # overdue=1 — просроченная есть, непросроченной нет
            resp = await client.get(
                f"/api/v1/teacher/help-requests?teacher_id={teacher_id}"
                f"&status=open&overdue=1&limit=100&api_key={api_key}"
            )
            if resp.status_code != 200:
                print(f"[FAIL] overdue=1: ожидался 200, получен {resp.status_code} {resp.text}")
                return False
            ids_overdue = {it["request_id"] for it in resp.json().get("items", [])}
            if overdue_id not in ids_overdue:
                print(f"[FAIL] overdue=1: просроченная заявка {overdue_id} не в списке")
                return False
            if fresh_id in ids_overdue:
                print(f"[FAIL] overdue=1: непросроченная заявка {fresh_id} просочилась в список")
                return False

            # каждая заявка в overdue-списке помечена is_overdue=True
            for it in resp.json().get("items", []):
                if not it.get("is_overdue"):
                    print(f"[FAIL] overdue=1: заявка {it['request_id']} без is_overdue")
                    return False

            # без overdue — обе заявки есть
            resp2 = await client.get(
                f"/api/v1/teacher/help-requests?teacher_id={teacher_id}"
                f"&status=open&limit=100&api_key={api_key}"
            )
            if resp2.status_code != 200:
                print(f"[FAIL] без overdue: ожидался 200, получен {resp2.status_code}")
                return False
            ids_all = {it["request_id"] for it in resp2.json().get("items", [])}
            if overdue_id not in ids_all or fresh_id not in ids_all:
                print(f"[FAIL] без overdue: ожидались обе заявки {seeded}, получено {ids_all & set(seeded)}")
                return False
    finally:
        await _cleanup(seeded)

    print("[PASS] overdue-фильтр отсекает непросроченные, is_overdue=True")
    return True


async def main():
    ok = True
    ok &= await test_overdue_filter_returns_only_overdue()
    print("\n=== ИТОГ ===")
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
