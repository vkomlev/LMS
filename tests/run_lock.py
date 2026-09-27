"""Машинный замок «один полный прогон тестов за раз» (tsk-1143).

Полный pytest LMS и полный vitest SPW съедают сотни мегабайт каждый; несколько
сессий-чипов, запустивших их разом на машине с 16 ГБ, роняли систему. Замок
общий с SPW (``D:\\Work\\SPW\\scripts\\full-test-lock.mjs``) — тот же файл и формат:
``%LOCALAPPDATA%\\test-run-lock\\full-test.lock`` с JSON ``{pid, project, cmd, started}``.

Берётся только для полного прогона (без явных путей к тестам и без ``-k``).
Замок мёртвого процесса считается брошенным и перехватывается.
Ожидание — ``TEST_LOCK_WAIT_SEC`` (по умолчанию 1200, 0 — сразу отказ);
обход — ``TEST_LOCK_OFF=1``.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

LOCK_FILE = (
    Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    / "test-run-lock"
    / "full-test.lock"
)


def _is_alive(pid: int) -> bool:
    """Жив ли процесс. На Windows — через OpenProcess: ``os.kill(pid, 0)`` там шлёт CTRL_C."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return kernel32.GetLastError() == 5  # ACCESS_DENIED — процесс есть
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_holder() -> dict | None:
    """Прочитать владельца замка; ``None``, если файла нет или он битый."""
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def is_full_run(test_args: list[str], keyword: str) -> bool:
    """Полный ли это прогон: пути, разобранные pytest (``config.args``), — только корень, и нет ``-k``."""
    if keyword:
        return False
    return all(Path(a.split("::")[0]).name in {"tests", "LMS", "."} for a in test_args)


def _release() -> None:
    """Снять замок, только если он наш."""
    holder = _read_holder()
    if holder and holder.get("pid") == os.getpid():
        LOCK_FILE.unlink(missing_ok=True)


def acquire_full_test_lock(test_args: list[str], keyword: str) -> None:
    """Взять замок для полного прогона или ждать; по истечении ожидания — отказ.

    Raises:
        pytest.Exit: код 3, если замок держит другой живой прогон дольше ожидания.
    """
    if os.environ.get("TEST_LOCK_OFF") == "1" or not is_full_run(test_args, keyword):
        return
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    info = {
        "pid": os.getpid(),
        "project": "LMS",
        "cmd": " ".join(sys.argv),
        "started": datetime.now(timezone.utc).isoformat(),
    }
    wait_sec = int(os.environ.get("TEST_LOCK_WAIT_SEC", "1200"))
    deadline = time.monotonic() + wait_sec
    announced = False
    while True:
        try:
            with LOCK_FILE.open("x", encoding="utf-8") as fh:
                json.dump(info, fh)
            break
        except FileExistsError:
            holder = _read_holder()
            if holder and holder.get("pid") == os.getpid():
                return  # conftest импортируется дважды за процесс — замок уже наш
            if not holder or not _is_alive(int(holder.get("pid", 0))):
                logger.warning("full-test-lock: брошенный замок %s снят", holder)
                LOCK_FILE.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                pytest.exit(
                    f"[full-test-lock] Отказ: на машине уже идёт полный прогон тестов "
                    f"({holder.get('project')}, pid {holder.get('pid')}, с {holder.get('started')}). "
                    "Два полных прогона разом съедают память и роняют систему (tsk-1143). "
                    "Дождитесь конца, прогоните один файл (pytest tests/test_x.py) или, "
                    "если вы точно одни, TEST_LOCK_OFF=1.",
                    returncode=3,
                )
            if not announced:
                sys.stderr.write(
                    f"[full-test-lock] Жду: идёт полный прогон {holder.get('project')} "
                    f"(pid {holder.get('pid')}); ожидание до {wait_sec} с.\n"
                )
                announced = True
            time.sleep(3)
    atexit.register(_release)
