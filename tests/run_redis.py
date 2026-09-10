"""Своя логическая база Redis на каждый прогон pytest (tsk-880).

Продолжение tsk-872: там прогон получил собственную базу PostgreSQL, но Redis
остался общим — `REDIS_URL` в `.env` один на проект, и по умолчанию это
`redis://localhost:6379/2`. Делят её не только параллельные прогоны: там же
лежит FSM бота разработки (`fsm:*` от TG_LMS).

Чем это опасно именно здесь. Ограничители частоты ключуются по IP, а IP у
тестового клиента всегда один и тот же: `ml_send:127.0.0.1` — пять запросов за
десять минут, `quiz_lead:127.0.0.1` — десять за час. Счётчик общий, поэтому
второй прогон тратит окно первого и получает отказ там, где его кода никто не
трогал. Гостевые сессии и метки одноразовых токенов живут в тех же ключах.

Как. Прогон занимает свободную логическую базу Redis (у сервера их обычно 16) и
подменяет `REDIS_URL` в окружении процесса — до импорта приложения, потому что
`get_redis` кеширует пул на первый переданный URL. Захват атомарный: ключ аренды
пишется `SET NX`, поэтому два прогона, стартовавшие разом, не займут одну базу.
В конце прогона база очищается целиком; если процесс убили — аренда истечёт
сама.

Управление через окружение:
  LMS_TEST_REDIS_ISOLATION = auto (по умолчанию) | on | off
      auto — занять свободную базу, а если свободной нет — предупредить и
             работать по-старому, на базе из `.env`;
      on   — занять или отказать всему прогону;
      off  — прежнее поведение.
"""
from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

_logger = logging.getLogger(__name__)

#: Ключ аренды в самой занимаемой базе. Лежит именно там, а не в общей нулевой:
#: тогда «свободна ли база» и «кто её держит» — один и тот же вопрос к одному
#: месту, и брошенная аренда истекает вместе с данными прогона.
LEASE_KEY = "__lms_test_run_lease__"

#: Срок аренды. Заведомо больше самого долгого полного прогона (около часа),
#: чтобы живой прогон не потерял свою базу, но и не настолько велик, чтобы
#: убитый процесс держал её до перезапуска Redis.
LEASE_TTL_SECONDS = 2 * 60 * 60

#: Сколько ждать ответа Redis при захвате. Столько же, сколько проверка
#: доступности в `conftest.py`: если сервер молчит, изоляция не важнее прогона.
_TIMEOUT_S = 1.5

#: Сколько логических баз считать, если сервер не отвечает на `CONFIG GET
#: databases` (Memurai и облачные сборки эту команду иногда закрывают).
_DEFAULT_DATABASES = 16

_provisioned: "RunRedis | None" = None


@dataclass(frozen=True)
class RunRedis:
    """Итог захвата логической базы Redis."""

    active: bool
    url: str
    index: int | None
    note: str
    cleanup: Callable[[], None] | None = None

    def release(self) -> None:
        """Очистить занятую базу, если она наша."""
        if self.cleanup is not None:
            self.cleanup()


def _swap_db_index(url: str, index: int) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{index}"))


def _db_index(url: str) -> int | None:
    raw = urlsplit(url).path.lstrip("/")
    try:
        return int(raw)
    except ValueError:
        return None


def _connect(url: str):  # noqa: ANN201 — redis.Redis, импорт внутри
    import redis as redis_sync

    return redis_sync.Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=_TIMEOUT_S,
        socket_timeout=_TIMEOUT_S,
    )


def _database_count(client) -> int:  # noqa: ANN001
    """Сколько логических баз у сервера."""
    try:
        value = client.config_get("databases").get("databases")
        return int(value)
    except Exception:  # noqa: BLE001 — команда бывает закрыта, это не отказ
        return _DEFAULT_DATABASES


def _try_claim(url: str, index: int, run_id: str) -> bool:
    """Занять базу `index`, если она свободна. Захват атомарный (`SET NX`)."""
    client = _connect(_swap_db_index(url, index))
    try:
        if not client.set(LEASE_KEY, run_id, nx=True, ex=LEASE_TTL_SECONDS):
            return False  # база уже за кем-то
        if client.dbsize() != 1:
            # Ключ аренды лёг, но в базе есть чужие данные — значит ей
            # пользуются в обход аренды (бот разработки, ручные опыты).
            # Забираем своё и идём дальше.
            client.delete(LEASE_KEY)
            return False
        return True
    finally:
        client.close()


def _flush(url: str, index: int, run_id: str) -> None:
    """Очистить базу прогона — но только если аренда всё ещё наша."""
    client = _connect(_swap_db_index(url, index))
    try:
        if client.get(LEASE_KEY) == run_id:
            client.flushdb()
    finally:
        client.close()


def provision_run_redis(base_url: str) -> RunRedis:
    """Занять свободную базу Redis и вернуть её URL.

    Вызывается из `tests/conftest.py` до импорта приложения. Результат
    кешируется на процесс: pytest импортирует conftest дважды, а база нужна
    одна (см. ту же оговорку в `run_database.py`).
    """
    global _provisioned
    if _provisioned is not None:
        return _provisioned
    _provisioned = _provision_run_redis(base_url)
    return _provisioned


def _provision_run_redis(base_url: str) -> RunRedis:
    mode = os.getenv("LMS_TEST_REDIS_ISOLATION", "auto").strip().lower()
    if mode not in {"auto", "on", "off"}:
        raise RuntimeError(f"LMS_TEST_REDIS_ISOLATION={mode!r}: допустимо auto|on|off")
    if mode == "off":
        return RunRedis(
            False, base_url, None, "Redis не изолирован (LMS_TEST_REDIS_ISOLATION=off)"
        )
    if not base_url:
        # `REDIS_URL` в окружении обычно нет — адрес берётся из умолчания
        # `Settings`. Читаем оттуда, а не повторяем строку здесь: иначе два
        # источника истины разъедутся при первом же переезде Redis.
        # Импорт безопасен и до импорта приложения: конфиг читает окружение и
        # не поднимает ни движка БД, ни клиентов.
        from app.core.config import Settings

        base_url = Settings().redis_url

    run_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}-{int(time.time())}"
    try:
        probe = _connect(base_url)
        try:
            total = _database_count(probe)
        finally:
            probe.close()

        default_index = _db_index(base_url)
        # Идём сверху вниз: старшие номера почти никогда не заняты руками, а
        # база из `.env` (обычно 2) остаётся нетронутой — там живёт бот
        # разработки, и очищать её в конце прогона нельзя.
        for index in range(total - 1, 0, -1):
            if index == default_index:
                continue
            if _try_claim(base_url, index, run_id):
                url = _swap_db_index(base_url, index)

                def _cleanup(index: int = index) -> None:
                    try:
                        _flush(base_url, index, run_id)
                    except Exception as exc:  # noqa: BLE001 — уборка не роняет выход
                        print(
                            f"tsk-880: базу Redis {index} очистить не удалось: "
                            f"{type(exc).__name__}: {exc} (аренда истечёт сама через "
                            f"{LEASE_TTL_SECONDS // 3600} ч)",
                            file=sys.stderr,
                            flush=True,
                        )

                return RunRedis(
                    True,
                    url,
                    index,
                    f"Redis прогона — логическая база {index} из {total}",
                    _cleanup,
                )

        message = (
            f"свободной логической базы Redis нет (проверено {total - 1} штук). "
            "Прогон делит базу с соседями — ограничители частоты считают по общему "
            "IP, и чужой прогон может исчерпать окно"
        )
    except Exception as exc:  # noqa: BLE001
        message = (
            f"Redis не изолирован ({type(exc).__name__}: {exc}). Прогон работает на "
            "общей базе"
        )

    if mode == "on":
        raise RuntimeError(f"tsk-880: {message}")
    _logger.warning("tsk-880: %s", message)
    return RunRedis(False, base_url, None, message)
