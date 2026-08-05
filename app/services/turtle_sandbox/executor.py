# app/services/turtle_sandbox/executor.py
"""
Оркестрация запуска `runner.py` в изолированном процессе (tsk-412).

На проде (Linux) команда оборачивается в `unshare --user --net --map-root-user`:
непривилегированный user-namespace + отдельный network-namespace без маршрутов
наружу (student-код не может обратиться в сеть, даже если AST-страж что-то
пропустил). На Windows (локальная разработка) `unshare` недоступен — код
запускается напрямую; это ДОСТАТОЧНО для тестов логики стража/стаба, но НЕ
для прод-исполнения — на проде сервис всегда Linux (см. runner.py и
`docs/qa/2026-08-05-tsk412-*.md` про проверку окружения).

Вызывающая сторона (CheckingService) обязана звать `run_student_code` через
`asyncio.to_thread` — это синхронная блокирующая функция (subprocess.run).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_RUNNER_PATH = Path(__file__).resolve().parent / "runner.py"

# Запас поверх timeout_sec самой задачи — время на старт unshare/интерпретатора.
_SUBPROCESS_OVERHEAD_SEC = 3.0

# tsk-412 review-gate находка P1: ничего не ограничивало число ОДНОВРЕМЕННЫХ
# subprocess-песочниц. На проде 2 vCPU / ~1.3GB свободной памяти (см. review-
# артефакт) — несколько параллельных отправок (или один ученик, быстро жмущий
# «отправить») удержат все потоки общего ThreadPoolExecutor'а FastAPI (через
# asyncio.to_thread) на несколько секунд каждый и рискуют устроить OOM. Лимит —
# per-process (каждый uvicorn-воркер прод считает свой): при недоступности
# слота в разумное время — честная ошибка «занято», а не очередь без границ.
_SANDBOX_CONCURRENCY_LIMIT = 3
_SANDBOX_SEMAPHORE = threading.Semaphore(_SANDBOX_CONCURRENCY_LIMIT)
_SEMAPHORE_WAIT_SEC = 5.0


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    trace: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None


def _build_command() -> List[str]:
    python_executable = sys.executable
    if sys.platform.startswith("linux"):
        # --pid --fork: отдельный PID-namespace (процесс не видит и не может
        # сигналить другие процессы хоста, включая сам FastAPI-воркер) —
        # проверено вручную на проде (2026-08-05), дешёвая доп. изоляция поверх
        # --net/--user. --fork обязателен с --pid: unshare должен породить
        # ребёнка ВНУТРИ нового namespace, иначе сам процесс unshare остаётся
        # снаружи и PID 1 в новом ns никогда не запускается.
        return [
            "unshare", "--user", "--net", "--pid", "--fork", "--map-root-user", "--",
            python_executable, str(_RUNNER_PATH),
        ]
    return [python_executable, str(_RUNNER_PATH)]


def run_student_code(
    code: str,
    *,
    random_seed: Optional[int],
    synthetic_clicks: List[List[float]],
    max_steps: int,
    timeout_sec: float,
) -> SandboxResult:
    """
    Исполняет код ученика в песочнице, возвращает трассу либо категоризированную
    ошибку. Ограничивает число ОДНОВРЕМЕННЫХ песочниц семафором (см. модульный
    докстринг константы выше) — при перегрузке возвращает `sandbox_busy`, не
    ставит вызовы в неограниченную очередь.
    """
    if not _SANDBOX_SEMAPHORE.acquire(timeout=_SEMAPHORE_WAIT_SEC):
        return SandboxResult(
            ok=False, error="sandbox_busy",
            message="Песочница перегружена — попробуйте отправить ответ ещё раз через несколько секунд.",
        )
    try:
        return _run_student_code_locked(
            code,
            random_seed=random_seed,
            synthetic_clicks=synthetic_clicks,
            max_steps=max_steps,
            timeout_sec=timeout_sec,
        )
    finally:
        _SANDBOX_SEMAPHORE.release()


def _run_student_code_locked(
    code: str,
    *,
    random_seed: Optional[int],
    synthetic_clicks: List[List[float]],
    max_steps: int,
    timeout_sec: float,
) -> SandboxResult:
    payload = json.dumps({
        "code": code,
        "seed": random_seed,
        "synthetic_clicks": synthetic_clicks,
        "max_steps": max_steps,
    })
    command = _build_command()
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(_PROJECT_ROOT),
    }

    with tempfile.TemporaryDirectory(prefix="turtle_sandbox_") as scratch_dir:
        try:
            proc = subprocess.run(
                command,
                input=payload,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec + _SUBPROCESS_OVERHEAD_SEC,
                env=env,
                cwd=scratch_dir,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(ok=False, error="timeout", message="Превышено время исполнения программы.")

    if proc.returncode != 0 or not proc.stdout.strip():
        stderr_tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        return SandboxResult(
            ok=False,
            error="sandbox_killed",
            message=f"Песочница завершилась аварийно (код {proc.returncode}): {stderr_tail[0][:200]}",
        )

    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return SandboxResult(ok=False, error="bad_output", message="Не удалось разобрать результат песочницы.")

    if not data.get("ok"):
        return SandboxResult(ok=False, error=data.get("error", "unknown"), message=data.get("message"))
    return SandboxResult(ok=True, trace=data.get("trace"))
