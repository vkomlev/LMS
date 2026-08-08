"""tsk-586: сервисный ключ в заголовке `X-API-Key` на legacy-двери `get_db`.

Что произошло. TG_LMS с коммита 8ceed6f (tsk-497, 2026-08-03) шлёт сервисный
ключ ТОЛЬКО заголовком `X-API-Key`. Legacy-dependency `get_api_key` читала
исключительно query-параметр `?api_key=` — все эндпоинты на `Depends(get_db)`
(привязка курса ученику, ручная проверка работ, вся переписка, справочники)
отвечали ботам 403, а бот показывал «Недостаточно прав для этого действия».
Дефект прожил 5 дней при зелёных 329/329 тестах TG_LMS: там HTTP-клиент
замокан, то есть транспорт авторизации не проверялся вовсе.

Почему тест такой. Точечный тест на один эндпоинт этот класс не ловит — ломается
не эндпоинт, а ОБЩАЯ дверь, и её отвал виден только обходом всех, кто за ней
стоит. Поэтому здесь метод-обход: берём КАЖДЫЙ маршрут, у которого в дереве
зависимостей есть `get_api_key`, и дёргаем его реальным ASGI-клиентом.

Проверяются два свойства, по отдельности бесполезные:
  1. с валидным ключом в ЗАГОЛОВКЕ ответ не 401/403 — дверь пускает;
  2. с мусорным ключом ответ 401/403 — дверь вообще существует (иначе п.1
     проходил бы и на полностью открытом эндпоинте).

Побочные эффекты безопасны: `conftest._override_app_db` сажает ASGI-запросы на
соединение с внешней транзакцией, которая откатывается после теста, а
id-заглушка в путях (`_ABSENT_ID`) заведомо отсутствует в БД.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from fastapi import HTTPException

from app.api.deps import get_api_key
from app.api.main import app
from app.core.config import Settings

_settings = Settings()

#: Заведомо отсутствующий id: обходчик доходит до хендлера (404), но ничего
#: реального не трогает.
_ABSENT_ID = 999_999_999
_ABSENT_STR = "tsk586-absent"

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _service_key() -> str:
    keys = list(_settings.valid_api_keys)
    if not keys:
        pytest.skip("VALID_API_KEYS не задан в .env — нечем проверять сервисный гейт")
    return keys[0]


def _depends_on_api_key(dependant) -> bool:
    """Есть ли `get_api_key` где-либо в дереве зависимостей маршрута."""
    if dependant.call is get_api_key:
        return True
    return any(_depends_on_api_key(sub) for sub in dependant.dependencies)


def _fill_path(route: APIRoute) -> str:
    """Подставить заглушки вместо path-параметров маршрута."""
    types = {p.name: p.type_ for p in route.dependant.path_params}
    path = route.path
    for name, type_ in types.items():
        value = str(_ABSENT_ID) if type_ is int else _ABSENT_STR
        path = path.replace("{" + name + "}", value)
    return path


def _legacy_endpoints() -> list[tuple[str, str, str]]:
    """Все (метод, путь-шаблон, путь-с-заглушками) за legacy-дверью `get_api_key`."""
    out: list[tuple[str, str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not _depends_on_api_key(route.dependant):
            continue
        filled = _fill_path(route)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            out.append((method, route.path, filled))
    return sorted(out, key=lambda row: (row[1], row[0]))


LEGACY_ENDPOINTS = _legacy_endpoints()


def test_legacy_gate_still_has_endpoints_behind_it():
    """Сторож самого обхода: если список опустеет, тесты ниже станут пустышкой."""
    assert len(LEGACY_ENDPOINTS) > 50, (
        f"За legacy-дверью найдено всего {len(LEGACY_ENDPOINTS)} маршрутов — "
        "обход перестал находить эндпоинты, проверь `_depends_on_api_key`"
    )


@pytest_asyncio.fixture(scope="function")
async def gate_client():
    """Клиент обхода: 500 внутри приложения превращается в статус, а не в исключение.

    Обычная фикстура `client` пробрасывает исключение хендлера наружу — тогда
    первый же сломанный эндпоинт (см. tsk-586, `/meta/tasks`) прервал бы обход
    и скрыл состояние остальных ста с лишним. Здесь важен ровно статус двери.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _call(client, method: str, path: str, headers: dict[str, str]):
    kwargs: dict[str, object] = {"headers": headers}
    if method in _BODY_METHODS:
        kwargs["json"] = {}
    return await client.request(method, path, **kwargs)


@pytest.mark.asyncio
async def test_every_legacy_endpoint_accepts_key_in_header(gate_client):
    """Ключ в заголовке `X-API-Key` открывает КАЖДЫЙ эндпоинт legacy-двери."""
    headers = {"X-API-Key": _service_key()}
    rejected: list[str] = []
    for method, template, path in LEGACY_ENDPOINTS:
        resp = await _call(gate_client, method, path, headers)
        if resp.status_code in (401, 403):
            rejected.append(f"{method} {template} -> {resp.status_code} {resp.text[:120]}")
    assert not rejected, (
        "Сервисный ключ в заголовке X-API-Key отвергнут на "
        f"{len(rejected)} из {len(LEGACY_ENDPOINTS)} эндпоинтов:\n" + "\n".join(rejected)
    )


@pytest.mark.asyncio
async def test_every_legacy_endpoint_still_rejects_bad_key(gate_client):
    """Обратная сторона: без валидного ключа дверь по-прежнему закрыта.

    Без этой проверки предыдущий тест зеленел бы и на эндпоинте, с которого
    авторизацию сняли вовсе.
    """
    headers = {"X-API-Key": "tsk586-definitely-not-a-valid-key"}
    opened: list[str] = []
    for method, template, path in LEGACY_ENDPOINTS:
        resp = await _call(gate_client, method, path, headers)
        if resp.status_code not in (401, 403):
            opened.append(f"{method} {template} -> {resp.status_code}")
    assert not opened, (
        "Эндпоинты пустили запрос с невалидным ключом (дверь снята?):\n" + "\n".join(opened)
    )


@pytest.mark.asyncio
async def test_legacy_query_key_still_works(client):
    """Обратная совместимость: ContentBackbone ходит с `?api_key=` — не ломаем."""
    resp = await client.get(f"/api/v1/roles/?api_key={_service_key()}")
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_user_courses_link_accepts_header(client):
    """Точка из жалобы оператора: привязка курса ученику из бота методиста.

    Тело намеренно пустое — важно, что ответ 422 (тело не прошло валидацию),
    а НЕ 403 (не пустили на порог).
    """
    resp = await client.post(
        "/api/v1/user-courses/",
        headers={"X-API-Key": _service_key()},
        json={},
    )
    assert resp.status_code == 422, resp.text


# --- Второй дефект той же задачи: 500 на списках курсов ---------------------


@pytest.mark.asyncio
async def test_courses_list_does_not_crash_on_lazy_parents(client):
    """`GET /courses/` не должен падать на lazy-load родительских курсов.

    `app/repos/base.py::paginate` определял «это курс?» через
    `hasattr(items[0], 'parent_courses')`. `hasattr` на незагруженной связи ORM
    не проверяет наличие поля, а читает его — то есть запускает lazy-load
    посреди async-сессии → `MissingGreenlet` → 500. На проде это ломало
    `GET /courses/` и `GET /meta/tasks` при валидном ключе.
    """
    resp = await client.get(
        "/api/v1/courses/?skip=0&limit=5", headers={"X-API-Key": _service_key()}
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert items, "в dev-БД нет курсов — проверка вырождается, наполните данные"
    # Связь должна быть реально подгружена, а не «съедена» защитой в property.
    assert all("parent_course_ids" in c for c in items)


@pytest.mark.asyncio
async def test_meta_tasks_returns_200(client):
    """`GET /meta/tasks` — тот же корень, отдельный вход (справочники для импорта)."""
    resp = await client.get("/api/v1/meta/tasks", headers={"X-API-Key": _service_key()})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["courses"], "справочник курсов пуст — проверка вырождается"
    assert body["difficulties"]


# --- Юнит-уровень самой dependency ------------------------------------------


@pytest.mark.asyncio
async def test_get_api_key_accepts_header():
    key = _service_key()
    assert await get_api_key(key_query=None, key_header=key) == key


@pytest.mark.asyncio
async def test_get_api_key_accepts_query():
    key = _service_key()
    assert await get_api_key(key_query=key, key_header=None) == key


@pytest.mark.asyncio
async def test_get_api_key_falls_back_to_query_when_header_invalid():
    """Мусорный заголовок не должен «затенять» валидный legacy-query."""
    key = _service_key()
    assert await get_api_key(key_query=key, key_header="garbage") == key


@pytest.mark.asyncio
async def test_get_api_key_rejects_missing_and_invalid():
    for query, header in ((None, None), ("garbage", None), (None, "garbage"), ("a", "b")):
        with pytest.raises(HTTPException) as exc:
            await get_api_key(key_query=query, key_header=header)
        assert exc.value.status_code == 403
