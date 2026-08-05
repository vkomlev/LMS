# tests/test_turtle_sandbox_tsk412.py
"""
Тесты песочницы черепашьей графики (tsk-412): AST-страж, стаб turtle,
исполнение в подпроцессе, сравнение трасс, диспетчеризация CheckingService.

Не требуют БД — чистая логика песочницы + Pydantic-схемы.
"""
from __future__ import annotations

import pytest

from app.schemas.checking import StudentAnswer, StudentResponse
from app.schemas.solution_rules import SolutionRules, TurtleSimRules, TurtleTrace
from app.services.checking_service import CheckingService
from app.services.turtle_sandbox.comparator import compare_traces
from app.services.turtle_sandbox.executor import run_student_code
from app.services.turtle_sandbox.guard import GuardViolation, check_code_is_safe

SQUARE_CODE = """
import turtle
t = turtle.Turtle()
for _ in range(4):
    t.forward(50)
    t.right(90)
turtle.done()
"""

PENTAGON_CODE = """
import turtle
t = turtle.Turtle()
for _ in range(5):
    t.forward(50)
    t.right(72)
turtle.done()
"""

SPIRAL_WITH_COLOR_CODE = """
import turtle
import colorsys
t = turtle.Turtle()
for i in range(100):
    hue = i / 100.0
    t.color(colorsys.hsv_to_rgb(hue, 1, 1))
    t.forward(i)
    t.right(45)
turtle.done()
"""

RANDOM_CODE = """
import turtle
import random
import colorsys
t = turtle.Turtle()
for _ in range(36):
    hue = random.random()
    t.color(colorsys.hsv_to_rgb(hue, 1, 1))
    size = random.randint(50, 150)
    t.forward(size)
    t.right(170)
turtle.done()
"""

CLICK_SQUARE_CODE = """
import turtle
def draw_square(t, size):
    for _ in range(4):
        t.forward(size)
        t.left(90)
def on_click(x, y):
    t.penup()
    t.goto(x, y)
    t.pendown()
    draw_square(t, 50)
t = turtle.Turtle()
turtle.onscreenclick(on_click)
turtle.done()
"""


# ---------- AST-страж ----------

@pytest.mark.parametrize(
    "code",
    [
        "import os\nos.system('echo hi')\n",
        "import subprocess\n",
        "x = ().__class__.__bases__[0].__subclasses__()\n",
        "eval('1+1')\n",
        "exec('x=1')\n",
        "open('secret.txt').read()\n",
        "__import__('os')\n",
        "compile('1', '<s>', 'eval')\n",
        "y = globals()\n",
    ],
)
def test_guard_rejects_dangerous_code(code: str) -> None:
    with pytest.raises(GuardViolation):
        check_code_is_safe(code)


# tsk-412 review-gate P0: PoC, независимо продемонстрировавший реальный побег
# из песочницы через живой фрейм, достижимый НЕ-dunder именами (tb_frame/
# f_back/f_globals), пойманный внутри __exit__ контекстного менеджера.
# check_code_is_safe обязана отклонять его и соседние варианты того же класса
# (генератор/корутина тоже дают живой фрейм через .gi_frame/.cr_frame).
FRAME_LEAK_POC_CODE = """
class Leaker:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        frame = exc_tb.tb_frame.f_back
        real_builtins = frame.f_globals['__builtins__']
        return True

with Leaker():
    raise ValueError("x")
"""

GENERATOR_FRAME_LEAK_CODE = """
def gen():
    yield 1

g = gen()
next(g)
frame = g.gi_frame
"""

BARE_TRY_EXCEPT_CODE = """
try:
    x = 1
except Exception:
    pass
"""

BARE_WITH_CODE = """
class Ctx:
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False

with Ctx():
    pass
"""

ASYNC_DEF_CODE = """
async def f():
    return 1
"""


@pytest.mark.parametrize(
    "code",
    [
        FRAME_LEAK_POC_CODE,
        GENERATOR_FRAME_LEAK_CODE,
        BARE_TRY_EXCEPT_CODE,
        BARE_WITH_CODE,
        ASYNC_DEF_CODE,
        "x = (i for i in range(10))\n",  # генераторное выражение — .gi_frame
    ],
)
def test_guard_rejects_frame_introspection_class_of_exploits(code: str) -> None:
    with pytest.raises(GuardViolation):
        check_code_is_safe(code)


def test_executor_rejects_frame_leak_poc_end_to_end() -> None:
    """То же самое, но через реальный подпроцесс — страж должен сработать ДО exec()."""
    result = run_student_code(
        FRAME_LEAK_POC_CODE, random_seed=None, synthetic_clicks=[], max_steps=5000, timeout_sec=5.0,
    )
    assert not result.ok
    assert result.error == "forbidden_construct"


@pytest.mark.parametrize("code", [SQUARE_CODE, SPIRAL_WITH_COLOR_CODE, RANDOM_CODE, CLICK_SQUARE_CODE])
def test_guard_allows_legitimate_turtle_code(code: str) -> None:
    check_code_is_safe(code)  # не должно бросить


def test_guard_rejects_syntax_error_as_syntax_error_not_guard_violation() -> None:
    with pytest.raises(SyntaxError):
        check_code_is_safe("def f(:\n")


# ---------- Исполнение в песочнице ----------

def test_executor_runs_square_and_returns_expected_geometry() -> None:
    result = run_student_code(
        SQUARE_CODE, random_seed=None, synthetic_clicks=[], max_steps=5000, timeout_sec=5.0,
    )
    assert result.ok, result.message
    segments = result.trace["segments"]
    assert len(segments) == 4
    final = result.trace["final_state"]
    assert final["position"] == pytest.approx([0.0, 0.0], abs=1e-6)
    assert final["heading"] == pytest.approx(0.0, abs=1e-6)


def test_executor_forbidden_construct_returns_ok_false() -> None:
    result = run_student_code(
        "import os\n", random_seed=None, synthetic_clicks=[], max_steps=5000, timeout_sec=5.0,
    )
    assert not result.ok
    assert result.error == "forbidden_construct"


def test_executor_step_limit_guards_runaway_loop() -> None:
    code = "import turtle\nt = turtle.Turtle()\nwhile True:\n    t.forward(1)\n"
    result = run_student_code(
        code, random_seed=None, synthetic_clicks=[], max_steps=200, timeout_sec=5.0,
    )
    assert not result.ok
    assert result.error == "step_limit_exceeded"


def test_executor_random_seed_is_reproducible() -> None:
    r1 = run_student_code(RANDOM_CODE, random_seed=42, synthetic_clicks=[], max_steps=5000, timeout_sec=5.0)
    r2 = run_student_code(RANDOM_CODE, random_seed=42, synthetic_clicks=[], max_steps=5000, timeout_sec=5.0)
    assert r1.ok and r2.ok
    assert r1.trace == r2.trace


def test_executor_synthetic_click_drives_final_position() -> None:
    result = run_student_code(
        CLICK_SQUARE_CODE, random_seed=None, synthetic_clicks=[[30, 40]], max_steps=5000, timeout_sec=5.0,
    )
    assert result.ok, result.message
    assert result.trace["final_state"]["position"] == pytest.approx([30.0, 40.0], abs=1e-6)


def test_executor_syntax_error_in_student_code() -> None:
    result = run_student_code("def f(:\n", random_seed=None, synthetic_clicks=[], max_steps=5000, timeout_sec=5.0)
    assert not result.ok
    assert result.error == "syntax_error"


# ---------- Сравнение трасс ----------

def test_compare_traces_identical_ok() -> None:
    r = run_student_code(SQUARE_CODE, random_seed=None, synthetic_clicks=[], max_steps=5000, timeout_sec=5.0)
    ok, reason = compare_traces(r.trace, r.trace, tolerance_px=0.75)
    assert ok, reason


def test_compare_traces_different_shape_fails() -> None:
    square = run_student_code(SQUARE_CODE, random_seed=None, synthetic_clicks=[], max_steps=5000, timeout_sec=5.0)
    pentagon = run_student_code(PENTAGON_CODE, random_seed=None, synthetic_clicks=[], max_steps=5000, timeout_sec=5.0)
    ok, reason = compare_traces(square.trace, pentagon.trace, tolerance_px=0.75)
    assert not ok
    assert reason


# ---------- CheckingService._check_turtle_sim ----------

def _build_turtle_sim_solution_rules(reference_code: str) -> SolutionRules:
    reference = run_student_code(
        reference_code, random_seed=None, synthetic_clicks=[], max_steps=5000, timeout_sec=5.0,
    )
    assert reference.ok
    return SolutionRules(
        max_score=1,
        turtle_sim=TurtleSimRules(
            expected_trace=TurtleTrace.model_validate(reference.trace),
            random_seed=None,
            synthetic_clicks=[],
            tolerance_px=0.75,
            max_steps=5000,
            timeout_sec=5.0,
        ),
    )


def _sa_answer(value: str) -> StudentAnswer:
    return StudentAnswer(type="SA", response=StudentResponse(value=value))


def test_checking_service_turtle_sim_correct_answer() -> None:
    solution_rules = _build_turtle_sim_solution_rules(SQUARE_CODE)
    service = CheckingService()
    from app.schemas.task_content import TaskContent

    task_content = TaskContent.model_validate({"type": "SA", "stem": "Нарисуй квадрат"})
    result = service.check_task(task_content, solution_rules, _sa_answer(SQUARE_CODE))
    assert result.is_correct is True
    assert result.score == 1


def test_checking_service_turtle_sim_wrong_answer() -> None:
    solution_rules = _build_turtle_sim_solution_rules(SQUARE_CODE)
    service = CheckingService()
    from app.schemas.task_content import TaskContent

    task_content = TaskContent.model_validate({"type": "SA", "stem": "Нарисуй квадрат"})
    result = service.check_task(task_content, solution_rules, _sa_answer(PENTAGON_CODE))
    assert result.is_correct is False
    assert result.score == 0


def test_checking_service_turtle_sim_forbidden_code_scores_zero_not_crash() -> None:
    solution_rules = _build_turtle_sim_solution_rules(SQUARE_CODE)
    service = CheckingService()
    from app.schemas.task_content import TaskContent

    task_content = TaskContent.model_validate({"type": "SA", "stem": "Нарисуй квадрат"})
    result = service.check_task(task_content, solution_rules, _sa_answer("import os\n"))
    assert result.is_correct is False
    assert result.score == 0
    assert result.feedback is not None


def test_checking_service_turtle_sim_empty_answer() -> None:
    solution_rules = _build_turtle_sim_solution_rules(SQUARE_CODE)
    service = CheckingService()
    from app.schemas.task_content import TaskContent

    task_content = TaskContent.model_validate({"type": "SA", "stem": "Нарисуй квадрат"})
    result = service.check_task(task_content, solution_rules, _sa_answer(""))
    assert result.is_correct is False
    assert result.score == 0


def test_has_reference_answer_true_for_turtle_sim() -> None:
    solution_rules = _build_turtle_sim_solution_rules(SQUARE_CODE)
    assert solution_rules.has_reference_answer() is True


# ---------- Лимит параллелизма (tsk-412 review-gate P1) ----------

def test_executor_returns_busy_when_semaphore_exhausted() -> None:
    """Симулирует занятость всех слотов семафора: должен вернуться sandbox_busy
    быстро (в пределах _SEMAPHORE_WAIT_SEC), а не зависнуть/упасть."""
    from app.services.turtle_sandbox import executor as executor_module

    acquired = []
    try:
        for _ in range(executor_module._SANDBOX_CONCURRENCY_LIMIT):
            sem_acquired = executor_module._SANDBOX_SEMAPHORE.acquire(timeout=1.0)
            assert sem_acquired
            acquired.append(sem_acquired)

        result = run_student_code(
            SQUARE_CODE, random_seed=None, synthetic_clicks=[], max_steps=5000, timeout_sec=5.0,
        )
        assert not result.ok
        assert result.error == "sandbox_busy"
    finally:
        for _ in acquired:
            executor_module._SANDBOX_SEMAPHORE.release()
