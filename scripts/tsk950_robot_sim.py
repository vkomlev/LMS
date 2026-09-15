# -*- coding: utf-8 -*-
"""tsk-950: мини-симулятор Робота (КуМир) для проверки ориентиров к 25 заданиям ОГЭ-15.

Подмножество языка: команды вверх/вниз/влево/вправо/закрасить, проверки
«сверху|снизу|слева|справа свободно», связки и/или/не со скобками, «нц пока … кц»,
«если … то … [иначе …] все». Служебные строки (алг, нач, кон, использовать) пропускаются.

Поле: клетки (x, y), y растёт вниз. Стена — ребро между двумя соседними клетками.
Семейства полей генерируются случайно; ожидаемое множество закрашенных клеток
считается геометрически. Симметрии (отражения, транспонирование) переводят
канонический алгоритм и поле одного задания в родственные задания.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, List, Optional, Set, Tuple

Cell = Tuple[int, int]
Edge = FrozenSet[Cell]

DIRS: Dict[str, Tuple[int, int]] = {"вверх": (0, -1), "вниз": (0, 1), "влево": (-1, 0), "вправо": (1, 0)}
SIDE_TO_DIR: Dict[str, str] = {"сверху": "вверх", "снизу": "вниз", "слева": "влево", "справа": "вправо"}


def edge(a: Cell, b: Cell) -> Edge:
    return frozenset((a, b))


@dataclass
class Field:
    walls: Set[Edge]
    robot: Cell
    expected: Set[Cell]
    painted: Set[Cell] = field(default_factory=set)

    def free(self, side: str) -> bool:
        dx, dy = DIRS[SIDE_TO_DIR[side]]
        nb = (self.robot[0] + dx, self.robot[1] + dy)
        return edge(self.robot, nb) not in self.walls


class RobotError(Exception):
    pass


# ---------------------------------------------------------------------------
# Разбор и исполнение
# ---------------------------------------------------------------------------

_SKIP = re.compile(r"^(алг|нач|кон|использовать)\b", re.IGNORECASE)


def _tokenize_cond(s: str) -> List[str]:
    s = s.replace("(", " ( ").replace(")", " ) ")
    return s.split()


def _parse_cond(tokens: List[str]) -> Callable[[Field], bool]:
    """Рекурсивный спуск: или → и → не → атом."""
    pos = 0

    def peek() -> Optional[str]:
        return tokens[pos] if pos < len(tokens) else None

    def take() -> str:
        nonlocal pos
        t = tokens[pos]
        pos += 1
        return t

    def atom() -> Callable[[Field], bool]:
        t = take()
        if t == "(":
            f = or_expr()
            if take() != ")":
                raise RobotError("ожидалась )")
            return f
        if t == "не":
            f = atom()
            return lambda fld, f=f: not f(fld)
        if t in SIDE_TO_DIR:
            nxt = take()
            if nxt != "свободно":
                raise RobotError(f"ожидалось 'свободно' после {t}")
            return lambda fld, side=t: fld.free(side)
        raise RobotError(f"непонятное условие: {t}")

    def and_expr() -> Callable[[Field], bool]:
        f = atom()
        while peek() == "и":
            take()
            g = atom()
            f = (lambda fld, f=f, g=g: f(fld) and g(fld))
        return f

    def or_expr() -> Callable[[Field], bool]:
        f = and_expr()
        while peek() == "или":
            take()
            g = and_expr()
            f = (lambda fld, f=f, g=g: f(fld) or g(fld))
        return f

    f = or_expr()
    if pos != len(tokens):
        raise RobotError(f"лишние символы в условии: {tokens[pos:]}")
    return f


def parse(program: str) -> List:
    """Строит дерево: список узлов ('cmd', name) | ('while', cond, body) | ('if', cond, then, else)."""
    lines = [ln.strip() for ln in program.splitlines()]
    lines = [ln for ln in lines if ln and not _SKIP.match(ln)]
    pos = 0

    def block(stop: Tuple[str, ...]) -> List:
        nonlocal pos
        out: List = []
        while pos < len(lines):
            ln = lines[pos]
            low = ln.lower()
            if low in stop or low.split()[0] in stop:
                return out
            pos += 1
            if low.startswith("нц пока"):
                cond = _parse_cond(_tokenize_cond(ln[len("нц пока"):]))
                body = block(("кц",))
                if pos >= len(lines) or lines[pos].lower() != "кц":
                    raise RobotError("нет кц")
                pos += 1
                out.append(("while", cond, body))
            elif low.startswith("если"):
                rest = ln[len("если"):]
                if " то" not in rest and not rest.endswith("то"):
                    raise RobotError("нет 'то' в если")
                head, _, tail = rest.rpartition(" то")
                cond = _parse_cond(_tokenize_cond(head))
                inline = tail.strip()
                if inline and inline.lower().endswith("все"):
                    # однострочная форма: «если X то закрасить все»
                    cmds = inline[:-3].split()
                    out.append(("if", cond, [("cmd", c) for c in cmds], []))
                    continue
                then = block(("иначе", "все"))
                els: List = []
                if pos < len(lines) and lines[pos].lower() == "иначе":
                    pos += 1
                    els = block(("все",))
                if pos >= len(lines) or lines[pos].lower() != "все":
                    raise RobotError("нет все")
                pos += 1
                out.append(("if", cond, then, els))
            else:
                for c in ln.split():
                    if c not in DIRS and c != "закрасить":
                        raise RobotError(f"неизвестная команда: {c}")
                    out.append(("cmd", c))
        return out

    tree = block(())
    if pos != len(lines):
        raise RobotError(f"неожиданная строка: {lines[pos]}")
    return tree


def run(program: str, fld: Field, max_steps: int = 20000) -> None:
    tree = parse(program)
    steps = 0

    def exec_block(nodes: List) -> None:
        nonlocal steps
        for node in nodes:
            if node[0] == "cmd":
                steps += 1
                if steps > max_steps:
                    raise RobotError("превышен лимит шагов (зацикливание)")
                if node[1] == "закрасить":
                    fld.painted.add(fld.robot)
                else:
                    dx, dy = DIRS[node[1]]
                    nb = (fld.robot[0] + dx, fld.robot[1] + dy)
                    if edge(fld.robot, nb) in fld.walls:
                        raise RobotError(f"Робот разрушился: {node[1]} из {fld.robot}")
                    fld.robot = nb
            elif node[0] == "while":
                while node[1](fld):
                    steps += 1
                    if steps > max_steps:
                        raise RobotError("превышен лимит шагов (зацикливание)")
                    exec_block(node[2])
            else:
                if node[1](fld):
                    exec_block(node[2])
                else:
                    exec_block(node[3])

    exec_block(tree)


# ---------------------------------------------------------------------------
# Симметрии
# ---------------------------------------------------------------------------

def _swap_words(text: str, pairs: List[Tuple[str, str]]) -> str:
    table = {}
    for a, b in pairs:
        table[a] = b
        table[b] = a
    return re.sub(r"[а-яё]+", lambda m: table.get(m.group(0), m.group(0)), text)


TRANSFORMS: Dict[str, Tuple[Callable[[Cell], Cell], List[Tuple[str, str]]]] = {
    "flipV": (lambda c: (c[0], -c[1]), [("вверх", "вниз"), ("сверху", "снизу")]),
    "flipH": (lambda c: (-c[0], c[1]), [("влево", "вправо"), ("слева", "справа")]),
    "T": (lambda c: (c[1], c[0]), [("вверх", "влево"), ("вниз", "вправо"), ("сверху", "слева"), ("снизу", "справа")]),
}


def transform_program(program: str, seq: List[str]) -> str:
    for name in seq:
        program = _swap_words(program, TRANSFORMS[name][1])
    return program


def transform_field(fld: Field, seq: List[str]) -> Field:
    walls, robot, expected = fld.walls, fld.robot, fld.expected
    for name in seq:
        f = TRANSFORMS[name][0]
        walls = {frozenset(f(c) for c in e) for e in walls}
        robot = f(robot)
        expected = {f(c) for c in expected}
    return Field(walls=walls, robot=robot, expected=expected)


# ---------------------------------------------------------------------------
# Генераторы канонических полей (по одному на семейство)
# ---------------------------------------------------------------------------

def hwall(y_above: int, x1: int, x2: int, gap: Tuple[int, int] = (0, -1)) -> Set[Edge]:
    """Горизонтальная стена под строкой y_above, столбцы x1..x2, проход gap (вкл.)."""
    return {edge((x, y_above), (x, y_above + 1)) for x in range(x1, x2 + 1) if not gap[0] <= x <= gap[1]}


def vwall(x_left: int, y1: int, y2: int, gap: Tuple[int, int] = (0, -1)) -> Set[Edge]:
    """Вертикальная стена правее столбца x_left, строки y1..y2, проход gap (вкл.)."""
    return {edge((x_left, y), (x_left + 1, y)) for y in range(y1, y2 + 1) if not gap[0] <= y <= gap[1]}


def _gap(rnd: random.Random, lo: int, hi: int) -> Tuple[int, int]:
    """Проход строго внутри отрезка lo..hi (не касается концов), ширина 1–2."""
    w = rnd.choice([1, 1, 2])
    a = rnd.randint(lo + 1, hi - w)
    return a, a + w - 1


def gen_K1(rnd: random.Random) -> Field:
    """7201: Робот под H у левого конца; H вправо до угла, V вниз от правого конца.
    Проход в каждой стене. Закрасить клетки под H и слева от V, кроме проходов."""
    L, M = rnd.randint(5, 10), rnd.randint(5, 10)
    x1, x2, y0 = 0, L - 1, 0            # H под строкой y0, столбцы x1..x2
    gh = _gap(rnd, x1, x2)
    gv = _gap(rnd, y0 + 1, y0 + M)      # V правее столбца x2, строки y0+1..y0+M
    walls = hwall(y0, x1, x2, gh) | vwall(x2, y0 + 1, y0 + M, gv)
    expected = {(x, y0 + 1) for x in range(x1, x2 + 1) if not gh[0] <= x <= gh[1]}
    expected |= {(x2, y) for y in range(y0 + 1, y0 + M + 1) if not gv[0] <= y <= gv[1]}
    return Field(walls=walls, robot=(x1, y0 + 1), expected=expected)


def gen_K2(rnd: random.Random) -> Field:
    """7208: H вправо до угла, V вниз; проход только в H. Робот слева от V у её нижнего
    конца. Закрасить клетки над и под H, кроме прохода."""
    L, M = rnd.randint(5, 10), rnd.randint(4, 9)
    x1, x2, y0 = 0, L - 1, 0
    gh = _gap(rnd, x1, x2)
    walls = hwall(y0, x1, x2, gh) | vwall(x2, y0 + 1, y0 + M)
    expected = {(x, y0) for x in range(x1, x2 + 1) if not gh[0] <= x <= gh[1]}
    expected |= {(x, y0 + 1) for x in range(x1, x2 + 1) if not gh[0] <= x <= gh[1]}
    return Field(walls=walls, robot=(x2, y0 + M), expected=expected)


def gen_K4(rnd: random.Random, robot_under_lower: bool) -> Field:
    """7215/7216: две одинаковые горизонтальные стены, левые края на одном уровне,
    расстояние > 1 клетки. Робот под верхней (7215) или под нижней (7216) в
    произвольном столбце. Закрасить клетки под обеими стенами."""
    L = rnd.randint(4, 10)
    d = rnd.randint(2, 6)
    x1, x2 = 0, L - 1
    yu, yl = 0, d                       # верхняя под строкой 0, нижняя под строкой d
    walls = hwall(yu, x1, x2) | hwall(yl, x1, x2)
    expected = {(x, yu + 1) for x in range(x1, x2 + 1)} | {(x, yl + 1) for x in range(x1, x2 + 1)}
    rx = rnd.randint(x1, x2)
    robot = (rx, yl + 1) if robot_under_lower else (rx, yu + 1)
    return Field(walls=walls, robot=robot, expected=expected)


def gen_K6(rnd: random.Random, variant: str) -> Field:
    """7218–7223: V вертикальная (правее столбца 0, строки 0..M-1); H отходит вправо от
    её верхнего (7218) или нижнего (остальные) конца. Позиция Робота и цель по варианту."""
    M, L = rnd.randint(3, 9), rnd.randint(3, 9)
    xv = 0                              # V между столбцами 0 и 1
    walls = vwall(xv, 0, M - 1)
    left_of_v = {(xv, y) for y in range(M)}
    right_of_v = {(xv + 1, y) for y in range(M)}
    if variant == "7218":
        # H от верхнего конца вправо: под строкой -1, столбцы 1..L
        walls |= hwall(-1, xv + 1, xv + L)
        above_h = {(x, -1) for x in range(xv + 1, xv + L + 1)}
        return Field(walls=walls, robot=(xv, M - 1), expected=left_of_v | above_h)
    # H от нижнего конца вправо: под строкой M-1, столбцы 1..L
    walls |= hwall(M - 1, xv + 1, xv + L)
    above_h = {(x, M - 1) for x in range(xv + 1, xv + L + 1)}
    below_h = {(x, M) for x in range(xv + 1, xv + L + 1)}
    corner = (xv + 1, M - 1)
    if variant == "7219":
        return Field(walls=walls, robot=(xv + L, M - 1), expected=right_of_v)
    if variant == "7220":
        return Field(walls=walls, robot=(xv + L, M - 1), expected=right_of_v | above_h)
    if variant == "7221":
        return Field(walls=walls, robot=(xv + L, M - 1), expected=(right_of_v | above_h) - {corner})
    ry = rnd.randint(0, M - 1)
    if variant == "7222":
        return Field(walls=walls, robot=(xv, ry), expected=below_h)
    if variant == "7223":
        return Field(walls=walls, robot=(xv, ry), expected=left_of_v)
    raise ValueError(variant)


def gen_K5(rnd: random.Random) -> Field:
    """7217: лестница по рисунку задания (SVG прода): от Робота (0,0) вниз-влево kl
    ступеней (высота 1, ширина 2), затем вниз-вправо kr ступеней. Закрасить клетки
    над ступенями правой (спускающейся слева направо) части."""
    kl, kr = rnd.randint(1, 5), rnd.randint(1, 6)
    walls: Set[Edge] = set()
    # левая часть: подступёнок i на границе x=-2i (строка i), ступень i на границе y=i+1
    for i in range(kl):
        walls.add(edge((-2 * i - 1, i), (-2 * i, i)))
        for x in (-2 * i - 2, -2 * i - 1):
            walls.add(edge((x, i), (x, i + 1)))
    walls.add(edge((-2 * kl - 1, kl), (-2 * kl, kl)))       # нижний подступёнок слева
    expected: Set[Cell] = set()
    x0 = -2 * kl
    for j in range(kr):
        cols = (x0 + 2 * j, x0 + 2 * j + 1)
        for x in cols:
            walls.add(edge((x, kl + j), (x, kl + j + 1)))     # ступень j
            expected.add((x, kl + j))
        if j < kr - 1:
            walls.add(edge((x0 + 2 * j + 1, kl + j + 1), (x0 + 2 * j + 2, kl + j + 1)))
    return Field(walls=walls, robot=(0, 0), expected=expected)


def gen_K7(rnd: random.Random, upper: bool) -> Field:
    """7224/7225: прямоугольник W×H, Робот внутри. Закрасить верхние/нижние углы."""
    W, H = rnd.randint(3, 9), rnd.randint(3, 9)
    walls: Set[Edge] = set()
    walls |= hwall(-1, 0, W - 1) | hwall(H - 1, 0, W - 1)
    walls |= vwall(-1, 0, H - 1) | vwall(W - 1, 0, H - 1)
    robot = (rnd.randint(0, W - 1), rnd.randint(0, H - 1))
    expected = {(0, 0), (W - 1, 0)} if upper else {(0, H - 1), (W - 1, H - 1)}
    return Field(walls=walls, robot=robot, expected=expected)


# ---------------------------------------------------------------------------
# Канонические алгоритмы
# ---------------------------------------------------------------------------

ALG_K1 = """нц пока справа свободно
  если не сверху свободно то
    закрасить
  все
  вправо
кц
нц пока не справа свободно
  закрасить
  вниз
кц
нц пока справа свободно
  вниз
кц
нц пока не справа свободно
  закрасить
  вниз
кц
"""

ALG_K2 = """нц пока сверху свободно
  вверх
кц
нц пока не сверху свободно
  закрасить
  влево
кц
нц пока сверху свободно
  влево
кц
нц пока не сверху свободно
  закрасить
  влево
кц
вверх
вправо
нц пока не снизу свободно
  закрасить
  вправо
кц
нц пока снизу свободно
  вправо
кц
нц пока не снизу свободно
  закрасить
  вправо
кц
"""

ALG_7215 = """нц пока не сверху свободно
  влево
кц
вправо
нц пока не сверху свободно
  закрасить
  вправо
кц
влево
нц пока снизу свободно
  вниз
кц
вправо
вниз
влево
нц пока не сверху свободно
  закрасить
  влево
кц
"""

ALG_7216 = """нц пока не сверху свободно
  влево
кц
вправо
нц пока не сверху свободно
  закрасить
  вправо
кц
вверх
влево
нц пока сверху свободно
  вверх
кц
нц пока не сверху свободно
  закрасить
  влево
кц
"""

ALG_7217 = """вниз
нц пока слева свободно
  влево
  влево
  если снизу свободно то
    вниз
  все
кц
нц пока не снизу свободно
  закрасить
  вправо
  закрасить
  вправо
  вниз
кц
"""

ALG_7218 = """нц пока не справа свободно
  закрасить
  вверх
кц
вправо
нц пока не снизу свободно
  закрасить
  вправо
кц
"""

ALG_7219 = """нц пока слева свободно
  влево
кц
нц пока не слева свободно
  закрасить
  вверх
кц
"""

ALG_7220 = """нц пока слева свободно
  закрасить
  влево
кц
нц пока не слева свободно
  закрасить
  вверх
кц
"""

ALG_7221 = """нц пока слева свободно
  закрасить
  влево
кц
вверх
нц пока не слева свободно
  закрасить
  вверх
кц
"""

ALG_7222 = """нц пока не справа свободно
  вниз
кц
вправо
нц пока не сверху свободно
  закрасить
  вправо
кц
"""

ALG_7223 = """нц пока не справа свободно
  вверх
кц
вниз
нц пока не справа свободно
  закрасить
  вниз
кц
"""

ALG_7224 = """нц пока сверху свободно
  вверх
кц
нц пока слева свободно
  влево
кц
закрасить
нц пока справа свободно
  вправо
кц
закрасить
"""

# Задание → (генератор канонического поля, канонический алгоритм, цепочка симметрий)
TASKS: Dict[int, Tuple[Callable[[random.Random], Field], str, List[str]]] = {
    7201: (gen_K1, ALG_K1, []),
    7202: (gen_K1, ALG_K1, ["flipV"]),
    7203: (gen_K1, ALG_K1, ["flipV", "flipH"]),
    7204: (gen_K1, ALG_K1, ["flipH"]),
    7205: (gen_K1, ALG_K1, ["T"]),
    7206: (gen_K1, ALG_K1, ["T", "flipH"]),
    7207: (gen_K1, ALG_K1, ["T", "flipH", "flipV"]),
    7208: (gen_K2, ALG_K2, []),
    7209: (gen_K2, ALG_K2, ["flipV"]),
    7210: (gen_K2, ALG_K2, ["flipV", "flipH"]),
    7211: (gen_K2, ALG_K2, ["T", "flipV"]),
    7212: (gen_K2, ALG_K2, ["T", "flipV", "flipH"]),
    7213: (gen_K2, ALG_K2, ["T", "flipH"]),
    7214: (gen_K2, ALG_K2, ["T"]),
    7215: (lambda r: gen_K4(r, False), ALG_7215, []),
    7216: (lambda r: gen_K4(r, True), ALG_7216, []),
    7217: (gen_K5, ALG_7217, []),
    7218: (lambda r: gen_K6(r, "7218"), ALG_7218, []),
    7219: (lambda r: gen_K6(r, "7219"), ALG_7219, []),
    7220: (lambda r: gen_K6(r, "7220"), ALG_7220, []),
    7221: (lambda r: gen_K6(r, "7221"), ALG_7221, []),
    7222: (lambda r: gen_K6(r, "7222"), ALG_7222, []),
    7223: (lambda r: gen_K6(r, "7223"), ALG_7223, []),
    7224: (lambda r: gen_K7(r, True), ALG_7224, []),
    7225: (lambda r: gen_K7(r, True), ALG_7224, ["flipV"]),
}
# 7217 (лестница): геометрия снята с SVG условия на проде (media ecc1b085…/2754c98f…).


def reference_program(task_id: int) -> str:
    gen, alg, seq = TASKS[task_id]
    return transform_program(alg, seq)


def check_task(task_id: int, trials: int = 200, seed: int = 950) -> Tuple[int, List[str]]:
    rnd = random.Random(seed + task_id)
    gen, alg, seq = TASKS[task_id]
    prog = transform_program(alg, seq)
    failures: List[str] = []
    for _ in range(trials):
        fld = transform_field(gen(rnd), seq)
        try:
            run(prog, fld)
        except RobotError as e:
            failures.append(str(e))
            continue
        if fld.painted != fld.expected:
            extra = sorted(fld.painted - fld.expected)[:3]
            miss = sorted(fld.expected - fld.painted)[:3]
            failures.append(f"лишние {extra}, пропущены {miss}")
    return trials, failures


if __name__ == "__main__":
    total_bad = 0
    for tid in sorted(TASKS):
        n, fails = check_task(tid)
        status = "OK " if not fails else "BAD"
        total_bad += bool(fails)
        print(f"[{status}] {tid}: {n - len(fails)}/{n}" + (f"  например: {fails[0]}" if fails else ""))
    print("заданий с ошибками:", total_bad)
