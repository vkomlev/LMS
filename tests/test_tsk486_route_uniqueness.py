"""tsk-486: один адрес — один рукописный обработчик.

FastAPI матчит маршруты по порядку регистрации. Если один и тот же путь
объявлен дважды, второй обработчик не выполняется НИКОГДА — но продолжает
жить в `openapi.json` со своей формой ответа. Потребитель, написанный по
схеме, сломается на первом же вызове.

Это уже третий случай в проекте: `GET /courses/{id}` (Волна 1 tsk-433),
`GET /users/{id}` (Волна 3.1) и `GET /users/{id}/courses` (эта задача).

**Затенение бывает двух видов, и второй проверка изначально пропускала.**

1. *Одинаковые пути* — два обработчика на одном адресе.
2. *Литерал под параметром* — `/roles/catalog` объявлен ПОСЛЕ `/roles/{item_id}`.
   Строки разные, столкновения по первому виду нет, но литеральный путь всё
   равно мёртв: параметр матчит слово «catalog», и запрос падает на разборе его
   числом. Типизация в сигнатуре (`item_id: int`) путь НЕ сужает — она проверяет
   значение уже ПОСЛЕ матчинга, отдавая 422. За сутки этот класс всплыл трижды:
   `/users/{id}/courses`, `/users/duplicates`, `/roles/catalog`.

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


def _ordered_routes() -> list[tuple[int, str, str, str, object]]:
    """Маршруты В ПОРЯДКЕ РЕГИСТРАЦИИ — именно он решает, кто победит."""
    out = []
    for index, route in enumerate(app.routes):
        path = getattr(route, "path", None)
        regex = getattr(route, "path_regex", None)
        if not path or not path.startswith("/api/") or regex is None:
            continue
        for method in getattr(route, "methods", None) or []:
            if method in ("HEAD", "OPTIONS"):
                continue
            out.append((index, method, path, getattr(route, "name", "?"), regex))
    return out


def test_literal_path_not_shadowed_by_parametric() -> None:
    """Литеральный путь обязан регистрироваться РАНЬШЕ параметрического.

    Сопоставление берём у самого FastAPI (`path_regex`), а не угадываем своими
    правилами: проверка должна отвечать на вопрос «дойдёт ли запрос», а не на
    вопрос «похожи ли строки».
    """
    routes = _ordered_routes()
    shadowed: list[str] = []

    for index, method, path, name, _regex in routes:
        if "{" not in path:
            concrete = path
        else:
            # Подставляем значение, которое подойдёт любому параметру: спор
            # именно о том, что параметр съедает и слова тоже.
            concrete = re.sub(r"\{[^}]+\}", "1", path)

        for earlier_index, earlier_method, earlier_path, earlier_name, earlier_regex in routes:
            if earlier_index >= index or earlier_method != method:
                continue
            if earlier_path == path:
                continue  # полные дубли ловит соседняя проверка
            if not earlier_regex.match(concrete):
                continue
            # Затенение generic-CRUD своим обработчиком — приём, а не дефект.
            if name in _GENERIC_CRUD_NAMES:
                continue
            shadowed.append(
                f"{method} {path} ({name}) недостижим: раньше зарегистрирован "
                f"{earlier_path} ({earlier_name})"
            )

    assert not shadowed, "Мёртвые маршруты: " + "; ".join(shadowed)


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
