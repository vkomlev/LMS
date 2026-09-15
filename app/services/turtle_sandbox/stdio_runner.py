# app/services/turtle_sandbox/stdio_runner.py
"""
Точка входа песочницы в режиме «stdin → stdout» (tsk-953) — исполняется
ОТДЕЛЬНЫМ OS-процессом (на проде — под `unshare --user --net --pid`, см.
`executor.py`), по одному процессу на один тест.

Протокол: один JSON-объект на stdin
    {"code": str, "stdin": str, "max_output_chars": int}
и один JSON-объект на stdout:
    {"ok": true, "stdout": "<что напечатала программа>"}
    {"ok": false, "error": "<категория>", "message": "<для лога/фидбека>"}

Категории ошибок (каждая — отдельное сообщение ученику в CheckingService):
    syntax_error, forbidden_construct, input_exhausted (программа просит ввод,
    когда данные закончились — чаще всего цикл «до 0» без чтения нуля или
    чтение N+1 чисел), output_limit_exceeded (печать в бесконечном цикле),
    runtime_error (любое исключение: ValueError на int('abc'), деление на
    ноль, IndexError, …).

Отличие от `runner.py` (черепаха): вместо заглушки turtle подставляются
`input`, читающий заранее поданные строки, и `print`, пишущий в буфер с
потолком. Всё остальное — тот же стдлиб-минимум и те же лимиты процесса.
Ученик не видит своего stdout напрямую (решение оператора 2026-09-15):
песочница не должна быть каналом вывода чего-либо с сервера, поэтому наружу
уходит только вердикт сравнения.
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any, Dict, List

from app.services.turtle_sandbox.runner import _emit, _set_resource_limits


class OutputLimitExceeded(RuntimeError):
    """Программа напечатала больше, чем разрешено правилом."""


class _StdinLines:
    """Замена `input()`: отдаёт заранее поданные строки, в конце — EOFError.

    Как настоящий Python на исчерпанном stdin: `input()` поднимает `EOFError`,
    а не блокируется. Это важно для программ с бесконечным `input()` — они
    падают мгновенно категорией `input_exhausted`, а не ждут таймаута.
    """

    def __init__(self, text: str) -> None:
        # Как sys.stdin.readline(): строка — до перевода строки; последняя без
        # перевода тоже считается строкой; пустой хвост после финального \n —
        # нет (иначе «3\n10\n» дал бы третью, пустую строку).
        self._lines: List[str] = text.split("\n")
        if self._lines and self._lines[-1] == "":
            self._lines.pop()
        self._pos = 0

    def readline(self, prompt: Any = None) -> str:
        if self._pos >= len(self._lines):
            raise EOFError("Программа запросила ввод, а данные закончились.")
        line = self._lines[self._pos]
        self._pos += 1
        # input() отрезает перевод строки; \r (Windows-ввод в тестах) — тоже.
        return line.rstrip("\r")


def _make_print(buffer: io.StringIO, max_chars: int):
    def _print(*args: Any, sep: Any = " ", end: Any = "\n", file: Any = None, flush: Any = False) -> None:
        # `file=` игнорируется намеренно: никакого другого потока у программы нет.
        if sep is None:
            sep = " "
        if end is None:
            end = "\n"
        buffer.write(str(sep).join(str(a) for a in args))
        buffer.write(str(end))
        if buffer.tell() > max_chars:
            raise OutputLimitExceeded(
                f"Программа напечатала больше {max_chars} символов — похоже на бесконечный цикл."
            )
    return _print


def build_stdio_globals(stdin_text: str, buffer: io.StringIO, max_chars: int) -> Dict[str, Any]:
    """globals() для exec() кода ученика: безопасные builtins + input/print.

    Набор builtins — тот же, что у черепашьей заглушки, плюс то, без чего не
    пишут консольную программу: `input`, `print`, `ord`/`chr`, `iter`/`next`,
    `repr`/`format`, `EOFError`. `open`, `exec`, `getattr`, `type`, `object`
    и прочее, дающее рефлексию или доступ к системе, по-прежнему не определены.
    """
    stdin = _StdinLines(stdin_text)
    safe_builtins: Dict[str, Any] = {
        "__import__": _stdio_import,
        "abs": abs, "min": min, "max": max, "round": round, "len": len,
        "range": range, "enumerate": enumerate, "zip": zip, "map": map,
        "filter": filter, "sorted": sorted, "reversed": reversed, "sum": sum,
        "any": any, "all": all, "divmod": divmod, "pow": pow,
        "int": int, "float": float, "str": str, "bool": bool, "complex": complex,
        "list": list, "tuple": tuple, "dict": dict, "set": set, "frozenset": frozenset,
        "isinstance": isinstance, "ord": ord, "chr": chr, "bin": bin, "hex": hex, "oct": oct,
        "iter": iter, "next": next, "repr": repr, "format": format, "hash": hash,
        "input": stdin.readline,
        "print": _make_print(buffer, max_chars),
        "True": True, "False": False, "None": None,
        "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
        "RuntimeError": RuntimeError, "ZeroDivisionError": ZeroDivisionError,
        "StopIteration": StopIteration, "IndexError": IndexError, "KeyError": KeyError,
        "ArithmeticError": ArithmeticError, "OverflowError": OverflowError,
        "EOFError": EOFError, "NameError": NameError, "AttributeError": AttributeError,
        "__build_class__": __builtins__["__build_class__"] if isinstance(__builtins__, dict) else __builtins__.__build_class__,
    }
    return {"__builtins__": safe_builtins, "__name__": "__main__"}


def _stdio_import(
    name: str,
    globals: Any = None,  # noqa: A002 — сигнатура builtins.__import__
    locals: Any = None,  # noqa: A002
    fromlist: Any = (),
    level: int = 0,
) -> Any:
    """Замена `__import__`: пропускает только модули профиля stdio (см. guard.py)."""
    from app.services.turtle_sandbox.guard import STDIO_ALLOWED_IMPORT_MODULES  # noqa: PLC0415

    root = name.split(".")[0]
    if root not in STDIO_ALLOWED_IMPORT_MODULES:
        raise ImportError(f"Импорт модуля '{name}' запрещён в этом задании.")
    import builtins as _builtins  # noqa: PLC0415

    return _builtins.__import__(name, globals, locals, fromlist, level)


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    _set_resource_limits()

    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _emit({"ok": False, "error": "bad_input", "message": str(exc)})
        return

    code = payload.get("code", "")
    stdin_text = payload.get("stdin") or ""
    max_chars = int(payload.get("max_output_chars") or 20_000)

    from app.services.turtle_sandbox.guard import GuardViolation, check_code_is_safe  # noqa: PLC0415

    try:
        check_code_is_safe(code, profile="stdio")
    except GuardViolation as exc:
        _emit({"ok": False, "error": "forbidden_construct", "message": str(exc)})
        return
    except SyntaxError as exc:
        _emit({"ok": False, "error": "syntax_error", "message": str(exc)})
        return

    buffer = io.StringIO()
    restricted_globals = build_stdio_globals(stdin_text, buffer, max_chars)

    try:
        compiled = compile(code, "<student_code>", "exec")
        exec(compiled, restricted_globals)  # noqa: S102 — намеренно, это и есть песочница
    except OutputLimitExceeded as exc:
        _emit({"ok": False, "error": "output_limit_exceeded", "message": str(exc)})
        return
    except EOFError as exc:
        _emit({"ok": False, "error": "input_exhausted", "message": str(exc)})
        return
    except SyntaxError as exc:
        _emit({"ok": False, "error": "syntax_error", "message": str(exc)})
        return
    except BaseException as exc:  # noqa: BLE001 — код ученика может кинуть что угодно
        _emit({"ok": False, "error": "runtime_error", "message": f"{type(exc).__name__}: {exc}"})
        return

    _emit({"ok": True, "stdout": buffer.getvalue()})


if __name__ == "__main__":
    main()
