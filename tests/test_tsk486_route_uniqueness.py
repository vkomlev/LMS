"""tsk-486: один адрес — один рукописный обработчик.

FastAPI матчит маршруты по порядку регистрации. Если один и тот же путь
объявлен дважды, второй обработчик не выполняется НИКОГДА — но продолжает
жить в `openapi.json` со своей формой ответа. Потребитель, написанный по
схеме, сломается на первом же вызове.

Это уже третий случай в проекте: `GET /courses/{id}` (Волна 1 tsk-433),
`GET /users/{id}` (Волна 3.1) и `GET /users/{id}/courses` (эта задача).

**Две тонкости, без которых проверка бесполезна.**

1. Сравнивать пути надо с нормализацией имени параметра. Дубль из этой задачи
   выглядел как `/users/{user_id}/courses` и `/users/{teacher_id}/courses` —
   строки разные, маршрут один. Наивная проверка по точному пути его пропускает.
2. Перекрытие ОБЩЕГО CRUD-роутера — приём, а не дефект: свой обработчик
   объявляется до `include_router(crud_router)` и намеренно затеняет generic
   `get_item`/`patch_item`/`list_items`. Так сделаны карточка человека, правка
   материала и задания. Запрещать надо столкновение двух РУКОПИСНЫХ
   обработчиков.
"""
from __future__ import annotations

import re
from collections import defaultdict

from app.api.main import app

# Имена обработчиков generic-CRUD роутера: их затенение — осознанный приём.
_GENERIC_CRUD_NAMES = {"get_item", "patch_item", "list_items", "create_item", "delete_item"}


def _normalize(path: str) -> str:
    """`/users/{teacher_id}/courses` → `/users/{}/courses`.

    Имя параметра на маршрутизацию не влияет: разные имена дают ОДИН адрес.
    """
    return re.sub(r"\{[^}]+\}", "{}", path)


def _collect() -> dict[tuple[str, str], list[str]]:
    routes: dict[tuple[str, str], list[str]] = defaultdict(list)
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path or not path.startswith("/api/"):
            continue
        for method in getattr(route, "methods", None) or []:
            if method in ("HEAD", "OPTIONS"):
                continue
            routes[(method, _normalize(path))].append(getattr(route, "name", "?"))
    return routes


def test_no_two_handwritten_handlers_on_one_route() -> None:
    collisions = {}
    for key, names in _collect().items():
        handwritten = [n for n in names if n not in _GENERIC_CRUD_NAMES]
        if len(handwritten) > 1:
            collisions[key] = handwritten
    assert not collisions, (
        "Один адрес обслуживают несколько рукописных обработчиков — победит "
        f"зарегистрированный первым, остальные мертвы: {collisions}"
    )


def test_generic_crud_is_shadowed_by_at_most_one_handler() -> None:
    """Перекрытие общего CRUD допустимо, но ровно одним обработчиком."""
    for key, names in _collect().items():
        generic = [n for n in names if n in _GENERIC_CRUD_NAMES]
        assert len(generic) <= 1, f"{key}: общий CRUD объявлен дважды — {names}"


def test_teacher_courses_route_is_gone() -> None:
    """Мёртвый `list_teacher_courses` удалён, а не просто переименован."""
    names = {getattr(r, "name", "") for r in app.routes}
    assert "list_teacher_courses" not in names


def test_user_courses_route_still_served() -> None:
    """Удаление мёртвого не задело живой обработчик того же адреса."""
    served = {
        (m, getattr(r, "name", ""))
        for r in app.routes
        for m in (getattr(r, "methods", None) or [])
        if _normalize(getattr(r, "path", "") or "") == "/api/v1/users/{}/courses"
    }
    assert ("GET", "get_user_courses_endpoint") in served
