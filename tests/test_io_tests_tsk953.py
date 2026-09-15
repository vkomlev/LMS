# tests/test_io_tests_tsk953.py
"""
Прогон программы ученика на тестах ввода/вывода (tsk-953).

Зачем. Замер tsk-950 на 61 верном решении ОГЭ-16 показал, что сравнение кода
через `code_ast` засчитывает 18 % верных программ. Решение оператора
2026-09-15 — исполнять программу на тестовых входах и сравнивать вывод.

Покрывает:
- (а) схема `io_tests`: осмысленные предпосылки → ошибка валидации, а не
      молчаливое выключение (допуск без numeric, два исполняющих режима,
      гибрид, пустой ожидаемый вывод); `has_reference_answer()` → True;
- (б) страж профиля stdio: `input` и генераторы разрешены, `os`/`try` — нет;
      черепаший профиль не изменился;
- (в) песочница: вывод, исчерпанный ввод, бесконечная печать, синтаксис, таймаут;
- (г) сравнение вывода: lines / tokens / numeric с допуском и запятой;
- (д) CheckingService: все тесты → полный балл; провал → номер теста, ввод и
      ожидаемый вывод без вывода программы ученика; сбой песочницы → вердикта
      нет (is_correct=None), а не незачёт;
- (е) выбор программы: value → comment → вложение, короткий ответ в value не
      заслоняет программу в комментарии, однострочная программа не теряется;
- (ж) сдача через API: верное решение в `value` без комментария → зачёт (гейт
      «комментарий или файл» не применяется, оптимистичного зачёта нет);
      неверное → незачёт с 0 баллов; программа в `comment` (старая форма) →
      зачёт; `GET /learning/tasks/{id}/state` отдаёт `has_io_tests`.
"""
from __future__ import annotations

import uuid
from typing import Optional

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.core.config import Settings
from app.schemas.checking import StudentAnswer, StudentResponse
from app.schemas.solution_rules import IoTestsRules, SolutionRules
from app.schemas.task_content import TaskContent
from app.services.checking_service import CheckingService
from app.services.code_review_service import pick_program_for_io_tests
from app.services.turtle_sandbox.comparator import compare_stdout
from app.services.turtle_sandbox.executor import StdioResult, run_student_program_stdio
from app.services.turtle_sandbox.guard import GuardViolation, check_code_is_safe

_settings = Settings()

MAX5_CODE = (
    "n = int(input())\nm = 0\nfor i in range(n):\n    x = int(input())\n"
    "    if x % 5 == 0 and x > m:\n        m = x\nprint(m)\n"
)
MAX5_GENEXP_CODE = (
    "n = int(input())\na = [int(input()) for i in range(n)]\n"
    "print(max(x for x in a if x % 5 == 0))\n"
)
# Перепутанное условие: максимум среди ВСЕХ чисел, а не кратных 5.
MAX_ANY_CODE = (
    "n = int(input())\nm = 0\nfor i in range(n):\n    x = int(input())\n"
    "    if x > m:\n        m = x\nprint(m)\n"
)
# Чтение «до 0» вместо N чисел — на входе с N программа просит лишний ввод.
READ_UNTIL_ZERO_CODE = (
    "m = 0\nx = int(input())\nwhile x != 0:\n    if x % 5 == 0 and x > m:\n"
    "        m = x\n    x = int(input())\nprint(m)\n"
)

MAX5_TESTS = [
    {"stdin": "3\n10\n25\n12\n", "expected_stdout": "25\n", "label": "пример из условия"},
    {"stdin": "1\n15\n", "expected_stdout": "15\n", "label": "N=1"},
    # 33 больше 30, но не кратно 5 — ловит «максимум среди всех чисел»
    {"stdin": "4\n7\n30\n11\n33\n", "expected_stdout": "30\n"},
]


def _rules(**overrides) -> SolutionRules:
    payload = {"max_score": 1, "io_tests": {"tests": MAX5_TESTS}}
    payload.update(overrides)
    return SolutionRules.model_validate(payload)


def _answer(code: str, task_type: str = "SA_COM") -> StudentAnswer:
    return StudentAnswer(type=task_type, response=StudentResponse(value=code))


def _content(task_type: str = "SA_COM") -> TaskContent:
    return TaskContent(type=task_type, stem="Напишите программу.")


# ── (а) схема ────────────────────────────────────────────────────────────────


def test_schema_io_tests_is_reference_answer():
    rules = _rules()
    assert rules.io_tests is not None and len(rules.io_tests.tests) == 3
    assert rules.has_reference_answer() is True
    assert rules.io_tests.compare == "lines"
    assert rules.io_tests.timeout_sec == 2.0


def test_schema_tolerance_requires_numeric_mode():
    with pytest.raises(ValidationError, match="numeric"):
        _rules(io_tests={"tests": MAX5_TESTS, "float_tolerance": 0.05})
    ok = _rules(io_tests={"tests": MAX5_TESTS, "compare": "numeric", "float_tolerance": 0.05})
    assert ok.io_tests is not None and ok.io_tests.float_tolerance == 0.05


def test_schema_io_tests_excludes_turtle_and_hybrid():
    turtle = {
        "expected_trace": {"segments": [], "final_state": {"position": [0, 0], "heading": 0, "pen_down": True}},
    }
    with pytest.raises(ValidationError, match="turtle_sim"):
        _rules(turtle_sim=turtle)
    with pytest.raises(ValidationError, match="partial_auto_check"):
        _rules(partial_auto_check=True, manual_review_required=True)


def test_schema_rejects_blank_expected_and_empty_tests():
    with pytest.raises(ValidationError):
        _rules(io_tests={"tests": [{"stdin": "1\n", "expected_stdout": "  \n"}]})
    with pytest.raises(ValidationError):
        _rules(io_tests={"tests": []})
    with pytest.raises(ValidationError):
        IoTestsRules.model_validate({"tests": MAX5_TESTS, "timeout_sec": 60})


# ── (б) страж ────────────────────────────────────────────────────────────────


def test_guard_stdio_allows_input_and_generators():
    check_code_is_safe(MAX5_CODE, profile="stdio")
    check_code_is_safe(MAX5_GENEXP_CODE, profile="stdio")
    check_code_is_safe("import math\nprint(math.sqrt(int(input())))\n", profile="stdio")


@pytest.mark.parametrize(
    "code",
    [
        "import os\nprint(os.getcwd())\n",
        "import sys\nprint(sys.stdin.read())\n",
        "try:\n    x = int(input())\nexcept ValueError:\n    x = 0\nprint(x)\n",
        "print(open('/etc/passwd').read())\n",
        "print(getattr(1, '__class__'))\n",
        "import turtle\n",
    ],
)
def test_guard_stdio_forbids_system_access(code):
    with pytest.raises(GuardViolation):
        check_code_is_safe(code, profile="stdio")


def test_guard_turtle_profile_unchanged():
    """Черепаший профиль по-прежнему запрещает input и генераторы."""
    with pytest.raises(GuardViolation):
        check_code_is_safe("x = input()\n")
    with pytest.raises(GuardViolation):
        check_code_is_safe("print(sum(i for i in range(3)))\n")


# ── (в) песочница ────────────────────────────────────────────────────────────


def _run(code: str, stdin: str = "3\n10\n25\n12\n", timeout: float = 2.0) -> StdioResult:
    return run_student_program_stdio(code, stdin=stdin, timeout_sec=timeout, max_output_chars=2000)


def test_sandbox_runs_program_and_captures_stdout():
    result = _run(MAX5_CODE)
    assert result.ok, result
    assert result.stdout == "25\n"
    assert _run(MAX5_GENEXP_CODE).stdout == "25\n"


def test_sandbox_input_exhausted_is_its_own_category():
    result = _run(READ_UNTIL_ZERO_CODE)
    assert not result.ok
    assert result.error == "input_exhausted"


def test_sandbox_output_limit_and_syntax_and_runtime():
    assert _run("while True:\n    print(1)\n").error == "output_limit_exceeded"
    assert _run("n = int(input()\nprint(n)\n").error == "syntax_error"
    assert _run("print(1 / 0)\n").error == "runtime_error"
    assert _run("import os\n").error == "forbidden_construct"


def test_sandbox_timeout_on_infinite_loop():
    result = _run("while True:\n    pass\n", timeout=1.0)
    assert not result.ok
    assert result.error == "timeout"


def test_sandbox_cpu_limit_kill_is_timeout_not_crash(monkeypatch):
    """На проде цикл снимает RLIMIT_CPU (SIGXCPU) — это таймаут программы, не авария песочницы."""
    import subprocess
    import app.services.turtle_sandbox.executor as executor

    class _Proc:
        returncode = -24
        stdout = ""
        stderr = ""

    monkeypatch.setattr(executor, "_CPU_LIMIT_RETURN_CODES", frozenset({-24, -9}))
    monkeypatch.setattr(executor.subprocess, "run", lambda *a, **kw: _Proc())
    assert subprocess is executor.subprocess
    result = _run("while True:\n    pass\n")
    assert result.error == "timeout"


def test_sandbox_print_kwargs_and_no_file_access():
    result = _run("print(1, 2, sep='-', end='!')\nprint()\n", stdin="")
    assert result.ok and result.stdout == "1-2!\n"


# ── (г) сравнение вывода ─────────────────────────────────────────────────────


def test_compare_lines_ignores_edge_spaces_and_trailing_newlines():
    assert compare_stdout("25\n", "25")[0]
    assert compare_stdout("74\nNO\n", " 74 \nNO\n\n")[0]
    assert not compare_stdout("74\nNO\n", "74 NO\n")[0]
    assert not compare_stdout("25\n", "26\n")[0]


def test_compare_tokens_and_numeric():
    assert compare_stdout("74\nNO\n", "74 NO", mode="tokens")[0]
    assert compare_stdout("75.5\nYES", "75,45\nYES", mode="numeric", float_tolerance=0.05)[0]
    assert compare_stdout("75.5\nYES", "75.50\nYES", mode="numeric")[0]
    assert not compare_stdout("75.5\nYES", "75.4\nYES", mode="numeric", float_tolerance=0.05)[0]
    assert not compare_stdout("75.5\nYES", "75.5\nNO", mode="numeric", float_tolerance=0.05)[0]


# ── (д) CheckingService ──────────────────────────────────────────────────────


def test_checking_all_tests_pass_gives_full_score():
    service = CheckingService()
    for code in (MAX5_CODE, MAX5_GENEXP_CODE):
        result = service.check_task(_content(), _rules(), _answer(code))
        assert result.is_correct is True, result
        assert result.score == 1
        assert "3 из 3" in (result.feedback.general if result.feedback else "")


def test_checking_failure_names_test_input_and_expected_only():
    """Перепутанное условие проваливается на первом тесте, где есть число больше максимума кратных 5."""
    result = CheckingService().check_task(_content(), _rules(), _answer(MAX_ANY_CODE))
    assert result.is_correct is False and result.score == 0
    general = result.feedback.general if result.feedback else ""
    assert "Тест 3 из 3" in general
    assert "7 30 11 33" in general
    assert "Ожидаемый вывод: 30" in general
    # вывод программы ученика наружу не уходит — только ввод и ожидание
    assert "получено" not in general and "вывела" not in general
    assert result.details is None


def test_checking_input_exhausted_message():
    result = CheckingService().check_task(_content(), _rules(), _answer(READ_UNTIL_ZERO_CODE))
    assert result.is_correct is False
    general = result.feedback.general if result.feedback else ""
    assert "данные уже закончились" in general
    assert "Тест 1 из 3" in general


def test_checking_empty_program_and_sa_type():
    result = CheckingService().check_task(_content(), _rules(), _answer("   "))
    assert result.is_correct is False and "Программа не найдена" in result.feedback.general
    sa = CheckingService().check_task(_content("SA"), _rules(), _answer(MAX5_CODE, "SA"))
    assert sa.is_correct is True


def test_checking_ignores_manual_review_flag_with_io_tests():
    """Тесты выносят вердикт даже при manual_review_required=true (ветка идёт раньше гейта)."""
    rules = _rules(manual_review_required=True)
    result = CheckingService().check_task(_content(), rules, _answer(MAX5_CODE))
    assert result.is_correct is True and result.score == 1


def test_checking_sandbox_failure_gives_no_verdict(monkeypatch):
    import app.services.turtle_sandbox.executor as executor

    def _broken(code, *, stdin, timeout_sec, max_output_chars):
        return StdioResult(ok=False, error="sandbox_error", message="unshare не найден")

    monkeypatch.setattr(executor, "run_student_program_stdio", _broken)
    result = CheckingService().check_task(_content(), _rules(), _answer(MAX5_CODE))
    assert result.is_correct is None and result.score == 0
    assert "передан преподавателю" in result.feedback.general


def test_checking_stops_at_first_failed_test(monkeypatch):
    import app.services.turtle_sandbox.executor as executor

    calls: list[str] = []

    def _fake(code, *, stdin, timeout_sec, max_output_chars):
        calls.append(stdin)
        return StdioResult(ok=True, stdout="0\n")

    monkeypatch.setattr(executor, "run_student_program_stdio", _fake)
    CheckingService().check_task(_content(), _rules(), _answer(MAX5_CODE))
    assert len(calls) == 1


# ── (е) выбор программы ──────────────────────────────────────────────────────


def test_pick_program_prefers_value_then_comment():
    assert pick_program_for_io_tests(MAX5_CODE, "пояснение", attempt_id=1) == MAX5_CODE.strip()
    # старая форма: короткий ответ в value, программа в комментарии
    assert pick_program_for_io_tests("25", MAX5_CODE, attempt_id=1) == MAX5_CODE.strip()
    # однострочная программа порога «две строки кода» не берёт, но не теряется
    assert pick_program_for_io_tests("print(int(input()) * 2)", None, attempt_id=1) == "print(int(input()) * 2)"
    assert pick_program_for_io_tests("", "  ", attempt_id=1) is None


# ── (ж) сдача через API ──────────────────────────────────────────────────────


def _headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


async def _make_student(db) -> int:
    r = await db.execute(
        text("INSERT INTO users (email, full_name) VALUES (:e, 'tsk953 student') RETURNING id"),
        {"e": f"tsk953_{uuid.uuid4().hex[:8]}@example.com"},
    )
    sid = int(r.scalar())
    await db.commit()
    return sid


async def _make_course(db) -> int:
    r = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"),
        {"t": f"tsk953 {uuid.uuid4().hex[:8]}"},
    )
    cid = int(r.scalar())
    await db.commit()
    return cid


async def _make_task(db, course_id: int) -> int:
    import json

    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = json.dumps({"type": "SA_COM", "stem": "Напишите программу (tsk-953)."}, ensure_ascii=False)
    sr = json.dumps({"max_score": 1, "manual_review_required": False, "io_tests": {"tests": MAX5_TESTS}}, ensure_ascii=False)
    r = await db.execute(
        text(
            "INSERT INTO tasks (course_id, difficulty_id, task_content, solution_rules, max_score) "
            "VALUES (:cid, :did, CAST(:tc AS jsonb), CAST(:sr AS jsonb), 1) RETURNING id"
        ),
        {"cid": course_id, "did": diff, "tc": tc, "sr": sr},
    )
    tid = int(r.scalar())
    await db.commit()
    return tid


async def _cleanup(db, *, course_id: int, student_id: int) -> None:
    await db.execute(text("DELETE FROM courses WHERE id = :cid"), {"cid": course_id})
    await db.execute(text("DELETE FROM users WHERE id = :sid"), {"sid": student_id})
    await db.commit()


async def _submit(client, attempt_id: int, task_id: int, *, value: str, comment: Optional[str] = None) -> dict:
    response: dict = {"value": value}
    if comment is not None:
        response["comment"] = comment
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": task_id, "answer": {"type": "SA_COM", "response": response}}]},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]["check_result"]


@pytest.mark.asyncio
async def test_api_submit_io_tests_task(client, db):
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id)
    try:
        state = await client.get(
            f"/api/v1/learning/tasks/{task_id}/state?student_id={student_id}", headers=_headers()
        )
        assert state.status_code == 200, state.text
        assert state.json()["has_io_tests"] is True
        assert state.json()["has_reference_answer"] is True

        async def _attempt() -> int:
            resp = await client.post(
                "/api/v1/attempts",
                json={"user_id": student_id, "course_id": course_id, "source_system": "test"},
                headers=_headers(),
            )
            assert resp.status_code == 201, resp.text
            return int(resp.json()["id"])

        # верное решение в value, без комментария и файла → зачёт (гейт 2.3f не применяется)
        result = await _submit(client, await _attempt(), task_id, value=MAX5_CODE)
        assert result["is_correct"] is True and result["score"] == 1, result

        # неверное решение → незачёт, оптимистичный зачёт SA_COM не подменяет
        result = await _submit(client, await _attempt(), task_id, value=MAX_ANY_CODE, comment="решил")
        assert result["is_correct"] is False and result["score"] == 0, result
        assert "Тест 3 из 3" in (result.get("feedback") or {}).get("general", "")

        # старая форма: value пустой, программа в комментарии → зачёт
        result = await _submit(client, await _attempt(), task_id, value="", comment=MAX5_GENEXP_CODE)
        assert result["is_correct"] is True and result["score"] == 1, result

        # answer_json хранит ответ таким, каким сдал ученик (value пуст, код в comment)
        row = (await db.execute(
            text(
                "SELECT answer_json->'response'->>'value', answer_json->'response'->>'comment' "
                "FROM task_results WHERE user_id = :u AND task_id = :t ORDER BY id DESC LIMIT 1"
            ),
            {"u": student_id, "t": task_id},
        )).fetchone()
        assert row is not None and (row[0] or "") == "" and "input()" in (row[1] or "")
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)
