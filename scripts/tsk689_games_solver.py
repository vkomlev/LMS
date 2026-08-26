# -*- coding: utf-8 -*-
"""tsk-689: решатель задач 19-21 по эталонному алгоритму оператора.

АЛГОРИТМ — ДОСЛОВНО ТОТ, ЧТО ДАЛ ОПЕРАТОР (26.08), обобщён ровно по двум осям,
которые он сам и назвал: условие конца игры и список ходов.

    def f(s):
        if s >= K: return 0
        results = [f(s + 1), f(s * 2)]
        negative = [i for i in results if i <= 0]
        if negative: return -max(negative) + 1
        else:        return -max(results)

Чтение результата (проверено прогоном, а не выведено из вида кода):
  f(s) > 0  — ходящий выигрывает, модуль = за сколько ходов;
  f(s) <= 0 — ходящий проигрывает, модуль = за сколько ходов его добьют;
  0         — позиция конца игры.

Отсюда прямое соответствие трём вопросам ЕГЭ:
  задание 19 «Петя не может выиграть за один ход, но при любом его ходе Ваня
              выигрывает своим первым»            ->  f(S) == -1
  задание 20 «у Пети выигрышная стратегия: не первым ходом, но вторым»
                                                   ->  f(S) ==  2
  задание 21 «у Вани есть выигрыш первым ИЛИ вторым ходом, но нет
              гарантированного первым»             ->  f(S) == -2

ЕДИНСТВЕННОЕ ОБОБЩЕНИЕ СВЕРХ ЭТОГО — игры с двумя диапазонами окончания
(«если камней от K1 до K2, победил сделавший ход, иначе противник»). Позиция
конца, в которую пришедший ПРОИГРАЛ, получает значение 1, а не 0: для того, кто
в неё сходил, это не победа. Ветки алгоритма при этом не меняются.

Скрипт ничего не пишет в базу — только считает и печатает.
"""
from __future__ import annotations

import os
import sys
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.setrecursionlimit(100000)

Position = object  # int для одной кучи, tuple для двух


class Game:
    """Игра 19-21: список ходов + условие конца."""

    def __init__(
        self,
        name: str,
        moves: Callable[[Position], Iterable[Position]],
        is_end: Callable[[Position], bool],
        starts: Sequence[Position],
        *,
        arriver_lost: Optional[Callable[[Position], bool]] = None,
        label: Callable[[Position], str] = str,
    ) -> None:
        self.name = name
        self._moves = moves
        self._is_end = is_end
        self.starts = list(starts)
        # Позиция конца, в которой ПРИШЕДШИЙ проиграл (игры с двумя диапазонами).
        self._arriver_lost = arriver_lost or (lambda s: False)
        self.label = label
        self._memo: Dict[Position, int] = {}

    def f(self, s: Position) -> int:
        """Значение позиции. Обход итеративный, со своим стеком.

        Рекурсия здесь не годится: в задании 3498 куча растёт до 65535 ходом «+1»,
        то есть глубина обхода — десятки тысяч кадров, и Python падает раньше,
        чем считает. Порядок вычисления и сами ветки — те же, что в алгоритме
        оператора; меняется только способ дойти до дна.
        """
        cached = self._memo.get(s)
        if cached is not None:
            return cached

        stack = [s]
        while stack:
            cur = stack[-1]
            if cur in self._memo:
                stack.pop()
                continue
            if self._is_end(cur):
                self._memo[cur] = 1 if self._arriver_lost(cur) else 0
                stack.pop()
                continue
            # Ход, не меняющий позицию, ходом не является: «уменьшить вдвое» из
            # нуля оставляет ноль и зациклило бы обход. В играх на рост такого нет.
            children = [m for m in self._moves(cur) if m != cur]
            if not children:
                raise RuntimeError(f"{self.name}: из позиции {cur} нет ходов, а конец не объявлен")
            pending = [m for m in children if m not in self._memo]
            if pending:
                stack.extend(pending)
                continue
            results = [self._memo[m] for m in children]
            negative = [i for i in results if i <= 0]
            self._memo[cur] = -max(negative) + 1 if negative else -max(results)
            stack.pop()
        return self._memo[s]

    # --- Наборы под формулировки вопросов ---
    # Формулировка вопроса 19 в банке встречается в трёх видах, и они дают РАЗНЫЕ
    # множества. Считать «19 = f(S) == -1» для всех — ошибка: половина боевых
    # эталонов на этом расходится (проверено калибровкой).
    #   A «Петя не может выиграть за один ход, но при ЛЮБОМ ходе Пети Ваня
    #      выигрывает своим первым»                       -> f(S) == -1
    #   B «Ваня выиграл своим первым ходом после (неудачного) первого хода Пети;
    #      когда такая ситуация возможна»                 -> есть ход в позицию,
    #      где ходящий (Ваня) выигрывает сразу            -> ∃ m: f(m) == 1
    #   C «Петя выигрывает своим первым ходом»            -> f(S) == 1

    def set_a(self) -> List[Position]:
        return [s for s in self.starts if self.f(s) == -1]

    def set_b(self) -> List[Position]:
        return [
            s for s in self.starts
            if not self._is_end(s) and any(self.f(m) == 1 for m in self._moves(s))
        ]

    def set_c(self) -> List[Position]:
        return [s for s in self.starts if self.f(s) == 1]

    def set_win_second(self) -> List[Position]:
        """Задание 20: Петя не выигрывает первым ходом, но выигрывает вторым."""
        return [s for s in self.starts if self.f(s) == 2]

    def set_lose_second(self) -> List[Position]:
        """Задание 21: Ваня выигрывает первым или вторым, но не гарантированно первым."""
        return [s for s in self.starts if self.f(s) == -2]

    def named(self, kind: str) -> List[Position]:
        return {
            "A": self.set_a,
            "B": self.set_b,
            "C": self.set_c,
            "W2": self.set_win_second,
            "L2": self.set_lose_second,
        }[kind]()

    def report(self) -> str:
        fmt = lambda xs: ", ".join(self.label(x) for x in xs) or "—"
        return (
            f"{self.name}\n"
            f"  A  (f = -1, «при любом ходе Пети Ваня выигрывает первым»): {fmt(self.set_a())}\n"
            f"  B  («Ваня выиграл первым ходом после неудачного хода Пети»): {fmt(self.set_b())}\n"
            f"  C  (f =  1, «Петя выигрывает первым ходом»):                {fmt(self.set_c())}\n"
            f"  W2 (f =  2, задание 20):                                    {fmt(self.set_win_second())}\n"
            f"  L2 (f = -2, задание 21):                                    {fmt(self.set_lose_second())}"
        )


# ---------- Конструкторы типовых игр ----------


def one_pile_grow(name: str, adds: Sequence[int], muls: Sequence[int], k: int,
                  s_lo: int, s_hi: int) -> Game:
    """Одна куча, игра на рост: конец при s >= k."""
    def moves(s: int) -> List[int]:
        return [s + a for a in adds] + [s * m for m in muls]
    return Game(name, moves, lambda s: s >= k, range(s_lo, s_hi + 1))


def one_pile_shrink(name: str, subs: Sequence[int], divs: Sequence[float], k: int,
                    s_lo: int, s_hi: int, *, div_up: bool = False) -> Game:
    """Одна куча, игра на убывание: конец при s <= k.

    `divs` — делители; округление по умолчанию вниз (как в условиях ЕГЭ),
    `div_up=True` — вверх. Дробный делитель (1.5) считается целочисленно.
    """
    def apply_div(s: int, d: float) -> int:
        if float(d).is_integer():
            q, r = divmod(s, int(d))
            return q + 1 if (div_up and r) else q
        num = int(round(d * 2))  # 1.5 -> 3, делим как s*2//3
        q, r = divmod(s * 2, num)
        return q + 1 if (div_up and r) else q

    def moves(s: int) -> List[int]:
        out = [s - x for x in subs if s - x >= 0]
        out += [apply_div(s, d) for d in divs]
        return out
    return Game(name, moves, lambda s: s <= k, range(s_lo, s_hi + 1))


def two_piles_grow(name: str, adds: Sequence[int], muls: Sequence[int], k: int,
                   fixed: int, s_lo: int, s_hi: int) -> Game:
    """Две кучи, конец при сумме >= k; первая куча фиксирована, вторая — S."""
    def moves(p: Tuple[int, int]) -> List[Tuple[int, int]]:
        a, b = p
        out: List[Tuple[int, int]] = []
        for x in adds:
            out += [(a + x, b), (a, b + x)]
        for m in muls:
            out += [(a * m, b), (a, b * m)]
        return out
    return Game(
        name, moves, lambda p: p[0] + p[1] >= k,
        [(fixed, s) for s in range(s_lo, s_hi + 1)],
        label=lambda p: str(p[1]),
    )


def one_pile_two_ranges(name: str, adds: Sequence[int], muls: Sequence[int],
                        k_lo: int, k_hi: int, s_lo: int, s_hi: int) -> Game:
    """Одна куча: конец при s >= k_lo; если s > k_hi — сходивший проиграл."""
    def moves(s: int) -> List[int]:
        return [s + a for a in adds] + [s * m for m in muls]
    return Game(
        name, moves, lambda s: s >= k_lo, range(s_lo, s_hi + 1),
        arriver_lost=lambda s: s > k_hi,
    )


# ---------- Калибровка: задания, у которых все три ответа уже есть ----------

def agg(kind: str, values: List[int]) -> List[int]:
    """Свёртка множества под формулировку ответа."""
    v = sorted(values)
    if kind == "min":
        return v[:1]
    if kind == "max":
        return v[-1:]
    if kind == "two_min":
        return v[:2]
    if kind == "min_max":
        return [v[0], v[-1]] if v else []
    if kind == "count":
        return [len(v)]
    if kind == "all":
        return v
    raise ValueError(kind)


# (игра, id, (тип19, свёртка19, эталон), (тип20, свёртка20, эталон), (тип21, свёртка21, эталон))
CALIBRATION = [
    (one_pile_grow("3765 одна куча +1 ×2, конец >53", [1], [2], 54, 1, 53), "3765",
     ("B", "min", [14]), ("W2", "two_min", [13, 25]), ("L2", "min", [24])),
    (one_pile_grow("3766 одна куча +1 ×4, конец >64", [1], [4], 65, 1, 64), "3766",
     ("B", "min", [5]), ("W2", "two_min", [4, 15]), ("L2", "min", [14])),
    (one_pile_grow("3767 одна куча +1 ×3, конец >=38", [1], [3], 38, 1, 37), "3767",
     ("B", "min", [5]), ("W2", "two_min", [4, 11]), ("L2", "min", [10])),
    (one_pile_grow("3470 одна куча +1 +4 ×3, конец >=67", [1, 4], [3], 67, 1, 66), "3470",
     ("A", "min", [22]), ("W2", "two_min", [18, 21]), ("L2", "min", [17])),
    (one_pile_grow("10028 одна куча +3 +5 ×3, конец >=97", [3, 5], [3], 97, 1, 96), "10028",
     ("A", "min", [30]), ("W2", "two_min", [10, 25]), ("L2", "min", [22])),
    (one_pile_grow("2203 одна куча +2 +5 ×2, конец >=128", [2, 5], [2], 128, 2, 126), "2203",
     ("A", "min", [62]), ("W2", "two_min", [31, 57]), ("L2", "min", [55])),
    (one_pile_grow("9518 одна куча +1 +5 ×4, конец >=205", [1, 5], [4], 205, 1, 204), "9518",
     ("A", "min", [51]), ("W2", "two_min", [46, 50]), ("L2", "min", [45])),
    (one_pile_shrink("4579 одна куча -3 -5 //4, конец <=60", [3, 5], [4], 60, 61, 400), "4579",
     ("A", "min", [244]), ("W2", "two_min", [247, 248]), ("L2", "min", [252])),
    (one_pile_shrink("9505 одна куча -2 -5 //3, конец <=31", [2, 5], [3], 31, 32, 300), "9505",
     ("A", "min", [96]), ("W2", "two_min", [98, 99]), ("L2", "min", [100])),
    (one_pile_shrink("2204 одна куча -3 -7 //3, конец <=11", [3, 7], [3], 11, 12, 200), "2204",
     ("A", "min", [36]), ("W2", "two_min", [39, 40]), ("L2", "min", [42])),
    (one_pile_shrink("2202 одна куча -2 //1.5, конец <=13", [2], [1.5], 13, 14, 200), "2202",
     ("A", "min", [21]), ("W2", "two_min", [23, 24]), ("L2", "min", [25])),
    (one_pile_shrink("2997 одна куча -3 -4 //2, конец <=15", [3, 4], [2], 15, 16, 200), "2997",
     ("A", "max", [34]), ("W2", "min_max", [35, 69]), ("L2", "min", [39])),
    (two_piles_grow("3472 две кучи +2 ×2, сумма >=42, первая 8", [2], [2], 42, 8, 1, 33), "3472",
     ("C", "min", [17]), ("B", "min", [9]), ("W2", "min", [8])),
    (two_piles_grow("3896 две кучи +1 ×2, сумма >=77, первая 7", [1], [2], 77, 7, 1, 69), "3896",
     ("B", "min", [18]), ("W2", "two_min", [31, 34]), ("L2", "min", [30])),
    (two_piles_grow("4067 две кучи +1 ×2, сумма >=87, первая 9", [1], [2], 87, 9, 1, 77), "4067",
     ("B", "min", [20]), ("W2", "two_min", [34, 38]), ("L2", "min", [33])),
    (two_piles_grow("4580 две кучи +1 ×2, сумма >=123, первая 9", [1], [2], 123, 9, 1, 113), "4580",
     ("B", "min", [29]), ("W2", "two_min", [52, 56]), ("L2", "min", [51])),
    (two_piles_grow("4260 две кучи +1 ×4, сумма >=133, первая 7", [1], [4], 133, 7, 1, 125), "4260",
     ("B", "min", [8]), ("W2", "min_max", [20, 31]), ("L2", "min", [30])),
    (two_piles_grow("4261 две кучи +1 ×4, сумма >=125, первая 7", [1], [4], 125, 7, 1, 117), "4261",
     ("B", "min", [8]), ("W2", "min_max", [12, 29]), ("L2", "min", [28])),
    (one_pile_two_ranges("2079 одна куча +1 ×2, конец 50..70", [1], [2], 50, 70, 1, 49), "2079",
     ("B", "min", [13]), ("W2", "two_min", [24, 47]), ("L2", "min", [46])),
    (one_pile_two_ranges("3329 одна куча +3 +5 ×3, конец 97..105", [3, 5], [3], 97, 105, 1, 96), "3329",
     ("A", "max", [91]), ("W2", "min_max", [30, 88]), ("L2", "count", [3])),
]


def run_calibration() -> int:
    """Сверяет решатель с боевыми эталонами. Возвращает число расхождений."""
    bad = 0
    for game, task_id, q19, q20, q21 in CALIBRATION:
        problems = []
        for num, (kind, how, expected) in (("19", q19), ("20", q20), ("21", q21)):
            values = [int(game.label(x)) for x in game.named(kind)]
            got = agg(how, values)
            if got != expected:
                problems.append(
                    f"{num} ({kind}/{how}): эталон {expected}, решатель {got} "
                    f"(множество {sorted(values)[:14]})"
                )
        if problems:
            bad += 1
            print(f"[РАСХОЖДЕНИЕ] {task_id} {game.name}")
            for p in problems:
                print(f"    {p}")
        else:
            print(f"[ok] {task_id} {game.name}")
    return bad


if __name__ == "__main__":
    print("=== Калибровка решателя на боевых эталонах ===")
    bad = run_calibration()
    print(f"\nРасхождений: {bad} из {len(CALIBRATION)}")
    raise SystemExit(1 if bad else 0)


# ---------- Целевые задания: где вопросов не хватает ----------


def two_piles_shrink_halve(name: str, k: int, fixed: int, s_lo: int, s_hi: int) -> Game:
    """Две кучи: убрать 1 камень или уменьшить кучу вдвое; конец при сумме <= k.

    «Если количество камней нечётно, остаётся на 1 камень меньше, чем убирается»
    — то есть из 9 получается 4 (убирается 5, остаётся 4): целочисленное деление.
    """
    def moves(p):
        a, b = p
        out = []
        if a >= 1:
            out.append((a - 1, b))
        if b >= 1:
            out.append((a, b - 1))
        out.append((a // 2, b))
        out.append((a, b // 2))
        return out
    return Game(name, moves, lambda p: p[0] + p[1] <= k,
                [(fixed, s) for s in range(s_lo, s_hi + 1)],
                label=lambda p: str(p[1]))


def one_pile_no_repeat(name: str, moves_list, k: int, s_lo: int, s_hi: int) -> Game:
    """Одна куча, нельзя повторять СВОЙ предыдущий ход.

    Состояние — (камни, свой прошлый ход, прошлый ход соперника): запрет личный,
    поэтому одной «последней команды» на двоих не хватает.
    """
    def moves(state):
        s, mine, theirs = state
        out = []
        for idx, fn in enumerate(moves_list):
            if idx == mine:
                continue
            out.append((fn(s), theirs, idx))
        return out
    return Game(name, moves, lambda st: st[0] >= k,
                [(s, -1, -1) for s in range(s_lo, s_hi + 1)],
                label=lambda st: str(st[0]))


def one_pile_take_last(name: str, subs, s_lo: int, s_hi: int) -> Game:
    """Одна куча: забравший последний камень выигрывает (конец при 0)."""
    def moves(s):
        return [s - x for x in subs if s - x >= 0]
    return Game(name, moves, lambda s: s == 0, range(s_lo, s_hi + 1))


TARGETS = [
    # (игра, id, что уже есть в задании: (тип, свёртка, эталон))
    (one_pile_shrink("3505 одна куча -2 -5 //3, конец <=19", [2, 5], [3], 19, 20, 300),
     "3505", ("A", "min", [60])),
    (one_pile_take_last("3981 одна куча -1 -2 -4, последний камень выигрывает", [1, 2, 4], 1, 15),
     "3981", ("B", "max", [8])),
    (two_piles_grow("3949 две кучи +1 +2, сумма >=13, первая 3", [1, 2], [], 13, 3, 1, 9),
     "3949", ("BC", "min", [8])),
    (two_piles_shrink_halve("2383 две кучи -1 //2, конец <=20, первая 10", 20, 10, 11, 200),
     "2383", ("W2", "all", [23, 24, 32, 44, 45])),
    (one_pile_no_repeat(
        "2385 одна куча +1 +2 ×2 без повтора своего хода, конец >=29",
        [lambda s: s + 1, lambda s: s + 2, lambda s: s * 2], 29, 1, 28),
     "2385", ("L2", "all", [10, 11])),
]


def run_targets() -> int:
    bad = 0
    for game, task_id, (kind, how, expected) in TARGETS:
        if kind == "BC":
            values = sorted(
                {int(game.label(x)) for x in game.set_b()}
                & {int(game.label(x)) for x in game.set_c()}
            )
        else:
            values = sorted(int(game.label(x)) for x in game.named(kind))
        got = agg(how, values)
        mark = "ok" if got == expected else "РАСХОЖДЕНИЕ"
        if got != expected:
            bad += 1
        print(f"[{mark}] {task_id}: имеющийся вопрос ({kind}/{how}) — эталон {expected}, решатель {got}")
        print(f"        A(19) {sorted(int(game.label(x)) for x in game.set_a())[:10]}")
        print(f"        W2(20) {sorted(int(game.label(x)) for x in game.set_win_second())[:10]}")
        print(f"        L2(21) {sorted(int(game.label(x)) for x in game.set_lose_second())[:10]}")
    return bad


def recover_2384() -> None:
    """Подбор потерянного описания игры для задания 2384 по известному ответу.

    Известно: первая куча 11, вторая S (1..39), ответ на вопрос вида W2
    (минимальное и максимальное) — 22 и 35. Перебираем правдоподобные наборы
    ходов и порогов; совпадение по КРАЯМ множества — сильная улика, но не
    доказательство, поэтому решение всё равно за оператором.
    """
    print("\n=== Подбор игры для 2384 (первая куча 11, S 1..39, W2 = 22 и 35) ===")
    found = []
    for adds in ([1], [2], [1, 2]):
        for muls in ([2], [3], [4], []):
            if not adds and not muls:
                continue
            for k in range(30, 90):
                g = two_piles_grow(f"2384? +{adds} ×{muls} k={k}", adds, muls, k, 11, 1, 39)
                try:
                    vals = sorted(int(g.label(x)) for x in g.set_win_second())
                except RecursionError:
                    continue
                if vals and [vals[0], vals[-1]] == [22, 35]:
                    found.append((adds, muls, k, vals))
    for adds, muls, k, vals in found:
        print(f"  подходит: добавить {adds}, умножить {muls}, конец сумма >= {k}; W2 = {vals}")
    if not found:
        print("  ни один типовой набор не подошёл — описание игры восстановить не удалось")


# ---------- Игры курса 1397 («Сложные»), типовые «кучи камней» ----------


def one_pile_capped(name: str, adds, muls, k: int, cap: int, s_lo: int, s_hi: int) -> Game:
    """Одна куча с общим запасом камней: ход возможен, если итог не больше cap."""
    def moves(s):
        out = [s + a for a in adds] + [s * m for m in muls]
        return [x for x in out if x <= cap]
    return Game(name, moves, lambda s: s >= k, range(s_lo, s_hi + 1))


def toward_target(name: str, steps, target: int, s_lo: int, s_hi: int) -> Game:
    """Ход меняет кучу на один из `steps` строго В СТОРОНУ target; конец — ровно target."""
    def moves(s):
        out = []
        for x in steps:
            nxt = s + x if s < target else s - x
            if abs(nxt - target) < abs(s - target):
                out.append(nxt)
        return out
    return Game(name, moves, lambda s: s == target,
                [s for s in range(s_lo, s_hi + 1) if s != target])


def two_piles_add_to_smaller(name: str, adds, fixed: int, s_lo: int, s_hi: int) -> Game:
    """Две кучи: добавить в МЕНЬШУЮ; конец, когда кучи сравнялись.

    Позиция сведена к РАЗНИЦЕ куч: сами числа для исхода не важны, а по парам
    (a, b) игра бесконечна — добавив две монеты при разнице в одну, игрок меняет
    кучи ролями, и пара растёт без предела (обход на парах падает по памяти).
    Разница же не растёт никогда: d -> d - x или |d - x|. Второй элемент позиции —
    исходное S, он ни на что не влияет и нужен только чтобы подписать ответ.
    """
    def moves(p):
        d, tag = p
        return [(abs(d - x), tag) for x in adds]
    return Game(name, moves, lambda p: p[0] == 0,
                [(abs(fixed - s), s) for s in range(s_lo, s_hi + 1) if s != fixed],
                label=lambda p: str(p[1]))


def two_piles_two_ranges(name: str, adds, muls, k_lo: int, k_hi: int,
                         fixed: int, s_lo: int, s_hi: int) -> Game:
    """Две кучи: конец при сумме >= k_lo; если сумма > k_hi — сходивший проиграл."""
    def moves(p):
        a, b = p
        out = []
        for x in adds:
            out += [(a + x, b), (a, b + x)]
        for m in muls:
            out += [(a * m, b), (a, b * m)]
        return out
    return Game(name, moves, lambda p: p[0] + p[1] >= k_lo,
                [(fixed, s) for s in range(s_lo, s_hi + 1)],
                arriver_lost=lambda p: p[0] + p[1] > k_hi,
                label=lambda p: str(p[1]))


def candies_eat_up_to_five_or_half(name: str, k: int, s_lo: int, s_hi: int) -> Game:
    """Съесть от 1 до 5 конфет либо половину, если их чётное число; конец при s < k."""
    def moves(s):
        out = [s - x for x in range(1, 6) if s - x >= 0]
        if s % 2 == 0:
            out.append(s // 2)
        return out
    return Game(name, moves, lambda s: s < k, range(s_lo, s_hi + 1))


def candies_at_most_half(name: str, s_lo: int, s_hi: int) -> Game:
    """Съесть не более половины оставшихся, но не менее одной; кто съел последнюю — выиграл.

    Из кучи в 1 конфету хода нет (половина от одной — меньше конфеты), поэтому
    единица и есть позиция конца: ходящий проиграл, пришедший выиграл.
    """
    def moves(s):
        return [s - x for x in range(1, s // 2 + 1)]
    return Game(name, moves, lambda s: s <= 1, range(s_lo, s_hi + 1))


def halves_and_thirds(name: str, s_lo: int, s_hi: int) -> Game:
    """Убрать половину / две трети при делимости, иначе убрать 2 / 3; конец — ровно 1 камень."""
    def moves(s):
        out = []
        if s % 2 == 0:
            out.append(s // 2)
        else:
            out.append(s - 2)
        if s % 3 == 0:
            out.append(s // 3)
        else:
            out.append(s - 3)
        return [x for x in out if x >= 1]
    return Game(name, moves, lambda s: s == 1, range(s_lo, s_hi + 1))


HARD_TARGETS = [
    (one_pile_grow("3498 одна куча +1..+32 ×3 ×9 ×27, конец >=65535",
                   [1, 2, 4, 8, 16, 32], [3, 9, 27], 65535, 2, 64999),
     "3498", ("A", "min", [2427])),
    (one_pile_no_repeat("3380 = игра 2385 (запрет повтора своего хода), конец >=29",
                        [lambda s: s + 1, lambda s: s + 2, lambda s: s * 2], 29, 1, 28),
     "3380", ("W2", "min", [12])),
    (one_pile_capped("3594 одна куча +1 +2 ×2, общий запас 50, конец >=41",
                     [1, 2], [2], 41, 50, 1, 40),
     "3594", ("L2", "all", [35])),
    (toward_target("4187 ход на 1/3/7 в сторону 42, конец ровно 42", [1, 3, 7], 42, 1, 100),
     "4187", ("B", "min", [28])),
    (two_piles_add_to_smaller("3518 две кучи, +1/+2 в меньшую, конец при равенстве",
                              [1, 2], 15, 1, 30),
     "3518", ("A", "max", [18])),
    (two_piles_two_ranges("3571 две кучи +10 ×2, конец 107..170, первая 5",
                          [10], [2], 107, 170, 5, 1, 100),
     "3571", ("B", "min", [26])),
    (candies_eat_up_to_five_or_half("4262 конфеты: 1..5 или половина, конец < 10", 10, 10, 200),
     "4262", ("A", "all", [15])),
    (halves_and_thirds("3851 половина/две трети или -2/-3, конец ровно 1", 2, 37),
     "3851", ("BC", "max", [4])),
    (candies_at_most_half("4036 конфеты: не более половины, последняя выигрывает", 10, 99),
     "4036", ("LOSE", "min", [11])),
    (candies_at_most_half("4232 конфеты: не более половины, последняя выигрывает", 10, 99),
     "4232", ("LOSE", "max", [95])),
]


def run_hard() -> int:
    bad = 0
    for game, task_id, (kind, how, expected) in HARD_TARGETS:
        if kind == "BC":
            values = sorted({int(game.label(x)) for x in game.set_b()}
                            & {int(game.label(x)) for x in game.set_c()})
        elif kind == "LOSE":
            values = sorted(int(game.label(x)) for x in game.starts if game.f(x) <= 0)
        else:
            values = sorted(int(game.label(x)) for x in game.named(kind))
        got = agg(how, values)
        mark = "ok" if got == expected else "РАСХОЖДЕНИЕ"
        if got != expected:
            bad += 1
        print(f"[{mark}] {task_id} ({kind}/{how}): эталон {expected}, решатель {got}")
        print(f"        A(19) {sorted(int(game.label(x)) for x in game.set_a())[:8]}")
        print(f"        W2(20) {sorted(int(game.label(x)) for x in game.set_win_second())[:8]}")
        print(f"        L2(21) {sorted(int(game.label(x)) for x in game.set_lose_second())[:8]}")
    return bad
