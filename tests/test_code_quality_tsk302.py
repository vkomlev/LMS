# tests/test_code_quality_tsk302.py
"""
Тесты статического анализа качества/стиля кода ученика (tsk-302, направление 1):
запуск pylint/radon в изоляции песочницы turtle_sandbox (executor.run_code_quality_check),
сервисная обёртка (code_quality_service), интеграция с _check_turtle_sim и защита от
утечки отчёта в ответ ученику (CheckResult).

Не требуют БД — как и test_turtle_sandbox_tsk412.py, реальный subprocess (без unshare
на Windows), но реальный pylint/radon из .venv.
"""
from __future__ import annotations

import pytest

from app.schemas.checking import CheckResult
from app.services.code_quality_service import analyze_student_code_quality
from app.services.turtle_sandbox.executor import run_code_quality_check

CLEAN_CODE = """
import turtle

def draw_square(t, size):
    for _ in range(4):
        t.forward(size)
        t.right(90)

t = turtle.Turtle()
draw_square(t, 50)
turtle.done()
"""

MAGIC_NUMBER_CODE = """
import turtle

t = turtle.Turtle()
for i in range(4):
    if i == 42:
        t.forward(999)
    t.forward(50)
    t.right(90)
"""

DEEPLY_NESTED_CODE = """
def f(a, b, c, d, e, x, y):
    if a == 1:
        if b == 2:
            if c == 3:
                if d == 4:
                    if e == 5:
                        return x + y
    return 0
"""


# ---------- executor.run_code_quality_check (subprocess-уровень) ----------

def test_run_code_quality_check_ok_for_clean_code() -> None:
    result = run_code_quality_check(CLEAN_CODE, timeout_sec=5.0)
    assert result.ok, result.message
    assert result.report is not None
    assert "radon" in result.report
    assert "pylint" in result.report
    assert result.report["pylint"]["score"] is not None
    # draw_square действительно найдена радоном как функция с CC>=1.
    names = [c["name"] for c in result.report["radon"]["complexity"]]
    assert "draw_square" in names


def test_run_code_quality_check_flags_magic_number() -> None:
    result = run_code_quality_check(MAGIC_NUMBER_CODE, timeout_sec=5.0)
    assert result.ok, result.message
    symbols = {m["symbol"] for m in result.report["pylint"]["messages"]}
    assert "magic-value-comparison" in symbols


def test_run_code_quality_check_flags_deep_nesting_and_arg_count() -> None:
    result = run_code_quality_check(DEEPLY_NESTED_CODE, timeout_sec=5.0)
    assert result.ok, result.message
    symbols = {m["symbol"] for m in result.report["pylint"]["messages"]}
    assert "too-many-arguments" in symbols
    # radon видит вложенные if как рост цикломатической сложности.
    complexity = result.report["radon"]["complexity"]
    assert complexity[0]["complexity"] >= 5


def test_run_code_quality_check_syntax_error_is_reported_not_crashed() -> None:
    result = run_code_quality_check("def f(:\n    pass", timeout_sec=5.0)
    assert not result.ok
    assert result.error == "syntax_error"


def test_run_code_quality_check_empty_code_reports_error_not_crash() -> None:
    result = run_code_quality_check("   \n", timeout_sec=5.0)
    assert not result.ok
    assert result.error == "empty_code"


def test_run_code_quality_check_oserror_is_reported_not_crashed(monkeypatch: pytest.MonkeyPatch) -> None:
    # review-gate (2026-08-06) находка Б2: subprocess.run/TemporaryDirectory
    # способны бросить OSError помимo TimeoutExpired (бинарь недоступен, нет
    # места на диске) — это не должно пробрасываться наружу.
    import subprocess as subprocess_module

    def _raise_oserror(*args, **kwargs):
        raise FileNotFoundError("unshare: no such file or directory")

    monkeypatch.setattr(subprocess_module, "run", _raise_oserror)
    result = run_code_quality_check("x = 1\n", timeout_sec=5.0)
    assert not result.ok
    assert result.error == "sandbox_error"


# ---------- code_quality_service (сервисный уровень) ----------

def test_analyze_student_code_quality_returns_none_for_empty_code() -> None:
    assert analyze_student_code_quality("") is None
    assert analyze_student_code_quality("   \n\t") is None


def test_analyze_student_code_quality_returns_report_for_real_code() -> None:
    report = analyze_student_code_quality(CLEAN_CODE)
    assert report is not None
    assert "radon" in report
    assert "pylint" in report
    assert "error" not in report


def test_analyze_student_code_quality_degrades_gracefully_on_sandbox_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # Сбой анализа (таймаут/авария subprocess) не должен ронять приём ответа —
    # сервис возвращает отчёт с error, а не бросает исключение.
    from app.services.turtle_sandbox.executor import CodeQualityResult

    def _fake_run_code_quality_check(code: str, *, timeout_sec: float = 5.0) -> CodeQualityResult:
        return CodeQualityResult(ok=False, error="timeout", message="Превышено время анализа кода.")

    monkeypatch.setattr(
        "app.services.turtle_sandbox.executor.run_code_quality_check",
        _fake_run_code_quality_check,
    )
    report = analyze_student_code_quality(CLEAN_CODE)
    assert report == {"error": "timeout", "message": "Превышено время анализа кода."}


# ---------- Защита от утечки отчёта ученику (решение оператора tsk-302) ----------

def test_check_result_schema_has_no_code_quality_field() -> None:
    """
    CheckResult эхо-возвращается ученику в POST /attempts/{id}/answers
    (AttemptAnswerResult.check_result, см. app/api/v1/attempts.py). Отчёт по
    качеству кода НЕ должен попадать в эту схему ни сейчас, ни при будущих
    правках — иначе он утечёт ученику в обход решения оператора "видимость
    только teacher/methodist" (2026-08-06). Отчёт передаётся отдельно, напрямую
    в task_results.metrics (см. app/api/v1/attempts.py, code_quality_metrics).
    """
    assert "code_quality" not in CheckResult.model_fields
    for field_name in CheckResult.model_fields:
        assert "quality" not in field_name.lower(), (
            f"CheckResult.{field_name} похоже на утечку code_quality в ответ ученику"
        )
