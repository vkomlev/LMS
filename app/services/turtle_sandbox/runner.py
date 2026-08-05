# app/services/turtle_sandbox/runner.py
"""
Точка входа песочницы (tsk-412) — исполняется ОТДЕЛЬНЫМ OS-процессом
(на проде — под `unshare --user --net --map-root-user`, см. `executor.py`).

Протокол: один JSON-объект на stdin
    {"code": str, "seed": int|null, "synthetic_clicks": [[x,y],...], "max_steps": int}
и один JSON-объект на stdout:
    {"ok": true, "trace": {...}}
    {"ok": false, "error": "<категория>", "message": "<для лога/фидбека>"}

Файл намеренно не импортирует ничего из `app.*` кроме двух соседних модулей
песочницы (guard/stub_turtle) — оба чистый stdlib, без FastAPI/SQLAlchemy/БД.
Чем меньше загружено в процессе, исполняющем код ученика, тем меньше
поверхность атаки при гипотетическом обходе AST-стража.
"""

from __future__ import annotations

import json
import sys


def _set_resource_limits() -> None:
    """Жёсткие лимиты процесса (POSIX only — на Windows это no-op для тестов)."""
    try:
        import resource
    except ImportError:
        return  # Windows: локальная разработка/тесты, прод всегда Linux.

    try:
        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    except (ValueError, OSError):
        pass
    try:
        limit_bytes = 256 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    except (ValueError, OSError):
        pass


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def main() -> None:
    # Явная UTF-8 кодировка stdin/stdout: на Windows (локальная разработка/тесты)
    # sys.stdin/stdout по умолчанию используют системную кодировку (cp1251),
    # а условия задач и сообщения об ошибках — кириллица. На проде (Linux,
    # UTF-8 локаль) это no-op.
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    _set_resource_limits()

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _emit({"ok": False, "error": "bad_input", "message": str(exc)})
        return

    code = payload.get("code", "")
    seed = payload.get("seed")
    synthetic_clicks = payload.get("synthetic_clicks") or []
    max_steps = int(payload.get("max_steps") or 5000)

    # Импорт после resource limits: сами модули — чистый Python, но лимиты
    # должны действовать на ВЕСЬ процесс, включая их загрузку. Корень проекта
    # обязан быть в PYTHONPATH — это гарантирует executor.py при запуске.
    from app.services.turtle_sandbox.guard import check_code_is_safe, GuardViolation
    from app.services.turtle_sandbox.stub_turtle import (
        _Session, StepLimitExceeded, TurtleSandboxError, build_restricted_globals,
    )

    try:
        check_code_is_safe(code)
    except GuardViolation as exc:
        _emit({"ok": False, "error": "forbidden_construct", "message": str(exc)})
        return
    except SyntaxError as exc:
        _emit({"ok": False, "error": "syntax_error", "message": str(exc)})
        return

    if seed is not None:
        import random
        random.seed(seed)

    session = _Session(max_steps=max_steps, synthetic_clicks=synthetic_clicks)
    restricted_globals = build_restricted_globals(session)

    try:
        compiled = compile(code, "<student_code>", "exec")
        exec(compiled, restricted_globals)  # noqa: S102 — намеренно, это и есть песочница
        session.replay_pending_events()
    except StepLimitExceeded as exc:
        _emit({"ok": False, "error": "step_limit_exceeded", "message": str(exc)})
        return
    except TurtleSandboxError as exc:
        _emit({"ok": False, "error": "turtle_usage_error", "message": str(exc)})
        return
    except SyntaxError as exc:
        _emit({"ok": False, "error": "syntax_error", "message": str(exc)})
        return
    except BaseException as exc:  # noqa: BLE001 — код ученика может кинуть что угодно
        _emit({"ok": False, "error": "runtime_error", "message": f"{type(exc).__name__}: {exc}"})
        return

    _emit({"ok": True, "trace": session.export_trace()})


if __name__ == "__main__":
    main()
