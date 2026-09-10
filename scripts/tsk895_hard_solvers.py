# -*- coding: utf-8 -*-
"""tsk-895: точные методы для пяти заданий класса "образ функции на большом
пространстве", где прямой перебор по N неподъёмен (сотни миллионов - 10^16
кандидатов). Перебором здесь эталон не проверить принципиально — ниже не
эвристика, а доказанно точный счёт (непересекающиеся блоки [k*N, k*N+c_max]
для 2291/2290, комбинаторный подсчёт по сумме цифр для 4046, жадное
построение минимального числа для 3696, инкрементальный поиск для 3687 —
для него подъёмность подтверждена прогоном, не предположением).

Все пять результатов сверены с боевым эталоном (D:\\Work\\LMS\\docs\\specs\\
tsk895-task5-dump.json) — расхождений нет. Скрипт ничего не пишет в БД,
только считает и печатает; правка эталонов в этой задаче не требовалась.

Запуск: python scripts/tsk895_hard_solvers.py
"""
from __future__ import annotations

import os
import sys

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# id=2291 (курс 156, магнит заявок помощи): трижды дописать бит по чётности
# суммы ДЕСЯТИЧНЫХ цифр текущего числа; посчитать, сколько чисел из отрезка
# [123 456 789; 1 987 654 321] может получиться в результате.
#
# Метод: R(N) = 8*N + c(N), c(N) в [0,7] — три дописанных бита. Отрезки
# [8N, 8N+7] для разных N не пересекаются и вместе покрывают все целые, но
# R(N) попадает ровно в одну точку СВОЕГО отрезка. Значит для любого N, чей
# ЦЕЛЫЙ отрезок лежит внутри [L,U], R(N) гарантированно в [L,U] — какой бы
# ни была c(N). Считать это НЕ нужно перебором: интервал N с "внутренними"
# блоками даёт прямую арифметику; проверки требуют только граничные N (их
# считаные единицы), где блок мог вылезти за L или за U.
# ---------------------------------------------------------------------------
def r_2291(n: int) -> int:
    v = n
    for _ in range(3):
        s = sum(int(ch) for ch in str(v))
        v = v * 2 + (s % 2)
    return v


def count_2291(lo: int, hi: int) -> int:
    n_lo_full = -(-lo // 8)          # ceil(lo/8)
    n_hi_full = (hi - 7) // 8        # floor((hi-7)/8)
    count = max(0, n_hi_full - n_lo_full + 1)
    for n in range(max(1, n_lo_full - 3), n_lo_full):
        if lo <= r_2291(n) <= hi:
            count += 1
    for n in range(n_hi_full + 1, n_hi_full + 4):
        if lo <= r_2291(n) <= hi:
            count += 1
    return count


# ---------------------------------------------------------------------------
# id=2290 (курс 1383): дописать 2 бита (остаток N mod 3) и затем 3 бита
# (остаток ОТ ПОЛУЧЕННОГО НА ШАГЕ 2 числа mod 5); посчитать, сколько чисел
# из отрезка [1 222 222 222; 1 555 555 666] может получиться.
#
# Метод тот же: R(N) = 32*N + c(N), c(N) в [0,20] < ширина блока 32, значит
# блоки снова не пересекаются, снова считается арифметикой + граница.
# ---------------------------------------------------------------------------
def r_2290(n: int) -> int:
    v1 = n * 4 + (n % 3)
    return v1 * 8 + (v1 % 5)


def count_2290(lo: int, hi: int) -> int:
    n_lo_full = -(-lo // 32)
    n_hi_full = (hi - 20) // 32
    count = max(0, n_hi_full - n_lo_full + 1)
    for n in range(max(1, n_lo_full - 5), n_lo_full):
        if lo <= r_2290(n) <= hi:
            count += 1
    for n in range(n_hi_full + 1, n_hi_full + 6):
        if lo <= r_2290(n) <= hi:
            count += 1
    return count


# ---------------------------------------------------------------------------
# id=4046 (курс 1383): девятиразрядное N (10**8..10**9-1, ~900 млн кандидатов)
# -> R зависит ТОЛЬКО от суммы цифр N (1..81), не от самого N. Значит не
# нужно перебирать N: считаем R для каждой из 81 суммы, находим совпадающие
# с целью, и считаем количество 9-разрядных чисел с этой суммой цифр —
# комбинаторика (DP по разрядам), а не перебор чисел.
# ---------------------------------------------------------------------------
def r_4046_from_sum(s: int) -> int:
    b = bin(s)[2:]
    ones = b.count("1")
    newb = ("1" + b + "00") if ones % 2 == 0 else ("10" + b + "1")
    return int(newb, 2)


def count_nine_digit_with_sum(s: int) -> int:
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def dp(pos: int, remaining: int) -> int:
        if pos == 9:
            return 1 if remaining == 0 else 0
        lo = 1 if pos == 0 else 0
        return sum(
            dp(pos + 1, remaining - d)
            for d in range(lo, 10)
            if remaining - d >= 0
        )

    return dp(0, s)


def count_4046(target_r: int) -> int:
    matching_sums = [s for s in range(1, 82) if r_4046_from_sum(s) == target_r]
    return sum(count_nine_digit_with_sum(s) for s in matching_sums)


# ---------------------------------------------------------------------------
# id=3696 (курс 1383): 16-значное N, контрольная сумма по алгоритму Луна,
# найти МИНИМАЛЬНОЕ N с суммой S=25 (ответ — остаток от деления на 10**15).
#
# Метод: вклад каждой позиции в S лежит в {0..9} независимо от разряда
# (чётная позиция — сама цифра; нечётная — luhn-удвоение, тоже биекция на
# {0..9}) -> сумма k позиций достижима для ЛЮБОГО целого в [0, 9k] без
# пропусков. Значит минимальное число строится жадно слева направо: на
# каждой позиции берём наименьшую цифру, для которой остаток по сумме ещё
# достижим оставшимися позициями (простая проверка диапазона, не перебор
# по всем 10**16 числам).
# ---------------------------------------------------------------------------
def _digitsum(n: int) -> int:
    return sum(int(c) for c in str(n))


def _luhn_contrib(d: int, is_odd_pos: bool) -> int:
    return _digitsum(d * 2) if is_odd_pos else d


def min_number_with_luhn_sum(n_digits: int, target: int) -> str:
    digits_out = [0] * n_digits
    remaining = target
    for idx in range(n_digits):
        pos = n_digits - 1 - idx  # позиция с конца, 0-индексация
        is_odd = pos % 2 == 1
        allowed = range(1, 10) if idx == 0 else range(0, 10)
        positions_left_after = pos
        for d in allowed:
            c = _luhn_contrib(d, is_odd)
            rem_after = remaining - c
            if 0 <= rem_after <= 9 * positions_left_after:
                digits_out[idx] = d
                remaining = rem_after
                break
        else:
            raise RuntimeError(f"нет подходящей цифры на позиции idx={idx}")
    return "".join(str(d) for d in digits_out)


def luhn_S(n_str: str) -> int:
    digits = [int(c) for c in n_str][::-1]
    return sum(
        (_digitsum(d * 2) if pos % 2 == 1 else d) for pos, d in enumerate(digits)
    )


# ---------------------------------------------------------------------------
# id=3687 (курс 1383): найти наименьшее N > 1234567891011121 (16-значное),
# для которого сумма по алгоритму Луна кратна 10.
#
# Это НЕ задача класса "большое пространство" — сумма Луна меняется
# предсказуемо при инкременте N на 1 (почти всегда меняется только младший
# разряд), поэтому ответ гарантированно близко: прямой инкрементальный
# перебор от start+1 находит его за считаные шаги (проверено прогоном —
# 6 шагов), в отличие от четырёх заданий выше, которым перебор
# принципиально недоступен.
# ---------------------------------------------------------------------------
def next_valid_luhn(start: int, limit_steps: int = 1_000_000) -> tuple[int, int]:
    n = start + 1
    for step in range(limit_steps):
        if luhn_S(str(n)) % 10 == 0:
            return n, step
        n += 1
    raise RuntimeError("не найдено в пределах limit_steps — нужен другой метод")


CHECKS = [
    ("2291", lambda: count_2291(123_456_789, 1_987_654_321), 233_024_691),
    ("2290", lambda: count_2290(1_222_222_222, 1_555_555_666), 10_416_669),
    ("4046", lambda: count_4046(21), 9),
    (
        "3696",
        lambda: int(min_number_with_luhn_sum(16, 25)) % (10 ** 15),
        599,
    ),
    (
        "3687",
        lambda: int(str(next_valid_luhn(1_234_567_891_011_121)[0])[-8:]),
        91_011_128,
    ),
]


def main() -> int:
    bad = 0
    for task_id, fn, expected in CHECKS:
        got = fn()
        ok = got == expected
        bad += 0 if ok else 1
        mark = "ok" if ok else "РАСХОЖДЕНИЕ"
        print(f"[{mark}] id={task_id}: эталон {expected}, посчитано {got}")
    print(f"\nРасхождений: {bad} из {len(CHECKS)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
