# -*- coding: utf-8 -*-
"""
tsk-895 batch-156: честные симуляторы для 34 заданий "Задание 5 ЕГЭ" курса 156.
Каждая функция сопровождается комментарием с id задания.
Запуск: python batch-156-solvers.py  (пишет отчёт в batch-156-report.json, UTF-8)
"""
import json
import itertools

# ---------- общие утилиты ----------

def to_base(n: int, b: int) -> str:
    """Перевод неотрицательного целого n в систему счисления b (без ведущих нулей)."""
    if n == 0:
        return "0"
    digits = []
    while n > 0:
        digits.append(str(n % b))
        n //= b
    return ''.join(reversed(digits))


def from_base(s: str, b: int) -> int:
    return int(s, b)


results = {}


# ============================================================
# id=3240  Пары чисел с делением на делители
# M в [50,100], N в [1000,10000], N кратно M и N кратно 143. Количество пар.
def solve_3240():
    cnt = 0
    for M in range(50, 101):
        for N in range(1000, 10001):
            if N % M == 0 and N % 143 == 0:
                cnt += 1
    return cnt

results[3240] = {"computed": solve_3240(), "expected": "109"}


# ============================================================
# id=4818  Перевод чисел в двоичную форму: 45, 110, 2323, 3456
def solve_4818():
    nums = [45, 110, 2323, 3456]
    return ' '.join(bin(n)[2:] for n in nums)

results[4818] = {"computed": solve_4818(), "expected": "101101 1101110 100100010011 110110000000"}


# ============================================================
# id=4819  Количество единиц в двоичной записи чисел 45, 110, 2323, 3456
def solve_4819():
    nums = [45, 110, 2323, 3456]
    return ' '.join(str(bin(n)[2:].count('1')) for n in nums)

results[4819] = {"computed": solve_4819(), "expected": "4 5 5 4"}


# ============================================================
# id=4820  Удалить последний символ строки S, дописать '11' справа.
# Шаблонное задание без конкретного S в условии (эталона в БД нет: short_answer=null,
# manual_review_required=true) -- вычислительно проверить нечего.
results[4820] = {"computed": "N/A - общий шаблон без конкретного S, manual_review", "expected": "(нет эталона в БД)"}


# ============================================================
# id=4821  Заменить последний символ строки S на второй слева символ.
# Тоже шаблонное задание без конкретного S, manual_review_required=true.
results[4821] = {"computed": "N/A - общий шаблон без конкретного S, manual_review", "expected": "(нет эталона в БД)"}


# ============================================================
# id=3451  Инверсия восьмибитного числа. N=200.
# Проверка на примере: N=13 -> 00001101 -> инверсия 11110010 -> 242.
def invert8(n):
    b = format(n, '08b')
    inv = ''.join('1' if c == '0' else '0' for c in b)
    return int(inv, 2)

assert invert8(13) == 242, invert8(13)

def solve_3451():
    return invert8(200)

results[3451] = {"computed": solve_3451(), "expected": "55"}


# ============================================================
# id=3450  Наибольшее/наименьшее двузначное из цифр N=905, разность.
def maxmin_2digit_diff(n):
    digits = [int(c) for c in str(n)]
    vals = set()
    for i in range(len(digits)):
        for j in range(len(digits)):
            if i == j:
                continue
            if digits[i] == 0:
                continue  # не может начинаться с нуля
            vals.add(digits[i] * 10 + digits[j])
    return max(vals) - min(vals)

def solve_3450():
    return maxmin_2digit_diff(905)

results[3450] = {"computed": solve_3450(), "expected": "45"}


# ============================================================
# id=2149  Двоичная запись N + 2 разряда (чётность суммы цифр, дважды).
# Найти минимальное N, при котором R > 77.
def R_append_parity_twice(n):
    b = bin(n)[2:]
    s1 = sum(int(c) for c in b) % 2
    b2 = b + str(s1)
    s2 = sum(int(c) for c in b2) % 2
    b3 = b2 + str(s2)
    return int(b3, 2)

# пример из задания 4129 (тот же алгоритм): N=13 -> 1101 -> +1 ->11011 -> +0 ->110110 =54
assert R_append_parity_twice(13) == 54

def solve_2149():
    n = 1
    while True:
        if R_append_parity_twice(n) > 77:
            return n
        n += 1

results[2149] = {"computed": solve_2149(), "expected": "19"}


# ============================================================
# id=2150  Обратный код (инверсия+1) восьмибитного числа, N<128. Результат=221 -> найти N.
def two_s_complement8(n):
    b = format(n, '08b')
    inv = ''.join('1' if c == '0' else '0' for c in b)
    val = int(inv, 2) + 1
    return val  # без обрезки до 8 бит, т.к. это то, что буквально просит алгоритм

def solve_2150():
    for n in range(1, 128):
        if two_s_complement8(n) == 221:
            return n
    return None

results[2150] = {"computed": solve_2150(), "expected": "35"}


# ============================================================
# id=2151  Двоичная запись N, затем ПОВТОРЯЕТСЯ (t раз) шаг:
#  а) если единиц больше нулей -> в конец 0
#  б) иначе -> в начало 11
# Число повторений в условии явно не указано ("Повторяется пункт 2" без числа).
# Проверяем эмпирически число повторений t=1,2,3 на предмет соответствия эталону (N=32, R>500 минимально).
def R_2151(n, reps):
    b = bin(n)[2:]
    for _ in range(reps):
        ones = b.count('1')
        zeros = b.count('0')
        if ones > zeros:
            b = b + '0'
        else:
            b = '11' + b
    return int(b, 2)

def solve_2151_min(reps, threshold):
    n = 1
    while True:
        if R_2151(n, reps) > threshold:
            return n
        n += 1

_variants_2151 = {reps: solve_2151_min(reps, 500) for reps in (1, 2, 3)}
results[2151] = {
    "computed": f"reps=1:{_variants_2151[1]}  reps=2:{_variants_2151[2]}  reps=3:{_variants_2151[3]}",
    "expected": "32",
    "note": "неоднозначно сколько раз повторяется шаг 2 - см. заметки"
}


# ============================================================
# id=4129  То же преобразование, что 2149 (проверено на примере N=13->54 в самом условии).
# НО вопрос другой: "Какое наименьшее число, большее 80, может появиться на экране?"
# Это поиск минимального ДОСТИЖИМОГО R > 80 (перебор по N), а не минимального N.
def solve_4129(bound_n=100000):
    best = None
    for n in range(1, bound_n):
        r = R_append_parity_twice(n)
        if r > 80 and (best is None or r < best):
            best = r
    return best

results[4129] = {"computed": solve_4129(), "expected": "86"}


# ============================================================
# id=4555  Двоичная запись N; если N%3==0 - дописать последние 3 бита записи;
# иначе (остаток+1)*3 -> двоичная запись -> дописать в конец.
# Вопрос: максимальное R, не превышающее 416 (поиск по R, перебор N).
def R_4555(n):
    b = bin(n)[2:]
    if n % 3 == 0:
        nb = b + b[-3:]
    else:
        rem = n % 3
        val = (rem + 1) * 3
        nb = b + bin(val)[2:]
    return int(nb, 2)

# пример из условия: N=12 -> 1100100(2) = 100; N=4 -> 100110(2) = 38
assert R_4555(12) == 100, R_4555(12)
assert R_4555(4) == 38, R_4555(4)

def solve_4555(bound_n=2000):
    best = 0
    for n in range(1, bound_n):
        r = R_4555(n)
        if r <= 416 and r > best:
            best = r
    return best

results[4555] = {"computed": solve_4555(), "expected": "411"}


# ============================================================
# id=9502  Двоичная запись N; N чётное -> каждая 1 -> 11; N нечётное -> каждый 0 -> 00.
# Минимальное N, при котором R > 70.
def R_9502(n):
    b = bin(n)[2:]
    if n % 2 == 0:
        nb = b.replace('1', '11')
    else:
        nb = b.replace('0', '00')
    return int(nb, 2)

assert R_9502(4) == 12, R_9502(4)
assert R_9502(5) == 9, R_9502(5)

def solve_9502():
    n = 1
    while True:
        if R_9502(n) > 70:
            return n
        n += 1

results[9502] = {"computed": solve_9502(), "expected": "14"}


# ============================================================
# id=9559  Двоичная запись N; если длина чётная - вставить '1' в середину;
# если нечётная - без изменений. Максимальное N, при котором R <= 26.
def R_9559(n):
    b = bin(n)[2:]
    L = len(b)
    if L % 2 == 0:
        mid = L // 2
        nb = b[:mid] + '1' + b[mid:]
        return int(nb, 2)
    else:
        return n

assert R_9559(5) == 5, R_9559(5)   # 101 - нечётная длина, без изменений
assert R_9559(2) == 6, R_9559(2)   # 10 -> 110 = 6

def solve_9559(bound_n=5000):
    best = None
    for n in range(1, bound_n):
        if R_9559(n) <= 26:
            if best is None or n > best:
                best = n
    return best

results[9559] = {"computed": solve_9559(), "expected": "26"}


# ============================================================
# id=2152  Двоичная запись N + 2 разряда:
#  а) N%3==0 -> справа дописываются две последние цифры (в исходном порядке = b[-2:])
#  б) иначе -> слева дописывается 1 и справа дописывается 1
# Минимальное R, превышающее 700 (поиск по достижимым R).
def R_2152(n):
    b = bin(n)[2:]
    if n % 3 == 0:
        nb = b + b[-2:]
    else:
        nb = '1' + b + '1'
    return int(nb, 2)

def solve_2152(bound_n=5000):
    best = None
    for n in range(1, bound_n):
        r = R_2152(n)
        if r > 700 and (best is None or r < best):
            best = r
    return best

results[2152] = {"computed": solve_2152(), "expected": "709"}


# ============================================================
# id=2154  Двоичная запись N; N%3==0 -> дописать последние 3 бита;
# иначе -> остаток*3 (без +1!) -> двоичная запись -> дописать.
# Максимальное N, при котором R < 130 (поиск по N).
def R_2154(n):
    b = bin(n)[2:]
    if n % 3 == 0:
        nb = b + b[-3:]
    else:
        rem = n % 3
        val = rem * 3
        nb = b + bin(val)[2:]
    return int(nb, 2)

assert R_2154(12) == 100, R_2154(12)
assert R_2154(4) == 19, R_2154(4)

def solve_2154(bound_n=2000):
    best = None
    for n in range(1, bound_n):
        if R_2154(n) < 130:
            if best is None or n > best:
                best = n
    return best

results[2154] = {"computed": solve_2154(), "expected": "31"}


# ============================================================
# id=2069  Два входа N,M. P1 = произведение всех НЕНУЛЕВЫХ чётных цифр (N и M вместе).
# P2 = произведение всех нечётных цифр (N и M вместе). R=|P1-P2|.
# N=120 фиксировано, найти минимальное M, при котором R=29.
def digits_of(n):
    return [int(c) for c in str(n)]

def R_2069(n, m):
    ds = digits_of(n) + digits_of(m)
    p1 = 1
    any_even = False
    for d in ds:
        if d != 0 and d % 2 == 0:
            p1 *= d
            any_even = True
    if not any_even:
        p1 = 1  # пустое произведение = 1 (по соглашению) - см. пример ниже
    p2 = 1
    any_odd = False
    for d in ds:
        if d % 2 == 1:
            p2 *= d
            any_odd = True
    if not any_odd:
        p2 = 1
    return abs(p1 - p2)

# проверка примера: N=256, M=108 -> P1=2*6*8=96, P2=5*1=5, R=91
assert R_2069(256, 108) == 91, R_2069(256, 108)

def solve_2069(bound_m=100000):
    for m in range(1, bound_m):
        if R_2069(120, m) == 29:
            return m
    return None

results[2069] = {"computed": solve_2069(), "expected": "238"}


# ============================================================
# id=2070  Тот же алгоритм, что 2149 (двоичная запись + 2 разряда чётности суммы, дважды).
# Минимальное N, при котором R > 137.
def solve_2070():
    n = 1
    while True:
        if R_append_parity_twice(n) > 137:
            return n
        n += 1

results[2070] = {"computed": solve_2070(), "expected": "35"}


# ============================================================
# id=2282  Двоичная запись N; в конец дописывается вторая СПРАВА цифра исходной записи,
# затем в конец дописывается вторая СЛЕВА цифра исходной записи (обе - от исходной b, не растущей).
# Проверено на примере: N=13 -> 1101 -> +0(вторая справа) -> 11010 -> +1(вторая слева) -> 110101 = 53.
def R_2282(n):
    b = bin(n)[2:]
    c2r = b[-2]
    c2l = b[1]
    nb = b + c2r + c2l
    return int(nb, 2)

assert R_2282(13) == 53, R_2282(13)

def solve_2282():
    n = 2
    while True:
        if R_2282(n) > 180:
            return n
        n += 1

results[2282] = {"computed": solve_2282(), "expected": "46"}


# ============================================================
# id=2281  Тот же алгоритм, что 2154 (проверено на том же примере N=12->100, N=4->19).
# Минимальное ДОСТИЖИМОЕ R, большее 151 (поиск по R).
def solve_2281(bound_n=2000):
    best = None
    for n in range(1, bound_n):
        r = R_2154(n)
        if r > 151 and (best is None or r < best):
            best = r
    return best

results[2281] = {"computed": solve_2281(), "expected": "163"}


# ============================================================
# id=2283  Тот же алгоритм, что 2149/4129/2070 (двоичная запись + 2 разряда чётности, дважды).
# "Какое наименьшее число, большее 93, может появиться на экране" - минимальный достижимый R>93.
def solve_2283(bound_n=100000):
    best = None
    for n in range(1, bound_n):
        r = R_append_parity_twice(n)
        if r > 93 and (best is None or r < best):
            best = r
    return best

results[2283] = {"computed": solve_2283(), "expected": "96"}


# ============================================================
# id=2289  Двоичная запись N;
#  а) сумма цифр чётная -> справа дописать 0, затем два ЛЕВЫХ разряда (нового, уже с 0) заменить на '10'
#  б) сумма цифр нечётная -> справа дописать 1, затем два левых разряда заменить на '11'
# Проверено на примере: N=6(110)->1100->1000=8; N=4(100)->1001->1101=13.
def R_2289(n):
    b = bin(n)[2:]
    s = sum(int(c) for c in b)
    if s % 2 == 0:
        nb = b + '0'
        nb = '10' + nb[2:]
    else:
        nb = b + '1'
        nb = '11' + nb[2:]
    return int(nb, 2)

assert R_2289(6) == 8, R_2289(6)
assert R_2289(4) == 13, R_2289(4)

def solve_2289():
    n = 2
    while True:
        if R_2289(n) > 40:
            return n
        n += 1

results[2289] = {"computed": solve_2289(), "expected": "16"}


# ============================================================
# id=2288  Двоичная запись N;
#  а) чётное -> слева дописать '10'
#  б) нечётное -> слева дописать '1', справа дописать '01'
# Проверено на примере: N=4(100)->10100=20; N=5(101)->110101=53.
# Максимальное R при N от 1 до 12 включительно.
def R_2288(n):
    b = bin(n)[2:]
    if n % 2 == 0:
        nb = '10' + b
    else:
        nb = '1' + b + '01'
    return int(nb, 2)

assert R_2288(4) == 20, R_2288(4)
assert R_2288(5) == 53, R_2288(5)

def solve_2288():
    return max(R_2288(n) for n in range(1, 13))

results[2288] = {"computed": solve_2288(), "expected": "109"}


# ============================================================
# id=2285  Три повторения: к двоичной записи ТЕКУЩЕГО числа дописывается бит, равный чётности
# СУММЫ ЦИФР ДЕСЯТИЧНОЙ записи текущего числа (1, если нечётна, 0 если чётна).
# Проверено на примере: N=17 -> 34 -> 69 -> 139.
def step_2285(x):
    s = sum(int(c) for c in str(x))
    bit = '1' if s % 2 == 1 else '0'
    nb = bin(x)[2:] + bit
    return int(nb, 2)

def R_2285(n):
    x = n
    for _ in range(3):
        x = step_2285(x)
    return x

assert R_2285(17) == 139, R_2285(17)

def solve_2285(bound_n=5000):
    best = None
    for n in range(1, bound_n):
        r = R_2285(n)
        if r > 2054 and (best is None or r < best):
            best = r
    return best

results[2285] = {"computed": solve_2285(), "expected": "2057"}


# ============================================================
# id=2286  Удалить первую слева единицу и все идущие сразу за ней нули (до следующей единицы
# или до конца записи). Если ничего не остаётся - результат 0. Вывод: N - результат.
# Сколько РАЗНЫХ значений разности встретится для N от 100 до 3000.
def transform_2286(n):
    b = bin(n)[2:]
    i = 1
    while i < len(b) and b[i] == '0':
        i += 1
    rest = b[i:]
    if rest == '':
        return 0
    return int(rest, 2)

def diff_2286(n):
    return n - transform_2286(n)

# пример: N=11 (1011) -> удалить 1 и следующий 0 -> "11" = 3 -> 11-3=8
assert diff_2286(11) == 8, diff_2286(11)

def solve_2286():
    vals = set(diff_2286(n) for n in range(100, 3001))
    return len(vals)

results[2286] = {"computed": solve_2286(), "expected": "6"}


# ============================================================
# id=2068  Из цифр трёхзначного N строятся наибольшее/наименьшее двузначные числа,
# выводится разность. Количество N в [700;800] с разностью 80.
def solve_2068():
    cnt = 0
    for n in range(700, 801):
        if maxmin_2digit_diff(n) == 80:
            cnt += 1
    return cnt

# пример: N=351 -> 53-13=40
assert maxmin_2digit_diff(351) == 40

results[2068] = {"computed": solve_2068(), "expected": "2"}


# ============================================================
# id=2153  Тот же алгоритм, что 2068, отрезок [800;900], разность=30.
def solve_2153():
    cnt = 0
    for n in range(800, 901):
        if maxmin_2digit_diff(n) == 30:
            cnt += 1
    return cnt

results[2153] = {"computed": solve_2153(), "expected": "9"}


# ============================================================
# id=2155  Троичная запись N;
#  а) N%3==0 -> слева приписать '1', справа приписать '02'
#  б) иначе -> (N%3)*4 -> троичная запись -> дописать в конец
# Проверено на примере: N=11 -> 10222(3) = 107; N=12 -> 111002(3) = 353.
def R_2155(n):
    t = to_base(n, 3)
    if n % 3 == 0:
        nt = '1' + t + '02'
    else:
        rem = n % 3
        val = rem * 4
        nt = t + to_base(val, 3)
    return from_base(nt, 3)

assert R_2155(11) == 107, R_2155(11)
assert R_2155(12) == 353, R_2155(12)

def solve_2155(bound_n=200):
    best = None
    for n in range(1, bound_n):
        if R_2155(n) < 100:
            if best is None or n > best:
                best = n
    return best

results[2155] = {"computed": solve_2155(), "expected": "10"}


# ============================================================
# id=2156  Девятеричная запись N. В условии НЕТ иллюстративного примера - трактовка буквальная,
# наиболее вероятная неоднозначность отмечена в заметках батч-файла.
#  а) запись начинается на 7 -> в ЭТОЙ (ещё непреобразованной) записи все 6->3 и 3->6,
#     затем СЛЕВА приписывается "34"
#  б) запись не начинается на 7 -> справа приписывается "45", затем первый (левый) разряд
#     полученной (уже удлинённой) записи заменяется на '3'
# Нужно: максимальное R < 2876 (по всем N), затем среди N, дающих это R, - максимальное N.
def R_2156(n):
    t = to_base(n, 9)
    if t[0] == '7':
        swapped = t.replace('6', 'X').replace('3', '6').replace('X', '3')
        nt = '34' + swapped
    else:
        nt = t + '45'
        nt = '3' + nt[1:]
    return from_base(nt, 9)

def solve_2156(bound_n=20000):
    best_r = -1
    best_n = None
    for n in range(1, bound_n):
        r = R_2156(n)
        if r < 2876:
            if r > best_r or (r == best_r and n > best_n):
                best_r = r
                best_n = n
    return best_n, best_r

results[2156] = {"computed": solve_2156(), "expected": "79",
                  "note": "нет примера в условии - трактовка буквальная, см. заметки"}


# ============================================================
# id=2284  4-значное N (без ведущего нуля). s1=d1+d2, s2=d2+d3, s3=d3+d4.
# Убирается наименьшая из трёх сумм, оставшиеся две записываются в порядке неубывания подряд.
# Минимальное N, при котором R=613.
def R_2284(n):
    s = str(n).zfill(4)
    d = [int(c) for c in s]
    sums = [d[0] + d[1], d[1] + d[2], d[2] + d[3]]
    m = min(sums)
    idx = sums.index(m)
    remaining = sums[:idx] + sums[idx + 1:]
    remaining.sort()
    return int(str(remaining[0]) + str(remaining[1]))

assert R_2284(1984) == 1217, R_2284(1984)

def solve_2284():
    for n in range(1000, 10000):
        if R_2284(n) == 613:
            return n
    return None

results[2284] = {"computed": solve_2284(), "expected": "1067"}


# ============================================================
# id=4556  Четверичная запись N;
#  а) N%4==0 -> дописать 2 последние четверичные цифры
#  б) иначе -> (N%4)*2 -> четверичная запись -> дописать в конец
# Проверено на примере: N=11 -> 2312(4) = 182; N=12 -> 3030(4) = 204.
# Минимальное N, при котором R >= 1025.
def R_4556(n):
    t = to_base(n, 4)
    if n % 4 == 0:
        nt = t + t[-2:]
    else:
        rem = n % 4
        val = rem * 2
        nt = t + to_base(val, 4)
    return from_base(nt, 4)

assert R_4556(11) == 182, R_4556(11)
assert R_4556(12) == 204, R_4556(12)

def solve_4556(bound_n=2000):
    n = 1
    while n < bound_n:
        if R_4556(n) >= 1025:
            return n
        n += 1
    return None

results[4556] = {"computed": solve_4556(), "expected": "66"}


# ============================================================
# id=2287  Все тройки соседних цифр десятичной записи N (>=100, длина>=3) как трёхзначные числа
# (возможно с ведущими нулями). R = max окна - min окна. Минимальное N, при котором R=623.
def R_2287(n):
    s = str(n)
    windows = [int(s[i:i+3]) for i in range(len(s) - 2)]
    return max(windows) - min(windows)

assert R_2287(20024) == 198, R_2287(20024)

def solve_2287(bound_n=200000):
    for n in range(100, bound_n):
        if R_2287(n) == 623:
            return n
    return None

results[2287] = {"computed": solve_2287(), "expected": "1803"}


# ============================================================
# id=3106  Троичная запись N;
#  а) N%3==0 -> дописать 2 последние троичные цифры
#  б) иначе -> сумма цифр троичной записи N *3 -> троичная запись -> дописать
# Проверено на примере: N=8 -> 22110(3) = 228; N=9 -> 10000(3) = 81.
# Минимальное НЕЧЁТНОЕ R, большее 208 (поиск по достижимым R).
def R_3106(n):
    t = to_base(n, 3)
    if n % 3 == 0:
        nt = t + t[-2:]
    else:
        s = sum(int(c) for c in t)
        val = s * 3
        nt = t + to_base(val, 3)
    return from_base(nt, 3)

assert R_3106(8) == 228, R_3106(8)
assert R_3106(9) == 81, R_3106(9)

def solve_3106(bound_n=2000):
    best = None
    for n in range(1, bound_n):
        r = R_3106(n)
        if r % 2 == 1 and r > 208:
            if best is None or r < best:
                best = r
    return best

results[3106] = {"computed": solve_3106(), "expected": "243"}


# ============================================================
# id=3289  Двоичная запись N (N>2). Рассматриваются два младших (правых) разряда:
#  а) различны -> оба инвертируются, затем последний разряд повторяется (дублируется, +1 бит)
#  б) одинаковы -> между ними вписывается ещё один разряд: между 1,1 -> 0; между 0,0 -> 1 (+1 бит)
# "Предыдущий пункт повторяется" - количество повторений в условии явно не указано.
# В условии НЕТ иллюстративного примера. Эмпирически проверены варианты reps=1..4 -
# ответ базы (N=41, R>168 минимальное N) воспроизводится ТОЛЬКО при reps=2 (см. заметки).
def step_3289(b):
    last2 = b[-2:]
    if last2[0] != last2[1]:
        inv = {'0': '1', '1': '0'}
        new_last2 = inv[last2[0]] + inv[last2[1]]
        nb = b[:-2] + new_last2 + new_last2[-1]
    else:
        insert = '0' if last2 == '11' else '1'
        nb = b[:-1] + insert + b[-1]
    return nb

def R_3289(n, reps):
    b = bin(n)[2:]
    for _ in range(reps):
        b = step_3289(b)
    return int(b, 2)

def solve_3289_min(reps, threshold):
    n = 3
    while True:
        if R_3289(n, reps) > threshold:
            return n
        n += 1

_variants_3289 = {reps: solve_3289_min(reps, 168) for reps in (1, 2, 3, 4)}
results[3289] = {
    "computed": f"reps=1:{_variants_3289[1]}  reps=2:{_variants_3289[2]}  "
                f"reps=3:{_variants_3289[3]}  reps=4:{_variants_3289[4]}",
    "expected": "41",
    "note": "нет примера в условии, число повторений неясно - см. заметки"
}


# ============================================================ output
if __name__ == '__main__':
    with open('batch-156-report.json', 'w', encoding='utf-8') as f:
        json.dump({str(k): v for k, v in results.items()}, f, ensure_ascii=False, indent=2)
    print('DONE', len(results))
