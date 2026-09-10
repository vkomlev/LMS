"""Своя база данных на каждый прогон pytest (tsk-872).

Зачем. Транзакционная изоляция tsk-333 защищает от мусора соседнего ТЕСТА, но
не от соседнего ПРОЦЕССА: два полных прогона по одной локальной базе роняют
друг друга в областях, которых никто не трогал. Усилителей два — 22 модуля из
`SELF_MANAGED_CONNECTION_MODULES` пишут по-настоящему (им нужны параллельные
соединения: advisory-lock, дедлоки, гонки), а `test_migrations.py` гоняет
downgrade/upgrade СХЕМЫ, то есть соседний прогон в это время работает на
плывущей схеме.

Как. Перед сборкой тестов прогон получает собственную базу — клон шаблона,
который создаётся один раз из рабочей dev-базы. Клонирование идёт на уровне
файлов, поэтому стоит секунды, а не минуты (замер на dev-базе 406 МБ: 8 c
клон, 0.4 c удаление против ~нескольких минут на `alembic upgrade head` с
нуля). `DATABASE_URL` подменяется в окружении процесса ДО импорта приложения,
поэтому во временную базу смотрят все три пути разом: фикстуры с общей
транзакцией, модули со своим движком и подпроцессы alembic внутри тестов.

Управление через окружение:
  LMS_TEST_DB_ISOLATION = auto (по умолчанию) | on | off
      auto — изолировать, а если не вышло (нет прав, занят шаблон) —
             предупредить и работать по-старому, на общей базе;
      on   — изолировать или отказать всему прогону;
      off  — прежнее поведение, прогон идёт в базе из `.env`.
  LMS_TEST_TEMPLATE_DB — имя базы-шаблона (по умолчанию `lms_test_template`).
  LMS_TEST_DB_KEEP=1   — не удалять базу прогона в конце (для разбора).
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Префикс баз прогона. По нему же идёт уборка сирот, оставшихся от прогонов,
# убитых по Ctrl+C или упавших вместе с процессом.
RUN_DB_PREFIX = "lms_test_run_"
DEFAULT_TEMPLATE_DB = "lms_test_template"

# Сирота старше этого срока точно не принадлежит живому прогону: самый долгий
# полный прогон занимает около часа.
ORPHAN_MAX_AGE_SEC = 6 * 60 * 60

# Ключ advisory-lock на подготовку шаблона: два прогона, стартовавшие
# одновременно после смены head, не должны мигрировать шаблон вдвоём.
_TEMPLATE_LOCK_KEY = 872_000_001

# tsk-467: сигнатуры боевой БД LMS. Здесь они нужны раньше, чем в
# `pytest_configure`: создавать базы на проде нельзя даже с
# `ALLOW_PROD_TESTS=1` (тот override существует для read-only проверки).
PROD_DB_SIGNATURES: tuple[str, ...] = ("5.42.107.253", "lms_prod")

# База прогона на процесс — одна (см. `provision_run_database`).
_provisioned: "RunDatabase | None" = None


@dataclass(frozen=True)
class RunDatabase:
    """Итог подготовки базы прогона."""

    active: bool
    dsn: str
    name: str | None
    note: str
    # Уборка вызывается из `pytest_sessionfinish`, а не из `atexit`: на выходе
    # интерпретатор уже не даёт запускать потоки, а asyncpg на Windows
    # разрешает адрес в отдельном потоке — уборка через atexit падает с
    # «cannot schedule new futures after interpreter shutdown».
    cleanup: "Callable[[], None] | None" = None

    def drop(self) -> None:
        """Удалить базу прогона, если она наша и её просили не сохранять."""
        if self.cleanup is not None:
            self.cleanup()


def _swap_database(dsn: str, db_name: str) -> str:
    """Тот же DSN, но с другим именем базы."""
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path=f"/{db_name}"))


def _asyncpg_dsn(dsn: str) -> str:
    """DSN без префикса диалекта SQLAlchemy — asyncpg его не понимает."""
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


def _quote_ident(name: str) -> str:
    """Имя объекта PostgreSQL в кавычках (имя базы `Learn` регистрозависимо)."""
    return '"' + name.replace('"', '""') + '"'


def _database_name(dsn: str) -> str:
    return urlsplit(dsn).path.lstrip("/")


def _looks_like_prod(dsn: str) -> bool:
    low = (dsn or "").lower()
    return any(sig.lower() in low for sig in PROD_DB_SIGNATURES)


def _alembic_head() -> str:
    """Актуальный head из дерева миграций, а не из прибитой константы."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    script_location = cfg.get_main_option("script_location")
    cfg.set_main_option("script_location", str(PROJECT_ROOT / script_location))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _run_alembic_upgrade(dsn: str) -> None:
    """Накатить миграции до head на указанную базу отдельным процессом.

    Отдельным — потому что `env.py` внутри поднимает свой event loop через
    `asyncio.run`, а мы вызываемся из уже работающего цикла.
    """
    env = dict(os.environ)
    env["DATABASE_URL"] = dsn
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head на базе-шаблоне не прошёл:\n"
            f"{result.stdout}\n{result.stderr}"
        )


async def _drop_orphan_databases(conn) -> int:
    """Удалить базы прошлых прогонов, которые никто не занял и которые стары.

    Имя базы несёт метку времени старта, поэтому возраст читается из имени —
    системного каталога с датой создания базы в PostgreSQL нет.
    """
    rows = await conn.fetch(
        "SELECT datname FROM pg_database WHERE datname LIKE $1", f"{RUN_DB_PREFIX}%"
    )
    now = int(time.time())
    dropped = 0
    for row in rows:
        name: str = row["datname"]
        stamp = name[len(RUN_DB_PREFIX) :].split("_", 1)[0]
        try:
            started = int(stamp)
        except ValueError:
            continue
        if now - started < ORPHAN_MAX_AGE_SEC:
            continue
        busy = await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = $1", name
        )
        if busy:
            continue
        try:
            await conn.execute(f"DROP DATABASE {_quote_ident(name)} WITH (FORCE)")
            dropped += 1
            _logger.info("tsk-872: удалена осиротевшая база прогона %s", name)
        except Exception as exc:  # noqa: BLE001 — уборка не должна ронять прогон
            _logger.warning("tsk-872: не удалось удалить %s: %s", name, exc)
    return dropped


async def _ensure_template(conn, base_dsn: str, template_db: str) -> None:
    """Создать базу-шаблон (если её нет) и довести её схему до head.

    Шаблон — клон рабочей dev-базы, а не пустая схема из миграций: тесты
    опираются на её справочники и объекты (например, `difficulties`), и цель
    задачи — изолировать прогон, а не переписать тесты.
    """
    base_db = _database_name(base_dsn)
    exists = await conn.fetchval(
        "SELECT 1 FROM pg_database WHERE datname = $1", template_db
    )
    if not exists:
        started = time.monotonic()
        try:
            await conn.execute(
                f"CREATE DATABASE {_quote_ident(template_db)} "
                f"TEMPLATE {_quote_ident(base_db)}"
            )
        except Exception as exc:  # noqa: BLE001 — нужна понятная причина, не трассировка
            # PostgreSQL клонирует базу только когда к источнику нет ЧУЖИХ
            # подключений. Чаще всего мешает запущенный локально dev-сервер, и
            # без этой подсказки сообщение драйвера читается как поломка.
            raise RuntimeError(
                f"не удалось создать базу-шаблон {template_db} из {base_db}: {exc}. "
                "Обычно мешает открытое подключение к базе разработки (запущенный "
                "локально сервер приложения, окно pgAdmin/DBeaver). Закрыть их и "
                "повторить прогон — шаблон создаётся один раз, дальше он не нужен."
            ) from exc
        _logger.info(
            "tsk-872: база-шаблон %s создана из %s за %.1f c",
            template_db,
            base_db,
            time.monotonic() - started,
        )

    # Схема шаблона могла отстать: dev-базу мигрируют, шаблон живёт своей
    # жизнью. Сверяем ревизию и догоняем — это дешевле пересоздания.
    import asyncpg

    template_dsn = _swap_database(base_dsn, template_db)
    probe = await asyncpg.connect(_asyncpg_dsn(template_dsn))
    try:
        current = await probe.fetchval(
            "SELECT version_num FROM alembic_version LIMIT 1"
        )
    except Exception:  # noqa: BLE001 — таблицы может не быть вовсе
        current = None
    finally:
        # Закрыть обязательно: клонирование запрещено, пока к шаблону есть
        # хоть одно чужое подключение.
        await probe.close()

    head = _alembic_head()
    if current != head:
        _logger.info(
            "tsk-872: схема шаблона %s (%s) отстала от head (%s) — догоняю",
            template_db,
            current,
            head,
        )
        _run_alembic_upgrade(template_dsn)


async def _provision(base_dsn: str, template_db: str) -> tuple[str, str, float]:
    """Создать базу этого прогона. Возвращает (имя, DSN, секунды на клон)."""
    import asyncpg

    admin_dsn = _asyncpg_dsn(_swap_database(base_dsn, "postgres"))
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", _TEMPLATE_LOCK_KEY)
        try:
            await _drop_orphan_databases(conn)
            await _ensure_template(conn, base_dsn, template_db)
            run_db = (
                f"{RUN_DB_PREFIX}{int(time.time())}_{os.getpid()}_{uuid.uuid4().hex[:6]}"
            )
            started = time.monotonic()
            await conn.execute(
                f"CREATE DATABASE {_quote_ident(run_db)} "
                f"TEMPLATE {_quote_ident(template_db)}"
            )
            elapsed = time.monotonic() - started
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _TEMPLATE_LOCK_KEY)
    finally:
        await conn.close()
    return run_db, _swap_database(base_dsn, run_db), elapsed


async def _drop_run_databases(base_dsn: str, run_db: str) -> None:
    """Удалить базу прогона и всё, что тесты породили от её имени.

    `test_migrations.py` заводит свою одноразовую базу как `<база>_migrations_test`
    — она тоже наша и тоже должна уйти.
    """
    import asyncpg

    admin_dsn = _asyncpg_dsn(_swap_database(base_dsn, "postgres"))
    conn = await asyncpg.connect(admin_dsn)
    try:
        rows = await conn.fetch(
            "SELECT datname FROM pg_database WHERE datname LIKE $1", f"{run_db}%"
        )
        for row in rows:
            name = row["datname"]
            try:
                await conn.execute(f"DROP DATABASE {_quote_ident(name)} WITH (FORCE)")
            except Exception as exc:  # noqa: BLE001
                _logger.warning("tsk-872: не удалось удалить базу %s: %s", name, exc)
    finally:
        await conn.close()


def provision_run_database(base_dsn: str) -> RunDatabase:
    """Подготовить базу этого прогона и вернуть её DSN.

    Вызывается из `tests/conftest.py` до импорта приложения — подменённый
    `DATABASE_URL` должен успеть попасть в `Settings()` и в глобальный движок.

    Результат кешируется на процесс: pytest импортирует conftest дважды, а база
    прогона нужна одна (иначе вторая, никем не убранная, остаётся висеть на
    диске — по 400 МБ за прогон).
    """
    global _provisioned
    if _provisioned is not None:
        return _provisioned
    _provisioned = _provision_run_database(base_dsn)
    return _provisioned


def _provision_run_database(base_dsn: str) -> RunDatabase:
    mode = os.getenv("LMS_TEST_DB_ISOLATION", "auto").strip().lower()
    if mode not in {"auto", "on", "off"}:
        raise RuntimeError(
            f"LMS_TEST_DB_ISOLATION={mode!r}: допустимо auto|on|off"
        )
    if mode == "off":
        return RunDatabase(
            False, base_dsn, None, "изоляция прогона выключена (LMS_TEST_DB_ISOLATION=off)"
        )
    if not base_dsn:
        return RunDatabase(False, base_dsn, None, "DATABASE_URL пуст — изоляция пропущена")
    if _looks_like_prod(base_dsn):
        # Отказ всему прогону выдаст `pytest_configure`; здесь важно одно —
        # не создавать никаких баз на боевом сервере.
        return RunDatabase(
            False, base_dsn, None, "DATABASE_URL похож на прод — базы не создаются"
        )

    template_db = os.getenv("LMS_TEST_TEMPLATE_DB", DEFAULT_TEMPLATE_DB).strip()
    try:
        run_db, run_dsn, elapsed = asyncio.run(_provision(base_dsn, template_db))
    except Exception as exc:  # noqa: BLE001
        message = (
            f"изоляция прогона НЕ включилась ({type(exc).__name__}: {exc}). "
            f"Прогон идёт в общей базе {_database_name(base_dsn)} — параллельный "
            "прогон соседа может его уронить"
        )
        if mode == "on":
            raise RuntimeError(f"tsk-872: {message}") from exc
        _logger.warning("tsk-872: %s", message)
        return RunDatabase(False, base_dsn, None, message)

    keep = os.getenv("LMS_TEST_DB_KEEP", "").strip().lower() in {"1", "true", "yes"}
    if keep:
        return RunDatabase(
            True,
            run_dsn,
            run_db,
            f"прогон идёт в отдельной базе {run_db} (клон {template_db} за "
            f"{elapsed:.1f} c); база НЕ будет удалена (LMS_TEST_DB_KEEP=1)",
        )

    def _cleanup() -> None:
        try:
            asyncio.run(_drop_run_databases(base_dsn, run_db))
        except Exception as exc:  # noqa: BLE001 — уборка не должна ронять выход
            # Печать, а не только лог: к концу прогона логи уже не попадают в
            # вывод, а незамеченная гора баз по 400 МБ съедает диск молча.
            print(
                f"tsk-872: базу прогона {run_db} удалить не удалось: "
                f"{type(exc).__name__}: {exc} "
                "(её подберёт уборка сирот следующего прогона)",
                file=sys.stderr,
                flush=True,
            )

    return RunDatabase(
        True,
        run_dsn,
        run_db,
        f"прогон идёт в отдельной базе {run_db} "
        f"(клон {template_db} за {elapsed:.1f} c)",
        _cleanup,
    )
