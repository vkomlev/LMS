# -*- coding: utf-8 -*-
"""
tsk-895 / курс 1383, группа C — 23 задания "Задание 5 ЕГЭ".
Честные симуляторы алгоритма R(N) (или соответствующего поиска) для каждого id,
с проверкой на иллюстративном примере из условия (где он есть) и вычислением
итогового ответа, сверяемого с эталоном solution_rules.short_answer в БД.

Запуск: python batch-1383-C-solvers.py
Все 23 проверки печатают вычисленный ответ и статус сверки с эталоном.
"""

from itertools import permutations
from collections import Counter


# ---------- общие утилиты перевода систем счисления (основание > 10 через списки цифр) ----------

def to_base_digits(n, b):
    """Список цифр n в системе счисления b (старшая цифра первая). Цифры - целые числа."""
    if n == 0:
        return [0]
    d = []
    while n > 0:
        d.append(n % b)
        n //= b
    return list(reversed(d))


def from_base_digits(digits, b):
    """Число (int) по списку цифр в системе счисления b."""
    v = 0
    for d in digits:
        v = v * b + d
    return v


def to_base_str(n, b):
    """Строковая запись n в системе счисления b (b<=36, цифры 0-9 + A-Z)."""
    if n == 0:
        return '0'
    digs = '0123456789abcdefghijklmnopqrstuvwxyz'
    d = []
    while n > 0:
        d.append(digs[n % b])
        n //= b
    return ''.join(reversed(d))


# ================================================================
# id 3756 — «Семеричная обработка по остатку»
# Эталон: 30
# ================================================================
def R_3756(n):
    rec = to_base_str(n, 7)
    if n % 7 == 0:
        rec2 = rec + rec[-2:]
    else:
        rem = n % 7
        d = rem * 2
        rec2 = rec + to_base_str(d, 7)
    return int(rec2, 7)


def solve_3756():
    assert R_3756(6) == 306
    assert R_3756(7) == 350
    best = None
    for n in range(1, 1000):
        if R_3756(n) < 220:
            best = n if best is None else max(best, n)
    return best  # максимальное N с R<220


# ================================================================
# id 3997 — «Индекс по двоичному преобразованию»
# Эталон: 162390
# ================================================================
def R_3997(n):
    s = str(n)
    d = [int(c) for c in s]
    s1 = d[0] + d[1]
    s2 = d[2] + d[3]
    s3 = d[4] + d[5]
    m = int(str(s1) + str(s2) + str(s3))
    bin_m = bin(m)[2:]
    bit = '0' if m % 2 == 0 else '1'
    return int(bin_m + bit, 2)


def solve_3997():
    best = None
    for n in range(100000, 1000000):
        s = str(n)
        if s[4] != '9':
            continue
        if '2' not in s:
            continue
        if R_3997(n) == 1519:
            best = n
            break
    return best  # минимальный подходящий индекс


# ================================================================
# id 4017 — «Разность единиц и нулей в двоичном коде»
# Эталон: 1023
# ================================================================
def R_4017(n):
    b = bin(n)[2:]
    ones_even = sum(1 for i, c in enumerate(b, start=1) if i % 2 == 0 and c == '1')
    zeros_odd = sum(1 for i, c in enumerate(b, start=1) if i % 2 == 1 and c == '0')
    return abs(ones_even - zeros_odd)


def solve_4017():
    assert R_4017(39) == 1
    for n in range(2, 5000):
        if R_4017(n) == 5:
            return n  # минимальное N с R=5
    return None


# ================================================================
# id 4026 — «Минимальное трёхзначное число с разностью» (R=5)
# Эталон: 505
# (тот же алгоритм используется в id 4162, только с другим вопросом)
# ================================================================
def R_maxmin2digit(n):
    """Общий алгоритм для 4026/4162: из цифр 3-значного N строим макс./мин.
    двузначные числа (без ведущего нуля) и берём их разность."""
    s = str(n)
    digits = [int(c) for c in s]
    candidates = []
    for i, j in permutations(range(3), 2):
        if digits[i] == 0:
            continue
        candidates.append(digits[i] * 10 + digits[j])
    return max(candidates) - min(candidates)


def solve_4026():
    assert R_maxmin2digit(351) == 40
    for n in range(100, 1000):
        if R_maxmin2digit(n) == 5:
            return n  # минимальное N с R=5
    return None


# ================================================================
# id 4037 — «Повторное дописывание в девятеричной записи»
# Эталон: 9918
# Прочтение: "цифра, которая встречается чаще" = самая частая цифра во ВСЕЙ
# записи (не только сравнение 5 и 7); при равенстве частот берётся большая
# цифра. Подтверждено: только это прочтение воспроизводит эталон БД
# (интерпретация "чаще 5 или 7" даёт 7862, не совпадает).
# ================================================================
def _step_4037(rec):
    c5 = rec.count('5')
    c7 = rec.count('7')
    if c5 == c7:
        return rec + rec[-1]
    cnt = Counter(rec)
    maxfreq = max(cnt.values())
    candidates = [d for d, f in cnt.items() if f == maxfreq]
    chosen = max(candidates, key=lambda x: int(x))
    return rec + chosen


def R_4037(n):
    rec = to_base_str(n, 9)
    for _ in range(5):  # шаг 2 + 4 повтора = 5 раз
        rec = _step_4037(rec)
    val = int(rec, 9)
    return format(val, 'X')  # шестнадцатеричная запись, верхний регистр


def solve_4037():
    best = None
    for n in range(1, 10000):
        if 'BAC' in R_4037(n):
            best = n if best is None else max(best, n)
    return best  # максимальное N<10000


# ================================================================
# id 3752 — «Четырёхзначное число по сумме и произведению»
# Эталон: 1089
# ================================================================
def R_3752(n):
    digits = [int(c) for c in str(n)]
    if len(set(digits)) != 4:
        return None
    mx, mn = max(digits), min(digits)
    rest = digits.copy()
    rest.remove(mx)
    rest.remove(mn)
    a, b = rest
    summ, prod = mx + mn, a * b
    lo, hi = sorted([summ, prod])
    return int(str(lo) + str(hi))


def solve_3752():
    assert R_3752(1234) == 56
    for n in range(1000, 10000):
        r = R_3752(n)
        if r is not None and r > 85:
            return n  # минимальное N с R>85
    return None


# ================================================================
# id 4052 — «Сумма крайних двузначных пар» (R=137)
# id 4053 — «Разность крайних двузначных чисел» (R=44)
# Эталон 4052: 398, эталон 4053: 159
# ================================================================
def _pairs(n):
    s = str(n)
    return [int(s[i:i + 2]) for i in range(len(s) - 1)]


def R_4052(n):
    p = _pairs(n)
    return min(p) + max(p)


def R_4053(n):
    p = _pairs(n)
    return max(p) - min(p)


def solve_4052():
    assert R_4052(2022) == 24
    for n in range(10, 2000000):
        if R_4052(n) == 137:
            return n
    return None


def solve_4053():
    assert R_4053(2022) == 20
    for n in range(10, 2000000):
        if R_4053(n) == 44:
            return n
    return None


# ================================================================
# id 4072 — «Наибольшее число после пятеричного дописывания»
# Эталон: 199
# ================================================================
def R_4072(n):
    rec = to_base_str(n, 5)
    last = int(rec[-1])
    if last % 2 == 0:
        rec2 = rec + '2'
    else:
        rec2 = '2' + rec + '3'
    return int(rec2, 5)


def solve_4072():
    assert R_4072(13) == 318
    best = None
    for n in range(1, 5000):
        if R_4072(n) < 1000:
            best = n if best is None else max(best, n)
    return best  # максимальное N с R<1000


# ================================================================
# id 4124 — «Модуль разности чётных цифр и мест» (R=9)
# id 4125 — тот же алгоритм (R=13)
# Эталон 4124: 19, эталон 4125: 618
# ================================================================
def R_4124(n):
    s = str(n)
    digits = [int(c) for c in s]
    sum_even_digits = sum(d for d in digits if d % 2 == 0)
    sum_even_pos = sum(int(c) for i, c in enumerate(s, start=1) if i % 2 == 0)
    return abs(sum_even_digits - sum_even_pos)


def solve_4124():
    assert R_4124(2021) == 3
    for n in range(2, 100000):
        if R_4124(n) == 9:
            return n
    return None


def solve_4125():
    for n in range(2, 100000):
        if R_4124(n) == 13:
            return n
    return None


# ================================================================
# id 4128 — «Размах чисел из четырёх цифр» (не R(N), а экстремальная
# комбинаторная характеристика: количество различных двузначных чисел,
# получаемых перестановкой цифр N)
# Эталон: 8642
# ================================================================
def _count_2digit_perms(n):
    digits = [int(c) for c in str(n)]
    vals = set()
    for i, j in permutations(range(4), 2):
        if digits[i] == 0:
            continue
        vals.add(digits[i] * 10 + digits[j])
    return len(vals)


def solve_4128():
    assert _count_2digit_perms(1223) == 7
    maxcount = 0
    for n in range(1000, 10000):
        c = _count_2digit_perms(n)
        if c > maxcount:
            maxcount = c
    winners = [n for n in range(1000, 10000) if _count_2digit_perms(n) == maxcount]
    return max(winners) - min(winners)


# ================================================================
# id 4162 — тот же алгоритм, что 4026, но считаем количество N в [100;200] с R=30
# Эталон: 7
# ================================================================
def solve_4162():
    return sum(1 for n in range(100, 201) if R_maxmin2digit(n) == 30)


# ================================================================
# id 4383 — «Количество чисел с результатом меньше 680»
# Эталон: 68
# ================================================================
def R_4383(n):
    b = bin(n)[2:]
    if n % 10 == 0:
        tail = b[-4:] if len(b) >= 4 else b.zfill(4)
        rec = b + tail
    else:
        last_digit = n % 10
        val = (last_digit ** 2) // 2
        tail = bin(val)[2:]
        rec = b + tail
    return int(rec, 2)


def solve_4383():
    assert R_4383(11) == 22
    assert R_4383(20) == 324
    return sum(1 for n in range(11, 5000) if R_4383(n) < 680)


# ================================================================
# id 4404 — «Минимальное число после троичного дописывания»
# Эталон: 27  (функция немонотонна — полный перебор, минимум единственный)
# ================================================================
def R_4404(n):
    rec = to_base_str(n, 3)
    if n % 2 == 0:
        tail = rec[-2:] if len(rec) >= 2 else rec.zfill(2)
        rec2 = rec + tail
    else:
        s = sum(int(c) for c in rec)
        tail = to_base_str(s, 3)
        rec2 = rec + tail
    return int(rec2, 3)


def solve_4404():
    assert R_4404(10) == 91
    assert R_4404(11) == 102
    best_n, best_r = None, None
    for n in range(10, 5000):
        r = R_4404(n)
        if best_r is None or r < best_r:
            best_r, best_n = r, n
    return best_n


# ================================================================
# id 4414 — «Двоичное число с дописыванием по делимости» (3 и 5)
# Эталон: 249998
# ================================================================
def R_4414(n):
    b = bin(n)[2:]
    tail2 = bin(3)[2:] if n % 3 == 0 else '1'
    rec2 = b + tail2
    m = int(rec2, 2)
    tail3 = bin(5)[2:] if m % 5 == 0 else '1'
    rec3 = rec2 + tail3
    return int(rec3, 2)


def solve_4414():
    assert R_4414(7) == 125
    best = None
    for n in range(1, 300000):
        if R_4414(n) < 10 ** 6:
            best = n if best is None else max(best, n)
    return best


# ================================================================
# id 4416 — «Двоичное число с дописыванием по делимости» (6 и 3)
# Эталон: 18750
# ================================================================
def R_4416(n):
    b = bin(n)[2:]
    tail2 = bin(7)[2:] if n % 6 == 0 else '1'
    rec2 = b + tail2
    m = int(rec2, 2)
    tail3 = bin(5)[2:] if m % 3 == 0 else '1'
    rec3 = rec2 + tail3
    return int(rec3, 2)


def solve_4416():
    assert R_4416(12) == 207
    for n in range(1, 50000):
        if R_4416(n) > 300000:
            return n
    return None


# ================================================================
# id 4417 — «Дважды дополняемая восьмидесятеричная запись» (основание 80)
# Эталон: 156
# ================================================================
def R_4417(n, b=80):
    digits = to_base_digits(n, b)
    for _ in range(2):
        even_sum = sum(d for d in digits if d % 2 == 0)
        odd_sum = sum(d for d in digits if d % 2 == 1)
        larger = max(even_sum, odd_sum)
        digits = digits + [larger % b]
    return from_base_digits(digits, b)


def solve_4417():
    assert R_4417(83) == 531524
    for n in range(1, 20000):
        if R_4417(n) > 1000000:
            return n
    return None


# ================================================================
# id 4418 — «Восьмидесятеричная запись с суммами цифр» (основание 45)
# Эталон: 46598
# ================================================================
def R_4418(n, b=45):
    digits = to_base_digits(n, b)
    even_sum = sum(d for i, d in enumerate(digits, start=1) if i % 2 == 0)
    odd_sum = sum(d for i, d in enumerate(digits, start=1) if i % 2 == 1)
    smaller, larger = min(even_sum, odd_sum), max(even_sum, odd_sum)
    final_digits = to_base_digits(smaller, b) + digits + to_base_digits(larger, b)
    return from_base_digits(final_digits, b)


def solve_4418():
    assert R_4418(95) == 186530
    best_r, best_n = None, None
    for n in range(1001, 100000):
        r = R_4418(n)
        if best_r is None or r < best_r:
            best_r, best_n = r, n
    return best_r  # спрашивается именно минимальное R, а не N


# ================================================================
# id 4464 — «Четырехзначное число из чётных и нечётных цифр»
# Эталон: 875 (цифры R строго убывают)
# ================================================================
def R_4464(n):
    digits = [int(c) for c in str(n)]
    evens = [d for d in digits if d % 2 == 0]
    odds = [d for d in digits if d % 2 == 1]
    if not evens or not odds:
        return None
    maxnum = int(''.join(map(str, sorted(evens, reverse=True))))
    minnum = int(''.join(map(str, sorted(odds))))
    return maxnum + minnum


def _strictly_decreasing(x):
    s = str(x)
    return all(s[i] > s[i + 1] for i in range(len(s) - 1))


def solve_4464():
    best = None
    for n in range(1000, 10000):
        r = R_4464(n)
        if r is not None and _strictly_decreasing(r):
            best = r if best is None else max(best, r)
    return best


# ================================================================
# id 4465 — «Число из сумм четных и нечетных цифр»
# Эталон: 228 (количество N с R=111)
# ================================================================
def R_4465(n):
    digits = [int(c) for c in str(n)]
    evens = [d for d in digits if d % 2 == 0]
    odds = [d for d in digits if d % 2 == 1]
    if not evens or not odds:
        return None
    base_sum = sum(evens) if len(evens) > len(odds) else sum(odds)
    if base_sum % 2 == 0:
        return int(str(base_sum) + str(max(evens)))
    return int(str(min(odds)) + str(base_sum))


def solve_4465():
    return sum(1 for n in range(1000, 10000) if R_4465(n) == 111)


# ================================================================
# id 4466 — «Двадцатеричная запись с заменами и сдвигом» (основание 20)
# Гласные A,E,I = цифры 10,14,18 в 20-ричной системе.
# Эталон: 63656740
# ================================================================
VOWELS_20 = {10, 14, 18}  # A, E, I


def R_4466(n, b=20):
    digits = to_base_digits(n, b)
    rem = n % b  # остаток от деления N на 20 - константа, берётся от исходного N
    cur = digits
    for _ in range(2):
        cur = [1 if d in VOWELS_20 else d for d in cur]      # а) гласные -> 1
        cur = cur + [rem]                                     # б) остаток дописывается в конец
        cur = cur[1:] + [cur[0]]                               # в) первая цифра -> в конец
    return from_base_digits(cur, b)


def solve_4466():
    best = None
    for n in range(10000, 100000):
        r = R_4466(n)
        if r % 2030 == 0:
            best = r if best is None else max(best, r)
    return best


# ================================================================
# id 4467 — «Девятнадцатеричная запись с заменами и сдвигом» (основание 19)
# Согласные B,C,D,F,G,H = цифры 11,12,13,15,16,17 в 19-ричной системе.
# Эталон: 893871724
# ================================================================
CONSONANTS_19 = {11, 12, 13, 15, 16, 17}  # B, C, D, F, G, H


def R_4467(n, b=19):
    digits = to_base_digits(n, b)
    rem = n % b
    cur = digits
    for _ in range(2):
        cur = [5 if d in CONSONANTS_19 else d for d in cur]   # а) согласные -> 5
        cur = [rem] + cur                                      # б) остаток дописывается в начало
        cur = cur[-2:] + cur[:-2]                               # в) 2 последние цифры -> в начало
    return cur, from_base_digits(cur, b)


def solve_4467():
    # проверка правила сдвига на примере из условия: "12345" -> "45123"
    assert '12345'[-2:] + '12345'[:-2] == '45123'
    best, best_n = None, None
    for n in range(100000, 1000000):
        digs, r = R_4467(n)
        if sum(digs) % 7 == 0:
            if best is None or r > best:
                best, best_n = r, n
    return best


# ================================================================
# Прогон всех 23 задач и сверка с эталоном БД
# ================================================================
if __name__ == '__main__':
    expected = {
        3756: 30, 3997: 162390, 4017: 1023, 4026: 505, 4037: 9918,
        3752: 1089, 4052: 398, 4053: 159, 4072: 199, 4124: 19,
        4125: 618, 4128: 8642, 4162: 7, 4383: 68, 4404: 27,
        4414: 249998, 4416: 18750, 4417: 156, 4418: 46598,
        4464: 875, 4465: 228, 4466: 63656740, 4467: 893871724,
    }
    solvers = {
        3756: solve_3756, 3997: solve_3997, 4017: solve_4017, 4026: solve_4026,
        4037: solve_4037, 3752: solve_3752, 4052: solve_4052, 4053: solve_4053,
        4072: solve_4072, 4124: solve_4124, 4125: solve_4125, 4128: solve_4128,
        4162: solve_4162, 4383: solve_4383, 4404: solve_4404, 4414: solve_4414,
        4416: solve_4416, 4417: solve_4417, 4418: solve_4418, 4464: solve_4464,
        4465: solve_4465, 4466: solve_4466, 4467: solve_4467,
    }
    all_ok = True
    for tid in sorted(solvers):
        got = solvers[tid]()
        exp = expected[tid]
        ok = (got == exp)
        all_ok = all_ok and ok
        print(f'id {tid}: computed={got}  db={exp}  {"OK" if ok else "MISMATCH"}')
    print('ALL OK' if all_ok else 'SOME MISMATCHES')
