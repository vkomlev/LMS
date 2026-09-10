"""Сторож изоляции прогона: база данных (tsk-872) и Redis (tsk-880).

Проверяет не поведение продукта, а то, что прогон действительно работает на
своих ресурсах и что в них смотрят ВСЕ пути. Регресс здесь тихий: если подмена
`DATABASE_URL` или `REDIS_URL` уедет ниже импорта приложения, глобальный движок
`app.db.session` и кешированный пул `get_redis` останутся на общих ресурсах,
тесты продолжат зеленеть, а падения от соседнего прогона вернутся — то есть
задачи молча откатятся.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest

import run_database
import run_redis
from app.core.config import Settings
from app.db.session import engine as app_engine

_run_db = run_database.provision_run_database(os.environ.get("DATABASE_URL", ""))
_run_redis = run_redis.provision_run_redis(os.environ.get("REDIS_URL", ""))

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


@pytest.mark.skipif(
    not _run_redis.active,
    reason="Redis прогона не изолирован — смотри строку tsk-880 в шапке прогона",
)
def test_redis_points_to_run_database() -> None:
    """Redis прогона — своя логическая база, и её же видит конфиг приложения.

    Ограничители частоты ключуются по IP тестового клиента, а он у всех
    прогонов один: без своей базы второй прогон тратит окно первого (tsk-880).
    """
    assert _db_name(os.environ["REDIS_URL"]) == str(_run_redis.index)
    assert _db_name(Settings().redis_url) == str(_run_redis.index)
    assert _run_redis.index != 2, "база 2 — общая, там живёт FSM бота разработки"
