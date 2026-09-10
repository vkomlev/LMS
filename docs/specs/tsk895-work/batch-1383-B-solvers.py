# -*- coding: utf-8 -*-
"""
tsk-895: чистые вычислительные симуляторы для банка "Задание 5 ЕГЭ", курс 1383, группа B.

Источник условий и эталонов: D:\\Work\\LMS\\docs\\specs\\tsk895-task5-dump.json
Каждая функция r_<id>(...) — честная симуляция алгоритма из stem конкретного задания,
проверенная на иллюстративном примере из условия (см. соответствующий assert в solve_<id>()).
Каждая функция solve_<id>() выполняет полный перебор в обоснованных границах и возвращает
вычисленный ответ. Результат сравнения с эталоном БД — в batch-1383-B.md.

Это read-only анализ: скрипт ничего не пишет в БД и не изменяет исходные задания.
"""
import itertools

# ---------- вспомогательные функции ----------
def to_base_digits(n, base):
    """Список цифр числа n в системе счисления base, старший разряд первый. n >= 0."""
    if n == 0:
        return [0]
    digs = []
    while n > 0:
        digs.append(n % base)
        n //= base
    return digs[::-1]

def digits_to_int(digs, base):
    """Собрать десятичное значение из списка цифр (старший разряд первый) в системе base."""
    v = 0
    for d in digs:
        v = v * base + d
    return v

# =====================================================================
# id 3678 - Замена цифр и произведение числа
# =====================================================================
def r_3678(n_digits):
    """n_digits: список исходных цифр N (из множества {1,3,6,7,9})."""
    mapped = []
    for d in n_digits:
        if d == 3:
            mapped.append(4)
        elif d == 7:
            mapped.append(8)
        else:
            mapped.append(d)
    prod = 1
    for d in mapped:
        prod *= d
    return prod

def solve_3678():
    assert r_3678([7, 3]) == 32, r_3678([7, 3])  # пример из условия: 73 -> 74 -> 84 -> R=32
    allowed = [1, 3, 6, 7, 9]
    best = None
    for combo in itertools.product(allowed, repeat=4):
        if r_3678(list(combo)) == 256:
            n = int(''.join(map(str, combo)))
            if best is None or n < best:
                best = n
    return best

# =====================================================================
# id 3682 - Построение числа по семеричной записи (основание 7)
# =====================================================================
def r_3682(n):
    s = to_base_digits(n, 7)
    s2 = [d + 1 if d % 2 == 1 else d for d in s]           # шаг2: нечётные цифры +1
    total = sum(s2)                                        # шаг3: сумма цифр
    sum_digits = to_base_digits(total, 7)                   # шаг4: сумма в base7
    combined = sum_digits + s2                              # ...дописана в начало
    if combined[0] % 2 == 1:                                # шаг5: дублирование нечётной головы
        combined = [combined[0]] + combined
    return digits_to_int(combined, 7)                       # шаг6: в десятичную

def solve_3682():
    assert r_3682(56) == 1484, r_3682(56)  # пример из условия
    best = None
    for n in range(1, 20000):
        r = r_3682(n)
        if r > 2000 and (best is None or r < best):
            best = r
    return best

# =====================================================================
# id 3690 - Построение двенадцатеричного числа (основание 12)
# =====================================================================
def r_3690(n):
    s = to_base_digits(n, 12)
    maxd = max(s)
    if n % 4 == 0:
        combined = [2] + s + [6, 4]      # слева "2", справа "64"
    else:
        combined = s + [maxd]            # справа максимальная цифра записи
    return digits_to_int(combined, 12)

def solve_3690():
    assert r_3690(11) == 143, r_3690(11)
    assert r_3690(12) == 43276, r_3690(12)
    best = None
    for n in range(1, 5000):
        r = r_3690(n)
        if r > 1799 and (best is None or r < best):
            best = r
    return best

# =====================================================================
# id 3698 - Подсчёт чисел с результатом 1128
# =====================================================================
def r_3698(n):
    digs = [int(c) for c in str(n)]
    s = sorted(digs)
    total = s[0] + s[3]
    prod = s[1] * s[2]
    return int(str(total) + str(prod))

def solve_3698():
    assert r_3698(4231) == 56, r_3698(4231)  # пример: 4231->1234, 1+4=5, 2*3=6 -> 56
    count = 0
    for n in range(1000, 10000):
        if r_3698(n) == 1128:
            count += 1
    return count

# =====================================================================
# id 3699 - Разные значения после удаления нулей
# =====================================================================
def r_3699(n):
    b = bin(n)[2:]
    b2 = b.replace('0', '')
    return int(b2, 2) if b2 else 0

def solve_3699():
    vals = {r_3699(n) for n in range(10, 2501)}  # диапазон задан условием буквально
    return len(vals)

# =====================================================================
# id 3701 - Построение числа из трёхзначного
# =====================================================================
def r_3701(n):
    h, t, u = n // 100, (n // 10) % 10, n % 10
    append = abs(h - u) if h % 2 == 1 else h // 2
    digs = [h, t, u, append]
    p1, p2 = digs[0] * digs[1], digs[2] * digs[3]
    lo, hi = sorted([p1, p2])                     # запись в порядке неубывания
    return int(str(lo) + str(hi))

def solve_3701():
    assert r_3701(582) == 640, r_3701(582)  # пример из условия
    best = None
    for n in range(100, 1000):
        if r_3701(n) == 1012 and (best is None or n < best):
            best = n
    return best

# =====================================================================
# id 3702 - Двоичное преобразование числа с вставкой
# =====================================================================
def r_3702(n):
    s = bin(n)[2:]
    L = len(s)
    if L % 2 == 0:
        mid = L // 2
        res = s[:mid] + '000' + s[mid:]
    else:
        res = '1' + s + '01'
    return int(res, 2)

def solve_3702():
    assert r_3702(5) == 53, r_3702(5)
    assert r_3702(8) == 64, r_3702(8)
    best = None  # минимальное N (не R!), поэтому идём по возрастанию N и берём первое попадание
    for n in range(1, 2000):
        if r_3702(n) > 100:
            best = n
            break
    return best

# =====================================================================
# id 3705 - Минимальное число после троичного преобразования (основание 3)
# =====================================================================
def r_3705(n):
    s = to_base_digits(n, 3)
    s1 = s + [s[-1]]              # шаг1: дублировать правый символ записи N
    total = sum(s)                # сумма цифр ОРИГИНАЛЬНОЙ записи N (важный нюанс!)
    if total % 3 == 0:
        combined = [2] + s1 + [1]
    else:
        rem = total % 3
        app = to_base_digits(rem * 2, 3)
        combined = s1 + app
    return digits_to_int(combined, 3)

def solve_3705():
    assert r_3705(8) == 80, r_3705(8)     # 8=22_3 -> R=2222_3=80
    assert r_3705(11) == 592, r_3705(11)  # 11=102_3 -> R=210221_3=592
    best_r, best_n = None, None
    for n in range(1, 50000):
        r = r_3705(n)
        if r > 1000 and (best_r is None or r < best_r):
            best_r, best_n = r, n
    return best_n  # вопрос просит N, дающее минимальное R>1000

# =====================================================================
# id 3707 - Максимальное число после четверичного преобразования (основание 4), N нечётное
# =====================================================================
def r_3707(n):
    s = to_base_digits(n, 4)
    if n % 3 == 0:
        s2 = s[:]
        s2[0], s2[-1] = s2[-1], s2[0]
        combined = s2 + [1]
    else:
        combined = s + [n % 3]
    return digits_to_int(combined, 4)

def solve_3707():
    assert r_3707(11) == 46, r_3707(11)
    assert r_3707(13) == 53, r_3707(13)
    best = None
    for n in range(1, 50000, 2):        # только нечётные N
        r = r_3707(n)
        if r <= 340 and (best is None or r > best):   # "не превышающее 340"
            best = r
    return best

# =====================================================================
# id 3709 - Минимальное число после пятеричного преобразования (основание 5), N>10
# =====================================================================
def r_3709(n):
    s = to_base_digits(n, 5)
    if n % 5 == 0:
        combined = s + s[-3:]
    else:
        app = to_base_digits((n % 5) * 5, 5)
        combined = app + s
    return digits_to_int(combined, 5)

def solve_3709():
    assert r_3709(7) == 257, r_3709(7)
    best = None  # минимальное N (не R!) с R>375 -> идём по возрастанию, берём первое попадание
    for n in range(11, 50000):
        if r_3709(n) > 375:
            best = n
            break
    return best

# =====================================================================
# id 3712 - Наибольшее число по чётным разрядам
# =====================================================================
def r_3712(n):
    digs = [int(c) for c in str(n)]
    L = len(digs)
    primes = {2, 3, 5, 7}
    K_sum = sum(d for i, d in enumerate(digs) if (L - 1 - i) % 2 == 0)
    L_sum = sum(d * d for d in digs if d in primes)
    return abs(K_sum ** 2 - L_sum)

def solve_3712():
    best = None
    for n in range(100000, 1000000):    # полный перебор всех шестизначных N
        s = str(n)
        if len(set(s)) == 6 and r_3712(n) == 407:
            if best is None or n > best:
                best = n
    return best

# =====================================================================
# id 3717 - Максимальное число после инверсии битов
# =====================================================================
def r_3717(n):
    s = bin(n)[2:]
    inv = ''.join('1' if c == '0' else '0' for c in s)
    parity = sum(int(c) for c in s) % 2
    return int(inv + str(parity), 2)

def solve_3717():
    assert r_3717(60) == 6, r_3717(60)
    best = None
    for n in range(1, 50000):
        r = r_3717(n)
        if r < 170 and (best is None or r > best):
            best = r
    return best

# =====================================================================
# id 3719 - Наибольшее число после сортировки троичных цифр (основание 3)
# =====================================================================
def r_3719(n):
    s = to_base_digits(n, 3)
    combined = sorted(s, reverse=True) + [max(s)]
    return digits_to_int(combined, 3)

def solve_3719():
    assert r_3719(123) == 605, r_3719(123)
    best = None
    for n in range(1, 50000):
        r = r_3719(n)
        if r < 1200 and (best is None or r > best):
            best = r
    return best

# =====================================================================
# id 3720 - Минимальное число после двоичного дополнения
# =====================================================================
def r_3720(n):
    s = bin(n)[2:]
    if n % 2 == 0:
        res = '1' + s + '00'
    else:
        digsum = sum(int(c) for c in s)
        res = s + bin(digsum)[2:]
    return int(res, 2)

def solve_3720():
    assert r_3720(4) == 48, r_3720(4)
    assert r_3720(13) == 55, r_3720(13)
    best_r, best_n = None, None
    for n in range(1, 20000):
        r = r_3720(n)
        if r > 190 and (best_r is None or r < best_r):
            best_r, best_n = r, n
    return best_n  # вопрос просит N, дающее минимальный R>190

# =====================================================================
# id 3721 - Минимальное число после троичного дополнения (основание 3)
# =====================================================================
def r_3721(n):
    s = to_base_digits(n, 3)
    if n % 3 == 0:
        combined = s + s[-2:]
    else:
        app = to_base_digits((n % 3) * 5, 3)
        combined = s + app
    return digits_to_int(combined, 3)

def solve_3721():
    assert r_3721(11) == 307, r_3721(11)
    assert r_3721(12) == 111, r_3721(12)
    best = None
    for n in range(1, 50000):
        r = r_3721(n)
        if r > 133 and (best is None or r < best):
            best = r
    return best

# =====================================================================
# id 3724 - Преобразование троичного числа с реверсом (основание 3)
# =====================================================================
def r_3724(n):
    s = to_base_digits(n, 3)
    mapping = {1: 2, 2: 0, 0: 1}
    mapped = [mapping[d] for d in s]
    i = 0
    while i < len(mapped) - 1 and mapped[i] == 0:   # удалить незначащие нули
        i += 1
    stripped = mapped[i:]
    reversed_ = stripped[::-1]                       # реверс
    total = sum(reversed_)                            # сумма цифр СЧИТАЕТСЯ ПОСЛЕ реверса
    combined = reversed_ + to_base_digits(total, 3)
    return digits_to_int(combined, 3)

def solve_3724():
    assert r_3724(69) == 102, r_3724(69)
    assert r_3724(41) == 240, r_3724(41)
    best = None
    for n in range(1, 200000):
        r = r_3724(n)
        if r > 10000 and (best is None or r < best):
            best = r
    return best

# =====================================================================
# id 3734 - Контрольное значение по разрядам трёхзначных чисел (пара чисел!)
# =====================================================================
def control_value(a, b):
    h1, t1, u1 = a // 100, (a // 10) % 10, a % 10
    h2, t2, u2 = b // 100, (b // 10) % 10, b % 10
    S1, S2, S3 = h1 + h2, t1 + t2, u1 + u2
    s = (str(S2) + str(S1)) if S2 > S1 else (str(S1) + str(S2))
    return int(s + str(S3))

def solve_3734():
    assert control_value(123, 567) == 8610, control_value(123, 567)
    vals = set()
    for a in range(100, 1000):                 # полный перебор всех неупорядоченных пар
        for b in range(a + 1, 1000):
            vals.add(control_value(a, b))
    return len(vals)

# =====================================================================
# id 3735 - Преобразование троичного числа с условиями (основание 3)
# =====================================================================
def r_3735(n):
    s = to_base_digits(n, 3)
    if len(s) % 2 == 1:
        s = [1] + s                              # нечётная длина -> дописать 1 в начало
    total = sum(s)
    if total % 2 == 0:
        combined = s + s[:2]                      # дописать первые две цифры в конец
    else:
        combined = s + to_base_digits(n % 5, 3)    # остаток N % 5 (от исходного N!) в конец
    if combined[0] == 2:                           # начинается на "2" -> удалить разряд
        combined = combined[1:]
    if len(combined) >= 2 and combined[-1] == combined[-2]:  # 2 одинаковые в конце -> удалить
        combined = combined[:-1]
    return digits_to_int(combined, 3)

def solve_3735():
    assert r_3735(14) == 124, r_3735(14)
    assert r_3735(68) == 133, r_3735(68)
    best = None
    for n in range(1, 50000):
        r = r_3735(n)
        if r > 150 and (best is None or r < best):
            best = r
    return best

# =====================================================================
# id 3739 - Четырёхзначные числа с произведениями цифр
# =====================================================================
def r_3739(n):
    digs = [int(c) for c in str(n)]
    prods = sorted([digs[0]*digs[1], digs[1]*digs[2], digs[2]*digs[3]])  # окно пар + сортировка
    s = ''.join(str(p) for p in prods).lstrip('0') or '0'
    return s

def solve_3739():
    assert r_3739(1024) == '8', r_3739(1024)
    assert r_3739(2311) == '136', r_3739(2311)
    count = 0
    for n in range(1000, 10000):
        if len(r_3739(n)) == 4:
            count += 1
    return count

# =====================================================================
# id 3741 - Максимальное число с четверичным преобразованием (основание 4), N>4
# =====================================================================
def r_3741(n):
    s = to_base_digits(n, 4)
    if sum(s) % 2 == 0:
        combined = s + s[:2]
    else:
        temp = s + [2]
        temp[0], temp[1] = 1, 0                  # два левых разряда заменяются на "10"
        combined = temp
    return digits_to_int(combined, 4)

def solve_3741():
    assert r_3741(8) == 136, r_3741(8)
    assert r_3741(18) == 74, r_3741(18)
    best = None
    for n in range(5, 50000):
        r = r_3741(n)
        if r < 250 and (best is None or n > best):    # максимальное N (не R!) с R<250
            best = n
    return best

# =====================================================================
# id 3748 - Наибольшее неизменяемое число после реверса (8-битная запись)
# =====================================================================
def r_3748(n):
    s = format(n, '08b')
    return int(s[:-1][::-1], 2)   # убрать последнюю цифру, перевернуть остаток

def solve_3748():
    best = None
    for n in range(1, 100):        # диапазон N<100 задан условием
        if r_3748(n) == n and (best is None or n > best):
            best = n
    return best

# =====================================================================
# id 4100 - Число после инверсии двоично-десятичного кода (BCD, 4 бита/разряд)
# =====================================================================
def r_4100(n):
    d1, d2 = n // 10, n % 10
    s = format(d1, '04b') + format(d2, '04b')
    inv = ''.join('1' if c == '0' else '0' for c in s)
    return int(inv, 2)

def solve_4100():
    assert r_4100(13) == 236, r_4100(13)
    for n in range(10, 100):        # полный перебор всех двузначных N
        if r_4100(n) == 151:
            return n
    return None

# =====================================================================
# id 4415 - Дописывание кода по делимости
# =====================================================================
def r_4415(n):
    s = bin(n)[2:]
    s += '111' if n % 7 == 0 else '1'      # шаг2: делимость N на 7
    val2 = int(s, 2)
    s += '101' if val2 % 5 == 0 else '1'   # шаг3: делимость ПОСЛЕ шага2 на 5
    return int(s, 2)

def solve_4415():
    assert r_4415(14) == 239, r_4415(14)
    for n in range(1, 200000):     # минимальное N (не R!), идём по возрастанию
        if r_4415(n) > 500000:
            return n
    return None


if __name__ == '__main__':
    results = {
        '3678': solve_3678(), '3682': solve_3682(), '3690': solve_3690(),
        '3698': solve_3698(), '3699': solve_3699(), '3701': solve_3701(),
        '3702': solve_3702(), '3705': solve_3705(), '3707': solve_3707(),
        '3709': solve_3709(), '3712': solve_3712(), '3717': solve_3717(),
        '3719': solve_3719(), '3720': solve_3720(), '3721': solve_3721(),
        '3724': solve_3724(), '3734': solve_3734(), '3735': solve_3735(),
        '3739': solve_3739(), '3741': solve_3741(), '3748': solve_3748(),
        '4100': solve_4100(), '4415': solve_4415(),
    }
    with open('results.txt', 'w', encoding='utf-8') as f:
        for k, v in results.items():
            f.write(f'{k}: {v}\n')
    for k, v in results.items():
        print(f'{k}: {v}')
