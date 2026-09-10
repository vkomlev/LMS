"""Сторож изоляции прогона (tsk-872).

Проверяет не поведение продукта, а то, что прогон действительно идёт в своей
временной базе и что в неё смотрят ВСЕ пути к БД. Регресс здесь тихий: если
подмена `DATABASE_URL` уедет ниже импорта приложения, глобальный движок
`app.db.session` останется на общей dev-базе, тесты продолжат зеленеть, а
падения от соседнего прогона вернутся — то есть задача молча откатится.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest

import run_database
from app.core.config import Settings
from app.db.session import engine as app_engine

_run_db = run_database.provision_run_database(os.environ.get("DATABASE_URL", ""))

pytestmark = pytest.mark.skipif(
    not _run_db.active,
    reason=f"изоляция прогона не активна: {_run_db.note}",
)


def _db_name(dsn: str) -> str:
    return urlsplit(dsn).path.lstrip("/")


def test_environment_points_to_run_database() -> None:
    """`DATABASE_URL` процесса — база этого прогона, а не база из `.env`."""
    assert _db_name(os.environ["DATABASE_URL"]) == _run_db.name
    assert _run_db.name.startswith(run_database.RUN_DB_PREFIX)


def test_settings_and_app_engine_point_to_run_database() -> None:
    """Конфиг приложения и его глобальный движок — в той же временной базе.

    Именно эта пара ломается при перестановке импортов в `conftest.py`: и
    настройки, и движок читают окружение один раз, на импорте модуля.
    """
    assert _db_name(Settings().database_url) == _run_db.name
    assert app_engine.url.database == _run_db.name
