"""Сторож ширины перечисления «тип заявки помощи» (tsk-771).

Живой случай 02.09: `GET /teacher/students/4543/tasks/4129/history` отдавал 500 —
`app/schemas/task_history.py` объявлял свою копию литерала на двух значениях
(`manual_help`, `blocked_limit`), а в базе с tsk-303 живёт третий вид,
`individual_review`. Ответ падал на сериализации, преподаватель видел красную
строку вместо истории. Ровно та же необновлённая копия нашлась в дереве
прогресса ученика (`open_help_request_type`).

Класс дефекта — «значение добавили в базу и в часть схем, но не во все».
Поэтому здесь НЕ перечисление трёх видов руками (это бы состарилось вместе с
кодом), а два механических правила:

1. общий псевдоним `HelpRequestType` совпадает с CHECK-ограничением базы;
2. ни одно поле API с именем `*request_type*` не объявляет копию УЖЕ, чем база.

Четвёртый вид заявки, добавленный в базу и забытый в какой-нибудь схеме, падает
здесь, а не на проде.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
import types
import typing
from typing import Any, Iterator, Literal, Optional, get_args, get_origin

import pytest
from pydantic import BaseModel
from sqlalchemy import text

import app.api.v1 as api_v1_pkg
import app.schemas as schemas_pkg
from app.schemas.task_history import TaskHistoryHelpRequest, TaskHistoryResponse
from app.schemas.teacher_help_requests import HelpRequestType

_CONSTRAINT = "help_requests_request_type_check"
# Значения фильтров: «любой тип» — не вид заявки, наличия в базе не требует.
_FILTER_ONLY = {"all"}


async def _db_request_types(db) -> set[str]:
    """Допустимые значения `help_requests.request_type` по CHECK-ограничению базы."""
    definition = (
        await db.execute(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :c"),
            {"c": _CONSTRAINT},
        )
    ).scalar()
    assert definition, f"в базе нет ограничения {_CONSTRAINT} — сторож потерял источник истины"
    return set(re.findall(r"'([a-z_]+)'::character varying", definition))


def _iter_models(package) -> Iterator[type[BaseModel]]:
    """Все pydantic-модели пакета (модули импортируются, дубли отсеиваются)."""
    seen: set[int] = set()
    for mod in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
        module = importlib.import_module(mod.name)
        for obj in vars(module).values():
            if isinstance(obj, type) and issubclass(obj, BaseModel) and id(obj) not in seen:
                seen.add(id(obj))
                yield obj


def _literal_values(annotation: Any) -> Optional[set[str]]:
    """Множество строк Literal внутри аннотации (через Optional/Union); None — не Literal."""
    if get_origin(annotation) is Literal:
        return {v for v in get_args(annotation) if isinstance(v, str)}
    if get_origin(annotation) in (typing.Union, getattr(types, "UnionType", None)):
        collected: set[str] = set()
        found = False
        for arg in get_args(annotation):
            values = _literal_values(arg)
            if values is not None:
                collected |= values
                found = True
        return collected if found else None
    return None


async def test_shared_alias_matches_db_constraint(db) -> None:
    """`HelpRequestType` = ровно то, что разрешает база. Разъезд ловится здесь."""
    assert set(get_args(HelpRequestType)) == await _db_request_types(db)


async def test_no_narrow_request_type_copies_in_api_schemas(db) -> None:
    """Ни одна схема API не объявляет свой список видов заявки уже, чем база."""
    allowed = await _db_request_types(db)
    narrow: list[str] = []
    for model in list(_iter_models(schemas_pkg)) + list(_iter_models(api_v1_pkg)):
        for name, field in model.model_fields.items():
            if "request_type" not in name:
                continue
            values = _literal_values(field.annotation)
            if values is None:
                continue  # свободный str — сузить не может
            missing = allowed - values - _FILTER_ONLY
            if missing:
                narrow.append(f"{model.__module__}.{model.__name__}.{name}: не хватает {sorted(missing)}")
    assert not narrow, (
        "узкая копия перечисления видов заявки — брать общий тип "
        "app.schemas.teacher_help_requests.HelpRequestType:\n" + "\n".join(narrow)
    )


@pytest.mark.parametrize("request_type", ["manual_help", "blocked_limit", "individual_review"])
def test_task_history_serializes_every_request_type(request_type: str) -> None:
    """Ответ истории собирается для любого вида заявки — включая индивидуальный разбор."""
    response = TaskHistoryResponse(
        user_id=4543,
        task={"task_id": 4129},
        attempts=[],
        help_requests=[
            {
                "request_id": 260,
                "status": "open",
                "request_type": request_type,
                "message": None,
                "created_at": "2026-09-02T12:15:15Z",
                "replies": [],
            }
        ],
        hints={"total": 0, "text": 0, "video": 0},
    )
    assert response.help_requests[0].request_type == request_type
    assert isinstance(response.help_requests[0], TaskHistoryHelpRequest)
