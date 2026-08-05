# app/services/turtle_sandbox/stub_turtle.py
"""
Headless-заглушка модуля `turtle` (tsk-412).

Реализует минимальный публичный API, которым реально пользуются эталонные
решения материала 314 «Рисование сложных узоров»: движение (forward/backward/
goto/circle), поворот (left/right/setheading), перо (penup/pendown/color) и
клик мыши (onscreenclick + done/mainloop как точка воспроизведения синтетических
кликов). Вместо рисования на холсте каждый примитив с опущенным пером
записывается в трассу (см. `app.schemas.solution_rules.TurtleTrace`), которая
затем сравнивается с эталонной.

Никакого Tkinter/GUI, никакого реального времени — весь модуль синхронный и
детерминированный (при заданном random.seed).
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple

Number = float

_NAMED_COLORS: Dict[str, Tuple[float, float, float]] = {
    "black": (0.0, 0.0, 0.0),
    "white": (1.0, 1.0, 1.0),
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "yellow": (1.0, 1.0, 0.0),
    "orange": (1.0, 0.6470588235294118, 0.0),
    "purple": (0.5019607843137255, 0.0, 0.5019607843137255),
    "brown": (0.6470588235294118, 0.16470588235294117, 0.16470588235294117),
    "pink": (1.0, 0.7529411764705882, 0.796078431372549),
    "gray": (0.5019607843137255, 0.5019607843137255, 0.5019607843137255),
    "grey": (0.5019607843137255, 0.5019607843137255, 0.5019607843137255),
    "cyan": (0.0, 1.0, 1.0),
    "magenta": (1.0, 0.0, 1.0),
}


class StepLimitExceeded(RuntimeError):
    """Превышен лимит примитивов движения (`TurtleSimRules.max_steps`)."""


class TurtleSandboxError(RuntimeError):
    """Общая ошибка стаба (некорректные аргументы вызова и т.п.)."""


def _normalize_color(value: Any) -> Tuple[float, float, float]:
    """Приводит цвет (имя или RGB-кортеж) к нормализованному RGB [0..1]."""
    if isinstance(value, str):
        key = value.strip().lower()
        if key not in _NAMED_COLORS:
            raise TurtleSandboxError(f"Неизвестное имя цвета: {value!r}")
        return _NAMED_COLORS[key]
    if isinstance(value, (tuple, list)) and len(value) == 3:
        r, g, b = value
        # colorsys.hsv_to_rgb уже отдаёт 0..1; допускаем и 0..255 на глаз по величине.
        if max(r, g, b) > 1.0:
            return (r / 255.0, g / 255.0, b / 255.0)
        return (float(r), float(g), float(b))
    raise TurtleSandboxError(f"Некорректное значение цвета: {value!r}")


class _Session:
    """
    Общее состояние песочницы на одно исполнение программы: счётчик шагов,
    трасса, обработчики кликов и очередь синтетических событий.
    """

    def __init__(self, max_steps: int, synthetic_clicks: List[List[float]]) -> None:
        self.max_steps = max_steps
        self.synthetic_clicks = [tuple(c) for c in synthetic_clicks]
        self.step_count = 0
        self.segments: List[Dict[str, Any]] = []
        self._click_handlers: List[Callable[[float, float], None]] = []
        self._events_replayed = False
        self._first_turtle: Optional["Turtle"] = None

    def count_step(self) -> None:
        self.step_count += 1
        if self.step_count > self.max_steps:
            raise StepLimitExceeded(
                f"Превышен предел примитивов движения ({self.max_steps}). "
                "Проверьте условие выхода из цикла."
            )

    def register_click_handler(self, fn: Callable[[float, float], None]) -> None:
        self._click_handlers.append(fn)

    def replay_pending_events(self) -> None:
        """Проигрывает синтетические клики зарегистрированным обработчикам (once)."""
        if self._events_replayed:
            return
        self._events_replayed = True
        for x, y in self.synthetic_clicks:
            for handler in self._click_handlers:
                handler(float(x), float(y))

    def record_line(self, start: Tuple[float, float], end: Tuple[float, float], color: Tuple[float, float, float]) -> None:
        self.segments.append({
            "kind": "line",
            "start": [round(start[0], 4), round(start[1], 4)],
            "end": [round(end[0], 4), round(end[1], 4)],
            "color_rgb": [round(c, 4) for c in color],
        })

    def record_circle(
        self,
        start: Tuple[float, float],
        end: Tuple[float, float],
        color: Tuple[float, float, float],
        radius: float,
        extent: float,
    ) -> None:
        self.segments.append({
            "kind": "circle",
            "start": [round(start[0], 4), round(start[1], 4)],
            "end": [round(end[0], 4), round(end[1], 4)],
            "color_rgb": [round(c, 4) for c in color],
            "radius": round(radius, 4),
            "extent": round(extent, 4),
        })

    def export_trace(self) -> Dict[str, Any]:
        first = self._first_turtle
        final_state = {
            "position": [0.0, 0.0],
            "heading": 0.0,
            "pen_down": True,
        }
        if first is not None:
            final_state = {
                "position": [round(first.xcor(), 4), round(first.ycor(), 4)],
                "heading": round(first.heading() % 360, 4),
                "pen_down": bool(first.isdown()),
            }
        return {"segments": self.segments, "final_state": final_state}


class Turtle:
    """Одна черепаха. Все экземпляры пишут в общий `_Session.segments`."""

    def __init__(self, session: _Session, *_args: Any, **_kwargs: Any) -> None:
        self._session = session
        self._x = 0.0
        self._y = 0.0
        self._heading = 0.0  # градусы, 0 = восток, против часовой стрелки
        self._pen_down = True
        self._pen_color: Tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._fill_color: Tuple[float, float, float] = (0.0, 0.0, 0.0)
        if session._first_turtle is None:
            session._first_turtle = self

    # ---------- движение ----------

    def forward(self, distance: Number) -> None:
        rad = math.radians(self._heading)
        end = (self._x + distance * math.cos(rad), self._y + distance * math.sin(rad))
        if self._pen_down:
            self._session.record_line((self._x, self._y), end, self._pen_color)
        self._x, self._y = end
        self._session.count_step()

    fd = forward

    def backward(self, distance: Number) -> None:
        self.forward(-distance)

    bk = backward
    back = backward

    def goto(self, x: Number, y: Optional[Number] = None) -> None:
        if y is None and isinstance(x, (tuple, list)):
            x, y = x[0], x[1]
        end = (float(x), float(y))
        if self._pen_down:
            self._session.record_line((self._x, self._y), end, self._pen_color)
        self._x, self._y = end
        self._session.count_step()

    setpos = goto
    setposition = goto

    def setx(self, x: Number) -> None:
        self.goto(x, self._y)

    def sety(self, y: Number) -> None:
        self.goto(self._x, y)

    def circle(self, radius: Number, extent: Number = 360, steps: Optional[int] = None) -> None:
        if radius == 0:
            raise TurtleSandboxError("circle(0) не определён.")
        direction = 1 if radius >= 0 else -1
        r = abs(radius)
        heading_rad = math.radians(self._heading)
        center_angle = heading_rad + direction * (math.pi / 2)
        cx = self._x + r * math.cos(center_angle)
        cy = self._y + r * math.sin(center_angle)
        start_vec_angle = math.atan2(self._y - cy, self._x - cx)
        end_vec_angle = start_vec_angle + direction * math.radians(extent)
        end = (cx + r * math.cos(end_vec_angle), cy + r * math.sin(end_vec_angle))
        if self._pen_down:
            self._session.record_circle((self._x, self._y), end, self._pen_color, radius, extent)
        self._x, self._y = end
        self._heading = (self._heading + direction * extent) % 360
        self._session.count_step()

    # ---------- поворот ----------

    def right(self, angle: Number) -> None:
        self._heading = (self._heading - angle) % 360

    rt = right

    def left(self, angle: Number) -> None:
        self._heading = (self._heading + angle) % 360

    lt = left

    def setheading(self, angle: Number) -> None:
        self._heading = float(angle) % 360

    seth = setheading

    # ---------- перо ----------

    def penup(self) -> None:
        self._pen_down = False

    pu = penup
    up = penup

    def pendown(self) -> None:
        self._pen_down = True

    pd = pendown
    down = pendown

    def isdown(self) -> bool:
        return self._pen_down

    def color(self, *args: Any) -> Any:
        if not args:
            return (self._pen_color, self._fill_color)
        if len(args) == 1:
            c = _normalize_color(args[0])
            self._pen_color = c
            self._fill_color = c
        else:
            self._pen_color = _normalize_color(args[0])
            self._fill_color = _normalize_color(args[1])
        return None

    def pencolor(self, *args: Any) -> Any:
        if not args:
            return self._pen_color
        self._pen_color = _normalize_color(args[0] if len(args) == 1 else args)
        return None

    def fillcolor(self, *args: Any) -> Any:
        if not args:
            return self._fill_color
        self._fill_color = _normalize_color(args[0] if len(args) == 1 else args)
        return None

    # ---------- запросы состояния ----------

    def position(self) -> Tuple[float, float]:
        return (self._x, self._y)

    pos = position

    def xcor(self) -> float:
        return self._x

    def ycor(self) -> float:
        return self._y

    def heading(self) -> float:
        return self._heading

    def distance(self, x: Any, y: Optional[Number] = None) -> float:
        if y is None:
            if isinstance(x, Turtle):
                ox, oy = x._x, x._y
            else:
                ox, oy = x[0], x[1]
        else:
            ox, oy = float(x), float(y)
        return math.hypot(self._x - ox, self._y - oy)

    # ---------- события мыши ----------

    def onscreenclick(self, fun: Optional[Callable[[float, float], None]], *_a: Any, **_kw: Any) -> None:
        if fun is not None:
            self._session.register_click_handler(fun)

    onclick = onscreenclick

    def ondrag(self, *_a: Any, **_kw: Any) -> None:
        return None

    # ---------- косметика / no-op ----------

    def speed(self, *_a: Any) -> Optional[int]:
        return 0

    def pensize(self, *_a: Any) -> Optional[int]:
        return None

    width = pensize

    def shape(self, *_a: Any) -> None:
        return None

    def shapesize(self, *_a: Any, **_kw: Any) -> None:
        return None

    def hideturtle(self) -> None:
        return None

    ht = hideturtle

    def showturtle(self) -> None:
        return None

    st = showturtle

    def write(self, *_a: Any, **_kw: Any) -> None:
        return None

    def begin_fill(self) -> None:
        return None

    def end_fill(self) -> None:
        return None

    def done(self) -> None:
        self._session.replay_pending_events()

    mainloop = done


_TURTLE_INSTANCE_METHOD_NAMES = frozenset(
    name for name in vars(Turtle) if not name.startswith("_")
)


class TurtleModule:
    """
    Объект, подставляемый в `sys.modules['turtle']` — эмулирует и модульные
    функции (`turtle.forward(10)` через скрытую дефолтную черепаху), и класс
    `turtle.Turtle`.
    """

    def __init__(self, session: _Session) -> None:
        self._session = session
        self.Turtle = lambda *a, **kw: Turtle(session, *a, **kw)
        self.Pen = self.Turtle
        self._default: Optional[Turtle] = None

    def __getattr__(self, name: str) -> Any:
        # Модульные функции-делегаты: turtle.forward(...) == default_turtle.forward(...).
        # ВАЖНО: дефолтная черепаха создаётся ЛЕНИВО, только если код реально
        # вызвал модульную функцию — иначе она стала бы session._first_turtle
        # ДО того, как ученик создаст свою через turtle.Turtle(), и export_trace()
        # всегда отдавал бы состояние неиспользуемой заглушки (0,0,0), а не
        # реального ответа (найдено локальным прогоном всех 10 эталонов).
        if name in _TURTLE_INSTANCE_METHOD_NAMES:
            if self._default is None:
                self._default = Turtle(self._session)
            return getattr(self._default, name)
        # Косметические функции экрана, которых нет у Turtle — безопасные no-op.
        if name in {
            "bgcolor", "title", "screensize", "tracer", "update", "delay",
            "colormode", "setup", "listen", "onkey", "onkeypress", "onkeyrelease",
            "ontimer", "bye", "exitonclick", "getcanvas",
        }:
            return lambda *a, **kw: None
        raise AttributeError(f"turtle-заглушка: неизвестный атрибут {name!r}")

    def Screen(self) -> "TurtleModule":
        return self

    def done(self) -> None:
        self._session.replay_pending_events()

    mainloop = done


_ALLOWED_RUNTIME_IMPORTS = frozenset({"turtle", "math", "random", "colorsys"})


def _restricted_import(
    name: str,
    globals: Any = None,  # noqa: A002 — сигнатура builtins.__import__
    locals: Any = None,  # noqa: A002
    fromlist: Any = (),
    level: int = 0,
) -> Any:
    """
    Замена `__builtins__.__import__` внутри песочницы.

    Инструкция `import X` компилируется в вызов `__import__(...)` НЕЯВНО —
    просто убрать имя `__import__` из builtins сломало бы легальный
    `import turtle`/`import math` вместе с атакой. Поэтому вместо удаления —
    урезанная реализация: пропускает только 4 разрешённых модуля (тот же
    список, что и статический AST-страж в `guard.py`), для turtle отдаёт
    именно заглушку из `sys.modules`, для остальных — настоящий stdlib-модуль
    (math/random/colorsys не дают доступа к файлам/сети/процессам).
    """
    root = name.split(".")[0]
    if root not in _ALLOWED_RUNTIME_IMPORTS:
        raise ImportError(f"Импорт модуля '{name}' запрещён в этом задании.")
    import builtins as _builtins

    return _builtins.__import__(name, globals, locals, fromlist, level)


def build_restricted_globals(session: _Session) -> Dict[str, Any]:
    """
    Строит globals() для exec() кода ученика: безопасный набор builtins +
    предустановленный `sys.modules['turtle']`, чтобы `import turtle` и
    `from turtle import *` подхватывали заглушку, а не настоящий модуль.
    """
    import sys as _sys

    module = TurtleModule(session)
    _sys.modules["turtle"] = module

    safe_builtins: Dict[str, Any] = {
        "__import__": _restricted_import,
        "abs": abs, "min": min, "max": max, "round": round, "len": len,
        "range": range, "enumerate": enumerate, "zip": zip, "map": map,
        "filter": filter, "sorted": sorted, "reversed": reversed, "sum": sum,
        "any": any, "all": all, "divmod": divmod, "pow": pow,
        "int": int, "float": float, "str": str, "bool": bool, "complex": complex,
        "list": list, "tuple": tuple, "dict": dict, "set": set, "frozenset": frozenset,
        "isinstance": isinstance, "print": lambda *a, **kw: None,
        "True": True, "False": False, "None": None,
        "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
        "RuntimeError": RuntimeError, "ZeroDivisionError": ZeroDivisionError,
        "StopIteration": StopIteration, "IndexError": IndexError, "KeyError": KeyError,
        "ArithmeticError": ArithmeticError, "OverflowError": OverflowError,
        "__build_class__": __builtins__["__build_class__"] if isinstance(__builtins__, dict) else __builtins__.__build_class__,
    }
    return {"__builtins__": safe_builtins, "__name__": "__main__", "turtle": module}
