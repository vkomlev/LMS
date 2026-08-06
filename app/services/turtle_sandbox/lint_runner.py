# app/services/turtle_sandbox/lint_runner.py
"""
Точка входа песочницы статического анализа качества кода (tsk-302, направление 1).

Протокол, как у `runner.py` (tsk-412): один JSON-объект на stdin
    {"code": str}
и один JSON-объект на stdout:
    {"ok": true, "report": {...}}
    {"ok": false, "error": "<категория>", "message": "<для лога>"}

В отличие от `runner.py`, код ученика здесь НЕ ИСПОЛНЯЕТСЯ — pylint/radon
разбирают только AST (`ast.parse`, без `exec`). Тем не менее файл запускается
как отдельный OS-процесс той же обвязкой `executor.py` (на проде — под
`unshare`), потому что вход — код ученика: pylint/astroid на патологическом
вводе (глубокая вложенность, огромные выражения) способны уйти в тяжёлый
CPU/память путь, а RLIMIT_CPU/RLIMIT_AS процесса — дешёвая защита от этого.

Оценивается только СТИЛЬ (магические числа, сложность, длина/число
аргументов функций, имена) — не корректность (её проверяет `runner.py`
исполнением в песочнице и сравнением трассы).
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from typing import Any, Dict, List


def _set_resource_limits() -> None:
    """Лимиты процесса анализа (POSIX only — на Windows no-op, прод всегда Linux).

    Мягче, чем у `runner.py._set_resource_limits`: там лимиты рассчитаны на
    интерпретатор, исполняющий код ученика (RLIMIT_FSIZE=0 — код не должен
    писать файлы). Здесь код не исполняется, а pylint/astroid сами по себе
    требовательнее к памяти, чем стаб turtle — берём RLIMIT_AS пошире и не
    трогаем RLIMIT_FSIZE/RLIMIT_NPROC (pylint не форкает при `--jobs=1`,
    но может использовать собственный дисковый кеш инференса).
    """
    try:
        import resource
    except ImportError:
        return  # Windows: локальная разработка/тесты, прод всегда Linux.

    try:
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    except (ValueError, OSError):
        pass
    try:
        limit_bytes = 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    except (ValueError, OSError):
        pass


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _run_radon(code: str) -> Dict[str, Any]:
    from radon.complexity import cc_visit, cc_rank
    from radon.metrics import mi_visit
    from radon.raw import analyze as radon_raw_analyze

    complexity: List[Dict[str, Any]] = []
    for block in cc_visit(code):
        complexity.append({
            "name": block.name,
            "kind": type(block).__name__,  # "Function" | "Class"
            "complexity": block.complexity,
            "rank": cc_rank(block.complexity),
        })

    raw = radon_raw_analyze(code)
    return {
        "complexity": complexity,
        "maintainability_index": round(mi_visit(code, multi=True), 2),
        "raw": {
            "loc": raw.loc,
            "lloc": raw.lloc,
            "sloc": raw.sloc,
            "comments": raw.comments,
            "blank": raw.blank,
        },
    }


# Пилот tsk-302: магические числа, сложность (дублирует radon, но с точным
# местом), длина/число аргументов функций, читаемость имён. Полный список
# доступных проверок шире — сознательно взят узкий набор под задачу, а не
# "всё, что есть в pylint" (anti-bloat: лишние правила только шумят методисту).
_PYLINT_ENABLE = (
    "invalid-name,"
    "too-many-branches,"
    "too-many-statements,"
    "too-many-locals,"
    "too-many-arguments,"
    "magic-value-comparison"
)


def _run_pylint(code: str) -> Dict[str, Any]:
    from pylint.lint import Run
    from pylint.reporters.json_reporter import JSON2Reporter

    # dir="." кладёт файл в cwd процесса — это scratch_dir, который executor.py
    # создаёт как tempfile.TemporaryDirectory и удаляет целиком после того, как
    # subprocess.run вернётся; отдельного os.unlink здесь поэтому не требуется.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8", dir="."
    ) as f:
        f.write(code)
        tmp_path = f.name

    buf = io.StringIO()
    reporter = JSON2Reporter(buf)
    args = [
        tmp_path,
        "--disable=all",
        f"--enable={_PYLINT_ENABLE}",
        "--load-plugins=pylint.extensions.magic_value",
        "--jobs=1",
        "--persistent=n",
    ]
    run = Run(args, reporter=reporter, exit=False)

    score = getattr(run.linter.stats, "global_note", None)
    try:
        parsed = json.loads(buf.getvalue())
    except json.JSONDecodeError:
        parsed = {"messages": []}

    messages = [
        {
            "symbol": m.get("symbol"),
            "message": m.get("message"),
            "type": m.get("type"),
            "obj": m.get("obj") or None,
            "line": m.get("line"),
            "column": m.get("column"),
        }
        for m in parsed.get("messages", [])
    ]
    return {"score": round(score, 2) if score is not None else None, "messages": messages}


def main() -> None:
    # Явная UTF-8: на Windows (локальная разработка) stdin/stdout по умолчанию
    # используют системную кодировку, а сообщения/имена — кириллица.
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
    if not code.strip():
        _emit({"ok": False, "error": "empty_code", "message": "Код пуст."})
        return

    try:
        import ast
        ast.parse(code, mode="exec")
    except SyntaxError as exc:
        _emit({"ok": False, "error": "syntax_error", "message": str(exc)})
        return

    try:
        report = {
            "radon": _run_radon(code),
            "pylint": _run_pylint(code),
        }
    except BaseException as exc:  # noqa: BLE001 — анализ не должен уронить процесс без ответа
        _emit({"ok": False, "error": "analysis_error", "message": f"{type(exc).__name__}: {exc}"})
        return

    _emit({"ok": True, "report": report})


if __name__ == "__main__":
    main()
