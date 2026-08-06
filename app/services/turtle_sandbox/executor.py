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
_LINT_RUNNER_PATH = Path(__file__).resolve().parent / "lint_runner.py"

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

# tsk-302 (направление 1) review-gate находка Б1: анализ стиля кода (pylint/
# radon, не exec) сперва делил _SANDBOX_SEMAPHORE с исполнением turtle-кода —
# один и тот же ученик синхронно занимал слот исполнения, а следом ещё и слот
# анализа (до ~10с суммарно вместо ~5с), и при умеренной параллельной нагрузке
# (класс сдаёт одно задание) это провоцировало sandbox_busy у ДРУГИХ учеников
# ради второстепенной, невидимой ученику фичи. Отдельный, более узкий бюджет —
# анализ стиля не должен конкурировать за тот же ресурс с корректностной
# проверкой.
_LINT_CONCURRENCY_LIMIT = 2
_LINT_SEMAPHORE = threading.Semaphore(_LINT_CONCURRENCY_LIMIT)


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    trace: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None


@dataclass(frozen=True)
class CodeQualityResult:
    """Результат статического анализа стиля кода (tsk-302, направление 1)."""
    ok: bool
    report: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None


def _build_command(entry_script: Path) -> List[str]:
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
            python_executable, str(entry_script),
        ]
    return [python_executable, str(entry_script)]


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
    command = _build_command(_RUNNER_PATH)
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


def run_code_quality_check(code: str, *, timeout_sec: float = 5.0) -> CodeQualityResult:
    """
    Статический анализ стиля кода ученика (pylint/radon, tsk-302) в ТОЙ ЖЕ
    изоляции, что и `run_student_code` (`lint_runner.py` вместо `runner.py`,
    та же обёртка `unshare`), но с ОТДЕЛЬНЫМ, более узким бюджетом
    одновременных процессов (`_LINT_SEMAPHORE`, не `_SANDBOX_SEMAPHORE`) —
    см. комментарий у `_LINT_CONCURRENCY_LIMIT` (review-gate находка Б1,
    2026-08-06): второстепенный, невидимый ученику анализ не должен
    конкурировать за тот же слот, что и исполнение turtle-кода, иначе он
    провоцирует `sandbox_busy` у ДРУГИХ учеников.

    В отличие от `run_student_code`, код ученика здесь НЕ исполняется —
    pylint/radon разбирают только AST, — но процесс всё равно изолирован и
    ограничен по ресурсам (см. `lint_runner.py`): вход студенческий,
    непривилегированный, а pylint/astroid на патологическом вводе способны
    уйти в тяжёлый CPU/память путь.

    Не бросает исключений — сбой анализа (таймаут, авария процесса) не должен
    ронять приём ответа; вызывающая сторона получает `CodeQualityResult(ok=False, ...)`.
    """
    if not _LINT_SEMAPHORE.acquire(timeout=_SEMAPHORE_WAIT_SEC):
        return CodeQualityResult(
            ok=False, error="sandbox_busy",
            message="Песочница анализа кода перегружена.",
        )
    try:
        return _run_code_quality_check_locked(code, timeout_sec=timeout_sec)
    finally:
        _LINT_SEMAPHORE.release()


def _run_code_quality_check_locked(code: str, *, timeout_sec: float) -> CodeQualityResult:
    payload = json.dumps({"code": code})
    command = _build_command(_LINT_RUNNER_PATH)

    try:
        with tempfile.TemporaryDirectory(prefix="turtle_sandbox_lint_") as scratch_dir:
            # HOME/PYLINT_HOME/XDG_CACHE_HOME — в scratch, и вот почему (найдено
            # живым прогоном на проде 2026-08-06, tsk-302 этап 0). Процесс идёт под
            # `unshare --map-root-user`, то есть ВНУТРИ namespace он root, и pylint
            # пытается сохранить статистику в `/root/.cache/pylint/…stats`. Записи
            # туда нет → каждый анализ падал `PermissionError`, а отчёт превращался
            # в `{"error": "analysis_error"}`. На Windows (локальная разработка и
            # тесты) HOME доступен, поэтому дефект был не виден вовсе.
            # `--persistent=n` от этого не спасает: он про переиспользование
            # результатов между запусками, а не про сам факт записи файла.
            # Каталог удаляется вместе со scratch — мусор не копится.
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "PYTHONPATH": str(_PROJECT_ROOT),
                "HOME": scratch_dir,
                "PYLINT_HOME": scratch_dir,
                "XDG_CACHE_HOME": scratch_dir,
            }
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
                return CodeQualityResult(ok=False, error="timeout", message="Превышено время анализа кода.")
    except OSError as exc:
        # review-gate (2026-08-06) находка Б2: TemporaryDirectory/subprocess.run
        # способны бросить OSError (бинарь unshare/интерпретатор недоступен, нет
        # места на диске) помимо TimeoutExpired — сбой побочного анализа стиля не
        # должен ронять весь приём ответа ученика (тот же принцип, что и timeout).
        return CodeQualityResult(
            ok=False, error="sandbox_error",
            message=f"Анализ кода не запустился: {type(exc).__name__}: {exc}",
        )

    if proc.returncode != 0 or not proc.stdout.strip():
        stderr_tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        return CodeQualityResult(
            ok=False,
            error="sandbox_killed",
            message=f"Анализ кода завершился аварийно (код {proc.returncode}): {stderr_tail[0][:200]}",
        )

    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return CodeQualityResult(ok=False, error="bad_output", message="Не удалось разобрать результат анализа.")

    if not data.get("ok"):
        return CodeQualityResult(ok=False, error=data.get("error", "unknown"), message=data.get("message"))
    return CodeQualityResult(ok=True, report=data.get("report"))
