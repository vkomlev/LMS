"""
Тесты POST /api/v1/materials/bulk-upsert (Subsystem A / Phase 1).

- OpenAPI: путь зарегистрирован
- create / update / unchanged / дубликат ключа в batch
- несуществующий course_id
- 403 без api_key
- смешанный batch: одна валидная строка + битый content → 200, per-item validation error
- атомарность: сбой на второй записи → rollback, в БД нет ни одной из двух строк
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text

from app.core.config import Settings

settings = Settings()


def test_openapi_materials_bulk_upsert_registered():
    """Маршрут bulk-upsert есть в OpenAPI."""
    from app.api.main import app

    openapi = app.openapi()
    paths = openapi.get("paths", {})
    path = "/api/v1/materials/bulk-upsert"
    assert path in paths, f"Ожидался {path}, есть: {list(paths.keys())}"
    post = paths[path].get("post")
    assert post is not None


async def _run_bulk_upsert_tests() -> bool:
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

    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        r = await session.execute(text("SELECT id FROM courses ORDER BY id LIMIT 1"))
        row = r.first()
        if not row:
            print("[SKIP] Нет курсов в БД")
            return True
        course_id = row[0]

    api_key = cfg.valid_api_keys[0]
    transport = ASGITransport(app=app)
    base = "http://test"
    ext = f"wp:bulk-{uuid.uuid4().hex}"

    def item_payload(title: str, **kwargs):
        base_item = {
            "course_id": course_id,
            "external_uid": ext,
            "title": title,
            "type": "text",
            "content": {"text": "hello", "format": "markdown"},
        }
        base_item.update(kwargs)
        return base_item

    async with httpx.AsyncClient(transport=transport, base_url=base) as client:
        # 403 без ключа
        r0 = await client.post(
            "/api/v1/materials/bulk-upsert",
            json={"items": [item_payload("A")]},
        )
        if r0.status_code != 403:
            print(f"[FAIL] Ожидали 403 без api_key, получили {r0.status_code}")
            return False

        # create
        r1 = await client.post(
            f"/api/v1/materials/bulk-upsert?api_key={api_key}",
            json={"items": [item_payload("First title")]},
        )
        if r1.status_code != 200:
            print(f"[FAIL] create: {r1.status_code} {r1.text}")
            return False
        d1 = r1.json()
        if d1.get("created") != 1 or d1.get("processed") != 1:
            print(f"[FAIL] create counters: {d1}")
            return False
        if d1["items"][0].get("status") != "created":
            print(f"[FAIL] create item status: {d1}")
            return False
        material_id = d1["items"][0].get("material_id")
        if not material_id:
            print(f"[FAIL] нет material_id: {d1}")
            return False

        # unchanged (повтор того же payload)
        r2 = await client.post(
            f"/api/v1/materials/bulk-upsert?api_key={api_key}",
            json={"items": [item_payload("First title")]},
        )
        if r2.status_code != 200:
            print(f"[FAIL] unchanged: {r2.status_code}")
            return False
        d2 = r2.json()
        if d2.get("unchanged") != 1 or d2.get("processed") != 1:
            print(f"[FAIL] unchanged counters: {d2}")
            return False

        # update
        r3 = await client.post(
            f"/api/v1/materials/bulk-upsert?api_key={api_key}",
            json={"items": [item_payload("Second title")]},
        )
        if r3.status_code != 200:
            print(f"[FAIL] update: {r3.status_code}")
            return False
        d3 = r3.json()
        if d3.get("updated") != 1:
            print(f"[FAIL] update counters: {d3}")
            return False

        # дубликат ключа в batch: последний выигрывает
        r4 = await client.post(
            f"/api/v1/materials/bulk-upsert?api_key={api_key}",
            json={
                "items": [
                    item_payload("Lose"),
                    item_payload("Win"),
                ]
            },
        )
        if r4.status_code != 200:
            print(f"[FAIL] dup batch: {r4.status_code}")
            return False
        d4 = r4.json()
        if d4.get("processed") != 1:
            print(f"[FAIL] dup batch должен дать 1 обработанный ключ: {d4}")
            return False

    async with async_session() as session:
        r = await session.execute(
            text(
                "SELECT COUNT(*) FROM materials WHERE course_id = :cid AND external_uid = :ext"
            ),
            {"cid": course_id, "ext": ext},
        )
        cnt = r.scalar()
        if cnt != 1:
            print(f"[FAIL] ожидали 1 строку (course_id, external_uid), count={cnt}")
            return False
        r2 = await session.execute(
            text("SELECT title FROM materials WHERE id = :mid"),
            {"mid": material_id},
        )
        title_row = r2.first()
        if not title_row or title_row[0] != "Win":
            print(f"[FAIL] ожидали title Win после dup batch, got {title_row}")
            return False

    # несуществующий курс
    async with httpx.AsyncClient(transport=transport, base_url=base) as client:
        bad_course = 2_147_483_647
        r5 = await client.post(
            f"/api/v1/materials/bulk-upsert?api_key={api_key}",
            json={
                "items": [
                    {
                        "course_id": bad_course,
                        "external_uid": f"wp:bad-{uuid.uuid4().hex}",
                        "title": "x",
                        "type": "text",
                        "content": {"text": "a", "format": "plain"},
                    }
                ]
            },
        )
        if r5.status_code != 200:
            print(f"[FAIL] bad course status: {r5.status_code}")
            return False
        d5 = r5.json()
        if not d5.get("items") or d5["items"][0].get("status") != "error":
            print(f"[FAIL] bad course item: {d5}")
            return False
        if d5.get("processed") != 0:
            print(f"[FAIL] processed должен быть 0: {d5}")
            return False

        # смешанная валидность: первая строка OK, вторая — неверный content для type=text
        ext_good = f"wp:mix-good-{uuid.uuid4().hex}"
        ext_bad = f"wp:mix-bad-{uuid.uuid4().hex}"
        r6 = await client.post(
            f"/api/v1/materials/bulk-upsert?api_key={api_key}",
            json={
                "items": [
                    {
                        "course_id": course_id,
                        "external_uid": ext_good,
                        "title": "OK row",
                        "type": "text",
                        "content": {"text": "ok", "format": "plain"},
                    },
                    {
                        "course_id": course_id,
                        "external_uid": ext_bad,
                        "title": "Bad row",
                        "type": "text",
                        "content": {"wrong_key": True},
                    },
                ]
            },
        )
        if r6.status_code != 200:
            print(f"[FAIL] mixed batch HTTP: {r6.status_code} {r6.text}")
            return False
        d6 = r6.json()
        if d6.get("processed") != 1 or d6.get("created") != 1:
            print(f"[FAIL] mixed batch counters: {d6}")
            return False
        if len(d6.get("items", [])) != 2:
            print(f"[FAIL] mixed batch items len: {d6}")
            return False
        if d6["items"][0].get("status") != "created":
            print(f"[FAIL] mixed first item: {d6['items']}")
            return False
        if d6["items"][1].get("status") != "error":
            print(f"[FAIL] mixed second item: {d6['items']}")
            return False

    async with async_session() as session:
        c_good = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM materials WHERE course_id = :cid AND external_uid = :ext"
                ),
                {"cid": course_id, "ext": ext_good},
            )
        ).scalar()
        c_bad = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM materials WHERE course_id = :cid AND external_uid = :ext"
                ),
                {"cid": course_id, "ext": ext_bad},
            )
        ).scalar()
        if c_good != 1 or c_bad != 0:
            print(f"[FAIL] mixed DB counts good={c_good} bad={c_bad}")
            return False

    # атомарность записи: второй create падает — откат всей транзакции
    from app.repos.materials_repo import MaterialsRepository

    ext_a = f"wp:atom-a-{uuid.uuid4().hex}"
    ext_b = f"wp:atom-b-{uuid.uuid4().hex}"
    orig_create = MaterialsRepository.create
    call_counter = {"n": 0}

    async def flaky_create(self, db, obj_in, *, commit=True):
        call_counter["n"] += 1
        if call_counter["n"] == 2:
            raise RuntimeError("simulated DB failure on second row")
        return await orig_create(self, db, obj_in, commit=commit)

    async with httpx.AsyncClient(transport=transport, base_url=base) as client:
        with patch.object(MaterialsRepository, "create", flaky_create):
            r7 = await client.post(
                f"/api/v1/materials/bulk-upsert?api_key={api_key}",
                json={
                    "items": [
                        {
                            "course_id": course_id,
                            "external_uid": ext_a,
                            "title": "A",
                            "type": "text",
                            "content": {"text": "a", "format": "plain"},
                        },
                        {
                            "course_id": course_id,
                            "external_uid": ext_b,
                            "title": "B",
                            "type": "text",
                            "content": {"text": "b", "format": "plain"},
                        },
                    ]
                },
            )
        if r7.status_code != 200:
            print(f"[FAIL] atomic test HTTP: {r7.status_code}")
            return False
        d7 = r7.json()
        if d7.get("processed") != 0:
            print(f"[FAIL] atomic processed должен быть 0: {d7}")
            return False
        if not d7.get("errors"):
            print(f"[FAIL] atomic ожидали errors не пусто: {d7}")
            return False
        for it in d7.get("items", []):
            if it.get("status") != "error":
                print(f"[FAIL] atomic все items должны быть error: {d7}")
                return False

    async with async_session() as session:
        ca = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM materials WHERE course_id = :cid AND external_uid = :ext"
                ),
                {"cid": course_id, "ext": ext_a},
            )
        ).scalar()
        cb = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM materials WHERE course_id = :cid AND external_uid = :ext"
                ),
                {"cid": course_id, "ext": ext_b},
            )
        ).scalar()
        if ca != 0 or cb != 0:
            print(f"[FAIL] atomic rollback: ожидали 0 строк, ca={ca} cb={cb}")
            return False

    await engine.dispose()
    print("[PASS] materials bulk-upsert")
    return True


def test_materials_bulk_upsert_integration():
    """Интеграционные проверки через ASGI + БД."""
    ok = asyncio.run(_run_bulk_upsert_tests())
    assert ok


if __name__ == "__main__":
    asyncio.run(_run_bulk_upsert_tests())
